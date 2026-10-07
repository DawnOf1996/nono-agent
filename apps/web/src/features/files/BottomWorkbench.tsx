import { useState, type DragEvent } from "react";

import { DockedTab } from "@/features/files/DockedTab";
import { usePanelStore, type PanelTab } from "@/features/files/panel-store";
import { collectTabIds, type DropZone, type SplitNode } from "@/features/files/split";
import { startDragSession } from "@/lib/drag-session";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const DRAG_TYPE = "application/x-nono-tab";

export function readTabDrag(data: DataTransfer): string | null {
  const id = data.getData(DRAG_TYPE);
  return id || null;
}

export function writeTabDrag(data: DataTransfer, tabId: string) {
  data.setData(DRAG_TYPE, tabId);
  data.effectAllowed = "move";
}

function zoneAt(event: DragEvent<HTMLElement>): DropZone {
  const rect = event.currentTarget.getBoundingClientRect();
  if (rect.width === 0 || rect.height === 0) return "center";
  const x = (event.clientX - rect.left) / rect.width;
  const y = (event.clientY - rect.top) / rect.height;
  if (x < 0.22) return "left";
  if (x > 0.78) return "right";
  if (y < 0.22) return "up";
  if (y > 0.78) return "down";
  return "center";
}

function LeafPane({
  leaf,
  tabs,
  root,
  revision,
}: {
  leaf: { id: string; tabIds: string[]; active: string | null };
  tabs: PanelTab[];
  root: string;
  revision: number;
}) {
  const dockTab = usePanelStore((state) => state.dockTab);
  const focusDocked = usePanelStore((state) => state.focusDocked);
  const close = usePanelStore((state) => state.close);
  const [zone, setZone] = useState<DropZone | null>(null);
  const open = tabs.filter((tab) => leaf.tabIds.includes(tab.id));
  const activeId = open.some((tab) => tab.id === leaf.active) ? leaf.active : open[open.length - 1]?.id ?? null;

  return (
    <div
      className="relative flex min-h-0 min-w-0 flex-1 flex-col"
      onDragOver={(event) => {
        if (![...event.dataTransfer.types].includes(DRAG_TYPE)) return;
        event.preventDefault();
        setZone(zoneAt(event));
      }}
      onDragLeave={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setZone(null);
      }}
      onDrop={(event) => {
        const tabId = readTabDrag(event.dataTransfer);
        const next = zone ?? zoneAt(event);
        setZone(null);
        if (!tabId) return;
        event.preventDefault();
        dockTab(tabId, leaf.id, next);
      }}
    >
      {zone ? <div className={cn("pointer-events-none absolute z-10 bg-ink/10", zoneClass(zone))} /> : null}
      <div className="flex h-8 shrink-0 items-center gap-1 overflow-x-auto border-b border-line px-1">
        {open.length === 0 ? <span className="px-2 text-[12px] text-faint">把标签拖到这里。靠边缘松开会拆成新栏，中间松开则并入。</span> : null}
        {open.map((tab) => (
          <button
            key={tab.id}
            type="button"
            draggable
            className={cn(
              "flex h-6 max-w-36 shrink-0 items-center gap-1 rounded-md px-2 text-[12px]",
              tab.id === activeId ? "bg-active text-ink" : "text-faint hover:bg-hover hover:text-ink",
            )}
            onClick={() => focusDocked(leaf.id, tab.id)}
            onDragStart={(event) => writeTabDrag(event.dataTransfer, tab.id)}
          >
            <span className="truncate">{tab.title}</span>
            <span
              className="text-faint hover:text-ink"
              onClick={(event) => {
                event.stopPropagation();
                close(tab.id);
              }}
            >
              ×
            </span>
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1">
        {open.map((tab) => (
          <div key={tab.id} className={tab.id === activeId ? "flex h-full min-h-0 flex-col" : "hidden"}>
            <DockedTab tab={tab} active={tab.id === activeId} root={root} revision={revision} />
          </div>
        ))}
      </div>
    </div>
  );
}

function zoneClass(zone: DropZone): string {
  if (zone === "left") return "inset-y-0 left-0 w-1/4";
  if (zone === "right") return "inset-y-0 right-0 w-1/4";
  if (zone === "up") return "inset-x-0 top-0 h-1/4";
  if (zone === "down") return "inset-x-0 bottom-0 h-1/4";
  return "inset-[18%]";
}

function SplitView({ node, tabs, root, revision }: { node: SplitNode; tabs: PanelTab[]; root: string; revision: number }) {
  const resizeDock = usePanelStore((state) => state.resizeDock);
  if (node.kind === "leaf") return <LeafPane leaf={node} tabs={tabs} root={root} revision={revision} />;
  const row = node.dir === "row";
  return (
    <div className={cn("flex min-h-0 min-w-0 flex-1", row ? "flex-row" : "flex-col")}>
      <div className="flex min-h-0 min-w-0" style={row ? { width: `${node.ratio * 100}%` } : { height: `${node.ratio * 100}%` }}>
        <SplitView node={node.children[0]} tabs={tabs} root={root} revision={revision} />
      </div>
      <div
        role="separator"
        aria-orientation={row ? "vertical" : "horizontal"}
        aria-label="调整分栏"
        className={cn("shrink-0 bg-line", row ? "w-px cursor-col-resize" : "h-px cursor-row-resize")}
        onPointerDown={(event) => {
          const parent = event.currentTarget.parentElement;
          const box = parent?.getBoundingClientRect();
          if (!box) return;
          const start = row ? event.clientX : event.clientY;
          const span = row ? box.width : box.height;
          const origin = node.ratio;
          // 分栏里可能停着浏览器标签页，指针划进去就不能再由它接管事件。
          startDragSession(event, {
            cursor: row ? "col-resize" : "row-resize",
            onMove: (ev) => {
              const delta = (row ? ev.clientX : ev.clientY) - start;
              resizeDock(node.id, origin + delta / Math.max(1, span));
            },
          });
        }}
      />
      <div className="flex min-h-0 min-w-0 flex-1">
        <SplitView node={node.children[1]} tabs={tabs} root={root} revision={revision} />
      </div>
    </div>
  );
}

export function BottomWorkbench() {
  const bottom = usePanelStore((state) => state.bottom);
  const tabs = usePanelStore((state) => state.tabs);
  const revision = usePanelStore((state) => state.revision);
  const root = useChatStore((state) => state.workspace);
  const docked = collectTabIds(bottom);
  const visible = tabs.filter((tab) => docked.includes(tab.id));
  return (
    <div className="flex h-full min-h-0 flex-col bg-bg">
      <SplitView node={bottom} tabs={visible} root={root} revision={revision} />
    </div>
  );
}
