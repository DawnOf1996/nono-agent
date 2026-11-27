import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

/** 复制成功后停留多久再退回「复制」态。 */
const CONFIRM_MS = 1600;

/**
 * 复制到剪贴板。
 *
 * 用户和 AI 两侧的复制按钮长得一样，就共用这一个 —— 顺带给一点反馈，
 * 否则点了没反应，用户多半要点第二下。
 */
export function CopyButton({ text, title = "复制" }: { text: string; title?: string }) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), CONFIRM_MS);
    return () => window.clearTimeout(timer);
  }, [copied]);

  const label = copied ? "已复制" : title;
  return (
    <button
      type="button"
      className="text-faint hover:text-ink"
      title={label}
      aria-label={label}
      onClick={() => {
        // 剪贴板被拒（窗口无焦点/无权限）时静默退回：一次复制失败不该打断对话。
        void navigator.clipboard.writeText(text).then(() => setCopied(true)).catch(() => setCopied(false));
      }}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
    </button>
  );
}
