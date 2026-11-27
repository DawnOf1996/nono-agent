import { ListTree } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { startDragSession } from "@/lib/drag-session";

type TocEntry = { level: number; text: string; el: HTMLElement };

const MIN_HEADINGS = 3;
/** 按钮边长（size-8）。吸到边上时靠它把留白算准。 */
const BUTTON_SIZE = 32;
/** 吸到边上之后离面板边缘的距离。 */
const EDGE = 8;
/** 指针走过这么多像素才算拖拽，否则算点击 —— 与 lib/use-pan-zoom 的 DRAG_SLOP 同一套判定。 */
const DRAG_SLOP = 3;

/**
 * 挂在预览滚动容器里的目录按钮。
 *
 * 它是浮在正文上方的一颗按钮，不是文档流里占位的一条栏：锚条高度为 0、顶边被
 * sticky 钉住，按钮自己绝对定位（规则见 index.css 的 .md-toc）。所以预览顶部
 * 不用为它留出空白，正文第一行也不会被压在下面。
 *
 * 往哪儿放由人拖，横向只会吸到左右两侧，纵向停在松手的地方。位置不进
 * localStorage —— 重新打开预览一律回右上角。
 */
export function MdToc() {
  const barRef = useRef<HTMLDivElement>(null);
  const [entries, setEntries] = useState<TocEntry[]>([]);
  const [open, setOpen] = useState(false);
  const signature = useRef("");
  /** 拖到哪儿了；null 表示还没拖过，停在右上角。 */
  const [spot, setSpot] = useState<{ x: number; y: number } | null>(null);
  const [side, setSide] = useState<"left" | "right">("right");
  const dragging = useRef<{ pointerX: number; pointerY: number; x: number; y: number } | null>(null);
  const moved = useRef(false);
  /** spot 的镜像：松手那刻 state 还没落，吸附要拿最新的位置算。 */
  const posRef = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const container = barRef.current?.parentElement;
    if (!container) return;
    const scan = () => {
      const found: TocEntry[] = [];
      for (const el of container.querySelectorAll<HTMLElement>("h1, h2, h3, h4, h5, h6")) {
        const text = (el.textContent ?? "").trim();
        // frontmatter 卡片目前只用 dl，但排除留在这儿：它哪天加了标题，
        // 或者将来支持 `title:` 之类渲染成 heading，目录都不该冒出一条"元信息"。
        if (!text || el.closest(".md-toc, [data-md-frontmatter]")) continue;
        found.push({ level: Number(el.tagName.charAt(1)), text, el });
      }
      const next = found.map((entry) => `${entry.level}:${entry.text}`).join("\n");
      if (next === signature.current) return;
      signature.current = next;
      setEntries(found);
    };
    scan();
    const observer = new MutationObserver(scan);
    observer.observe(container, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  /** 夹在面板内：x 以锚条（宽度 = 面板内容宽）为基准，y 以面板可视高为基准。 */
  function clamp(x: number, y: number): { x: number; y: number } {
    const bar = barRef.current;
    const panel = bar?.parentElement;
    if (!bar || !panel) return { x, y };
    return {
      x: Math.min(Math.max(x, EDGE), Math.max(EDGE, bar.clientWidth - BUTTON_SIZE - EDGE)),
      y: Math.min(Math.max(y, EDGE), Math.max(EDGE, panel.clientHeight - BUTTON_SIZE - EDGE)),
    };
  }

  function applySpot(x: number, y: number): void {
    const next = clamp(x, y);
    posRef.current = next;
    setSpot(next);
  }

  function beginDrag(event: React.PointerEvent<HTMLButtonElement>) {
    if (event.button !== 0) return;
    const bar = barRef.current;
    // 绝对定位的那一层，按钮坐标的基准。
    const holder = event.currentTarget.parentElement;
    if (!bar || !holder) return;
    const host = bar.getBoundingClientRect();
    const box = holder.getBoundingClientRect();
    moved.current = false;
    dragging.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      x: box.left - host.left,
      y: box.top - host.top,
    };
    startDragSession(event, {
      cursor: "grabbing",
      onMove: (move) => {
        const start = dragging.current;
        if (!start) return;
        const x = start.x + move.clientX - start.pointerX;
        const y = start.y + move.clientY - start.pointerY;
        if (!moved.current && (Math.abs(x - start.x) > DRAG_SLOP || Math.abs(y - start.y) > DRAG_SLOP)) {
          moved.current = true;
          // 拖走时把目录浮层收起来，不然它跟着按钮一路飘。
          setOpen(false);
        }
        applySpot(x, y);
      },
    });
  }

  /**
   * 松手：横向吸到更近的那一侧，纵向留在原处。
   *
   * 收在按钮的 onPointerUp 上，不靠 click —— startDragSession 在 pointerdown
   * 拦了默认行为，兼容鼠标事件不一定还来；pointerup 因为指针被按钮抓着，一定
   * 回到按钮身上，拖到面板外面松手也一样。
   */
  function settle() {
    const bar = barRef.current;
    const current = posRef.current;
    dragging.current = null;
    if (!bar || !current) return;
    const maxX = Math.max(EDGE, bar.clientWidth - BUTTON_SIZE - EDGE);
    const toRight = current.x + BUTTON_SIZE / 2 > bar.clientWidth / 2;
    setSide(toRight ? "right" : "left");
    applySpot(toRight ? maxX : EDGE, current.y);
  }

  return (
    <div ref={barRef} className="md-toc">
      {entries.length >= MIN_HEADINGS ? (
        <div
          className="pointer-events-auto absolute"
          style={spot ? { left: spot.x, top: spot.y } : { right: EDGE, top: EDGE }}
        >
          <button
            type="button"
            className="flex size-8 cursor-grab items-center justify-center rounded-lg border border-line bg-bg text-muted shadow-sm hover:bg-hover hover:text-ink active:cursor-grabbing"
            title="目录 · 可拖到左边或右边"
            aria-label="目录"
            aria-expanded={open}
            onPointerDown={beginDrag}
            onPointerUp={settle}
            onPointerCancel={() => {
              dragging.current = null;
            }}
            onClick={() => {
              // 拖完松手也会走到这里，别把这一下当成点击把浮层弹开。
              if (moved.current) {
                moved.current = false;
                return;
              }
              setOpen((value) => !value);
            }}
          >
            <ListTree className="size-4" />
          </button>
          {open ? (
            <div
              className={`absolute top-full mt-1 max-h-80 w-64 overflow-auto rounded-lg border border-line bg-bg py-1 shadow-lg ${
                side === "right" ? "right-0" : "left-0"
              }`}
            >
              {entries.map((entry, index) => (
                <button
                  key={`${entry.text}-${index}`}
                  type="button"
                  className="block w-full truncate px-3 py-1 text-left text-[12px] text-ink/80 hover:bg-hover"
                  style={{ paddingLeft: 8 + (entry.level - 1) * 12 }}
                  title={entry.text}
                  onClick={() => {
                    let node: HTMLElement | null = entry.el;
                    while (node) {
                      if (node instanceof HTMLDetailsElement) node.open = true;
                      node = node.parentElement;
                    }
                    entry.el.scrollIntoView({ behavior: "smooth", block: "start" });
                    setOpen(false);
                  }}
                >
                  {entry.text}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}