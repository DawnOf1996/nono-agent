import { Fragment, memo, useEffect, useRef, useState, type ReactNode } from "react";

import { countChanges, diffLines, parseUnifiedDiff, splitLines, type DiffLine } from "@/lib/line-diff";
import { cn } from "@/lib/utils";
import type { ChatToolCall } from "@/store/chat";

/** 文件类工具：卡片正文按这三类分别渲染，其余工具仍走输入/输出两节。 */
export type FileToolKind = "read" | "write" | "edit";

const FILE_TOOLS: Record<string, FileToolKind> = {
  read_file: "read",
  write_file: "write",
  edit_file: "edit",
};

const VERB: Record<FileToolKind, string> = {
  read: "读取",
  write: "写入",
  edit: "编辑",
};

/**
 * 折叠时首尾各给这么多行，中间用一行按钮交代折叠了多少。
 *
 * 读/写/编辑三种正文一律 5 + 折叠 + 5：文件类正文动辄几百行，全展开会把一整屏对话
 * 淹掉，而只看头部又会漏掉文件尾部的收尾（import、闭合括号、导出清单这些），所以两头
 * 各露一点，中间那段折起来。
 */
const PEEK_HEAD = 5;
const PEEK_TAIL = 5;

export function fileToolKind(tool: ChatToolCall): FileToolKind | null {
  return FILE_TOOLS[tool.name] ?? null;
}

export function fileToolVerb(kind: FileToolKind): string {
  return VERB[kind];
}

/** 卡片正文的数据。读是行号+原文，写/编辑是行级差异。 */
export type FileToolView =
  | { kind: "read"; start: number; lines: string[]; stats: null }
  | { kind: "diff"; lines: DiffLine[]; stats: { added: number; removed: number }; note?: string }
  /** 覆盖写但差异太大、后端没附：给纯预览，不拿增删配色糊弄。 */
  | { kind: "plain"; note: string; lines: string[]; stats: { added: number; removed: number } }
  /** 调用失败：文件没动过，只回错误，不画"打算怎么改"。 */
  | { kind: "error"; message: string; stats: null };

/** write_file 结果尾巴里的 `(overwrite: +12/-3)`，后端在 tools/builtin/write.py 里拼的。 */
const OVERWRITE_STATS = /\(overwrite: \+(\d+)\/-(\d+)\)/;

/**
 * 从一次工具调用里取出卡片要显示的东西。
 *
 * 失败优先：edit_file 报错时 old_string 根本没匹配上、write_file 报错时文件也没换，
 * 这时候拿入参算出来的差异是"打算怎么改"，画出来等于报假账。所以先看 is_error，
 * 有错就只回错误文本。
 *
 * 有后端 UI 附件（ui/tool-result 事件）就用它：那是整文件的真实差异，行号可信。
 * 没有的才退回本地推导（老会话没有这条事件）。
 *
 * read 的结果自带行号（`  12  内容`，宽度按最大行号对齐），这里把行号剥掉换成
 * 自己的 gutter：结果里的行号是给模型看的排版，跟着 offset 变，直接显示会让同一
 * 文件两次读取的行号对不上。
 *
 * write 的本地兜底有三条路：后端附了差异就渲染；只留了统计说明差异太大，就纯预览；
 * 新建文件（结果以 "(new file)" 结尾）整篇都是新增，自己算就行。
 * edit 两端都在入参里，本地算。
 */
