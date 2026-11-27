import type { TurnChanges } from "@nono/protocol";
import { ChevronDown, ChevronRight, ChevronUp, FileCode2, Files } from "lucide-react";
import { memo, useState } from "react";

import { DiffStat, StatusBadge, statusLetter } from "@/features/files/diff/DiffStat";
import { usePanelStore } from "@/features/files/panel-store";
import { baseName } from "@/lib/workspace";
import type { ChatMessage } from "@/store/chat";

/** 折叠时显示的行数。 */
const COLLAPSED_ROWS = 4;

/** 回复对应的那一轮文件变更：流式中按 changesId，历史消息按事件序号区间。 */
export function turnForMessage(turns: TurnChanges[] | undefined, message: ChatMessage): TurnChanges | undefined {
  if (!turns?.length || message.role !== "assistant") return undefined;
  if (message.changesId) return turns.find((turn) => turn.id === message.changesId);
  const seq = message.seq;
  if (typeof seq !== "number") return undefined;
  return turns.find((turn) => seq > turn.start_seq && seq <= turn.end_seq);
}

function title(turn: TurnChanges): string {
  if (turn.total === 1) {
    const file = turn.files[0];
    const name = baseName(file.display);
    if (file.status === "added") return `新建了 ${name}`;
    if (file.status === "deleted") return `删除了 ${name}`;
    return `修改了 ${name}`;
  }
  return `修改了 ${turn.total} 个文件`;
}

/** 回复末尾的文件变更卡片：点标题或文件行在右侧栏打开这一轮的对比。 */
export const TurnChangesCard = memo(function TurnChangesCard({ turn }: { turn: TurnChanges }) {
  const [expanded, setExpanded] = useState(false);
  const openDiff = usePanelStore((state) => state.openDiff);
  const showChanges = usePanelStore((state) => state.showChanges);
  const single = turn.total === 1 ? turn.files[0] : undefined;
  const foldable = turn.files.length > COLLAPSED_ROWS;
  const rows = foldable && !expanded ? turn.files.slice(0, COLLAPSED_ROWS) : turn.files;

  function open(index: number) {
    const file = turn.files[index];
    openDiff(
      { source: "turn", sessionId: turn.session_id, turnId: turn.id, index },
      file ? baseName(file.display) : "本轮改动",
    );
  }

  return (
    <div className="mt-3 overflow-hidden rounded-xl border border-line bg-bg" data-turn-changes={turn.id}>
      <button
        type="button"
        className="group flex w-full min-w-0 items-center gap-2.5 bg-sidebar px-3 py-2.5 text-left hover:bg-hover"
        title={single ? single.abs_path : "在右侧栏查看这一轮的改动"}
        onClick={() => open(0)}
      >
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-line bg-bg text-muted">
          {single ? <FileCode2 className="size-4" /> : <Files className="size-4" />}
        </span>
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-[13px] font-medium text-ink">{title(turn)}</span>
          <span className="flex items-center gap-2 text-[11px] text-faint">
            <DiffStat
              added={turn.added}
              deleted={turn.deleted}
              binary={single?.binary}
              oversized={single?.oversized}
            />
            <span className="hidden group-hover:inline">查看对比</span>
          </span>
        </span>
        <ChevronRight className="size-4 shrink-0 text-faint" />
      </button>
      {single ? null : (
        <ul className="border-t border-line">
          {rows.map((file, index) => (
            <li key={`${file.display}-${index}`}>
              <button
                type="button"
                title={file.abs_path}
                className="flex w-full min-w-0 items-center gap-2 px-3 py-1.5 text-left hover:bg-hover"
                onClick={() => open(index)}
              >
                <StatusBadge letter={statusLetter(file.status)} />
                <span className="min-w-0 flex-1 truncate text-[12px] text-muted">{file.display}</span>
                <DiffStat added={file.added} deleted={file.deleted} binary={file.binary} oversized={file.oversized} />
              </button>
            </li>
          ))}
        </ul>
      )}
      {foldable || !single ? (
        <div className="flex items-center border-t border-line">
          {foldable ? (
            <button
              type="button"
              className="flex items-center gap-1 px-3 py-1.5 text-[12px] text-faint hover:text-ink"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? "收起" : `显示全部 ${turn.files.length} 个文件`}
              {expanded ? <ChevronUp className="size-3.5" /> : <ChevronDown className="size-3.5" />}
            </button>
          ) : null}
          <span className="flex-1" />
          <button
            type="button"
            className="px-3 py-1.5 text-[12px] text-faint hover:text-ink"
            onClick={() => showChanges("session")}
          >
            所有改动
          </button>
        </div>
      ) : null}
    </div>
  );
});
