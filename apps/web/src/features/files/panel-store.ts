import { create } from "zustand";

import type { FileDiff } from "@nono/protocol";

import type { TerminalInfo } from "@/api/client";

import { activateInLeaf, collapse, emptySplit, placeTab, removeTab, setRatio, type DropZone, type SplitNode } from "@/features/files/split";
import {
  EMPTY_PINS,
  dropPanel,
  loadPanel,
  pruneBottom,
  savePanel,
  toPanelTabs,
  type SavedPins,
} from "@/features/files/panel-storage";
import { baseName, normalizeWorkspace, sameWorkspace } from "@/lib/workspace";

/** 一个对比标签页展示的内容。 */
export type DiffTarget =
  | {
      source: "git";
      root: string;
      repo: string | null;
      /** 仓库内相对路径（git 路径）。 */
      path: string;
      absPath: string;
      staged: boolean;
      untracked: boolean;
    }
  | {
      source: "turn";
      sessionId: string;
      turnId: string;
      index: number;
    }
  | {
      source: "commit";
      root: string;
      repo: string | null;
      hash: string;
      subject: string;
    }
  | {
      source: "op";
      callId: string;
      diff: FileDiff;
      errorText?: string;
    };

export type PanelTab =
  | { id: string; kind: "file"; path: string; title: string }
  | { id: string; kind: "diff"; title: string; target: DiffTarget }
  | { id: string; kind: "browser"; url: string; title: string }
  | { id: string; kind: "terminal"; serverId: string; title: string; shell: string; cwd: string }
  | { id: string; kind: "sidechat"; productId: string; engineId: string; parentId: string; title: string };

export type ChangesLens = "git" | "session";

const PREVIEW_HEIGHT_KEY = "nono.changes.previewHeight";

function readPreviewHeight(): number {
  try {
    const value = Number(localStorage.getItem(PREVIEW_HEIGHT_KEY));
    return Number.isFinite(value) && value >= 120 ? value : 220;
  } catch {
    return 220;
  }
}

export const TREE_TAB = "tree";
export const CHANGES_TAB = "changes";
export const TASKS_TAB = "tasks";
export const START_TAB = "start";

export type PanelPin = "files" | "changes" | "tasks";

type PanelPins = SavedPins;

const CLOSED_PINS: PanelPins = { ...EMPTY_PINS };

/** 新起一个终端标签页时传的东西；`id` 是本地标签 id，和服务端的 `serverId` 不是一回事。 */
export type NewTerminal = {
  id: string;
  serverId: string;
  title: string;
  shell: string;
  cwd: string;
};

/** 面板快照里和标签页顺序无关、但要跟着会话一起还原的部分。 */
type ScopeSnapshot = {
  tabs: PanelTab[];
  pins: PanelPins;
  active: string;
  lens: ChangesLens;
  bottom: SplitNode;
  sideFile: { path: string; title: string } | null;
  embeddedFile: { path: string; title: string } | null;
};