export function fileToolView(tool: ChatToolCall, kind: FileToolKind): FileToolView | null {
  const input = tool.input ?? {};
  if (tool.isError) {
    return { kind: "error", message: tool.output ?? "调用失败", stats: null };
  }
  if (kind === "read") {
    if (tool.output === undefined) return null;
    const raw = tool.output.split("\n");
    const first = raw.find((line) => /^\s*\d+ {2}/.test(line));
    const width = first ? (/^\s*\d+ {2}/.exec(first)?.[0].length ?? 0) : 0;
    const start = first ? Number(/^\s*(\d+)/.exec(first)?.[1] ?? 1) : 1;
    const lines: string[] = [];
    for (const line of raw) {
      // 结尾 "... [showing lines 1-131 of 131]" 是给模型的截断说明，不进卡片。
      if (line.startsWith("... [showing lines")) continue;
      lines.push(width > 0 ? line.slice(width) : line);
    }
    return { kind: "read", start, lines, stats: null };
  }

  if (kind === "write") {
    const content = typeof input.content === "string" ? input.content : "";
    const fromUi = uiDiff(tool);
    if (fromUi) return fromUi;
    const output = tool.output ?? "";
    const parsed = parseUnifiedDiff(output);
    if (parsed) return { kind: "diff", lines: parsed, stats: countChanges(parsed) };
    const stats = OVERWRITE_STATS.exec(output);
    if (stats) {
      const added = Number(stats[1]);
      const removed = Number(stats[2]);
      return {
        kind: "plain",
        note: `整文件覆盖，+${added}/-${removed}；差异过大，这里只给新内容`,
        lines: splitLines(content),
        stats: { added, removed },
      };
    }
    // 新建文件：整份内容按新增渲染。
    const lines = splitLines(content).map((text, index) => ({
      kind: "add" as const,
      text,
      newNo: index + 1,
    }));
    return { kind: "diff", lines, stats: countChanges(lines) };
  }

  const fromUi = uiDiff(tool);
  if (fromUi) return fromUi;
  const oldString = typeof input.old_string === "string" ? input.old_string : "";
  const newString = typeof input.new_string === "string" ? input.new_string : "";
  const lines = diffLines(splitLines(oldString), splitLines(newString));
  return { kind: "diff", lines, stats: countChanges(lines) };
}

/**
 * 后端算好的整文件差异 —— 唯一可信的行号来源。
 *
 * 它走 ``ui/tool-result`` 事件（ignorable）送到界面，不进模型上下文。行号来自
 * unified diff 的 @@ 头，就是文件里的真实行号；本地拿 old_string/new_string 算
 * 只能得到片段内的相对编号，跨行编辑还会整体错位。
 * 老会话没有这条事件（也没走过上一版的文字契约），这时才退回本地算法。
 */
function uiDiff(tool: ChatToolCall): FileToolView | null {
  const ui = tool.ui;
  if (!ui || ui.kind !== "file_diff") return null;
  if (ui.diff) {
    const lines = parseUnifiedDiff(ui.diff);
    if (lines) {
      return { kind: "diff", lines, stats: { added: ui.added, removed: ui.removed } };
    }
  }
  // 后端把差异判为过大（或没附）：给纯预览，不拿增删配色糊弄。
  const content = typeof (tool.input ?? {}).content === "string"
    ? ((tool.input ?? {}).content as string)
    : typeof (tool.input ?? {}).new_string === "string"
      ? ((tool.input ?? {}).new_string as string)
      : "";
  return {
    kind: "plain",
    note: `+${ui.added}/-${ui.removed}；差异过大，这里只给新内容`,
    lines: splitLines(content),
    stats: { added: ui.added, removed: ui.removed },
  };
}

/** 复制按钮的内容：读=原文，写=新内容，编辑=替换后的片段。 */
export function fileToolText(tool: ChatToolCall, kind: FileToolKind): string {
  const input = tool.input ?? {};
  if (kind === "read") return tool.output ?? "";
  if (kind === "write") return typeof input.content === "string" ? input.content : "";
  return typeof input.new_string === "string" ? input.new_string : "";
}

/**
 * 卡片头里的路径：整条右对齐，文件名那一头永远留在右缘（照 DSH PathLabel）。
 *
 * 目录段用 ellipsis 截断，只有真被截断时才叠一层左缘渐隐：渐隐是无条件挂的话，
 * 短目录（如 `runtime-test/`）那 28px 直接盖在正常文字上，整段看着发灰断掉。
 * 挂上渐隐的判据是 scrollWidth > clientWidth，按容器宽度变化重算。
 *
 * 给了 onOpen 时文件名变成可点：在侧栏开一个文件预览标签页。整行头本身是展开/收起的
 * 按钮，文件名不能是 <button>（按钮套按钮不是合法 HTML，点击还会带着外层一起触发），
 * 所以用 span + role="button"，并挡掉冒泡。
 */
