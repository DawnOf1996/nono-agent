import DOMPurify from "dompurify";
import { memo, useMemo, type ComponentProps } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkMath from "remark-math";
import remarkGfm from "remark-gfm";

import { CodeFence } from "@/components/CodeFence";
import { MarkdownLink } from "@/components/MarkdownLink";
import { MdToc } from "@/features/files/preview/markdown/MdToc";
import { previewImageSrc } from "@/features/files/preview/markdown/media";
import { SvgFigure, svgBlockSource } from "@/components/SvgFigure";
import { MermaidDiagram, mermaidBlockSource } from "@/features/files/preview/markdown/MermaidDiagram";
import { AlertBlock } from "@/features/files/preview/markdown/AlertBlock";
import { segmentReadmeHtml } from "@/features/files/preview/markdown/segments";
import { frontmatterLineOffset } from "@/features/files/preview/markdown/line-numbers";
import { remarkLineNumbers } from "@/features/files/preview/markdown/remark-lines";
import { FrontmatterCard } from "@/features/files/preview/markdown/FrontmatterCard";
import { splitFrontmatter } from "@/features/files/preview/markdown/frontmatter";
import { markdownSchema } from "@/features/files/preview/markdown/schema";
import { rehypeMathLines } from "@/features/files/preview/markdown/rehype-math-lines";
import { KATEX_OPTIONS } from "@/lib/katex";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

/** 行号属性由 remarkLineNumbers 注入，sanitize 不放行的话到不了 DOM。 */

type MarkdownProps = ComponentProps<typeof Markdown>;
const remarkPlugins: MarkdownProps["remarkPlugins"] = [remarkGfm, remarkMath, remarkLineNumbers];

/**
 * rehypeKatex 必须排在 sanitize 之后。
 *
 * KaTeX 吐出来的标签和 class 非常多（span/math/mrow/semantics… 外加上百个 katex-* 类），
 * 放前面就得把整套 MathML 塞进白名单，白名单等于形同虚设。放后面则输出全部由 KaTeX
 * 自己拼，KATEX_OPTIONS 里 trust 关着就没有用户输入能进到 HTML 里，配合那道闸是安全的。
 * KaTeX 的选项和"为什么 trust 要写死"写在 lib/katex.ts，聊天侧共用同一份。
 */
const rehypePlugins: MarkdownProps["rehypePlugins"] = [
  rehypeRaw,
  [rehypeSanitize, markdownSchema],
  // 用 rehypeMathLines 而不是裸的 rehypeKatex：它渲染完会把行号还回去，
  // 选中公式里的文字做引用时才换得出源文件行号。见 rehype-math-lines.ts。
  [rehypeMathLines, KATEX_OPTIONS],
];

/**
 * components 按 root/path 缓存，不能每次渲染都新建。
 * 新建的话 react-markdown 每次拿到的都是全新的组件类型，React 会卸载重建整棵 pre 子树，
 * 代码块里的 mermaid/svg 跟着重画一遍；父组件因为切对话之类的原因重渲染一次，图就白画一次。
 */
const componentCache = new Map<string, Components>();

function previewComponents(root: string, filePath: string): Components {
  const key = `${root}\u0000${filePath}`;
  const hit = componentCache.get(key);
  if (hit) return hit;
  if (componentCache.size > 32) componentCache.clear();
  const created: Components = {
    // 和对话里同一个滚动壳。注意 <table> 本身要补回去：react-markdown 只给 children
    // （thead/tbody），少了 <table> 浏览器会兜一个匿名 table，表格就缩成内容宽了。
    table: ({ children }) => (
      <div className="table-scroll">
        <table>{children}</table>
      </div>
    ),
    pre: ({ children }) => {
      const mermaid = mermaidBlockSource(children);
      if (mermaid !== null) return <MermaidDiagram code={mermaid} />;
      const svg = svgBlockSource(children);
      if (svg !== null) return <SvgFigure markup={svg} />;
      // 和对话里同一个围栏组件：语言名 + 复制按钮 + 头部那条细线，预览和聊天长得一样。
      return <CodeFence>{children}</CodeFence>;
    },
    // `> [!NOTE]` 走 AlertBlock，不是提示块就退回普通 blockquote。
    blockquote: ({ children }) => <AlertBlock>{children}</AlertBlock>,
    img: ({ src, alt }) => {
      const resolved = previewImageSrc(root, filePath, typeof src === "string" ? src : undefined);
      if (!resolved) return alt ? <span>{alt}</span> : null;
      return <img src={resolved} alt={alt ?? ""} />;
    },
    // 不带 target：<webview> 里的 target=_blank 会让桌面版另开一个原生窗口。
    // 点击交给 lib/use-link-router 的全局代理，网址开侧边栏浏览器、文件路径
    // （含 ./README.md 这类相对路径）开侧边栏文件查看器。data-link-dir 让代理
    // 知道要把相对路径接在哪个文件目录下。
    a: ({ href, children, title }) => (
      <MarkdownLink href={href} title={title}>
        {children}
      </MarkdownLink>
    ),
  };
  componentCache.set(key, created);
  return created;
}

const MarkdownChunk = memo(function MarkdownChunk({ root, path, text }: { root: string; path: string; text: string }) {
  if (!text.trim()) return null;
  return (
    <Markdown remarkPlugins={remarkPlugins} rehypePlugins={rehypePlugins} components={previewComponents(root, path)}>
      {text}
    </Markdown>
  );
});

export const MarkdownBody = memo(function MarkdownBody({ root, path, content }: DocumentPreviewProps) {
  // 顺序有讲究：先摘 frontmatter 再分段。反过来的话，YAML 块标量里写的 HTML
  // （`description: |` 后面跟着 <details>）会被 segments 当成真的标签拆出来。
  const frontmatter = useMemo(() => (content.kind === "text" ? splitFrontmatter(content.text) : null), [content]);
  const segments = useMemo(() => (frontmatter ? segmentReadmeHtml(frontmatter.body) : []), [frontmatter]);
  // 分段时行号是相对 body 的；frontmatter 剥掉的头几行还得再补回来，
  // 补完才是源文件里的真实行号。挂到容器上，选区层靠它换算。
  const fmOffset = useMemo(
    () => (content.kind === "text" ? frontmatterLineOffset(content.text) : 0),
    [content],
  );
  if (content.kind !== "text") return null;
  return (
    <div
      className="file-preview relative h-full overflow-auto px-4 py-4"
      data-link-file={path}
      data-quote-source="file"
      data-file-path={path}
      data-file-root={root}
    >
      <MdToc />
      <div className="prose-md">
        {frontmatter?.present ? <FrontmatterCard raw={frontmatter.raw} variant="card" /> : null}
        {segments.map((segment, index) => {
          // 前端这层再叠一次 frontmatter 偏移，得到源文件行号基准。
          const lineOffset = segment.lineOffset + fmOffset;
          if (segment.kind === "details") {
            return (
              <details key={index} open={segment.open || undefined} data-line-offset={lineOffset}>
                <summary dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(segment.summary ?? "") }} />
                <MarkdownChunk root={root} path={path} text={segment.body ?? ""} />
              </details>
            );
          }
          if (segment.kind === "div") {
            return (
              <div
                key={index}
                style={segment.align ? { textAlign: segment.align } : undefined}
                data-line-offset={lineOffset}
              >
                <MarkdownChunk root={root} path={path} text={segment.body ?? ""} />
              </div>
            );
          }
          return (
            <div key={index} data-line-offset={lineOffset}>
              <MarkdownChunk root={root} path={path} text={segment.text ?? ""} />
            </div>
          );
        })}
      </div>
    </div>
  );
});
