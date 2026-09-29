import { memo, useMemo } from "react";
import type { Components } from "react-markdown";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { CodeFence } from "@/components/CodeFence";
import { SvgFigure, svgBlockSource } from "@/components/SvgFigure";
import { MermaidDiagram, mermaidBlockSource } from "@/features/files/preview/markdown/MermaidDiagram";
import {
  isUnclosedMermaidFence,
  isUnclosedSvgFence,
  normalizeGluedHeadingTables,
  normalizeGluedMermaidFences,
} from "@/lib/mermaid-fence";

const remarkPlugins = [remarkGfm];

const linkComponents: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
};

const components: Components = {
  ...linkComponents,
  pre: ({ children }) => {
    const mermaid = mermaidBlockSource(children);
    if (mermaid !== null) return <MermaidDiagram code={mermaid} />;
    const svg = svgBlockSource(children);
    if (svg !== null) return <SvgFigure markup={svg} />;
    return <CodeFence>{children}</CodeFence>;
  },
};

const FENCE_OPEN = /^ {0,3}(`{3,}|~{3,})/;
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;
const LIST_ITEM = /^ {0,3}(?:[-*+]|\d{1,9}[.)])\s/;
const QUOTE = /^ {0,3}>/;
const INDENTED = /^(?: {4,}|\t)/;

function closesFence(line: string, fence: string): boolean {
  const match = FENCE_CLOSE.exec(line);
  return Boolean(match && match[1][0] === fence[0] && match[1].length >= fence.length);
}

function continuesPrevious(block: string, previous: string): boolean {
  if (INDENTED.test(block)) return true;
  if (LIST_ITEM.test(block) && LIST_ITEM.test(previous)) return true;
  if (QUOTE.test(block) && QUOTE.test(previous)) return true;
  return false;
}

/**
 * 按顶层块切分 markdown：围栏代码整块保留，空行分段，缩进/列表/引用的续段并回上一块。
 *
 * 流式追加时只有最后一块在变，前面的块字符串保持不变，配合 memo 就不用重新解析整篇。
 */
export function splitMarkdownBlocks(text: string): string[] {
  const blocks: string[] = [];
  let buffer: string[] = [];
  let fence: string | null = null;

  const flush = () => {
    if (!buffer.length) return;
    const block = buffer.join("\n");
    buffer = [];
    const previous = blocks[blocks.length - 1];
    if (previous !== undefined && continuesPrevious(block, previous)) {
      blocks[blocks.length - 1] = `${previous}\n\n${block}`;
      return;
    }
    blocks.push(block);
  };

  for (const line of text.split("\n")) {
    if (fence) {
      buffer.push(line);
      if (closesFence(line, fence)) {
        fence = null;
        flush();
      }
      continue;
    }
    const opening = FENCE_OPEN.exec(line);
    if (opening) {
      flush();
      buffer.push(line);
      fence = opening[1];
      continue;
    }
    if (!line.trim()) {
      if (buffer.length) flush();
      continue;
    }
    buffer.push(line);
  }
  flush();
  return blocks;
}

const MarkdownChunk = memo(function MarkdownChunk({
  source,
  deferMermaid = false,
}: {
  source: string;
  deferMermaid?: boolean;
}) {
  return (
    <Markdown remarkPlugins={remarkPlugins} components={deferMermaid ? linkComponents : components}>
      {source}
    </Markdown>
  );
});

export const MarkdownText = memo(function MarkdownText({
  text,
  tone = "body",
  streaming = false,
}: {
  text: string;
  tone?: "body" | "muted";
  /** 正在流式追加：分块渲染，让已写完的部分不再重复解析。 */
  streaming?: boolean;
}) {
  // react-markdown 不包外层元素，分块后仍是 .prose-md 的直接子节点，样式与整篇渲染一致。
  const prepared = useMemo(
    () => normalizeGluedHeadingTables(normalizeGluedMermaidFences(text)),
    [text],
  );
  const blocks = useMemo(() => (streaming ? splitMarkdownBlocks(prepared) : null), [streaming, prepared]);

  return (
    <div className={tone === "muted" ? "prose-md prose-md-muted" : "prose-md"}>
      {blocks ? (
        blocks.map((source, index) => (
          <MarkdownChunk
            key={index}
            source={source}
            deferMermaid={isUnclosedMermaidFence(source) || isUnclosedSvgFence(source)}
          />
        ))
      ) : (
        <MarkdownChunk source={prepared} />
      )}
    </div>
  );
});
