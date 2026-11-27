import type { FileDiff } from "@nono/protocol";

import type { DiffTarget, PanelTab } from "@/features/files/panel-store";
import type { SplitNode } from "@/features/files/split";
import { normalizeWorkspace } from "@/lib/workspace";

/**
 * 右侧面板的本地快照缓存，**按会话分桶**。
 *
 * 面板里的标签页是「这段对话我当时在看什么」，不是「这个工作区里有什么」：A 会话翻开的
 * 文件、对比、网页，切到 B 会话不该跟着过去，切回来又得原样在。所以这里以会话（未落库的
 * 新会话用它的 draft 键）为键存一整份面板状态。
 *
 * 终端的**存在性**仍然由服务端决定（切换工作区不终止进程），快照只负责把标签页的
 * 身份、标题、停靠位置和分栏留下来；切回来时再用 `listTerminals` 的结果核对一遍。
 */
export type SavedTab =
  | { kind: "file"; id: string; path: string; title: string }
  | { kind: "diff"; id: string; title: string; target: DiffTarget }
  | { kind: "browser"; id: string; url: string; title: string }
  | { kind: "terminal"; id: string; serverId: string; title: string; shell: string; cwd: string }
  | { kind: "sidechat"; id: string; productId: string; engineId: string; parentId: string; title: string };

export type SavedPins = { files: boolean; changes: boolean; tasks: boolean };

export type SavedPanel = {
  version: 1;
  /** 写下这份快照时的工作区，用来在读回时丢弃换了根目录的旧桶。 */
  root: string;
  updatedAt: number;
  tabs: SavedTab[];
  pins: SavedPins;
  active: string;
  lens: "git" | "session";
  bottom: SplitNode | null;
  sideFile: { path: string; title: string } | null;
  embeddedFile: { path: string; title: string } | null;
};

/** 单份快照超过这个体积就不落盘了：diff 内容可能很大，写爆配额会连终端布局一起丢。 */
const MAX_SNAPSHOT_BYTES = 1_500_000;
/** 最多留多少个会话的快照，超出的按最旧的丢。 */
const MAX_SNAPSHOTS = 40;

const PREFIX = "nono.panel.v3";
const INDEX_KEY = `${PREFIX}.index`;
/** 旧版只按工作区存终端布局；升级后读一次就迁移到当前会话的桶里。 */
const LEGACY_TERMINAL_PREFIX = "nono.terminals.v2";

export const EMPTY_PINS: SavedPins = { files: false, changes: false, tasks: false };

const EMPTY: SavedPanel = {
  version: 1,
  root: "",
  updatedAt: 0,
  tabs: [],
  pins: { ...EMPTY_PINS },
  active: "",
  lens: "git",
  bottom: null,
  sideFile: null,
  embeddedFile: null,
};

