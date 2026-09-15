const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("nonoDesktop", {
  platform: process.platform,
  isDesktop: true,
  selectWorkspace: () => ipcRenderer.invoke("select-workspace"),
  openPath: (target) => ipcRenderer.invoke("open-path", target),
});