type PanelState = ScopeSnapshot & {
  root: string;
  /**
   * 面板当前归属的会话（未落库的新会话传它的 draft 键）。
   * 空字符串表示还没绑上 —— 这时候不落盘，免得把某个会话的标签写进公共桶。
   */
  scope: string;
  /** 文件树、预览、变更列表的刷新代数；一轮对话结束或保存文件后递增。 */
  revision: number;
  /** 请求 Workbench 展开右侧栏的计数。 */
  revealRequest: number;
  /** 用户点开一段对话的次数。用来在工作区重置之后再决定要不要展开文件树。 */
  conversationOpen: number;
  /** 请求展开底部工作台的计数。 */
  bottomRequest: number;
  dirty: Record<string, boolean>;
  /**
   * 各标签页未保存的编辑内容，键是「会话 + 标签页」。
   * 只活在内存里：落盘既占配额，又会在下次打开时把一个已经不存在的编辑缓冲区
   * 伪装成「有未保存修改」。切会话时换一份，切回来原样还在。
   */
  drafts: Record<string, string>;
  /** 变更页底部的 diff 预览，还没展开成独立标签。是一次性的临时态，不进快照。 */
  changePreview: { target: DiffTarget; title: string } | null;
  gitCount: number;
  taskCount: number;
  previewHeight: number;
  /**
   * 把面板切到「某工作区下的某个会话」：先把当前这份存回它自己的桶，再换上目标桶。
   * 会话切换和换工作区走的是同一条路 —— 换会话往往也意味着换工作区。
   */
  bindContext: (root: string, scope: string) => void;
  /** 会话被删除时清掉它的桶。 */
  forgetScope: (scope: string) => void;
  markConversationOpen: () => void;
  activate: (id: string) => void;
  showHome: () => void;
  openPin: (pin: PanelPin) => void;
  closePin: (pin: PanelPin) => void;
  showChanges: (lens?: ChangesLens) => void;
  setLens: (lens: ChangesLens) => void;
  openFile: (path: string, title?: string) => void;
  openDiff: (target: DiffTarget, title: string) => void;
  retarget: (id: string, target: DiffTarget, title: string) => void;
  close: (id: string) => void;
  setDirty: (id: string, dirty: boolean) => void;
  /** 记录/清除某个标签页的未保存内容，切换会话时靠它把编辑接回去。 */
  setDraftText: (id: string, text: string | null) => void;
  /** 某标签页在本会话里的未保存内容。 */
  draftFor: (id: string) => string | null;
  bumpRevision: () => void;
  setGitCount: (count: number) => void;
  setTaskCount: (count: number) => void;
  showTasks: () => void;
  openBeside: (path: string, title?: string) => void;
  clearSide: () => void;
  openBrowser: (url?: string) => void;
  patchBrowser: (id: string, patch: { url?: string; title?: string }) => void;
  addTerminal: (tab: NewTerminal) => void;
  patchTerminal: (id: string, patch: Partial<NewTerminal>) => void;
  /** 用服务端的终端列表刷新快照里的终端标签页；不认领别的会话的终端。 */
  restorePanel: (live: readonly TerminalInfo[]) => void;
  openSideChat: (input: { productId: string; engineId: string; parentId: string; title: string }, focus?: boolean) => void;
  retainSideChats: (parentId: string, keep?: ReadonlySet<string>) => void;
  setChangePreview: (target: DiffTarget, title: string) => void;
  clearChangePreview: () => void;
  setPreviewHeight: (height: number) => void;
  setEmbeddedFile: (path: string, title?: string) => void;
  dockTab: (tabId: string, leafId: string, zone: DropZone) => void;
  focusDocked: (leafId: string, tabId: string) => void;
  resizeDock: (splitId: string, ratio: number) => void;
};

export function fileTabId(path: string): string {
  return `file:${normalizeWorkspace(path).toLowerCase()}`;
}

export function diffTabId(target: DiffTarget): string {
  switch (target.source) {
    case "git":
      return `diff:git:${normalizeWorkspace(target.absPath).toLowerCase()}:${target.staged ? "s" : "u"}`;
    case "turn":
      return `diff:turn:${target.turnId}`;
    case "commit":
      return `diff:commit:${target.hash}`;
    case "op":
      return `diff:op:${target.callId}`;
  }
}

function fallbackActive(state: { pins: PanelPins; tabs: PanelTab[] }, excluding?: string): string {
  if (state.pins.files && excluding !== TREE_TAB) return TREE_TAB;
  if (state.pins.changes && excluding !== CHANGES_TAB) return CHANGES_TAB;
  if (state.pins.tasks && excluding !== TASKS_TAB) return TASKS_TAB;
  const tab = state.tabs.find((item) => item.id !== excluding);
  return tab?.id ?? START_TAB;
}

/**
 * 各会话未保存的编辑内容，跨切换保留。
 *
 * 标签页跟着会话换掉时，FilePreview 会整个卸载，CodeMirror 里那份没落盘的内容就没了。
 * 所以内容本身由 store 保管，切走时按会话收好，切回来再取出来喂回编辑器。
 * 只存内存：落盘会既占配额，又在下次打开时把一个早已不存在的缓冲区
 * 伪装成「有未保存修改」。
 */
const draftBuckets = new Map<string, Record<string, string>>();

/**
 * 只留下这个会话还开着的标签页对应的草稿。
 *
 * 标签页已经没了的草稿留着没有意义：没有标签会去挂载那个编辑器，它只是一块永远
 * 读不出来的内存，顺手清掉免得越攒越多。
 */
function pickDrafts(
  bucket: Record<string, string> | undefined,
  restored: { tabs: readonly { id: string }[] } | null,
): Record<string, string> {
  if (!bucket) return {};
  const ids = new Set(restored ? restored.tabs.map((tab) => tab.id) : []);
  const kept: Record<string, string> = {};
  for (const [id, text] of Object.entries(bucket)) {
    if (ids.has(id)) kept[id] = text;
  }
  return kept;
}

