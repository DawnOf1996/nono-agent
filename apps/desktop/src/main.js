const { app, BrowserWindow, Menu, Tray, WebContentsView, clipboard, dialog, ipcMain, nativeImage, nativeTheme, shell, webContents } = require("electron");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");
const util = require("node:util");

// 中文 Windows 的 Cursor / Git Bash 终端按系统代码页（CP936）解码管道输出。
// Electron 的 console.log 走 Chromium，写出 UTF-8，于是「内置」变成「鍐呯疆」。
// Python sidecar 按 GBK 写，所以同一窗口里 sidecar 中文正常、主进程中文乱码。
// console.log 不经过 process.stdout.write，必须自己按 GBK 写到 fd。
function createGbkEncoder() {
  let table = null;
  const decoder = new TextDecoder("gbk");
  return (text) => {
    if (!table) {
      table = new Map();
      const pair = new Uint8Array(2);
      for (let lead = 0x81; lead <= 0xfe; lead++) {
        for (let trail = 0x40; trail <= 0xfe; trail++) {
          if (trail === 0x7f) continue;
          pair[0] = lead;
          pair[1] = trail;
          const ch = decoder.decode(pair);
          if (ch && ch !== "\uFFFD" && ch.length === 1 && !table.has(ch)) {
            table.set(ch, (lead << 8) | trail);
          }
        }
      }
    }
    const bytes = [];
    for (const ch of text) {
      const code = ch.codePointAt(0);
      if (code < 0x80) {
        bytes.push(code);
        continue;
      }
      const packed = table.get(ch);
      if (packed == null) {
        bytes.push(0x3f);
        continue;
      }
      bytes.push((packed >> 8) & 0xff, packed & 0xff);
    }
    return Buffer.from(bytes);
  };
}

function patchWindowsConsoleGbk() {
  if (process.platform !== "win32") return;
  const encodeGbk = createGbkEncoder();
  // 行尾必须是 CRLF，不能是裸 LF。
  //
  // pnpm 和 Git Bash 都把 stdout 接成管道/pty，不是控制台。裸 \n 在那里只换行、
  // 不回车 —— 每一行的起点就成了上一行的终点，输出于是变成阶梯：
  //
  //     $ pnpm --filter @nono/web dev
  //                              $ vite     <- 缩进正好等于上一行的长度
  //
  // 同一窗口里 sidecar 的 Python 日志一直是整齐的，因为它走文本模式、自己把 \n
  // 翻成了 \r\n（实测 node 写 "AAA\n"，Python 文本模式写 "AAA\r\n"）。所以乱的
  // 只有主进程这一路。
  const eol = "\r\n";
  const bind = (fd) => (...args) => {
    // 用 writeFileSync 而不是 writeSync：前者对非阻塞 fd 会自己重试、也会写完整个
    // 缓冲区。后者只写一次就返回，长日志会被截断，管道忙时还会抛 EAGAIN。
    fs.writeFileSync(fd, encodeGbk(`${util.format(...args)}${eol}`));
  };
  console.log = bind(1);
  console.info = bind(1);
  console.warn = bind(2);
  console.error = bind(2);
  console.debug = bind(2);
}
patchWindowsConsoleGbk();

// 开发态的三个地址必须从**同一个**端口推导出来。
//
// 以前这里是两套互不相干的来源：探针读 NONO_SIDECAR_URL（默认 8000），而 spawn
// 出去的 sidecar 读 NONO_PORT。开发态分支里根本没把端口传给子进程，全靠继承
// process.env —— 于是「设了 NONO_PORT=8001」会变成「探针还在打 8000、sidecar 却
// 绑 8001」，永远探不到，10 秒后必然掉线报 offline。
//
// 现在端口收口到 devSidecarPort()，探针、spawn、vite 代理三边共用同一个值。
const DEV_DEFAULT_PORT = 8000;

function devSidecarPort() {
  const raw = (process.env.NONO_PORT || "").trim();
  if (!raw) return DEV_DEFAULT_PORT;
  const parsed = Number(raw);
  if (!Number.isInteger(parsed) || parsed <= 0 || parsed > 65535) {
    console.warn(`NONO_PORT=${raw} 不是合法端口，回落 ${DEV_DEFAULT_PORT}`);
    return DEV_DEFAULT_PORT;
  }
  return parsed;
}

/** 取 origin，容忍用户写进 NONO_WEB_URL 的畸形值 —— 这里抛异常会直接掀翻整个启动。 */
function safeOrigin(value, fallback) {
  try {
    return new URL(value).origin;
  } catch {
    console.warn(`地址 ${value} 不合法，回落 ${fallback}`);
    return fallback;
  }
}

// **显式指向别处**的地址。这两个变量一旦有值，桌面壳就不再自己拉起对应的进程：
// 网页和引擎会连它们指的那个服务，我们这个端口上起来的东西没人用 —— 白占一个
// 端口，还多一份不在你眼前的日志。mock-sidecar（scripts/dev/mock-sidecar.mjs）
// 走的就是 NONO_SIDECAR_URL。
//
// 这跟「探到端口上有服务就复用」是两回事：那是**猜**，这是用户点名。区别与理由
// 见下面 ensureSidecar 上方那段「进程所有权」。
const externalSidecarOrigin = (process.env.NONO_SIDECAR_URL || "").trim().replace(/\/+$/, "");
const externalWebUrl = (process.env.NONO_WEB_URL || "").trim();

const DEV_WEB_URL = externalWebUrl || "http://127.0.0.1:5173";
const DEV_SIDECAR_ORIGIN = externalSidecarOrigin || `http://127.0.0.1:${devSidecarPort()}`;
const DEV_SIDECAR_HEALTH = `${DEV_SIDECAR_ORIGIN}/health`;
const DEV_WEB_ORIGIN = safeOrigin(DEV_WEB_URL, "http://127.0.0.1:5173");

function devWebPort() {
  try {
    const url = new URL(DEV_WEB_URL);
    if (url.port) return Number(url.port);
    return url.protocol === "https:" ? 443 : 80;
  } catch {
    return 5173;
  }
}

// 外部 CDP 调试端口 —— **默认不开**，只有用户在设置页显式打开才会挂。
//
// 给的是「让外部工具驱动内置浏览器 <webview>」这条通路（实测 Electron 会把
// <webview> 以 type=webview 的 target 暴露出来）。之所以门控得这么死，是因为
// CDP 端点没有任何认证：一旦常开，本机任何进程都能驱动整个 Electron 应用 ——
// 不只是那个 webview，主窗口、终端、设置页全都算内；而主窗口的渲染进程带着
// preload 桥（openPath / openExternal / 剪贴板 / relaunch），等于凭空多出一条
// 「任意本地进程 → 在应用上下文里执行代码」的通路。
//
// 开关放在设置页的**插件面板**里，而不是命令行：能在屏幕前决定这件事的应该是
// 用户本人。见 https://github.com/vercel-labs/agent-browser 之外 BrowserSkill 的
// 教训 ——「命令行参数是个糟糕的同意决策场所」。Agent 能自己改的开关不叫开关。
//
// 配置本体是 ``<数据目录>/settings.json`` 里的 ``tools["browser-agent"].enabled``
// —— 和其它工具插件同一份文件、同一套面板，所以用户只有一个地方要管。
//
// **没有环境变量后门。** 这里曾经留过 NONO_CDP_PORT 供开发态快速验证，后来去掉了：
// 一个能被环境变量悄悄打开的端口，等于给「默认关闭」开了个后门。开发态要测也走
// 插件面板那个开关 + 重启，那正好就是用户真实会走的路径。
//
// 端口只能在 app ready 之前挂上，所以改开关必须重启应用（面板会提示并提供
// 重启入口，走 relaunch）。
// 端口传 **0** =「让 Chromium 自己挑一个空闲端口」。挑完之后它会把真实端口写进
// ``<userData>/DevToolsActivePort``，:func:`resolveCdpPort` 再读回来。
//
// 为什么不再写死 9222：agent-browser 的 daemon 是**跨 Electron 存活的孤儿进程**
// （父进程一死就被重新挂载，`taskkill /T` 那棵树根本够不着它）。Electron 退出时
// 它还连着调试端口，那些连接处于半关闭状态，而监听进程已经死了、没法完成拆除，
// 内核里的 TCB 就永远停在 CLOSE_WAIT —— **连 Listener 一起不释放**。实测：正常
// 退出再启动，9222 必然绑不上（winerror 10048），杀掉 daemon 也救不回来，只有
// 重启系统能回收。
//
// 自选端口让这一整类失败直接消失：上一个实例漏掉 9222 也无所谓，下次会挑别的；
// 开发版和已安装版也可以同时跑，不必互相抢同一个号。
const BROWSER_AGENT_PORT = 0;

