import { create } from "zustand";

import type { FileDiff } from "@nono/protocol";

import { activateInLeaf, collapse, emptySplit, placeTab, removeTab, setRatio, type DropZone, type SplitNode } from "@/features/files/split";
import { loadTerminals, saveTerminals, type SavedTerminal } from "@/features/files/terminal-storage";
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

type PanelPins = Record<PanelPin, boolean>;

const CLOSED_PINS: PanelPins = { files: false, changes: false, tasks: false };

type PanelState = {
  root: string;
  tabs: PanelTab[];
  pins: PanelPins;
  active: string;
  lens: ChangesLens;
  /** 文件树、预览、变更列表的刷新代数；一轮对话结束或保存文件后递增。 */
  revision: number;
  /** 请求 Workbench 展开右侧栏的计数。 */
  revealRequest: number;
  /** 请求展开底部工作台的计数。 */
  bottomRequest: number;
  bottom: SplitNode;
  dirty: Record<string, boolean>;
  gitCount: number;
  taskCount: number;
  /** 文件树右侧并排打开的文件；切走「文件」页后仍保留，回到文件页时还在。 */
  sideFile: { path: string; title: string } | null;
  /** 变更页底部的 diff 预览，还没展开成独立标签。 */
  changePreview: { target: DiffTarget; title: string } | null;
  /** 内嵌资源管理器当前预览的文件。 */
  embeddedFile: { path: string; title: string } | null;
  previewHeight: number;
  setRoot: (root: string) => void;
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
  bumpRevision: () => void;
  setGitCount: (count: number) => void;
  setTaskCount: (count: number) => void;
  showTasks: () => void;
  openBeside: (path: string, title?: string) => void;
  clearSide: () => void;
  openBrowser: (url?: string) => void;
  patchBrowser: (id: string, patch: { url?: string; title?: string }) => void;
  addTerminal: (tab: SavedTerminal) => void;
  patchTerminal: (id: string, patch: Partial<SavedTerminal>) => void;
  restoreTerminals: (root: string) => void;
  openSideChat: (input: { productId: string; engineId: string; parentId: string; title: string }, focus?: boolean) => void;
  retainSideChats: (parentId: string) => void;
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

export const usePanelStore = create<PanelState>((set, get) => ({
  root: "",
  tabs: [],
  pins: { ...CLOSED_PINS },
  active: START_TAB,
  lens: "git",
  revision: 0,
  revealRequest: 0,
  bottomRequest: 0,
  bottom: emptySplit(),
  dirty: {},
  gitCount: 0,
  taskCount: 0,
  sideFile: null,
  changePreview: null,
  previewHeight: readPreviewHeight(),
  embeddedFile: null,
  setRoot: (root) => {
    const current = get();
    if (sameWorkspace(current.root, root) && current.root !== "") return;
    saveTerminals(current.root, current.tabs);
    set({
      root,
      tabs: [],
      pins: { ...CLOSED_PINS },
      active: START_TAB,
      dirty: {},
      gitCount: 0,
      taskCount: 0,
      sideFile: null,
      changePreview: null,
      embeddedFile: null,
      bottom: emptySplit(),
    });
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
      if (state.active !== id) return { tabs, dirty };
      const neighbor = tabs[Math.min(index, tabs.length - 1)];
      return { tabs, dirty, active: neighbor?.id ?? fallbackActive({ ...state, tabs }) };
    });
    set({ bottom: collapse(removeTab(get().bottom, id)) });
    saveTerminals(get().root, get().tabs);
  },
  setDirty: (id, dirty) =>
    set((state) => (Boolean(state.dirty[id]) === dirty ? {} : { dirty: { ...state.dirty, [id]: dirty } })),
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
    saveTerminals(get().root, get().tabs);
  },
  patchTerminal: (id, patch) => {
    set((state) => ({
      tabs: state.tabs.map((tab) => (tab.id === id && tab.kind === "terminal" ? { ...tab, ...patch, id: tab.id } : tab)),
    }));
    saveTerminals(get().root, get().tabs);
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
  retainSideChats: (parentId) =>
    set((state) => {
      const tabs = state.tabs.filter((tab) => tab.kind !== "sidechat" || tab.parentId === parentId);
      if (tabs.length === state.tabs.length) return {};
      const activeGone = !tabs.some((tab) => tab.id === state.active) && state.active.startsWith("side:");
      return { tabs, ...(activeGone ? { active: fallbackActive({ ...state, tabs }) } : {}) };
    }),
  restoreTerminals: (root) => {
    if (!sameWorkspace(get().root, root)) return;
    const saved = loadTerminals(root);
    if (!saved.length) return;
    set((state) => {
      const existing = new Set(state.tabs.filter((tab) => tab.kind === "terminal").map((tab) => tab.id));
      const added = saved.filter((item) => !existing.has(item.id));
      if (!added.length) return {};
      return {
        tabs: [
          ...state.tabs,
          ...added.map((item) => ({
            id: item.id,
            kind: "terminal" as const,
            serverId: item.serverId,
            title: item.title,
            shell: item.shell,
            cwd: item.cwd,
          })),
        ],
      };
    });
  },
}));
