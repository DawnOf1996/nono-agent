import { MarkdownText } from "@/components/MarkdownText";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

export function MarkdownBody({ content }: DocumentPreviewProps) {
  if (content.kind !== "text") return null;
  return (
    <div className="file-preview h-full overflow-auto px-4 py-4">
      <MarkdownText text={content.text} />
    </div>
  );
}