// 当前生效的 CDP 端口。``resolveCdpPort()`` 跑完之前是 0，表示「还不知道」；
// 一直拿不到就一直是 0，而 ``sidecarSpec`` 正是靠这个 0 来决定**不**告诉
// sidecar「浏览器控制已开启」。
let browserAgentPort = 0;

// agent-browser 可执行文件。**可选依赖**：没有它插件仍然能开标签（那部分只走
// 渲染进程的 SSE 总线），只是读不了页面、点不了元素 —— 引擎侧会检测到并主动
// 说明原因，而不是抛一个「找不到文件」。
const BROWSER_AGENT_BINARY = "agent-browser-win32-x64.exe";

function agentBrowserBinaryPath() {
  if (!isPackaged()) {
    // 开发态指 apps/resources/（提交进仓库的可外挂输入），不是
    // apps/desktop/resources/ —— 后者是 pack-desktop 每次打包现建现删的组装区，
    // 跑一次打包就没了，开发时反而最先失效。
    return path.join(__dirname, "..", "..", "resources", "agent-browser", BROWSER_AGENT_BINARY);
  }
  return path.join(process.resourcesPath, "agent-browser", BROWSER_AGENT_BINARY);
}

// 与 nono_agent/paths.py 的 user_home() 同序：环境变量 > 指针文件 > 默认位置。
// 指针文件路径本身是固定的（默认位置下的 data-dir.json），所以这里不需要复刻
// 那套解析 —— 只需要跟着读同一个文件。
function resolveDataDir() {
  const fromEnv = (process.env.NONO_DATA_DIR || "").trim();
  if (fromEnv) return fromEnv;
  const pointer = path.join(os.homedir(), ".nono-agent", "data-dir.json");
  try {
    const parsed = JSON.parse(fs.readFileSync(pointer, "utf8"));
    const dir = typeof parsed?.data_dir === "string" ? parsed.data_dir.trim() : "";
    if (dir) return dir;
  } catch {
    // 没有指针文件 / 内容坏了 —— 回落默认位置。
  }
  return path.join(os.homedir(), ".nono-agent");
}

// **只读用户级那一份**，绝不看工作区的 ``.nono-agent/settings.json``。
// 项目层在 Settings 合并里优先级更高（见 toolplugins/service.py 的说明），
// 一个工作区完全可能顺手写上 ``tools["browser-agent"].enabled = true``。工具侧
// 有 fail-fast 兜底，但那是第二道防线；这里的第一道是压根不看那份文件。
function readBrowserAgentEnabled() {
  try {
    const settings = JSON.parse(
      fs.readFileSync(path.join(resolveDataDir(), "settings.json"), "utf8"),
    );
    const block = settings?.tools?.["browser-agent"];
    return Boolean(block && block.enabled === true);
  } catch {
    // 读不到 / 坏了 / 都没有 —— 一律按「关」处理（fail-closed）。
    return false;
  }
}

const browserAgentEnabled = readBrowserAgentEnabled();

if (browserAgentEnabled) {
  // 端口 0：让 Chromium 自己挑一个空闲端口，避免和上一个没退干净的实例抢号。
  app.commandLine.appendSwitch("remote-debugging-port", String(BROWSER_AGENT_PORT));
  // 地址写死回环：绑 0.0.0.0 等于把整个桌面应用敞到局域网上。
  app.commandLine.appendSwitch("remote-debugging-address", "127.0.0.1");

  // 先把**上一次**留下的 DevToolsActivePort 删掉。
  //
  // 不删的话有一个真实的竞态：新实例起来到 Chromium 覆写这个文件之间，旧的还在，
  // resolveCdpPort() 会把上一个实例的端口当成自己的交给 sidecar —— 于是工具对着
  // 一个已经没人监听的端口操作，而日志上还显示「端口就绪」。
  //
  // 必须在 app ready 之前删：那时 Chromium 还没写盘，删的就是纯旧的；ready 之后再
  // 删就可能和它自己的写入撞上。app.getPath("userData") 在 ready 前就能取到。
  try {
    fs.rmSync(path.join(app.getPath("userData"), "DevToolsActivePort"), { force: true });
  } catch {
    // 读不到用户目录就算了，resolveCdpPort() 还有端口探不通的兜底。
  }

  console.log("[cdp] 已请求内置浏览器控制，端口由 Chromium 自选。");
}

/**
 * 读回 Chromium 自己挑的那个端口，读到就写进 ``browserAgentPort``。
 *
 * **这个文件是异步落盘的**：它可能晚于 app ready 才出现，也可能先写一半 ——
 * 第一行只有 ``127.0.0.1:`` 而端口号还没落进去。所以这里轮询到拿到一个合法
 * 整数为止，绝不把半截内容当成结果。
 *
 * 返回 0 表示读不到 —— 调用方据此**不**把「已开启」告诉 sidecar，而不是塞一个
 * 猜的端口进去让它对着空气报错。
 */
async function resolveCdpPort() {
  const file = path.join(app.getPath("userData"), "DevToolsActivePort");
  const deadline = Date.now() + 15_000;
  let sawPartial = false;
  while (Date.now() < deadline) {
    try {
      const raw = fs.readFileSync(file, "utf8");
      const first = (raw.split(/\r?\n/, 1)[0] || "").trim();
      // 两个版本见过两种写法：纯端口号，或者 host:port。都取最后一段。
      const tail = first.includes(":") ? first.slice(first.lastIndexOf(":") + 1) : first;
      const port = Number(tail);
      if (Number.isInteger(port) && port > 0 && port < 65536) {
        return port;
      }
      sawPartial = true;
    } catch {
      // 文件还没出现 —— 继续等。
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  if (sawPartial) {
    console.warn(`[cdp] ${file} 里始终没有合法的端口号，疑似写到一半卡住了。`);
  }
  return 0;
}

/**
 * 端口定下来之后探一次，把「自选出端口」落实成「真的在应答」。
 *
 * 这不是锦上添花：没有它，端口出问题时整条 Agent 浏览器链路会一路超时，而唯一
 * 的线索是主进程自己打的那句「已请求」。
 */
async function verifyCdpPort() {
  const url = `http://127.0.0.1:${browserAgentPort}/json/version`;
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(3000) });
    if (response.ok) {
      console.log(`[cdp] 内置浏览器控制端口就绪：127.0.0.1:${browserAgentPort}`);
      return;
    }
    console.warn(`[cdp] 端口 ${browserAgentPort} 返回 ${response.status}，按不可用处理。`);
  } catch {
    console.warn(`[cdp] 内置浏览器控制端口 ${browserAgentPort} 没有应答。`);
    console.warn("[cdp] 文件在、端口却不通，通常是有别的进程抢占了刚挑中的号；重开应用一般就换到别的号了。");
  }
}

let sidecar = null;
// 开发态由桌面壳拉起的 Vite。用户手起的那个不会被记在这里，也就不会被我们杀。
let webServer = null;
let mainWindow = null;
let tray = null;
let quitting = false;
// 退出收尾的状态机：idle → running → done。托盘退出要等端口空出来才能真退，
// 所以第一次 before-quit 会被拦下来（理由见 cleanupOwnedProcesses）。
let cleanupState = "idle";

// 停靠在内部浏览器面板里的 DevTools 宿主视图。一次只有一个。
let devToolsView = null;
let devToolsWindow = null;
let devToolsTargetId = null;

function isPackaged() {
  return app.isPackaged || process.env.NONO_DESKTOP_PACKAGED === "1";
}

function pickFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 8000;
      server.close((err) => (err ? reject(err) : resolve(port)));
    });
  });
}

async function sidecarUp(healthUrl) {
  try {
    const response = await fetch(healthUrl, { signal: AbortSignal.timeout(1500) });
    return response.ok;
  } catch {
    return false;
  }
}

/** 轮询到健康为止；超时返回 false。 */
async function waitHealthy(healthUrl, timeoutMs, delayMs) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (await sidecarUp(healthUrl)) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
}

