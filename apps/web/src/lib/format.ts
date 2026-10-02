import { workspaceLabel } from "@/lib/workspace";

export function sessionTitle(
  id: string,
  titles: Record<string, string>,
  fallback?: string,
): string {
  return titles[id] || fallback?.trim() || "新会话";
}

export function groupLabel(cwd: string): string {
  if (cwd === "未绑定目录") return cwd;
  return workspaceLabel(cwd);
}

export function formatElapsed(ms: number): string {
  return `${(Math.max(0, ms) / 1000).toFixed(1)}秒`;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

function sameDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
  );
}

/**
 * 消息气泡上的时刻（毫秒时间戳）。
 *
 * 同一天只给时分 —— 列表里每条都重复「今天」没人看；昨天补一个字，
 * 再往前的补上月日；跨年才带上年份。缺失时间戳时返回空串，调用方据此不渲染。
 */
export function formatMessageTime(ms?: number | null): string {
  if (!ms || !Number.isFinite(ms)) return "";
  const at = new Date(ms);
  if (Number.isNaN(at.getTime())) return "";
  const now = new Date();
  const clock = `${pad2(at.getHours())}:${pad2(at.getMinutes())}`;
  if (sameDay(at, now)) return clock;
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (sameDay(at, yesterday)) return `昨天 ${clock}`;
  const monthDay = `${at.getMonth() + 1}月${at.getDate()}日 ${clock}`;
  return at.getFullYear() === now.getFullYear() ? monthDay : `${at.getFullYear()}年${monthDay}`;
}

/** 时刻的 title 提示：补齐秒和年份，避免跨天/跨年后看不出是哪条。 */
export function formatFullTime(ms?: number | null): string {
  if (!ms || !Number.isFinite(ms)) return "";
  const at = new Date(ms);
  if (Number.isNaN(at.getTime())) return "";
  return `${at.getFullYear()}年${at.getMonth() + 1}月${at.getDate()}日 ${pad2(at.getHours())}:${pad2(
    at.getMinutes(),
  )}:${pad2(at.getSeconds())}`;
}

export function formatRelativeTime(unixSeconds?: number | null): string {
  if (!unixSeconds) return "";
  const elapsed = Date.now() - unixSeconds * 1000;
  if (elapsed < 60_000) return "刚刚";
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 60) return `${minutes}分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时`;
  const days = Math.floor(hours / 24);
  if (days < 365) return `${days}天`;
  return `${Math.floor(days / 365)}年`;
}

export function formatTokenSize(n: number | null | undefined): string {
  if (n == null || n <= 0) return "";
  if (n % 1_000_000 === 0) return `${n / 1_000_000}M`;
  if (n % 1_000 === 0) return `${n / 1_000}K`;
  return String(n);
}

export function parseTokenSize(raw: string): number | null {
  const text = raw.trim().replace(/,/g, "").replace(/_/g, "");
  if (!text) return null;
  const match = text.match(/^(\d+)\s*([kKmM])?$/);
  if (!match) return null;
  let n = Number(match[1]);
  const suffix = (match[2] ?? "").toUpperCase();
  if (suffix === "K") n *= 1_000;
  if (suffix === "M") n *= 1_000_000;
  return n > 0 ? n : null;
}
