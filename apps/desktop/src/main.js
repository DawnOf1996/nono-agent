const { app, BrowserWindow, dialog, ipcMain, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");

const WEB_URL = process.env.NONO_WEB_URL || "http://127.0.0.1:5173";
const SIDECAR_HEALTH = process.env.NONO_SIDECAR_URL || "http://127.0.0.1:8000/health";

let sidecar = null;

async function sidecarUp() {
  try {
    const response = await fetch(SIDECAR_HEALTH);
    return response.ok;
  } catch {
    return false;
  }
}

function startSidecar() {
  const serverDir = path.resolve(__dirname, "..", "..", "server");
  const child = spawn("uv", ["run", "nono-app-server"], {
    cwd: serverDir,
    env: { ...process.env, PYTHONUNBUFFERED: "1" },
    windowsHide: true,
    stdio: "inherit",
  });
  sidecar = child;
  child.on("exit", (code) => {
    if (sidecar === child) sidecar = null;
    console.log(`sidecar exited with code ${code}`);
  });
}

async function ensureSidecar() {
  if (process.env.NONO_SIDECAR === "0") return;
  if (await sidecarUp()) return;
  startSidecar();
  for (let i = 0; i < 40; i += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
    if (await sidecarUp()) return;
  }
  console.warn("sidecar did not become healthy in time; UI will show offline");
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: "#14181F",
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  win.loadURL(WEB_URL);
}

app.whenReady().then(async () => {
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
  await ensureSidecar();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", () => {
  if (sidecar && !sidecar.killed) sidecar.kill();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
