import type { PanelTab } from "@/features/files/panel-store";
import type { SplitNode } from "@/features/files/split";
import { normalizeWorkspace } from "@/lib/workspace";

import type { TerminalInfo } from "@/api/client";

/**
 * 面板的本地布局缓存。
 *
 * 终端的**存在性**由服务端决定（切换工作区不终止进程，进程活多久是服务端的事），
 * 这里只保存拿不回来的那一半：标签页身份、标题、停靠位置和分栏。切回工作区时
 * 用 `listTerminals` 的结果与这份缓存对账，缓存丢了也不会丢终端。
 */
export type SavedTerminal = {
  id: string;
  serverId: string;
  title: string;
  shell: string;
  cwd: string;
};

export type SavedPanel = {
  terminals: SavedTerminal[];
  bottom: SplitNode | null;
};

const EMPTY: SavedPanel = { terminals: [], bottom: null };

function storageKey(root: string): string {
  return `nono.terminals.v2:${normalizeWorkspace(root).toLowerCase()}`;
}

function isTerminalTab(tab: PanelTab): tab is PanelTab & { kind: "terminal" } {
  return tab.kind === "terminal";
}

function isSplitNode(value: unknown): value is SplitNode {
  if (!value || typeof value !== "object") return false;
  const node = value as Partial<SplitNode>;
  if (node.kind === "leaf") {
    return typeof node.id === "string" && Array.isArray(node.tabIds);
  }
  if (node.kind === "split") {
    return typeof node.id === "string" && Array.isArray(node.children) && node.children.length === 2;
  }
  return false;
}

export function loadPanel(root: string): SavedPanel {
  if (!root.trim()) return EMPTY;
  try {
    const raw = localStorage.getItem(storageKey(root));
    if (!raw) return EMPTY;
    const parsed = JSON.parse(raw) as Partial<SavedPanel>;
    const terminals = Array.isArray(parsed.terminals) ? parsed.terminals : [];
    return {
      terminals: terminals.filter((item): item is SavedTerminal => {
        if (!item || typeof item !== "object") return false;
        const row = item as SavedTerminal;
        return Boolean(row.id && row.serverId && row.shell && row.cwd && row.title);
      }),
      bottom: isSplitNode(parsed.bottom) ? parsed.bottom : null,
    };
  } catch {
    return EMPTY;
  }
}

export function savePanel(root: string, tabs: readonly PanelTab[], bottom: SplitNode): void {
  if (!root.trim()) return;
  const saved: SavedPanel = {
    terminals: tabs.filter(isTerminalTab).map((tab) => ({
      id: tab.id,
      serverId: tab.serverId,
      title: tab.title,
      shell: tab.shell,
      cwd: tab.cwd,
    })),
    bottom,
  };
  try {
    localStorage.setItem(storageKey(root), JSON.stringify(saved));
  } catch {
    // private mode / 配额写满：布局缓存丢了不影响终端本身，服务端还持有它。
  }
}

/**
 * 用服务端的终端列表和本地布局对账，算出该工作区应该有的标签页。
 *
 * 服务端有、本地没有的补出来（本地缓存被清空、换浏览器都走这条路）；本地有、
 * 服务端没有的保留，让 TerminalPane 显示「已结束」并给出显式重建入口，而不是
 * 静默丢弃——用户可能只是还没看见它退出。
 */
export function reconcileTerminals(saved: readonly SavedTerminal[], live: readonly TerminalInfo[]) {
  const byServerId = new Map(saved.map((item) => [item.serverId, item]));
  const tabs: PanelTab[] = [];
  const seen = new Set<string>();

  for (const item of live) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    const cached = byServerId.get(item.id);
    tabs.push({
      id: cached?.id ?? `terminal:${item.id}`,
      kind: "terminal",
      serverId: item.id,
      title: cached?.title ?? item.title ?? item.label,
      shell: item.shell,
      cwd: item.cwd,
    });
  }
  for (const cached of saved) {
    if (seen.has(cached.serverId)) continue;
    seen.add(cached.serverId);
    tabs.push({
      id: cached.id,
      kind: "terminal",
      serverId: cached.serverId,
      title: cached.title,
      shell: cached.shell,
      cwd: cached.cwd,
    });
  }
  return tabs;
}

/** 布局里的分栏只保留仍然存在的标签页，丢掉已经关掉的那些 id。 */
export function pruneBottom(bottom: SplitNode | null, liveTabIds: ReadonlySet<string>): SplitNode | null {
  if (!bottom) return null;
  const walk = (node: SplitNode): SplitNode | null => {
    if (node.kind === "leaf") {
      const tabIds = node.tabIds.filter((id) => liveTabIds.has(id));
      return { ...node, tabIds, active: node.active && liveTabIds.has(node.active) ? node.active : null };
    }
    const left = walk(node.children[0]);
    const right = walk(node.children[1]);
    if (!left) return right;
    if (!right) return left;
    return { ...node, children: [left, right] };
  };
  return walk(bottom);
}
