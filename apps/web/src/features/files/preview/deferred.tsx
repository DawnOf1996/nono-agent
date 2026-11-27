import { lazy, Suspense } from "react";

import { PreviewLoading } from "@/features/files/preview/Loading";
import type { DocumentPreviewProps } from "@/features/files/preview/types";

const CodeBody = lazy(() => import("@/features/files/preview/CodeBody").then((module) => ({ default: module.CodeBody })));
const PdfBody = lazy(() => import("@/features/files/preview/PdfBody").then((module) => ({ default: module.PdfBody })));
const SheetBody = lazy(() => import("@/features/files/preview/SheetBody").then((module) => ({ default: module.SheetBody })));
const DocxBody = lazy(() => import("@/features/files/preview/DocxBody").then((module) => ({ default: module.DocxBody })));
const PptxBody = lazy(() => import("@/features/files/preview/PptxBody").then((module) => ({ default: module.PptxBody })));

function deferred(Body: typeof CodeBody) {
  return function DeferredPreview(props: DocumentPreviewProps) {
    return (
      <Suspense fallback={<PreviewLoading />}>
        <Body {...props} />
      </Suspense>
    );
  };
}

export const CodePreview = deferred(CodeBody);
export const PdfPreview = deferred(PdfBody);
export const SheetPreview = deferred(SheetBody);
export const DocxPreview = deferred(DocxBody);
export const PptxPreview = deferred(PptxBody);
