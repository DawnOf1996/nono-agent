/** 一轮对话的 token 用量。四个桶互不重叠（对齐 Anthropic 口径）。 */
export type TurnTokens = {
  /** 未命中缓存的新鲜输入。 */
  inputTokens: number;
  outputTokens: number;
  /** 命中 prompt cache 读回来的输入。 */
  cacheReadTokens: number;
  /** 写入 prompt cache 的输入。 */
  cacheCreationTokens: number;
  /** 本轮实际调用的模型 id；多模型接力时取最后一次。 */
  model?: string;
};

/** 一轮的 token 总数 —— 四桶相加。 */
export function usageTotal(usage: TurnTokens): number {
  return (
    usage.inputTokens + usage.outputTokens + usage.cacheReadTokens + usage.cacheCreationTokens
  );
}

/**
 * 缓存命中率 = 读回 / (读回 + 新鲜输入)。
 *
 * 分母刻意不含 cacheCreation：那是「写」，不是「命中」，
 * 把写算进分母会把命中率算低。两桶都是 0 时返回 null（没有可算的输入）。
 */
export function cacheHitRatio(usage: TurnTokens): number | null {
  const read = usage.cacheReadTokens;
  const input = usage.inputTokens;
  const denominator = read + input;
  if (denominator <= 0) return null;
  return read / denominator;
}

/** 带千分位的完整数字：3,374,448。面板里要看到确切的量。 */
export function formatTokenCount(n: number): string {
  return Math.max(0, Math.round(n)).toLocaleString("en-US");
}

/** 紧凑写法：3.4M / 12.9M / 89.4k / 847。给一行摘要用。 */
export function formatTokensCompact(n: number): string {
  const value = Math.max(0, Math.round(n));
  if (value < 1000) return String(value);
  if (value < 1_000_000) return `${trimZero((value / 1000).toFixed(1))}k`;
  if (value < 1_000_000_000) return `${trimZero((value / 1_000_000).toFixed(1))}M`;
  return `${trimZero((value / 1_000_000_000).toFixed(1))}B`;
}

/** 「11分40秒」—— 时长用中文单位。 */
export function formatDurationCn(ms: number): string {
  const total = Math.max(0, ms) / 1000;
  if (total < 1) return `${Math.round(total * 1000)}毫秒`;
  if (total < 60) return `${trimZero(total.toFixed(1))}秒`;
  // 先把总秒数取整再拆，否则 119.7s 会拆出「1分60秒」。
  const rounded = Math.round(total);
  const minutes = Math.floor(rounded / 60);
  const rest = rounded % 60;
  if (minutes < 60) return rest ? `${minutes}分${rest}秒` : `${minutes}分`;
  return `${Math.floor(minutes / 60)}时${minutes % 60}分`;
}

function trimZero(value: string): string {
  return value.replace(/\.0$/, "");
}

/** 百分比：0.143 → 14%。 */
export function formatPercent(ratio: number): string {
  return `${Math.round(ratio * 100)}%`;
}

/** 「105 tok/s」。 */
export function formatTps(tps: number): string {
  return `${tps < 10 ? tps.toFixed(1) : Math.round(tps)} tok/s`;
}

