import { useEffect, useRef, useState } from "react";

import type { CaptureRect } from "@/features/files/browser-tools";

/** 小于这个尺寸的拖拽当作误触，直接忽略 —— 裁出来也是一片纯色，没法用。 */
const MIN_EDGE = 8;

/**
 * 区域框选覆盖层。
 *
 * 盖在 `<webview>` 正上方，把鼠标事件整个吃掉，拖出一个矩形。盖在这里而不是往访客页里
 * 注入样式：访客页在另一个浏览上下文里，注入就得负责清理（用户中途导航、组件卸载、
 * 页面自己改 DOM，都会把高亮留成脏样式），而宿主这边画个 div 什么都不用管。
 *
 * 坐标是相对**这层覆盖层**的，覆盖层和 webview 元素严丝合缝，所以拿到的就是访客页
 * 视口坐标系里的 CSS 像素，裁剪时直接用（位图缩放由 cropCapture 负责换算）。
 */
export function RegionSelect({
  onCancel,
  onSubmit,
}: {
  onCancel: () => void;
  onSubmit: (rect: CaptureRect) => void;
}) {
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [rect, setRect] = useState<CaptureRect | null>(null);
  /** 按下和松开都落在同一处时不当作框选 —— 那是一次点击，不是拖拽。 */
  const movedRef = useRef(false);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  function local(event: React.PointerEvent<HTMLDivElement>): { x: number; y: number } {
    const box = event.currentTarget.getBoundingClientRect();
    return { x: event.clientX - box.left, y: event.clientY - box.top };
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    movedRef.current = false;
    const point = local(event);
    setStart(point);
    setRect({ ...point, width: 0, height: 0 });
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!start) return;
    const point = local(event);
    const next = {
      x: Math.min(start.x, point.x),
      y: Math.min(start.y, point.y),
      width: Math.abs(point.x - start.x),
      height: Math.abs(point.y - start.y),
    };
    if (next.width > MIN_EDGE / 2 || next.height > MIN_EDGE / 2) movedRef.current = true;
    setRect(next);
  }

  function onPointerUp(event: React.PointerEvent<HTMLDivElement>) {
    event.currentTarget.releasePointerCapture(event.pointerId);
    const current = rect;
    setStart(null);
    if (!current || !movedRef.current) {
      setRect(null);
      return;
    }
    if (current.width < MIN_EDGE || current.height < MIN_EDGE) {
      setRect(null);
      return;
    }
    onSubmit(current);
  }

  return (
    <div
      className="absolute inset-0 z-10 cursor-crosshair select-none overflow-hidden"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
    >
      <div className="pointer-events-none absolute inset-0 bg-black/25" />
      {rect ? (
        <>
          {/* 选中的那一块保持透明。用外扩的 box-shadow 当遮罩，比再画四个 div 干净。 */}
          <div
            className="pointer-events-none absolute border border-accent"
            style={{
              left: rect.x,
              top: rect.y,
              width: rect.width,
              height: rect.height,
              boxShadow: "0 0 0 9999px rgba(0,0,0,0.25)",
            }}
          >
            <span className="absolute -top-6 left-0 rounded bg-accent px-1.5 py-0.5 text-[11px] text-bg">
              {Math.round(rect.width)} × {Math.round(rect.height)}
            </span>
          </div>
        </>
      ) : (
        <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 text-center">
          <p className="text-[13px] text-white drop-shadow">拖动框选一片区域，松开后截成图片加进对话框</p>
          <p className="mt-1 text-[11px] text-white/70 drop-shadow">Esc 取消</p>
        </div>
      )}
    </div>
  );
}
