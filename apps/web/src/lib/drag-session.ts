import type { PointerEvent as ReactPointerEvent } from "react";

/**
 * 按住拖动（调分隔条、拖分栏）的一次拖拽会话。
 *
 * 为什么不能就地 window.addEventListener("pointermove", …) 就完事：
 * 分隔条旁边常常是内嵌的网页 —— 侧边浏览器的 <webview>、HTML 预览的 <iframe>。
 * 它们跑在各自的渲染进程里，指针一移进去，浏览器就把后续的 pointermove /
 * pointerup 派给了访客，宿主上的监听器再也收不到：分隔条停在指针进界面的那一像素
 * 不动，松手也没人收尾，body 上的 col-resize 和 user-select: none 会一直挂着。
 * 在 Electron 37 上实测过，按住分隔条往面板方向拖，宿主只收到进界面前的那一个移动
 * 事件，pointerup 直接被访客吞掉。
 *
 * 所以拖拽期间给 <html> 挂一个标记，把这些内嵌页面的指针事件关掉（规则在 index.css
 * 里就一条）：命中测试回到宿主文档，拖拽就不会断。开关挂在 <html> 上而不是逐个通知
 * 组件，以后再加内嵌页面也不用记得来登记。
 */
const DRAG_ATTR = "data-drag-session";

type DragSession = {
  /** 拖动中每一帧的指针位置。 */
  onMove: (event: PointerEvent) => void;
  /** 拖动期间的光标。指针一旦离开分隔条那几像素就靠它续上。 */
  cursor?: string;
  /** 拖动期间禁掉正文选区，默认开：按住横着拖会顺路划一片字。 */
  blockSelect?: boolean;
};

let current: (() => void) | null = null;

/** 收掉当前这次拖拽（如果有）。组件卸载时兜底调它，别把监听器和光标留在屏幕上。 */
export function endDragSession(): void {
  current?.();
}

/**
 * 从一次 pointerdown 起一个拖拽会话，返回收尾函数。
 *
 * 收尾函数可以直接当 effect 的清理用；不接也没关系，指针抬起时自己会收。
 */
export function startDragSession(event: ReactPointerEvent<HTMLElement>, session: DragSession): () => void {
  const { onMove, cursor, blockSelect = true } = session;
  // 不拦默认行为的话，按下会顺手在分隔条背后的正文里起一段选区。
  event.preventDefault();
  // 同一时间只该有一个在拖：上一个没收干净的先收掉。
  endDragSession();

  const root = document.documentElement;
  const host = event.currentTarget;
  // currentTarget 在事件派发完就被置空，这里必须留一份给收尾时用。
  const pointerId = event.pointerId;
  const previousCursor = document.body.style.cursor;
  const previousSelect = document.body.style.userSelect;
  root.setAttribute(DRAG_ATTR, "");
  if (cursor) document.body.style.cursor = cursor;
  if (blockSelect) document.body.style.userSelect = "none";
  // 指针交给分隔条抓着：拖出窗口外面再松手，pointerup 也会回到这里收尾。
  // 抓不住（指针已经抬起或失效）就算了，那只是少一条兜底路，不该把拖拽整个打断。
  try {
    host.setPointerCapture(pointerId);
  } catch {
    // 指针已失效。
  }

  let ended = false;
  function end() {
    if (ended) return;
    ended = true;
    window.removeEventListener("pointermove", move);
    window.removeEventListener("pointerup", end);
    window.removeEventListener("pointercancel", end);
    // 失焦（切窗口、锁屏）时 pointerup 收不到，在这里自己收尾，
    // 否则整个界面的光标会一直停在 col-resize 上。
    window.removeEventListener("blur", end);
    if (host.hasPointerCapture(pointerId)) host.releasePointerCapture(pointerId);
    root.removeAttribute(DRAG_ATTR);
    document.body.style.cursor = previousCursor;
    document.body.style.userSelect = previousSelect;
    current = null;
  }

  function move(ev: PointerEvent) {
    onMove(ev);
  }

  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
  window.addEventListener("blur", end);
  current = end;
  return end;
}
