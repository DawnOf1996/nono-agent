import { Composer } from "@/features/chat/Composer";
import { MessageList } from "@/features/chat/MessageList";
import { Trajectory } from "@/features/chat/Trajectory";
import { sessionTitle } from "@/lib/format";
import { workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

export function ChatPane() {
  const { sessionId, sessionTitles, view, setView, workspace } = useChatStore();
  const title = sessionId ? sessionTitle(sessionId, sessionTitles) : "新会话";
  const workspaceName = workspace.trim() ? workspaceLabel(workspace) : "";

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-bg">
      <header className="flex min-h-[72px] items-end justify-between gap-4 px-6 pb-3 pt-3">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-medium">{title}</div>
          {workspaceName ? (
            <div className="mt-0.5 truncate text-[12px] text-faint" title={workspace}>
              {workspaceName}
            </div>
          ) : null}
        </div>
        <div className="flex gap-5 text-[13px]">
          <button
            type="button"
            className={view === "chat" ? "border-b-2 border-ink pb-1 text-ink" : "pb-1 text-faint hover:text-ink"}
            onClick={() => setView("chat")}
          >
            对话
          </button>
          <button
            type="button"
            className={view === "trajectory" ? "border-b-2 border-ink pb-1 text-ink" : "pb-1 text-faint hover:text-ink"}
            onClick={() => setView("trajectory")}
          >
            轨迹
          </button>
        </div>
      </header>
      {view === "chat" ? <MessageList /> : <Trajectory />}
      <Composer />
    </section>
  );
}
