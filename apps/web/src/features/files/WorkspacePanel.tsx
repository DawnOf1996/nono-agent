import { PanelRightClose, RefreshCw, X } from "lucide-react";
import { useState } from "react";
import type { FsEntry } from "@nono/protocol";

import { FilePreview } from "@/features/files/FilePreview";
import { FileTree } from "@/features/files/FileTree";
import { normalizeWorkspace, sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { useChatStore } from "@/store/chat";

const TREE_TAB = "tree";

type OpenFile = {
  path: string;
  name: string;
};

export function WorkspacePanel({ onCollapse }: { onCollapse: () => void }) {
  const workspace = useChatStore((state) => state.workspace);
  const root = workspace.trim();
  const [seenRoot, setSeenRoot] = useState(root);
  const [files, setFiles] = useState<OpenFile[]>([]);
  const [active, setActive] = useState(TREE_TAB);
  const [revision, setRevision] = useState(0);

  if (root !== seenRoot) {
    setSeenRoot(root);
    setFiles([]);
    setActive(TREE_TAB);
  }

  const activeFile = files.find((item) => sameWorkspace(item.path, active)) ?? null;
  const label = root ? workspaceLabel(root) : "文件";

  function openFile(entry: FsEntry) {
    setFiles((current) => {
      if (current.some((item) => sameWorkspace(item.path, entry.path))) return current;
      return [...current, { path: entry.path, name: entry.name }];
    });
    setActive(entry.path);
  }

  function closeFile(path: string) {
    const next = files.filter((item) => !sameWorkspace(item.path, path));
    setFiles(next);
    if (!sameWorkspace(active, path)) return;
    const index = files.findIndex((item) => sameWorkspace(item.path, path));
    const neighbor = next[Math.min(index, next.length - 1)];
    setActive(neighbor?.path ?? TREE_TAB);
  }

  return (
    <aside className="flex h-full min-h-0 w-full min-w-0 flex-col border-l border-line bg-bg">
      <header className="flex h-12 shrink-0 items-center border-b border-line pl-2 pr-1">
        <div role="tablist" aria-label="工作区文件" className="flex h-full min-w-0 flex-1 items-center overflow-x-auto">
          <TabButton selected={activeFile === null} label="文件" title={label} onSelect={() => setActive(TREE_TAB)} />
          {files.map((file) => (
            <TabButton
              key={file.path}
              selected={activeFile !== null && sameWorkspace(activeFile.path, file.path)}
              label={file.name}
              title={file.path}
              onSelect={() => setActive(file.path)}
              onClose={() => closeFile(file.path)}
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
            onClick={() => setRevision((value) => value + 1)}
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
          <div className={activeFile ? "hidden" : "flex min-h-0 flex-1 flex-col"}>
            <FileTree
              key={normalizeWorkspace(root)}
              root={root}
              revision={revision}
              activePath={activeFile?.path ?? null}
              onOpenFile={openFile}
            />
          </div>
        ) : (
          <p className="px-4 py-6 text-[13px] leading-6 text-muted">先打开一个工作区，这里会列出其中的文件。</p>
        )}
        {root && activeFile ? (
          <FilePreview key={normalizeWorkspace(activeFile.path)} root={root} path={activeFile.path} revision={revision} />
        ) : null}
      </div>
    </aside>
  );
}

function TabButton({
  selected,
  label,
  title,
  onSelect,
  onClose,
}: {
  selected: boolean;
  label: string;
  title: string;
  onSelect: () => void;
  onClose?: () => void;
}) {
  return (
    <div
      role="tab"
      aria-selected={selected}
      title={title}
      className={`mb-[-1px] flex max-w-[148px] shrink-0 items-center gap-1 border-b-2 px-2.5 py-2 text-[13px] ${
        selected ? "border-ink text-ink" : "border-transparent text-faint hover:text-ink"
      }`}
    >
      <button type="button" className="min-w-0 truncate" onClick={onSelect}>
        {label}
      </button>
      {onClose ? (
        <button
          type="button"
          className="flex size-4 shrink-0 items-center justify-center rounded text-faint hover:bg-hover hover:text-ink"
          title={`关闭 ${label}`}
          aria-label={`关闭 ${label}`}
          onClick={onClose}
        >
          <X className="size-3" />
        </button>
      ) : null}
    </div>
  );
}
