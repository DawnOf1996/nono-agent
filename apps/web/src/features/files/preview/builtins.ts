import { HtmlBody } from "@/features/files/preview/HtmlBody";
import { ImageBody } from "@/features/files/preview/ImageBody";
import { MarkdownBody } from "@/features/files/preview/MarkdownBody";
import { TextBody } from "@/features/files/preview/TextBody";
import { CODE_EXTENSIONS } from "@/features/files/preview/languages";
import { CodePreview, DocxPreview, PdfPreview, PptxPreview, SheetPreview } from "@/features/files/preview/deferred";
import { PLAIN_PREVIEW_ID, getDocumentPreviews, registerDocumentPreview } from "@/features/files/preview/registry";

let ready = false;

/** 注册内置预览。重复调用没有效果。 */
export function ensureBuiltinPreviews(): void {
  if (ready || getDocumentPreviews().some((item) => item.id === PLAIN_PREVIEW_ID)) {
    ready = true;
    return;
  }
  ready = true;
  registerDocumentPreview({
    id: "@nono/preview/markdown",
    extensions: ["md", "markdown"],
    priority: "builtin",
    title: "Markdown",
    loading: "text",
    Body: MarkdownBody,
  });
  registerDocumentPreview({
    id: "@nono/preview/html",
    extensions: ["html", "htm"],
    priority: "builtin",
    title: "HTML",
    loading: "bytes",
    Body: HtmlBody,
  });
  registerDocumentPreview({
    id: "@nono/preview/image",
    extensions: ["png", "jpg", "jpeg", "gif", "webp", "bmp", "ico", "svg"],
    priority: "builtin",
    title: "图片",
    loading: "url",
    Body: ImageBody,
  });
  registerDocumentPreview({
    id: "@nono/preview/sheet",
    extensions: ["csv", "tsv", "xlsx", "xls", "xlsm", "ods"],
    priority: "builtin",
    title: "表格",
    loading: "bytes",
    Body: SheetPreview,
  });
  registerDocumentPreview({
    id: "@nono/preview/docx",
    extensions: ["docx"],
    priority: "builtin",
    title: "Word",
    loading: "bytes",
    Body: DocxPreview,
  });
  registerDocumentPreview({
    id: "@nono/preview/pptx",
    extensions: ["pptx"],
    priority: "builtin",
    title: "PowerPoint",
    loading: "bytes",
    Body: PptxPreview,
  });
  registerDocumentPreview({
    id: "@nono/preview/pdf",
    extensions: ["pdf"],
    priority: "builtin",
    title: "PDF",
    loading: "bytes",
    Body: PdfPreview,
  });
  registerDocumentPreview({
    id: "@nono/preview/code",
    extensions: CODE_EXTENSIONS,
    priority: "builtin",
    title: "代码",
    loading: "text",
    wrap: true,
    Body: CodePreview,
  });
  registerDocumentPreview({
    id: PLAIN_PREVIEW_ID,
    extensions: [],
    priority: "builtin",
    title: "纯文本",
    loading: "text",
    wrap: true,
    Body: TextBody,
  });
}
