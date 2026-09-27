import { create } from "zustand";

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
    };

export type PanelTab =
  | { id: string; kind: "file"; path: string; title: string }
  | { id: string; kind: "diff"; title: string; target: DiffTarget };

export type ChangesLens = "git" | "session";

export const TREE_TAB = "tree";
export const CHANGES_TAB = "changes";

type PanelState = {
  root: string;
  tabs: PanelTab[];
  active: string;
  lens: ChangesLens;
  /** 文件树、预览、变更列表的刷新代数；一轮对话结束或保存文件后递增。 */
  revision: number;
  /** 请求 Workbench 展开右侧栏的计数。 */
  revealRequest: number;
  dirty: Record<string, boolean>;
  gitCount: number;
  setRoot: (root: string) => void;
  activate: (id: string) => void;
  showChanges: (lens?: ChangesLens) => void;
  setLens: (lens: ChangesLens) => void;
  openFile: (path: string, title?: string) => void;
  openDiff: (target: DiffTarget, title: string) => void;
  retarget: (id: string, target: DiffTarget, title: string) => void;
  close: (id: string) => void;
  setDirty: (id: string, dirty: boolean) => void;
  bumpRevision: () => void;
  setGitCount: (count: number) => void;
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
  }
}

export const usePanelStore = create<PanelState>((set, get) => ({
  root: "",
  tabs: [],
  active: TREE_TAB,
  lens: "git",
  revision: 0,
  revealRequest: 0,
  dirty: {},
  gitCount: 0,
  setRoot: (root) => {
    if (sameWorkspace(get().root, root) && get().root !== "") return;
    set({ root, tabs: [], active: TREE_TAB, dirty: {}, gitCount: 0 });
  },
  activate: (id) => set({ active: id }),
  showChanges: (lens) =>
    set((state) => ({
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
  close: (id) =>
    set((state) => {
      const index = state.tabs.findIndex((tab) => tab.id === id);
      if (index < 0) return {};
      const tabs = state.tabs.filter((tab) => tab.id !== id);
      const dirty = { ...state.dirty };
      delete dirty[id];
      if (state.active !== id) return { tabs, dirty };
      const neighbor = tabs[Math.min(index, tabs.length - 1)];
      return { tabs, dirty, active: neighbor?.id ?? TREE_TAB };
    }),
  setDirty: (id, dirty) =>
    set((state) => (Boolean(state.dirty[id]) === dirty ? {} : { dirty: { ...state.dirty, [id]: dirty } })),
  bumpRevision: () => set((state) => ({ revision: state.revision + 1 })),
  setGitCount: (count) => set((state) => (state.gitCount === count ? {} : { gitCount: count })),
}));
