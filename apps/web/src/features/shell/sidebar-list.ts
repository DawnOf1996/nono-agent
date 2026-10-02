import type { SessionListItem, Workspace } from "@nono/protocol";

export type GroupMode = "workspace" | "tree" | "flat";
export type SortMode = "manual" | "recent";
export type ArchiveFilter = "hide" | "all" | "only";
/** 落点相对目标行的位置：上沿插入还是下沿插入。 */
export type DropSide = "before" | "after";

/** 一个分组默认露出多少条会话，多出来的折叠成「展开其余 N 个会话」。 */
export const DEFAULT_VISIBLE_SESSIONS = 5;

/**
 * 分组里默认只露前 limit 条会话。
 *
 * 展开时原样返回。收起时当前打开的会话永远留着，免得选中项被藏进折叠区；
 * 「新会话」草稿行不算在 limit 里，由调用方单独渲染。
 */
export function visibleSessionWindow<T extends { engine_session_id: string }>(
  sessions: T[],
  limit: number,
  options: { expanded?: boolean; activeSessionId?: string | null } = {},
): { shown: T[]; hidden: number } {
  const { expanded = false, activeSessionId = null } = options;
  if (expanded || sessions.length <= limit) {
    return { shown: sessions, hidden: 0 };
  }
  const head = sessions.slice(0, limit);
  const active = activeSessionId
    ? sessions.find((item) => item.engine_session_id === activeSessionId)
    : undefined;
  if (active && !head.includes(active)) head.push(active);
  return { shown: head, hidden: sessions.length - head.length };
}

const VIEW_KEY = "nono.sidebar.listView";
const ORDER_KEY = "nono.sidebar.sessionOrder";
const WORKSPACE_ORDER_KEY = "nono.sidebar.workspaceOrder";
const ARCHIVE_KEY = "nono.sidebar.archiveFilter";
const EXPANDED_GROUPS_KEY = "nono.sidebar.expandedSessionGroups";

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

/** 哪些分组被手动展开过会话折叠区，默认都是折叠的。 */
export function loadExpandedSessionGroups(): Set<string> {
  return new Set(loadIds(EXPANDED_GROUPS_KEY));
}

export function saveExpandedSessionGroups(ids: Set<string>) {
  try {
    localStorage.setItem(EXPANDED_GROUPS_KEY, JSON.stringify([...ids]));
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

function loadIds(key: string): string[] {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === "string");
  } catch {
    return [];
  }
}

export function loadManualOrder(): string[] {
  return loadIds(ORDER_KEY);
}

export function saveManualOrder(ids: string[]) {
  try {
    localStorage.setItem(ORDER_KEY, JSON.stringify(ids));
  } catch {
    // private mode / blocked storage
  }
}

export function loadWorkspaceOrder(): string[] {
  return loadIds(WORKSPACE_ORDER_KEY);
}

export function saveWorkspaceOrder(ids: string[]) {
  try {
    localStorage.setItem(WORKSPACE_ORDER_KEY, JSON.stringify(ids));
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

/**
 * 把 source 挪到 target 的上沿或下沿。
 *
 * `ids` 可以只传当前可见的那些行：手动顺序是全序，插进可见的邻居之间，
 * 隐藏行仍然留在原来的相对位置上。source 紧挨着落点时是空操作。
 */
export function reorderManualTo(
  saved: string[],
  ids: string[],
  sourceId: string,
  targetId: string,
  side: DropSide,
): string[] {
  const base = mergeManualOrder(saved, ids);
  const from = base.indexOf(sourceId);
  const to = base.indexOf(targetId);
  if (from < 0 || to < 0) return base;
  // 摘掉 source 之后 target 会前移一位，插它前面要跟着退一位。
  const at = side === "after" ? to : to > from ? to - 1 : to;
  if (at === from) return base;
  const next = base.slice();
  const [moved] = next.splice(from, 1);
  next.splice(at, 0, moved);
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

/** 工作区没有“最近更新”以外的排法，一直按拖出来的顺序排，没排过的沿用列表原序。 */
export function orderWorkspaces<T extends { id: string }>(workspaces: T[], order: string[]): T[] {
  const rank = new Map(
    mergeManualOrder(
      order,
      workspaces.map((item) => item.id),
    ).map((id, index) => [id, index]),
  );
  return [...workspaces].sort((a, b) => (rank.get(a.id) ?? 0) - (rank.get(b.id) ?? 0));
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
