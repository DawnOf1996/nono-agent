import { FolderOpen } from "lucide-react";

import { Composer } from "@/features/chat/Composer";
import { MessageList } from "@/features/chat/MessageList";
import { Select } from "@/components/ui/input";
import { pickWorkspace, workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

export function ChatPane() {
  const {
    error,
    workspace,
    model,
    catalog,
    setModel,
    setWorkspace,
    sessions,
    sessionId,
    newSession,
  } = useChatStore();
  const session = sessions.find((item) => item.session_id === sessionId);
  const cwd = session?.cwd || workspace;

  async function changeWorkspace() {
    const path = await pickWorkspace(cwd);
    if (!path) return;
    setWorkspace(path);
    await newSession({ cwd: path, model });
  }

  return (
    <section className="flex min-w-0 flex-1 flex-col bg-panel">
      <header className="flex h-12 items-center justify-between gap-3 border-b border-line px-4">
        <button
          type="button"
          className="min-w-0 truncate text-left font-mono text-[11px] text-muted hover:text-paper"
          title={cwd || "未选择工作空间"}
          onClick={() => void changeWorkspace()}
        >
          <FolderOpen className="mr-1 inline size-3.5 align-[-2px] text-steel" />
          {cwd ? workspaceLabel(cwd) : "选择工作空间"}
        </button>
        <div className="flex min-w-0 items-center gap-3">
          {error ? <span className="max-w-56 truncate text-xs text-danger">{error}</span> : null}
          <Select
            className="h-8 w-52 shrink-0"
            value={model}
            onChange={(event) => {
              const next = event.target.value;
              setModel(next);
              if (session && session.turn_count === 0 && cwd) {
                void newSession({ cwd, model: next });
              }
            }}
          >
            {!model ? <option value="">模型</option> : null}
            {(catalog?.providers ?? []).map((provider) => (
              <optgroup key={provider.id} label={provider.display_name}>
                {provider.models.map((item) => (
                  <option key={`${provider.id}:${item.id}`} value={item.id}>
                    {item.name || item.id}
                  </option>
                ))}
              </optgroup>
            ))}
          </Select>
        </div>
      </header>
      <MessageList />
      <Composer />
    </section>
  );
}
