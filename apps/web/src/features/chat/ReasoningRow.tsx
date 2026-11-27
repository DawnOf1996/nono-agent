import {
  Check,
  ChevronRight,
  CircleAlert,
  FileText,
  FolderOpen,
  Globe,
  ListTodo,
  Loader2,
  PenLine,
  Search,
  Terminal,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { MarkdownText } from "@/components/MarkdownText";
import { CopyButton } from "@/features/chat/CopyButton";
import {
  DiffStats,
  FileToolBody,
  ToolPathLabel,
  fileToolKind,
  fileToolVerb,
  fileToolView,
} from "@/features/chat/FileToolBlock";
import { usePanelStore } from "@/features/files/panel-store";
import { formatElapsed } from "@/lib/format";
import { joinWorkspace } from "@/lib/workspace";
import type { ChatStep, ChatToolCall } from "@/store/chat";

/** 工具给的路径可能是工作区相对路径，侧栏按绝对路径取文件，这里补上工作区。 */
function resolveToolPath(root: string, path: string): string {
  if (!path) return "";
  const absolute = /^[a-zA-Z]:[\\/]/.test(path) || path.startsWith("/") || path.startsWith("\\\\");
  if (absolute || !root) return path;
  return joinWorkspace(root, path);
}

function pickString(input: Record<string, unknown>, keys: string[]): string {
  for (const key of keys) {
    const value = input[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "";
}

function basename(path: string): string {
  const normalized = path.replace(/\\/g, "/");
  return normalized.split("/").pop() || path;
}

export function toolTitle(tool: ChatToolCall): string {
  const input = tool.input ?? {};
  const file = pickString(input, ["path", "file_path", "target_file", "target"]);
  const command = pickString(input, ["command"]);
  const pattern = pickString(input, ["pattern", "glob", "query"]);
  switch (tool.name) {
    case "bash":
      return "运行命令";
    case "read_file":
      return file ? `查看文件: ${basename(file)}` : "查看文件";
    case "list_directory":
      return file ? `列出目录: ${file}` : "列出目录";
    case "write_file":
      return file ? `写入文件: ${basename(file)}` : "写入文件";
    case "grep":
      return pattern ? `搜索: ${pattern}` : "搜索";
    case "glob":
      return pattern ? `匹配文件: ${pattern}` : "匹配文件";
    case "web_search":
      return pattern ? `网页搜索: ${pattern}` : "网页搜索";
    case "web_fetch":
      return "获取网页";
    case "todo_write":
      return "更新任务";
    case "edit_file":
      return file ? `编辑文件: ${basename(file)}` : "编辑文件";
    default:
      if (file) return `运行 ${tool.name}: ${basename(file)}`;
      if (command) return `运行 ${tool.name}`;
      if (pattern) return `运行 ${tool.name}: ${pattern}`;
      return `运行 ${tool.name}`;
  }
}

/** 工具名 → 图标。折叠行右侧那个小图标，让扫读时能一眼分清是命令还是读写文件。 */
function toolIcon(name: string): LucideIcon {
  switch (name) {
    case "bash":
      return Terminal;
    case "read_file":
      return FileText;
    case "list_directory":
      return FolderOpen;
    case "write_file":
    case "edit_file":
      return PenLine;
    case "grep":
    case "glob":
      return Search;
    case "web_search":
    case "web_fetch":
      return Globe;
    case "todo_write":
      return ListTodo;
    default:
      return Wrench;
  }
}

function RowToggle({
  label,
  open,
  onToggle,
  chevron = true,
  dot,
}: {
  label: ReactNode;
  open: boolean;
  onToggle: () => void;
  chevron?: boolean;
  dot?: string;
}) {
  return (
    <button
      type="button"
      className="flex w-full min-w-0 items-center gap-2 text-left text-faint hover:text-muted"
      onClick={onToggle}
    >
      <span className={dot ?? "size-1.5 shrink-0 rounded-full border border-faint"} />
      <span className="min-w-0 truncate">{label}</span>
      {chevron ? (
        <ChevronRight className={`size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
      ) : null}
    </button>
  );
}

/**
 * 深度思考折叠块。
 *
 * `startOpen` 是"流式中"的意思，不是"父级展开"：运行中强制展开（配合 `follow`
 * 贴底），一旦落定就自己收起来，用户随后点开「已思考」看到的是折叠态而不是一大坨
 * 思考全文。落定那一刻用户手动开过就尊重用户，不再自动收。
 */
function ThinkingWrap({
  text,
  startOpen = false,
  follow = false,
}: {
  text: string;
  startOpen?: boolean;
  follow?: boolean;
}) {
  const [open, setOpen] = useState(startOpen || follow);
  const touchedRef = useRef(false);
  const wasStreamingRef = useRef(startOpen || follow);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    const streaming = startOpen || follow;
    if (streaming) {
      setOpen(true);
    } else if (wasStreamingRef.current && !touchedRef.current) {
      setOpen(false);
    }
    wasStreamingRef.current = streaming;
  }, [startOpen, follow]);

  useEffect(() => {
    if (follow) stickRef.current = true;
  }, [follow]);

  useLayoutEffect(() => {
    const el = scrollerRef.current;
    if (!el || !open || !follow || !stickRef.current) return;
    el.scrollTop = el.scrollHeight;
  }, [text, open, follow]);

  if (!text.trim()) return null;

  return (
    <div>
      <RowToggle
        label="深度思考"
        open={open}
        onToggle={() => {
          touchedRef.current = true;
          setOpen((value) => !value);
        }}
      />
      {open ? (
        <div
          ref={scrollerRef}
          className="mt-1 max-h-48 overflow-auto border-l border-line pl-3"
          onScroll={() => {
            const el = scrollerRef.current;
            if (!el) return;
            stickRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
          }}
        >
          <MarkdownText text={text} tone="muted" streaming={follow} />
        </div>
      ) : null}
    </div>
  );
}

/** 工具行状态：结果没落定是「运行中」，落定了按 is_error 分成功/失败。 */
type ToolState = "running" | "ok" | "error";

function toolState(tool: ChatToolCall): ToolState {
  if (tool.output === undefined) return "running";
  return tool.isError ? "error" : "ok";
}

/**
 * 命令功能描述 —— 模型写在 ``description`` 里的一句人话（bash 工具必填）。
 * 折叠行显示它，展开时的输入区就不再重复。
 */
function toolDescription(tool: ChatToolCall): string {
  return pickString(tool.input ?? {}, ["description"]);
}

/** bash 的参数里只有这三个走「命令」展示，其余（command/workdir/timeout）标在标题行上。 */
const BASH_COMMAND_FIELDS = new Set(["command", "workdir", "timeout"]);

/** 展开后的输入：bash 给命令本身，其余工具给参数 JSON。描述已在折叠行显示，不重复。 */
function toolInputText(tool: ChatToolCall): string {
  const input = tool.input ?? {};
  const rest = { ...input };
  delete rest.description;
  if (tool.name === "bash") {
    const extras = Object.keys(rest).filter((key) => !BASH_COMMAND_FIELDS.has(key));
    if (extras.length) return JSON.stringify(rest, null, 2);
    return pickString(input, ["command"]);
  }
  return Object.keys(rest).length ? JSON.stringify(rest, null, 2) : "";
}

/** 命令行输入区的补充说明：工作目录与超时。 */
function toolInputHint(tool: ChatToolCall): string {
  if (tool.name !== "bash") return "";
  const input = tool.input ?? {};
  const parts: string[] = [];
  const workdir = pickString(input, ["workdir"]);
  if (workdir) parts.push(`工作目录 ${workdir}`);
  const timeout = input.timeout;
  if (typeof timeout === "number" && Number.isFinite(timeout)) parts.push(`超时 ${timeout}s`);
  return parts.join(" · ");
}

/** 输入/输出正文区。超长就自己滚，不把整条对话撑开。 */
function ToolSectionScroll({ value, tone }: { value: string; tone?: "danger" }) {
  return (
    <div className="max-h-72 overflow-auto overscroll-contain">
      <pre
        className={`px-3 py-2.5 font-mono text-[12px] leading-[1.65] whitespace-pre-wrap break-words ${
          tone === "danger" ? "text-danger" : "text-ink/85"
        }`}
      >
        {value}
      </pre>
    </div>
  );
}

/** 展开后的一节：小标题条（标签 / 提示 / 复制）+ 正文区。 */
function ToolSection({
  label,
  value,
  hint,
  copyTitle,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  copyTitle: string;
  tone?: "danger";
}) {
  return (
    <section className="overflow-hidden rounded-lg border border-line/80 bg-code/50">
      <header
        className={`flex items-center gap-2 border-b border-line/70 px-3 py-1.5 ${
          tone === "danger" ? "bg-del-soft/50" : "bg-sidebar/60"
        }`}
      >
        <span
          className={`shrink-0 text-[11px] font-medium tracking-wide ${
            tone === "danger" ? "text-danger" : "text-muted"
          }`}
        >
          {label}
        </span>
        {hint ? <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{hint}</span> : <span className="flex-1" />}
        <span className="shrink-0 text-faint hover:text-ink">
          <CopyButton text={value} title={copyTitle} />
        </span>
      </header>
      <ToolSectionScroll value={value} tone={tone} />
    </section>
  );
}

const ToolRunRow = memo(function ToolRunRow({ tool }: { tool: ChatToolCall }) {
  const [open, setOpen] = useState(false);
  const state = toolState(tool);
  const input = toolInputText(tool);
  const hint = toolInputHint(tool);
  const output = tool.output ?? "";
  const description = toolDescription(tool);
  const title = toolTitle(tool);
  const Icon = toolIcon(tool.name);

  // 文件类工具（读/写/编辑）换一套卡片：头部给路径与增删统计，正文给行级差异或带行号的预览。
  // 其余工具保持输入/输出两节，展开的还是参数 JSON。
  const fileKind = fileToolKind(tool);
  const filePath = fileKind ? pickString(tool.input ?? {}, ["path", "file_path"]) : "";
  const view = useMemo(
    () => (fileKind ? fileToolView(tool, fileKind) : null),
    // 依赖 tool 本身：流式期间同一个 tool 对象被就地补上 output/results，
    // 换成按字段订阅反而会漏掉那次补写。
    [tool, fileKind],
  );
  const hasBody = fileKind ? view !== null || tool.output !== undefined : Boolean(input) || tool.output !== undefined;
  const openFile = usePanelStore((state) => state.openFile);
  const root = usePanelStore((state) => state.root);
  const bumpRevision = usePanelStore((state) => state.bumpRevision);

  // 点文件名在侧栏开一个文件预览标签页。写/编辑刚动过磁盘，bump 一下让已开的同文件
  // 标签页重新取内容，不然还停在打开时那一版。
  const openInPanel = useCallback(
    (path: string) => {
      const full = resolveToolPath(root, path);
      if (!full) return;
      if (fileKind === "write" || fileKind === "edit") bumpRevision();
      openFile(full, basename(full));
    },
    [bumpRevision, fileKind, openFile, root],
  );

  return (
    <div
      data-tool-state={state}
      className={`overflow-hidden rounded-lg border transition-colors ${
        state === "error"
          ? "border-del-deep bg-del-soft/40"
          : open
            ? "border-line-strong bg-card/60"
            : "border-line/70 bg-card/40 hover:border-line-strong hover:bg-card/70"
      }`}
    >
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-2.5 px-2.5 py-1.5 text-left select-none"
        aria-expanded={open && hasBody}
        onClick={() => hasBody && setOpen((value) => !value)}
      >
        <span
          className={`flex size-5 shrink-0 items-center justify-center rounded-[5px] ${
            state === "error" ? "bg-del-soft text-danger" : "bg-code text-faint"
          }`}
        >
          {state === "running" ? (
            <Loader2 className="size-3 animate-spin" />
          ) : state === "error" ? (
            <CircleAlert className="size-3.5" />
          ) : (
            <Icon className="size-3.5" />
          )}
        </span>
        {fileKind ? (
          <>
            <span className="shrink-0 text-[13px] text-ink">{fileToolVerb(fileKind)}</span>
            <ToolPathLabel path={filePath} onOpen={filePath ? openInPanel : undefined} />
            <DiffStats stats={view?.stats ?? null} />
          </>
        ) : (
          <span className="min-w-0 flex-1 truncate text-[13px] text-ink">{title}</span>
        )}
        {!fileKind && description ? (
          <span className="hidden min-w-0 max-w-[45%] truncate text-[12px] text-faint sm:inline">{description}</span>
        ) : null}
        {state === "ok" ? <Check className="size-3 shrink-0 text-faint/70" /> : null}
        {hasBody ? (
          <ChevronRight className={`size-3.5 shrink-0 text-faint transition-transform ${open ? "rotate-90" : ""}`} />
        ) : null}
      </button>
      {open && hasBody ? (
        <div className="border-t border-line/70 p-2.5">
          {fileKind ? (
            <>
              {view ? <FileToolBody view={view} /> : null}
              {tool.output === undefined ? (
                <div className="flex items-center gap-2 px-3 py-2.5 font-mono text-[12px] text-faint">
                  <Loader2 className="size-3 animate-spin" />
                  运行中…
                </div>
              ) : null}
            </>
          ) : (
            <div className="space-y-2">
              {input ? (
                <ToolSection label="输入" value={input} hint={hint || undefined} copyTitle="复制输入" />
              ) : null}
              {tool.output === undefined ? (
                <div className="flex items-center gap-2 px-3 py-2.5 font-mono text-[12px] text-faint">
                  <Loader2 className="size-3 animate-spin" />
                  运行中…
                </div>
              ) : (
                <ToolSection
                  label="输出"
                  value={output}
                  copyTitle="复制输出"
                  tone={state === "error" ? "danger" : undefined}
                />
              )}
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
});

/** 已经落定的中间步骤不该跟着流式分片重渲染。 */
const StepBody = memo(function StepBody({ step, reveal }: { step: ChatStep; reveal: boolean }) {
  return (
    <div className="space-y-2">
      {step.thinking ? <ThinkingWrap text={step.thinking} startOpen={reveal} /> : null}
      {step.text ? <MarkdownText text={step.text} tone="muted" /> : null}
      {step.tools?.length ? (
        <div className="flex flex-col gap-1.5">
          {step.tools.map((tool, index) => (
            <ToolRunRow key={`${tool.name}-${index}`} tool={tool} />
          ))}
        </div>
      ) : null}
    </div>
  );
});

const ElapsedClock = memo(function ElapsedClock({
  active,
  durationMs,
}: {
  active: boolean;
  durationMs?: number;
}) {
  const nodeRef = useRef<HTMLSpanElement>(null);
  const startedRef = useRef<number | null>(null);
  const lastMsRef = useRef(0);

  useEffect(() => {
    const write = (ms: number) => {
      if (nodeRef.current) nodeRef.current.textContent = `（用时 ${formatElapsed(ms)}）`;
    };
    if (!active) {
      const ms = durationMs && durationMs > 0 ? durationMs : lastMsRef.current;
      if (ms > 0) write(ms);
      return;
    }
    if (startedRef.current == null) startedRef.current = performance.now();
    const started = startedRef.current;
    let raf = 0;
    const tick = () => {
      lastMsRef.current = performance.now() - started;
      write(lastMsRef.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [active, durationMs]);

  return <span ref={nodeRef} />;
});

export function ReasoningRow({
  thinking,
  steps,
  liveText,
  running,
  durationMs,
}: {
  thinking?: string;
  steps?: ChatStep[];
  liveText?: string;
  running: boolean;
  durationMs?: number;
}) {
  const [expanded, setExpanded] = useState(running);
  const hasSteps = Boolean(steps?.some((step) => step.text || step.thinking || step.tools?.length));
  const hasBody = Boolean(thinking?.trim() || liveText?.trim() || hasSteps);

  useEffect(() => {
    if (running) setExpanded(true);
  }, [running]);

  if (!hasBody && !running) return null;

  return (
    <div className="mb-3 text-[13px] leading-6 text-muted" data-state={running ? "running" : "ok"}>
      <button
        type="button"
        className="flex w-full min-w-0 items-center gap-1.5 text-left select-none hover:text-ink"
        onClick={() => setExpanded((value) => !value)}
      >
        <ChevronRight className={`size-3.5 shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`} />
        <span>
          {running ? "思考中" : "已思考"}
          <ElapsedClock active={running} durationMs={durationMs} />
        </span>
      </button>
      {expanded ? (
        <div className="mt-2 space-y-3 pl-[22px]">
          {steps?.map((step) => (
            // reveal 只喂"还在流式"：跑的时候思考跟着展开，落定后默认收起，
            // 用户点开「已思考」看到的是折叠态。
            <StepBody key={step.id} step={step} reveal={running} />
          ))}
          {thinking?.trim() ? (
            <ThinkingWrap text={thinking} startOpen={running} follow={running} />
          ) : null}
          {liveText?.trim() ? <MarkdownText text={liveText} tone="muted" streaming={running} /> : null}
          {running && !hasBody ? <div className="text-faint">…</div> : null}
        </div>
      ) : null}
    </div>
  );
}
