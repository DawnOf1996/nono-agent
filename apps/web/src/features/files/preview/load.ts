import { fetchWorkspaceBytes, fetchWorkspaceFile, workspaceFileRawUrl } from "@/api/client";
import { formatBytes } from "@/features/files/preview/format";
import type { PreviewContent, PreviewLoading } from "@/features/files/preview/types";

export type LoadedPreview = {
  content: PreviewContent;
  size: number;
};

function tooLarge(size: number): Error {
  return new Error(`这个文件有 ${formatBytes(size)}，超过预览上限。`);
}

export async function loadPreview(
  root: string,
  path: string,
  loading: PreviewLoading,
  revision: number,
  signal: AbortSignal,
): Promise<LoadedPreview> {
  if (loading === "url") {
    const meta = await fetchWorkspaceFile(root, path, signal);
    if (meta.kind === "too_large") throw tooLarge(meta.size);
    if (meta.kind !== "image") throw new Error("这个文件不能以图片方式预览。");
    const url = `${workspaceFileRawUrl(root, path)}&v=${revision}`;
    return { content: { kind: "url", url }, size: meta.size };
  }
  if (loading === "text") {
    const meta = await fetchWorkspaceFile(root, path, signal);
    if (meta.kind === "too_large") throw tooLarge(meta.size);
    if (meta.kind !== "text" || meta.content === null) throw new Error("这个文件不能以文本方式预览。");
    return { content: { kind: "text", text: meta.content }, size: meta.size };
  }
  const data = await fetchWorkspaceBytes(root, path, signal);
  return { content: { kind: "bytes", data }, size: data.byteLength };
}
