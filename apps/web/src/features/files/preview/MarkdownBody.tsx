import DOMPurify from "dompurify";
import { useMemo } from "react";
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

function previewComponents(root: string, filePath: string): Components {
  return {
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
}

function MarkdownChunk({ root, path, text }: { root: string; path: string; text: string }) {
  if (!text.trim()) return null;
  return (
    <Markdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, schema]]}
      components={previewComponents(root, path)}
    >
      {text}
    </Markdown>
  );
}

export function MarkdownBody({ root, path, content }: DocumentPreviewProps) {
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
}