export const usePanelStore = create<PanelState>((set, get) => ({
  root: "",
  scope: "",
  tabs: [],
  pins: { ...CLOSED_PINS },
  active: START_TAB,
  lens: "git",
  revision: 0,
  revealRequest: 0,
  conversationOpen: 0,
  bottomRequest: 0,
  bottom: emptySplit(),
  dirty: {},
  drafts: {},
  gitCount: 0,
  taskCount: 0,
  sideFile: null,
  changePreview: null,
  previewHeight: readPreviewHeight(),
  embeddedFile: null,
  markConversationOpen: () => set((state) => ({ conversationOpen: state.conversationOpen + 1 })),
  bindContext: (root, scope) => {
    const current = get();
    const rootChanged = current.root === "" || !sameWorkspace(current.root, root);
    if (!rootChanged && current.scope === scope) return;
    // 先把手上这份按它自己的 root/scope 存回去，再去读新的那一份。
    persistScope(current);
    // 未保存的编辑也先收进它所属会话的桶，切回来才接得上。
    if (current.scope && Object.keys(current.drafts).length) {
      draftBuckets.set(current.scope, current.drafts);
    }
    // 草稿变成正式会话时，面板直接带过去：否则第一条消息发出去的那一刻，
    // 用户刚翻开的文件和刚开的终端会集体消失。工作区也变了就不带 —— 那些路径
    // 是按旧根目录记的，带过去只会指向意料之外的地方。
    const carry =
      !rootChanged && current.scope.startsWith("draft:") && Boolean(scope) && !scope.startsWith("draft:") ? current : null;
    if (carry && Object.keys(current.drafts).length) {
      draftBuckets.set(scope, current.drafts);
      draftBuckets.delete(current.scope);
    }
    const restored = carry ?? (root.trim() && scope ? loadPanel(root, scope) : null);
    // 只接回这个会话还在乎的那些：别的会话的标签页对应的编辑器已经卸载了。
    const nextDrafts = pickDrafts(draftBuckets.get(scope), restored);

    if (!restored) {
      set({
        root,
        scope,
        tabs: [],
        pins: { ...CLOSED_PINS },
        active: START_TAB,
        dirty: {},
        drafts: nextDrafts,
        bottom: emptySplit(),
        sideFile: null,
        embeddedFile: null,
        changePreview: null,
        ...(rootChanged ? { gitCount: 0, taskCount: 0 } : {}),
      });
      return;
    }

    const tabs = toPanelTabs(restored.tabs);
    const ids = new Set(tabs.map((tab) => tab.id));
    const bottom = restored.bottom ? pruneBottom(restored.bottom, ids) : emptySplit();
    const pinned = (id: string) =>
      (id === TREE_TAB && restored.pins.files) ||
      (id === CHANGES_TAB && restored.pins.changes) ||
      (id === TASKS_TAB && restored.pins.tasks);
    const active = restored.active && (ids.has(restored.active) || pinned(restored.active))
      ? restored.active
      : fallbackActive({ pins: restored.pins, tabs });
    set({
      root,
      scope,
      tabs,
      pins: { ...restored.pins },
      active,
      lens: restored.lens,
      bottom: bottom ?? emptySplit(),
      sideFile: restored.sideFile,
      embeddedFile: restored.embeddedFile,
      changePreview: null,
      dirty: Object.fromEntries(Object.keys(nextDrafts).map((id) => [id, true])),
      drafts: nextDrafts,
    });
  },
  forgetScope: (scope) => {
    draftBuckets.delete(scope);
    dropPanel(get().root, scope);
  },
  activate: (id) => set({ active: id }),
  showHome: () => set({ active: START_TAB }),
  openPin: (pin) =>
    set((state) => ({
      pins: { ...state.pins, [pin]: true },
      active: pin === "files" ? TREE_TAB : pin === "changes" ? CHANGES_TAB : TASKS_TAB,
      revealRequest: state.revealRequest + 1,
    })),
  closePin: (pin) =>
    set((state) => {
      const pins = { ...state.pins, [pin]: false };
      const id = pin === "files" ? TREE_TAB : pin === "changes" ? CHANGES_TAB : TASKS_TAB;
      if (state.active !== id) return { pins };
      return { pins, active: fallbackActive({ ...state, pins }, id) };
    }),
  showChanges: (lens) =>
    set((state) => ({
      pins: { ...state.pins, changes: true },
      active: CHANGES_TAB,
      lens: lens ?? state.lens,
      revealRequest: state.revealRequest + 1,
    })),
  setLens: (lens) => set({ lens }),
  openFile: (path, title) => {
    const id = fileTabId(path);
    set((state) => ({
      tabs: state.tabs.some((tab) => tab.id === id)
        ? state.tabs
        : [...state.tabs, { id, kind: "file", path, title: title || baseName(path) }],
      active: id,
      revealRequest: state.revealRequest + 1,
    }));
  },
  openDiff: (target, title) => {
    const id = diffTabId(target);
    set((state) => {
      const existing = state.tabs.find((tab) => tab.id === id);
      const tab: PanelTab = { id, kind: "diff", title, target };
      return {
        tabs: existing ? state.tabs.map((item) => (item.id === id ? tab : item)) : [...state.tabs, tab],
        active: id,
        revealRequest: state.revealRequest + 1,
      };
    });
  },
  retarget: (id, target, title) =>
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id && tab.kind === "diff" ? { ...tab, target, title } : tab)),
    })),
  close: (id) => {
    set((state) => {
      const index = state.tabs.findIndex((tab) => tab.id === id);
      if (index < 0) return {};
      const tabs = state.tabs.filter((tab) => tab.id !== id);
      const dirty = { ...state.dirty };
      delete dirty[id];
      const drafts = { ...state.drafts };
      delete drafts[id];
      if (state.active !== id) return { tabs, dirty, drafts };
      const neighbor = tabs[Math.min(index, tabs.length - 1)];
      return { tabs, dirty, drafts, active: neighbor?.id ?? fallbackActive({ ...state, tabs }) };
    });
    set({ bottom: collapse(removeTab(get().bottom, id)) });
  },
  setDirty: (id, dirty) =>
    set((state) => (Boolean(state.dirty[id]) === dirty ? {} : { dirty: { ...state.dirty, [id]: dirty } })),
  setDraftText: (id, text) =>
    set((state) => {
      const next = { ...state.drafts };
      if (text === null) {
        if (!(id in next)) return {};
        delete next[id];
      } else {
        if (next[id] === text) return {};
        next[id] = text;
      }
      return { drafts: next };
    }),
  draftFor: (id) => get().drafts[id] ?? null,
  bumpRevision: () => set((state) => ({ revision: state.revision + 1 })),
  setGitCount: (count) => set((state) => (state.gitCount === count ? {} : { gitCount: count })),
  setTaskCount: (count) => set((state) => (state.taskCount === count ? {} : { taskCount: count })),
  showTasks: () =>
    set((state) => ({
      pins: { ...state.pins, tasks: true },
      active: TASKS_TAB,
      revealRequest: state.revealRequest + 1,
    })),
  openBeside: (path, title) =>
    set((state) => ({
      pins: { ...state.pins, files: true },
      sideFile: { path, title: title || baseName(path) },
      active: TREE_TAB,
      revealRequest: state.revealRequest + 1,
    })),
  clearSide: () => set({ sideFile: null }),
  openBrowser: (url) => {
    const id = `browser:${crypto.randomUUID()}`;
    const tab: PanelTab = { id, kind: "browser", url: url?.trim() ?? "", title: "浏览器" };
    set((state) => ({
      tabs: [...state.tabs, tab],
      active: id,
      revealRequest: state.revealRequest + 1,
    }));
  },
  patchBrowser: (id, patch) =>
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id && tab.kind === "browser" ? { ...tab, ...patch } : tab)),
    })),
  addTerminal: (tab) => {
    set((state) => ({
      tabs: [
        ...state.tabs,
        {
          id: tab.id,
          kind: "terminal",
          serverId: tab.serverId,
          title: tab.title,
          shell: tab.shell,
          cwd: tab.cwd,
        },
      ],
      active: tab.id,
      revealRequest: state.revealRequest + 1,
    }));
  },
  patchTerminal: (id, patch) => {
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id && tab.kind === "terminal" ? { ...tab, ...patch, id: tab.id } : tab)),
    }));
  },
  openSideChat: (input, focus = true) => {
    const id = `side:${input.productId}`;
    set((state) => {
      const exists = state.tabs.some((tab) => tab.id === id);
      const tabs = exists
        ? state.tabs
        : [...state.tabs, { id, kind: "sidechat" as const, ...input }];
      if (!focus) return exists ? {} : { tabs };
      return { tabs, active: id, revealRequest: state.revealRequest + 1 };
    });
  },
  dockTab: (tabId, leafId, zone) =>
    set((state) => ({
      bottom: placeTab(state.bottom, tabId, leafId, zone),
      bottomRequest: state.bottomRequest + 1,
      active: state.active === tabId ? fallbackActive(state, tabId) : state.active,
    })),
  focusDocked: (leafId, tabId) => set((state) => ({ bottom: activateInLeaf(state.bottom, leafId, tabId) })),
  resizeDock: (splitId, ratio) => set((state) => ({ bottom: setRatio(state.bottom, splitId, ratio) })),
  setChangePreview: (target, title) => set({ changePreview: { target, title } }),
  clearChangePreview: () => set({ changePreview: null }),
  setPreviewHeight: (height) => {
    const next = Math.min(560, Math.max(120, Math.round(height)));
    try {
      localStorage.setItem(PREVIEW_HEIGHT_KEY, String(next));
    } catch {
      // private mode
    }
    set({ previewHeight: next });
  },
  setEmbeddedFile: (path, title) =>
    set((state) => ({
      pins: { ...state.pins, files: true },
      embeddedFile: { path, title: title || baseName(path) },
      active: TREE_TAB,
    })),
  retainSideChats: (parentId, keep) =>
    set((state) => {
      // 侧边对话本来就归某一个父会话；给了 `keep` 就以服务端还活着的为准，
      // 顺手把已经被删掉的那些标签丢掉，免得恢复出一个点开就没的页。
      const tabs = state.tabs.filter((tab) => {
        if (tab.kind !== "sidechat") return true;
        if (tab.parentId !== parentId) return false;
        return keep ? keep.has(tab.productId) : true;
      });
      if (tabs.length === state.tabs.length) return {};
      const activeGone = !tabs.some((tab) => tab.id === state.active) && state.active.startsWith("side:");
      return { tabs, ...(activeGone ? { active: fallbackActive({ ...state, tabs }) } : {}) };
    }),
  restorePanel: (live) => {
    // 就地刷新终端标签页的标题/尺寸，**不认领**服务端还活着但不属于这个会话的终端 ——
    // 那正是「标签页跟着会话走」这条规则要求的；快照里记着、服务端却没了的终端照样留着，
    // 让 TerminalPane 显示「已结束」并给出显式重建入口。
    set((state) => {
      if (!state.tabs.some((tab) => tab.kind === "terminal")) return {};
      const byServerId = new Map(live.map((item) => [item.id, item]));
      let changed = false;
      const tabs = state.tabs.map((tab) => {
        if (tab.kind !== "terminal") return tab;
        const item = byServerId.get(tab.serverId);
        if (!item) return tab;
        const title = item.title || item.label || tab.title;
        const shell = item.shell || tab.shell;
        const cwd = item.cwd || tab.cwd;
        if (title === tab.title && shell === tab.shell && cwd === tab.cwd) return tab;
        changed = true;
        return { ...tab, title, shell, cwd };
      });
      return changed ? { tabs } : {};
    });
  },
}));

