import { Archive, ArchiveRestore, ArrowUpDown, Check, ChevronRight, Clock, Eye, EyeOff, Folder, FolderOpen, FolderPlus, FolderTree, GitFork, List, Loader2, MessageSquarePlus, MoreHorizontal, PanelLeftClose, Pencil, Settings, Settings2, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { SessionListItem, Workspace } from "@nono/protocol";

import { openWorkspaceFolder } from "@/api/client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { formatRelativeTime, sessionTitle } from "@/lib/format";
import { sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { sessionRun, useChatStore } from "@/store/chat";
import { blankDrag, useSidebarReorder, type RowDrag } from "@/features/shell/sidebar-drag";
import {
  buildWorkspaceTree,
  DEFAULT_VISIBLE_SESSIONS,
  loadExpandedSessionGroups,
  loadListView,
  loadManualOrder,
  loadWorkspaceOrder,
  orderWorkspaces,
  pruneEmptyArchiveTree,
  reorderManualTo,
  saveExpandedSessionGroups,
  saveListView,
  saveManualOrder,
  saveWorkspaceOrder,
  sortSessions,
  visibleSessionWindow,
  type ArchiveFilter,
  type DropSide,
  type GroupMode,
  type SortMode,
  type WorkspaceTreeNode,
} from "@/features/shell/sidebar-list";

const COLLAPSED_KEY = "nono.sidebar.collapsedWorkspaces";
const UNGROUPED_ID = "__ungrouped__";

function loadCollapsed(): Set<string> {
  try {
    const raw = localStorage.getItem(COLLAPSED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((item): item is string => typeof item === "string"));
  } catch {
    return new Set();
  }
}

function saveCollapsed(ids: Set<string>) {
  try {
    localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...ids]));
  } catch {
    // private mode / blocked storage
  }
}

