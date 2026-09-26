import { ChevronRight, File, FileText, Folder, FolderOpen, Image, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import type { FsEntry } from "@nono/protocol";

import { fetchWorkspaceEntries } from "@/api/client";
import { sameWorkspace, workspaceLabel } from "@/lib/workspace";

function fileIcon(name: string) {
  const lower = name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(lower)) return Image;
  if (/\.(md|markdown|txt|json|ya?ml|toml|xml|csv|py|ts|tsx|js|jsx|css|html|rs|go|vue|svelte)$/.test(lower)) {
    return FileText;
  }
  return File;
}

function fsError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : "无法读取文件夹";
  if (message === "Not Found") return "读不到文件接口，请重启本机服务后再试";
  return message;
}

function DirectoryList({
  root,
  path,
  depth,
  revision,
  activePath,
  onOpenFile,
}: {
  root: string;
  path: string;
  depth: number;
  revision: number;
  activePath: string | null;
  onOpenFile: (entry: FsEntry) => void;
}) {
  const [entries, setEntries] = useState<FsEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  useEffect(() => {
    let live = true;
    setError(null);
    void fetchWorkspaceEntries(root, path)
      .then((listing) => {
        if (live) setEntries(listing.entries);
      })
      .catch((cause: unknown) => {
        if (live) setError(fsError(cause));
      });
    return () => {
      live = false;
    };
  }, [root, path, revision]);

  if (error) {
    return (
      <p className="px-3 py-2 text-xs leading-5 text-danger" style={{ paddingLeft: 12 + depth * 14 }}>
        {error}
      </p>
    );
  }
  if (!entries) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-faint" style={{ paddingLeft: 12 + depth * 14 }}>
        <Loader2 className="size-3.5 animate-spin" />
        正在读取
      </div>
    );
  }
  if (entries.length === 0) {
    return (
      <p className="px-3 py-2 text-xs text-faint" style={{ paddingLeft: 12 + depth * 14 }}>
        {depth === 0 ? "这个工作区是空的" : "空文件夹"}
      </p>
    );
  }

  return (
    <ul>
      {entries.map((entry) => {
        const Icon = entry.is_dir ? Folder : fileIcon(entry.name);
        const open = expanded.has(entry.path);
        const selected = !entry.is_dir && activePath !== null && sameWorkspace(activePath, entry.path);
        return (
          <li key={entry.path}>
            <button
              type="button"
              className={`flex w-full min-w-0 items-center gap-1 rounded-lg py-1.5 pr-2 text-left text-[13px] ${
                selected ? "bg-active text-ink" : "text-ink/80 hover:bg-hover"
              }`}
              style={{ paddingLeft: 8 + depth * 14 }}
              title={entry.path}
              aria-expanded={entry.is_dir ? open : undefined}
              onClick={() => {
                if (entry.is_dir) {
                  setExpanded((current) => {
                    const next = new Set(current);
                    if (next.has(entry.path)) next.delete(entry.path);
                    else next.add(entry.path);
                    return next;
                  });
                  return;
                }
                onOpenFile(entry);
              }}
            >
              {entry.is_dir ? (
                <ChevronRight className={`size-3 shrink-0 text-faint transition-transform ${open ? "rotate-90" : ""}`} />
              ) : (
                <span className="size-3 shrink-0" />
              )}
              {entry.is_dir && open ? (
                <FolderOpen className="size-3.5 shrink-0 text-muted" />
              ) : (
                <Icon className="size-3.5 shrink-0 text-muted" />
              )}
              <span className="truncate">{entry.name}</span>
            </button>
            {entry.is_dir && open ? (
              <DirectoryList
                root={root}
                path={entry.path}
                depth={depth + 1}
                revision={revision}
                activePath={activePath}
                onOpenFile={onOpenFile}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function FileTree({
  root,
  revision,
  activePath,
  onOpenFile,
}: {
  root: string;
  revision: number;
  activePath: string | null;
  onOpenFile: (entry: FsEntry) => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="truncate px-3 pb-1 pt-3 text-[11px] font-medium text-faint" title={root}>
        {workspaceLabel(root)}
      </div>
      <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-3">
        <DirectoryList
          root={root}
          path={root}
          depth={0}
          revision={revision}
          activePath={activePath}
          onOpenFile={onOpenFile}
        />
      </div>
    </div>
  );
}
