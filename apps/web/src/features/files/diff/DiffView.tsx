import type { DiffHunk, FileDiff } from "@nono/protocol";
import { memo, useEffect, useMemo, useState, type ReactNode } from "react";
import type { ThemedToken } from "shiki/core";

import {
  gapBefore,
  hunkHeader,
  hunkRows,
  renderedHunks,
  splitRows,
  MAX_RENDERED_LINES,
  type DiffRow,
  type InlineRange,
} from "@/features/files/diff/rows";
import { highlightCode } from "@/features/files/preview/highlight";
import { useResolvedTheme, type ResolvedTheme } from "@/lib/theme";
import { languageForPath } from "@/features/files/preview/languages";
import { cn } from "@/lib/utils";

export type DiffLayout = "unified" | "split";

type SideTokens = Map<number, ThemedToken[]>;
type HunkTokens = { old: SideTokens; new: SideTokens };

const ROW_TONE = {
  add: "bg-add-soft",
  del: "bg-del-soft",
  context: "",
} as const;

const GUTTER_TONE = {
  add: "bg-add-deep text-add-deep-ink",
  del: "bg-del-deep text-del-deep-ink",
  context: "text-faint",
} as const;

/** 整行仍是增绿、删红；配对后真正改掉的那一段用蓝色。 */
const INLINE_CHANGE = "bg-[color-mix(in_srgb,var(--color-inline-change)_85%,var(--color-ink))]";

async function highlightSide(
  rows: readonly DiffRow[],
  side: "old" | "new",
  language: string,
  theme: ResolvedTheme,
): Promise<SideTokens> {
  const source = rows.flatMap((row) => {
    const no = row[side];
    return no === undefined ? [] : [{ no, text: row.text }];
  });
  const map: SideTokens = new Map();
  if (!source.length) return map;
  const tokens = await highlightCode(source.map((line) => line.text).join("\n"), language, theme);
  if (!tokens) return map;
  source.forEach((line, index) => {
    const lineTokens = tokens[index];
    if (lineTokens && lineTokens.reduce((sum, token) => sum + token.content.length, 0) === line.text.length) {
      map.set(line.no, lineTokens);
    }
  });
  return map;
}