export const ToolPathLabel = memo(function ToolPathLabel({
  path,
  onOpen,
}: {
  path: string;
  onOpen?: (path: string) => void;
}) {
  const dirRef = useRef<HTMLSpanElement | null>(null);
  const [faded, setFaded] = useState(false);

  // 按最后一段分隔符切，不做 \\ → / 的归一：卡片上要显示工具给的原样路径。
  const cut = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
  const dir = cut > 0 ? path.slice(0, cut + 1) : "";
  const name = cut > 0 ? path.slice(cut + 1) : path;

  // 路径和容器宽度都会变（侧栏拖动、窗口缩放、换一条路径），都得以此重算。
  useEffect(() => {
    const el = dirRef.current;
    if (!el) return;
    const sync = () => setFaded(el.scrollWidth > el.clientWidth + 1);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(el);
    return () => observer.disconnect();
  }, [dir]);

  const open = (event: { preventDefault: () => void; stopPropagation: () => void }) => {
    // 别让这次点击顺手把卡片展开/收起，也别在按钮上选中文本。
    event.preventDefault();
    event.stopPropagation();
    onOpen?.(path);
  };

  if (!path) return null;
  return (
    <span
      className="flex min-w-0 flex-1 justify-end overflow-hidden text-[12px] whitespace-nowrap"
      title={onOpen ? `${path}\n点击在侧栏打开` : path}
    >
      {/* 目录弱、文件名实：一眼扫过去落在哪、叫什么。 */}
      <span
        ref={dirRef}
        className={cn(
          "min-w-0 truncate text-faint",
          faded && "[mask-image:linear-gradient(to_right,transparent,black_28px)]",
        )}
      >
        {dir}
      </span>
      {onOpen ? (
        <span
          role="button"
          tabIndex={0}
          title="在侧栏打开这个文件"
          className="shrink-0 cursor-pointer rounded-sm text-muted underline decoration-transparent underline-offset-2 outline-none hover:bg-hover hover:text-ink hover:decoration-current focus-visible:bg-hover focus-visible:text-ink focus-visible:decoration-current"
          onClick={open}
          onKeyDown={(event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            open(event);
          }}
        >
          {name}
        </span>
      ) : (
        <span className="shrink-0 text-muted">{name}</span>
      )}
    </span>
  );
});

export function DiffStats({ stats }: { stats: { added: number; removed: number } | null }) {
  if (!stats || (stats.added === 0 && stats.removed === 0)) return null;
  return (
    <span className="shrink-0 font-mono text-[12px]">
      {stats.added > 0 ? <span className="text-add">+{stats.added}</span> : null}
      {stats.added > 0 && stats.removed > 0 ? <span className="px-1 text-faint"> </span> : null}
      {stats.removed > 0 ? <span className="text-del">-{stats.removed}</span> : null}
    </span>
  );
}

/** 一行差异：左两列是旧/新行号，右侧是 +-/空格 标记和正文。 */
function DiffRow({ line }: { line: DiffLine }) {
  return (
    <div
      className={cn(
        "flex px-3 py-px",
        line.kind === "add" && "bg-add-soft text-add-deep-ink shadow-[inset_3px_0_0_var(--color-add)]",
        line.kind === "del" && "bg-del-soft text-del-deep-ink shadow-[inset_3px_0_0_var(--color-del)]",
        line.kind === "same" && "text-muted",
      )}
    >
      <span className="w-9 shrink-0 pr-1.5 text-right text-faint/80 tabular-nums select-none">
        {line.oldNo ?? ""}
      </span>
      <span className="w-9 shrink-0 pr-1.5 text-right text-faint/80 tabular-nums select-none">
        {line.newNo ?? ""}
      </span>
      <span className="select-none opacity-60">
        {line.kind === "add" ? "+ " : line.kind === "del" ? "- " : "  "}
      </span>
      <span className="min-w-0 flex-1 break-all whitespace-pre-wrap">{line.text || "\u200b"}</span>
    </div>
  );
}

