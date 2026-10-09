import { useEffect, useRef, useState, useSyncExternalStore } from "react";

import { fetchWorkspaceBytes } from "@/api/client";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { createHtmlDocument } from "@/features/files/preview/html/document";
import { packHtml, resolvePreviewRelative } from "@/features/files/preview/html/pack";
import type { DocumentPreviewProps } from "@/features/files/preview/types";
import { getSidebarPrefs, subscribeSidebarPrefs } from "@/features/settings/sidebar-prefs";
import { openLinkTarget } from "@/lib/use-link-router";

/** iframe 里拦截到的链接请求。会话内只认自己那条命名消息。 */
const OPEN_LINK_MESSAGE = "nono";

export function HtmlBody({ root, path, content }: DocumentPreviewProps) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 认 postMessage 的来源：别的 iframe 递过来的同名消息不能拿去开标签。
  const frameRef = useRef<HTMLIFrameElement>(null);
  const data = content.kind === "bytes" ? content.data : null;
  const { htmlNoSandbox, htmlUnsafe } = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs);
  const sandbox = htmlNoSandbox ? undefined : htmlUnsafe ? "allow-scripts allow-same-origin allow-forms allow-popups" : "allow-scripts";

  // iframe 是独立文档，全局的点击代理收不到它的点击。预览页把地址递过来，
  // 这里按「这个 HTML 文件自己」为基准解析：预览里的 ./app.js 得回到同目录去。
  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.source !== frameRef.current?.contentWindow) return;
      const payload = event.data as { nono?: unknown; href?: unknown } | null;
      if (payload?.nono !== OPEN_LINK_MESSAGE || typeof payload.href !== "string") return;
      openLinkTarget(payload.href, { root, fromFile: path });
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [path, root]);

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
  return (
    <iframe
      ref={frameRef}
      className="h-full w-full border-0 bg-white"
      src={url}
      sandbox={sandbox}
      title="HTML 预览"
    />
  );
}
