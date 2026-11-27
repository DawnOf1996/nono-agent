import type { DocumentPreviewProps } from "@/features/files/preview/types";

/**
 * 纯文本预览：没有语法高亮，但一样要能被选中挂引用和评论。
 *
 * 行号靠预先把内容按行切开渲染：每行一个带 data-line-number 的元素，
 * 选中区端点往上找最近的祖先就能拿到行号。整篇塞进单个 <pre> 不行，
 * 那样选区只知道"在这个 pre 里"，不知道第几行。
 */
export function TextBody({ root, path, content, wrap }: DocumentPreviewProps) {
  if (content.kind !== "text") return null;
  const lines = content.text.split("\n");
  return (
    <div
      className={`h-full overflow-auto px-4 py-3 font-mono text-[12.5px] leading-6 text-ink ${
        wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"
      }`}
      data-quote-source="file"
      data-file-path={path}
      data-file-root={root}
    >
      {lines.map((line, index) => (
        <div key={index} data-line-number={index + 1}>
          {line.length > 0 ? line : "\n"}
        </div>
      ))}
    </div>
  );
}