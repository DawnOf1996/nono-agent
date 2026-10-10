import DOMPurify from "dompurify";
import { memo, useMemo, type ComponentProps } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { CodeFence } from "@/components/CodeFence";
import { MarkdownLink } from "@/components/MarkdownLink";
import { MdToc } from "@/features/files/preview/markdown/MdToc";
import { previewImageSrc } from "@/features/files/preview/markdown/media";
import { SvgFigure, svgBlockSource } from "@/components/SvgFigure";
import { MermaidDiagram, mermaidBlockSource } from "@/features/files/preview/markdown/MermaidDiagram";
import { segmentReadmeHtml } from "@/features/files/preview/markdown/segments";
import { FrontmatterCard } from "@/features/files/preview/markdown/FrontmatterCard";
import { splitFrontmatter } from "@/features/files/preview/markdown/frontmatter";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

const schema = {
  ...defaultSchema,
  tagNames: [...new Set([...(defaultSchema.tagNames ?? []), "div", "details", "summary", "center"])],
  attributes: {
    ...defaultSchema.attributes,
    div: [...(defaultSchema.attributes?.div ?? []), "align"],
    center: ["align"],
    img: [...(defaultSchema.attributes?.img ?? []), "src", "alt", "title", "width", "height", "align"],
    a: [...(defaultSchema.attributes?.a ?? []), "href", "title", "name"],
    details: ["open"],
  },
};

type MarkdownProps = ComponentProps<typeof Markdown>;
const remarkPlugins: MarkdownProps["remarkPlugins"] = [remarkGfm];
const rehypePlugins: MarkdownProps["rehypePlugins"] = [rehypeRaw, [rehypeSanitize, schema]];

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
  if (content.kind !== "text") return null;
  return (
    <div className="file-preview relative h-full overflow-auto px-4 py-4" data-link-file={path}>
      <MdToc />
      <div className="prose-md">
        {frontmatter?.present ? <FrontmatterCard raw={frontmatter.raw} variant="card" /> : null}
        {segments.map((segment, index) => {
          if (segment.kind === "details") {
            return (
              <details key={index} open={segment.open || undefined}>
                <summary dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(segment.summary) }} />
                <MarkdownChunk root={root} path={path} text={segment.body} />
              </details>
            );
          }
          if (segment.kind === "div") {
            return (
              <div key={index} style={segment.align ? { textAlign: segment.align } : undefined}>
                <MarkdownChunk root={root} path={path} text={segment.body} />
              </div>
            );
          }
          return <MarkdownChunk key={index} root={root} path={path} text={segment.text} />;
        })}
      </div>
    </div>
  );
});
