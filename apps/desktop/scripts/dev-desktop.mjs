// 开发态启动器：拉起桌面壳，并在终端 Ctrl+C 时把**整条进程树**收干净。
//
// 为什么需要它 —— Electron 主进程在 Windows 上收不到终端 Ctrl+C 的 JS 信号：
// process.on("SIGINT") 永远不触发，主进程是被系统直接终止的（实测连
// process.on("exit") 都不执行）。于是 src/main.js 里 before-quit 那套「杀进程树、
// 等端口空」根本没机会跑，sidecar 和 Vite 变成没人认领的孤儿，下一次启动就撞
// 「端口被占用」。
//
// 这里换一条路。两条性质都是实测出来的：
//
//   1. 纯 node 收得到 JS 层 SIGINT（Electron 主进程收不到）；
//   2. detached + windowsHide 起 Electron，它自成一个进程组、不共享本终端的
//      控制台 —— 于是终端的 Ctrl+C 只打到启动器，Electron 那棵树毫发无损。
//
// 启动器再 taskkill /T 把 Electron 连同所有后代一次收掉。关键在于**此刻根进程
// 还活着**：要是先让 Electron 死掉，它的 stdout 管道写端随之断开，根上的
// cmd.exe 会跟着自己退出，taskkill 就扑空，pnpm/vite 子树原样留下。
//
// 托盘「退出」走的是另一条路（main.js 自己的 before-quit），两条互不干扰。
//
// 只服务开发：打包态由 electron-builder 直接跑 src/main.js，不经过这里。
//
// 往终端写的日志一律用 ASCII。中文 Windows 终端按 GBK 解码，Node 写出的是 UTF-8，
// 在这里会变乱码 —— main.js 顶部为此专门做过 GBK 编码（见那里的注释），这里
// 只有寥寥几行，不值得再抄一遍。

import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
// apps/desktop —— 和 `electron .` 原本的 cwd 保持一致，别让相对路径解析跑偏。
const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

let electronBinary;
try {
  // electron 这个包只在「以 node 运行时」导出 exe 路径；被当成 Electron 主进程
  // 跑时会是个对象，那说明这个脚本被用错了地方。
  electronBinary = require("electron");
} catch {
  electronBinary = null;
}
if (typeof electronBinary !== "string" || !electronBinary) {
  console.error("[dev] electron package not found. Run `pnpm install` in apps/ first.");
  process.exit(1);
}

const child = spawn(electronBinary, ["."], {
  cwd: appDir,
  detached: true,
  windowsHide: true,
  // 日志照旧打到当前终端：句柄是继承来的，Electron 自己那套 GBK 转发照常工作。
  stdio: "inherit",
});

let stopping = false;
let giveUp = null;

function stopWholeTree() {
  if (stopping) return;
  stopping = true;
  const pid = child.pid;
  if (!pid) return;
  if (process.platform === "win32") {
    // /T 连后代一起收；/F 是因为它此刻收不到任何优雅退出的机会。
    spawn("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true, stdio: "ignore" });
  } else {
    // detached 让子进程自成一组，负 pid 就是整组。
    try {
      process.kill(-pid, "SIGTERM");
    } catch {
      /* 已经没了 */
    }
  }
  // taskkill 是异步的。等 Electron 真的退出再走 —— 否则 pnpm 这层先返回了，
  // 用户以为命令已经结束，其实树还在收。
  giveUp = setTimeout(() => process.exit(0), 5000);
}

// SIGHUP 是关掉终端窗口时来的（libuv 把 CTRL_CLOSE_EVENT 映射成它）。
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    console.log(`[dev] ${signal} received - killing the whole Electron process tree`);
    stopWholeTree();
  });
}

child.on("error", (error) => {
  if (giveUp) clearTimeout(giveUp);
  console.error(`[dev] failed to start electron: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code, signal) => {
  if (giveUp) clearTimeout(giveUp);
  console.log(`[dev] electron exited (code=${code}, signal=${signal})`);
  // 用户从托盘正常退出时也走这里，退出码要如实传出去。
  process.exit(typeof code === "number" ? code : 0);
});
