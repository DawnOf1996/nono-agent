/**
 * 读写剪贴板文本。
 *
 * 桌面端优先走主进程：渲染进程里的 `navigator.clipboard.readText()` 要过权限请求，
 * 而 desktop/src/main.js 没有设权限处理器，Electron 默认拒绝未处理的请求。所以打包版
 * 必须经 preload 桥（read-clipboard-text）落到主进程的 `clipboard`。
 * 浏览器里没有桥，退回标准 API——菜单点击本身就是 user gesture，正常会被放行。
 */
export async function readClipboardText(): Promise<string> {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.readClipboardText : undefined;
  if (bridge) return bridge();
  return navigator.clipboard.readText();
}

export async function writeClipboardText(text: string): Promise<void> {
  const bridge = typeof window !== "undefined" ? window.nonoDesktop?.writeClipboardText : undefined;
  if (bridge) {
    await bridge(text);
    return;
  }
  await navigator.clipboard.writeText(text);
}
