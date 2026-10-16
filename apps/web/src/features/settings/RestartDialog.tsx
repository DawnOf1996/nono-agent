import { Check, Loader2, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { Button } from "@/components/ui/button";
import { relaunchApp, canRelaunch } from "@/lib/relaunch";

/**
 * 「改完要重启」的通用弹窗。
 *
 * 凡是「运行期改不动、必须重启才生效」的操作都走这里 —— 目前有数据目录迁移和
 * 内置浏览器的 Agent 开关。**重启提示不放在设置行里**：设置面板一屏能放好几项，
 * 每项底下挂个「立即重启」按钮，用户根本分不清那个按钮是管哪一行的；而且点错
 * 的代价是整个应用重启。弹窗把「改了什么 → 为什么要重启 → 现在重启还是稍后」
 * 三件事说在一个地方。
 *
 * 沿用 :mod:`DataDirDialog` 的两段式：确认重启 / 正在重启。默认焦点刻意不给
 * 「立即重启」—— 整体重启不该被一次回车顺手触发。
 */
export function PendingRestartDialog({
  title,
  description,
  onClose,
}: {
  title: string;
  description: string;
  onClose: () => void;
}) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      // 必须在捕获阶段收掉：设置面板自己也监听 Esc（冒泡阶段）。不
      // stopImmediatePropagation 的话一次按键会把弹窗和整个设置面板一起带走。
      event.preventDefault();
      event.stopImmediatePropagation();
      onClose();
    }
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/30 p-6"
      onPointerDown={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="pending-restart-title"
        className="w-full max-w-md rounded-2xl border border-line bg-bg p-5 shadow-[0_16px_48px_var(--shadow-color-strong)]"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <h2 id="pending-restart-title" className="flex items-center gap-2 text-[15px] font-medium">
          <Check className="size-4 text-add" />
          {title}
        </h2>
        <p className="mt-2 text-[13px] leading-6 text-muted">{description}</p>
        {/* gap-2 少写一个，两个按钮就会贴在一起 —— RestartButtons 是 fragment，
            间距得由这一层给。 */}
        <div className="mt-5 flex justify-end gap-2">
          <RestartButtons onClose={onClose} />
        </div>
      </div>
    </div>,
    document.body,
  );
}

/** 待重启这一步的按钮：桌面端能自己重启，浏览器里只能让人手动关。 */
export function RestartButtons({ onClose }: { onClose: () => void }) {
  const [restarting, setRestarting] = useState(false);
  const canRelaunchNow = canRelaunch();

  async function restart() {
    setRestarting(true);
    await relaunchApp();
  }

  return (
    <>
      <Button variant="outline" onClick={onClose}>
        稍后重启
      </Button>
      <Button disabled={restarting || !canRelaunchNow} onClick={() => void restart()}>
        {restarting ? <Loader2 className="size-3.5 animate-spin" /> : <RotateCw className="size-3.5" />}
        {canRelaunchNow ? "立即重启" : "请手动重启应用"}
      </Button>
    </>
  );
}