// 「这个端口有没有人在听」。
//
// 只用来回答「要不要 spawn」，不当作占用检测的最终结论。超时设 1.5s 是有依据的：
// 连**有监听**的回环端口是 0ms（见 server/nono_app_server/main.py 里关于 WFP
// filter 的那段注释——装了那类 filter 时，连空闲端口反而要空等约 2s），所以这个
// 超时只会在「端口确实空着」时被耗掉，而那种情况本来就是我们要的答案。
function portInUse(port, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const socket = net.connect({ host: "127.0.0.1", port });
    const done = (value) => {
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

/** 等端口真正空出来。Vite 是 strictPort，旧进程不彻底退出，新进程会直接起不来。 */
async function waitPortFree(port, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    if (!(await portInUse(port, 400))) return true;
    if (Date.now() >= deadline) return false;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
}

// ---------------------------------------------------------------------------
// 进程所有权：一个进程，只服务它自己启动的那些依赖
//
// 一条规则贯穿下面两个 ensure：**端口上已有的服务一律不接管，只自己拉起的才算。**
// 这不是洁癖，是被现实按出来的 ——
//
// 复用来的 sidecar 带着**上一个 Electron 实例**注入的环境变量。浏览器控制的
// CDP 端口就是这么变成过期值的：主进程这边刚让 Chromium 自选到 57524，引擎那边
// 却还在戳上一个实例的 9222，最后报一句「端口没绑上」—— 而端口其实好好的，
// 57524 上 /json/version 正常回。这个错误提示还是**误导**的：它把原因归给
// 「端口被占」，真凶恰恰就是「端口被复用」。
//
// 更麻烦的是这种状态没法自愈：进程活着、/health 照常回 ok，应用内部察觉不到，
// 只有等某次真正操作页面时才炸出来。所以现在改成：端口空就自己起，被占就明确
// 报错并且不接管；退出时只回收自己起的那几个 —— 进程和它的主人始终是同一个。
//
// 仍然存在的两条「不自己起」的口子，都是用户**点名**的，不是猜的：
// NONO_SIDECAR=0 / NONO_WEB=0（这个我全权负责），以及上面那两个 URL 变量。
// ---------------------------------------------------------------------------

/** netstat / lsof 的输出里找出监听该端口的那个进程。查不到就返回空串。 */
function parsePortHolder(output, port) {
  const text = String(output || "");
  if (process.platform === "win32") {
    // `  TCP    127.0.0.1:8000    0.0.0.0:0    LISTENING    2740`
    for (const line of text.split(/\r?\n/)) {
      const hit = line.match(/^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$/i);
      if (hit && Number(hit[1]) === port) return describePid(Number(hit[2]));
    }
    return "";
  }
  // lsof：`python  2740  user  21u  IPv4  12345  0t0  TCP *:8000 (LISTEN)`
  for (const line of text.split(/\r?\n/)) {
    if (!new RegExp(`:${port}\\s+\\(LISTEN\\)`).test(line)) continue;
    const hit = line.trim().match(/^(\S+)\s+(\d+)\s+/);
    if (hit) return `${hit[1]}（PID ${hit[2]}）`;
  }
  return "";
}

function describePid(pid) {
  try {
    process.kill(pid, 0);
    return `PID ${pid}`;
  } catch (error) {
    // ESRCH：内核里还挂着一条属于死进程的监听 socket —— 进程没了、连接停在
    // CLOSE_WAIT，Listener 也没释放（main.js 顶部讲 9222 那段就是这个坑）。这种
    // 占位杀不掉也等不掉，只能把「进程已经不在了」说出来，否则用户会去 taskkill
    // 一个空 PID还以为杀成功了。
    return error?.code === "EPERM"
      ? `PID ${pid}（没有权限查看）`
      : `PID ${pid}（进程已经不在了，是残留的 socket）`;
  }
}

/**
 * 谁占着这个端口 —— 同步查，**尽力而为**：工具不在、没权限、超时都只返回空串。
 *
 * 刻意用 spawnSync 而不是异步 spawn：这条路径的终点就是弹模态框，而模态框会卡住
 * 主进程的消息循环，于是并发的另一个冲突检查（比如同一轮启动里的 sidecar 与 Vite）
 * 的 netstat 定时器会先于它的输出被处理，把已经到手的答案白白丢掉。实测就踩到过：
 * 同一个启动里 5173 报出了占用者 PID，8000 却显示「查不到」。同步查没有这个竞态，
 * 代价只是这条冷门路径上阻塞几十毫秒 —— 马上就要弹窗了，阻塞一下无所谓。
 */
function portHolder(port, timeoutMs = 1500) {
  try {
    const isWin = process.platform === "win32";
    const result = spawnSync(
      isWin ? "netstat" : "lsof",
      isWin ? ["-ano", "-p", "tcp"] : ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN"],
      {
        encoding: "utf8",
        timeout: timeoutMs,
        windowsHide: true,
        stdio: ["ignore", "pipe", "ignore"],
      },
    );
    if (result.error || result.status !== 0) return "";
    return parsePortHolder(result.stdout, port);
  } catch {
    return "";
  }
}

/**
 * 端口被一个**不是我们拉起**的进程占了：把话说清楚，然后不接管。
 *
 * 这个分支必须有人看见，所以开发态弹窗。超时不弹 —— 冷启动慢是常事，不该拦人；
 * 而端口被占是确定性的配置错误，不弹的话界面会一直离线（sidecar）或一直停在
 * 加载页（Vite），原因却只写在这次启动的终端里。
 */
function reportPortConflict(label, port, remedies) {
  const holder = portHolder(port);
  const who = holder ? `占用者：${holder}` : "占用者：查不到（工具缺失或权限不足）";
  console.error(`[${label}] 127.0.0.1:${port} 已经被别的进程占用，这次不接管它。`);
  console.error(`[${label}]   ${who}`);
  console.error(`[${label}]   不接管是因为它带着自己主人的环境变量跑着，退出时我们也不能`);
  console.error(`[${label}]   替用户杀它 —— 那种状态既不可控也没法自愈。`);
  for (const line of remedies) console.error(`[${label}]   · ${line}`);
  if (!isPackaged()) {
    dialog.showErrorBox(
      `${label} 端口被占用`,
      [
        `127.0.0.1:${port} 已经被别的进程占用，桌面壳不会接管别人的服务。`,
        holder ? `\n占用者：${holder}` : "",
        `\n${remedies.map((line) => `· ${line}`).join("\n")}`,
      ].join(""),
    );
  }
}

function sidecarSpec(port) {
  if (isPackaged()) {
    const exeName = process.platform === "win32" ? "nono-app-server.exe" : "nono-app-server";
    const exe = path.join(process.resourcesPath, "sidecar", exeName);
    return {
      command: exe,
      args: [],
      cwd: path.dirname(exe),
      env: {
        NONO_HOST: "127.0.0.1",
        NONO_PORT: String(port),
        NONO_WEB_DIST: path.join(process.resourcesPath, "web"),
        NONO_WEB_ORIGIN: `http://127.0.0.1:${port}`,
        NONO_SIDECAR_LOG: path.join(app.getPath("userData"), "sidecar.log"),
        // 端口真拿到了才告诉 sidecar。开关开着但端口读不回来时，这几个键干脆
        // 不出现 —— 引擎侧要能靠「有没有这个变量」区分「用户没开」和
        // 「开了但端口没落地」，两者的补救动作完全不同。
        ...(browserAgentPort > 0
          ? {
              NONO_BROWSER_CDP_ENABLED: "1",
              NONO_BROWSER_CDP_PORT: String(browserAgentPort),
              NONO_BROWSER_BIN: agentBrowserBinaryPath(),
            }
          : {}),
      },
    };
  }
  return {
    command: "uv",
    args: ["run", "nono-app-server"],
    cwd: path.resolve(__dirname, "..", "..", "server"),
    // 端口与 origin 显式传下去，不能指望继承父进程 env —— 父进程里很可能根本没有
    // NONO_PORT，而 Vite 是按 NONO_PORT 决定代理目标的；两边必须同源。
    env: {
      NONO_HOST: "127.0.0.1",
      NONO_PORT: String(port),
      // 开发态网页跑在 Vite 上，与 sidecar 不同源，写清楚 CORS 白名单。
      NONO_WEB_ORIGIN: DEV_WEB_ORIGIN,
      // 同打包态：端口真拿到了才告诉 sidecar，否则引擎侧会以为「开关开了」。
      ...(browserAgentPort > 0
        ? {
            NONO_BROWSER_CDP_ENABLED: "1",
            NONO_BROWSER_CDP_PORT: String(browserAgentPort),
            NONO_BROWSER_BIN: agentBrowserBinaryPath(),
          }
        : {}),
    },
  };
}

// ---------------------------------------------------------------------------
// 子进程输出的行尾归一
//
// 同一个终端里混着两种行尾：pnpm / vite / node 这类工具写出的是**裸 LF**
// （实测 `pnpm --filter @nono/web dev` 的 stdout+stderr 共 7 处换行、0 个 CR），
// 而 sidecar 的 Python 走文本模式、发的是 CRLF。Git Bash 的 pty 对裸 LF 不做
// 回车，于是每一行的起点停在上一行的终点，输出变成阶梯：
//
//     $ pnpm --filter @nono/web dev
//                              $ vite     <- 缩进 29 列，正好是上一行的长度
//
// 我们改不了 pnpm/vite 的字节，但可以接管它们的管道。做法是给每个子进程
// pipe 而不是 inherit，读出来补成 CRLF 再转发到自己的 fd。
//
// 只动行尾那一个字节，其余**原样透传、绝不重新编码** —— sidecar 是 GBK、
// vite 是 UTF-8，任何一层转码都会把中文打碎（打包态压根不转发，见 startSidecar）。
// ---------------------------------------------------------------------------

function pipeForwarder(child) {
  // lastWasCR 必须跨 chunk 保留：一次 read 正好切在 \r 和 \n 中间的话，
  // 逐 chunk 判断会把那个 \n 误判成裸 LF，凭空多插一个 \r。
  let lastWasCR = false;
  const normalize = (chunk) => {
    const bytes = [];
    for (const byte of chunk) {
      if (byte === 0x0a && !lastWasCR) bytes.push(0x0d);
      bytes.push(byte);
      lastWasCR = byte === 0x0d;
    }
    return Buffer.from(bytes);
  };
  for (const [stream, fd] of [
    [child.stdout, 1],
    [child.stderr, 2],
  ]) {
    if (!stream) continue;
    stream.on("data", (chunk) => {
      // 终端先关掉了（用户 Ctrl+C / 关窗口）就别再往里写，EPIPE 不该掀翻主进程。
      try {
        fs.writeFileSync(fd, normalize(chunk));
      } catch {
        /* 终端没了，丢弃即可 */
      }
    });
  }
}

function startSidecar(port) {
  const spec = sidecarSpec(port);
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: { ...process.env, PYTHONUNBUFFERED: "1", ...spec.env },
    windowsHide: true,
    // 打包态没有终端可转发，直接丢弃；开发态接管管道做行尾归一。
    stdio: isPackaged() ? "ignore" : ["ignore", "pipe", "pipe"],
  });
  if (!isPackaged()) pipeForwarder(child);
  sidecar = child;
  child.on("exit", (code) => {
    if (sidecar === child) sidecar = null;
    console.log(`sidecar exited with code ${code}`);
  });
}

