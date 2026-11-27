import type { ComponentType } from "react";

/** 预览宿主怎么把文件交给渲染器。 */
export type PreviewLoading = "text" | "bytes" | "url";

export type PreviewContent =
  | { kind: "text"; text: string }
  | { kind: "bytes"; data: Uint8Array }
  | { kind: "url"; url: string };

/**
 * 一个文件预览实现。
 *
 * 用 `registerDocumentPreview` 注册。`extensions` 是不带点的后缀；
 * 空数组不会按后缀匹配。`priority` 省略时视为 `extension`，会盖过内置实现。
 * 同一后缀更长的优先，其次是注册顺序。
 */
export type DocumentPreviewDefinition = {
  id: string;
  extensions: readonly string[];
  priority?: "builtin" | "extension";
  title: string;
  loading: PreviewLoading;
  /** 为 true 时，预览栏显示换行开关，并把当前选择传给 Body。 */
  wrap?: boolean;
  Body: ComponentType<DocumentPreviewProps>;
};

export type DocumentPreviewProps = {
  root: string;
  path: string;
  name: string;
  content: PreviewContent;
  wrap: boolean;
};
