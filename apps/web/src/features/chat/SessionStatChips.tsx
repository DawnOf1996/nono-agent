import { Clock, Database, Gauge } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import { Dropdown } from "@/components/ui/dropdown";
import {
  formatDurationCn,
  formatPercent,
  formatTokenCount,
  formatTokensCompact,
  formatTps,
} from "@/lib/usage";
import type { SessionStats } from "@nono/protocol";

function Row({ label, value, swatch }: { label: string; value: string; swatch?: string }) {
  return (
    <div className="flex items-center justify-between gap-6 py-[3px]">
      <span className="flex items-center gap-1.5 text-[12px] whitespace-nowrap text-faint">
        {swatch ? <span className={`size-2 shrink-0 rounded-[2px] ${swatch}`} /> : null}
        {label}
      </span>
      <span className="truncate text-[12px] tabular-nums text-muted" title={value}>
        {value}
      </span>
    </div>
  );
}

function Chip({
  icon,
  open,
  onOpenChange,
  label,
  panel,
}: {
  icon: ReactNode;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: ReactNode;
  panel: ReactNode;
}) {
  return (
    <Dropdown
      open={open}
      onOpenChange={onOpenChange}
      align="left"
      placement="top"
      panelClassName="w-72 px-3 py-2.5"
      trigger={
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[11px] text-muted hover:bg-hover hover:text-ink"
          aria-expanded={open}
          onClick={() => onOpenChange(!open)}
        >
          {icon}
          {label}
        </button>
      }
    >
      {panel}
    </Dropdown>
  );
}

function PanelHead({ icon, title, value }: { icon: ReactNode; title: string; value?: string }) {
  return (
    <div className="flex items-center justify-between gap-6 border-b border-line pb-2">
      <span className="flex items-center gap-1.5 text-[12px] font-medium text-ink">
        {icon}
        {title}
      </span>
      {value ? <span className="text-[12px] font-medium tabular-nums text-ink">{value}</span> : null}
    </div>
  );
}

/** 「—」：服务端没给这个数（老日志 / 没开流式分片），别拿 0 冒充。 */
const NONE = "—";

function tpsLabel(tps: number | null): string {
  return tps != null ? formatTps(tps) : NONE;
}

function TurnStatChip({ stats }: { stats: SessionStats }) {
  const [open, setOpen] = useState(false);
  return (
    <Chip
      open={open}
      onOpenChange={setOpen}
      icon={<Clock className="size-3 shrink-0 text-faint" />}
      label={
        <span className="tabular-nums">
          {stats.turns}轮 {stats.steps}步
          {stats.tps != null ? ` · ${formatTps(stats.tps)}` : ""}
        </span>
      }
      panel={
        <>
          <PanelHead icon={<Clock className="size-3.5 shrink-0" />} title="会话统计" />
          <div className="pt-1.5">
            <Row label="模型用时" value={formatDurationCn(stats.model_duration_ms)} />
            <Row label="工具调用用时" value={formatDurationCn(stats.tool_duration_ms)} />
            <Row
              label="首 token 平均 (TTFT)"
              value={stats.ttft_ms != null ? formatDurationCn(stats.ttft_ms) : NONE}
            />
            <Row label="输出速度 (TPS)" value={tpsLabel(stats.tps)} />
          </div>
        </>
      }
    />
  );
}

function TokenStatChip({ stats }: { stats: SessionStats }) {
  const [open, setOpen] = useState(false);
  const { usage } = stats;
  const hit = usage.cache_hit_ratio;
  return (
    <Chip
      open={open}
      onOpenChange={setOpen}
      icon={<Database className="size-3 shrink-0 text-faint" />}
      label={
        <span className="tabular-nums">
          {formatTokensCompact(usage.total_tokens)} tok
          {hit != null ? ` · 缓存命中 ${formatPercent(hit)}` : ""}
        </span>
      }
      panel={
        <>
          <PanelHead
            icon={<Database className="size-3.5 shrink-0" />}
            title="Token 用量"
            value={`${formatTokenCount(usage.total_tokens)} tok`}
          />
          <div className="pt-1.5">
            {hit != null ? <Row label="缓存命中" value={formatPercent(hit)} /> : null}
            <Row label="未缓存输入" value={`${formatTokenCount(usage.input_tokens)} tok`} />
            <Row label="缓存读取" value={`${formatTokenCount(usage.cache_read_input_tokens)} tok`} />
            <Row label="输出" value={`${formatTokenCount(usage.output_tokens)} tok`} />
          </div>
        </>
      }
    />
  );
}

