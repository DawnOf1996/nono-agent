import { Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { FsFilePreview } from "@nono/protocol";

import { fetchWorkspaceFile, workspaceFileRawUrl } from "@/api/client";
import { MarkdownText } from "@/components/MarkdownText";
import { normalizeWorkspace } from "@/lib/workspace";

function formatBytes(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function relativePath(root: string, target: string): string {
  const base = normalizeWorkspace(root);
  const full = normalizeWorkspace(target);
  if (full.toLowerCase().startsWith(`${base.toLowerCase()}\\`) || full.toLowerCase() === base.toLowerCase()) {
    return full.slice(base.length).replace(/^\\/, "") || full;
  }
  return full;
}

function isMarkdown(name: string): boolean {
  const lower = name.toLowerCase();
  return lower.endsWith(".md") || lower.endsWith(".markdown");
}

function fsError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : "无法读取文件";
  if (message === "Not Found") return "读不到文件接口，请重启本机服务后再试";
  return message;
}

export function FilePreview({ root, path, revision }: { root: string; path: string; revision: number }) {
  const [preview, setPreview] = useState<FsFilePreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setPreview(null);
    setError(null);
    void fetchWorkspaceFile(root, path)
      .then((data) => {
        if (live) setPreview(data);
      })
      .catch((cause: unknown) => {
        if (live) setError(fsError(cause));
      });
    return () => {
      live = false;
    };
  }, [root, path, revision]);

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-2">
        <span className="min-w-0 truncate text-[12px] text-faint" title={path}>
          {relativePath(root, path)}
        </span>
        {preview ? <span className="shrink-0 text-[11px] text-faint">{formatBytes(preview.size)}</span> : null}
      </div>
      {error ? (
        <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>
      ) : !preview ? (
        <div className="flex flex-1 items-center justify-center gap-2 text-[13px] text-faint">
          <Loader2 className="size-4 animate-spin" />
          正在读取
        </div>
      ) : preview.kind === "image" ? (
        <div className="flex min-h-0 flex-1 items-center justify-center overflow-auto p-4">
          <img
            src={workspaceFileRawUrl(root, path)}
            alt={preview.name}
            className="max-h-full max-w-full object-contain"
          />
        </div>
      ) : preview.kind === "text" && preview.content !== null ? (
        <div className="min-h-0 flex-1 overflow-auto">
          {isMarkdown(preview.name) ? (
            <div className="file-preview min-w-full px-4 py-4">
              <MarkdownText text={preview.content} />
            </div>
          ) : (
            <pre className="min-w-full whitespace-pre px-4 py-3 font-mono text-[12.5px] leading-6 text-ink">
              {preview.content}
            </pre>
          )}
        </div>
      ) : (
        <div className="flex flex-1 items-center justify-center px-6 text-center text-[13px] leading-6 text-muted">
          {preview.kind === "too_large"
            ? `这个文件有 ${formatBytes(preview.size)}，超过预览上限。`
            : "这个文件不能以文本方式预览。"}
        </div>
      )}
    </div>
  );
}
