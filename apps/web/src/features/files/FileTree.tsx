import type { FsEntry, FsSearchMatch } from "@nono/protocol";
import {
  AtSign,
  ChevronRight,
  Copy,
  File,
  FileText,
  Folder,
  FolderOpen,
  FolderUp,
  Image,
  Link2,
  Loader2,
  PanelRight,
  Search,
  Upload,
  X,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";

import { fetchWorkspaceEntries, searchWorkspaceFiles, uploadWorkspaceFiles } from "@/api/client";
import { usePanelStore } from "@/features/files/panel-store";
import { useDirectoryWatch } from "@/features/files/use-dir-watch";
import { getSidebarPrefs, subscribeSidebarPrefs } from "@/features/settings/sidebar-prefs";
import { joinWorkspace, mentionToken, normalizeWorkspace, parentPath, relativeTo, sameWorkspace, workspaceLabel } from "@/lib/workspace";
import { cn } from "@/lib/utils";

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

function pathKey(path: string): string {
  return normalizeWorkspace(path).toLowerCase();
}

type MenuState = { x: number; y: number; entry: FsEntry };

function DirectoryList({
  root,
  path,
  depth,
  revision,
  ticks,
  expanded,
  onToggle,
  activePath,
  dropPath,
  onOpenFile,
  onContextMenu,
  onDragTarget,
}: {
  root: string;
  path: string;
  depth: number;
  revision: number;
  ticks: Readonly<Record<string, number>>;
  expanded: ReadonlySet<string>;
  onToggle: (path: string) => void;
  activePath: string | null;
  dropPath: string | null;
  onOpenFile: (entry: FsEntry) => void;
  onContextMenu: (entry: FsEntry, event: ReactMouseEvent) => void;
  onDragTarget: (path: string | null) => void;
}) {
  const showHidden = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs).showHiddenFiles;
  const [entries, setEntries] = useState<FsEntry[] | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const localKey = `${revision}:${ticks[pathKey(path)] ?? 0}`;

  useEffect(() => {
    let live = true;
    setError(null);
    void fetchWorkspaceEntries(root, path)
      .then((listing) => {
        if (!live) return;
        setEntries(listing.entries);
        setTruncated(Boolean(listing.truncated));
      })
      .catch((cause: unknown) => {
        if (live) setError(fsError(cause));
      });
    return () => {
      live = false;
    };
  }, [root, path, localKey]);

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
  const shown = showHidden ? entries : entries.filter((entry) => !entry.hidden);
  if (shown.length === 0) {
    return (
      <p className="px-3 py-2 text-xs text-faint" style={{ paddingLeft: 12 + depth * 14 }}>
        {depth === 0 ? "这个工作区是空的" : "空文件夹"}
      </p>
    );
  }

  return (
    <ul>
      {shown.map((entry) => {
        const Icon = entry.is_dir ? Folder : fileIcon(entry.name);
        const open = expanded.has(entry.path) || [...expanded].some((item) => pathKey(item) === pathKey(entry.path));
        const selected = !entry.is_dir && activePath !== null && sameWorkspace(activePath, entry.path);
        const dropping = entry.is_dir && dropPath !== null && sameWorkspace(dropPath, entry.path);
        return (
          <li key={entry.path}>
            <div
              role="button"
              tabIndex={0}
              className={cn(
                "group flex w-full min-w-0 items-center gap-1 rounded-lg py-1.5 pr-1 text-left text-[13px]",
                entry.broken ? "text-danger" : entry.hidden ? "text-faint" : "text-ink/80",
                selected ? "bg-active text-ink" : "hover:bg-hover",
                dropping && "bg-active ring-1 ring-ink/20",
              )}
              style={{ paddingLeft: 8 + depth * 14 }}
              title={entry.broken ? `${entry.path}（链接失效）` : entry.path}
              aria-expanded={entry.is_dir ? open : undefined}
              onClick={() => {
                if (entry.is_dir && !entry.broken) onToggle(entry.path);
                else if (!entry.is_dir && !entry.broken) onOpenFile(entry);
              }}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                if (entry.is_dir && !entry.broken) onToggle(entry.path);
                else if (!entry.broken) onOpenFile(entry);
              }}
              onContextMenu={(event) => {
                event.preventDefault();
                onContextMenu(entry, event);
              }}
              onDragOver={(event) => {
                if (!entry.is_dir || entry.broken || ![...event.dataTransfer.types].includes("Files")) return;
                event.preventDefault();
                event.stopPropagation();
                onDragTarget(entry.path);
              }}
            >
              {entry.is_dir ? (
                <ChevronRight className={cn("size-3 shrink-0 text-faint transition-transform", open && "rotate-90")} />
              ) : (
                <span className="size-3 shrink-0" />
              )}
              {entry.is_dir && open ? (
                <FolderOpen className="size-3.5 shrink-0 text-muted" />
              ) : (
                <Icon className={cn("size-3.5 shrink-0", entry.broken ? "text-danger" : "text-muted")} />
              )}
              <span className={cn("min-w-0 flex-1 truncate", entry.broken && "line-through")}>{entry.name}</span>
              {entry.is_symlink ? <Link2 className="size-3 shrink-0 text-faint" aria-label="软链接" /> : null}
              <button
                type="button"
                className="flex size-5 shrink-0 items-center justify-center rounded text-faint opacity-0 hover:bg-bg hover:text-ink group-hover:opacity-100"
                title="引用到输入框"
                aria-label={`引用 ${entry.name}`}
                onClick={(event) => {
                  event.stopPropagation();
                  window.dispatchEvent(new CustomEvent("nono-insert-input", { detail: mentionToken(root, entry.path) }));
                }}
              >
                <AtSign className="size-3" />
              </button>
            </div>
            {entry.is_dir && open && !entry.broken ? (
              <DirectoryList
                root={root}
                path={entry.path}
                depth={depth + 1}
                revision={revision}
                ticks={ticks}
                expanded={expanded}
                onToggle={onToggle}
                activePath={activePath}
                dropPath={dropPath}
                onOpenFile={onOpenFile}
                onContextMenu={onContextMenu}
                onDragTarget={onDragTarget}
              />
            ) : null}
          </li>
        );
      })}
      {truncated ? <li className="px-3 py-1.5 text-[11px] text-faint">这一层文件太多，只列出前一部分。</li> : null}
    </ul>
  );
}

