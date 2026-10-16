import { useMemo } from "react";

import { extractTextDelta, extractThinkingDelta } from "@/api/sse";
import type { SessionEvent } from "@nono/protocol";
import { CopyButton } from "@/components/CopyButton";
import { useChatStore } from "@/store/chat";

type TrajectoryRow = {
  key: string;
  type: string;
  seqLabel: string;
  count?: number;
  data: Record<string, unknown>;
};

export function Trajectory() {
  const events = useChatStore((state) => state.events);
  const rows = useMemo(() => groupChunkEvents(events), [events]);

  if (events.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-8 text-sm text-muted">
        还没有轨迹。发一条消息后，事件会列在这里。
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto px-8 py-6">
      <ol className="mx-auto max-w-3xl space-y-2">
        {rows.map((row) => (
          <li key={row.key} className="rounded-xl bg-card px-3 py-2">
            <div className="flex items-center justify-between gap-3">
              <span className="font-mono text-[12px] text-ink">
                {row.type}
                {row.count && row.count > 1 ? (
                  <span className="ml-2 text-faint">×{row.count}</span>
                ) : null}
              </span>
              {/* 事件数据是排查问题时唯一要往外搬的东西（贴 issue、喂给模型、
                  存成样本），没有复制键就只能一段段手选。放在标题行而不是代码块
                  里面：数据块可能很长，按钮浮在内容上会跟着滚动，且会挡住第一行。 */}
              <div className="flex shrink-0 items-center gap-1">
                <CopyButton
                  text={JSON.stringify(row.data, null, 2)}
                  label="复制事件数据"
                  compact
                />
                <span className="font-mono text-[11px] text-faint">{row.seqLabel}</span>
              </div>
            </div>
            {Object.keys(row.data).length ? (
              <pre className="mt-1 max-h-40 overflow-auto font-mono text-[11px] leading-5 text-muted">
                {JSON.stringify(row.data, null, 2)}
              </pre>
            ) : null}
          </li>
        ))}
      </ol>
    </div>
  );
}

function groupChunkEvents(events: SessionEvent[]): TrajectoryRow[] {
  const rows: TrajectoryRow[] = [];
  let index = 0;
  while (index < events.length) {
    const event = events[index];
    if (event.type !== "assistant/chunk") {
      rows.push({
        key: String(event.seq),
        type: event.type,
        seqLabel: `#${event.seq}`,
        data: event.data,
      });
      index += 1;
      continue;
    }

    const chunks: SessionEvent[] = [];
    while (index < events.length && events[index].type === "assistant/chunk") {
      chunks.push(events[index]);
      index += 1;
    }

    const first = chunks[0];
    const last = chunks[chunks.length - 1];
    if (chunks.length === 1) {
      rows.push({
        key: String(first.seq),
        type: first.type,
        seqLabel: `#${first.seq}`,
        data: first.data,
      });
      continue;
    }

    const text = chunks.map(chunkText).join("");
    const thinking = chunks.map(chunkThinking).join("");
    const data: Record<string, unknown> = {
      count: chunks.length,
      seq: [first.seq, last.seq],
    };
    if (text) data.text = text;
    if (thinking) data.thinking = thinking;

    rows.push({
      key: `chunk-${first.seq}-${last.seq}`,
      type: "assistant/chunk",
      seqLabel: `#${first.seq}–${last.seq}`,
      count: chunks.length,
      data,
    });
  }
  return rows;
}

function chunkInner(event: SessionEvent): { event: unknown } {
  return { event: event.data.chunk };
}

function chunkText(event: SessionEvent): string {
  return extractTextDelta(chunkInner(event));
}

function chunkThinking(event: SessionEvent): string {
  return extractThinkingDelta(chunkInner(event));
}
