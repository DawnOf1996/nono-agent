/// <reference types="vite/client" />

type NonoDesktop = {
  platform: string;
  isDesktop?: boolean;
  version?: string;
  selectWorkspace?: () => Promise<string | null>;
  openPath?: (target: string) => Promise<{ ok: boolean; error?: string }>;
  /** 交给系统默认浏览器打开。iframe 装不下禁止嵌入的站点时用。 */
  openExternal?: (target: string) => Promise<{ ok: boolean; error?: string }>;
  /** 访客页请求新窗口（target=_blank）时回调地址，返回取消订阅函数。 */
  onNewTab?: (handler: (url: string) => void) => () => void;
  readClipboardText?: () => Promise<string>;
  writeClipboardText?: (text: string) => Promise<{ ok: boolean }>;
};

interface Window {
  nonoDesktop?: NonoDesktop;
}

/** 开发态由 Vite 注入，终端 WebSocket 直连 sidecar，不走会被浏览器挂起的代理。 */
declare const __NONO_SIDECAR_HTTP__: string;

/** 桌面应用版本。浏览器里来自 desktop/package.json；打包后的壳会用安装包版本覆盖。 */
declare const __NONO_APP_VERSION__: string;
