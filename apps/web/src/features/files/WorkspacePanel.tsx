import { FileDiff, FileText, FolderTree, GitCompare, PanelRightClose, RefreshCw, X } from "lucide-react";
import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import type { FsEntry } from "@nono/protocol";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { ChangesPanel } from "@/features/files/changes/ChangesPanel";
import { FilePreview } from "@/features/files/FilePreview";
import { FileTree } from "@/features/files/FileTree";
import { CHANGES_TAB, TREE_TAB, usePanelStore, type PanelTab } from "@/features/files/panel-store";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { normalizeWorkspace, workspaceLabel } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const DiffTab = lazy(() => import("@/features/files/diff/DiffTab").then((module) => ({ default: module.DiffTab })));

export function WorkspacePanel({ onCollapse }: { onCollapse: () => void }) {
  const workspace = useChatStore((state) => state.workspace);
  const root = workspace.trim();
  const tabs = usePanelStore((state) => state.tabs);
  const active = usePanelStore((state) => state.active);
  const revision = usePanelStore((state) => state.revision);
  const dirty = usePanelStore((state) => state.dirty);
  const gitCount = usePanelStore((state) => state.gitCount);
  const setRoot = usePanelStore((state) => state.setRoot);
  const activate = usePanelStore((state) => state.activate);
  const openFile = usePanelStore((state) => state.openFile);
  const close = usePanelStore((state) => state.close);
  const bumpRevision = usePanelStore((state) => state.bumpRevision);
  const [pendingClose, setPendingClose] = useState<PanelTab | null>(null);

  useEffect(() => {
    setRoot(root);
  }, [root, setRoot]);

  const activeTab = tabs.find((tab) => tab.id === active) ?? null;
  const activeFile = activeTab?.kind === "file" ? activeTab : null;
  const label = root ? workspaceLabel(root) : "文件";

  function requestClose(tab: PanelTab) {
    if (dirty[tab.id]) {
      setPendingClose(tab);
      return;
    }
    close(tab.id);
  }

  return (
    <aside className="flex h-full min-h-0 w-full min-w-0 flex-col border-l border-line bg-bg">
      <header className="flex h-12 shrink-0 items-center border-b border-line pl-2 pr-1">
        <div
          role="tablist"
          aria-label="工作区文件"
          className="flex h-full min-w-0 flex-1 items-center overflow-x-auto [scrollbar-width:none]"
          onWheel={(event) => {
            if (event.deltaY !== 0) event.currentTarget.scrollLeft += event.deltaY;
          }}
        >
          <TabButton
            selected={active === TREE_TAB}
            icon={<FolderTree className="size-3.5" />}
            label="文件"
            title={label}
            onSelect={() => activate(TREE_TAB)}
          />
          <TabButton
            selected={active === CHANGES_TAB}
            icon={<GitCompare className="size-3.5" />}
            label="变更"
            title="Git 状态与每轮对话的文件改动"
            badge={gitCount > 0 ? gitCount : undefined}
            onSelect={() => activate(CHANGES_TAB)}
          />
          {tabs.length ? <span className="mx-1 h-4 w-px shrink-0 bg-line" /> : null}
          {tabs.map((tab) => (
            <TabButton
              key={tab.id}
              selected={tab.id === active}
              icon={tab.kind === "file" ? <FileText className="size-3.5" /> : <FileDiff className="size-3.5" />}
              label={tab.title}
              title={tab.kind === "file" ? tab.path : `对比：${tab.title}`}
              dirty={Boolean(dirty[tab.id])}
              onSelect={() => activate(tab.id)}
              onClose={() => requestClose(tab)}
            />
          ))}
        </div>
        <div className="flex shrink-0 items-center">
          <button
            type="button"
            className="flex size-8 items-center justify-center rounded-lg text-faint hover:bg-hover hover:text-ink disabled:opacity-40"
            title="刷新"
            aria-label="刷新文件"
            disabled={!root}
            onClick={bumpRevision}
          >
            <RefreshCw className="size-4" />
          </button>
          <button
            type="button"
            className="flex size-8 items-center justify-center rounded-lg text-faint hover:bg-hover hover:text-ink"
            title="收起文件栏"
            aria-label="收起文件栏"
            onClick={onCollapse}
          >
            <PanelRightClose className="size-4" />
          </button>
        </div>
      </header>
      <div className="flex min-h-0 flex-1 flex-col">
        {root ? (
          <>
            <div className={active === TREE_TAB ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
              <FileTree
                key={normalizeWorkspace(root)}
                root={root}
                revision={revision}
                activePath={activeFile?.path ?? null}
                onOpenFile={(entry: FsEntry) => openFile(entry.path, entry.name)}
              />
            </div>
            <div className={active === CHANGES_TAB ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
              <ChangesPanel root={root} visible={active === CHANGES_TAB} />
            </div>
            {tabs.map((tab) => (
              <div key={tab.id} className={tab.id === active ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
                {tab.kind === "file" ? (
                  <FilePreview root={root} path={tab.path} revision={revision} tabId={tab.id} />
                ) : (
                  <Suspense fallback={<PreviewLoading />}>
                    <DiffTab tabId={tab.id} target={tab.target} visible={tab.id === active} />
                  </Suspense>
                )}
              </div>
            ))}
          </>
        ) : (
          <p className="px-4 py-6 text-[13px] leading-6 text-muted">先打开一个工作区，这里会列出其中的文件和改动。</p>
        )}
      </div>
      <ConfirmDialog
        open={pendingClose !== null}
        title="放弃未保存的修改？"
        description={`${pendingClose?.title ?? ""} 有未保存的修改，关闭后这些修改会丢失。`}
        confirmLabel="放弃并关闭"
        danger
        onCancel={() => setPendingClose(null)}
        onConfirm={() => {
          if (pendingClose) close(pendingClose.id);
          setPendingClose(null);
        }}
      />
    </aside>
  );
}

function TabButton({
  selected,
  icon,
  label,
  title,
  badge,
  dirty,
  onSelect,
  onClose,
}: {
  selected: boolean;
  icon?: ReactNode;
  label: string;
  title: string;
  badge?: number;
  dirty?: boolean;
  onSelect: () => void;
  onClose?: () => void;
}) {
  return (
    <div
      role="tab"
      aria-selected={selected}
      title={title}
      className={cn(
        "group mb-[-1px] flex h-full max-w-[160px] shrink-0 items-center gap-1 border-b-2 px-2 text-[13px]",
        selected ? "border-ink text-ink" : "border-transparent text-faint hover:text-ink",
      )}
      onAuxClick={(event) => {
        if (event.button === 1 && onClose) {
          event.preventDefault();
          onClose();
        }
      }}
    >
      <button type="button" className="flex min-w-0 items-center gap-1.5" onClick={onSelect}>
        {icon ? <span className="shrink-0">{icon}</span> : null}
        <span className="min-w-0 truncate">{label}</span>
        {badge !== undefined ? (
          <span className="shrink-0 rounded-full bg-card px-1.5 text-[10px] leading-4 tabular-nums text-muted">
            {badge > 99 ? "99+" : badge}
          </span>
        ) : null}
      </button>
      {onClose ? (
        <button
          type="button"
          className="relative flex size-4 shrink-0 items-center justify-center rounded text-faint hover:bg-hover hover:text-ink"
          title={`关闭 ${label}`}
          aria-label={`关闭 ${label}`}
          onClick={onClose}
        >
          {dirty ? (
            <>
              <span className="size-2 rounded-full bg-ink group-hover:hidden" />
              <X className="hidden size-3 group-hover:block" />
            </>
          ) : (
            <X className="size-3" />
          )}
        </button>
      ) : null}
    </div>
  );
}
