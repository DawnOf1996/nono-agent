import { Compass, FileDiff, FileText, Folder, FolderTree, GitCompare, Globe, ListTodo, MessagesSquare, PanelRightClose, Plus, TerminalSquare, X } from "lucide-react";
import { lazy, Suspense, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";
import type { FsEntry } from "@nono/protocol";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { deleteAppSession, fetchSideSessions, fetchTerminalShells, openSideSession, startTerminal, stopTerminal } from "@/api/client";
import { BrowserPane } from "@/features/files/BrowserPane";
import { ChangesPanel } from "@/features/files/changes/ChangesPanel";
import { FilePreview } from "@/features/files/FilePreview";
import { FileTree } from "@/features/files/FileTree";
import { CHANGES_TAB, START_TAB, TASKS_TAB, TREE_TAB, usePanelStore, type PanelPin, type PanelTab } from "@/features/files/panel-store";
import { TasksPanel } from "@/features/files/tasks/TasksPanel";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { writeTabDrag } from "@/features/files/BottomWorkbench";
import { SideChatPane } from "@/features/files/SideChatPane";
import { collectTabIds } from "@/features/files/split";
import { getSidebarPrefs, subscribeSidebarPrefs } from "@/features/settings/sidebar-prefs";
import { TerminalPane } from "@/features/files/TerminalPane";
import { normalizeWorkspace, workspaceLabel } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const DiffTab = lazy(() => import("@/features/files/diff/DiffTab").then((module) => ({ default: module.DiffTab })));

export function WorkspacePanel({ onCollapse }: { onCollapse: () => void }) {
  const workspace = useChatStore((state) => state.workspace);
  const root = workspace.trim();
  const tabs = usePanelStore((state) => state.tabs);
  const pins = usePanelStore((state) => state.pins);
  const bottom = usePanelStore((state) => state.bottom);
  const prefs = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs);
  const docked = new Set(collectTabIds(bottom));
  const visibleTabs = tabs.filter((tab) => !docked.has(tab.id));
  const active = usePanelStore((state) => state.active);
  const revision = usePanelStore((state) => state.revision);
  const dirty = usePanelStore((state) => state.dirty);
  const gitCount = usePanelStore((state) => state.gitCount);
  const taskCount = usePanelStore((state) => state.taskCount);
  const sessionId = useChatStore((state) => state.sessionId);
  const sessions = useChatStore((state) => state.sessions);
  const streaming = useChatStore((state) => state.streaming);
  const messages = useChatStore((state) => state.messages);
  const setRoot = usePanelStore((state) => state.setRoot);
  const activate = usePanelStore((state) => state.activate);
  const showHome = usePanelStore((state) => state.showHome);
  const openPin = usePanelStore((state) => state.openPin);
  const closePin = usePanelStore((state) => state.closePin);
  const openFile = usePanelStore((state) => state.openFile);
  const close = usePanelStore((state) => state.close);
  const sideFile = usePanelStore((state) => state.sideFile);
  const embeddedFile = usePanelStore((state) => state.embeddedFile);
  const setEmbeddedFile = usePanelStore((state) => state.setEmbeddedFile);
  const openBeside = usePanelStore((state) => state.openBeside);
  const clearSide = usePanelStore((state) => state.clearSide);
  const openBrowser = usePanelStore((state) => state.openBrowser);
  const addTerminal = usePanelStore((state) => state.addTerminal);
  const patchTerminal = usePanelStore((state) => state.patchTerminal);
  const restoreTerminals = usePanelStore((state) => state.restoreTerminals);
  const openSideChat = usePanelStore((state) => state.openSideChat);
  const retainSideChats = usePanelStore((state) => state.retainSideChats);
  const parentSession = sessions.find((item) => item.engine_session_id === sessionId) ?? null;
  const [pendingClose, setPendingClose] = useState<PanelTab | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [treeWidth, setTreeWidth] = useState(240);
  const mergedExplorer = prefs.explorerMode === "merged";

  useEffect(() => {
    setRoot(root);
    if (root) restoreTerminals(root);
  }, [root, restoreTerminals, setRoot]);

  useEffect(() => {
    const parentId = parentSession?.id ?? "";
    retainSideChats(parentId);
    if (!parentId) return;
    let live = true;
    void fetchSideSessions(parentId).then((rows) => {
      if (!live) return;
      for (const row of rows) {
        openSideChat(
          {
            productId: row.id,
            engineId: row.engine_session_id,
            parentId,
            title: row.title || "侧边对话",
          },
          false,
        );
      }
    });
    return () => {
      live = false;
    };
  }, [openSideChat, parentSession?.id, retainSideChats]);

  const activeTab = tabs.find((tab) => tab.id === active) ?? null;
  const activeFile = activeTab?.kind === "file" ? activeTab : null;
  const otherTabsOpen =
    pins.files || pins.changes || pins.tasks || visibleTabs.length > 0;

  function leaveHome() {
    if (pins.files) activate(TREE_TAB);
    else if (pins.changes) activate(CHANGES_TAB);
    else if (pins.tasks) activate(TASKS_TAB);
    else if (visibleTabs[0]) activate(visibleTabs[0].id);
  }

  async function openPortalItem(id: PanelPin | "sidechat" | "terminal" | "browser") {
    if (id === "files" || id === "changes" || id === "tasks") {
      openPin(id);
      return;
    }
    if (id === "sidechat") {
      await openNewSideChat();
      return;
    }
    if (id === "terminal") {
      await openNewTerminal();
      return;
    }
    openBrowser(prefs.browserHome.trim() || undefined);
  }

  function requestClose(tab: PanelTab) {
    if (dirty[tab.id]) {
      setPendingClose(tab);
      return;
    }
    if (tab.kind === "terminal") void stopTerminal(tab.serverId);
    if (tab.kind === "sidechat") void deleteAppSession(tab.productId);
    close(tab.id);
  }

  async function openNewSideChat() {
    if (!parentSession) {
      setNotice("先开始一段对话，侧边对话才能继承它的上下文");
      return;
    }
    setNotice(null);
    const draft = [...messages].reverse().find((item) => item.role === "assistant" && item.streaming);
    const interrupt = streaming ? draft?.text.trim() : "";
    try {
      const created = await openSideSession(parentSession.id, interrupt || undefined);
      openSideChat({
        productId: created.id,
        engineId: created.engine_session_id,
        parentId: parentSession.id,
        title: created.title || "侧边对话",
      });
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "无法打开侧边对话");
    }
  }

  async function openNewTerminal() {
    if (!root) return;
    setNotice(null);
    try {
      const shells = await fetchTerminalShells();
      const chosen = shells[0];
      if (!chosen) throw new Error("没有可用的 shell");
      const created = await startTerminal({ root, cwd: root, shell: chosen.id, cols: 80, rows: 24 });
      addTerminal({
        id: crypto.randomUUID(),
        serverId: created.id,
        title: chosen.label,
        shell: chosen.id,
        cwd: root,
      });
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "无法打开终端");
    }
  }

  return (
    <aside className="flex h-full min-h-0 w-full min-w-0 flex-col border-l border-line bg-bg">
      <header className="flex h-12 shrink-0 items-center border-b border-line pl-2 pr-1">
        <div
          role="tablist"
          aria-label="工作区"
          className="flex h-full min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]"
          onWheel={(event) => {
            if (event.deltaY !== 0) event.currentTarget.scrollLeft += event.deltaY;
          }}
        >
          {pins.files && prefs.files ? (
            <TabButton
              selected={active === TREE_TAB}
              icon={<FolderTree className="size-3.5" />}
              label="文件"
              title={root ? workspaceLabel(root) : "文件"}
              onSelect={() => activate(TREE_TAB)}
              onClose={() => closePin("files")}
            />
          ) : null}
          {pins.changes && prefs.changes ? (
            <TabButton
              selected={active === CHANGES_TAB}
              icon={<GitCompare className="size-3.5" />}
              label="变更"
              title="Git 状态与每轮对话的文件改动"
              badge={gitCount > 0 ? gitCount : undefined}
              onSelect={() => activate(CHANGES_TAB)}
              onClose={() => closePin("changes")}
            />
          ) : null}
          {pins.tasks && prefs.tasks ? (
            <TabButton
              selected={active === TASKS_TAB}
              icon={<ListTodo className="size-3.5" />}
              label="任务"
              title="子代理和正在执行的命令"
              badge={taskCount > 0 ? taskCount : undefined}
              onSelect={() => activate(TASKS_TAB)}
              onClose={() => closePin("tasks")}
            />
          ) : null}
          {visibleTabs.map((tab) => (
            <TabButton
              key={tab.id}
              selected={tab.id === active}
              icon={
                tab.kind === "file" ? (
                  <FileText className="size-3.5" />
                ) : tab.kind === "browser" ? (
                  <Globe className="size-3.5" />
                ) : tab.kind === "terminal" ? (
                  <TerminalSquare className="size-3.5" />
                ) : tab.kind === "sidechat" ? (
                  <MessagesSquare className="size-3.5" />
                ) : (
                  <FileDiff className="size-3.5" />
                )
              }
              label={tab.title}
              title={
                tab.kind === "file"
                  ? tab.path
                  : tab.kind === "browser"
                    ? tab.url || "浏览器"
                    : tab.kind === "terminal"
                      ? tab.cwd
                      : tab.kind === "sidechat"
                        ? "侧边对话，不写回主会话"
                        : `对比：${tab.title}`
              }
              dirty={Boolean(dirty[tab.id])}
              dragId={tab.id}
              onSelect={() => activate(tab.id)}
              onRename={
                tab.kind === "terminal"
                  ? (title) => patchTerminal(tab.id, { title })
                  : undefined
              }
              onClose={() => requestClose(tab)}
            />
          ))}
          {active === START_TAB ? (
            <TabButton
              selected
              icon={<Compass className="size-3.5" />}
              label="开始"
              title="打开一个面板"
              onSelect={showHome}
              onClose={otherTabsOpen ? leaveHome : undefined}
            />
          ) : null}
        </div>
        {active === START_TAB ? null : (
          <button
            type="button"
            className="ml-1 flex size-7 shrink-0 items-center justify-center rounded-md border border-line text-muted hover:bg-hover hover:text-ink"
            title="新建标签"
            aria-label="新建标签"
            onClick={showHome}
          >
            <Plus className="size-3.5" />
          </button>
        )}
        <button
          type="button"
          className="flex size-8 shrink-0 items-center justify-center rounded-lg text-faint hover:bg-hover hover:text-ink"
          title="收起文件栏"
          aria-label="收起文件栏"
          onClick={onCollapse}
        >
          <PanelRightClose className="size-4" />
        </button>
      </header>
      {notice ? <p className="border-b border-line px-3 py-1.5 text-[12px] text-danger">{notice}</p> : null}
      <div className="flex min-h-0 flex-1 flex-col">
        {active === START_TAB ? (
          <WorkspaceHome
            prefs={prefs}
            disabled={{ sidechat: !parentSession, terminal: !root }}
            onOpen={(id) => void openPortalItem(id)}
          />
        ) : null}
        {root && pins.files ? (
          <>
            <div className={active === TREE_TAB ? "flex min-h-0 flex-1" : "hidden"}>
              <div
                className={mergedExplorer ? "flex min-h-0 shrink-0 flex-col" : "flex min-h-0 min-w-0 flex-1 flex-col"}
                style={mergedExplorer ? { width: treeWidth } : undefined}
              >
                <FileTree
                  key={normalizeWorkspace(root)}
                  root={root}
                  revision={revision}
                  activePath={mergedExplorer ? embeddedFile?.path ?? null : sideFile?.path ?? activeFile?.path ?? null}
                  onOpenFile={(entry: FsEntry) =>
                    mergedExplorer ? setEmbeddedFile(entry.path, entry.name) : openFile(entry.path, entry.name)
                  }
                  onOpenBeside={(entry: FsEntry) => openBeside(entry.path, entry.name)}
                />
              </div>
              {mergedExplorer ? (
                <>
                  <div
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="调整文件树宽度"
                    className="w-px shrink-0 cursor-col-resize bg-line hover:bg-ink/30"
                    onPointerDown={(event) => {
                      event.preventDefault();
                      const startX = event.clientX;
                      const start = treeWidth;
                      function move(ev: PointerEvent) {
                        setTreeWidth(Math.min(480, Math.max(160, start + ev.clientX - startX)));
                      }
                      function end() {
                        window.removeEventListener("pointermove", move);
                        window.removeEventListener("pointerup", end);
                      }
                      window.addEventListener("pointermove", move);
                      window.addEventListener("pointerup", end);
                    }}
                  />
                  <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                    {embeddedFile ? (
                      <FilePreview root={root} path={embeddedFile.path} revision={revision} tabId={`embedded:${embeddedFile.path}`} />
                    ) : (
                      <p className="px-4 py-6 text-[13px] leading-6 text-muted">从左侧选择一个文件，预览会留在这一页里。</p>
                    )}
                  </div>
                </>
              ) : null}
              {!mergedExplorer && sideFile ? (
                <div className="flex min-h-0 min-w-0 flex-1 flex-col border-l border-line">
                  <div className="flex h-8 shrink-0 items-center justify-end border-b border-line px-1">
                    <button
                      type="button"
                      className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
                      title="关闭侧边预览"
                      aria-label="关闭侧边预览"
                      onClick={clearSide}
                    >
                      <X className="size-3.5" />
                    </button>
                  </div>
                  <FilePreview root={root} path={sideFile.path} revision={revision} tabId={`side:${sideFile.path}`} />
                </div>
              ) : null}
            </div>
          </>
        ) : null}
        {root && pins.changes ? (
          <div className={active === CHANGES_TAB ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
            <ChangesPanel root={root} visible={active === CHANGES_TAB} />
          </div>
        ) : null}
        <div className={active === TASKS_TAB ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
          <TasksPanel sessionId={sessionId} />
        </div>
        {root ? (
          <>
            {tabs.map((tab) =>
              docked.has(tab.id) || tab.kind === "browser" || tab.kind === "terminal" || tab.kind === "sidechat" ? null : (
                <div key={tab.id} className={tab.id === active ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
                  {tab.kind === "file" ? (
                    <FilePreview root={root} path={tab.path} revision={revision} tabId={tab.id} />
                  ) : (
                    <Suspense fallback={<PreviewLoading />}>
                      <DiffTab tabId={tab.id} target={tab.target} visible={tab.id === active} />
                    </Suspense>
                  )}
                </div>
              ),
            )}
          </>
        ) : active === START_TAB || activeTab?.kind === "browser" ? null : (
          <p className="px-4 py-6 text-[13px] leading-6 text-muted">先打开一个工作区，这里会列出其中的文件和改动。</p>
        )}
        {tabs.map((tab) =>
          docked.has(tab.id) ? null : tab.kind === "browser" ? (
            <div key={tab.id} className={tab.id === active ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
              <BrowserPane tabId={tab.id} url={tab.url} />
            </div>
          ) : tab.kind === "sidechat" ? (
            <div key={tab.id} className={tab.id === active ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
              <SideChatPane tabId={tab.id} productId={tab.productId} engineId={tab.engineId} />
            </div>
          ) : tab.kind === "terminal" ? (
            <div key={tab.id} className={tab.id === active ? "flex min-h-0 flex-1 flex-col" : "hidden"}>
              <TerminalPane
                tabId={tab.id}
                serverId={tab.serverId}
                shell={tab.shell}
                cwd={tab.cwd}
                root={root}
                active={tab.id === active}
              />
            </div>
          ) : null,
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

function WorkspaceHome({
  prefs,
  disabled,
  onOpen,
}: {
  prefs: { files: boolean; changes: boolean; tasks: boolean; sidechat: boolean; terminal: boolean; browser: boolean };
  disabled: { sidechat: boolean; terminal: boolean };
  onOpen: (id: PanelPin | "sidechat" | "terminal" | "browser") => void;
}) {
  const items: Array<{
    id: PanelPin | "sidechat" | "terminal" | "browser";
    label: string;
    icon: ReactNode;
    hidden?: boolean;
    disabled?: boolean;
  }> = [
    { id: "files", label: "文件", icon: <Folder className="size-4" />, hidden: !prefs.files },
    { id: "changes", label: "文件变动", icon: <GitCompare className="size-4" />, hidden: !prefs.changes },
    { id: "tasks", label: "任务管理", icon: <ListTodo className="size-4" />, hidden: !prefs.tasks },
    {
      id: "sidechat",
      label: "侧边对话",
      icon: <MessagesSquare className="size-4" />,
      hidden: !prefs.sidechat,
      disabled: disabled.sidechat,
    },
    { id: "terminal", label: "终端", icon: <TerminalSquare className="size-4" />, hidden: !prefs.terminal, disabled: disabled.terminal },
    { id: "browser", label: "浏览器", icon: <Globe className="size-4" />, hidden: !prefs.browser },
  ];
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center px-6 pb-16">
      <Compass className="mb-8 size-10 text-faint" strokeWidth={1.25} />
      <div className="flex w-full max-w-[280px] flex-col gap-3">
        {items.filter((item) => !item.hidden).map((item) => (
          <button
            key={item.id}
            type="button"
            disabled={item.disabled}
            className="flex h-11 items-center gap-3 rounded-2xl border border-line px-4 text-[14px] text-ink hover:bg-hover disabled:opacity-40"
            onClick={() => onOpen(item.id)}
          >
            <span className="text-muted">{item.icon}</span>
            {item.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function TabButton({
  selected,
  icon,
  label,
  title,
  badge,
  dirty,
  dragId,
  onSelect,
  onRename,
  onClose,
}: {
  selected: boolean;
  icon?: ReactNode;
  label: string;
  title: string;
  badge?: number;
  dirty?: boolean;
  dragId?: string;
  onSelect: () => void;
  onRename?: (title: string) => void;
  onClose?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(label);
  return (
    <div
      role="tab"
      aria-selected={selected}
      title={title}
      draggable={Boolean(dragId)}
      onDragStart={(event) => {
        if (dragId) writeTabDrag(event.dataTransfer, dragId);
      }}
      className={cn(
        "group flex h-7 max-w-[160px] shrink-0 items-center gap-1 rounded-lg border px-2 text-[13px]",
        selected ? "border-line bg-card text-ink" : "border-transparent text-muted hover:bg-hover hover:text-ink",
      )}
      onAuxClick={(event) => {
        if (event.button === 1 && onClose) {
          event.preventDefault();
          onClose();
        }
      }}
    >
      <button
        type="button"
        className="flex min-w-0 items-center gap-1.5"
        onClick={onSelect}
        onDoubleClick={() => {
          if (!onRename) return;
          setDraft(label);
          setEditing(true);
        }}
      >
        {icon ? <span className="shrink-0">{icon}</span> : null}
        {editing ? (
          <input
            autoFocus
            value={draft}
            className="w-24 bg-transparent text-[13px] outline-none"
            onClick={(event) => event.stopPropagation()}
            onChange={(event) => setDraft(event.target.value)}
            onBlur={() => {
              setEditing(false);
              const next = draft.trim();
              if (next && onRename) onRename(next);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") event.currentTarget.blur();
              if (event.key === "Escape") {
                setDraft(label);
                setEditing(false);
              }
            }}
          />
        ) : (
          <span className="min-w-0 truncate">{label}</span>
        )}
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
