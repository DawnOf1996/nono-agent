import { useEffect, useRef, useState } from "react";

import {
  pickElement,
  probeElement,
  type HoverProbe,
  type PickedElement,
  type WebviewElement,
} from "@/features/files/browser-tools";

/**
 * DOM 元素拾取器。
 *
 * 悬停高亮 + 点击选中，最后把元素的地址、选择器、outerHTML 作为一条「网页」引用发出去。
 *
 * 高亮画在宿主这边，不往访客页注入 outline 样式：访客页在另一个浏览上下文里，注入的
 * 样式得由宿主负责清理，中途导航或页面自己改 DOM 都会留成脏样式。代价是每帧都要问一次
 * 访客页「这个点上是什么」，所以用 rAF 合并 —— mousemove 的频率比一帧高得多。
 *
 * webview 开了 sandbox 且没有 Node，但 executeJavaScript 是宿主主动调用，在不在沙箱里
 * 都照常执行，所以这条路走得通。
 */
export function ElementPicker({
  tag,
  onCancel,
  onSubmit,
}: {
  tag: WebviewElement | null;
  onCancel: () => void;
  onSubmit: (element: PickedElement) => void;
}) {
  const [probe, setProbe] = useState<HoverProbe | null>(null);
  const [picking, setPicking] = useState(false);
  const frameRef = useRef(0);
  const pointRef = useRef<{ x: number; y: number } | null>(null);
  /** 组件可能先于最后一次探测返回就卸载了，回来的结果得丢掉。 */
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
      if (frameRef.current) cancelAnimationFrame(frameRef.current);
    };
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  function flush() {
    frameRef.current = 0;
    const point = pointRef.current;
    const current = tag;
    if (!point || !current) return;
    void probeElement(current, point.x, point.y).then((next) => {
      // 问不到东西（正在导航 / 上下文销毁）就清掉高亮，留着旧框反而误导。
      if (aliveRef.current) setProbe(next);
    });
  }

  function onPointerMove(event: React.PointerEvent<HTMLDivElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    pointRef.current = { x: event.clientX - box.left, y: event.clientY - box.top };
    if (!frameRef.current) frameRef.current = requestAnimationFrame(flush);
  }

  function onPointerDown(event: React.PointerEvent<HTMLDivElement>) {
    const current = tag;
    if (!current || event.button !== 0) return;
    setPicking(true);
    const box = event.currentTarget.getBoundingClientRect();
    const x = event.clientX - box.left;
    const y = event.clientY - box.top;
    void pickElement(current, x, y).then((element) => {
      if (!aliveRef.current) return;
      setPicking(false);
      // 点到空白处（访客页上没元素，或正好是根元素之外）会拿到 null，那不是一次有效拾取。
      if (element) onSubmit(element);
    });
  }

  return (
    <div
      className="absolute inset-0 z-10 cursor-crosshair select-none overflow-hidden"
      onPointerMove={onPointerMove}
      onPointerDown={onPointerDown}
    >
      {probe && probe.width > 0 && probe.height > 0 ? (
        <div
          className="pointer-events-none absolute border-2 border-accent bg-accent/10"
          style={{
            left: probe.x,
            top: probe.y,
            width: probe.width,
            height: probe.height,
          }}
        >
          <span className="absolute -top-6 left-0 flex items-center gap-1.5 whitespace-nowrap rounded bg-accent px-1.5 py-0.5 text-[11px] text-bg">
            &lt;{probe.tag}&gt;
            {probe.label ? <span className="max-w-40 truncate opacity-80">{probe.label}</span> : null}
            <span className="opacity-60">
              {Math.round(probe.width)}×{Math.round(probe.height)}
            </span>
          </span>
        </div>
      ) : null}
      <div className="pointer-events-none absolute inset-x-0 top-1/2 -translate-y-1/2 text-center">
        <p className="text-[13px] text-white drop-shadow">
          {picking ? "正在读取元素…" : "移动鼠标高亮，点击选中这个元素"}
        </p>
        <p className="mt-1 text-[11px] text-white/70 drop-shadow">Esc 取消</p>
      </div>
    </div>
  );
}
