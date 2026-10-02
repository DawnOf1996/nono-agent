/**
 * 外观（浅色 / 深色 / 跟随系统）。写在本机 localStorage。
 *
 * 落点是 <html data-theme="...">：index.css 里浅色那套写在 :root，深色那套覆写同名
 * --color-* 变量。所有用 bg-bg / text-ink / border-line 这类工具类的组件因此自动跟着变，
 * 不需要在每个组件里判断主题。
 * index.html 里有段等价的首屏脚本先定好 data-theme，深色下才不会先闪一屏白再翻过去。
 */

import { useSyncExternalStore } from "react";

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_MODES: Array<{ id: ThemeMode; label: string }> = [
  { id: "light", label: "浅色" },
  { id: "dark", label: "深色" },
  { id: "system", label: "跟随系统" },
];

const KEY = "nono.theme";

function parse(raw: string | null): ThemeMode {
  return raw === "light" || raw === "dark" || raw === "system" ? raw : "system";
}

const query = typeof window === "undefined" ? null : window.matchMedia("(prefers-color-scheme: dark)");

function systemTheme(): ResolvedTheme {
  return query?.matches ? "dark" : "light";
}

function read(): ThemeMode {
  try {
    return parse(localStorage.getItem(KEY));
  } catch {
    return "system";
  }
}

let mode = read();
let resolved: ResolvedTheme = mode === "system" ? systemTheme() : mode;
const listeners = new Set<() => void>();

function apply(): void {
  const root = document.documentElement;
  root.dataset.theme = resolved;
  // 让原生控件（滚动条、下拉、日期选择器）也切过去，不然深色页面里会出现一条白滚动条。
  root.style.colorScheme = resolved;
}

// 模块加载时先落一次。首屏脚本没跑成（比如被 CSP 挡了）时，这里兜底。
apply();

// 选了"跟随系统"时，系统在应用运行期间改主题，这边要跟着翻。
query?.addEventListener("change", () => {
  if (mode !== "system") return;
  resolved = systemTheme();
  apply();
  for (const listener of listeners) listener();
});

export function getThemeMode(): ThemeMode {
  return mode;
}

export function getResolvedTheme(): ResolvedTheme {
  return resolved;
}

export function subscribeTheme(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function setThemeMode(next: ThemeMode): void {
  if (next === mode) return;
  mode = next;
  resolved = next === "system" ? systemTheme() : next;
  try {
    localStorage.setItem(KEY, next);
  } catch {
    // private mode / blocked storage
  }
  apply();
  for (const listener of listeners) listener();
}

/** 当前实际生效的那一套（"跟随系统"会解析成 light 或 dark）。 */
export function useResolvedTheme(): ResolvedTheme {
  return useSyncExternalStore(subscribeTheme, getResolvedTheme, getResolvedTheme);
}
