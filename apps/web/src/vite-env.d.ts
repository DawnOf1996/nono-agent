/// <reference types="vite/client" />

type NonoDesktop = {
  platform: string;
  isDesktop?: boolean;
  version?: string;
  selectWorkspace?: () => Promise<string | null>;
  openPath?: (target: string) => Promise<{ ok: boolean; error?: string }>;
  /** 交给系统默认浏览器打开。iframe 装不下禁止嵌入的站点时用。 */
  openExternal?: (target: string) => Promise<{ ok: boolean; error?: string }>;
  /**
   * 截内部浏览器当前可见区域，回 base64 PNG。id 来自 <webview>.getWebContentsId()。
   * 只有走 <webview> 的桌面版有这个桥，纯 web 下是 undefined。
   */
  captureWebview?: (webContentsId: number) => Promise<{ ok: boolean; data?: string; error?: string }>;
  /**
   * 把 DevTools 前端停到主进程的一个原生视图上，位置对齐 bounds（DOM 坐标）。
   * 宿主不能用 webview —— 那样前端拿不到调试后端连接，只剩下一个空面板。
   */
  attachDevtools?: (
    targetId: number,
    bounds: { x: number; y: number; width: number; height: number },
  ) => Promise<{ ok: boolean; url?: string; error?: string }>;
  /** 占位块位置变了就推一次。只在停靠期间有效。 */
  moveDevtools?: (bounds: { x: number; y: number; width: number; height: number }) => Promise<{ ok: boolean }>;
  /**
   * 拖分隔条期间让 DevTools 视图不再抢鼠标，否则鼠标一进面板区拖拽就断了。
   * 拖完记得传 false 恢复。
   */
  passThroughDevtools?: (ignore: boolean) => Promise<{ ok: boolean }>;
  detachDevtools?: () => Promise<{ ok: boolean }>;
  /** 访客页请求新窗口（target=_blank）时回调地址，返回取消订阅函数。 */
  onNewTab?: (handler: (url: string) => void) => () => void;
  readClipboardText?: () => Promise<string>;
  writeClipboardText?: (text: string) => Promise<{ ok: boolean }>;
  /** 改过数据目录后重启应用。浏览器里没有这个桥，只能提示用户自己重开。 */
  relaunch?: () => Promise<boolean>;
  /** 检查有没有新版。只查不下载。开发态或纯 web 打开时是 undefined。 */
  checkForUpdate?: () => Promise<{ ok: boolean; reason?: string; error?: string }>;
  /** 下载已发现的新版安装包。下载进度走 onUpdateEvent 的 progress 事件。 */
  downloadUpdate?: () => Promise<{ ok: boolean; error?: string }>;
  /** 装下已下载的更新并重启应用。 */
  installUpdate?: () => Promise<{ ok: boolean; error?: string }>;
  /** 订阅更新事件流，返回退订函数。渲染进程不直接持有 updater，只能这么听。 */
  onUpdateEvent?: (handler: (event: NonoUpdateEvent) => void) => () => void;
  /**
   * 把用户选中的主题模式（light / dark / system）告诉桌面壳。
   * 窗口按钮、加载页底色这些是原生控件，不跟网页的 localStorage 走，只认主进程的
   * nativeTheme；不告诉它的话，用户手动选了深色、系统还是浅色时，右上角那三个按钮
   * 会继续用深色图标，压在深色页头上几乎看不见。纯 web 下是 undefined。
   */
  setThemeMode?: (mode: "light" | "dark" | "system") => void;
};

/**
 * 主进程推回来的更新事件。和 electron-updater 的事件一一对应，
 * 字段名由 desktop/src/main.js 的 sendUpdateEvent 决定，两边要一起改。
 */
type NonoUpdateEvent =
  | { type: "checking" }
  | { type: "available"; version: string }
  | { type: "latest"; version: string | null }
  | { type: "progress"; percent: number; transferred: number; total: number }
  | { type: "downloaded"; version: string }
  | { type: "error"; message: string };

interface Window {
  nonoDesktop?: NonoDesktop;
}

/** 开发态由 Vite 注入，终端 WebSocket 直连 sidecar，不走会被浏览器挂起的代理。 */
declare const __NONO_SIDECAR_HTTP__: string;

/** 桌面应用版本。浏览器里来自 desktop/package.json；打包后的壳会用安装包版本覆盖。 */
declare const __NONO_APP_VERSION__: string;
