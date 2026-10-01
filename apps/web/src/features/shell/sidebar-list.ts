import type { SessionListItem, Workspace } from "@nono/protocol";

export type GroupMode = "workspace" | "tree" | "flat";
export type SortMode = "manual" | "recent";
export type ArchiveFilter = "hide" | "all" | "only";

const VIEW_KEY = "nono.sidebar.listView";
const ORDER_KEY = "nono.sidebar.sessionOrder";
const ARCHIVE_KEY = "nono.sidebar.archiveFilter";

export function loadArchiveFilter(): ArchiveFilter {
  try {
    const raw = localStorage.getItem(ARCHIVE_KEY);
    if (raw === "all" || raw === "only" || raw === "hide") return raw;
  } catch {
    // private mode / blocked storage
  }
  return "hide";
}

export function saveArchiveFilter(filter: ArchiveFilter) {
  try {
    localStorage.setItem(ARCHIVE_KEY, filter);
  } catch {
    // private mode / blocked storage
  }
}

export function loadListView(): { group: GroupMode; sort: SortMode } {
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (!raw) return { group: "workspace", sort: "recent" };
    const parsed = JSON.parse(raw) as { group?: string; sort?: string };
    const group: GroupMode = parsed.group === "tree" || parsed.group === "flat" ? parsed.group : "workspace";
    const sort: SortMode = parsed.sort === "manual" ? "manual" : "recent";
    return { group, sort };
  } catch {
    return { group: "workspace", sort: "recent" };
  }
}

export function saveListView(view: { group: GroupMode; sort: SortMode }) {
  try {
    localStorage.setItem(VIEW_KEY, JSON.stringify(view));
  } catch {
    // private mode / blocked storage
  }
}

export function loadManualOrder(): string[] {
  try {
    const raw = localStorage.getItem(ORDER_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === "string");
  } catch {
    return [];
  }
}

export function saveManualOrder(ids: string[]) {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(ids));
  } catch {
    // private mode / blocked storage
  }
}

/** 新会话排在已保存顺序前面，其余按上次拖动的顺序。 */
export function mergeManualOrder(saved: string[], ids: string[]): string[] {
  const present = new Set(ids);
  const known = saved.filter((id) => present.has(id));
  const knownSet = new Set(known);
  const fresh = ids.filter((id) => !knownSet.has(id));
  return [...fresh, ...known];
}

export function reorderManual(saved: string[], ids: string[], sourceId: string, targetId: string): string[] {
  const base = mergeManualOrder(saved, ids);
  const from = base.indexOf(sourceId);
  const to = base.indexOf(targetId);
  if (from < 0 || to < 0 || from === to) return base;
  const next = base.slice();
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

export function sortSessions(sessions: SessionListItem[], mode: SortMode, order: string[]): SessionListItem[] {
  const recent = [...sessions].sort(
    (a, b) => b.updated_at - a.updated_at || a.id.localeCompare(b.id),
  );
  if (mode === "recent") return recent;
  const rank = new Map(
    mergeManualOrder(
      order,
      recent.map((item) => item.id),
    ).map((id, index) => [id, index]),
  );
  return [...sessions].sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0));
}

export type WorkspaceTreeNode =
  | { kind: "dir"; id: string; label: string; children: WorkspaceTreeNode[] }
  | { kind: "workspace"; workspace: Workspace; children: WorkspaceTreeNode[] };

type Trie = {
  name: string;
  path: string;
  sep: string;
  workspace?: Workspace;
  children: Map<string, Trie>;
};

function pathParts(path: string): { segments: string[]; sep: string } {
  const trimmed = path.trim().replace(/[\\/]+$/, "");
  const sep = trimmed.includes("\\") ? "\\" : "/";
  return { segments: trimmed.split(/[\\/]+/).filter(Boolean), sep };
}

function foldTrie(node: Trie): Trie {
  let current = node;
  let name = node.name;
  let path = node.path;
  let sep = node.sep;
  while (!current.workspace && current.children.size === 1) {
    const only = [...current.children.values()][0];
    name = name ? `${name}${sep}${only.name}` : only.name;
    path = only.path;
    sep = only.sep;
    current = only;
  }
  const children = new Map<string, Trie>();
  for (const [key, child] of current.children) {
    children.set(key, foldTrie(child));
  }
  return { name, path, sep, workspace: current.workspace, children };
}

function trieToNode(node: Trie): WorkspaceTreeNode {
  const children = [...node.children.values()]
    .sort((a, b) => a.name.localeCompare(b.name, "zh"))
    .map(trieToNode);
  if (node.workspace) {
    return { kind: "workspace", workspace: node.workspace, children };
  }
  return { kind: "dir", id: `dir:${node.path.toLowerCase()}`, label: node.name, children };
}

/** 把工作区路径收成目录树。只有一条路径时会折叠成工作区本身。 */
export function buildWorkspaceTree(workspaces: Workspace[]): WorkspaceTreeNode[] {
  const root: Trie = { name: "", path: "", sep: "\\", children: new Map() };
  for (const workspace of workspaces) {
    const { segments, sep } = pathParts(workspace.path);
    if (!segments.length) continue;
    let cursor = root;
    let acc = "";
    for (const segment of segments) {
      acc = acc ? `${acc}${sep}${segment}` : segment;
      const key = segment.toLowerCase();
      let next = cursor.children.get(key);
      if (!next) {
        next = { name: segment, path: acc, sep, children: new Map() };
        cursor.children.set(key, next);
      }
      cursor = next;
    }
    cursor.workspace = workspace;
  }
  return [...root.children.values()]
    .map(foldTrie)
    .sort((a, b) => a.name.localeCompare(b.name, "zh"))
    .map(trieToNode);
}

/** 「仅显示已归档」时丢掉没有归档会话、也没有子节点的枝。 */
export function pruneEmptyArchiveTree(
  nodes: WorkspaceTreeNode[],
  sessionsByWorkspace: Map<string, SessionListItem[]>,
): WorkspaceTreeNode[] {
  const result: WorkspaceTreeNode[] = [];
  for (const node of nodes) {
    if (node.kind === "dir") {
      const children = pruneEmptyArchiveTree(node.children, sessionsByWorkspace);
      if (children.length) result.push({ ...node, children });
      continue;
    }
    const children = pruneEmptyArchiveTree(node.children, sessionsByWorkspace);
    const sessions = sessionsByWorkspace.get(node.workspace.id) ?? [];
    if (sessions.length || children.length) result.push({ ...node, children });
  }
  return result;
}
