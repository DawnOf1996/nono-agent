import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type DragEvent,
  type RefObject,
} from "react";
import { createPortal } from "react-dom";

import type { DropSide } from "@/features/shell/sidebar-list";

export type DragKind = "session" | "workspace";

/** 只有侧边栏自己的行会写这个 MIME，从别处拖进来的文本不会被误当成重排。 */
const ROW_MIME = "application/x-nono-sidebar-row";

/** 指针进入列表上下这条带子就开始滚，越靠边滚得越快。 */
const EDGE_ZONE = 56;
const EDGE_SPEED = 20;
/** 落点线离列表左边额外让开一点，免得贴着侧栏边缘。 */
const LINE_INSET = 10;

export interface RowRef {
  kind: DragKind;
  id: string;
}

export interface DropTarget extends RowRef {
  side: DropSide;
}

export interface RowDrag {
  /** 挂到行根节点上，落点线按它的上下沿算。 */
  ref: (node: HTMLElement | null) => void;
  draggable: boolean;
  /** 这一行正被拖起，画成半透明的占位。 */
  dragging: boolean;
  onDragStart: (event: DragEvent<HTMLElement>) => void;
  onDragEnd: () => void;
  onDragOver: (event: DragEvent<HTMLElement>) => void;
  onDrop: (event: DragEvent<HTMLElement>) => void;
}

function rowKey(row: RowRef): string {
  return `${row.kind}:${row.id}`;
}

/** 某些分组下某一行不可拖（比如树形里的工作区），给它一个什么都不做的句柄。 */
export const blankDrag: RowDrag = {
  ref: () => {},
  draggable: false,
  dragging: false,
  onDragStart: () => {},
  onDragEnd: () => {},
  onDragOver: () => {},
  onDrop: () => {},
};

function readRow(event: DragEvent<HTMLElement>): RowRef | null {
  const raw = event.dataTransfer.getData(ROW_MIME);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as Partial<RowRef>;
    if ((parsed.kind === "session" || parsed.kind === "workspace") && typeof parsed.id === "string") {
      return { kind: parsed.kind, id: parsed.id };
    }
  } catch {
    // 不是我们写的载荷
  }
  return null;
}

/**
 * 落点线：一道横贯列表的蓝线，左端带一个 > 标记，尖端正好接在线的起点上。
 *
 * 位置每帧都可能变（自动滚动、手动滚动、拖动），所以直接改 DOM 上的
 * left/width/top，不走 React state——否则整棵侧边栏会跟着每帧重渲染。
 */
function DropLine({ lineRef }: { lineRef: RefObject<HTMLDivElement | null> }) {
  return createPortal(
    // 容器是零高度的，top 边就是锚点：横线绕着它上下各铺 1px，箭头的 top-0
    // -translate-y-1/2 也对的是同一条边，两者才会严丝合缝地对齐。
    <div
      ref={lineRef}
      aria-hidden="true"
      className="pointer-events-none fixed left-0 top-0 z-50 h-0 w-0 opacity-0"
    >
      <div className="absolute left-0 top-[-1px] h-0.5 w-full rounded-full bg-drag" />
      <svg
        viewBox="0 0 8 12"
        width="8"
        height="12"
        className="absolute -left-1 top-0 -translate-y-1/2 text-drag"
      >
        <path
          d="M1 1 3.4 6 1 11"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </div>,
    document.body,
  );
}

