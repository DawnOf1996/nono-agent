import { useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

import { dismissToast, getToastAnchor, getToasts, subscribeToasts } from "@/lib/toast";
import { cn } from "@/lib/utils";

export { dismissToast, toast } from "@/lib/toast";

/**
 * 全局唯一的 toast 宿主，在 App 里挂一次。
 *
 * 落点有两个：
 * - 有弹框登记过锚点（见 lib/toast.ts 的 registerToastAnchor）就贴进那个弹框，
 *   用 absolute 跟着弹框定位。用户是在弹框里点的按钮，提示就该待在弹框里。
 * - 没有锚点就贴整窗底部，并且要压过所有对话框：z-[70] 高于设置面板的 z-30
 *   和确认框的 z-[60]。低了的话提示会被整个盖住，用户点了像是没反应。
 *
 * 容器 pointer-events-none、每条自己 pointer-events-auto：容器横跨整个底部，
 * 不能让它挡住下面的输入框和消息区，但 toast 本体要能点掉。
 */
export function Toaster() {
  const items = useSyncExternalStore(subscribeToasts, getToasts, getToasts);
  // 锚点和消息共用一套订阅：锚点变化也会 emit，这里会跟着重读。
  const anchor = useSyncExternalStore(subscribeToasts, getToastAnchor, getToastAnchor);
  if (items.length === 0) return null;

  return createPortal(
    <div
      className={cn(
        "pointer-events-none inset-x-0 flex flex-col items-center gap-2 px-6",
        anchor ? "absolute bottom-4 z-10" : "fixed bottom-8 z-[70]",
      )}
    >
      {items.map((item) => (
        <div
          key={item.id}
          role="status"
          onClick={() => dismissToast(item.id)}
          className="nono-toast pointer-events-auto cursor-default rounded-full border border-line bg-bg px-4 py-2 text-[13px] text-ink shadow-[0_8px_28px_var(--shadow-color)]"
        >
          {item.message}
        </div>
      ))}
    </div>,
    anchor ?? document.body,
  );
}