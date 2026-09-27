import DOMPurify from "dompurify";
import { useEffect, useState } from "react";

import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

export function DocxBody({ content }: DocumentPreviewProps) {
  const [html, setHtml] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = content.kind === "bytes" ? content.data : null;

  useEffect(() => {
    if (data === null) return;
    let live = true;
    setHtml(null);
    setError(null);
    void (async () => {
      try {
        const loaded = await import("mammoth/mammoth.browser.js");
        const convertToHtml = loaded.convertToHtml ?? loaded.default.convertToHtml;
        const buffer = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
        const result = await convertToHtml({ arrayBuffer: buffer });
        const clean = DOMPurify.sanitize(result.value, { USE_PROFILES: { html: true } });
        if (live) setHtml(clean);
      } catch (cause) {
        if (live) setError(cause instanceof Error ? cause.message : "无法预览这个文档");
      }
    })();
    return () => {
      live = false;
    };
  }, [data]);

  if (data === null) return null;
  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (html === null) return <PreviewLoading label="正在转换文档" />;
  return (
    <div
      className="file-preview prose-md h-full overflow-auto bg-white px-6 py-5"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}