function EntryMenu({
  menu,
  root,
  onClose,
  onOpen,
  onOpenBeside,
}: {
  menu: MenuState;
  root: string;
  onClose: () => void;
  onOpen: (entry: FsEntry) => void;
  onOpenBeside: (entry: FsEntry) => void;
}) {
  const file = !menu.entry.is_dir && !menu.entry.broken;
  useEffect(() => {
    function close() {
      onClose();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("pointerdown", close);
    window.addEventListener("keydown", onKey);
    window.addEventListener("blur", close);
    return () => {
      window.removeEventListener("pointerdown", close);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("blur", close);
    };
  }, [onClose]);

  const left = Math.min(menu.x, window.innerWidth - 180);
  const top = Math.min(menu.y, window.innerHeight - 160);

  return createPortal(
    <div
      role="menu"
      className="fixed z-50 min-w-[168px] rounded-lg border border-line bg-bg py-1 text-[13px] shadow-lg"
      style={{ left, top }}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {file ? (
        <>
          <MenuItem label="在新标签打开" icon={<FileText className="size-3.5" />} onClick={() => onOpen(menu.entry)} />
          <MenuItem label="在侧边打开" icon={<PanelRight className="size-3.5" />} onClick={() => onOpenBeside(menu.entry)} />
        </>
      ) : null}
      <MenuItem
        label="复制路径"
        icon={<Copy className="size-3.5" />}
        onClick={() => void navigator.clipboard.writeText(menu.entry.path)}
      />
      <MenuItem
        label="引用到输入框"
        icon={<AtSign className="size-3.5" />}
        onClick={() =>
          window.dispatchEvent(new CustomEvent("nono-insert-input", { detail: mentionToken(root, menu.entry.path) }))
        }
      />
    </div>,
    document.body,
  );
}

function MenuItem({ label, icon, onClick }: { label: string; icon: ReactNode; onClick: () => void }) {
  return (
    <button
      type="button"
      role="menuitem"
      className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-ink hover:bg-hover"
      onClick={onClick}
    >
      <span className="text-muted">{icon}</span>
      {label}
    </button>
  );
}

export function FileTree({
  root,
  revision,
  activePath,
  onOpenFile,
  onOpenInTab,
  onOpenBeside,
}: {
  root: string;
  revision: number;
  activePath: string | null;
  onOpenFile: (entry: FsEntry) => void;
  onOpenInTab: (entry: FsEntry) => void;
  onOpenBeside: (entry: FsEntry) => void;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [ticks, setTicks] = useState<Record<string, number>>({});
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [matches, setMatches] = useState<FsSearchMatch[]>([]);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [dropPath, setDropPath] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const folderRef = useRef<HTMLInputElement>(null);
  const needle = query.trim();
  const watched = useMemo(() => [root, ...expanded], [root, expanded]);
  const previewRefresh = useRef<number | null>(null);

  useDirectoryWatch(root, watched, (dir) => {
    const key = pathKey(dir);
    setTicks((current) => ({ ...current, [key]: (current[key] ?? 0) + 1 }));
    if (previewRefresh.current !== null) window.clearTimeout(previewRefresh.current);
    previewRefresh.current = window.setTimeout(() => usePanelStore.getState().bumpRevision(), 400);
  });

  useEffect(() => {
    if (!needle) return;
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setSearching(true);
      setSearchError(null);
      void searchWorkspaceFiles(root, needle, controller.signal)
        .then((result) => {
          if (controller.signal.aborted) return;
          setMatches(result.matches);
          setSearchTruncated(result.truncated);
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted) return;
          setSearchError(fsError(cause));
        })
        .finally(() => {
          if (!controller.signal.aborted) setSearching(false);
        });
    }, 180);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [needle, root, revision]);

  function toggle(path: string) {
    setExpanded((current) => {
      const next = new Set(current);
      const existing = [...next].find((item) => pathKey(item) === pathKey(path));
      if (existing) next.delete(existing);
      else next.add(path);
      return next;
    });
  }

  function reveal(match: FsSearchMatch) {
    const absolute = joinWorkspace(root, match.path);
    if (match.is_dir) {
      const chain = new Set(expanded);
      let cursor = absolute;
      const rootKey = pathKey(root);
      while (cursor && (pathKey(cursor) === rootKey || pathKey(cursor).startsWith(`${rootKey}\\`))) {
        chain.add(cursor);
        if (pathKey(cursor) === rootKey) break;
        const parent = parentPath(cursor);
        if (!parent || parent === cursor) break;
        cursor = parent;
      }
      setExpanded(chain);
      setQuery("");
      return;
    }
    onOpenFile({ name: match.path.split("/").pop() || match.path, path: absolute, is_dir: false });
  }

  async function upload(files: File[], directory: string) {
    if (!files.length) return;
    setUploading(true);
    setUploadError(null);
    try {
      await uploadWorkspaceFiles(root, directory, files);
      setTicks((current) => ({ ...current, [pathKey(directory)]: (current[pathKey(directory)] ?? 0) + 1 }));
    } catch (cause) {
      setUploadError(fsError(cause));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div
      className="relative flex min-h-0 min-w-0 flex-1 flex-col"
      onDragOver={(event) => {
        if (![...event.dataTransfer.types].includes("Files")) return;
        event.preventDefault();
        setDropPath((current) => current ?? root);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        setDropPath(null);
      }}
      onDrop={(event) => {
        event.preventDefault();
        const directory = dropPath ?? root;
        setDropPath(null);
        void upload([...event.dataTransfer.files], directory);
      }}
    >
      <div className="flex items-center gap-1 px-2 pb-1 pt-2">
        <div className="flex h-7 min-w-0 flex-1 items-center gap-1.5 rounded-md border border-line px-2">
          <Search className="size-3.5 shrink-0 text-faint" />
          <input
            value={query}
            placeholder="搜索文件名"
            className="min-w-0 flex-1 bg-transparent text-[12px] outline-none placeholder:text-faint"
            onChange={(event) => setQuery(event.target.value)}
          />
          {query ? (
            <button type="button" className="text-faint hover:text-ink" aria-label="清除搜索" onClick={() => setQuery("")}>
              <X className="size-3" />
            </button>
          ) : null}
        </div>
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
          title="上传文件"
          aria-label="上传文件"
          onClick={() => fileRef.current?.click()}
        >
          <Upload className="size-3.5" />
        </button>
        <button
          type="button"
          className="flex size-7 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
          title="上传文件夹"
          aria-label="上传文件夹"
          onClick={() => folderRef.current?.click()}
        >
          <FolderUp className="size-3.5" />
        </button>
        <input
          ref={fileRef}
          type="file"
          multiple
          className="hidden"
          onChange={(event) => {
            void upload([...(event.target.files ?? [])], root);
            event.target.value = "";
          }}
        />
        <input
          ref={(node) => {
            folderRef.current = node;
            node?.setAttribute("webkitdirectory", "");
            node?.setAttribute("directory", "");
          }}
          type="file"
          className="hidden"
          multiple
          onChange={(event) => {
            void upload([...(event.target.files ?? [])], root);
            event.target.value = "";
          }}
        />
      </div>
      <div className="truncate px-3 pb-1 text-[11px] font-medium text-faint" title={root}>
        {workspaceLabel(root)}
      </div>
      {uploadError ? <p className="px-3 pb-1 text-[12px] text-danger">{uploadError}</p> : null}
      <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-3">
        {needle ? (
          <SearchResults
            root={root}
            matches={matches}
            searching={searching}
            truncated={searchTruncated}
            error={searchError}
            onOpen={reveal}
          />
        ) : (
          <DirectoryList
            root={root}
            path={root}
            depth={0}
            revision={revision}
            ticks={ticks}
            expanded={expanded}
            onToggle={toggle}
            activePath={activePath}
            dropPath={dropPath}
            onOpenFile={onOpenFile}
            onContextMenu={(entry, event) => setMenu({ x: event.clientX, y: event.clientY, entry })}
            onDragTarget={setDropPath}
          />
        )}
      </div>
      {dropPath ? (
        <div className="pointer-events-none absolute inset-2 flex items-end justify-center rounded-xl border border-dashed border-ink/30 bg-bg/70 p-3">
          <span className="rounded-full bg-ink px-3 py-1 text-[12px] text-white">
            上传到 {sameWorkspace(dropPath, root) ? workspaceLabel(root) : relativeTo(root, dropPath)}
          </span>
        </div>
      ) : null}
      {uploading ? (
        <div className="absolute bottom-2 left-2 flex items-center gap-1.5 rounded-full bg-ink px-2.5 py-1 text-[11px] text-white">
          <Loader2 className="size-3 animate-spin" />
          正在上传
        </div>
      ) : null}
      {menu ? (
        <EntryMenu
          menu={menu}
          root={root}
          onClose={() => setMenu(null)}
          onOpen={(entry) => {
            setMenu(null);
            onOpenInTab(entry);
          }}
          onOpenBeside={(entry) => {
            setMenu(null);
            onOpenBeside(entry);
          }}
        />
      ) : null}
    </div>
  );
}

function SearchResults({
  root,
  matches,
  searching,
  truncated,
  error,
  onOpen,
}: {
  root: string;
  matches: FsSearchMatch[];
  searching: boolean;
  truncated: boolean;
  error: string | null;
  onOpen: (match: FsSearchMatch) => void;
}) {
  if (error) return <p className="px-3 py-2 text-xs text-danger">{error}</p>;
  if (searching && matches.length === 0) {
    return (
      <div className="flex items-center gap-2 px-3 py-2 text-xs text-faint">
        <Loader2 className="size-3.5 animate-spin" />
        正在搜索
      </div>
    );
  }
  if (matches.length === 0) return <p className="px-3 py-2 text-xs text-faint">没有匹配的文件名</p>;
  return (
    <ul>
      {matches.map((match) => {
        const name = match.path.split("/").pop() || match.path;
        const Icon = match.is_dir ? Folder : fileIcon(name);
        return (
          <li key={match.path}>
            <button
              type="button"
              className="flex w-full min-w-0 items-center gap-1.5 rounded-lg px-2 py-1.5 text-left text-[13px] hover:bg-hover"
              title={joinWorkspace(root, match.path)}
              onClick={() => onOpen(match)}
            >
              <Icon className="size-3.5 shrink-0 text-muted" />
              <span className="min-w-0 shrink truncate">{name}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{match.path}</span>
            </button>
          </li>
        );
      })}
      {truncated ? <li className="px-2 py-1.5 text-[11px] text-faint">只显示前一部分匹配。</li> : null}
    </ul>
  );
}
