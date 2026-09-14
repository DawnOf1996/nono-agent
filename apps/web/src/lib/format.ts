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

export function formatDuration(ms: number): string {
  if (!ms) return "";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return `${minutes}m${rest}s`;
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

export function formatTokens(n: number): string {
  if (n < 1000) return String(n);
  if (n < 10_000) return `${(n / 1000).toFixed(1)}k`;
  return `${Math.round(n / 1000)}k`;
}
