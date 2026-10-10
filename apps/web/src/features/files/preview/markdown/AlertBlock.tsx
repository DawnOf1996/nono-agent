import { Info, Lightbulb, MessageSquareWarning, OctagonAlert, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

import { splitAlert, type AlertKind } from "@/features/files/preview/markdown/alerts";

const ICONS: Record<AlertKind, typeof Info> = {
  note: Info,
  tip: Lightbulb,
  important: MessageSquareWarning,
  warning: TriangleAlert,
  caution: OctagonAlert,
};

/**
 * 块引用以 `[!NOTE]` 之类标记开头时按 GitHub Alert 渲染，否则就是普通引用。
 *
 * 用 div 而不是 blockquote：提示块要带底色和左色条，blockquote 那套样式压不住，
 * 而且 .prose-md blockquote 给的是 muted 正文色，提示块里正文该按正文色走。
 * 行号从标记所在那一段继承过来（见 alerts.ts），换掉外层元素不影响选区换算。
 */
export function AlertBlock({ children }: { children?: ReactNode }) {
  const alert = splitAlert(children);
  if (!alert) return <blockquote>{children}</blockquote>;
  const Icon = ICONS[alert.kind];
  return (
    <div className={`markdown-alert markdown-alert-${alert.kind}`}>
      <p className="markdown-alert-title" data-line={alert.line}>
        <Icon className="markdown-alert-icon" aria-hidden="true" />
        {alert.label}
      </p>
      {alert.children}
    </div>
  );
}