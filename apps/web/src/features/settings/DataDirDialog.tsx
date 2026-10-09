import { ArrowRight, Check, Loader2, RotateCw } from "lucide-react";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { updateDataDir } from "@/api/client";
import { Button } from "@/components/ui/button";
import { relaunchApp, canRelaunch } from "@/lib/relaunch";

/**
 * 更改数据目录的确认弹窗。
 *
 * 选目录和确认搬家分成两步：搬的是整个 ``.nono-agent``（配置、密钥、会话、记忆、
 * 托管运行时，几百 MB 起），搬完还必须重启才生效 —— 这两件事都不能塞进目录选择器
 * 顺手做掉，否则用户点「迁移到这里」之后既不知道发生了什么，也没法反悔。
 *
 * 三个阶段对应三个文案：确认 / 迁移中 / 待重启。「迁移中」必须留在弹窗里而不是回到
 * 设置页 —— 复制几百 MB 要一会儿，回设置页只剩一行「迁移中…」，用户会以为卡死了。
 */
type Phase = "confirm" | "moving" | "restart";

export function DataDirDialog({
  target,
  current,
  onClose,
  onMigrated,
}: {
  /** 选好的新目录。 */
  target: string;
  current: string;
  onClose: () => void;
  /** 迁移成功回调：新数据目录，交给上层刷新展示。 */
  onMigrated: (dataDir: string) => void;
}) {
  const [phase, setPhase] = useState<Phase>("confirm");
  const [error, setError] = useState<string | null>(null);
  const [moved, setMoved] = useState(0);
  // 弹窗开着时按 Esc 应该收掉；迁移中不行 —— 那时没有「取消」这回事，Esc 走掉只会
  // 让用户以为迁移停住了（它还在后台跑）。
  const closable = phase !== "moving";

  useEffect(() => {
    if (!closable) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      // 必须在这里收掉：设置面板自己也监听 Esc 关闭。不 stopPropagation 的话一次按键
      // 会先把本弹窗关掉、再顺手把整个设置面板也带走。
      event.preventDefault();
      event.stopImmediatePropagation();
      onClose();
    }
    // 捕获阶段：抢在设置面板的 window 冒泡监听之前。
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [closable, onClose]);

  useEffect(() => {
    if (phase !== "confirm") return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Enter") return;
      // 按下 Enter 时焦点通常在「取消」上（危险操作不该默认聚焦确认）。
      // 尊重这个焦点，否则一次回车会「关掉弹窗 + 开始搬数据」。
      const target_ = event.target as HTMLElement | null;
      if (target_?.tagName === "BUTTON") return;
      event.preventDefault();
      void migrate();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // migrate 只依赖 phase/target，重渲染不必重新挂监听。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, target]);

  async function migrate() {
    if (phase !== "confirm") return;
    setPhase("moving");
    setError(null);
    try {
      const result = await updateDataDir(target);
      setMoved(result.moved);
      setPhase("restart");
      onMigrated(result.data_dir);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "迁移数据目录失败");
      setPhase("confirm");
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center bg-black/30 p-6"
      onPointerDown={() => {
        if (closable) onClose();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="data-dir-dialog-title"
        className="w-full max-w-md rounded-2xl border border-line bg-bg p-5 shadow-[0_16px_48px_var(--shadow-color-strong)]"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <DialogTitle phase={phase} />

        {phase === "confirm" ? (
          <p className="mt-2 text-[13px] leading-6 text-muted">
            应用会把当前数据迁移到新目录，完成后立即重启。旧目录会留下一个指针文件，可用来找回位置。
          </p>
        ) : null}

        {/* 当前 → 目标 两条路径并排。搬几百 MB 的时候，这两个地址就是用户唯一的
            事后核对依据，得一直留在眼前，不能被 loading 顶掉。 */}
        <div className="mt-4 space-y-1.5 rounded-xl bg-sunken px-3 py-2.5">
          <PathRow label="当前" value={current} />
          <PathRow label="目标" value={target} arrow />
        </div>

        {phase === "moving" ? (
          <p className="mt-3 flex items-center gap-2 text-[13px] text-muted">
            <Loader2 className="size-3.5 shrink-0 animate-spin" />
            正在迁移，数据量大时可能要一会儿…
          </p>
        ) : null}

        {phase === "restart" ? (
          <p className="mt-3 flex items-center gap-2 text-[13px] text-muted">
            <Check className="size-3.5 shrink-0 text-add" />
            {moved > 0 ? `已搬完 ${moved} 个条目，` : "已搬完，"}重启后生效
          </p>
        ) : null}

        {error ? <p className="mt-3 text-[12px] leading-5 text-danger">{error}</p> : null}

        <div className="mt-5 flex justify-end gap-2">
          {phase === "confirm" ? (
            <>
              <Button variant="outline" onClick={onClose}>
                取消
              </Button>
              {/* 故意不给「立即迁移」autoFocus：默认焦点留在「取消」上。搬走几百 MB
                  是不可逆的，回车/空格不该顺手就触发。 */}
              <Button onClick={() => void migrate()}>
                立即迁移
              </Button>
            </>
          ) : null}
          {phase === "moving" ? (
            <Button disabled>
              <Loader2 className="size-3.5 animate-spin" />
              迁移中
            </Button>
          ) : null}
          {phase === "restart" ? <RestartButtons onClose={onClose} /> : null}
        </div>
      </div>
    </div>,
    document.body,
  );
}

function DialogTitle({ phase }: { phase: Phase }) {
  const title =
    phase === "moving" ? "正在迁移数据" : phase === "restart" ? "数据已迁移" : "更改数据目录";
  return (
    <h2 id="data-dir-dialog-title" className="flex items-center gap-2 text-[15px] font-medium">
      {phase === "restart" ? <Check className="size-4 text-add" /> : null}
      {title}
    </h2>
  );
}

function PathRow({ label, value, arrow }: { label: string; value: string; arrow?: boolean }) {
  return (
    <div className="flex items-center gap-2 text-[12px]">
      <span className="w-7 shrink-0 text-faint">{label}</span>
      {arrow ? <ArrowRight className="size-3 shrink-0 text-faint" /> : null}
      {/* 路径不换行截断：中段省略、结尾留着，才认得出是哪个盘。 */}
      <span className="min-w-0 flex-1 truncate font-mono text-muted" title={value}>
        {value}
      </span>
    </div>
  );
}

/** 待重启这一步的按钮：桌面端能自己重启，浏览器里只能让人手动关。 */
function RestartButtons({ onClose }: { onClose: () => void }) {
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