// 回收子进程。Windows 上必须 taskkill /T：pnpm 是壳，真正占着端口的是它下面
// 那个 node / vite，kill 掉壳本身端口不会释放。
function killTree(child) {
  if (!child) return;
  const pid = child.pid;
  if (process.platform === "win32" && pid) {
    spawn("taskkill", ["/pid", String(pid), "/T", "/F"], {
      windowsHide: true,
      stdio: "ignore",
    });
    return;
  }
  if (pid && !child.killed) {
    child.kill("SIGTERM");
  }
}

// 收尾时要知道「到底有没有子进程要杀」：没有就不该去等那个端口。
function stopSidecar() {
  const child = sidecar;
  sidecar = null;
  if (!child) return false;
  killTree(child);
  return true;
}

function stopWebServer() {
  const child = webServer;
  webServer = null;
  if (!child) return false;
  killTree(child);
  return true;
}

/**
 * 退出前的收尾：杀掉自己拉起的进程，**并且等端口真的空出来**才返回。
 *
 * 为什么不能「spawn 完 taskkill 就走」：Windows 上 taskkill 是异步的，而 Vite 那条
 * 链的根是 cmd.exe —— 主进程一退出，它继承的 stdout 管道写端就断了，cmd.exe 跟着
 * 自己退出，taskkill 就扑空了。实测（Electron + 三层进程链，最深一层真监听端口）：
 * killTree 之后主进程只多活 18ms 时，整条 cmd → pnpm → cmd → vite 子树原样留下、
 * 继续占着 5173；同一段 killTree 只要主进程多活约 700ms，两棵树连端口一起收得干
 * 干净净。relaunch 那条路一直就是等端口的（见 ipcMain "relaunch"），托盘退出没沿用
 * 同一套，于是每次只收得回 sidecar。
 *
 * 只等**自己杀过**的那个端口：NONO_SIDECAR=0 / NONO_WEB=0 时端口上是用户自己的服务，
 * 等它是白等 5 秒，还会让退出看起来像卡住了。
 */
async function cleanupOwnedProcesses() {
  const killedSidecar = stopSidecar();
  const killedWeb = stopWebServer();
  if (isPackaged()) {
    // 打包态网页是 sidecar 里的静态文件，没有额外进程；等一小段让 taskkill 落地。
    await new Promise((resolve) => setTimeout(resolve, 400));
    return;
  }
  if (killedSidecar && !(await waitPortFree(devSidecarPort(), 5000))) {
    console.warn(`[sidecar] 127.0.0.1:${devSidecarPort()} 5s 内没空出来。`);
  }
  if (killedWeb && !(await waitPortFree(devWebPort(), 5000))) {
    console.warn(`[web] 127.0.0.1:${devWebPort()} 5s 内没空出来。`);
  }
}

/**
 * 拉起 sidecar。**永远自己起**：端口上有东西就是冲突，不复用（理由见上面那段
 * 「进程所有权」）。
 *
 * 返回状态而不是布尔，因为启动流程要区分「没起来」和「不归我管」——
 * 前者要提示用户，后者一句日志就够，弹窗和报错都是噪音。
 * ``started`` / ``external`` / ``conflict`` / ``failed``。
 */
async function ensureSidecar(healthUrl, port) {
  if ((process.env.NONO_SIDECAR || "").trim() === "0") {
    console.log("[sidecar] NONO_SIDECAR=0：后端不由本进程拉起，退出时也不管它。");
    return "external";
  }
  if (externalSidecarOrigin) {
    console.log(`[sidecar] NONO_SIDECAR_URL=${externalSidecarOrigin}：用它，不自己起 sidecar。`);
    return "external";
  }

  // 端口被占 = 那个进程不是我们起的。以前这里是「等它就绪然后复用」，代价是
  // 上面那段注释说的那整套不可控状态；现在只回答「能不能自己起」。
  //
  // 这个探针有 1.5s 的超时成本：端口空着的时候，连空回环端口反而要空等（装了那类
  // WFP filter 的机器上尤其明显，见 portInUse 自己的注释）。以前这里无论开发态
  // 打包态都要付一次 sidecarUp 的同款代价，所以并没有变慢。
  if (await portInUse(port)) {
    reportPortConflict("sidecar", port, [
      "关掉占用它的进程再重开本进程（多半是上一次没退干净的 dev:desktop）",
      '换端口：$env:NONO_PORT = "<空闲端口>"（Vite 会跟着用同一个）',
      "后端你自己管：设 NONO_SIDECAR=0，再配 NONO_SIDECAR_URL 可以指向别处",
    ]);
    return "conflict";
  }

  startSidecar(port);
  // 以前打包态 500ms 一轮，平均要多等 250ms 才发现 sidecar 好了。80ms 一轮在本机
  // 几乎不花钱：Node 的 fetch 连空回环端口走 ConnectEx + SIO_LOOPBACK_FAST_PATH，
  // 实测 1~40ms 就返回。打包态总预算 60s 不变。
  //
  // 开发态从 10s 放宽到 20s：桌面壳现在是开发态启动后端的**主路径**，冷启动时
  // `uv run nono-app-server` 可能要十几秒，10s 容易误判成失败。
  const timeoutMs = isPackaged() ? 60_000 : 20_000;
  if (await waitHealthy(healthUrl, timeoutMs, 80)) return "started";
  console.warn("sidecar did not become healthy in time; UI will show offline");
  return "failed";
}

// ---------------------------------------------------------------------------
// 开发态的 Vite
//
// 打包态网页是冻在 sidecar 里的静态资源，不需要额外进程；开发态要的是 Vite 的 HMR，
// 这个没法从 sidecar 里长出来。以前它只能手起，于是「起全栈」要开三个终端。
//
// 这里让桌面壳在发现 5173 没服务时自己拉一个 —— 与 sidecar 同一套所有权语义：
// 端口空就 spawn，端口被占就报错并**不接管**，退出时只回收自己 spawn 的那一个。
// （曾经是「已经在跑就复用」，代价见上面那段「进程所有权」。）
// ---------------------------------------------------------------------------

function startWebServer() {
  // Windows 上 pnpm 是 pnpm.cmd，Node 现在禁止直接 spawn .cmd（安全修复），所以走
  // cmd.exe。这里**刻意不用 shell:true** —— 它会往终端打一条 DEP0190 弃用警告，
  // 而 args 全是这里的字面量、没有任何用户输入，没有需要转义的东西。
  const isWindows = process.platform === "win32";
  const command = isWindows ? process.env.ComSpec || "cmd.exe" : "pnpm";
  const args = isWindows ? ["/d", "/s", "/c", "pnpm dev:web"] : ["dev:web"];
  const child = spawn(command, args, {
    // 两级：`__dirname` 是 apps/desktop/src，pnpm workspace 根在 apps/。
    // 少一级会落到 apps/desktop，那里的 package.json 根本没有 dev:web 这个脚本。
    cwd: path.resolve(__dirname, "..", ".."),
    // Vite 按 NONO_PORT 决定代理目标，必须和 sidecar 拿到同一个端口。
    env: { ...process.env, NONO_PORT: String(devSidecarPort()) },
    windowsHide: true,
    // 跟 sidecar 一样接管管道：Vite 的编译报错要看得见，同时行尾归一成 CRLF。
    stdio: ["ignore", "pipe", "pipe"],
  });
  pipeForwarder(child);
  webServer = child;
  child.on("exit", (code) => {
    if (webServer === child) webServer = null;
    console.log(`web dev server exited with code ${code}`);
  });
}

