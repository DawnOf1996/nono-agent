import { Check, Copy } from "lucide-react";
import { Children, isValidElement, useEffect, useState, type ReactNode } from "react";

function nodeText(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return nodeText(node.props.children);
  return "";
}

function fenceMeta(children: ReactNode): { language: string; text: string } {
  const child = Children.toArray(children)[0];
  if (!isValidElement<{ className?: string; children?: ReactNode }>(child)) {
    return { language: "text", text: nodeText(children).replace(/\n$/, "") };
  }
  const match = /\blanguage-(\S+)/i.exec(child.props.className ?? "");
  return {
    language: (match?.[1] ?? "text").toLowerCase(),
    text: nodeText(child.props.children).replace(/\n$/, ""),
  };
}

/** 对话里的围栏代码：语言名在左，复制在右。 */
export function CodeFence({ children }: { children: ReactNode }) {
  const { language, text } = fenceMeta(children);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1600);
    return () => window.clearTimeout(timer);
  }, [copied]);

  return (
    <div className="code-fence my-[0.85em] max-w-full overflow-hidden rounded-xl border border-line bg-code">
      {/* 头部和代码体同色，只靠一条极淡的线分开：语言名/复制不至于浮在代码上。 */}
      <div className="flex items-center justify-between gap-3 border-b border-line/70 px-3.5 pt-2.5 pb-1.5 select-none">
        <span className="min-w-0 truncate text-[13px] text-muted">{language}</span>
        <button
          type="button"
          className="inline-flex shrink-0 cursor-pointer items-center gap-1 rounded-md px-1.5 py-1 text-[13px] text-muted select-none hover:bg-hover hover:text-ink focus-visible:ring-2 focus-visible:ring-ink/15 focus-visible:outline-none"
          title={copied ? "已复制" : "复制代码"}
          onClick={() => {
            void navigator.clipboard.writeText(text).then(() => setCopied(true)).catch(() => setCopied(false));
          }}
        >
          {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
          {copied ? "已复制" : "复制"}
        </button>
      </div>
      <pre>{children}</pre>
    </div>
  );
}
