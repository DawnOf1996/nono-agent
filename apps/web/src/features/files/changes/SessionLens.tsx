import type { TurnChanges } from "@nono/protocol";
import { useEffect, useMemo } from "react";

import { DiffStat, StatusBadge, statusLetter } from "@/features/files/diff/DiffStat";
import { diffTabId, usePanelStore } from "@/features/files/panel-store";
import { baseName, parentPath } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

function timeLabel(seconds: number): string {
  const date = new Date(seconds * 1000);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${date.toLocaleDateString()} ${time}`;
}

function TurnCard({ turn, order }: { turn: TurnChanges; order: number }) {
  const openDiff = usePanelStore((state) => state.openDiff);
  const active = usePanelStore((state) => state.active);
  const tabs = usePanelStore((state) => state.tabs);
  const target = { source: "turn" as const, sessionId: turn.session_id, turnId: turn.id, index: 0 };
  const openTab = tabs.find((tab) => tab.id === diffTabId(target));
  const openIndex =
    openTab && openTab.id === active && openTab.kind === "diff" && openTab.target.source === "turn"
      ? openTab.target.index
      : -1;
  return (
    <section className="mx-1 mt-2 overflow-hidden rounded-lg border border-line">
      <div className="flex items-center gap-2 bg-sidebar px-2.5 py-1.5 text-[11px] text-muted">
        <span className="font-medium text-ink">第 {order} 轮</span>
        <span>{timeLabel(turn.ended_at)}</span>
        <span className="flex-1" />
        <span>{turn.total} 个文件</span>
        <DiffStat added={turn.added} deleted={turn.deleted} />
      </div>
      <ul>
        {turn.files.map((file, index) => {
          const dir = parentPath(file.display);
          return (
            <li key={`${file.display}-${index}`}>
              <button
                type="button"
                title={file.abs_path}
                className={cn(
                  "flex h-7 w-full min-w-0 items-center gap-1.5 px-2.5 text-left text-[12.5px]",
                  openIndex === index ? "bg-active" : "hover:bg-hover",
                )}
                onClick={() => openDiff({ ...target, index }, baseName(file.display))}
              >
                <StatusBadge letter={statusLetter(file.status)} />
                <span className={cn("min-w-0 shrink truncate", file.status === "deleted" && "text-muted line-through")}>
                  {baseName(file.display)}
                </span>
                {dir ? <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{dir}</span> : <span className="flex-1" />}
                <DiffStat added={file.added} deleted={file.deleted} binary={file.binary} oversized={file.oversized} />
              </button>
            </li>
          );
        })}
        {turn.total > turn.files.length ? (
          <li className="px-2.5 py-1.5 text-[11px] text-faint">另有 {turn.total - turn.files.length} 个文件未列出</li>
        ) : null}
      </ul>
    </section>
  );
}

/** 当前会话里智能体每轮改动的文件，最新一轮在最上面。 */
export function SessionLens() {
  const sessionId = useChatStore((state) => state.sessionId);
  const turns = useChatStore((state) => (sessionId ? state.turnChanges[sessionId] : undefined));
  const load = useChatStore((state) => state.loadTurnChanges);

  useEffect(() => {
    if (sessionId && !turns) void load(sessionId);
  }, [sessionId, turns, load]);

  const ordered = useMemo(() => (turns ?? []).map((turn, index) => ({ turn, order: index + 1 })).reverse(), [turns]);

  if (!sessionId) {
    return <p className="px-4 py-6 text-[13px] leading-6 text-muted">开始对话后，智能体每轮改动的文件会列在这里。</p>;
  }
  if (!ordered.length) {
    return (
      <p className="px-4 py-6 text-[13px] leading-6 text-muted">
        这个会话还没有改动过文件。智能体改动文件后，这里会按轮次列出，点击即可查看对比。
      </p>
    );
  }
  return (
    <div className="min-h-0 flex-1 overflow-auto px-1 pb-3">
      {ordered.map(({ turn, order }) => (
        <TurnCard key={turn.id} turn={turn} order={order} />
      ))}
    </div>
  );
}