function bucketKey(root: string, scope: string): string {
  return `${PREFIX}:${normalizeWorkspace(root).toLowerCase()}:${scope}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function isSplitNode(value: unknown): value is SplitNode {
  if (!isRecord(value)) return false;
  if (value.kind === "leaf") {
    return typeof value.id === "string" && Array.isArray(value.tabIds);
  }
  if (value.kind === "split") {
    return (
      typeof value.id === "string" &&
      (value.dir === "row" || value.dir === "col") &&
      typeof value.ratio === "number" &&
      Array.isArray(value.children) &&
      value.children.length === 2
    );
  }
  return false;
}

/**
 * diff 的目标有四种形态，逐个校验字段。
 *
 * `op` 那一种内嵌了整份 FileDiff，只认它是对象就放行：这份内容是我们自己写进去的，
 * 真要损坏，DiffTab 自己的错误分支会接住。
 */
function readDiffTarget(value: unknown): DiffTarget | null {
  if (!isRecord(value)) return null;
  switch (value.source) {
    case "git": {
      const root = text(value.root);
      const path = text(value.path);
      const absPath = text(value.absPath);
      if (!root || !path || !absPath) return null;
      return {
        source: "git",
        root,
        repo: typeof value.repo === "string" ? value.repo : null,
        path,
        absPath,
        staged: value.staged === true,
        untracked: value.untracked === true,
      };
    }
    case "turn": {
      const sessionId = text(value.sessionId);
      const turnId = text(value.turnId);
      if (!sessionId || !turnId) return null;
      return { source: "turn", sessionId, turnId, index: Number(value.index) || 0 };
    }
    case "commit": {
      const root = text(value.root);
      const hash = text(value.hash);
      if (!root || !hash) return null;
      return {
        source: "commit",
        root,
        repo: typeof value.repo === "string" ? value.repo : null,
        hash,
        subject: typeof value.subject === "string" ? value.subject : "",
      };
    }
    case "op": {
      const callId = text(value.callId);
      if (!callId || !isRecord(value.diff)) return null;
      return {
        source: "op",
        callId,
        diff: value.diff as unknown as FileDiff,
        ...(typeof value.errorText === "string" ? { errorText: value.errorText } : {}),
      };
    }
    default:
      return null;
  }
}

function readTab(value: unknown): SavedTab | null {
  if (!isRecord(value)) return null;
  const id = text(value.id);
  if (!id) return null;
  const title = typeof value.title === "string" ? value.title : "";
  switch (value.kind) {
    case "file": {
      const path = text(value.path);
      return path ? { kind: "file", id, path, title } : null;
    }
    case "browser":
      return { kind: "browser", id, url: typeof value.url === "string" ? value.url : "", title };
    case "terminal": {
      const serverId = text(value.serverId);
      const shell = text(value.shell);
      const cwd = text(value.cwd);
      if (!serverId || !shell || !cwd) return null;
      return { kind: "terminal", id, serverId, title, shell, cwd };
    }
    case "sidechat": {
      const productId = text(value.productId);
      const engineId = text(value.engineId);
      const parentId = text(value.parentId);
      if (!productId || !engineId || !parentId) return null;
      return { kind: "sidechat", id, productId, engineId, parentId, title };
    }
    case "diff": {
      const target = readDiffTarget(value.target);
      return target ? { kind: "diff", id, title, target } : null;
    }
    default:
      return null;
  }
}

function readFileRef(value: unknown): { path: string; title: string } | null {
  if (!isRecord(value)) return null;
  const path = text(value.path);
  if (!path) return null;
  return { path, title: typeof value.title === "string" ? value.title : "" };
}

function readSnapshot(raw: string | null, root: string): SavedPanel | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!isRecord(parsed) || parsed.version !== 1) return null;
  const savedRoot = typeof parsed.root === "string" ? parsed.root : "";
  // 工作区对不上就当没有：标签页里的路径是绝对路径，落在别的根目录上只会指向意外的地方。
  if (!savedRoot || !sameRoot(savedRoot, root)) return null;

  const tabs = Array.isArray(parsed.tabs)
    ? parsed.tabs.map(readTab).filter((tab): tab is SavedTab => tab !== null)
    : [];
  const seen = new Set<string>();
  const deduped = tabs.filter((tab) => {
    if (seen.has(tab.id)) return false;
    seen.add(tab.id);
    return true;
  });
  const pins = isRecord(parsed.pins) ? parsed.pins : {};

  return {
    version: 1,
    root: savedRoot,
    updatedAt: typeof parsed.updatedAt === "number" ? parsed.updatedAt : 0,
    tabs: deduped,
    pins: {
      files: pins.files === true,
      changes: pins.changes === true,
      tasks: pins.tasks === true,
    },
    active: typeof parsed.active === "string" ? parsed.active : "",
    lens: parsed.lens === "session" ? "session" : "git",
    bottom: isSplitNode(parsed.bottom) ? parsed.bottom : null,
    sideFile: readFileRef(parsed.sideFile),
    embeddedFile: readFileRef(parsed.embeddedFile),
  };
}

function sameRoot(a: string, b: string): boolean {
  return normalizeWorkspace(a).toLowerCase() === normalizeWorkspace(b).toLowerCase();
}

/* ── 索引：只为了知道该淘汰谁，不参与读回 ────────────────────────────────── */

type IndexEntry = { key: string; updatedAt: number };

function readIndex(): IndexEntry[] {
  try {
    const raw = localStorage.getItem(INDEX_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .map((item): IndexEntry | null => {
        if (!isRecord(item)) return null;
        const key = text(item.key);
        return key ? { key, updatedAt: typeof item.updatedAt === "number" ? item.updatedAt : 0 } : null;
      })
      .filter((item): item is IndexEntry => item !== null);
  } catch {
    return [];
  }
}

function writeIndex(entries: IndexEntry[]): void {
  try {
    const kept = entries.slice().sort((a, b) => b.updatedAt - a.updatedAt).slice(0, MAX_SNAPSHOTS);
    // 先写索引再删旧桶：中途失败最多少认一个孤儿桶，不会留下指向已删桶的索引。
    localStorage.setItem(INDEX_KEY, JSON.stringify(kept));
    for (const entry of entries) {
      if (!kept.some((item) => item.key === entry.key)) {
        try {
          localStorage.removeItem(entry.key);
        } catch {
          // 单个桶删不掉不影响别的
        }
      }
    }
  } catch {
    // private mode
  }
}

function remember(key: string, updatedAt: number): void {
  const entries = readIndex().filter((item) => item.key !== key);
  entries.push({ key, updatedAt });
  writeIndex(entries);
}

function forget(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    // private mode
  }
  writeIndex(readIndex().filter((item) => item.key !== key));
}

/* ── 读 / 写 ─────────────────────────────────────────────────────────────── */

/** 取某个会话的面板快照；没有就返回 null，调用方据此决定是恢复还是开空白面板。 */
export function loadPanel(root: string, scope: string): SavedPanel | null {
  if (!root.trim() || !scope) return null;
  let raw: string | null = null;
  try {
    raw = localStorage.getItem(bucketKey(root, scope));
  } catch {
    return null;
  }
  const snapshot = readSnapshot(raw, root);
  if (snapshot) return snapshot;
  return migrateLegacy(root, scope);
}

/**
 * 从旧版「按工作区存终端」的缓存里搬一次。
 *
 * 旧键里只有终端标签和分栏，没有文件/对比/网页。搬给**第一次进来时正在看的那个会话**，
 * 搬完删掉旧键，避免升级之后每次切会话都重复认领一遍。
 */
function migrateLegacy(root: string, scope: string): SavedPanel | null {
  let legacyKey: string | null = null;
  let raw: string | null = null;
  try {
    legacyKey = `${LEGACY_TERMINAL_PREFIX}:${normalizeWorkspace(root).toLowerCase()}`;
    raw = localStorage.getItem(legacyKey);
  } catch {
    return null;
  }
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { terminals?: unknown; bottom?: unknown };
    const terminals = Array.isArray(parsed.terminals)
      ? parsed.terminals
          .map((item) => readTab({ ...(isRecord(item) ? item : {}), kind: "terminal" }))
          .filter((tab): tab is SavedTab => tab !== null && tab.kind === "terminal")
      : [];
    const snapshot: SavedPanel = {
      ...EMPTY,
      root,
      updatedAt: Date.now(),
      tabs: terminals,
      bottom: isSplitNode(parsed.bottom) ? parsed.bottom : null,
    };
    savePanel(root, scope, snapshot);
    return snapshot;
  } catch {
    return null;
  } finally {
    if (legacyKey) forget(legacyKey);
  }
}

export function savePanel(root: string, scope: string, snapshot: Omit<SavedPanel, "version" | "root" | "updatedAt">): void {
  if (!root.trim() || !scope) return;
  const key = bucketKey(root, scope);
  const payload: SavedPanel = { version: 1, root, updatedAt: Date.now(), ...snapshot };
  try {
    const raw = JSON.stringify(payload);
    if (raw.length > MAX_SNAPSHOT_BYTES) {
      // 太大了就退一步：丢掉 diff 里的 FileDiff 再存一次，标签页本身仍然能恢复。
      const slim: SavedPanel = {
        ...payload,
        tabs: payload.tabs.map((tab) =>
          tab.kind === "diff" && tab.target.source === "op"
            ? {
                ...tab,
                target: { ...tab.target, diff: { ...tab.target.diff, hunks: [] } },
              }
            : tab,
        ),
      };
      const retry = JSON.stringify(slim);
      if (retry.length > MAX_SNAPSHOT_BYTES) {
        forget(key);
        return;
      }
      localStorage.setItem(key, retry);
    } else {
      localStorage.setItem(key, raw);
    }
    remember(key, payload.updatedAt);
  } catch {
    // private mode / 配额写满：布局缓存丢了不影响终端本身，服务端还持有它。
  }
}

/** 会话被删掉时清掉它的桶，别让索引一直背着。 */
export function dropPanel(root: string, scope: string): void {
  if (!root.trim() || !scope) return;
  forget(bucketKey(root, scope));
}

/* ── 布局维护 ─────────────────────────────────────────────────────────────── */

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

/** 把存回来的标签页还原成面板内部用的形状。 */
export function toPanelTabs(saved: readonly SavedTab[]): PanelTab[] {
  return saved.map((tab) => ({ ...tab }));
}