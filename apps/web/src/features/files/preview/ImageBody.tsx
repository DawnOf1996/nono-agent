import type { DocumentPreviewProps } from "@/features/files/preview/types";

export function ImageBody({ content, name }: DocumentPreviewProps) {
  if (content.kind !== "url") return null;
  return (
    <div className="flex h-full items-center justify-center overflow-auto p-4">
      <img src={content.url} alt={name} className="max-h-full max-w-full object-contain" />
    </div>
  );
}
