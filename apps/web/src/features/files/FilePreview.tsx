import { ChevronDown, WrapText } from "lucide-react";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";

import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { ensureBuiltinPreviews, fileNameOf, formatBytes, getDocumentPreviews, previewCandidates, subscribeDocumentPreviews } from "@/features/files/preview";
import { loadPreview, type LoadedPreview } from "@/features/files/preview/load";
import type { PreviewLoading as PreviewLoadMode } from "@/features/files/preview/types";
import { normalizeWorkspace } from "@/lib/workspace";

function relativePath(root: string, target: string): string {
  const base = normalizeWorkspace(root);
  const full = normalizeWorkspace(target);
  if (full.toLowerCase().startsWith(`${base.toLowerCase()}\\`) || full.toLowerCase() === base.toLowerCase()) {
    return full.slice(base.length).replace(/^\\/, "") || full;
  }
  return full;
}

function isAbort(cause: unknown): boolean {
  return cause instanceof Error && cause.name === "AbortError";
}

function readError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : "无法读取文件";
  if (message === "Not Found") return "读不到文件接口，请重启本机服务后再试";
  return message;
}

ensureBuiltinPreviews();

export function FilePreview({ root, path, revision }: { root: string; path: string; revision: number }) {
  const definitions = useSyncExternalStore(subscribeDocumentPreviews, getDocumentPreviews, getDocumentPreviews);
  const candidates = useMemo(() => previewCandidates(definitions, path), [definitions, path]);
  const [rendererId, setRendererId] = useState<string | null>(null);
  const [wrap, setWrap] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [loaded, setLoaded] = useState<(LoadedPreview & { loading: PreviewLoadMode }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const selected = candidates.find((item) => item.id === rendererId) ?? candidates[0];
  const current = loaded !== null && selected !== undefined && loaded.loading === selected.loading ? loaded : null;

  useEffect(() => {
    if (selected === undefined) return;
    const controller = new AbortController();
    const loading = selected.loading;
    setError(null);
    void loadPreview(root, path, loading, revision, controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setLoaded({ ...result, loading });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbort(cause)) return;
        setLoaded(null);
        setError(readError(cause));
      });
    return () => controller.abort();
  }, [path, revision, root, selected]);

  const Body = selected?.Body;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex items-center gap-1 border-b border-line px-3 py-1.5">
        <span className="min-w-0 flex-1 truncate text-[12px] text-faint" title={path}>
          {relativePath(root, path)}
        </span>
        {current ? <span className="shrink-0 text-[11px] text-faint">{formatBytes(current.size)}</span> : null}
        {selected && candidates.length > 1 ? (
          <Dropdown
            open={menuOpen}
            onOpenChange={setMenuOpen}
            align="right"
            trigger={
              <button
                type="button"
                className="flex h-7 max-w-28 items-center gap-1 rounded-md px-1.5 text-[12px] text-muted hover:bg-hover hover:text-ink"
                aria-label="预览方式"
                onClick={() => setMenuOpen((value) => !value)}
              >
                <span className="truncate">{selected.title}</span>
                <ChevronDown className="size-3 shrink-0" />
              </button>
            }
          >
            {candidates.map((item) => (
              <DropdownItem
                key={item.id}
                selected={item.id === selected.id}
                className="text-[13px]"
                onClick={() => {
                  setRendererId(item.id);
                  setMenuOpen(false);
                }}
              >
                {item.title}
              </DropdownItem>
            ))}
          </Dropdown>
        ) : null}
        {selected?.wrap ? (
          <button
            type="button"
            className={`flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-hover hover:text-ink ${
              wrap ? "text-ink" : "text-faint"
            }`}
            title={wrap ? "关闭自动换行" : "打开自动换行"}
            aria-label={wrap ? "关闭自动换行" : "打开自动换行"}
            aria-pressed={wrap}
            onClick={() => setWrap((value) => !value)}
          >
            <WrapText className="size-3.5" />
          </button>
        ) : null}
      </div>
      {error ? (
        <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>
      ) : !current || !Body || !selected ? (
        <PreviewLoading />
      ) : (
        <div className="min-h-0 flex-1 overflow-hidden">
          <Body
            root={root}
            path={path}
            name={fileNameOf(path)}
            content={current.content}
            wrap={wrap}
          />
        </div>
      )}
    </div>
  );
}
