import { ChevronRight } from "lucide-react";
import { memo, useEffect, useLayoutEffect, useRef, useState } from "react";

import { MarkdownText } from "@/components/MarkdownText";
import { formatElapsed } from "@/lib/format";
import type { ChatStep, ChatToolCall } from "@/store/chat";

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

function RowToggle({
  label,
  open,
  onToggle,
  chevron = true,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  chevron?: boolean;
}) {
  return (
    <button
      type="button"
      className="flex w-full min-w-0 items-center gap-2 text-left text-faint hover:text-muted"
      onClick={onToggle}
    >
      <span className="size-1.5 shrink-0 rounded-full border border-faint" />
      <span className="min-w-0 truncate">{label}</span>
      {chevron ? (
        <ChevronRight className={`size-3.5 shrink-0 transition-transform ${open ? "rotate-90" : ""}`} />
      ) : null}
    </button>
  );
}

function ThinkingWrap({
  text,
  startOpen = false,
  follow = false,
}: {
  text: string;
  startOpen?: boolean;
  follow?: boolean;
}) {
  const [open, setOpen] = useState(startOpen);
  const scrollerRef = useRef<HTMLDivElement>(null);
  const stickRef = useRef(true);

  useEffect(() => {
    if (startOpen) setOpen(true);
  }, [startOpen]);

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
      <RowToggle label="深度思考" open={open} onToggle={() => setOpen((value) => !value)} />
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

const ToolRunRow = memo(function ToolRunRow({ tool }: { tool: ChatToolCall }) {
  const [open, setOpen] = useState(false);
  const detail =
    tool.name === "bash" && tool.input?.command
      ? String(tool.input.command)
      : tool.input
        ? JSON.stringify(tool.input, null, 2)
        : "";

  return (
    <div>
      <RowToggle
        label={toolTitle(tool)}
        open={open}
        chevron={Boolean(detail)}
        onToggle={() => detail && setOpen((value) => !value)}
      />
      {open && detail ? (
        <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words border-l border-line pl-3 font-mono text-[12px] leading-5 text-faint">
          {detail}
        </pre>
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
      {step.tools?.map((tool, index) => (
        <ToolRunRow key={`${tool.name}-${index}`} tool={tool} />
      ))}
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
            <StepBody key={step.id} step={step} reveal={expanded || running} />
          ))}
          {thinking?.trim() ? (
            <ThinkingWrap text={thinking} startOpen={expanded || running} follow={running} />
          ) : null}
          {liveText?.trim() ? <MarkdownText text={liveText} tone="muted" streaming={running} /> : null}
          {running && !hasBody ? <div className="text-faint">…</div> : null}
        </div>
      ) : null}
    </div>
  );
}
