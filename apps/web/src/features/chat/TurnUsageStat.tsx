import type { CatalogSnapshot } from "@nono/protocol";
import { Database } from "lucide-react";
import { useMemo, useState } from "react";

import { Dropdown } from "@/components/ui/dropdown";
import {
  cacheHitRatio,
  formatTokenCount,
  formatTokensCompact,
  usageTotal,
  type TurnTokens,
} from "@/lib/usage";
import { useChatStore } from "@/store/chat";

/** 模型 id → ``提供方/模型``。目录里认不出就只给模型本身。 */
function modelLabel(model: string | undefined, catalog: CatalogSnapshot | null): string {
  if (!model) return "";
  for (const provider of catalog?.providers ?? []) {
    if (provider.models.some((item) => item.id === model)) return `${provider.id}/${model}`;
  }
  return model;
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-6 py-[3px]">
      <span className="text-[12px] whitespace-nowrap text-faint">{label}</span>
      <span className="truncate text-[12px] tabular-nums text-muted" title={value}>
        {value}
      </span>
    </div>
  );
}

/**
 * 一轮对话的用量角标：常态只显示总量，点开看四桶明细。
 *
 * 走 Dropdown 而不是自己定位：外面点击、Esc、滚动跟随、贴边翻转都现成，
 * 面板还走 portal，不会被消息列表的 overflow 裁掉。
 *
 * 数据来自事件流里按轮累加的 usage 事件（见 sse.ts），所以历史会话
 * 重开后每轮的数还在，而不是只剩一个全会话总数。
 */
export function TurnUsageStat({ usage }: { usage: TurnTokens }) {
  const catalog = useChatStore((state) => state.catalog);
  const [open, setOpen] = useState(false);
  const total = usageTotal(usage);
  const hit = cacheHitRatio(usage);
  const model = useMemo(() => modelLabel(usage.model, catalog), [usage.model, catalog]);

  if (total <= 0) return null;

  return (
    <Dropdown
      open={open}
      onOpenChange={setOpen}
      align="right"
      placement="top"
      panelClassName="w-64 px-3 py-2.5"
      trigger={
        <button
          type="button"
          className="inline-flex items-center gap-1 rounded-md border border-line px-1.5 py-0.5 text-[11px] text-muted hover:border-line-strong hover:text-ink"
          title="本轮用量明细"
          aria-label={`本轮用量 ${formatTokenCount(total)} tok`}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <Database className="size-3 shrink-0 text-faint" />
          <span className="tabular-nums">用量 {formatTokensCompact(total)} tok</span>
        </button>
      }
    >
      <div className="flex items-baseline justify-between gap-6 border-b border-line pb-1.5">
        <span className="flex items-center gap-1.5 text-[12px] text-ink">
          <Database className="size-3.5 shrink-0" />
          本轮用量
        </span>
        <span className="text-[12px] font-medium tabular-nums text-ink">
          {formatTokenCount(total)} tok
        </span>
      </div>
      <div className="pt-1.5">
        {model ? <Row label="提供方 / 模型" value={model} /> : null}
        {hit != null ? <Row label="缓存命中" value={`${(hit * 100).toFixed(1)}%`} /> : null}
        <Row label="未缓存输入" value={`${formatTokenCount(usage.inputTokens)} tok`} />
        <Row label="缓存读取" value={`${formatTokenCount(usage.cacheReadTokens)} tok`} />
        {usage.cacheCreationTokens > 0 ? (
          <Row label="缓存写入" value={`${formatTokenCount(usage.cacheCreationTokens)} tok`} />
        ) : null}
        <Row label="输出" value={`${formatTokenCount(usage.outputTokens)} tok`} />
      </div>
    </Dropdown>
  );
}
