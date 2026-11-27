/**
 * 会话正文字号。写在本机 localStorage。
 *
 * 落点是 <html> 上的 --content-font-size，CSS 里只有 .prose-md（对话正文）和输入框读它，
 * 侧边栏、设置面板这些界面文字不跟着变 —— 跟正文走的面板会把界面撑散。
 * index.html 里有一段等价的首屏脚本先写一次这个变量，免得 React 挂载前后字号跳一下；
 * 那段脚本不认识这个文件里的常量，改上下限时两处一起改。
 */

export const CONTENT_FONT_SIZE_MIN = 10;
export const CONTENT_FONT_SIZE_MAX = 22;
export const CONTENT_FONT_SIZE_DEFAULT = 14;

const KEY = "nono.content.fontSize";

function clamp(value: number): number {
  if (!Number.isFinite(value)) return CONTENT_FONT_SIZE_DEFAULT;
  return Math.min(CONTENT_FONT_SIZE_MAX, Math.max(CONTENT_FONT_SIZE_MIN, Math.round(value)));
}

function read(): number {
  try {
    const raw = localStorage.getItem(KEY);
    // Number(null) 是 0，先挡掉空值再进 clamp。
    if (raw === null) return CONTENT_FONT_SIZE_DEFAULT;
    return clamp(Number(raw));
  } catch {
    return CONTENT_FONT_SIZE_DEFAULT;
  }
}

let current = read();
const listeners = new Set<() => void>();

function apply(): void {
  document.documentElement.style.setProperty("--content-font-size", `${current}px`);
}

// 模块加载时补写一次：首屏脚本被 CSP 或扩展挡掉时，这里兜底。
apply();

export function getContentFontSize(): number {
  return current;
}

export function subscribeContentFontSize(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setContentFontSize(next: number): void {
  const size = clamp(next);
  if (size === current) return;
  current = size;
  try {
    localStorage.setItem(KEY, String(size));
  } catch {
    // private mode / blocked storage
  }
  apply();
  for (const listener of listeners) listener();
}
