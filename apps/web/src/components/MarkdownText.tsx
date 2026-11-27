import { memo, useMemo, type ComponentProps } from "react";
import type { Components } from "react-markdown";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";

import { CodeFence } from "@/components/CodeFence";
import { MarkdownLink } from "@/components/MarkdownLink";
import { SvgFigure, svgBlockSource } from "@/components/SvgFigure";
import { MermaidDiagram, mermaidBlockSource } from "@/features/files/preview/markdown/MermaidDiagram";
import { AlertBlock } from "@/features/files/preview/markdown/AlertBlock";
import { FrontmatterCard } from "@/features/files/preview/markdown/FrontmatterCard";
import { splitFrontmatter, splitOpenFrontmatterStart } from "@/features/files/preview/markdown/frontmatter";
import { KATEX_OPTIONS } from "@/lib/katex";
import { isUnclosedMathBlock, splitMarkdownBlocks } from "@/lib/markdown-blocks";
import {
  isUnclosedMermaidFence,
  isUnclosedSvgFence,
  normalizeGluedHeadingTables,
  normalizeGluedMermaidFences,
} from "@/lib/mermaid-fence";

const remarkPlugins = [remarkGfm, remarkMath];

type MarkdownProps = ComponentProps<typeof Markdown>;

/** 聊天侧不跑 sanitize，KaTeX 直接挂在管道末尾即可，没有预览那边的顺序约束。 */
const rehypePluginsMath: MarkdownProps["rehypePlugins"] = [[rehypeKatex, KATEX_OPTIONS]];

/**
 * 表格外面套一层横向滚动壳。
 *
 * react-markdown 只把 <table> 的 children（thead/tbody）交给组件，<table> 元素本身得
 * 补回去：只渲染 children 的话表格元素就没了，浏览器兜出一个匿名 table（width: auto），
 * 表格缩成内容宽，.prose-md table 的 width: 100% 也无处可落。
 *
 * CSS 里表格铺满正文列（width: 100%）。这层壳是兜底 —— 列多到 min-content 加起来都超过
 * 列宽时表格会溢出，那时横滚，别让溢出顶到消息列表上。
 * linkComponents 里也必须带：流式渲染会切到那套组件，缺了壳宽表会在流到一半时把整页撑宽。
 */
const tableComponents: Components = {
  table: ({ children }) => (
    <div className="table-scroll">
      <table>{children}</table>
    </div>
  ),
};

const linkComponents: Components = {
  ...tableComponents,
  // 不带 target：不带的话 <webview> 里的 target=_blank 会让桌面版另开一个原生窗口。
  // 点击由 App 上的全局代理接管（见 lib/use-link-router），网址开侧边栏浏览器、
  // 文件路径开侧边栏文件查看器。保留 href 是为了「复制链接地址」和
  // Ctrl/Cmd+点击（用户明确要新开窗口）仍然可用。
  a: ({ href, children, title }) => <MarkdownLink href={href} title={title}>{children}</MarkdownLink>,
  // `> [!NOTE]` 走 AlertBlock，不是提示块就退回普通 blockquote。
  // 和 a 一样必须两套组件都挂：流式渲染会切到 linkComponents，漏了的话
  // 流到一半的提示块会突然变回引用。
  blockquote: ({ children }) => <AlertBlock>{children}</AlertBlock>,
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

const MarkdownChunk = memo(function MarkdownChunk({
  source,
  deferMermaid = false,
  deferMath = false,
}: {
  source: string;
  deferMermaid?: boolean;
  deferMath?: boolean;
}) {
  return (
    <Markdown
      remarkPlugins={remarkPlugins}
      // 流式途中还没写完的 $$ 块先不挂 KaTeX：半截公式渲染出来是一片红字，
      // 跟着每个新 token 闪一次比晚点显示更难读。闭合了就正常挂上。
      rehypePlugins={deferMath ? undefined : rehypePluginsMath}
      components={deferMermaid ? linkComponents : components}
    >
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
  //
  // YAML 头先摘出去：模型贴一份 SKILL.md 时，首行 `---` 会变成 <hr>、结尾 `---` 会把
  // 整块顶成假 <h2>，`allowed-tools:` 下面的 `- Read` 还会把后面所有字段吞成列表项。
  // splitOpenFrontmatterStart 兜住流式的中途状态：结束分隔符还没到的那几十帧。
  const meta = useMemo(() => {
    const full = splitFrontmatter(text);
    if (full.present) return { raw: full.raw, body: full.body };
    return splitOpenFrontmatterStart(text);
  }, [text]);
  const prepared = useMemo(
    () => normalizeGluedHeadingTables(normalizeGluedMermaidFences(meta?.body ?? text)),
    [meta, text],
  );
  const blocks = useMemo(() => (streaming ? splitMarkdownBlocks(prepared) : null), [streaming, prepared]);

  return (
    <div className={tone === "muted" ? "prose-md prose-md-muted" : "prose-md"}>
      {meta ? <FrontmatterCard raw={meta.raw} variant="compact" /> : null}
      {blocks ? (
        blocks.map((source, index) => (
          <MarkdownChunk
            key={index}
            source={source}
            deferMermaid={isUnclosedMermaidFence(source) || isUnclosedSvgFence(source)}
            deferMath={isUnclosedMathBlock(source)}
          />
        ))
      ) : (
        <MarkdownChunk source={prepared} />
      )}
    </div>
  );
});
