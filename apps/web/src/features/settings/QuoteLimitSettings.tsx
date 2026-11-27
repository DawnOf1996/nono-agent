import { useSyncExternalStore } from "react";

import { MAX_QUOTES, MAX_QUOTES_LIMIT, MAX_QUOTES_MIN, resolveMaxQuotes } from "@/lib/quote";
import { getSidebarPrefs, subscribeSidebarPrefs, updateSidebarPrefs } from "@/features/settings/sidebar-prefs";

/** 输入框里一次最多能挂多少条引用/评论。 */
export function QuoteLimitField() {
  const prefs = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs);
  const value = resolveMaxQuotes(prefs.maxQuotes);
  return (
    <div className="flex items-center gap-2">
      <input
        type="number"
        min={MAX_QUOTES_MIN}
        max={MAX_QUOTES_LIMIT}
        value={value}
        aria-label="引用条数上限"
        onChange={(event) => {
          const next = Number(event.target.value);
          // 输入框清空时 event.target.value 是空串，Number 得到 NaN —— 别写进去。
          if (!Number.isFinite(next)) return;
          updateSidebarPrefs({ maxQuotes: resolveMaxQuotes(next) });
        }}
        className="h-8 w-20 rounded-lg border border-line bg-bg px-2 text-[13px] text-ink outline-none focus:border-active tabular-nums"
      />
      <span className="text-[12px] text-muted">条</span>
    </div>
  );
}

export const DEFAULT_QUOTE_LIMIT = MAX_QUOTES;