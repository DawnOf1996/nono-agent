export { ensureBuiltinPreviews } from "@/features/files/preview/builtins";
export { formatBytes, fileNameOf } from "@/features/files/preview/format";
export {
  PLAIN_PREVIEW_ID,
  getDocumentPreviews,
  matchingDocumentPreviews,
  previewCandidates,
  registerDocumentPreview,
  subscribeDocumentPreviews,
} from "@/features/files/preview/registry";
export type { DocumentPreviewDefinition, DocumentPreviewProps, PreviewContent, PreviewLoading } from "@/features/files/preview/types";
