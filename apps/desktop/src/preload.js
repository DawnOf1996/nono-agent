const { contextBridge, ipcRenderer } = require("electron");

function appVersion() {
  const prefix = "--nono-app-version=";
  const found = process.argv.find((item) => item.startsWith(prefix));
  return found ? found.slice(prefix.length) : "";
}

contextBridge.exposeInMainWorld("nonoDesktop", {
  platform: process.platform,
  isDesktop: true,
  version: appVersion(),
  selectWorkspace: () => ipcRenderer.invoke("select-workspace"),
  openPath: (target) => ipcRenderer.invoke("open-path", target),
});
