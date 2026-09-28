const { app, BrowserWindow, Menu, Tray, dialog, ipcMain, nativeImage, shell } = require("electron");
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
  const attempts = isPackaged() ? 120 : 40;
  const delayMs = isPackaged() ? 500 : 250;
  for (let i = 0; i < attempts; i += 1) {
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

function createWindow() {
  const icon = resolveAppIcon();
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#14181F",
    autoHideMenuBar: true,
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
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

  const port = isPackaged() ? await pickFreePort() : Number(process.env.NONO_PORT || 8000);
  const healthUrl = isPackaged()
    ? `http://127.0.0.1:${port}/health`
    : DEV_SIDECAR_HEALTH;
  const webUrl = isPackaged() ? `http://127.0.0.1:${port}/` : DEV_WEB_URL;

  mainWindow = createWindow();
  tray = await createTray(mainWindow);
  if (isPackaged()) {
    await mainWindow.loadFile(path.join(__dirname, "loading.html"));
  }
  await ensureSidecar(healthUrl, port);
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
