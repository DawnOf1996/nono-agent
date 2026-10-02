import { PanelBottom, PanelRight } from "lucide-react";

import { Composer } from "@/features/chat/Composer";
import { MessageList } from "@/features/chat/MessageList";
import { TodoDock } from "@/features/chat/TodoDock";
import { Trajectory } from "@/features/chat/Trajectory";
import { sessionTitle } from "@/lib/format";
import { sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

export function ChatPane({
  rightOpen,
  onToggleRight,
  bottomOpen,
  onToggleBottom,
}: {
  rightOpen: boolean;
  onToggleRight: () => void;
  bottomOpen: boolean;
  onToggleBottom: () => void;
}) {
  const sessionId = useChatStore((state) => state.sessionId);
  const sessionTitles = useChatStore((state) => state.sessionTitles);
  const view = useChatStore((state) => state.view);
  const setView = useChatStore((state) => state.setView);
  const workspace = useChatStore((state) => state.workspace);
  const workspaces = useChatStore((state) => state.workspaces);
  const draftWorkspaceId = useChatStore((state) => state.draftWorkspaceId);
  const title = sessionId ? sessionTitle(sessionId, sessionTitles) : "新会话";
  const listed = workspaces.find((item) => sameWorkspace(item.path, workspace));
  const bound = Boolean(sessionId || draftWorkspaceId);
  const workspaceName =
    bound && workspace.trim() ? listed?.name || workspaceLabel(workspace) : "";

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-bg">
      <header className="flex h-12 shrink-0 items-center gap-3 border-b border-line px-4">
        <div
          className="min-w-0 flex-1 truncate text-[14px]"
          title={workspaceName ? `${workspaceName} / ${title}` : title}
        >
          {workspaceName ? (
            <>
              <span className="text-muted" title={workspace}>
                {workspaceName}
              </span>
              <span className="text-faint"> / </span>
            </>
          ) : null}
          <span className="font-medium">{title}</span>
        </div>
        <div className="flex h-full shrink-0 items-center gap-1">
          <div className="flex h-full items-stretch gap-4 text-[13px]">
            <button
              type="button"
              className={
                view === "chat"
                  ? "-mb-px border-b-2 border-ink text-ink"
                  : "-mb-px border-b-2 border-transparent text-faint hover:text-ink"
              }
              onClick={() => setView("chat")}
            >
              对话
            </button>
            <button
              type="button"
              className={
                view === "trajectory"
                  ? "-mb-px border-b-2 border-ink text-ink"
                  : "-mb-px border-b-2 border-transparent text-faint hover:text-ink"
              }
              onClick={() => setView("trajectory")}
            >
              轨迹
            </button>
          </div>
          <button
            type="button"
            className={`flex size-8 items-center justify-center rounded-lg hover:bg-hover hover:text-ink ${
              bottomOpen ? "text-ink" : "text-faint"
            }`}
            title={bottomOpen ? "收起底部工作台" : "打开底部工作台"}
            aria-label={bottomOpen ? "收起底部工作台" : "打开底部工作台"}
            aria-pressed={bottomOpen}
            onClick={onToggleBottom}
          >
            <PanelBottom className="size-4" />
          </button>
          {rightOpen ? null : (
            <button
              type="button"
              className="flex size-8 items-center justify-center rounded-lg text-faint hover:bg-hover hover:text-ink"
              title="打开文件栏"
              aria-label="打开文件栏"
              aria-pressed={false}
              onClick={onToggleRight}
            >
              <PanelRight className="size-4" />
            </button>
          )}
        </div>
      </header>
      {view === "chat" ? <MessageList /> : <Trajectory />}
      <TodoDock />
      <Composer />
    </section>
  );
}