/** 一行读取结果：行号靠右排版、不参与选中（和 DSH ReadBlock 的 gutter 一样）。 */
function ReadRow({ no, text }: { no: number; text: string }) {
  return (
    <div className="flex px-3 py-px">
      <span className="w-10 shrink-0 pr-2 text-right text-faint tabular-nums select-none">{no}</span>
      <span className="min-w-0 flex-1 break-words whitespace-pre-wrap text-ink/90">
        {text || "\u200b"}
      </span>
    </div>
  );
}

/**
 * 折叠正文：默认首尾各 head/tail 行，中间一行按钮交代折叠了多少行，可整段展开。
 *
 * 渲染交给调用方按 (行, 行序号) 出：读要按行号排版、差异要按 +/- 配色，折叠时行序号
 * 仍得是原来的序号，所以这里给的是 index 而不是切片。
 */
function FoldBody<T>({
  lines,
  head,
  tail,
  render,
}: {
  lines: readonly T[];
  head: number;
  tail: number;
  render: (line: T, index: number) => ReactNode;
}) {
  const [expanded, setExpanded] = useState(false);
  const hidden = lines.length - head - tail;
  const rows = (from: number, to: number) =>
    lines.slice(from, to).map((line, offset) => <Fragment key={from + offset}>{render(line, from + offset)}</Fragment>);

  // 不足两头之和的短正文没有可折的部分，直接全给。
  if (hidden <= 0) return <>{rows(0, lines.length)}</>;

  if (expanded) {
    return (
      <>
        {rows(0, lines.length)}
        <FoldToggle label="收起" onClick={() => setExpanded(false)} />
      </>
    );
  }
  return (
    <>
      {rows(0, head)}
      <FoldToggle label={`… 折叠 ${hidden} 行`} onClick={() => setExpanded(true)} />
      {rows(lines.length - tail, lines.length)}
    </>
  );
}

/** 折叠处的那一行：上下各一条虚线，当成被折起来那段的边界。 */
function FoldToggle({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="w-full cursor-pointer border-y border-dashed border-line/70 px-3 py-1 text-left font-sans text-[11px] text-faint hover:bg-hover hover:text-muted"
      aria-expanded={label === "收起"}
      onClick={onClick}
    >
      {label}
    </button>
  );
}

/** 工具卡片的正文：读=带行号的预览，写/编辑=行级差异。 */
export const FileToolBody = memo(function FileToolBody({ view }: { view: FileToolView }) {
  if (view.kind === "read") {
    return (
      <div className="font-mono text-[12px] leading-[18px]">
        <FoldBody
          lines={view.lines}
          head={PEEK_HEAD}
          tail={PEEK_TAIL}
          render={(text, index) => <ReadRow no={view.start + index} text={text} />}
        />
      </div>
    );
  }
  if (view.kind === "plain") {
    return (
      <div className="font-mono text-[12px] leading-[18px]">
        <p className="px-3 pb-1 font-sans text-[11px] text-faint">{view.note}</p>
        <FoldBody
          lines={view.lines}
          head={PEEK_HEAD}
          tail={PEEK_TAIL}
          render={(text, index) => (
            <div className="flex px-3 py-px text-ink/90">
              {/* 只剩新内容，行号就是新文件的 1..N。 */}
              <span className="w-9 shrink-0 pr-1.5 text-right text-faint/80 tabular-nums select-none">
                {index + 1}
              </span>
              <span className="min-w-0 flex-1 break-all whitespace-pre-wrap">{text || "\u200b"}</span>
            </div>
          )}
        />
      </div>
    );
  }
  if (view.kind === "error") {
    return (
      <p className="px-3 py-2 font-mono text-[12px] leading-[18px] break-words whitespace-pre-wrap text-danger">
        {view.message}
      </p>
    );
  }
  return (
    <div className="font-mono text-[12px] leading-[18px]">
      {view.kind === "diff" && view.note ? (
        <p className="px-3 pb-1 font-sans text-[11px] text-faint">{view.note}</p>
      ) : null}
      <FoldBody
        lines={view.lines}
        head={PEEK_HEAD}
        tail={PEEK_TAIL}
        render={(line) => <DiffRow line={line} />}
      />
    </div>
  );
});