/**
 * 拉起开发态的 Vite。同样**永远自己起**（理由见上面那段「进程所有权」）。
 *
 * 返回状态与 :func:`ensureSidecar` 一致：``started`` / ``external`` /
 * ``conflict`` / ``failed``。
 */
async function ensureWebServer(webUrl) {
  if ((process.env.NONO_WEB || "").trim() === "0") {
    console.log("[web] NONO_WEB=0：网页不由本进程拉起，退出时也不管它。");
    return "external";
  }
  if (externalWebUrl) {
    console.log(`[web] NONO_WEB_URL=${externalWebUrl}：用它，不自己起 Vite。`);
    return "external";
  }

  const port = devWebPort();
  if (await portInUse(port)) {
    reportPortConflict("web", port, [
      "关掉占用它的进程再重开本进程（多半是上一次没退干净的 dev:desktop）",
      '换端口：$env:NONO_WEB_URL = "http://127.0.0.1:<空闲端口>"',
      "网页你自己起：设 NONO_WEB=0，再配 NONO_WEB_URL 指向你的地址",
    ]);
    return "conflict";
  }

  console.log(`[web] 拉起 Vite：${webUrl}`);
  startWebServer();
  if (await waitHealthy(webUrl, 30_000, 250)) return "started";
  console.warn(`web dev server did not come up in time: ${webUrl}`);
  return "failed";
}

function showWindow(win = mainWindow) {
  if (!win) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
}

function quitApp() {
  quitting = true;
  app.quit();
}

function resolveAppIcon() {
  const candidates = [
    path.join(__dirname, "..", "build", "icon.ico"),
    path.join(__dirname, "..", "build", "icon.png"),
    path.join(__dirname, "..", "..", "logo.png"),
    path.join(process.resourcesPath || "", "web", "logo.png"),
  ];
  return candidates.find((candidate) => candidate && fs.existsSync(candidate));
}

// 应用里选中的主题模式（light / dark / system）。null 表示还没收到网页的报信，
// 一切跟着系统走。
//
// 窗口按钮的符号色、窗口底色都是原生控件，只认 nativeTheme，而应用里的主题存在
// 网页自己的 localStorage 里，主进程读不到。以前两边各看各的：用户在应用里手动
// 选了深色、系统还是浅色时，右上角那三个按钮继续用深色图标，压在深色头带上几乎
// 看不见。网页启动和每次切换都会把模式报过来（lib/theme.ts 的 apply()）。
let appThemeMode = null;

/** 当前该按哪套配色渲染窗口。显式选择优先，没收到报信才看系统。 */
function isDarkTheme() {
  if (appThemeMode) return appThemeMode === "dark";
  return nativeTheme.shouldUseDarkColors;
}

// 加载页与应用本体必须同主题，否则 loadURL 那一刻会闪一次明暗翻转，白色主题用户
// 尤其明显（以前窗口底色写死 #14181F，深色用户也跟着受了一次多余的变色）。
// 两个值取自 apps/web/src/index.css 的 --color-bg。
//
// 加载页只在建窗那一次加载，那时网页还没报主题上来，所以这里多半还是系统的值：
// 对 theme=system（默认）的用户是准的；手动改过主题的用户在加载页会有一次轻微
// 不一致，但不会再是黑屏。
function appTheme() {
  return isDarkTheme() ? "dark" : "light";
}

function appBackground() {
  return isDarkTheme() ? "#16181d" : "#ffffff";
}

// 窗口头部的高度：页面最顶上那一整条空带，只负责一件事 —— 让用户抓着窗口走。
// 页面侧对应 App.tsx 里那条 h-9 的 .titlebar-drag，两边必须一致，改一个
// 就要改另一个（见 web/src/index.css 的同名段落）。
//
// 这一条不是原来那行 h-12 的页头。原来让页头自己当标题栏，两个毛病：内容挤在
// 窗口最上沿，看着像"只有正文、没有头部"；而 Windows 的窗口按钮以 overlay 浮
// 在右上角，压在页头上又得专门给它让出 138px。把头部腾成一条空的，这两件事
// 一起消失 —— 按钮浮在空处，下面三块页头（Sidebar 表头、对话页头、文件页头，
// 都是 h-10）规规矩矩待在下面。
const TITLEBAR_HEIGHT = 36;

// 窗口按钮的颜色是写死的，不跟页面的 CSS 走 —— 它们是原生控件。深色下要换成浅色
// 图标（#e6e8ec 就是应用深色主题的 --color-ink），否则压在深色头带上几乎看不见；
// 浅色下用深灰（#3c4048）。口径和 appTheme() 一致，但直接读 appThemeMode，不绕
// nativeTheme：Windows 上 themeSource 什么时候传到 shouldUseDarkColors 不好说，
// 而这里一旦算错，症状就是"深色主题配深色按钮"。
function overlaySymbolColor() {
  return isDarkTheme() ? "#e6e8ec" : "#3c4048";
}

// 按当前原生主题重刷窗口自身那些网页刷不到的东西：底色 + Windows 窗口按钮。
// 三处（建窗、nativeTheme 变化、渲染进程报来新主题）都走这一个函数，别各写一份。
function syncWindowChrome(win = mainWindow) {
  if (!win || win.isDestroyed()) return;
  win.setBackgroundColor(appBackground());
  if (process.platform === "win32") {
    // setTitleBarOverlay 是局部更新，但只给一个键时不同版本的合并行为不一致，稳一点全给。
    win.setTitleBarOverlay?.({
      color: "#00000000",
      symbolColor: overlaySymbolColor(),
      height: TITLEBAR_HEIGHT,
    });
  }
}

function createWindow() {
  const icon = resolveAppIcon();
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: appBackground(),
    // 这里刻意不写 autoHideMenuBar。那个选项的语义就是「按 Alt 才把菜单栏
    // 闪出来」，属于半吊子：用户不会去记这个约定，正式包更不该留一条按 Alt
    // 就能唤出的系统菜单。菜单在 whenReady 里用 setApplicationMenu(null)
    // 彻底不装，没有菜单栏，就没有东西可闪。
    // 原生标题栏（图标 + 产品名）让给页面自己画：内容顶到窗口最上沿。
    // 右上角三个窗口按钮仍是系统的，以 overlay 形式浮在网页上 —— 所以阴影、
    // resize 边框、Aero 贴靠最大化、鼠标拖到屏幕上半屏分屏全都还在。
    // 换成 frame:false 换来的是三键自由，代价是上面这些全丢，不划算。
    //
    // 只在 Windows / macOS 上让出标题栏：Linux 没有 window controls overlay，
    // 'hidden' 会把标题栏连同关闭键一起吃掉，用户就再也关不掉窗口了。
    ...(process.platform === "win32"
      ? {
          titleBarStyle: "hidden",
          // 透明覆盖层：页面自己的页头底色透上来，明暗主题切换时不会有一块
          // 写死的色和 --color-sidebar 打架。
          titleBarOverlay: {
            color: "#00000000",
            symbolColor: overlaySymbolColor(),
            height: TITLEBAR_HEIGHT,
          },
        }
      : {}),
    // macOS 收起标题栏后红绿灯会落到左上角、正好压住 Sidebar 的 logo，挪一下。
    // 坐标是照默认间距估的，真机上看着不对就直接调这两个数。
    ...(process.platform === "darwin"
      ? { titleBarStyle: "hidden", trafficLightPosition: { x: 14, y: 15 } }
      : {}),
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      // 侧边栏浏览器用 <webview> 而不是 <iframe>：webview 是独立的顶层浏览上下文，
      // 不受 X-Frame-Options / CSP frame-ancestors 约束，否则 github.com 这类
      // 明确禁止嵌入的站点会整个打不开。Electron 默认禁用该标签，必须显式打开。
      webviewTag: true,
      additionalArguments: [`--nono-app-version=${app.getVersion()}`],
    },
  });
  win.on("close", (event) => {
    if (quitting) return;
    event.preventDefault();
    win.hide();
  });
  // 跟随系统主题重刷窗口按钮。窗口关掉后把监听摘掉：main.js 是单进程常驻的，
  // 挂着就是纯泄漏。
  if (process.platform === "win32") {
    const syncOverlay = () => syncWindowChrome(win);
    nativeTheme.on("updated", syncOverlay);
    win.on("closed", () => nativeTheme.removeListener("updated", syncOverlay));
  }
  return win;
}

