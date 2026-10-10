import { Check, Copy } from "lucide-react";
import { useEffect, useState } from "react";
import type { ThemedToken } from "shiki/core";

import { highlightCode } from "@/features/files/preview/highlight";
import { useResolvedTheme } from "@/lib/theme";
import { languageForPath } from "@/features/files/preview/languages";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

function plainLines(text: string): string[] {
  const lines = text.split("\n");
  if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function SourceLines({
  lines,
  wrap,
}: {
  lines: Array<string | ThemedToken[]>;
  wrap: boolean;
}) {
  return (
    <div className="min-w-full px-3 py-3 font-mono text-[12.5px] leading-6">
      {lines.map((line, index) => (
        // 整行挂 data-line-number：选中代码里的文字时，选区端点往上找就能拿到行号。
        <div key={index} className="flex min-w-full" data-line-number={index + 1}>
          <span className="sticky left-0 w-11 shrink-0 select-none bg-bg pr-3 text-right text-[11px] text-faint tabular-nums">
            {index + 1}
          </span>
          <span className={wrap ? "min-w-0 whitespace-pre-wrap break-words" : "whitespace-pre"}>
            {typeof line === "string" ? (
              line.length > 0 ? line : "\n"
            ) : line.length === 0 ? (
              "\n"
            ) : (
              line.map((token, tokenIndex) => (
                <span key={tokenIndex} style={token.color ? { color: token.color } : undefined}>
                  {token.content}
                </span>
              ))
            )}
          </span>
        </div>
      ))}
    </div>
  );
}

export function CodeBody({ root, path, content, wrap }: DocumentPreviewProps) {
  const [tokens, setTokens] = useState<ThemedToken[][] | null>(null);
  const [copied, setCopied] = useState(false);
  const text = content.kind === "text" ? content.text : "";
  const language = languageForPath(path);
  const theme = useResolvedTheme();

  useEffect(() => {
    if (content.kind !== "text" || language === undefined) return;
    let live = true;
    setTokens(null);
    void highlightCode(content.text, language, theme).then((next) => {
      if (live) setTokens(next);
    });
    return () => {
      live = false;
    };
    // theme 也进依赖：切外观要重新高亮，缓存里存的是另一套配色。
  }, [content, language, theme]);

  useEffect(() => {
    if (!copied) return;
    const timer = window.setTimeout(() => setCopied(false), 1500);
    return () => window.clearTimeout(timer);
  }, [copied]);

  if (content.kind !== "text") return null;
  const lines = tokens ?? plainLines(text);

  return (
    <div
      className="relative h-full"
      data-wrap={wrap}
      data-quote-source="file"
      data-file-path={path}
      data-file-root={root}
    >
      <button
        type="button"
        className="absolute right-2 top-2 z-10 flex h-7 items-center gap-1 rounded-md bg-bg/90 px-2 text-[12px] text-muted hover:bg-hover hover:text-ink"
        onClick={() => {
          void navigator.clipboard.writeText(text).then(() => setCopied(true)).catch(() => setCopied(false));
        }}
      >
        {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
        {copied ? "已复制" : "复制"}
      </button>
      <div className="h-full overflow-auto">
        <SourceLines lines={lines} wrap={wrap} />
      </div>
    </div>
  );
}
