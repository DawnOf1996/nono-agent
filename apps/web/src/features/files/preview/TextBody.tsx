import type { DocumentPreviewProps } from "@/features/files/preview/types";

export function TextBody({ content, wrap }: DocumentPreviewProps) {
  if (content.kind !== "text") return null;
  return (
    <pre
      className={`h-full overflow-auto px-4 py-3 font-mono text-[12.5px] leading-6 text-ink ${
        wrap ? "whitespace-pre-wrap break-words" : "whitespace-pre"
      }`}
    >
      {content.text}
    </pre>
  );
}
