const { app, BrowserWindow, Menu, Tray, WebContentsView, clipboard, dialog, ipcMain, nativeImage, nativeTheme, shell, webContents } = require("electron");
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