function ContextStatChip({ stats }: { stats: SessionStats }) {
  const [open, setOpen] = useState(false);
  const context = stats.context;
  // 还没有任何请求（全新会话）时没有上下文快照，但角标仍占位，
  // 免得底部这排东西忽有忽无。
  const ratio = context?.usage_ratio ?? 0;
  const system = context?.system_prompt_tokens ?? null;
  const tools = context?.tools_tokens ?? null;
  const messages = context?.messages_tokens ?? null;
  return (
    <Chip
      open={open}
      onOpenChange={setOpen}
      icon={<Gauge className="size-3 shrink-0 text-faint" />}
      label={
        <span className="tabular-nums">{context ? formatPercent(ratio) : NONE}</span>
      }
      panel={
        <>
          <div className="flex items-center justify-between gap-6 border-b border-line pb-2">
            <span className="flex items-baseline gap-1.5 text-[12px] text-ink">
              <Gauge className="size-3.5 shrink-0 self-center" />
              上下文已用
              <span className="font-medium tabular-nums">{formatPercent(ratio)}</span>
            </span>
            <span className="text-[12px] tabular-nums text-muted">
              {context
                ? `~${formatTokensCompact(context.total_tokens)} / ${formatTokensCompact(context.context_window)}`
                : NONE}
            </span>
          </div>
          <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-hover">
            <div
              className="h-full rounded-full bg-accent transition-[width] duration-300"
              style={{ width: context ? `${Math.max(1, Math.min(1, ratio) * 100)}%` : "0%" }}
            />
          </div>
          {/* 三个分项只有新日志才有；老日志服务端给 null，这里就不编数字。 */}
          {system != null ? (
            <div className="pt-1.5">
              <Row label="系统提示词" value={`~${formatTokensCompact(system)}`} swatch="bg-faint" />
              <Row label="工具定义" value={`~${formatTokensCompact(tools ?? 0)}`} swatch="bg-muted" />
              <Row label="对话消息" value={`~${formatTokensCompact(messages ?? 0)}`} swatch="bg-ink" />
            </div>
          ) : null}
        </>
      }
    />
  );
}

/** 还没有会话、或者统计还没回来时的占位 —— 角标照常显示，只是数都是 0。 */
const EMPTY_STATS: SessionStats = {
  turns: 0,
  steps: 0,
  model_duration_ms: 0,
  tool_duration_ms: 0,
  ttft_ms: null,
  tps: null,
  model: null,
  usage: {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_input_tokens: 0,
    cache_creation_input_tokens: 0,
    total_tokens: 0,
    cache_hit_ratio: null,
  },
  context: null,
};

/**
 * 输入框下方的会话级角标：会话统计 / Token 用量 / 上下文占用，点开看明细。
 *
 * 数字全部来自服务端（GET /session/{id}/stats，由事件流推导），前端只做格式化，
 * 不自己算 —— 这样重开历史会话和实时显示走的是同一套口径。
 *
 * 常驻显示：还没有数据时也占位，避免底部忽有忽无。
 */
export function SessionStatChips({ stats }: { stats?: SessionStats }) {
  const value = stats ?? EMPTY_STATS;
  return (
    <div className="flex flex-wrap items-center justify-center gap-1">
      <TurnStatChip stats={value} />
      <TokenStatChip stats={value} />
      <ContextStatChip stats={value} />
    </div>
  );
}