function ListViewMenu({
  groupMode,
  sortMode,
  archiveFilter,
  onGroup,
  onSort,
  onArchive,
}: {
  groupMode: GroupMode;
  sortMode: SortMode;
  archiveFilter: ArchiveFilter;
  onGroup: (mode: GroupMode) => void;
  onSort: (mode: SortMode) => void;
  onArchive: (filter: ArchiveFilter) => void;
}) {
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const tuned = groupMode !== "workspace" || sortMode !== "recent" || archiveFilter !== "hide";

  useEffect(() => {
    if (!open) return;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      const width = 248;
      setPos({
        top: rect.bottom + 4,
        left: Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8),
      });
    }
    function onPointer(event: PointerEvent) {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function pickGroup(mode: GroupMode) {
    onGroup(mode);
    setOpen(false);
  }

  function pickSort(mode: SortMode) {
    onSort(mode);
    setOpen(false);
  }

  function pickArchive(filter: ArchiveFilter) {
    onArchive(filter);
    setOpen(false);
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className={`flex size-6 items-center justify-center rounded-md hover:bg-hover hover:text-ink ${
          tuned ? "text-accent" : "text-faint"
        }`}
        title="工作区选项"
        aria-label="工作区选项"
        aria-expanded={open}
        aria-haspopup="menu"
        onClick={() => setOpen((value) => !value)}
      >
        <Settings2 className="size-3.5" />
      </button>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              role="menu"
              className="fixed z-50 w-[248px] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_var(--shadow-color)]"
              style={{ top: pos.top, left: pos.left }}
            >
              <p className="px-2.5 pb-1 pt-1.5 text-[11px] text-faint">分组方式</p>
              <MenuChoice
                icon={<Folder className="size-3.5 text-muted" />}
                label="按工作区"
                selected={groupMode === "workspace"}
                onClick={() => pickGroup("workspace")}
              />
              <MenuChoice
                icon={<FolderTree className="size-3.5 text-muted" />}
                label="按工作区树"
                selected={groupMode === "tree"}
                onClick={() => pickGroup("tree")}
              />
              <MenuChoice
                icon={<List className="size-3.5 text-muted" />}
                label="单列表"
                selected={groupMode === "flat"}
                onClick={() => pickGroup("flat")}
              />
              <div className="my-1 border-t border-line" />
              <p className="px-2.5 pb-1 pt-1 text-[11px] text-faint">排序方式</p>
              <MenuChoice
                icon={<ArrowUpDown className="size-3.5 text-muted" />}
                label="手动排序"
                selected={sortMode === "manual"}
                onClick={() => pickSort("manual")}
              />
              <MenuChoice
                icon={<Clock className="size-3.5 text-muted" />}
                label="最近更新"
                selected={sortMode === "recent"}
                onClick={() => pickSort("recent")}
              />
              <div className="my-1 border-t border-line" />
              <p className="px-2.5 pb-1 pt-1 text-[11px] text-faint">筛选会话</p>
              <MenuChoice
                icon={<EyeOff className="size-3.5 text-muted" />}
                label="隐藏已归档"
                selected={archiveFilter === "hide"}
                onClick={() => pickArchive("hide")}
              />
              <MenuChoice
                icon={<Eye className="size-3.5 text-muted" />}
                label="全部对话（显示已归档）"
                selected={archiveFilter === "all"}
                onClick={() => pickArchive("all")}
              />
              <MenuChoice
                icon={<Archive className="size-3.5 text-muted" />}
                label="仅显示已归档"
                selected={archiveFilter === "only"}
                onClick={() => pickArchive("only")}
              />
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

function MenuChoice({
  icon,
  label,
  selected,
  onClick,
}: {
  icon: ReactNode;
  label: string;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitemradio"
      aria-checked={selected}
      className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
      onClick={onClick}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {selected ? <Check className="size-3.5 shrink-0 text-ink" /> : null}
    </button>
  );
}

function WorkspaceRowMenu({
  workspace,
  onRename,
}: {
  workspace: { id: string; path: string; name: string };
  onRename: () => void;
}) {
  const removeWorkspace = useChatStore((state) => state.removeWorkspace);
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const label = workspace.name || workspaceLabel(workspace.path);

  useEffect(() => {
    if (!open) return;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      const width = 168;
      setPos({
        top: rect.bottom + 4,
        left: Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8),
      });
    }
    function onPointer(event: PointerEvent) {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  function rename() {
    setOpen(false);
    onRename();
  }

  async function openFolder() {
    setOpen(false);
    try {
      await openWorkspaceFolder(workspace.path);
    } catch (error) {
      console.error(error);
    }
  }

  function requestRemove() {
    setOpen(false);
    setConfirming(true);
  }

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="flex size-6 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
        title="工作区选项"
        onClick={(event) => {
          event.stopPropagation();
          setOpen((value) => !value);
        }}
      >
        <MoreHorizontal className="size-3.5" />
      </button>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              className="fixed z-50 w-[168px] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_var(--shadow-color)]"
              style={{ top: pos.top, left: pos.left }}
            >
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
                onClick={rename}
              >
                <Pencil className="size-3.5 text-muted" />
                重命名
              </button>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
                onClick={() => void openFolder()}
              >
                <FolderOpen className="size-3.5 text-muted" />
                打开文件夹
              </button>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-danger hover:bg-danger/10"
                onClick={requestRemove}
              >
                <Trash2 className="size-3.5" />
                删除工作区
              </button>
            </div>,
            document.body,
          )
        : null}
      <ConfirmDialog
        open={confirming}
        title="删除工作区"
        description={`删除「${label}」后，其中的会话会移到未分组。磁盘上的文件夹不会被删除。`}
        confirmLabel="删除工作区"
        danger
        onConfirm={() => {
          setConfirming(false);
          void removeWorkspace(workspace.id);
        }}
        onCancel={() => setConfirming(false)}
      />
    </>
  );
}

function SessionRowMenu({
  sessionId,
  archived,
  onRename,
  onOpenChange,
}: {
  sessionId: string;
  archived: boolean;
  onRename: () => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const forkSession = useChatStore((state) => state.forkSession);
  const archiveSession = useChatStore((state) => state.archiveSession);
  const unarchiveSession = useChatStore((state) => state.unarchiveSession);
  const deleteSession = useChatStore((state) => state.deleteSession);
  const [open, setOpen] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  function toggle(next: boolean) {
    setOpen(next);
    onOpenChange?.(next);
  }

  useEffect(() => {
    if (!open) return;
    const rect = buttonRef.current?.getBoundingClientRect();
    if (rect) {
      const width = 168;
      setPos({
        top: rect.bottom + 4,
        left: Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8),
      });
    }
    function onPointer(event: PointerEvent) {
      const target = event.target as Node;
      if (buttonRef.current?.contains(target) || menuRef.current?.contains(target)) return;
      toggle(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") toggle(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <>
      <button
        ref={buttonRef}
        type="button"
        className="flex size-6 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
        title="会话选项"
        onClick={(event) => {
          event.stopPropagation();
          toggle(!open);
        }}
      >
        <MoreHorizontal className="size-3.5" />
      </button>
      {open
        ? createPortal(
            <div
              ref={menuRef}
              className="fixed z-50 w-[168px] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_var(--shadow-color)]"
              style={{ top: pos.top, left: pos.left }}
            >
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
                onClick={() => {
                  toggle(false);
                  onRename();
                }}
              >
                <Pencil className="size-3.5 text-muted" />
                重命名
              </button>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
                onClick={() => {
                  toggle(false);
                  void forkSession(sessionId);
                }}
              >
                <GitFork className="size-3.5 text-muted" />
                叉分
              </button>
              {archived ? (
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
                  onClick={() => {
                    toggle(false);
                    void unarchiveSession(sessionId);
                  }}
                >
                  <ArchiveRestore className="size-3.5 text-muted" />
                  取消归档
                </button>
              ) : (
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover hover:text-ink"
                  onClick={() => {
                    toggle(false);
                    void archiveSession(sessionId);
                  }}
                >
                  <Archive className="size-3.5 text-muted" />
                  归档
                </button>
              )}
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-danger hover:bg-danger/10"
                onClick={() => {
                  setOpen(false);
                  setConfirming(true);
                  onOpenChange?.(true);
                }}
              >
                <Trash2 className="size-3.5" />
                删除
              </button>
            </div>,
            document.body,
          )
        : null}
      <ConfirmDialog
        open={confirming}
        title="删除会话"
        description="删除会话后无法恢复。"
        confirmLabel="删除"
        danger
        onConfirm={() => {
          setConfirming(false);
          onOpenChange?.(false);
          void deleteSession(sessionId);
        }}
        onCancel={() => {
          setConfirming(false);
          onOpenChange?.(false);
        }}
      />
    </>
  );
}

function SessionRow({
  session,
  drag,
}: {
  session: SessionListItem;
  drag: RowDrag;
}) {
  const sessionId = useChatStore((state) => state.sessionId);
  const sessionTitles = useChatStore((state) => state.sessionTitles);
  const openSession = useChatStore((state) => state.openSession);
  const renameSession = useChatStore((state) => state.renameSession);
  // 每条会话读自己的运行态：后台会话在跑也会转圈。
  const running = useChatStore(
    (state) => sessionRun(state, session.engine_session_id).streaming,
  );
  const waiting = useChatStore((state) =>
    state.permissions.some((item) => item.session_id === session.engine_session_id),
  );
  const selected = session.engine_session_id === sessionId;
  const title = sessionTitle(session.engine_session_id, sessionTitles, session.title);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(title);
  const [menuOpen, setMenuOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const skipCommit = useRef(false);
  const archived = session.archived === true;

  useEffect(() => {
    if (!renaming) setDraft(title);
  }, [renaming, title]);

  useEffect(() => {
    if (!renaming) return;
    const node = inputRef.current;
    if (!node) return;
    node.focus();
    node.select();
  }, [renaming]);

  async function commitRename() {
    if (skipCommit.current) {
      skipCommit.current = false;
      setRenaming(false);
      return;
    }
    const name = draft.trim();
    setRenaming(false);
    if (!name || name === title) return;
    await renameSession(session.id, name);
  }

  return (
    <li
      ref={drag.ref}
      className={`group transition-opacity duration-150 ${drag.dragging ? "opacity-40" : ""}`}
      onDragOver={drag.onDragOver}
      onDrop={drag.onDrop}
    >
      <div
        className={`flex items-center rounded-lg ${
          selected ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
        }`}
      >
        {renaming ? (
          <input
            ref={inputRef}
            value={draft}
            className="min-w-0 flex-1 rounded-md bg-bg px-2 py-1 text-[13px] text-ink outline-none ring-1 ring-line-strong"
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => void commitRename()}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void commitRename();
              }
              if (event.key === "Escape") {
                event.preventDefault();
                skipCommit.current = true;
                setDraft(title);
                setRenaming(false);
              }
            }}
            onClick={(event) => event.stopPropagation()}
          />
        ) : (
          <button
            type="button"
            draggable={drag.draggable}
            onDragStart={drag.onDragStart}
            onDragEnd={drag.onDragEnd}
            title={archived ? "已归档" : drag.draggable ? "拖动调整顺序" : undefined}
            className={`flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1.5 text-left text-[13px] ${
              drag.draggable ? "cursor-grab active:cursor-grabbing" : ""
            }`}
            onClick={() => void openSession(session.engine_session_id)}
          >
            {archived ? <Archive className="size-3 shrink-0 text-faint" /> : null}
            <span className={`truncate ${archived ? "text-muted" : ""}`}>{title}</span>
          </button>
        )}
        <div className="flex shrink-0 items-center pr-0.5">
          {waiting ? (
            <span className="flex size-6 items-center justify-center" title="等待你确认授权">
              <ShieldAlert className="size-3.5 text-danger" />
            </span>
          ) : running ? (
            <span className="flex size-6 items-center justify-center" title="生成中">
              <Loader2 className="size-3.5 animate-spin text-accent" />
            </span>
          ) : (
            <>
              <span className={`px-1 text-[11px] text-faint ${menuOpen ? "hidden" : "group-hover:hidden"}`}>
                {formatRelativeTime(session.updated_at)}
              </span>
              <span className={menuOpen ? "flex" : "hidden group-hover:flex"}>
                <SessionRowMenu
                  sessionId={session.id}
                  archived={archived}
                  onRename={() => {
                    setDraft(title);
                    setRenaming(true);
                  }}
                  onOpenChange={setMenuOpen}
                />
              </span>
            </>
          )}
        </div>
      </div>
    </li>
  );
}

/** 折叠区里那两枚「展开其余 N 个会话 / 收起」的按钮长得一样，样式放一处。 */
const FOLD_ROW =
  "flex w-full items-center rounded-lg px-2 py-1.5 text-left text-[13px] text-muted hover:bg-hover hover:text-ink";

/**
 * 一个分组下面的会话列表：默认只露前几条，多出来的折叠起来。
 *
 * 「新会话」草稿行由调用方通过 header 传进来，它不算在默认名额里。
 */
function SessionGroupList({
  groupId,
  sessions,
  activeSessionId,
  expandedGroups,
  onToggleGroup,
  dragSessions,
  header,
}: {
  groupId: string;
  sessions: SessionListItem[];
  activeSessionId: string | null;
  /** 哪些分组被手动展开过，默认都是折叠的。 */
  expandedGroups: Set<string>;
  onToggleGroup: (id: string) => void;
  dragSessions: (session: SessionListItem) => RowDrag;
  header?: ReactNode;
}) {
  const expanded = expandedGroups.has(groupId);
  const { shown, hidden } = visibleSessionWindow(sessions, DEFAULT_VISIBLE_SESSIONS, {
    expanded,
    activeSessionId,
  });
  return (
    <ul className="ml-3 border-l border-line pl-2">
      {header}
      {shown.map((session) => (
        <SessionRow key={session.id} session={session} drag={dragSessions(session)} />
      ))}
      {hidden ? (
        <li>
          <button
            type="button"
            className={FOLD_ROW}
            onClick={() => onToggleGroup(groupId)}
          >
            展开其余 {hidden} 个会话
          </button>
        </li>
      ) : null}
      {expanded && sessions.length > DEFAULT_VISIBLE_SESSIONS ? (
        <li>
          <button type="button" className={FOLD_ROW} onClick={() => onToggleGroup(groupId)}>
            收起
          </button>
        </li>
      ) : null}
    </ul>
  );
}

function WorkspaceRow({
  workspace,
  sessions,
  active,
  collapsed,
  onToggle,
  onExpand,
  drag,
  dragSessions,
  nested,
  expandedGroups,
  onToggleSessions,
}: {
  workspace: Workspace;
  sessions: SessionListItem[];
  active: boolean;
  collapsed: boolean;
  onToggle: () => void;
  onExpand: () => void;
  drag: RowDrag;
  /** 工作区里那排会话自己的拖拽句柄，和工作区这一行互不干扰。 */
  dragSessions: (session: SessionListItem) => RowDrag;
  nested?: ReactNode;
  expandedGroups: Set<string>;
  onToggleSessions: (id: string) => void;
}) {
  const newSession = useChatStore((state) => state.newSession);
  const renameWorkspace = useChatStore((state) => state.renameWorkspace);
  const sessionId = useChatStore((state) => state.sessionId);
  const draftWorkspaceId = useChatStore((state) => state.draftWorkspaceId);
  const showDraft = draftWorkspaceId === workspace.id;
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(workspace.name || workspaceLabel(workspace.path));
  const inputRef = useRef<HTMLInputElement>(null);
  const skipCommit = useRef(false);
  const expanded = !collapsed || showDraft;

  useEffect(() => {
    if (!renaming) {
      setDraft(workspace.name || workspaceLabel(workspace.path));
    }
  }, [renaming, workspace.name, workspace.path]);

  useEffect(() => {
    if (!renaming) return;
    const node = inputRef.current;
    if (!node) return;
    node.focus();
    node.select();
  }, [renaming]);

  function startRename() {
    setDraft(workspace.name || workspaceLabel(workspace.path));
    setRenaming(true);
  }

  async function commitRename() {
    if (skipCommit.current) {
      skipCommit.current = false;
      setRenaming(false);
      return;
    }
    const name = draft.trim();
    setRenaming(false);
    if (!name || name === (workspace.name || workspaceLabel(workspace.path))) return;
    await renameWorkspace(workspace.id, name);
  }

  return (
    <div
      ref={drag.ref}
      className={`mb-1 transition-opacity duration-150 ${drag.dragging ? "opacity-40" : ""}`}
      onDragOver={drag.onDragOver}
      onDrop={drag.onDrop}
    >
      <div
        className={`flex items-center rounded-lg pr-0.5 ${
          active && !sessionId && !showDraft ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
        }`}
      >
        {renaming ? (
          <div className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-0.5">
            <Folder className="size-3.5 shrink-0 text-muted" />
            <input
              ref={inputRef}
              value={draft}
              className="min-w-0 flex-1 rounded-md bg-bg px-1.5 py-1 text-[13px] text-ink outline-none ring-1 ring-line-strong"
              onChange={(event) => setDraft(event.target.value)}
              onBlur={() => void commitRename()}
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  void commitRename();
                }
                if (event.key === "Escape") {
                  event.preventDefault();
                  skipCommit.current = true;
                  setDraft(workspace.name || workspaceLabel(workspace.path));
                  setRenaming(false);
                }
              }}
              onClick={(event) => event.stopPropagation()}
            />
          </div>
        ) : (
          <button
            type="button"
            draggable={drag.draggable}
            onDragStart={drag.onDragStart}
            onDragEnd={drag.onDragEnd}
            className={`flex min-w-0 flex-1 items-center gap-1 px-2 py-1.5 text-left text-[13px] ${
              drag.draggable ? "cursor-grab active:cursor-grabbing" : ""
            }`}
            title={drag.draggable ? "拖动调整顺序" : expanded ? "折起分组" : "展开分组"}
            aria-expanded={expanded}
            onClick={onToggle}
          >
            <ChevronRight
              className={`size-3 shrink-0 text-faint transition-transform ${expanded ? "rotate-90" : ""}`}
            />
            {expanded ? (
              <FolderOpen className="size-3.5 shrink-0 text-muted" />
            ) : (
              <Folder className="size-3.5 shrink-0 text-muted" />
            )}
            <span className="truncate">{workspace.name || workspaceLabel(workspace.path)}</span>
          </button>
        )}
        <WorkspaceRowMenu workspace={workspace} onRename={startRename} />
        <button
          type="button"
          className="flex size-6 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
          title="新会话"
          onClick={(event) => {
            event.stopPropagation();
            onExpand();
            void newSession({ cwd: workspace.path });
          }}
        >
          <MessageSquarePlus className="size-3.5" />
        </button>
      </div>
      {expanded ? (
        <SessionGroupList
          groupId={workspace.id}
          sessions={sessions}
          activeSessionId={sessionId}
          expandedGroups={expandedGroups}
          onToggleGroup={onToggleSessions}
          dragSessions={dragSessions}
          header={
            showDraft ? (
              <li>
                <div className="flex w-full items-center justify-between gap-2 rounded-lg bg-active px-2 py-1.5 text-left text-[13px] text-ink">
                  <span className="truncate">新会话</span>
                  <span className="shrink-0 text-[11px] text-faint">刚刚</span>
                </div>
              </li>
            ) : null
          }
        />
      ) : null}
      {expanded && nested ? <div className="ml-3 border-l border-line pl-2">{nested}</div> : null}
    </div>
  );
}