export function useSidebarReorder(options: {
  /** 可滚动列表的根节点：落点线按它的内容区取宽，拖到边缘时滚它。 */
  scrollerRef: RefObject<HTMLElement | null>;
  onCommit: (source: RowRef, target: DropTarget) => void;
}) {
  const { scrollerRef, onCommit } = options;
  const [drag, setDrag] = useState<RowRef | null>(null);

  const nodes = useRef(new Map<string, HTMLElement>());
  const dragRef = useRef<RowRef | null>(null);
  const dropRef = useRef<DropTarget | null>(null);
  const lineRef = useRef<HTMLDivElement>(null);
  const edgeRef = useRef(0);
  const frameRef = useRef(0);
  const trackRef = useRef({ left: 0, width: 0 });

  const paint = useCallback((target: DropTarget | null) => {
    const el = lineRef.current;
    if (!el) return;
    const node = target ? nodes.current.get(rowKey(target)) : null;
    if (!target || !node) {
      el.style.opacity = "0";
      return;
    }
    const rect = node.getBoundingClientRect();
    el.style.top = `${(target.side === "before" ? rect.top : rect.bottom) - 1}px`;
    el.style.left = `${trackRef.current.left}px`;
    el.style.width = `${trackRef.current.width}px`;
    el.style.opacity = "1";
  }, []);

  const update = useCallback(
    (next: DropTarget | null) => {
      const current = dropRef.current;
      if (current === next) return;
      if (current && next && current.kind === next.kind && current.id === next.id && current.side === next.side) {
        return;
      }
      dropRef.current = next;
      paint(next);
    },
    [paint],
  );

  const reset = useCallback(() => {
    dragRef.current = null;
    dropRef.current = null;
    edgeRef.current = 0;
    if (frameRef.current) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = 0;
    }
    setDrag(null);
    paint(null);
  }, [paint]);

  /** 记下列表内容区的左右边界，落点线每次都横贯这一段。 */
  const measureTrack = useCallback(() => {
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const style = window.getComputedStyle(scroller);
    const padLeft = Number.parseFloat(style.paddingLeft) || 0;
    const padRight = Number.parseFloat(style.paddingRight) || 0;
    // clientWidth 已经排除了滚动条，滚动条出现/消失时线都不用重算。
    trackRef.current = {
      left: scroller.getBoundingClientRect().left + padLeft + LINE_INSET,
      width: Math.max(0, scroller.clientWidth - padLeft - padRight - LINE_INSET),
    };
  }, [scrollerRef]);

  const scrollStep = useCallback(
    function step() {
      frameRef.current = 0;
      const speed = edgeRef.current;
      const scroller = scrollerRef.current;
      if (!speed || !scroller) return;
      const before = scroller.scrollTop;
      scroller.scrollTop = before + speed;
      if (scroller.scrollTop !== before) paint(dropRef.current);
      frameRef.current = requestAnimationFrame(step);
    },
    [scrollerRef, paint],
  );

  const trackEdge = useCallback(
    (clientY: number) => {
      const scroller = scrollerRef.current;
      if (!scroller) return;
      const rect = scroller.getBoundingClientRect();
      let speed = 0;
      if (clientY < rect.top + EDGE_ZONE) {
        speed = -(rect.top + EDGE_ZONE - clientY) / 5;
      } else if (clientY > rect.bottom - EDGE_ZONE) {
        speed = (clientY - (rect.bottom - EDGE_ZONE)) / 5;
      }
      const next = Math.round(Math.max(-EDGE_SPEED, Math.min(EDGE_SPEED, speed)));
      if (next === edgeRef.current) return;
      edgeRef.current = next;
      if (next === 0) {
        if (frameRef.current) cancelAnimationFrame(frameRef.current);
        frameRef.current = 0;
      } else if (!frameRef.current) {
        frameRef.current = requestAnimationFrame(scrollStep);
      }
    },
    [scrollerRef, scrollStep],
  );

  // 落点线跟着列表滚动走，不然手动滚一下线就错位了。
  useEffect(() => {
    const scroller = scrollerRef.current;
    if (!drag || !scroller) return;
    const onScroll = () => paint(dropRef.current);
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [drag, scrollerRef, paint]);

  // 拖出列表范围就收起落点线。行内的 dragover 虽然 stopPropagation，
  // 原生事件照样冒到 window，所以这里只能按坐标判断，不能指望事件不冒泡。
  useEffect(() => {
    if (!drag) return;
    const onWindowDragOver = (event: globalThis.DragEvent) => {
      const scroller = scrollerRef.current;
      if (!scroller) return;
      const rect = scroller.getBoundingClientRect();
      const inside =
        event.clientX >= rect.left &&
        event.clientX <= rect.right &&
        event.clientY >= rect.top &&
        event.clientY <= rect.bottom;
      if (!inside) update(null);
    };
    window.addEventListener("dragover", onWindowDragOver);
    return () => window.removeEventListener("dragover", onWindowDragOver);
  }, [drag, scrollerRef, update]);

  useEffect(() => {
    if (drag) paint(dropRef.current);
  }, [drag, paint]);

  useEffect(
    () => () => {
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    },
    [],
  );

  /** 挂到滚动列表根节点上：指针落在行与行之间时撤掉落点线。 */
  const scrollerProps = {
    onDragOver: (event: DragEvent<HTMLElement>) => {
      if (!dragRef.current) return;
      trackEdge(event.clientY);
      update(null);
    },
    onDrop: (event: DragEvent<HTMLElement>) => {
      if (!dragRef.current) return;
      event.preventDefault();
      reset();
    },
  };

  function bind(kind: DragKind, id: string, enabled: boolean): RowDrag {
    const row: RowRef = { kind, id };
    return {
      ref: (node) => {
        const key = rowKey(row);
        if (node) nodes.current.set(key, node);
        else nodes.current.delete(key);
      },
      draggable: enabled,
      dragging: enabled && drag?.kind === kind && drag.id === id,
      onDragStart: (event) => {
        if (!enabled) return;
        dragRef.current = row;
        measureTrack();
        setDrag(row);
        event.dataTransfer.setData(ROW_MIME, JSON.stringify(row));
        event.dataTransfer.effectAllowed = "move";
      },
      onDragEnd: reset,
      onDragOver: (event) => {
        const source = dragRef.current;
        if (!enabled || !source || source.kind !== kind) return;
        event.preventDefault();
        event.stopPropagation();
        event.dataTransfer.dropEffect = "move";
        trackEdge(event.clientY);
        if (source.id === id) {
          update(null);
          return;
        }
        const rect = event.currentTarget.getBoundingClientRect();
        update({ kind, id, side: event.clientY < rect.top + rect.height / 2 ? "before" : "after" });
      },
      onDrop: (event) => {
        if (!enabled) return;
        event.preventDefault();
        event.stopPropagation();
        const target = dropRef.current;
        const source = dragRef.current ?? readRow(event);
        reset();
        if (!source || !target || source.kind !== target.kind || source.id === target.id) return;
        onCommit(source, target);
      },
    };
  }

  return { bind, scrollerProps, line: drag ? <DropLine lineRef={lineRef} /> : null };
}
