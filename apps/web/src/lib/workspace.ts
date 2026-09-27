const WORKSPACE_KEY = "nono.draft.workspace";
const WORKSPACES_KEY = "nono.workspaces";
const MODEL_KEY = "nono.draft.model";

function readDraft(key: string): string {
  try {
    return localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(key: string, value: string): void {
  try {
    const trimmed = value.trim();
    if (trimmed) localStorage.setItem(key, trimmed);
    else localStorage.removeItem(key);
  } catch {
    // private mode / blocked storage
  }
}

export function loadDraftWorkspace(): string {
  return readDraft(WORKSPACE_KEY);
}

export function saveDraftWorkspace(value: string): void {
  writeDraft(WORKSPACE_KEY, value);
}

export function loadDraftModel(): string {
  return readDraft(MODEL_KEY);
}

export function saveDraftModel(value: string): void {
  writeDraft(MODEL_KEY, value);
}

export function normalizeWorkspace(cwd: string): string {
  return cwd.trim().replace(/[\\/]+$/, "").replace(/\//g, "\\");
}

export function sameWorkspace(a: string, b: string): boolean {
  return normalizeWorkspace(a).toLowerCase() === normalizeWorkspace(b).toLowerCase();
}

export function loadWorkspaces(): string[] {
  try {
    const raw = localStorage.getItem(WORKSPACES_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return mergeWorkspaces(parsed.filter((item): item is string => typeof item === "string"));
  } catch {
    return [];
  }
}

export function saveWorkspaces(paths: string[]): void {
  try {
    localStorage.setItem(WORKSPACES_KEY, JSON.stringify(mergeWorkspaces(paths)));
  } catch {
    // private mode / blocked storage
  }
}

export function mergeWorkspaces(paths: Array<string | null | undefined>): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of paths) {
    const path = (raw ?? "").trim();
    if (!path) continue;
    const key = normalizeWorkspace(path).toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(normalizeWorkspace(path));
  }
  return result;
}

/** 拼进输入框的 @文件 引用。路径相对工作区，含空格时加引号。 */
export function mentionToken(root: string, target: string): string {
  const relative = relativeTo(root, target).replace(/\\/g, "/");
  const body = /[\s"]/.test(relative) ? `"${relative.replace(/"/g, '\\"')}"` : relative;
  return `@${body} `;
}

/** 把搜索返回的正斜杠相对路径接回工作区绝对路径。 */
export function joinWorkspace(root: string, relative: string): string {
  const base = root.replace(/[\\/]+$/, "");
  const separator = base.includes("\\") ? "\\" : "/";
  return `${base}${separator}${relative.replace(/\//g, separator)}`;
}

/** target 在 root 内时返回相对路径（反斜杠），否则原样返回。 */
export function relativeTo(root: string, target: string): string {
  const base = normalizeWorkspace(root);
  const full = normalizeWorkspace(target);
  if (full.toLowerCase().startsWith(`${base.toLowerCase()}\\`)) return full.slice(base.length + 1);
  if (full.toLowerCase() === base.toLowerCase()) return full;
  return full;
}

export function baseName(path: string): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** 不含文件名的上级路径（正斜杠），没有上级时是空串。 */
export function parentPath(path: string): string {
  const parts = path.replace(/\\/g, "/").replace(/\/+$/, "").split("/");
  parts.pop();
  return parts.join("/");
}

export function workspaceLabel(cwd: string): string {
  const normalized = cwd.replace(/[\\/]+$/, "");
  const parts = normalized.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || cwd;
}

export async function pickWorkspace(current?: string): Promise<string | null> {
  if (window.nonoDesktop?.selectWorkspace) {
    return window.nonoDesktop.selectWorkspace();
  }
  const next = window.prompt("工作空间绝对路径", current ?? "");
  const trimmed = next?.trim() ?? "";
  return trimmed || null;
}
