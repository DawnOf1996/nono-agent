import { Folder, FolderOpen, MoreHorizontal, Plus, Settings, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { openWorkspaceFolder } from "@/api/client";
import { Button } from "@/components/ui/button";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { formatRelativeTime, sessionTitle } from "@/lib/format";
import { sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

function WorkspaceRowMenu({ workspace }: { workspace: { id: string; path: string } }) {
  const removeWorkspace = useChatStore((state) => state.removeWorkspace);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const buttonRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

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

  async function openFolder() {
    setOpen(false);
    try {
      await openWorkspaceFolder(workspace.path);
    } catch (error) {
      console.error(error);
    }
  }

  function remove() {
    setOpen(false);
    void removeWorkspace(workspace.id);
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
                onClick={() => void openFolder()}
              >
                <FolderOpen className="size-3.5 text-muted" />
                打开文件夹
              </button>
              <button
                type="button"
                className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] text-danger hover:bg-danger/10"
                onClick={remove}
              >
                <Trash2 className="size-3.5" />
                删除工作区
              </button>
            </div>,
            document.body,
          )
        : null}
    </>
  );
}

export function Sidebar() {
  const {
    sessions,
    sessionId,
    sessionTitles,
    healthOk,
    workspace,
    workspaces,
    setWorkspace,
    setSettingsOpen,
    newSession,
    openSession,
  } = useChatStore();
  const [pickerOpen, setPickerOpen] = useState(false);

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
            if (!workspace.trim()) {
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

        {workspaces.map((item) => {
          const items = groups.byId.get(item.id) ?? [];
          const activeWs = sameWorkspace(item.path, workspace);
          return (
            <div key={item.id} className="mb-1">
              <div
                className={`flex items-center rounded-lg pr-0.5 ${
                  activeWs && !sessionId ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
                }`}
              >
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-1.5 px-2 py-1.5 text-left text-[13px]"
                  onClick={() => setWorkspace(item.path)}
                >
                  <Folder className="size-3.5 shrink-0 text-muted" />
                  <span className="truncate">{item.name || workspaceLabel(item.path)}</span>
                </button>
                <WorkspaceRowMenu workspace={item} />
              </div>
              <ul className="ml-3 border-l border-line pl-2">
                {items.map((session) => {
                  const active = session.engine_session_id === sessionId;
                  return (
                    <li key={session.id}>
                      <button
                        type="button"
                        className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] ${
                          active ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
                        }`}
                        onClick={() => void openSession(session.engine_session_id)}
                      >
                        <span className="truncate">
                          {sessionTitle(session.engine_session_id, sessionTitles, session.title)}
                        </span>
                        <span className="shrink-0 text-[11px] text-faint">{formatRelativeTime(session.updated_at)}</span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}

        {groups.unbound.length ? (
          <div className="mb-1">
            <div className="px-2 py-1.5 text-[12px] text-faint">未绑定目录</div>
            <ul className="ml-3 border-l border-line pl-2">
              {groups.unbound.map((session) => (
                <li key={session.id}>
                  <button
                    type="button"
                    className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] ${
                      session.engine_session_id === sessionId ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
                    }`}
                    onClick={() => void openSession(session.engine_session_id)}
                  >
                    <span className="truncate">
                      {sessionTitle(session.engine_session_id, sessionTitles, session.title)}
                    </span>
                    <span className="shrink-0 text-[11px] text-faint">{formatRelativeTime(session.updated_at)}</span>
                  </button>
                </li>
              ))}
            </ul>
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
