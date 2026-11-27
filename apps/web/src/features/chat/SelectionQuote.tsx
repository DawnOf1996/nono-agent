import { Check, Copy, MessageSquarePlus, Reply, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { createPortal } from "react-dom";

import { useChatStore } from "@/store/chat";
import { selectionText } from "@/lib/selection-text";
import { relativeTo } from "@/lib/workspace";

type Anchor = {
  text: string;
  top: number;
  left: number;
  above: boolean;
};

/** 选区末端的一段几何，气泡贴着它摆位。 */
type Rect = { top: number; bottom: number; right: number };

/**
 * 选区落在哪一类宿主里。
 *
 * assistant：模型自己的回复，和文件无关，纯引用。
 * file：某个文件预览。行号怎么算交给宿主自己回答（data-line 契约），
 * 这里不猜——猜错了模型会拿着错行号去改错的行。
 */
type Host = { kind: "assistant" } | { kind: "file"; el: HTMLElement; root: string; path: string };

/** 行号解析：优先用 data-line（Markdown 预览注入），退回 data-line-number（代码预览的行号 span）。 */
function lineOf(node: Node): number | null {
  const element = node instanceof Element ? node : node.parentElement;
  if (!element) return null;
  const marked = element.closest<HTMLElement>("[data-line]");
  if (marked) {
    const offsetEl = element.closest<HTMLElement>("[data-line-offset]");
    // 缺偏移容器说明这是全文第一段，基准就是 1（不是 0 —— 那样第 1 行会算成 0 行）。
    const offset = offsetEl ? Number(offsetEl.dataset.lineOffset) : 1;
    const inSegment = Number(marked.dataset.line);
    if (Number.isFinite(inSegment)) return (Number.isFinite(offset) ? offset : 1) + Math.max(1, inSegment) - 1;
  }
  const numbered = element.closest<HTMLElement>("[data-line-number]");
  if (numbered) {
    const value = Number(numbered.dataset.lineNumber);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

function hostOf(node: Node, root: HTMLElement): Host | null {
  const element = node instanceof Element ? node : node.parentElement;
  const hit = element?.closest<HTMLElement>("[data-quote-source]");
  if (!hit || !root.contains(hit)) return null;
  const kind = hit.dataset.quoteSource;
  if (kind === "assistant") return { kind: "assistant" };
  if (kind === "file") {
    const path = hit.dataset.filePath;
    if (!path) return null;
    return { kind: "file", el: hit, root: hit.dataset.fileRoot ?? "", path };
  }
  return null;
}

/**
 * 一次选区的完整判定结果。
 *
 * rect 得留着：气泡是按**实测**盒子尺寸摆的（理由见 BAR_FALLBACK），
 * 而气泡尺寸本身会变 —— 折叠态三个按钮约 232px，点开评论是 w-72（288px），
 * 「复制」变成「已复制」又宽几个字。尺寸一变就要拿 rect 重新摆一次；
 * 只留算好的 anchor 就没依据了，只能继续用估值，而估值偏小正是溢出的来源。
 */
type Found = {
  anchor: Anchor;
  host: Host;
  startLine: number | null;
  endLine: number | null;
  rect: Rect;
};

/**
 * 外部选区源。
 *
 * CodeMirror 是自绘的，浏览器选区拿不到它的选区，只能问编辑器本身要。
 * 传了这个源，DOM 选区那条路就整个让位给它。
 */
export type ExternalSelection = {
  read: () => {
    text: string;
    startLine: number;
    endLine: number;
    rect: { top: number; bottom: number; left: number; right: number };
  } | null;
  /** 选区属于哪个文件。 */
  root: string;
  path: string;
};

function locate(root: HTMLElement, size: { width: number; height: number }): Found | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  // 走 selectionText 而不是 selection.toString()：后者会把 KaTeX 的 MathML 副本
  // 和字形副本一起抄进来，选一个公式出来是三份重复的乱码。selectionText 会把
  // 公式还原成 $...$ / $$...$$。见 lib/selection-text.ts。
  const text = selectionText(range).trim();
  if (!text) return null;
  const startHost = hostOf(range.startContainer, root);
  const endHost = hostOf(range.endContainer, root);
  if (!startHost || !endHost || startHost.kind !== endHost.kind) return null;
  // 跨文件拖选没有意义（同一次评论只对应一个文件）。
  if (startHost.kind === "file" && endHost.kind === "file" && startHost.el !== endHost.el) return null;

  // 多行选区时 getBoundingClientRect 给的是包围盒，气泡会飘到很远；
  // 取最后一个非零 rect（选区末端那一行）才是贴住选区的位置。
  const rects = range.getClientRects();
  let rect = range.getBoundingClientRect();
  for (let index = rects.length - 1; index >= 0; index -= 1) {
    const item = rects[index];
    if (item.width > 0 || item.height > 0) {
      rect = item;
      break;
    }
  }
  if (rect.width === 0 && rect.height === 0) return null;

  const rootRect = root.getBoundingClientRect();
  if (rect.bottom < rootRect.top + 4 || rect.top > rootRect.bottom - 4) return null;

  return {
    anchor: { text, ...placeAnchor(root, rect, size) },
    host: startHost,
    startLine: lineOf(range.startContainer),
    endLine: lineOf(range.endContainer),
    rect,
  };
}

/** 行号范围文案：13 / 13-18。 */
function lineLabel(start: number | null, end: number | null): string | null {
  if (start === null) return null;
  return end !== null && end !== start ? `${start}-${end}` : String(start);
}

/** 气泡和容器边界之间留的白。 */
const BAR_MARGIN = 8;

/**
 * 气泡挂上去之前的兜底尺寸，量到真值后就换成实测的。
 *
 * 这里宁可估大也不能估小：left 是按「root 左缘 + margin + 宽度」钳的，宽度一小，
 * left 就钳得太靠右，气泡整体往左伸出去的部分就越过 root 左缘 —— 选区在左边时
 * 尤其明显，288px 的评论框会整块压到左侧菜单栏上。
 */
const BAR_FALLBACK = { width: 240, height: 48 };

/**
 * 气泡挂在选区哪儿。rect 是视口坐标，size 必须是气泡的实测盒子尺寸。
 *
 * 右边缘对齐 left、整体往左伸（对应 JSX 里的 translateX(-100%)），所以钳位按
 * 「往左伸出去 width」算，不是按「往右还剩多少空间」算。
 */
function placeAnchor(
  root: HTMLElement,
  rect: Rect,
  size: { width: number; height: number },
): { top: number; left: number; above: boolean } {
  const rootRect = root.getBoundingClientRect();
  let left = rect.right;
  left = Math.max(rootRect.left + BAR_MARGIN + size.width, left);
  left = Math.min(window.innerWidth - BAR_MARGIN, left);
  const above = rect.top - size.height - 6 >= rootRect.top + BAR_MARGIN;
  return { top: above ? rect.top - 6 : rect.bottom + 6, left, above };
}

/** 在模型回复或文件预览里选中文字后，浮出复制 / 评论 / 添加到对话。 */
export function SelectionQuote({
  containerRef,
  external,
}: {
  containerRef: RefObject<HTMLElement | null>;
  external?: ExternalSelection | null;
}) {
  const addQuote = useChatStore((state) => state.addQuote);
  const [found, setFound] = useState<Found | null>(null);
  const [commenting, setCommenting] = useState(false);
  const [draft, setDraft] = useState("");
  const [copied, setCopied] = useState(false);
  const barRef = useRef<HTMLDivElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);
  // 气泡的实测尺寸。用 ref 而不是 state：装事件监听的那个 effect 一旦因 state 变化
  // 而重跑，清理函数会 setFound(null)，刚弹出来的气泡会被自己关掉。
  const sizeRef = useRef(BAR_FALLBACK);

  const anchor = found?.anchor ?? null;
  const host = found?.host ?? null;
  const startLine = found?.startLine ?? null;
  const endLine = found?.endLine ?? null;

  const isFile = host?.kind === "file";
  const fileMeta = useMemo(() => {
    if (!isFile || host?.kind !== "file") return null;
    const path = relativeTo(host.root, host.path).replace(/\\/g, "/");
    const label = lineLabel(startLine, endLine);
    return { path, label: label ? `${path}:${label}` : path };
  }, [host, isFile, startLine, endLine]);

  const close = useCallback(() => {
    setFound(null);
    setCommenting(false);
    setDraft("");
    setCopied(false);
  }, []);

  useEffect(() => {
    const root = containerRef.current;
    if (!root) return;

    let selecting = false;
    let timer = 0;

    // 外部源（CodeMirror）优先；没有就走浏览器选区。
    const read = (): Found | null => {
      if (external) {
        const picked = external.read();
        if (!picked) return null;
        const position = placeAnchor(root, picked.rect, sizeRef.current);
        return {
          anchor: { text: picked.text, ...position },
          host: { kind: "file", el: root, root: external.root, path: external.path },
          startLine: picked.startLine,
          endLine: picked.endLine,
          rect: picked.rect,
        };
      }
      return locate(root, sizeRef.current);
    };

    const place = () => {
      timer = 0;
      const next = read();
      setFound(next);
      // 新选区就是新的一次引用。上一轮点开「评论」却没写内容时 commenting 仍是 true，
      // 不在这里清掉的话新气泡会直接以评论框的形态冒出来，而不是三按钮的工具条。
      // 只在真选到东西时清：点空白处收工走的是 onSelectionChange，那条路不该顺手丢草稿。
      if (next !== null) {
        setCommenting(false);
        setDraft("");
      }
    };
    const schedule = () => {
      if (timer) return;
      timer = window.setTimeout(place, 0);
    };

    function onPointerDown(event: PointerEvent) {
      if (event.button !== 0) return;
      // 按在自己身上（含评论输入框）不算"点到别处"。
      // 这里必须放行：一旦在这里清掉，点进 textarea 时输入框会先被卸载，
      // 表现为"点了评论框毫无反应"。
      if (barRef.current?.contains(event.target as Node)) return;
      selecting = true;
      if (timer) {
        window.clearTimeout(timer);
        timer = 0;
      }
      setFound(null);
    }

    function onPointerUp(event: PointerEvent) {
      if (event.button !== 0 || !selecting) return;
      selecting = false;
      schedule();
    }

    const onSelectionChange = () => {
      // 正在拖选、或正在评论时不回收：此时选区本来就不在文档里了，
      // 回收会把正在用的气泡整个销毁。
      if (selecting || barRef.current?.contains(document.activeElement)) return;
      if (external) return;
      setFound((current) => (current && !locate(root, sizeRef.current) ? null : current));
    };

    const onScroll = () => {
      if (selecting) return;
      setFound((current) => (current ? read() : current));
    };

    // 编辑器里用方向键/Shift+方向键调整选区时也要跟上。
    const onKeyActivity = () => {
      if (selecting) return;
      schedule();
    };
    // 刻意不监听 focusin：在预览里点选文字本身就带焦点，
    // 一旦在这里收起，刚弹出来的工具条会被自己关掉。
    // "点到别处才关闭"由 onPointerDown 负责，那才是准确的时机。

    document.addEventListener("pointerdown", onPointerDown);
    window.addEventListener("pointerup", onPointerUp);
    document.addEventListener("selectionchange", onSelectionChange);
    // 捕获阶段：MarkdownBody / CodeBody 自己就是滚动壳，只听 root 不够。
    root.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onScroll);
    // CodeMirror 自绘选区，松手后 DOM 里没有 selectionchange 可听，
    // 只能跟着它的鼠标事件走一遍。
    if (external) {
      root.addEventListener("pointerup", onPointerUp);
      root.addEventListener("keyup", onKeyActivity);
    }
    return () => {
      if (timer) window.clearTimeout(timer);
      document.removeEventListener("pointerdown", onPointerDown);
      window.removeEventListener("pointerup", onPointerUp);
      document.removeEventListener("selectionchange", onSelectionChange);
      root.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onScroll);
      root.removeEventListener("pointerup", onPointerUp);
      root.removeEventListener("keyup", onKeyActivity);
      setFound(null);
    };
  }, [containerRef, external?.root, external?.path]);

  /**
   * 量到真尺寸后重新摆一次位。
   *
   * 气泡尺寸不是常量：折叠态三个按钮约 232×34，点开评论变成 w-72（288×150），
   * 「复制」变「已复制」又宽几个字。摆位用的 width 一旦偏小，left 就钳得太靠右，
   * 往左伸出去的部分就越过 root 左缘 —— 选区在左半边时，评论框会压到左侧菜单栏上。
   *
   * commenting / copied 进依赖：它们正是改尺寸的那两个开关。写回走 setFound，
   * 不会触发上面装监听的 effect，所以不会把正在用的气泡自己关掉；改完再量一次，
   * 尺寸没变就直接收手，不会来回抖。
   */
  useLayoutEffect(() => {
    const bar = barRef.current;
    if (!bar) return;
    const width = bar.offsetWidth;
    const height = bar.offsetHeight;
    const known = sizeRef.current;
    if (width === 0 || (known.width === width && known.height === height)) return;
    sizeRef.current = { width, height };
    setFound((current) => {
      const root = containerRef.current;
      if (current === null || root === null) return current;
      return {
        ...current,
        anchor: { text: current.anchor.text, ...placeAnchor(root, current.rect, sizeRef.current) },
      };
    });
  }, [found, commenting, copied, containerRef]);

  useEffect(() => {
    setCopied(false);
  }, [anchor?.text]);

  // 打开评论框时主动抢焦点，否则用户还得再点一次才输得进去。
  useEffect(() => {
    if (!commenting) return;
    const area = areaRef.current;
    if (!area) return;
    area.focus();
    const end = area.value.length;
    area.setSelectionRange(end, end);
  }, [commenting]);

  useEffect(() => {
    if (!anchor) return;
    function onKey(event: KeyboardEvent) {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      window.getSelection()?.removeAllRanges();
      close();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [anchor, close]);

  if (!anchor) return null;

  const submit = (comment?: string) => {
    const body = comment?.trim();
    if (isFile && fileMeta) {
      addQuote(anchor.text, "file", {
        path: fileMeta.path,
        startLine: startLine ?? undefined,
        endLine: endLine ?? undefined,
        ...(body ? { comment: body } : {}),
      });
    } else {
      addQuote(anchor.text, "assistant", body ? { comment: body } : undefined);
    }
    window.getSelection()?.removeAllRanges();
    close();
  };

  return createPortal(
    <div
      ref={barRef}
      role="toolbar"
      aria-label="选中文字"
      // w-max 不能去掉：工具条是 fixed + translateX(-100%)，右边缘对齐 anchor.left、整体往左伸。
      // 而 width:auto 的 fixed 元素走 shrink-to-fit，浏览器量的是「left 到视口右边」那段可用宽度，
      // 和它实际占据的方向正好相反。选区一靠右，这段宽度就窄于工具条的自然宽度，工具条被压扁，
      // 三个按钮里的中文被挤成逐字竖排（placeAnchor 里 barWidth=220 只是摆位用的估值，
      // 它管的是别越出 root 左缘，压根没参与宽度计算）。显式 max-content 才能锁住盒子宽度。
      className="fixed z-40 flex w-max flex-col rounded-xl border border-line bg-bg p-1 shadow-[0_8px_24px_var(--shadow-color)]"
      style={{
        top: anchor.top,
        left: anchor.left,
        transform: anchor.above ? "translate(-100%, -100%)" : "translate(-100%, 0)",
      }}
      // 只对按钮挡默认行为：点了就丢掉选区，文本和几何都取不到了。
      // 输入框必须放行，否则 preventDefault 会让它永远拿不到焦点，
      // 表现就是"点进评论框打不了字"。
      onPointerDown={(event) => {
        const target = event.target as HTMLElement | null;
        if (target?.closest("textarea")) return;
        event.preventDefault();
      }}
    >
      {commenting ? (
        <div className="flex w-72 flex-col gap-1 p-1">
          {fileMeta ? (
            <div className="truncate px-1 text-[11px] text-faint" title={fileMeta.label}>
              {fileMeta.label}
            </div>
          ) : null}
          <textarea
            ref={areaRef}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (draft.trim()) submit(draft);
                return;
              }
              if (event.key === "Escape") {
                event.stopPropagation();
                // 退评论回到工具条，而不是整个关掉。
                setCommenting(false);
                setDraft("");
              }
            }}
            placeholder="写下你的评论…"
            rows={3}
            className="w-full resize-none rounded-lg border border-line bg-bg px-2 py-1.5 text-[13px] leading-5 text-ink outline-none placeholder:text-faint focus:border-active"
          />
          <div className="flex items-center justify-end gap-1">
            <span className="flex-1 text-[11px] text-faint">Enter 发送</span>
            <button
              type="button"
              className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
              title="取消评论"
              aria-label="取消评论"
              onClick={() => {
                setCommenting(false);
                setDraft("");
              }}
            >
              <X className="size-3.5" />
            </button>
            <button
              type="button"
              disabled={!draft.trim()}
              className="flex size-7 items-center justify-center rounded-full bg-active text-ink disabled:opacity-40"
              title="评论并添加到对话"
              aria-label="评论并添加到对话"
              onClick={() => {
                if (draft.trim()) submit(draft);
              }}
            >
              <Check className="size-3.5" />
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center gap-0.5">
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[13px] text-ink hover:bg-hover"
            onClick={() => {
              void navigator.clipboard.writeText(anchor.text).then(
                () => setCopied(true),
                () => setCopied(false),
              );
            }}
          >
            {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
            {copied ? "已复制" : "复制"}
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[13px] text-ink hover:bg-hover"
            title={isFile ? "针对这段写评论，并一起添加到对话" : "针对这段写评论"}
            onClick={() => setCommenting(true)}
          >
            <MessageSquarePlus className="size-3.5" />
            评论
          </button>
          <button
            type="button"
            className="flex shrink-0 items-center gap-1 whitespace-nowrap rounded-md px-2 py-1 text-[13px] text-ink hover:bg-hover"
            title={isFile ? "引用这段（不带评论）" : "引用这段"}
            onClick={() => submit()}
          >
            <Reply className="size-3.5" />
            添加到对话
          </button>
        </div>
      )}
    </div>,
    document.body,
  );
}