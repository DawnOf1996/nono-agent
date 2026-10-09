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
  openExternal: (target) => ipcRenderer.invoke("open-external", target),
  // 订阅「访客页想开新窗口」的地址，返回取消订阅函数。
  onNewTab: (handler) => {
    const listener = (_event, url) => handler(url);
    ipcRenderer.on("browser:new-tab", listener);
    return () => ipcRenderer.removeListener("browser:new-tab", listener);
  },
  readClipboardText: () => ipcRenderer.invoke("read-clipboard-text"),
  writeClipboardText: (text) => ipcRenderer.invoke("write-clipboard-text", text),
  // 改过数据目录后重启应用。渲染进程不能自己重启，只有点一次主进程。
  relaunch: () => ipcRenderer.invoke("relaunch"),
});
