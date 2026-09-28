/// <reference types="vite/client" />

declare module "mammoth/mammoth.browser.js" {
  export function convertToHtml(input: { arrayBuffer: ArrayBuffer }): Promise<{ value: string }>;
  const mammoth: { convertToHtml: typeof convertToHtml };
  export default mammoth;
}

type NonoDesktop = {
  platform: string;
  isDesktop?: boolean;
  version?: string;
  selectWorkspace?: () => Promise<string | null>;
  openPath?: (target: string) => Promise<{ ok: boolean; error?: string }>;
};

interface Window {
  nonoDesktop?: NonoDesktop;
}

/** 开发态由 Vite 注入，终端 WebSocket 直连 sidecar，不走会被浏览器挂起的代理。 */
declare const __NONO_SIDECAR_HTTP__: string;

/** 桌面应用版本。浏览器里来自 desktop/package.json；打包后的壳会用安装包版本覆盖。 */
declare const __NONO_APP_VERSION__: string;
