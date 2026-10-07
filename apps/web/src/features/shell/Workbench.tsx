import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { ChatPane } from "@/features/chat/ChatPane";
import { BottomWorkbench } from "@/features/files/BottomWorkbench";
import { usePanelStore } from "@/features/files/panel-store";
import { WorkspacePanel } from "@/features/files/WorkspacePanel";
import { BOTTOM_MIN, columnWidths, loadLayoutPreference, MENU_RAIL_WIDTH, saveLayoutPreference } from "@/features/shell/layout";
import { Sidebar } from "@/features/shell/Sidebar";
import { startDragSession } from "@/lib/drag-session";

export function Workbench() {
  const containerRef = useRef<HTMLDivElement>(null);
  const initial = useRef(loadLayoutPreference());
  const [leftPreferred, setLeftPreferred] = useState(initial.current.leftPreferred);
  const [rightPreferred, setRightPreferred] = useState(initial.current.rightPreferred);
  const [leftOpen, setLeftOpen] = useState(initial.current.leftOpen);
  const [rightOpen, setRightOpen] = useState(initial.current.rightOpen);
  const [bottomOpen, setBottomOpen] = useState(initial.current.bottomOpen);
  const [bottomHeight, setBottomHeight] = useState(initial.current.bottomHeight);
  const [viewport, setViewport] = useState(() => window.innerWidth);
  const [resizing, setResizing] = useState<"left" | "right" | null>(null);
  const stopDrag = useRef<(() => void) | null>(null);

  const { left, right } = columnWidths(viewport, leftPreferred, rightPreferred, leftOpen, rightOpen);
  const menuOpen = leftOpen && left > 0;

  useEffect(() => {
    saveLayoutPreference({ leftPreferred, rightPreferred, leftOpen, rightOpen, bottomOpen, bottomHeight });
  }, [leftPreferred, rightPreferred, leftOpen, rightOpen, bottomOpen, bottomHeight]);

  useEffect(() => {
    function onResize() {
      setViewport(window.innerWidth);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => () => stopDrag.current?.(), []);

  const revealRequest = usePanelStore((state) => state.revealRequest);
  const bottomRequest = usePanelStore((state) => state.bottomRequest);
  const sidePath = usePanelStore((state) => state.sideFile?.path ?? null);
  const firstReveal = useRef(revealRequest);
  const firstBottom = useRef(bottomRequest);
  useEffect(() => {
    if (revealRequest !== firstReveal.current) setRightOpen(true);
  }, [revealRequest]);
  useEffect(() => {
    if (bottomRequest !== firstBottom.current) setBottomOpen(true);
  }, [bottomRequest]);
  useEffect(() => {
    if (!sidePath) return;
    setRightOpen(true);
    setRightPreferred((width) => (width < 680 ? 680 : width));
  }, [sidePath]);

  function beginResize(side: "left" | "right", event: ReactPointerEvent<HTMLDivElement>) {
    setResizing(side);
    // 拖动中不能被内嵌的网页抢走指针事件：浏览器面板一开，鼠标划进去分隔条就不动了。
    const end = startDragSession(event, {
      cursor: "col-resize",
      onMove: (ev) => {
        const box = containerRef.current?.getBoundingClientRect();
        if (!box) return;
        const width = window.innerWidth;
        setViewport(width);
        if (side === "left") {
          const next = columnWidths(width, Math.max(0, ev.clientX - box.left), rightPreferred, true, rightOpen);
          setLeftPreferred(next.left);
          return;
        }
        const next = columnWidths(width, leftPreferred, Math.max(0, box.right - ev.clientX), leftOpen, true);
        setRightPreferred(next.right);
      },
    });
    // 分隔条的高亮跟着会话走，会话怎么结束它就怎么撤。
    stopDrag.current = () => {
      end();
      setResizing(null);
    };
  }

  return (
    <div ref={containerRef} className="flex h-full min-h-0 min-w-0 flex-1 overflow-hidden">
      <div className="relative h-full shrink-0" style={{ width: menuOpen ? left : MENU_RAIL_WIDTH }}>
        <Sidebar
          expanded={menuOpen}
          onExpand={() => setLeftOpen(true)}
          onCollapse={() => setLeftOpen(false)}
        />
        {menuOpen ? (
          <div
            role="separator"
            aria-orientation="vertical"
            aria-label="调整菜单宽度"
            aria-valuenow={left}
            aria-valuemax={Math.floor(viewport * 0.2)}
            className="group absolute inset-y-0 -right-1.5 z-20 w-3 cursor-col-resize touch-none"
            onPointerDown={(event) => beginResize("left", event)}
          >
            <div
              className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 ${
                resizing === "left" ? "bg-ink/40" : "bg-transparent group-hover:bg-ink/30"
              }`}
            />
          </div>
        ) : null}
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <ChatPane
          rightOpen={rightOpen && right > 0}
          onToggleRight={() => setRightOpen((open) => !open)}
          bottomOpen={bottomOpen}
          onToggleBottom={() => setBottomOpen((open) => !open)}
        />
        {bottomOpen ? (
          <div className="relative shrink-0 border-t border-line" style={{ height: bottomHeight }}>
            <div
              role="separator"
              aria-orientation="horizontal"
              aria-label="调整底部工作台高度"
              aria-valuenow={bottomHeight}
              className="group absolute inset-x-0 -top-1.5 z-20 h-3 cursor-row-resize touch-none"
              onPointerDown={(event) => {
                const startY = event.clientY;
                const startHeight = bottomHeight;
                const limit = Math.max(BOTTOM_MIN, window.innerHeight - 280);
                const end = startDragSession(event, {
                  cursor: "row-resize",
                  onMove: (ev) => {
                    const next = Math.min(limit, Math.max(BOTTOM_MIN, startHeight + (startY - ev.clientY)));
                    setBottomHeight(next);
                  },
                });
                stopDrag.current = end;
              }}
            >
              <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-transparent group-hover:bg-ink/30" />
            </div>
            <BottomWorkbench />
          </div>
        ) : null}
      </div>
      <div
        className={`relative h-full shrink-0 ${rightOpen && right > 0 ? "" : "hidden"}`}
        style={{ width: right }}
        aria-hidden={!(rightOpen && right > 0)}
      >
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="调整文件栏宽度"
          aria-valuenow={right}
          className="group absolute inset-y-0 -left-1.5 z-20 w-3 cursor-col-resize touch-none"
          onPointerDown={(event) => beginResize("right", event)}
        >
          <div
            className={`absolute inset-y-0 left-1/2 w-px -translate-x-1/2 ${
              resizing === "right" ? "bg-ink/40" : "bg-transparent group-hover:bg-ink/30"
            }`}
          />
        </div>
        <WorkspacePanel onCollapse={() => setRightOpen(false)} />
      </div>
    </div>
  );
}
