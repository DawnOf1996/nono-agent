/**
 * 桌面端（Electron）专属的判断。纯 web 下 `window.nonoDesktop` 整个不存在，
 * 这几个函数一律返回 false，网页版不会因为桌面端的窗口改造而错位。
 */

/** 跑在 NoNo 桌面壳里，而不是浏览器里。 */
export function isDesktop() {
  return typeof window !== "undefined" && window.nonoDesktop?.isDesktop === true;
}

/**
 * 页面最上面那一条是不是「窗口头部」本身。
 *
 * Windows / macOS 上主窗口用了 titleBarStyle:'hidden'，原生标题栏让给了页面自绘：
 * 应用外壳顶上多渲染一条空的拖拽带（App.tsx 的 .titlebar-drag），高度与
 * desktop/src/main.js 的 TITLEBAR_HEIGHT 对齐。
 *
 * Linux 没走 titleBarStyle:'hidden'，原生标题栏在网页上方，页面顶行只是普通内容；
 * 纯 web 更没有窗口这一说。两种情况都不能凭空多渲染一条空边。
 */
export function hasSelfDrawnTitlebar() {
  return isDesktop() && window.nonoDesktop?.platform !== "linux";
}