async function createTray(win) {
  const iconPath = resolveAppIcon();
  let icon = iconPath ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  if (icon.isEmpty()) {
    icon = await app.getFileIcon(process.execPath, { size: "small" });
  } else if (process.platform === "win32") {
    icon = icon.resize({ width: 16, height: 16 });
  }
  const trayIcon = new Tray(icon);
  trayIcon.setToolTip("NoNo");
  trayIcon.setContextMenu(
    Menu.buildFromTemplate([
      { label: "显示窗口", click: () => showWindow(win) },
      { type: "separator" },
      { label: "退出", click: () => quitApp() },
    ]),
  );
  trayIcon.on("click", () => showWindow(win));
  return trayIcon;
}

// ---------------------------------------------------------------------------
// 更新检测
//
// 通道的形状：安装包发到 GitHub Releases，electron-builder 顺带产出 latest.yml
// （里面写着最新版本号和安装包地址），并把同一个地址打进 app-update.yml，
// electron-updater 启动时读它就知道该去哪儿问。喂给谁由 package.json 的
// publish 段决定 —— 没有这一段，打包就不会产出 latest.yml，更新也就无从谈起。
//
// 三条约束：
// 1. 开发态没有 app-update.yml（那是 electron-builder 打进包里的），硬调
//    checkForUpdates() 会抛。所以只在打包态 require，且每个入口先问 isPackaged()。
// 2. autoDownload 保持 false：一次 130MB 的下载是用户的事，不该在他对话说到
//    一半时偷偷占带宽。检查和下载分成两步，由设置页那行按钮驱动。
// 3. 下载完没点「重启」也别浪费：autoInstallOnAppQuit 让用户正常退出时顺手装上。
// ---------------------------------------------------------------------------

let autoUpdater = null;
let updaterLoaded = false;

/** 把更新进度转给渲染进程。没窗口的时候直接丢 —— 托盘常驻期间事件可能比窗口活得久。 */
function sendUpdateEvent(payload) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send("app:update-event", payload);
  }
}

/** 首次调用时才 require electron-updater。开发态永远返回 null，调用方自己判。 */
function loadUpdater() {
  if (updaterLoaded) return autoUpdater;
  updaterLoaded = true;
  if (!isPackaged()) return null;

  const { autoUpdater: updater } = require("electron-updater");
  autoUpdater = updater;
  updater.autoDownload = false;
  updater.autoInstallOnAppQuit = true;

  updater.on("checking-for-update", () => sendUpdateEvent({ type: "checking" }));
  updater.on("update-available", (info) => {
    sendUpdateEvent({ type: "available", version: info.version });
  });
  updater.on("update-not-available", (info) => {
    sendUpdateEvent({ type: "latest", version: info?.version || null });
  });
  updater.on("download-progress", (progress) => {
    sendUpdateEvent({
      type: "progress",
      percent: Math.round(progress.percent),
      transferred: progress.transferred,
      total: progress.total,
    });
  });
  updater.on("update-downloaded", (info) => {
    sendUpdateEvent({ type: "downloaded", version: info.version });
  });
  updater.on("error", (error) => {
    // 检查失败绝大多数是断网或仓库还没搬过去，不该在用户面前变成一句红色报错，
    // 但也不能吞掉 —— 设置页那行要能告诉用户「这次没查成」。
    sendUpdateEvent({ type: "error", message: error?.message || String(error) });
  });
  return updater;
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
}

app.on("second-instance", () => {
  showWindow();
});

app.setAppUserModelId("com.nono.agent");

// 正文里的 target=_blank / window.open 会让 Electron 另开一个原生窗口。
// webview 的 new-window 事件在 Electron 37 已经不再触发（实测点击后事件不响，
// 反而多出两个进程），只能在主进程对 guest 的 WebContents 挂 setWindowOpenHandler，
// 把地址转回渲染进程，由它开成应用内的浏览器标签。
//
// 这里同时覆盖两类 guest：侧边栏浏览器的 <webview>，以及装着应用自己的主窗口
// （type 为 window）。主窗口里的正文链接 —— 对话气泡、README 预览 —— 走的就是
// 主窗口这条；漏掉它的话点一下链接整个应用就被新窗体顶到后面去了。
app.on("web-contents-created", (_event, contents) => {
  const type = contents.getType();
  if (type !== "webview" && type !== "window") return;
  contents.setWindowOpenHandler(({ url }) => {
    const owner = contents.getOwnerBrowserWindow();
    // 只有 http/https 能转成浏览器标签；别的协议没有安全的落地方式，直接丢弃，
    // 免得 file: 之类被当成「打开一个网页」而执行本地程序。
    if (owner && /^https?:/i.test(url)) owner.webContents.send("browser:new-tab", url);
    return { action: "deny" };
  });
});

