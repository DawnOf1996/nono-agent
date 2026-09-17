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
