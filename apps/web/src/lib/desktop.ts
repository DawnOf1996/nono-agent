/**
 * 桌面端（Electron）专属的判断。纯 web 下 `window.nonoDesktop` 整个不存在，
 * 这几个函数一律返回 false，网页版不会因为桌面端的窗口改造而错位。
 */

/** 跑在 NoNo 桌面壳里，而不是浏览器里。 */
export function isDesktop() {
  return typeof window !== "undefined" && window.nonoDesktop?.isDesktop === true;
}

/**
 * 右上角有没有系统窗口按钮（最小化 / 最大化 / 关闭）压在页面上。
 *
 * Electron 用 titleBarStyle:'hidden' 把原生标题栏让给了页面，Windows 上那三个
 * 按钮以 overlay 形式浮在网页右上角，各 46px 合计 138px。布局在窗口最右侧的那一
 * 栏必须给它让出这段，否则自己的按钮会被压在系统按钮底下，点不到也看不到。
 *
 * macOS 的红绿灯在左上角（跟着 Sidebar 那侧），Linux 压根没有 overlay，都不用让位。
 */
export function hasWindowControls() {
  return isDesktop() && window.nonoDesktop?.platform === "win32";
}