const { app, BrowserWindow, Menu, Tray, clipboard, dialog, ipcMain, nativeImage, nativeTheme, shell } = require("electron");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const net = require("node:net");
const path = require("node:path");

const DEV_WEB_URL = process.env.NONO_WEB_URL || "http://127.0.0.1:5173";
const DEV_SIDECAR_HEALTH = process.env.NONO_SIDECAR_URL || "http://127.0.0.1:8000/health";

let sidecar = null;
let mainWindow = null;
let tray = null;
let quitting = false;

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
      },
    };
  }
  return {
    command: "uv",
    args: ["run", "nono-app-server"],
    cwd: path.resolve(__dirname, "..", "..", "server"),
    env: {},
  };
}

function startSidecar(port) {
  const spec = sidecarSpec(port);
  const child = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: { ...process.env, PYTHONUNBUFFERED: "1", ...spec.env },
    windowsHide: true,
    stdio: isPackaged() ? "ignore" : "inherit",
  });
  sidecar = child;
  child.on("exit", (code) => {
    if (sidecar === child) sidecar = null;
    console.log(`sidecar exited with code ${code}`);
  });
}

function stopSidecar() {
  if (!sidecar) return;
  const child = sidecar;
  const pid = child.pid;
  sidecar = null;
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

async function ensureSidecar(healthUrl, port) {
  if (process.env.NONO_SIDECAR === "0") return;
  if (await sidecarUp(healthUrl)) return;
  startSidecar(port);
  // 以前打包态 500ms 一轮，平均要多等 250ms 才发现 sidecar 好了。80ms 一轮在本机
  // 几乎不花钱：Node 的 fetch 连空回环端口走 ConnectEx + SIO_LOOPBACK_FAST_PATH，
  // 实测 1~40ms 就返回。总预算（60s / 10s）保持不变，只是探测更密。
  const timeoutMs = isPackaged() ? 60_000 : 10_000;
  const delayMs = 80;
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await new Promise((resolve) => setTimeout(resolve, delayMs));
    if (await sidecarUp(healthUrl)) return;
  }
  console.warn("sidecar did not become healthy in time; UI will show offline");
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

// 加载页与应用本体必须同主题，否则 loadURL 那一刻会闪一次明暗翻转，白色主题用户
// 尤其明显（以前窗口底色写死 #14181F，深色用户也跟着受了一次多余的变色）。
// 两个值取自 apps/web/src/index.css 的 --color-bg。
//
// 只能给系统值：loading.html 走 file://，应用本体走 http://127.0.0.1:<port>，
// localStorage 不同源，主进程读不到用户显式选过的主题。对 theme=system（默认）的
// 用户是准的；手动改过主题的用户在加载页会有一次轻微不一致，但不会再是黑屏。
function appTheme() {
  return nativeTheme.shouldUseDarkColors ? "dark" : "light";
}

function appBackground() {
  return nativeTheme.shouldUseDarkColors ? "#16181d" : "#ffffff";
}

function createWindow() {
  const icon = resolveAppIcon();
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: appBackground(),
    autoHideMenuBar: true,
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
  // 渲染进程的 navigator.clipboard 在 Electron 里要过权限请求，而本进程没设权限处理器，
  // 默认会被拒。终端右键菜单的粘贴/复制走主进程，一次 IPC 解决。
  ipcMain.handle("read-clipboard-text", async () => clipboard.readText());
  ipcMain.handle("write-clipboard-text", async (_event, text) => {
    if (typeof text !== "string") return { ok: false };
    clipboard.writeText(text);
    return { ok: true };
  });
  // 重启整个应用。换过数据目录之后只有重启才生效：数据目录在进程启动时解析一次
  // （见 nono_agent/paths），会话事件流、app.db 和托管运行时都被当前进程占着。
  // 顺序是「先停 sidecar 再退」：app.quit() 不会替我们回收子进程，sidecar 拿着旧
  // 数据目录的句柄活到新进程起来，Windows 上就变成两个进程抢同一个 app.db。
  ipcMain.handle("relaunch", async () => {
    quitting = true;
    stopSidecar();
    // taskkill 是异步的：旧 sidecar 还没退干净时新进程就抢端口，开发态（固定 8000）
    // 会直接撞上「端口已被占用」而启动失败。给它一点收尾时间。
    await new Promise((resolve) => setTimeout(resolve, 400));
    app.relaunch();
    app.quit();
    return true;
  });

  const port = isPackaged() ? await pickFreePort() : Number(process.env.NONO_PORT || 8000);
  const healthUrl = isPackaged()
    ? `http://127.0.0.1:${port}/health`
    : DEV_SIDECAR_HEALTH;
  const webUrl = isPackaged() ? `http://127.0.0.1:${port}/` : DEV_WEB_URL;

  // 先把 sidecar 拉起来再开窗：这里刻意不 await。ensureSidecar() 的第一步就是
  // spawn（打包态），Python 进程启动与建窗口、渲染 loading.html 并行进行。
  // 以前是建完窗口才 spawn，白白串行了一整个 Python 启动时间。
  const sidecarReady = ensureSidecar(healthUrl, port);
  mainWindow = createWindow();
  tray = await createTray(mainWindow);
  if (isPackaged()) {
    await mainWindow.loadFile(path.join(__dirname, "loading.html"), {
      query: { theme: appTheme() },
    });
  }
  await sidecarReady;
  await mainWindow.loadURL(webUrl);

  app.on("activate", () => {
    if (!mainWindow) mainWindow = createWindow();
    showWindow(mainWindow);
  });
});

app.on("before-quit", () => {
  quitting = true;
  stopSidecar();
  if (tray) {
    tray.destroy();
    tray = null;
  }
});

app.on("window-all-closed", () => {
  // 点 × 只是隐藏窗口，进程留在托盘，不退出。
});
