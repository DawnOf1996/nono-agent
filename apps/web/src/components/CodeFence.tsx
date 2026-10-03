import { Check, Copy } from "lucide-react";
import { Children, Fragment, isValidElement, useEffect, useState, type ReactNode } from "react";
import type { ThemedToken } from "shiki/core";

import { highlightCode } from "@/features/files/preview/highlight";
import { languageForFence, type CodeLanguage } from "@/features/files/preview/languages";
import { useResolvedTheme, type ResolvedTheme } from "@/lib/theme";

/** 超过这个长度的围栏直接当纯文本：流式时每个字都会让整块重跑一次词法分析，太长的块会拖住首字。 */
const MAX_HIGHLIGHT_LENGTH = 50_000;

const CACHE_LIMIT = 128;
const tokenCache = new Map<string, ThemedToken[][]>();

/**
 * 源码 → 带色的 token，会缓存。
 *
 * 同一段代码在会话里会被反复挂载（消息重渲染、切外观来回切、预览和聊天各渲染一次），
 * 缓存住省掉重复的词法分析。流式过程中每一帧的半截代码各占一条，128 条够滚完一整块。
 */
function highlight(text: string, language: CodeLanguage, theme: ResolvedTheme): Promise<ThemedToken[][] | null> {
  if (text.length > MAX_HIGHLIGHT_LENGTH) return Promise.resolve(null);
  // theme 和 language 都是不含 | 的定值，拿它当分隔符不会和 text 里的内容撞上。
  const key = `${theme}|${language}|${text}`;
  const hit = tokenCache.get(key);
  if (hit) return Promise.resolve(hit);
  return highlightCode(text, language, theme).then((tokens) => {
    if (tokens && tokenCache.size >= CACHE_LIMIT) {
      const oldest = tokenCache.keys().next();
      if (!oldest.done) tokenCache.delete(oldest.value);
    }
    if (tokens) tokenCache.set(key, tokens);
    return tokens;
  });
}

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

/**
 * shiki 的 token 里**没有**换行符：一行代码就是 tokens 数组里的一个元素，行尾的 \n 并不在
 * content 里。直接把所有 span 顺序铺出去，整块代码会挤成一行 —— 所以行与行之间要自己补回
 * \n，靠 .prose-md pre code 的 white-space: pre 还原。
 */
function HighlightedCode({ lines }: { lines: ThemedToken[][] }) {
  return (
    <code>
      {lines.map((line, lineIndex) => (
        <Fragment key={lineIndex}>
          {line.map((token, tokenIndex) => (
            <span key={tokenIndex} style={token.color ? { color: token.color } : undefined}>
              {token.content}
            </span>
          ))}
          {lineIndex < lines.length - 1 ? "\n" : null}
        </Fragment>
      ))}
    </code>
  );
}

/** 对话里的围栏代码：语言名在左，复制在右。 */
export function CodeFence({ children }: { children: ReactNode }) {
  const { language, text } = fenceMeta(children);
  const lang = languageForFence(language);
  const theme = useResolvedTheme();
  const [tokens, setTokens] = useState<ThemedToken[][] | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (lang === undefined) {
      setTokens(null);
      return;
    }
    let live = true;
    setTokens(null);
    void highlight(text, lang, theme).then((next) => {
      if (live) setTokens(next);
    });
    return () => {
      live = false;
    };
    // theme 也进依赖：切外观要重新高亮，缓存里存的是另一套配色。
  }, [lang, text, theme]);

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
      <pre>{tokens ? <HighlightedCode lines={tokens} /> : children}</pre>
    </div>
  );
}
