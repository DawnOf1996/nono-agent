import { Folder, Plus, Settings } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { WorkspacePicker } from "@/features/session/WorkspacePicker";
import { formatRelativeTime, sessionTitle } from "@/lib/format";
import { sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

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
    const byPath = new Map<string, typeof sessions>();
    for (const path of workspaces) {
      byPath.set(path, []);
    }
    const unbound: typeof sessions = [];
    for (const session of sessions) {
      const cwd = session.cwd?.trim() ?? "";
      const owner = cwd
        ? [...byPath.keys()].find((path) => sameWorkspace(path, cwd))
        : undefined;
      if (owner) {
        byPath.get(owner)?.push(session);
      } else if (cwd) {
        byPath.set(cwd, [...(byPath.get(cwd) ?? []), session]);
      } else {
        unbound.push(session);
      }
    }
    return { byPath, unbound };
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

        {[...groups.byPath.entries()].map(([cwd, items]) => {
          const activeWs = sameWorkspace(cwd, workspace);
          return (
            <div key={cwd} className="mb-1">
              <button
                type="button"
                className={`flex w-full items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[13px] ${
                  activeWs && !sessionId ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
                }`}
                onClick={() => setWorkspace(cwd)}
              >
                <Folder className="size-3.5 shrink-0 text-muted" />
                <span className="truncate">{workspaceLabel(cwd)}</span>
              </button>
              <ul className="ml-3 border-l border-line pl-2">
                {items.map((session) => {
                  const active = session.session_id === sessionId;
                  return (
                    <li key={session.session_id}>
                      <button
                        type="button"
                        className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] ${
                          active ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
                        }`}
                        onClick={() => void openSession(session.session_id)}
                      >
                        <span className="truncate">{sessionTitle(session.session_id, sessionTitles, session.summary)}</span>
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
                <li key={session.session_id}>
                  <button
                    type="button"
                    className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-left text-[13px] ${
                      session.session_id === sessionId ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
                    }`}
                    onClick={() => void openSession(session.session_id)}
                  >
                    <span className="truncate">{sessionTitle(session.session_id, sessionTitles, session.summary)}</span>
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
