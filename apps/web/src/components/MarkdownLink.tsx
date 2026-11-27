import { memo, type ReactNode } from "react";

import { usePanelStore } from "@/features/files/panel-store";
import { resolveLinkTarget } from "@/lib/link-target";

/**
 * 正文里的一个链接。
 *
 * 只管渲染外观和语义，真正「点下去会发生什么」由 lib/use-link-router 的全局代理决定。
 * 这里按目标类型挂 class：网页链接是蓝色可点、文件链接是中性虚线、页内锚点不着色 ——
 * 扫读的时候就能看出这个链接会不会把自己带出当前文档。
 *
 * 组件是 memo 的：流式渲染时每秒都会重渲很多段正文，链接判定（正则 + URL 解析）
 * 不该跟着一起跑。root 变了才会重算。
 */
export const MarkdownLink = memo(function MarkdownLink({
  href,
  children,
  title,
}: {
  href?: string;
  children?: ReactNode;
  title?: string;
}) {
  const root = usePanelStore((state) => state.root);
  const kind = resolveLinkTarget(href, { root }).kind;
  const tone = kind === "anchor" ? "md-anchor" : kind === "file" ? "md-file" : kind === "web" ? "md-web" : "";

  return (
    // 不带 target：<webview> 里的 target=_blank 会让桌面版另开一个原生窗口，
    // 应用整个被顶到任务栏后面。保留 href 让「复制链接地址」和 Ctrl/Cmd+点击仍可用。
    <a href={href} title={title ?? undefined} className={tone || undefined} data-link-kind={kind}>
      {children}
    </a>
  );
});