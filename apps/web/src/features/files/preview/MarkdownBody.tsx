import DOMPurify from "dompurify";
import { memo, useMemo, type ComponentProps } from "react";
import Markdown from "react-markdown";
import type { Components } from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import { httpUrl } from "@/features/files/BrowserPane";
import { usePanelStore } from "@/features/files/panel-store";
import { MdToc } from "@/features/files/preview/markdown/MdToc";
import { previewImageSrc } from "@/features/files/preview/markdown/media";
import { SvgFigure, svgBlockSource } from "@/components/SvgFigure";
import { MermaidDiagram, mermaidBlockSource } from "@/features/files/preview/markdown/MermaidDiagram";
import { segmentReadmeHtml } from "@/features/files/preview/markdown/segments";
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
    pre: ({ children }) => {
      const mermaid = mermaidBlockSource(children);
      if (mermaid !== null) return <MermaidDiagram code={mermaid} />;
      const svg = svgBlockSource(children);
      if (svg !== null) return <SvgFigure markup={svg} />;
      return <pre>{children}</pre>;
    },
    img: ({ src, alt }) => {
      const resolved = previewImageSrc(root, filePath, typeof src === "string" ? src : undefined);
      if (!resolved) return alt ? <span>{alt}</span> : null;
      return <img src={resolved} alt={alt ?? ""} />;
    },
    a: ({ href, children }) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer"
        onClick={(event) => {
          if (!href || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
          const safe = httpUrl(href);
          if (!safe) return;
          event.preventDefault();
          usePanelStore.getState().openBrowser(safe);
        }}
      >
        {children}
      </a>
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
  const segments = useMemo(() => (content.kind === "text" ? segmentReadmeHtml(content.text) : []), [content]);
  if (content.kind !== "text") return null;
  return (
    <div className="file-preview relative h-full overflow-auto px-4 py-4">
      <MdToc />
      <div className="prose-md">
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
