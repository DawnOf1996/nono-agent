import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";

import { ChatPane } from "@/features/chat/ChatPane";
import { WorkspacePanel } from "@/features/files/WorkspacePanel";
import { columnWidths, loadLayoutPreference, MENU_RAIL_WIDTH, saveLayoutPreference } from "@/features/shell/layout";
import { Sidebar } from "@/features/shell/Sidebar";

export function Workbench() {
  const containerRef = useRef<HTMLDivElement>(null);
  const initial = useRef(loadLayoutPreference());
  const [leftPreferred, setLeftPreferred] = useState(initial.current.leftPreferred);
  const [rightPreferred, setRightPreferred] = useState(initial.current.rightPreferred);
  const [leftOpen, setLeftOpen] = useState(initial.current.leftOpen);
  const [rightOpen, setRightOpen] = useState(initial.current.rightOpen);
  const [viewport, setViewport] = useState(() => window.innerWidth);
  const [resizing, setResizing] = useState<"left" | "right" | null>(null);
  const stopDrag = useRef<(() => void) | null>(null);

  const { left, right } = columnWidths(viewport, leftPreferred, rightPreferred, leftOpen, rightOpen);
  const menuOpen = leftOpen && left > 0;

  useEffect(() => {
    saveLayoutPreference({ leftPreferred, rightPreferred, leftOpen, rightOpen });
  }, [leftPreferred, rightPreferred, leftOpen, rightOpen]);

  useEffect(() => {
    function onResize() {
      setViewport(window.innerWidth);
    }
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(() => () => stopDrag.current?.(), []);

  function beginResize(side: "left" | "right", event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    stopDrag.current?.();
    setResizing(side);
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";

    function move(ev: PointerEvent) {
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
    }

    function end() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelect;
      setResizing(null);
      stopDrag.current = null;
    }

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
    stopDrag.current = end;
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
      <ChatPane
        rightOpen={rightOpen && right > 0}
        onToggleRight={() => setRightOpen((open) => !open)}
      />
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