function useHunkTokens(path: string, rowsByHunk: readonly DiffRow[][]): HunkTokens[] | null {
  const [tokens, setTokens] = useState<HunkTokens[] | null>(null);
  const language = languageForPath(path);
  const theme = useResolvedTheme();
  useEffect(() => {
    setTokens(null);
    if (language === undefined) return;
    let live = true;
    void Promise.all(
      rowsByHunk.map(async (rows) => ({
        old: await highlightSide(rows, "old", language, theme),
        new: await highlightSide(rows, "new", language, theme),
      })),
    )
      .then((next) => {
        if (live) setTokens(next);
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [language, rowsByHunk, theme]);
  return tokens;
}

/** 按语法着色与行内改动区间切分一行文本。 */
function LineText({
  text,
  tokens,
  changed,
  kind,
}: {
  text: string;
  tokens?: ThemedToken[];
  changed?: InlineRange;
  kind: DiffRow["kind"];
}): ReactNode {
  if (!text) return <>{"\u200b"}</>;
  if (text.startsWith("\ufeff")) {
    if (kind === "context") return <LineText text={text.slice(1)} kind={kind} />;
    return (
      <>
        <span className="mr-1 rounded bg-card px-1 font-sans text-[10px] text-muted" title="UTF-8 字节顺序标记">
          BOM
        </span>
        <LineText text={text.slice(1)} kind={kind} />
      </>
    );
  }
  const source = tokens ?? [{ content: text } as ThemedToken];
  if (!tokens && !changed) return <>{text}</>;
  const pieces: ReactNode[] = [];
  let offset = 0;
  source.forEach((token, tokenIndex) => {
    const start = offset;
    const end = offset + token.content.length;
    offset = end;
    const style = token.color ? { color: token.color } : undefined;
    const cuts = [start, end];
    if (changed) {
      for (const edge of changed) if (edge > start && edge < end) cuts.push(edge);
    }
    cuts.sort((a, b) => a - b);
    for (let at = 0; at < cuts.length - 1; at += 1) {
      const from = cuts[at];
      const to = cuts[at + 1];
      if (to <= from) continue;
      const inChange = Boolean(changed && from >= changed[0] && to <= changed[1]);
      pieces.push(
        <span
          key={`${tokenIndex}-${from}`}
          style={style}
          className={inChange ? cn("rounded-[2px]", INLINE_CHANGE) : undefined}
        >
          {token.content.slice(from - start, to - start)}
        </span>,
      );
    }
  });
  return <>{pieces}</>;
}

function noteOf(diff: FileDiff): string | null {
  if (diff.kind === "binary") return "二进制文件，无法按行对比。";
  if (diff.kind === "oversized") return "文件过大，未载入对比内容。";
  if (!diff.before) return diff.hunks.length ? "新文件" : "新建了一个空文件";
  if (!diff.after) return "文件已删除";
  if (diff.status === "renamed" && diff.old_path) return `由 ${diff.old_path} 重命名`;
  if (diff.hunks.length === 0) return "内容没有变化";
  return null;
}

function HunkHeader({ hunks, index, full }: { hunks: DiffHunk[]; index: number; full: DiffHunk[] }) {
  const gap = gapBefore(full, index);
  return (
    <div className="sticky left-0 flex items-center gap-2 border-y border-diff-head-line bg-diff-head px-3 py-1 font-mono text-[11px] text-diff-head-ink">
      <span className="whitespace-pre">{hunkHeader(hunks[index])}</span>
      {gap > 0 ? <span className="shrink-0 font-sans text-faint">省略 {gap} 行</span> : null}
    </div>
  );
}

function UnifiedHunk({
  rows,
  tokens,
  wrap,
}: {
  rows: DiffRow[];
  tokens: HunkTokens | undefined;
  wrap: boolean;
}) {
  return (
    <>
      {rows.map((row, index) => (
        <div key={index} className={cn("flex min-w-full", ROW_TONE[row.kind])} data-diff-line={row.kind}>
          <span className={cn("w-10 shrink-0 select-none pr-1.5 text-right tabular-nums", GUTTER_TONE[row.kind])}>
            {row.old ?? ""}
          </span>
          <span className={cn("w-10 shrink-0 select-none pr-1.5 text-right tabular-nums", GUTTER_TONE[row.kind])}>
            {row.new ?? ""}
          </span>
          <span className={cn("w-5 shrink-0 select-none text-center", GUTTER_TONE[row.kind], "bg-transparent")}>
            {row.kind === "add" ? "+" : row.kind === "del" ? "-" : ""}
          </span>
          <span className={cn("min-w-0 flex-1 pr-3", wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre")}>
            <LineText
              text={row.text}
              kind={row.kind}
              changed={row.changed}
              tokens={row.kind === "add" ? tokens?.new.get(row.new ?? -1) : tokens?.old.get(row.old ?? -1)}
            />
          </span>
        </div>
      ))}
    </>
  );
}

function SplitHunk({
  rows,
  tokens,
  wrap,
}: {
  rows: DiffRow[];
  tokens: HunkTokens | undefined;
  wrap: boolean;
}) {
  const paired = useMemo(() => splitRows(rows), [rows]);
  return (
    <>
      {paired.map((row, index) => (
        <div key={index} className="grid min-w-full grid-cols-2">
          {(["left", "right"] as const).map((side) => {
            const cell = row[side];
            const kind = cell?.kind ?? "context";
            return (
              <div
                key={side}
                className={cn(
                  "flex min-w-0",
                  cell ? ROW_TONE[kind] : "diff-zebra",
                  side === "right" && "border-l border-line",
                )}
              >
                <span className={cn("w-10 shrink-0 select-none pr-1.5 text-right tabular-nums", GUTTER_TONE[kind])}>
                  {cell?.no ?? ""}
                </span>
                <span
                  className={cn(
                    "min-w-0 flex-1 px-2",
                    wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre",
                  )}
                >
                  {cell ? (
                    <LineText
                      text={cell.text}
                      kind={kind}
                      changed={cell.changed}
                      tokens={side === "left" ? tokens?.old.get(cell.no) : tokens?.new.get(cell.no)}
                    />
                  ) : (
                    "\u200b"
                  )}
                </span>
              </div>
            );
          })}
        </div>
      ))}
    </>
  );
}

/** 一个文件的对比：统一或并排、换行或横向滚动，按语言着色并标出行内改动。 */
export const DiffView = memo(function DiffView({
  diff,
  layout,
  wrap,
}: {
  diff: FileDiff;
  layout: DiffLayout;
  wrap: boolean;
}) {
  const { hunks, truncated } = useMemo(() => renderedHunks(diff.hunks), [diff.hunks]);
  const rowsByHunk = useMemo(() => hunks.map((hunk) => hunkRows(hunk)), [hunks]);
  const tokens = useHunkTokens(diff.path, rowsByHunk);
  const note = noteOf(diff);
  const hasAdds = diff.added > 0;
  const hasDels = diff.deleted > 0;
  const split = layout === "split" && hasAdds && hasDels;

  return (
    <div className="font-mono text-[12px] leading-[20px] text-ink">
      {note ? <p className="px-3 py-2 font-sans text-[12px] text-muted">{note}</p> : null}
      {diff.coarse ? (
        <p className="px-3 py-2 font-sans text-[12px] text-muted">文件较大，按整段替换显示。</p>
      ) : null}
      {truncated ? (
        <p className="px-3 py-2 font-sans text-[12px] text-muted">只显示前 {MAX_RENDERED_LINES} 行。</p>
      ) : null}
      <div className={cn(wrap ? "" : "w-max min-w-full")}>
        {hunks.map((_hunk, index) => (
          <section key={index}>
            <HunkHeader hunks={hunks} index={index} full={diff.hunks} />
            {split ? (
              <SplitHunk rows={rowsByHunk[index]} tokens={tokens?.[index]} wrap={wrap} />
            ) : (
              <UnifiedHunk rows={rowsByHunk[index]} tokens={tokens?.[index]} wrap={wrap} />
            )}
          </section>
        ))}
      </div>
    </div>
  );
});
