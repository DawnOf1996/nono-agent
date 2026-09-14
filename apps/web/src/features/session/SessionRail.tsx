import { Plus, Settings } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

function shortId(id: string): string {
  return id.slice(0, 8);
}

export function SessionRail() {
  const {
    sessions,
    sessionId,
    healthOk,
    catalog,
    setNewTaskOpen,
    setSettingsOpen,
    openSession,
  } = useChatStore();

  const groups = new Map<string, typeof sessions>();
  for (const session of sessions) {
    const key = session.cwd || "未绑定目录";
    const list = groups.get(key) ?? [];
    list.push(session);
    groups.set(key, list);
  }

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-line bg-ink">
      <div className="flex items-center justify-between px-4 py-4">
        <div>
          <div className="font-mono text-[10px] uppercase tracking-[0.22em] text-copper">
            Nono
          </div>
          <div className="mt-1 text-sm text-paper">工作台</div>
        </div>
        <div className="flex gap-1">
          <Button size="icon" variant="ghost" onClick={() => setSettingsOpen(true)} aria-label="模型设置">
            <Settings className="size-4" />
          </Button>
          <Button size="icon" variant="ghost" onClick={() => setNewTaskOpen(true)} aria-label="新任务">
            <Plus className="size-4" />
          </Button>
        </div>
      </div>

      <div className="px-4 pb-3">
        <div className="flex items-center gap-2 font-mono text-[10px] uppercase tracking-wider text-muted">
          <span
            className={`size-1.5 rounded-full ${healthOk ? "bg-copper" : "bg-danger"}`}
            aria-hidden
          />
          {healthOk ? "sidecar 在线" : "sidecar 离线"}
        </div>
        <div className="mt-2 truncate text-[11px] text-muted">
          {catalog?.default
            ? `默认 ${catalog.default.model}`
            : "还没有默认模型"}
        </div>
      </div>

      <div className="flex-1 overflow-auto px-2 pb-4">
        {sessions.length === 0 ? (
          <p className="px-2 pt-6 text-xs leading-5 text-muted">
            点右上角 + 开一个任务，先选工作空间目录。
          </p>
        ) : (
          [...groups.entries()].map(([cwd, items]) => (
            <div key={cwd} className="mb-4">
              <div className="px-2 pb-1 font-mono text-[10px] uppercase tracking-wider text-steel">
                {workspaceLabel(cwd)}
              </div>
              <ul className="space-y-1">
                {items.map((session) => {
                  const active = session.session_id === sessionId;
                  return (
                    <li key={session.session_id}>
                      <button
                        type="button"
                        className={`flex w-full flex-col rounded-sm px-2 py-2 text-left ${
                          active ? "bg-panel text-paper" : "text-muted hover:bg-white/5 hover:text-paper"
                        }`}
                        onClick={() => openSession(session.session_id)}
                      >
                        <span className="flex items-center justify-between gap-2">
                          <span className="font-mono text-xs">{shortId(session.session_id)}</span>
                          <Badge>{session.turn_count} 轮</Badge>
                        </span>
                        <span className="mt-0.5 truncate font-mono text-[10px] text-muted">
                          {session.model}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
