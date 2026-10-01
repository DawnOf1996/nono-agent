import { useCallback, useEffect, useRef, type DragEvent as ReactDragEvent, type PointerEvent as ReactPointerEvent, type RefObject } from "react";

const MIN_SCALE = 0.2;
const MAX_SCALE = 8;
const WHEEL_STEP = 1.1;
/** 拖过这个距离才算平移，否则当作误触。 */
const DRAG_SLOP = 4;

type PanZoomView = { scale: number; x: number; y: number };

type PanZoomOptions = {
  min?: number;
  max?: number;
  /** 缩放倍率变化时回调，只给「当前倍率」这类展示用；平移不会触发。 */
  onScale?: (scale: number) => void;
  /** 滚轮监听挂在哪。默认挂在画布上；传外层容器可以顺带挡住背后的滚动。 */
  wheelHostRef?: RefObject<HTMLElement | null>;
};

export type PanZoom<TGraphic extends HTMLElement = HTMLDivElement> = {
  /** 承接拖拽的画布，套 `pan-zoom` 类。 */
  stageRef: RefObject<HTMLDivElement | null>;
  /** 被变换的元素，套 `pan-zoom-target` 类，transform 直接写在它身上。 */
  graphicRef: RefObject<TGraphic | null>;
  /** 立刻落一次 transform。 */
  paint: () => void;
  /** 合到下一帧落一次 transform。 */
  schedulePaint: () => void;
  /** 以某个屏幕坐标为锚点缩放。 */
  zoomAt: (clientX: number, clientY: number, factor: number) => void;
  /** 以画布中心为锚点缩放，给 +/- 按钮用。 */
  zoomBy: (factor: number) => void;
  /** 复位到初始视图。 */
  reset: (next?: Partial<PanZoomView>) => void;
  /** 本次手势是否真的拖动过，用来区分点击和拖拽。 */
  dragged: RefObject<boolean>;
  dragProps: {
    onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerMove: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerUp: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onPointerCancel: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onLostPointerCapture: (event: ReactPointerEvent<HTMLDivElement>) => void;
    onDragStart: (event: ReactDragEvent<HTMLElement>) => void;
  };
};

/**
 * 预览用的平移缩放。
 *
 * transform 直接写 DOM 并按 rAF 合并：走 setState 的话，每个 pointermove（高频鼠标每秒能报几百次）
 * 都会重渲染一次，节点多的 SVG 还要跟着重新排版和栅格化，拖起来就是一顿一顿的。
 * 拖拽期间给画布挂 data-panning，由 CSS 提层，纯位移交给合成器，不用每帧重画。
 */
export function usePanZoom<TGraphic extends HTMLElement = HTMLDivElement>({
  min = MIN_SCALE,
  max = MAX_SCALE,
  onScale,
  wheelHostRef,
}: PanZoomOptions = {}): PanZoom<TGraphic> {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const graphicRef = useRef<TGraphic | null>(null);
  const view = useRef<PanZoomView>({ scale: 1, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; ox: number; oy: number } | null>(null);
  const pending = useRef(0);
  const dragged = useRef(false);
  const onScaleRef = useRef(onScale);
  onScaleRef.current = onScale;
  const limits = useRef({ min, max });
  limits.current = { min, max };

  const clamp = useCallback(
    (scale: number) => Math.min(limits.current.max, Math.max(limits.current.min, scale)),
    [],
  );

  const paint = useCallback(() => {
    pending.current = 0;
    const graphic = graphicRef.current;
    if (!graphic) return;
    const { x, y, scale } = view.current;
    graphic.style.transform = `translate3d(${x}px, ${y}px, 0) scale(${scale})`;
  }, []);

  const schedulePaint = useCallback(() => {
    if (pending.current) return;
    pending.current = requestAnimationFrame(paint);
  }, [paint]);

  const zoomAt = useCallback(
    (clientX: number, clientY: number, factor: number) => {
      const graphic = graphicRef.current;
      if (!graphic) return;
      const current = view.current;
      const scale = clamp(current.scale * factor);
      if (scale === current.scale) return;
      const box = graphic.getBoundingClientRect();
      // transform-origin 在中心，包围盒中心是「未变换中心 + 位移」，减掉位移才能还原锚点。
      const anchorX = box.left + box.width / 2 - current.x;
      const anchorY = box.top + box.height / 2 - current.y;
      const ratio = scale / current.scale;
      view.current = {
        scale,
        x: ratio * current.x + (1 - ratio) * (clientX - anchorX),
        y: ratio * current.y + (1 - ratio) * (clientY - anchorY),
      };
      onScaleRef.current?.(scale);
      schedulePaint();
    },
    [clamp, schedulePaint],
  );

  const zoomBy = useCallback(
    (factor: number) => {
      const stage = stageRef.current;
      if (!stage) return;
      const box = stage.getBoundingClientRect();
      zoomAt(box.left + box.width / 2, box.top + box.height / 2, factor);
    },
    [zoomAt],
  );

  const reset = useCallback(
    (next?: Partial<PanZoomView>) => {
      const scale = clamp(next?.scale ?? 1);
      view.current = { scale, x: next?.x ?? 0, y: next?.y ?? 0 };
      onScaleRef.current?.(scale);
      paint();
    },
    [clamp, paint],
  );

  useEffect(() => () => cancelAnimationFrame(pending.current), []);

  useEffect(() => {
    const stage = stageRef.current;
    const host = wheelHostRef?.current ?? stage;
    if (!stage || !host) return;
    const onWheel = (event: WheelEvent) => {
      // React 的 onWheel 挂在根节点上且是被动监听，preventDefault 不生效，背后的页面会跟着滚。
      event.preventDefault();
      // 只有压在画布上的滚轮才缩放，工具条和背景不该抢。
      if (!stage.contains(event.target as Node | null)) return;
      zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? WHEEL_STEP : 1 / WHEEL_STEP);
    };
    host.addEventListener("wheel", onWheel, { passive: false });
    return () => host.removeEventListener("wheel", onWheel);
  }, [zoomAt, wheelHostRef]);

  function startDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    // 不拦默认行为，mousedown 会在文字上起一段选区：内容被涂蓝，选区每帧还要重算重绘。
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    event.currentTarget.setAttribute("data-panning", "true");
    dragged.current = false;
    drag.current = { x: event.clientX, y: event.clientY, ox: view.current.x, oy: view.current.y };
  }

  function moveDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const start = drag.current;
    if (!start) return;
    const x = start.ox + event.clientX - start.x;
    const y = start.oy + event.clientY - start.y;
    if (Math.abs(x - start.ox) > DRAG_SLOP || Math.abs(y - start.oy) > DRAG_SLOP) dragged.current = true;
    view.current.x = x;
    view.current.y = y;
    schedulePaint();
  }

  function endDrag(event: ReactPointerEvent<HTMLDivElement>) {
    if (!drag.current) return;
    drag.current = null;
    // 松手后撤掉提层，下一次缩放会按新倍率重新采样，内容保持清晰。
    event.currentTarget.removeAttribute("data-panning");
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return {
    stageRef,
    graphicRef,
    paint,
    schedulePaint,
    zoomAt,
    zoomBy,
    reset,
    dragged,
    dragProps: {
      onPointerDown: startDrag,
      onPointerMove: moveDrag,
      onPointerUp: endDrag,
      onPointerCancel: endDrag,
      onLostPointerCapture: endDrag,
      onDragStart: (event) => event.preventDefault(),
    },
  };
}
