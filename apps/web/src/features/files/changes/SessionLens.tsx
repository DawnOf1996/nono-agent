import type { TurnChanges } from "@nono/protocol";
import { useEffect, useMemo, useState } from "react";

import { DiffStat, StatusBadge, statusLetter } from "@/features/files/diff/DiffStat";
import {
  fileOpsFromEvents,
  fileOpsFromStreaming,
  groupOpsByFile,
  opDiff,
  type FileOp,
  type FileOpKind,
} from "@/features/files/changes/file-ops";
import { diffTabId, usePanelStore } from "@/features/files/panel-store";
import { baseName, parentPath } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const FILTERS: Array<{ id: "all" | FileOpKind; label: string }> = [
  { id: "all", label: "全部" },
  { id: "read", label: "读取" },
  { id: "write", label: "写入" },
  { id: "edit", label: "编辑" },
];

const KIND_LABEL: Record<FileOpKind, string> = {
  read: "读取",
  write: "写入",
  edit: "编辑",
};

function timeLabel(seconds: number): string {
  const date = new Date(seconds * 1000);
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  return sameDay ? time : `${date.toLocaleDateString()} ${time}`;
}

function TurnCard({ turn, order }: { turn: TurnChanges; order: number }) {
  const setChangePreview = usePanelStore((state) => state.setChangePreview);
  const changePreview = usePanelStore((state) => state.changePreview);
  const active = usePanelStore((state) => state.active);
  const tabs = usePanelStore((state) => state.tabs);
  const target = { source: "turn" as const, sessionId: turn.session_id, turnId: turn.id, index: 0 };
  const openTab = tabs.find((tab) => tab.id === diffTabId(target));
  const previewIndex =
    changePreview?.target.source === "turn" && changePreview.target.turnId === turn.id ? changePreview.target.index : -1;
  const openIndex =
    previewIndex >= 0
      ? previewIndex
      : openTab && openTab.id === active && openTab.kind === "diff" && openTab.target.source === "turn"
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
                onClick={() => setChangePreview({ ...target, index }, baseName(file.display))}
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
      </ul>
    </section>
  );
}

function OpRow({ op, selected }: { op: FileOp; selected: boolean }) {
  const setChangePreview = usePanelStore((state) => state.setChangePreview);
  const openFile = usePanelStore((state) => state.openFile);
  return (
    <button
      type="button"
      className={cn(
        "flex h-7 w-full min-w-0 items-center gap-1.5 rounded-md px-2 text-left text-[12px]",
        selected ? "bg-active" : "hover:bg-hover",
      )}
      onClick={() => {
        if (op.kind === "read" && !op.isError && !op.running) {
          openFile(op.path, baseName(op.path));
          return;
        }
        setChangePreview(
          { source: "op", callId: op.callId, diff: opDiff(op), errorText: op.errorText },
          baseName(op.path),
        );
      }}
    >
      <span
        className={cn(
          "shrink-0 rounded px-1 text-[10px] leading-4",
          op.kind === "read" && "bg-card text-muted",
          op.kind === "write" && "bg-[#e7f6ec] text-[#1d7a3a]",
          op.kind === "edit" && "bg-[#e8eefc] text-[#2a4ea3]",
        )}
      >
        {KIND_LABEL[op.kind]}
      </span>
      {op.running ? <span className="shrink-0 text-[10px] text-faint">进行中</span> : null}
      {op.isError ? <span className="shrink-0 text-[10px] text-danger">失败</span> : null}
      <span className="flex-1" />
      <span className="shrink-0 text-[11px] text-faint">{timeLabel(op.time)}</span>
    </button>
  );
}