if (gotLock) app.whenReady().then(async () => {
  // 顶部那条「应用 / 编辑」原生菜单栏：Windows / Linux 上一律不装，Alt 键
  // 按不出任何东西（不是 autoHideMenuBar 那种「按了才闪出来」的半吊子）。
  // 页面里已经有工作区、插件、设置这些入口，再挂一条原生菜单只是重复。
  // macOS 的菜单本来就在屏幕顶端的系统菜单栏里、不占窗口，但 Cmd+Q、复制
  // 粘贴这些系统快捷键要留着，所以那边换成一份精简模板。
  Menu.setApplicationMenu(
    process.platform === "darwin"
      ? Menu.buildFromTemplate([
          { role: "appMenu" },
          { role: "editMenu" },
          { role: "windowMenu" },
        ])
      : null,
  );

  ipcMain.handle("select-workspace", async () => {
    const result = await dialog.showOpenDialog({
      title: "选择工作空间",
      properties: ["openDirectory", "createDirectory"],
    });
    if (result.canceled || result.filePaths.length === 0) return null;
    return result.filePaths[0];
  });
  ipcMain.handle("open-path", async (_event, target) => {
    if (typeof target !== "string" || !target.trim()) return { ok: false };
    const error = await shell.openPath(target);
    return { ok: !error, error: error || undefined };
  });
  // 「在文件夹中显示」：打开系统文件管理器并定位到目标。
  //
  // 文件夹直接打开它自己；文件则让资源管理器打开它所在的目录并选中它
  // （showItemInFolder 就是这个语义）。定位不到就报错，别静悄悄地什么都不发生。
  ipcMain.handle("show-in-folder", async (_event, target) => {
    if (typeof target !== "string" || !target.trim()) return { ok: false, error: "缺少路径" };
    try {
      const stats = await fs.promises.stat(target);
      if (stats.isDirectory()) {
        const error = await shell.openPath(target);
        return { ok: !error, error: error || undefined };
      }
      shell.showItemInFolder(target);
      return { ok: true };
    } catch (cause) {
      return { ok: false, error: cause instanceof Error ? cause.message : "找不到这个路径" };
    }
  });
  // iframe 装不下禁止嵌入的站点，只能交给系统浏览器。渲染进程能把任意字符串递进来，
  // 所以这里只放行 http/https：file:// 与自定义协议（ms-msdt: 之类）会让 shell
  // 执行注册过的本地程序，不是「打开一个网页」该有的能力。
  ipcMain.handle("open-external", async (_event, target) => {
    if (typeof target !== "string" || !target.trim()) return { ok: false };
    let url;
    try {
      url = new URL(target);
    } catch {
      return { ok: false, error: "不是合法的 URL" };
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return { ok: false, error: "只允许打开 http/https 地址" };
    }
    await shell.openExternal(url.toString());
    return { ok: true };
  });
  // 截内部浏览器的视口截图。渲染进程碰不到 guest 的像素，只能由主进程截。
  //
  // 只截当前可见区域：整页长图得走 debugger 的 Page.captureScreenshot +
  // captureBeyondViewport，而用户开着 DevTools 时 debugger 已经被占住，会直接抛
  // "Another debugger is already attached"；长页面还会一次性拉进内存。
  //
  // webContentsId 是渲染进程从 <webview>.getWebContentsId() 拿的，可以随便递进来，
  // 所以必须确认目标是 webview 类型的 guest —— 否则渲染进程能指到主窗口自己，
  // 把整个应用界面截走。
  ipcMain.handle("browser:capture-viewport", async (_event, id) => {
    const target = typeof id === "number" ? webContents.fromId(id) : null;
    if (!target || target.isDestroyed()) return { ok: false, error: "页面还没加载好" };
    if (target.getType() !== "webview") return { ok: false, error: "只能截内部浏览器里的页面" };
    try {
      const png = (await target.capturePage()).toPNG();
      if (!png.length) return { ok: false, error: "这一页没有截到内容" };
      return { ok: true, data: png.toString("base64") };
    } catch (error) {
      return { ok: false, error: error instanceof Error ? error.message : "截图失败" };
    }
  });

  // 把 DevTools 停到内部浏览器面板里，而不是另开一个原生窗口。
  //
  // 绕过的坑：Electron 对 tag 形式的 contents 默认 mode:'detach'，直接 openDevTools()
  // 一定会甩一个独立窗口出来。文档给的出路是 setDevToolsWebContents() —— 把 DevTools
  // 前端的宿主 WebContents 换成我们指定的那个。
  //
  // 但**宿主不能用 <webview>**：试过，前端能加载（面板外壳完整渲染），却拿不到调试
  // 后端连接 —— 加载出来的地址是 devtools://.../devtools_app.html?remoteBase=https://
  // chrome-devtools-frontend.appspot.com，没有 ws= 参数，于是它去连公网那个 remoteBase，
  // 结果就是"标签页齐全、一条数据没有"。sandbox、partition、src 都调过了，均无效。
  //
  // 所以宿主改成主进程里的 WebContentsView：它是主窗口的原生顶层视图，不存在 guest
  // 会话那一层，前端和被调试页的连接都在主进程里完成。渲染进程那边只留一个空 div
  // 占位，把位置报过来给 setBounds。
  ipcMain.handle("browser:devtools-attach", async (event, targetId, bounds) => {
    const target = typeof targetId === "number" ? webContents.fromId(targetId) : null;
    if (!target || target.isDestroyed()) return { ok: false, error: "页面还没准备好" };
    if (target.getType() !== "webview") return { ok: false, error: "只能停靠内部浏览器里的页面" };
    const win = BrowserWindow.fromWebContents(event.sender);
    if (!win || win.isDestroyed()) return { ok: false, error: "找不到窗口" };

    // 重复点按钮时先收掉上一个，别叠出两层视图。
    detachDevTools();

    const view = new WebContentsView({
      webPreferences: {
        // 分区跟页面 webview 对齐（那边是 persist:nono-browser）。
        partition: "persist:nono-browser",
        // DevTools 前端要连调试后端，沙箱渲染进程给不了这个特权。
        sandbox: false,
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    devToolsView = view;
    devToolsWindow = win;
    devToolsTargetId = targetId;
    win.contentView.addChildView(view);
    view.setBounds(toDevToolsBounds(bounds));

    try {
      target.setDevToolsWebContents(view.webContents);
      target.openDevTools();
    } catch (error) {
      console.error("[devtools] 停靠失败", error);
      detachDevTools();
      return { ok: false, error: error instanceof Error ? error.message : "DevTools 打不开" };
    }
    // 前端加载失败**不抛异常**：留在初始页也算"成功"。不等一个结果的话，空白面板
    // 和正常面板长得一模一样，排查时连个错都拿不到。
    return waitForDevTools(view.webContents);
  });

  // 占位块的位置变了（窗口缩放、分隔条拖宽、面板区滚动）就推一次。
  ipcMain.handle("browser:devtools-bounds", async (_event, bounds) => {
    if (!devToolsView) return { ok: false };
    devToolsView.setBounds(toDevToolsBounds(bounds));
    return { ok: true };
  });

  // 拖分隔条期间把 DevTools 视图收起来。
  //
  // 为什么是"藏起来"而不是"让它穿透鼠标"：setIgnoreMouseEvents 只存在于
  // BrowserWindow / BaseWindow 上（见 electron.d.ts 里那两处声明），View 基类
  // 压根没这个方法，WebContentsView 继承的也是 View。所以原生视图一旦显示着，
  // 鼠标进它的地盘就被它吃掉，渲染进程里的 pointermove / pointerup 一起断掉。
  // 可用的手段只剩 setVisible(false)。
  //
  // 松手时把新 bounds 和恢复显示放在同一次调用里：先摆位置再显示，否则会出现
  // 一帧"按旧宽度闪一下再跳过去"。
  ipcMain.handle("browser:devtools-drag", async (_event, payload) => {
    if (!devToolsView) return { ok: false };
    const dragging = payload?.dragging === true;
    try {
      if (dragging) {
        devToolsView.setVisible(false);
        return { ok: true };
      }
      if (payload?.bounds) devToolsView.setBounds(toDevToolsBounds(payload.bounds));
      devToolsView.setVisible(true);
      return { ok: true };
    } catch (error) {
      console.error("[devtools] 拖拽状态切换失败", error);
      return { ok: false };
    }
  });

  ipcMain.handle("browser:devtools-detach", async () => {
    detachDevTools();
    return { ok: true };
  });

  // DevTools 前端加载成什么样了，等一下再说。
  //
  // 判据只有一条：加载完之后地址不能还停在初始页。devtools:// 能解析就会把那个
  // webContents 导航走；解析不了（协议没注册、会话不对）则原地不动 —— 而这两种
  // 情况**都不抛异常**，只能靠看地址分辨。
  function waitForDevTools(contents, timeoutMs = 5000) {
    return new Promise((resolve) => {
      const finish = (payload) => {
        clearTimeout(timer);
        contents.removeListener("did-fail-load", onFail);
        resolve(payload);
      };
      const onFail = (_event, errorCode, errorDescription, url) => {
        console.error("[devtools] 前端加载失败", errorCode, errorDescription, url);
        finish({ ok: false, error: `DevTools 前端加载失败：${errorDescription || errorCode}` });
      };
      const timer = setTimeout(() => {
        const url = contents.getURL();
        if (url && !url.startsWith("about:")) {
          finish({ ok: true, url });
          return;
        }
        // 还停在初始页 = 压根没被导航过去。多半是 devtools:// 在这个会话里没注册
        // —— 给 webview 设 partition 会多出一个独立的 BrowserContext，协议不在那儿。
        console.error("[devtools] 前端未加载，当前地址", JSON.stringify(url));
        finish({ ok: false, error: "DevTools 前端没有加载起来" });
      }, timeoutMs);
      contents.on("did-fail-load", onFail);
    });
  }

  // 收掉停靠视图。closeDevTools() **不会**销毁我们给的宿主 WebContents，所以
  // 顺序是：先断开 DevTools，再从窗口摘掉视图，最后销毁它自己的 webContents。
  // 三步都做了才算真回收，不然每开一次就漏一个渲染进程。
  function detachDevTools() {
    const view = devToolsView;
    const win = devToolsWindow;
    const id = devToolsTargetId;
    devToolsView = null;
    devToolsWindow = null;
    devToolsTargetId = null;
    if (!view) return;

    if (id != null) {
      const target = webContents.fromId(id);
      if (target && !target.isDestroyed()) {
        try {
          target.closeDevTools();
        } catch (error) {
          console.error("[devtools] 关闭失败", error);
        }
      }
    }
    if (win && !win.isDestroyed()) {
      try {
        win.contentView.removeChildView(view);
      } catch (error) {
        console.error("[devtools] 摘除视图失败", error);
      }
    }
    try {
      view.webContents.close();
    } catch {
      // 已经没了就算了。
    }
  }

  // 渲染进程报上来的是 DOMRect，转成 setBounds 要的整数，且不能出现负尺寸 ——
  // 占位块在布局过渡中完全可能量到 0 甚至负数，原样传下去会抛。
  function toDevToolsBounds(rect) {
    const num = (value) => (Number.isFinite(value) ? Math.round(value) : 0);
    return {
      x: num(rect?.x),
      y: num(rect?.y),
      width: Math.max(0, num(rect?.width)),
      height: Math.max(0, num(rect?.height)),
    };
  }
  // 渲染进程的 navigator.clipboard 在 Electron 里要过权限请求，而本进程没设权限处理器，
  // 默认会被拒。终端右键菜单的粘贴/复制走主进程，一次 IPC 解决。
  ipcMain.handle("read-clipboard-text", async () => clipboard.readText());
  ipcMain.handle("write-clipboard-text", async (_event, text) => {
    if (typeof text !== "string") return { ok: false };
    clipboard.writeText(text);
    return { ok: true };
  });

  // 应用里选的主题（浅色 / 深色 / 跟随系统）推过来。网页把这套存在自己的
  // localStorage 里，主进程读不到 —— 而窗口按钮、窗口底色这些是原生控件，不跟网页
  // 的 CSS 走。以前两边各看各的：用户手动选了深色、系统还是浅色时，右上角那三个按钮
  // 继续用深色图标，压在深色头带上几乎看不见。
  //
  // themeSource 照设，好让原生控件（滚动条、日期选择器这些）也跟着切；但按钮的颜色
  // 读的是 appThemeMode，不绕 shouldUseDarkColors —— Windows 上 themeSource 什么
  // 时候传过去并不好说，中间少这一环才稳。
  ipcMain.on("app:theme-mode", (_event, mode) => {
    appThemeMode = mode === "light" || mode === "dark" ? mode : null;
    nativeTheme.themeSource = mode === "light" || mode === "dark" ? mode : "system";
    // 不指望给 themeSource 赋值一定触发 updated（各平台不一致），直接重刷一次。
    syncWindowChrome();
  });
  // ---- 内置浏览器 Agent 控制 ----
  // 这里**没有 IPC**：开关是工具插件的开关，写在 <数据目录>/settings.json 里，
  // 由 sidecar 的插件接口负责读写，与其它工具插件走同一条路。面板判断「要不要
  // 重启」靠的是 sidecar 侧比对配置与本进程注入的环境变量，主进程不必再开一条
  // 只服务于这一个开关的通道。
  //
  // 重启本身走下面的 relaunch。

  // 重启整个应用。换过数据目录之后只有重启才生效：数据目录在进程启动时解析一次
  // （见 nono_agent/paths），会话事件流、app.db 和托管运行时都被当前进程占着。
  // 顺序是「先停 sidecar 再退」：app.quit() 不会替我们回收子进程，sidecar 拿着旧
  // 数据目录的句柄活到新进程起来，Windows 上就变成两个进程抢同一个 app.db。
  ipcMain.handle("relaunch", async () => {
    quitting = true;
    stopSidecar();
    // 开发态的自启 Vite 一并回收：strictPort 意味着它还占着 5173 的话，新进程
    // 再 spawn 一个必然起不来。而且所有权要跟着进程走 —— 不在这里杀，它就成了
    // 没人认领的孤儿进程。
    if (!isPackaged()) stopWebServer();
    // taskkill 是异步的：旧 sidecar 还没退干净时新进程就抢端口。开发态直接等到
    // 端口真的空出来，比盲等 400ms 稳。
    if (isPackaged()) {
      await new Promise((resolve) => setTimeout(resolve, 400));
    } else {
      await waitPortFree(devSidecarPort(), 5000);
      await waitPortFree(devWebPort(), 5000);
    }
    app.relaunch();
    app.quit();
    return true;
  });

  // ---- 更新 ----
  // 三个动作对应设置页那一行的三种状态：查 → 下 → 装。每一步都只回执成败，
  // 真正的进度和结果都靠 app:update-event 事件流推回去，避免这里再包一层轮询。
  ipcMain.handle("app:update-check", async () => {
    const updater = loadUpdater();
    // 开发态没有 app-update.yml，直接告诉调用方「这里不查」，让 UI 走降级文案
    // 而不是弹一个"检查失败"。
    if (!updater) return { ok: false, reason: "dev" };
    try {
      // autoDownload 是 false，这里只会查不会下。
      await updater.checkForUpdates();
      return { ok: true };
    } catch (error) {
      // 失败原因已经由 error 事件推给渲染进程了，这里只回执给直接调用方。
      return { ok: false, error: error?.message || String(error) };
    }
  });

  ipcMain.handle("app:update-download", async () => {
    const updater = loadUpdater();
    if (!updater) return { ok: false, error: "开发模式不支持更新下载" };
    try {
      await updater.downloadUpdate();
      return { ok: true };
    } catch (error) {
      return { ok: false, error: error?.message || String(error) };
    }
  });

  // 装完更新要重启应用。收尾顺序和 relaunch 完全一样：先停 sidecar 再退 ——
  // app.quit() 不回收子进程，sidecar 拿着旧 app.db 的句柄活到新进程起来，
  // Windows 上就变成两个进程抢同一个库。第二个参数 isForceRunAfter=true 是
  // 让安装器装完直接把新版本拉起来，否则用户会以为更新没生效。
  ipcMain.handle("app:update-install", async () => {
    const updater = loadUpdater();
    if (!updater) return { ok: false, error: "开发模式不支持更新安装" };
    quitting = true;
    stopSidecar();
    await new Promise((resolve) => setTimeout(resolve, 400));
    updater.quitAndInstall(false, true);
    return { ok: true };
  });

  // CDP 端口**必须**在 ensureSidecar 之前定下来：sidecarSpec() 是靠
  // ``browserAgentPort > 0`` 决定要不要告诉 sidecar「浏览器控制已开启」的，
  // 顺序反了就会拿一个还没解析出来的端口（0）去发环境变量。
  if (browserAgentEnabled) {
    browserAgentPort = await resolveCdpPort();
    if (browserAgentPort > 0) {
      console.log(`[cdp] Chromium 自选端口：127.0.0.1:${browserAgentPort}`);
    } else {
      console.warn("[cdp] 没能读回 Chromium 自选的调试端口，Agent 浏览器控制这次保持关闭。");
      console.warn(`[cdp] 预期文件：${path.join(app.getPath("userData"), "DevToolsActivePort")}`);
    }
  }

  const port = isPackaged() ? await pickFreePort() : devSidecarPort();
  const healthUrl = isPackaged()
    ? `http://127.0.0.1:${port}/health`
    : DEV_SIDECAR_HEALTH;
  const webUrl = isPackaged() ? `http://127.0.0.1:${port}/` : DEV_WEB_URL;

  // 先把 sidecar 拉起来再开窗：这里刻意不 await。ensureSidecar() 的第一步就是
  // spawn（打包态），Python 进程启动与建窗口、渲染 loading.html 并行进行。
  // 以前是建完窗口才 spawn，白白串行了一整个 Python 启动时间。
  //
  // 开发态多拉一个 Vite，同样不 await —— 它和 sidecar 各冷启动各的，并行比串行快。
  // 两边都不再探测端口上有没有现成的服务：占了就是冲突，报清楚、不接管（见上面
  // 那段「进程所有权」）。
  const sidecarReady = ensureSidecar(healthUrl, port);
  const webReady = isPackaged() ? Promise.resolve("packaged") : ensureWebServer(webUrl);
  // 不 await：端口通不通和 sidecar/vite 能不能起来无关，不该拖慢开窗。
  // 失败也只是打日志 —— 但这条日志是**真的**（见 verifyCdpPort 自己的注释）。
  if (browserAgentPort > 0) verifyCdpPort();
  mainWindow = createWindow();
  tray = await createTray(mainWindow);
  if (isPackaged()) {
    await mainWindow.loadFile(path.join(__dirname, "loading.html"), {
      query: { theme: appTheme() },
    });
  }
  const [, webStatus] = await Promise.all([sidecarReady, webReady]);
  // 网页没起来就**别**往里加载：明知 5173 上是别人的服务还要 loadURL，只会在窗口里
  // 留一张 ERR_CONNECTION_REFUSED 白页，把真正的原因（终端里那几行）挡在后面。
  // 冲突时 reportPortConflict 已经弹过窗了，这里让窗口空着就行 —— 用户要么按
  // 提示处理完从托盘退出重开，要么 NONO_WEB=0 自己管网页。
  if (webStatus !== "conflict" && webStatus !== "failed") {
    await mainWindow.loadURL(webUrl);
  }

  // 启动后静默问一次有没有新版。不下载、不弹窗，只把结果留给设置页那一行 ——
  // 用户打开设置时按钮已经是"有新版本"，而不是还得先点一下才知道。
  // 失败无所谓：error 事件会把「这次没查成」带到 UI 上，不该让启动路径受牵连。
  if (isPackaged()) {
    loadUpdater()?.checkForUpdates().catch(() => {});
  }

  app.on("activate", () => {
    if (!mainWindow) mainWindow = createWindow();
    showWindow(mainWindow);
  });
});

// 托盘「退出」走这里。收尾是**异步**的：killTree 只 spawn 一个 taskkill 就返回，
// 而 Vite 那棵树的根是 cmd.exe —— 主进程一退出它就自己死了，taskkill 扑空，整条
// pnpm/vite 子树留下继续占着 5173。所以先把这次退出拦下来，等端口真的空出来再放行
// （实测多活约 700ms 就能收干净，理由见 cleanupOwnedProcesses）。
app.on("before-quit", (event) => {
  quitting = true;
  if (tray) {
    tray.destroy();
    tray = null;
  }
  // done：这是收尾完成后自己发起的第二次 quit，放行。
  if (cleanupState === "done") return;
  event.preventDefault();
  // running：已经有一轮收尾在跑，它结束时会自己 app.quit()，这里别再排一轮。
  if (cleanupState === "running") return;
  cleanupState = "running";
  console.log("回收自己拉起的进程，等端口空出来再退出。");
  cleanupOwnedProcesses().finally(() => {
    cleanupState = "done";
    console.log("回收完成，退出。");
    app.quit();
  });
});

// 终端里 Ctrl+C、或直接关掉跑 dev:desktop 的那个终端窗口 —— 这两条路**不会**走到
// app.quit()，before-quit 于是也不会触发，sidecar 和 Vite 就成了没人认领的孤儿：
// 端口还占着、/health 还回 ok，而下一个 dev:desktop 只会撞上一句莫名其妙的端口冲突。
//
// 孤儿进程正是上面那套「自己起、自己收」要消灭的东西，所以把信号接到同一条收尾
// 路径上（依然是先杀进程树、再退出）。
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    if (quitting) return;
    console.log(`收到 ${signal}，回收自己拉起的进程后退出。`);
    quitApp();
  });
}

app.on("window-all-closed", () => {
  // 点 × 只是隐藏窗口，进程留在托盘，不退出。
});
