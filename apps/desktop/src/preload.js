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
  // 截内部浏览器当前可见区域，回 base64 PNG。像素只在主进程手里拿得到，
  // 纯 web 下没有这个桥。
  captureWebview: (webContentsId) => ipcRenderer.invoke("browser:capture-viewport", webContentsId),
  // DevTools 停靠在面板里。主进程建一个 WebContentsView 承载前端，渲染进程只负责
  // 报告占位块的位置（attach 时带一次，之后随布局变化持续推）。
  attachDevtools: (targetId, bounds) => ipcRenderer.invoke("browser:devtools-attach", targetId, bounds),
  moveDevtools: (bounds) => ipcRenderer.invoke("browser:devtools-bounds", bounds),
  /** 拖分隔条时传 true，让 DevTools 视图把鼠标透给底下的页面，拖拽才不会被截断。 */
  passThroughDevtools: (ignore) => ipcRenderer.invoke("browser:devtools-mouse", ignore),
  detachDevtools: () => ipcRenderer.invoke("browser:devtools-detach"),
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
  // 更新：查 / 下 / 装分三个动作，和 electron-updater 的「检查」与「下载」是两步
  // 对应的 —— autoDownload 关着，下载只能由用户点了按钮才发生。
  checkForUpdate: () => ipcRenderer.invoke("app:update-check"),
  downloadUpdate: () => ipcRenderer.invoke("app:update-download"),
  installUpdate: () => ipcRenderer.invoke("app:update-install"),
  // 进度、结果、错误都走这一条事件流返回 unsubscribe 函数，
  // 调用方在组件卸载时退订，避免热重载后重复挂监听。
  onUpdateEvent: (handler) => {
    const listener = (_event, payload) => handler(payload);
    ipcRenderer.on("app:update-event", listener);
    return () => ipcRenderer.removeListener("app:update-event", listener);
  },
  // 把用户选中的主题模式交给主进程。只通知、不等回执：窗口按钮的符号色是原生控件，
  // 不跟网页的 CSS 走，主进程要自己照着刷新窗口。
  setThemeMode: (mode) => ipcRenderer.send("app:theme-mode", mode),
});