function DirGroup({
  node,
  collapsed,
  onToggle,
  children,
}: {
  node: Extract<WorkspaceTreeNode, { kind: "dir" }>;
  collapsed: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  const expanded = !collapsed;
  return (
    <div className="mb-1">
      <button
        type="button"
        className="flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover"
        title={expanded ? "折起分组" : "展开分组"}
        aria-expanded={expanded}
        onClick={onToggle}
      >
        <ChevronRight className={`size-3 shrink-0 text-faint transition-transform ${expanded ? "rotate-90" : ""}`} />
        {expanded ? (
          <FolderOpen className="size-3.5 shrink-0 text-muted" />
        ) : (
          <Folder className="size-3.5 shrink-0 text-muted" />
        )}
        <span className="truncate">{node.label}</span>
      </button>
      {expanded ? <div className="ml-3 border-l border-line pl-2">{children}</div> : null}
    </div>
  );
}

function TreeBranch({
  nodes,
  sessionsById,
  workspacePath,
  collapsed,
  onToggle,
  onExpand,
  manualSort,
  dragSessions,
  expandedGroups,
  onToggleSessions,
}: {
  nodes: WorkspaceTreeNode[];
  sessionsById: Map<string, SessionListItem[]>;
  workspacePath: string;
  collapsed: Set<string>;
  onToggle: (id: string) => void;
  onExpand: (id: string) => void;
  manualSort: boolean;
  dragSessions: (session: SessionListItem) => RowDrag;
  expandedGroups: Set<string>;
  onToggleSessions: (id: string) => void;
}) {
  // 树形分组里工作区的位置由目录结构决定，交给用户拖会跟树打架，所以只放开会话。
  return (
    <>
      {nodes.map((node) =>
        node.kind === "dir" ? (
          <DirGroup
            key={node.id}
            node={node}
            collapsed={collapsed.has(node.id)}
            onToggle={() => onToggle(node.id)}
          >
            <TreeBranch
              nodes={node.children}
              sessionsById={sessionsById}
              workspacePath={workspacePath}
              collapsed={collapsed}
              onToggle={onToggle}
              onExpand={onExpand}
              manualSort={manualSort}
              dragSessions={dragSessions}
              expandedGroups={expandedGroups}
              onToggleSessions={onToggleSessions}
            />
          </DirGroup>
        ) : (
          <WorkspaceRow
            key={node.workspace.id}
            workspace={node.workspace}
            sessions={sessionsById.get(node.workspace.id) ?? []}
            active={sameWorkspace(node.workspace.path, workspacePath)}
            collapsed={collapsed.has(node.workspace.id)}
            onToggle={() => onToggle(node.workspace.id)}
            onExpand={() => onExpand(node.workspace.id)}
            drag={blankDrag}
            dragSessions={dragSessions}
            expandedGroups={expandedGroups}
            onToggleSessions={onToggleSessions}
            nested={
              node.children.length ? (
                <TreeBranch
                  nodes={node.children}
                  sessionsById={sessionsById}
                  workspacePath={workspacePath}
                  collapsed={collapsed}
                  onToggle={onToggle}
                  onExpand={onExpand}
                  manualSort={manualSort}
                  dragSessions={dragSessions}
                  expandedGroups={expandedGroups}
                  onToggleSessions={onToggleSessions}
                />
              ) : null
            }
          />
        ),
      )}
    </>
  );
}

function RailButton({
  title,
  onClick,
  children,
}: {
  title: string;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      className="flex size-9 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-ink"
      title={title}
      aria-label={title}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function Sidebar({
  expanded,
  onExpand,
  onCollapse,
}: {
  expanded: boolean;
  onExpand: () => void;
  onCollapse: () => void;
}) {
  const sessions = useChatStore((state) => state.sessions);
  const archiveFilter = useChatStore((state) => state.sessionArchiveFilter);
  const setSessionArchiveFilter = useChatStore((state) => state.setSessionArchiveFilter);
  const healthOk = useChatStore((state) => state.healthOk);
  const sessionId = useChatStore((state) => state.sessionId);
  const workspace = useChatStore((state) => state.workspace);
  const workspaces = useChatStore((state) => state.workspaces);
  const setSettingsOpen = useChatStore((state) => state.setSettingsOpen);
  const newSession = useChatStore((state) => state.newSession);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(loadExpandedSessionGroups);
  const [listView, setListView] = useState(loadListView);
  const [manualOrder, setManualOrder] = useState<string[]>(loadManualOrder);
  const [workspaceOrder, setWorkspaceOrder] = useState<string[]>(loadWorkspaceOrder);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const manualSort = listView.sort === "manual";

  const groups = useMemo(() => {
    const ordered = sortSessions(sessions, listView.sort, manualOrder);
    const byId = new Map<string, typeof sessions>();
    for (const item of workspaces) {
      byId.set(item.id, []);
    }
    const unbound: typeof sessions = [];
    for (const session of ordered) {
      if (byId.has(session.workspace_id)) {
        byId.get(session.workspace_id)?.push(session);
      } else {
        unbound.push(session);
      }
    }
    return { byId, unbound, ordered };
  }, [sessions, workspaces, listView.sort, manualOrder]);

  const treeNodes = useMemo(() => {
    const tree = buildWorkspaceTree(workspaces);
    if (archiveFilter !== "only") return tree;
    return pruneEmptyArchiveTree(tree, groups.byId);
  }, [workspaces, archiveFilter, groups.byId]);

  const visibleWorkspaces = useMemo(() => {
    const listed =
      archiveFilter === "only"
        ? workspaces.filter((item) => (groups.byId.get(item.id) ?? []).length > 0)
        : workspaces;
    return orderWorkspaces(listed, workspaceOrder);
  }, [workspaces, workspaceOrder, archiveFilter, groups.byId]);

  function toggleCollapsed(id: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      saveCollapsed(next);
      return next;
    });
  }

  function expandGroup(id: string) {
    setCollapsed((current) => {
      if (!current.has(id)) return current;
      const next = new Set(current);
      next.delete(id);
      saveCollapsed(next);
      return next;
    });
  }

  const ungroupedOpen = !collapsed.has(UNGROUPED_ID);

  /** 分组里默认只露前几条会话，这枚开关把折叠的那部分摊开或者收回去。 */
  function toggleSessionGroup(id: string) {
    setExpandedGroups((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      saveExpandedSessionGroups(next);
      return next;
    });
  }

  function chooseGroup(mode: GroupMode) {
    setListView((current) => {
      const next = { ...current, group: mode };
      saveListView(next);
      return next;
    });
  }

  function chooseSort(mode: SortMode) {
    setListView((current) => {
      const next = { ...current, sort: mode };
      saveListView(next);
      return next;
    });
  }

  function reorderSession(sourceId: string, targetId: string, side: DropSide) {
    setManualOrder((current) => {
      const next = reorderManualTo(
        current,
        sortSessions(sessions, "recent", []).map((item) => item.id),
        sourceId,
        targetId,
        side,
      );
      saveManualOrder(next);
      return next;
    });
  }

  function reorderWorkspace(sourceId: string, targetId: string, side: DropSide) {
    setWorkspaceOrder((current) => {
      const next = reorderManualTo(
        current,
        workspaces.map((item) => item.id),
        sourceId,
        targetId,
        side,
      );
      saveWorkspaceOrder(next);
      return next;
    });
  }

  const { bind, scrollerProps, line } = useSidebarReorder({
    scrollerRef,
    onCommit: (source, target) => {
      if (source.kind === "session") reorderSession(source.id, target.id, target.side);
      else reorderWorkspace(source.id, target.id, target.side);
    },
  });

  const dragSession = (session: SessionListItem) => bind("session", session.id, manualSort);
  const dragWorkspace = (item: Workspace) => bind("workspace", item.id, manualSort);

  function startNewSession() {
    const listed = workspaces.some((item) => sameWorkspace(item.path, workspace));
    if (!workspace.trim() || !listed) {
      setPickerOpen(true);
      return;
    }
    void newSession();
  }

  if (!expanded) {
    return (
      <aside className="sidebar flex h-full w-full flex-col items-center border-r border-line bg-sidebar">
        <div className="flex h-12 w-full shrink-0 items-center justify-center border-b border-line">
          <button
            type="button"
            className="rounded-[10px] hover:opacity-80"
            title="展开菜单"
            aria-label="展开菜单"
            onClick={onExpand}
          >
            <img src="/logo.png" alt="" draggable={false} className="size-8 rounded-[10px]" />
          </button>
        </div>
        <div className="flex flex-col items-center gap-1 px-1.5 pt-2">
          <RailButton title="新会话" onClick={startNewSession}>
            <MessageSquarePlus className="size-4" />
          </RailButton>
          <RailButton title="新建工作区" onClick={() => setPickerOpen(true)}>
            <FolderPlus className="size-4" />
          </RailButton>
        </div>
        <div className="mt-auto flex w-full justify-center border-t border-line p-1.5">
          <RailButton title="设置" onClick={() => setSettingsOpen(true)}>
            <Settings className="size-4" />
          </RailButton>
        </div>
        <WorkspacePicker open={pickerOpen} onClose={() => setPickerOpen(false)} />
      </aside>
    );
  }

  return (
    <aside className="sidebar flex h-full w-full min-w-0 flex-col border-r border-line bg-sidebar">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-line px-4">
        <img src="/logo.png" alt="" draggable={false} className="size-8 shrink-0 rounded-[10px]" />
        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <div className="truncate text-sm font-medium">NoNo Agent</div>
          <span
            className={`size-2 shrink-0 rounded-full ${healthOk ? "bg-ok" : "bg-danger"}`}
            title={healthOk ? "服务在线" : "服务离线"}
            aria-label={healthOk ? "服务在线" : "服务离线"}
          />
        </div>
        <button
          type="button"
          className="flex size-8 shrink-0 items-center justify-center rounded-lg text-faint hover:bg-hover hover:text-ink"
          title="收起菜单"
          aria-label="收起菜单"
          onClick={onCollapse}
        >
          <PanelLeftClose className="size-4" />
        </button>
      </div>

      <div className="px-3 pb-3 pt-3">
        <Button
          variant="outline"
          className="h-9 w-full justify-center gap-2 rounded-full border-line-strong bg-bg"
          onClick={startNewSession}
        >
          <MessageSquarePlus className="size-4" />
          新会话
        </Button>
      </div>

      {line}
      <div
        ref={scrollerRef}
        className="flex-1 overflow-auto px-2 pb-3"
        onDragOver={scrollerProps.onDragOver}
        onDrop={scrollerProps.onDrop}
      >
        <div className="mb-1 flex items-center justify-between px-2">
          <span className="text-[11px] font-medium text-faint">工作区</span>
          <div className="flex items-center">
            <ListViewMenu
              groupMode={listView.group}
              sortMode={listView.sort}
              archiveFilter={archiveFilter}
              onGroup={chooseGroup}
              onSort={chooseSort}
              onArchive={(filter) => void setSessionArchiveFilter(filter)}
            />
            <button
              type="button"
              className="flex size-6 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
              title="新建工作区"
              onClick={() => setPickerOpen(true)}
            >
              <FolderPlus className="size-3.5" />
            </button>
          </div>
        </div>

        {listView.group === "flat" ? (
          groups.ordered.length ? (
            <ul>
              {groups.ordered.map((session) => (
                <SessionRow key={session.id} session={session} drag={dragSession(session)} />
              ))}
            </ul>
          ) : null
        ) : listView.group === "tree" ? (
          <TreeBranch
            nodes={treeNodes}
            sessionsById={groups.byId}
            workspacePath={workspace}
            collapsed={collapsed}
            onToggle={toggleCollapsed}
            onExpand={expandGroup}
            manualSort={manualSort}
            dragSessions={dragSession}
            expandedGroups={expandedGroups}
            onToggleSessions={toggleSessionGroup}
          />
        ) : (
          visibleWorkspaces.map((item) => (
            <WorkspaceRow
              key={item.id}
              workspace={item}
              sessions={groups.byId.get(item.id) ?? []}
              active={sameWorkspace(item.path, workspace)}
              collapsed={collapsed.has(item.id)}
              onToggle={() => toggleCollapsed(item.id)}
              onExpand={() => expandGroup(item.id)}
              drag={dragWorkspace(item)}
              dragSessions={dragSession}
              expandedGroups={expandedGroups}
              onToggleSessions={toggleSessionGroup}
            />
          ))
        )}

        {listView.group !== "flat" && groups.unbound.length ? (
          <div className="mb-1">
            <button
              type="button"
              className="flex w-full items-center gap-1 rounded-lg px-2 py-1.5 text-left text-[13px] text-ink/80 hover:bg-hover"
              title={ungroupedOpen ? "折起分组" : "展开分组"}
              aria-expanded={ungroupedOpen}
              onClick={() => toggleCollapsed(UNGROUPED_ID)}
            >
              <ChevronRight
                className={`size-3 shrink-0 text-faint transition-transform ${ungroupedOpen ? "rotate-90" : ""}`}
              />
              {ungroupedOpen ? (
                <FolderOpen className="size-3.5 shrink-0 text-muted" />
              ) : (
                <Folder className="size-3.5 shrink-0 text-muted" />
              )}
              <span className="truncate">未分组</span>
            </button>
            {ungroupedOpen ? (
              <SessionGroupList
                groupId={UNGROUPED_ID}
                sessions={groups.unbound}
                activeSessionId={sessionId}
                expandedGroups={expandedGroups}
                onToggleGroup={toggleSessionGroup}
                dragSessions={dragSession}
              />
            ) : null}
          </div>
        ) : null}

        {listView.group === "flat" && groups.ordered.length === 0 ? (
          <p className="px-2 pt-2 text-xs leading-5 text-muted">
            {archiveFilter === "only" ? "没有已归档的会话。" : "还没有会话。"}
          </p>
        ) : null}
        {listView.group !== "flat" && archiveFilter === "only" && sessions.length === 0 ? (
          <p className="px-2 pt-2 text-xs leading-5 text-muted">没有已归档的会话。</p>
        ) : null}
        {listView.group !== "flat" && archiveFilter !== "only" && workspaces.length === 0 && sessions.length === 0 ? (
          <p className="px-2 pt-2 text-xs leading-5 text-muted">点右上角 + 添加一个工作区。</p>
        ) : null}
      </div>

      <div className="border-t border-line p-2">
        <Button
          variant="ghost"
          className="h-9 w-full justify-start gap-2 text-muted"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings className="size-4" />
          设置
        </Button>
      </div>

      <WorkspacePicker open={pickerOpen} onClose={() => setPickerOpen(false)} />
    </aside>
  );
}