/** 按 root + scope 落一份快照；没绑会话时什么都不做。 */
function persistScope(state: PanelState): void {
  if (!state.root.trim() || !state.scope) return;
  savePanel(state.root, state.scope, {
    tabs: state.tabs,
    pins: state.pins,
    active: state.active,
    lens: state.lens,
    bottom: state.bottom,
    sideFile: state.sideFile,
    embeddedFile: state.embeddedFile,
  });
}

/**
 * 面板一变就存一次，合并成一小段延迟。
 *
 * 之前只有终端相关的几个动作会写盘，翻开文件、拖进底部分栏、切换选中的标签都存不下来，
 * 于是切会话恢复出来的总是一份残缺的面板。这里统一收口：写盘跟着状态走，
 * 不用每个 action 都记得补一句。
 */
let pendingPersist: number | null = null;

function flushPersist(): void {
  if (pendingPersist !== null) {
    window.clearTimeout(pendingPersist);
    pendingPersist = null;
  }
  persistScope(usePanelStore.getState());
}

usePanelStore.subscribe((state, previous) => {
  // 换 root / 换 scope 时由 bindContext 自己写，这一笔跳过，免得把刚清空的面板盖回去。
  if (state.root !== previous.root || state.scope !== previous.scope) return;
  if (
    state.tabs === previous.tabs &&
    state.pins === previous.pins &&
    state.active === previous.active &&
    state.bottom === previous.bottom &&
    state.sideFile === previous.sideFile &&
    state.embeddedFile === previous.embeddedFile &&
    state.lens === previous.lens
  ) {
    return;
  }
  if (pendingPersist !== null) window.clearTimeout(pendingPersist);
  pendingPersist = window.setTimeout(() => {
    pendingPersist = null;
    persistScope(usePanelStore.getState());
  }, 250);
});

if (typeof window !== "undefined") {
  // 关窗口 / 切到后台时把尾巴补上，不然最后 250ms 里的改动会丢。
  window.addEventListener("pagehide", flushPersist);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushPersist();
  });
}
