import type { FsEntry } from "@nono/protocol";
import { ChevronRight, File, FileText, Folder, Loader2 } from "lucide-react";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useState } from "react";

import { fetchWorkspaceEntries } from "@/api/client";
import { relativeTo, sameWorkspace } from "@/lib/workspace";
import { cn } from "@/lib/utils";

/**
 * 输入框里的 @文件 选择器。
 *
 * 打字 @ 之后弹出来，列出当前工作目录下的文件和文件夹，一层一层地看：
 * 上下键移动、回车选中文件夹里的文件、Tab 进入文件夹（标题栏有「上级」可以退回来）。
 *
 * 这里**不做按名搜索**：工作区里文件成千上万，一搜就是一大片结果，
 * 反而不知道该选哪个；一层层翻反而更准。跨工作区搜文件的事，交给右侧文件树
 * 顶部那个搜索框（那儿本来就是干这个的）。
 *
 * 焦点始终留在输入框里 —— 列表是用键盘操作的（上下移动、回车选中、Tab 进目录），
 * 所以点击列表项时不能让输入框失焦，否则光标位置就丢了。
 */

/** 列表里的一项。path 是绝对路径。 */
export type MentionItem = {
  path: string;
  name: string;
  isDir: boolean;
  /** 次要信息：所在目录。 */
  hint: string;
};

/** 输入框通过 ref 驱动列表：上下移动、选中、进入目录。 */
export type MentionPickerHandle = {
  move: (delta: number) => void;
  /** 选中当前项。没有可选项时返回 false，调用方据此决定要不要拦下这个按键。 */
  pick: () => boolean;
  /** 进入当前项所在的文件夹。当前项不是文件夹时返回 false。 */
  enter: () => boolean;
};

function fileIcon(name: string) {
  const lower = name.toLowerCase();
  if (/\.(png|jpe?g|gif|webp|svg|bmp|ico)$/.test(lower)) return File;
  if (/\.(md|markdown|txt|json|ya?ml|toml|xml|csv|py|ts|tsx|js|jsx|css|html|rs|go|vue|svelte)$/.test(lower)) {
    return FileText;
  }
  return File;
}

/** 上级目录。已经是盘符根就返回 null。 */
function parentOf(path: string): string | null {
  const trimmed = path.replace(/[\\/]+$/, "");
  const index = Math.max(trimmed.lastIndexOf("\\"), trimmed.lastIndexOf("/"));
  if (index <= 0) return null;
  const parent = trimmed.slice(0, index);
  // "D:" 这种盘符不是目录，别拿它当上一级。
  return /^[a-z]:$/i.test(parent) ? null : parent;
}

function fromEntry(entry: FsEntry): MentionItem {
  return {
    path: entry.path,
    name: entry.name,
    isDir: entry.is_dir,
    hint: "",
  };
}

export const MentionPicker = forwardRef<MentionPickerHandle, {
  root: string;
  onPick: (item: MentionItem) => void;
}>(function MentionPicker({ root, onPick }, ref) {
  const [dir, setDir] = useState(root);
  const [items, setItems] = useState<MentionItem[]>([]);
  const [index, setIndex] = useState(0);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  // 换工作区就回到根目录：上一层的路径在新工作区里多半不存在。
  useEffect(() => {
    setDir(root);
  }, [root]);

  useEffect(() => {
    if (!root) return;
    setFailed(null);
    // 浏览模式：当前目录的一层。
    let live = true;
    setLoading(true);
    void fetchWorkspaceEntries(root, dir)
      .then((listing) => {
        if (!live) return;
        // 隐藏文件默认不列出来：工作区根目录里那几个 .xxx 一挡就是好几行。
        const shown = listing.entries.filter((entry) => !entry.hidden);
        setItems(shown.map(fromEntry));
        setIndex(0);
      })
      .catch((cause: unknown) => {
        if (!live) return;
        setItems([]);
        setFailed(cause instanceof Error ? cause.message : "无法读取这个文件夹");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [root, dir]);

  useImperativeHandle(
    ref,
    () => ({
      move(delta) {
        setIndex((current) => {
          if (!items.length) return 0;
          return Math.min(items.length - 1, Math.max(0, current + delta));
        });
      },
      pick() {
        const item = items[index];
        if (!item) return false;
        onPick(item);
        return true;
      },
      enter() {
        const item = items[index];
        if (!item?.isDir) return false;
        setDir(item.path);
        return true;
      },
    }),
    [items, index, onPick],
  );

  const up = useMemo(() => {
    if (sameWorkspace(dir, root)) return null;
    const parent = parentOf(dir);
    if (!parent) return null;
    return parent;
  }, [dir, root]);

  const title =
    dir === root ? "文件与文件夹" : relativeTo(root, dir).replace(/\\/g, "/") || "文件与文件夹";

  return (
    <div
      className="absolute bottom-[calc(100%+10px)] left-0 z-30 w-[min(30rem,calc(100vw-4rem))] overflow-hidden rounded-2xl border border-line bg-bg shadow-[0_12px_32px_var(--shadow-color)]"
      // 不让输入框失焦：光标位置是插入引用时用的。
      onMouseDown={(event) => event.preventDefault()}
    >
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 text-[11px] text-faint">
        {up ? (
          <button
            type="button"
            className="flex shrink-0 items-center gap-0.5 rounded px-1 py-0.5 hover:bg-hover hover:text-ink"
            onClick={() => setDir(up)}
          >
            <ChevronRight className="size-3 rotate-180" />
            上级
          </button>
        ) : null}
        <span className="min-w-0 flex-1 truncate" title={dir}>
          {title}
        </span>
        {loading ? <Loader2 className="size-3 shrink-0 animate-spin" /> : null}
      </div>
      <div className="max-h-72 overflow-auto p-1" role="listbox">
        {items.length === 0 ? (
          <p className="px-3 py-4 text-[12px] text-faint">
            {loading ? "正在查找…" : "这个文件夹是空的"}
          </p>
        ) : (
          items.map((item, position) => {
            const Icon = item.isDir ? Folder : fileIcon(item.name);
            return (
              <div
                key={item.path}
                role="option"
                aria-selected={position === index}
                className={cn(
                  "flex w-full items-center gap-2 rounded-xl px-3 py-1.5 text-left text-[13px]",
                  position === index ? "bg-hover text-ink" : "text-ink/80",
                )}
                onMouseEnter={() => setIndex(position)}
                onClick={() => onPick(item)}
              >
                <Icon className="size-3.5 shrink-0 text-muted" />
                <span className="min-w-0 shrink truncate">{item.name}</span>
                <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{item.hint}</span>
                {item.isDir && position === index ? (
                  <button
                    type="button"
                    className="flex shrink-0 items-center gap-0.5 rounded px-1.5 py-0.5 text-[11px] text-faint hover:bg-bg hover:text-ink"
                    onClick={(event) => {
                      event.stopPropagation();
                      setDir(item.path);
                    }}
                  >
                    进入目录
                    <span className="text-[10px] opacity-70">Tab ›</span>
                  </button>
                ) : null}
              </div>
            );
          })
        )}
      </div>
      {failed ? <p className="border-t border-line px-3 py-2 text-[11px] text-danger">{failed}</p> : null}
    </div>
  );
});
