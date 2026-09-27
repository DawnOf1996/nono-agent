import type { PanelTab } from "@/features/files/panel-store";
import { normalizeWorkspace } from "@/lib/workspace";

export type SavedTerminal = {
  id: string;
  serverId: string;
  title: string;
  shell: string;
  cwd: string;
};

function storageKey(root: string): string {
  return `nono.terminals.v1:${normalizeWorkspace(root).toLowerCase()}`;
}

export function loadTerminals(root: string): SavedTerminal[] {
  if (!root.trim()) return [];
  try {
    const raw = localStorage.getItem(storageKey(root));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is SavedTerminal => {
      if (!item || typeof item !== "object") return false;
      const row = item as SavedTerminal;
      return Boolean(row.id && row.serverId && row.shell && row.cwd && row.title);
    });
  } catch {
    return [];
  }
}

export function saveTerminals(root: string, tabs: readonly PanelTab[]): void {
  if (!root.trim()) return;
  const saved: SavedTerminal[] = tabs.flatMap((tab) =>
    tab.kind === "terminal"
      ? [{ id: tab.id, serverId: tab.serverId, title: tab.title, shell: tab.shell, cwd: tab.cwd }]
      : [],
  );
  try {
    localStorage.setItem(storageKey(root), JSON.stringify(saved));
  } catch {
    // private mode / blocked storage
  }
}
