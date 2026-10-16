import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";

import { writeClipboardText } from "@/lib/clipboard";
import { toast } from "@/lib/toast";

/**
 * 「复制这段文本」按钮 —— 代码块、事件数据块这类地方共用一个。
 *
 * **走 :func:`writeClipboardText` 而不是 ``navigator.clipboard``**：打包版是
 * Electron，渲染进程里的标准剪贴板 API 要过权限请求，而主进程没有设权限处理器。
 * 经 preload 桥落到主进程的 ``clipboard`` 才稳。
 *
 * 复制成功后就地变成对勾再退回 —— 不弹 toast。按钮就在手边，弹一条全局提示
 * 反而把视线从内容上拽走；真失败才值得说一句，因为那意味着用户以为复制成功了。
 */
export function CopyButton({
  text,
  label,
  compact = false,
  className,
}: {
  text: string;
  /** 无障碍名 + hover 提示。 */
  label: string;
  /** 只留图标。窄行（事件列表之类）放不下「复制」两个字。 */
  compact?: boolean;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  async function copy() {
    try {
      await writeClipboardText(text);
      setCopied(true);
    } catch (err) {
      setCopied(false);
      toast(err instanceof Error ? err.message : "复制失败");
    }
  }

  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={copied ? "已复制" : label}
      aria-label={label}
      className={
        "inline-flex shrink-0 cursor-pointer select-none items-center rounded-md text-muted transition-colors " +
        "hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/15 focus-visible:outline-none " +
        (compact ? "p-1" : "gap-1 px-1.5 py-1 text-[13px]") +
        (className ? ` ${className}` : "")
      }
    >
      {copied ? <Check className="size-3.5 text-add" /> : <Copy className="size-3.5" />}
      {compact ? null : copied ? "已复制" : "复制"}
    </button>
  );
}
