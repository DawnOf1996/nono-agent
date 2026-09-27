import { useEffect, useState, useSyncExternalStore } from "react";

import { fetchWorkspaceBytes } from "@/api/client";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { createHtmlDocument } from "@/features/files/preview/html/document";
import { packHtml, resolvePreviewRelative } from "@/features/files/preview/html/pack";
import type { DocumentPreviewProps } from "@/features/files/preview/types";
import { getSidebarPrefs, subscribeSidebarPrefs } from "@/features/settings/sidebar-prefs";

export function HtmlBody({ root, path, content }: DocumentPreviewProps) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = content.kind === "bytes" ? content.data : null;
  const { htmlNoSandbox, htmlUnsafe } = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs);
  const sandbox = htmlNoSandbox ? undefined : htmlUnsafe ? "allow-scripts allow-same-origin allow-forms allow-popups" : "allow-scripts";

  useEffect(() => {
    if (data === null) return;
    const controller = new AbortController();
    let objectUrl: string | undefined;
    const revoke = () => {
      if (objectUrl === undefined) return;
      URL.revokeObjectURL(objectUrl);
      objectUrl = undefined;
    };
    setUrl(null);
    setError(null);
    void (async () => {
      try {
        const bundle = await packHtml(
          data,
          async (reference, signal) => {
            const target = resolvePreviewRelative(path, reference);
            if (target === null) throw new Error("无法读取 HTML 依赖");
            return fetchWorkspaceBytes(root, target, signal);
          },
          controller.signal,
        );
        controller.signal.throwIfAborted();
        objectUrl = URL.createObjectURL(new Blob([createHtmlDocument(bundle)], { type: "text/html" }));
        if (controller.signal.aborted) {
          revoke();
          return;
        }
        setUrl(objectUrl);
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error && cause.message ? cause.message : "无法预览这个 HTML");
      }
    })();
    return () => {
      controller.abort();
      revoke();
    };
  }, [data, path, root]);

  if (data === null) return null;
  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (!url) return <PreviewLoading label="正在准备页面" />;
  return <iframe className="h-full w-full border-0 bg-white" src={url} sandbox={sandbox} title="HTML 预览" />;
}