/** 当前会话里模型读过、写过、编辑过的文件，按文件分组。 */
export function SessionLens() {
  const sessionId = useChatStore((state) => state.sessionId);
  const events = useChatStore((state) => state.events);
  const messages = useChatStore((state) => state.messages);
  const turns = useChatStore((state) => (sessionId ? state.turnChanges[sessionId] : undefined));
  const load = useChatStore((state) => state.loadTurnChanges);
  const active = usePanelStore((state) => state.active);
  const tabs = usePanelStore((state) => state.tabs);
  const changePreview = usePanelStore((state) => state.changePreview);
  const [filter, setFilter] = useState<"all" | FileOpKind>("all");
  const [turnsOpen, setTurnsOpen] = useState(false);

  useEffect(() => {
    if (sessionId && !turns) void load(sessionId);
  }, [sessionId, turns, load]);

  const ops = useMemo(() => {
    const settled = fileOpsFromEvents(events);
    const live = fileOpsFromStreaming(messages).filter(
      (op) => !settled.some((item) => item.running && item.kind === op.kind && item.path === op.path),
    );
    return [...settled, ...live];
  }, [events, messages]);
  const counts = useMemo(() => {
    const map: Record<FileOpKind, number> = { read: 0, write: 0, edit: 0 };
    for (const op of ops) map[op.kind] += 1;
    return map;
  }, [ops]);
  const visible = filter === "all" ? ops : ops.filter((op) => op.kind === filter);
  const groups = useMemo(() => groupOpsByFile(visible), [visible]);
  const orderedTurns = useMemo(
    () => (turns ?? []).map((turn, index) => ({ turn, order: index + 1 })).reverse(),
    [turns],
  );
  const selectedCall =
    changePreview?.target.source === "op"
      ? changePreview.target.callId
      : (tabs.find((tab) => tab.id === active && tab.kind === "diff" && tab.target.source === "op")?.id.replace(
          /^diff:op:/,
          "",
        ) ?? "");

  if (!sessionId) {
    return <p className="px-4 py-6 text-[13px] leading-6 text-muted">开始对话后，模型读过和改过的文件会列在这里。</p>;
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap gap-1 px-2 pt-2" role="group" aria-label="操作类型">
        {FILTERS.map((item) => {
          const count = item.id === "all" ? ops.length : counts[item.id];
          return (
            <button
              key={item.id}
              type="button"
              aria-pressed={filter === item.id}
              className={cn(
                "h-6 rounded-full px-2 text-[11px]",
                filter === item.id ? "bg-ink text-white" : "bg-card text-muted hover:text-ink",
              )}
              onClick={() => setFilter(item.id)}
            >
              {item.label}
              {item.id === "all" ? "" : ` ${count}`}
            </button>
          );
        })}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-3">
        {ops.length === 0 ? (
          <p className="px-2 py-4 text-[13px] leading-6 text-muted">这个会话还没有读过或改过文件。</p>
        ) : groups.length === 0 ? (
          <p className="px-2 py-4 text-[13px] leading-6 text-muted">这个类型下没有操作。</p>
        ) : (
          groups.map((group) => (
            <section key={group.path} className="mt-2">
              <div className="flex min-w-0 items-center gap-1.5 px-2 py-1" title={group.path}>
                <span className="min-w-0 truncate text-[12.5px] font-medium">{baseName(group.path)}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{parentPath(group.path)}</span>
                <span className="shrink-0 rounded-full bg-card px-1.5 text-[10px] tabular-nums text-muted">
                  {group.ops.length}
                </span>
              </div>
              {group.ops.map((op) => (
                <OpRow key={op.callId} op={op} selected={selectedCall === op.callId} />
              ))}
            </section>
          ))
        )}
        {orderedTurns.length ? (
          <div className="mt-3 border-t border-line pt-2">
            <button
              type="button"
              className="px-2 text-[12px] text-muted hover:text-ink"
              aria-expanded={turnsOpen}
              onClick={() => setTurnsOpen((value) => !value)}
            >
              {turnsOpen ? "收起" : "展开"}按轮次的改动（{orderedTurns.length}）
            </button>
            {turnsOpen
              ? orderedTurns.map(({ turn, order }) => <TurnCard key={turn.id} turn={turn} order={order} />)
              : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
