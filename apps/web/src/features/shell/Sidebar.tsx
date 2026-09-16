import { Archive, ChevronRight, Folder, FolderOpen, GitFork, Loader2, MoreHorizontal, Pencil, Plus, Settings, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { SessionListItem, Workspace } from "@nono/protocol";

import { openWorkspaceFolder } from "@/api/client";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { formatRelativeTime, sessionTitle } from "@/lib/format";
import { sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { sessionRun, useChatStore } from "@/store/chat";

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
              className="fixed z-50 w-[168px] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_rgba(15,17,21,0.12)]"
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
  title,
  onRename,
  onOpenChange,
}: {
  sessionId: string;
  title: string;
  onRename: () => void;
  onOpenChange?: (open: boolean) => void;
}) {
  const forkSession = useChatStore((state) => state.forkSession);
  const archiveSession = useChatStore((state) => state.archiveSession);
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
              className="fixed z-50 w-[168px] rounded-xl border border-line bg-bg p-1 shadow-[0_12px_32px_rgba(15,17,21,0.12)]"
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
        description={`删除「${title}」后无法恢复。`}
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

function SessionRow({ session }: { session: SessionListItem }) {
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
    <li className="group">
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
            className="flex min-w-0 flex-1 items-center px-2 py-1.5 text-left text-[13px]"
            onClick={() => void openSession(session.engine_session_id)}
          >
            <span className="truncate">{title}</span>
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
                  title={title}
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

function WorkspaceRow({
  workspace,
  sessions,
  active,
  collapsed,
  onToggle,
  onExpand,
}: {
  workspace: Workspace;
  sessions: SessionListItem[];
  active: boolean;
  collapsed: boolean;
  onToggle: () => void;
  onExpand: () => void;
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
    <div className="mb-1">
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
            className="flex min-w-0 flex-1 items-center gap-1 px-2 py-1.5 text-left text-[13px]"
            title={expanded ? "折起分组" : "展开分组"}
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
          <Plus className="size-3.5" />
        </button>
      </div>
      {expanded ? (
        <ul className="ml-3 border-l border-line pl-2">
          {showDraft ? (
            <li>
              <div className="flex w-full items-center justify-between gap-2 rounded-lg bg-active px-2 py-1.5 text-left text-[13px] text-ink">
                <span className="truncate">新会话</span>
                <span className="shrink-0 text-[11px] text-faint">刚刚</span>
              </div>
            </li>
          ) : null}
          {sessions.map((session) => (
            <SessionRow key={session.id} session={session} />
          ))}
        </ul>
      ) : null}
    </div>
  );
}

export function Sidebar() {
  const {
    sessions,
    healthOk,
    workspace,
    workspaces,
    setSettingsOpen,
    newSession,
  } = useChatStore();
  const [pickerOpen, setPickerOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Set<string>>(loadCollapsed);

  const groups = useMemo(() => {
    const byId = new Map<string, typeof sessions>();
    for (const item of workspaces) {
      byId.set(item.id, []);
    }
    const unbound: typeof sessions = [];
    for (const session of sessions) {
      if (byId.has(session.workspace_id)) {
        byId.get(session.workspace_id)?.push(session);
      } else {
        unbound.push(session);
      }
    }
    return { byId, unbound };
  }, [sessions, workspaces]);

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

  return (
    <aside className="flex w-[260px] shrink-0 flex-col border-r border-line bg-sidebar">
      <div className="flex items-center gap-2 px-4 py-4">
        <span className="flex size-7 items-center justify-center rounded-full bg-accent text-[11px] font-semibold text-white">
          N
        </span>
        <div className="min-w-0">
          <div className="text-sm font-medium">Nono</div>
          <div className="truncate text-[11px] text-faint">{healthOk ? "sidecar 在线" : "sidecar 离线"}</div>
        </div>
      </div>

      <div className="px-3 pb-3">
        <Button
          variant="outline"
          className="h-9 w-full justify-start gap-2 rounded-full border-line-strong bg-bg"
          onClick={() => {
            const listed = workspaces.some((item) => sameWorkspace(item.path, workspace));
            if (!workspace.trim() || !listed) {
              setPickerOpen(true);
              return;
            }
            void newSession();
          }}
        >
          <Plus className="size-4" />
          新会话
        </Button>
      </div>

      <div className="flex-1 overflow-auto px-2 pb-3">
        <div className="mb-1 flex items-center justify-between px-2">
          <span className="text-[11px] font-medium text-faint">工作区</span>
          <button
            type="button"
            className="flex size-6 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
            title="新建工作区"
            onClick={() => setPickerOpen(true)}
          >
            <Plus className="size-3.5" />
          </button>
        </div>

        {workspaces.map((item) => (
          <WorkspaceRow
            key={item.id}
            workspace={item}
            sessions={groups.byId.get(item.id) ?? []}
            active={sameWorkspace(item.path, workspace)}
            collapsed={collapsed.has(item.id)}
            onToggle={() => toggleCollapsed(item.id)}
            onExpand={() => expandGroup(item.id)}
          />
        ))}

        {groups.unbound.length ? (
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
              <ul className="ml-3 border-l border-line pl-2">
                {groups.unbound.map((session) => (
                  <SessionRow key={session.id} session={session} />
                ))}
              </ul>
            ) : null}
          </div>
        ) : null}

        {workspaces.length === 0 && sessions.length === 0 ? (
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
