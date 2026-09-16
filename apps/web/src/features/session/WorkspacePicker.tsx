import { ChevronRight, Folder, Home, Plus } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { createDirectory, fetchDirectory } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useChatStore } from "@/store/chat";
import type { FsListing } from "@nono/protocol";

const INVALID_NAME = /[<>:"/\\|?*]/;

export function WorkspacePicker({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const addWorkspace = useChatStore((state) => state.addWorkspace);
  const [listing, setListing] = useState<FsListing | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [draftName, setDraftName] = useState<string | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  async function load(path?: string | null) {
    setError(null);
    setDraftName(null);
    try {
      setListing(await fetchDirectory(path));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "无法列出目录");
    }
  }

  useEffect(() => {
    if (!open) {
      setDraftName(null);
      setError(null);
      return;
    }
    void load();
  }, [open]);

  useEffect(() => {
    if (draftName === null) return;
    nameRef.current?.focus();
    nameRef.current?.select();
  }, [draftName]);

  if (!open) return null;

  const canCreate = Boolean(listing?.path);
  const entries = (listing?.entries ?? []).filter(
    (item) => showHidden || !item.name.startsWith("."),
  );

  function startCreate() {
    if (!canCreate) {
      setError("请先进入一个磁盘或文件夹，再新建");
      return;
    }
    setError(null);
    setDraftName("新建文件夹");
  }

  async function submitCreate(event?: FormEvent) {
    event?.preventDefault();
    const name = draftName?.trim() ?? "";
    if (!listing?.path || creating) return;
    if (!name) {
      setError("请输入文件夹名称");
      nameRef.current?.focus();
      return;
    }
    if (INVALID_NAME.test(name) || name === "." || name === "..") {
      setError("名称不能包含 \\ / : * ? \" < > |");
      nameRef.current?.focus();
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const created = await createDirectory(listing.path, name);
      setDraftName(null);
      await load(created.path);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/25 p-6">
      <div className="flex h-[min(36rem,80vh)] w-full max-w-lg flex-col rounded-2xl bg-bg shadow-xl">
        <div className="px-5 pt-5">
          <div className="text-[15px] font-medium">选择工作区目录</div>
          <div className="mt-3 flex items-center justify-between text-[13px] text-muted">
            <span className="min-w-0 truncate" title={listing?.path || "选择磁盘"}>
              {listing?.path || "选择磁盘"}
            </span>
            <button
              type="button"
              className="rounded-full p-1 hover:bg-hover"
              title="回到主目录"
              onClick={() => void load(listing?.home)}
            >
              <Home className="size-4" />
            </button>
          </div>
        </div>

        <div className="mt-2 min-h-0 flex-1 overflow-auto px-2">
          {listing?.parent != null ? (
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] text-muted hover:bg-hover"
              onClick={() => void load(listing.parent)}
            >
              <Folder className="size-4" />
              ..
            </button>
          ) : null}
          {draftName !== null ? (
            <form
              className="flex items-center gap-2 rounded-lg bg-hover px-3 py-1.5"
              onSubmit={(event) => void submitCreate(event)}
            >
              <Folder className="size-4 shrink-0 text-muted" />
              <Input
                ref={nameRef}
                value={draftName}
                placeholder="文件夹名称"
                disabled={creating}
                className="h-8 bg-bg"
                onChange={(event) => setDraftName(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") {
                    event.preventDefault();
                    setDraftName(null);
                    setError(null);
                  }
                }}
              />
              <Button type="submit" size="sm" disabled={creating || !draftName.trim()}>
                创建
              </Button>
            </form>
          ) : null}
          {entries.map((entry) => (
            <button
              key={entry.path}
              type="button"
              className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-[13px] hover:bg-hover"
              onClick={() => void load(entry.path)}
            >
              <Folder className="size-4 text-muted" />
              <span className="min-w-0 flex-1 truncate">{entry.name}</span>
              <ChevronRight className="size-4 text-faint" />
            </button>
          ))}
          {error ? <p className="px-3 py-2 text-xs text-danger">{error}</p> : null}
        </div>

        <div className="flex items-center justify-between gap-3 px-4 py-3">
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="flex items-center gap-1 rounded-full px-2 py-1 text-[13px] text-muted hover:bg-hover disabled:opacity-40"
              disabled={creating}
              title={canCreate ? "在当前目录新建文件夹" : "请先进入一个磁盘或文件夹"}
              onClick={startCreate}
            >
              <Plus className="size-3.5" />
              新建文件夹
            </button>
            <button
              type="button"
              className="text-[12px] text-faint hover:text-muted"
              onClick={() => setShowHidden((value) => !value)}
            >
              {showHidden ? "隐藏点文件" : "显示隐藏文件"}
            </button>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="ghost" onClick={onClose}>
              取消
            </Button>
            <Button
              disabled={!listing?.path}
              onClick={() => {
                if (!listing?.path) return;
                void addWorkspace(listing.path);
                onClose();
              }}
            >
              打开
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
