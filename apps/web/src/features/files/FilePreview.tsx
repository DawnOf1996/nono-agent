import { ChevronDown, Eye, Loader2, Pencil, RotateCcw, Save, WrapText, X } from "lucide-react";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { SaveConflictError, saveWorkspaceFile } from "@/api/client";
import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
import { SelectionQuote } from "@/features/chat/SelectionQuote";
import type { CodeEditorHandle } from "@/features/files/editor/CodeEditor";
import { usePanelStore } from "@/features/files/panel-store";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { ensureBuiltinPreviews, fileNameOf, formatBytes, getDocumentPreviews, PLAIN_PREVIEW_ID, previewCandidates, subscribeDocumentPreviews } from "@/features/files/preview";
import { getSidebarPrefs, subscribeSidebarPrefs, type SidebarPrefs } from "@/features/settings/sidebar-prefs";
import { loadPreview, type LoadedPreview } from "@/features/files/preview/load";
import type { PreviewLoading as PreviewLoadMode } from "@/features/files/preview/types";
import { relativeTo } from "@/lib/workspace";
import { cn } from "@/lib/utils";

const CodeEditor = lazy(() => import("@/features/files/editor/CodeEditor"));

type SaveState = "idle" | "saving" | "saved" | "conflict" | "error";

function isAbort(cause: unknown): boolean {
  return cause instanceof Error && cause.name === "AbortError";
}

function readError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : "无法读取文件";
  if (message === "Not Found") return "读不到文件接口，请重启本机服务后再试";
  return message;
}

ensureBuiltinPreviews();

function previewEnabled(id: string, previewers: SidebarPrefs["previewers"]): boolean {
  if (id === PLAIN_PREVIEW_ID) return true;
  const key = id.split("/").pop();
  if (key === "markdown" || key === "html" || key === "image" || key === "pdf" || key === "code" || key === "sheet" || key === "docx" || key === "pptx") {
    return previewers[key];
  }
  return true;
}

export function FilePreview({
  root,
  path,
  revision,
  tabId,
  onClose,
}: {
  root: string;
  path: string;
  revision: number;
  tabId: string;
  onClose?: () => void;
}) {
  const definitions = useSyncExternalStore(subscribeDocumentPreviews, getDocumentPreviews, getDocumentPreviews);
  const previewers = useSyncExternalStore(subscribeSidebarPrefs, getSidebarPrefs, getSidebarPrefs).previewers;
  const candidates = useMemo(
    () => previewCandidates(definitions, path).filter((item) => previewEnabled(item.id, previewers)),
    [definitions, path, previewers],
  );
  const [rendererId, setRendererId] = useState<string | null>(null);
  const [wrap, setWrap] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [loaded, setLoaded] = useState<(LoadedPreview & { loading: PreviewLoadMode }) | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [editorOpened, setEditorOpened] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [draft, setDraft] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState<string | null>(null);
  const editorRef = useRef<CodeEditorHandle>(null);
  // 预览容器的滚动壳。选区层挂在它上面，滚动时要跟着重算气泡位置。
  const previewRef = useRef<HTMLDivElement>(null);
  const baseHash = useRef<string | null>(null);
  const dirtyRef = useRef(false);
  const setPanelDirty = usePanelStore((state) => state.setDirty);
  const setDraftText = usePanelStore((state) => state.setDraftText);
  const draftFor = usePanelStore((state) => state.draftFor);
  /**
   * 切走再切回来时接回上次没保存的内容。
   *
   * 面板按会话分桶，标签页会在切换时被换成另一批 —— 组件随之卸载，CodeMirror 里
   * 那份没落盘的编辑也就没了。所以每次变脏都把内容交给 store 存一份（只存内存），
   * 重新挂载时用它当初始内容填回去，磁盘上那份留作对照和冲突检测的基线。
   */
  const carriedDraft = useRef(draftFor(tabId)).current;
  useEffect(() => {
    if (carriedDraft === null) return;
    dirtyRef.current = true;
    setDirty(true);
    setEditorOpened(true);
    setMode("edit");
    setPanelDirty(tabId, true);
  }, [carriedDraft, setPanelDirty, tabId]);
  const bumpRevision = usePanelStore((state) => state.bumpRevision);
  const selected = candidates.find((item) => item.id === rendererId) ?? candidates[0];
  const current = loaded !== null && selected !== undefined && loaded.loading === selected.loading ? loaded : null;
  const loadedText = current?.content.kind === "text" ? current.content.text : null;
  const editable = selected?.loading === "text" && loadedText !== null;

  useEffect(() => {
    if (selected === undefined) return;
    const controller = new AbortController();
    const loading = selected.loading;
    setError(null);
    void loadPreview(root, path, loading, revision, controller.signal)
      .then((result) => {
        if (controller.signal.aborted) return;
        // 有未保存的编辑时不动编辑器，也保留打开时的基线哈希，保存时才能发现磁盘上的改动。
        if (!dirtyRef.current && result.content.kind === "text") {
          baseHash.current = result.hash ?? null;
          const editor = editorRef.current;
          if (editor && editor.getValue() !== result.content.text) editor.reset(result.content.text);
        }
        setLoaded({ ...result, loading });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbort(cause)) return;
        setLoaded(null);
        setError(readError(cause));
      });
    return () => controller.abort();
  }, [path, revision, root, selected]);

  useEffect(() => {
    if (saveState !== "saved") return;
    const timer = window.setTimeout(() => setSaveState("idle"), 1600);
    return () => window.clearTimeout(timer);
  }, [saveState]);

  useEffect(() => () => setPanelDirty(tabId, false), [setPanelDirty, tabId]);

  const onDirtyChange = useCallback(
    (next: boolean) => {
      dirtyRef.current = next;
      setDirty(next);
      setPanelDirty(tabId, next);
      // 变干净了就别再留着旧内容：reset / markSaved 走的是同一条回调。
      if (!next) {
        setDraftText(tabId, null);
        return;
      }
      setDraftText(tabId, editorRef.current?.getValue() ?? null);
      setSaveState((state) => (state === "saved" ? "idle" : state));
    },
    [setDraftText, setPanelDirty, tabId],
  );

  const save = useCallback(
    async (force = false) => {
      const editor = editorRef.current;
      if (!editor) return;
      const text = editor.getValue();
      setSaveState("saving");
      setSaveError(null);
      try {
        const result = await saveWorkspaceFile(root, path, text, force ? null : baseHash.current);
        baseHash.current = result.hash;
        editor.markSaved();
        setLoaded((prev) =>
          prev && prev.content.kind === "text"
            ? { ...prev, content: { kind: "text", text }, size: result.size, hash: result.hash }
            : prev,
        );
        setDraft(null);
        setSaveState("saved");
        bumpRevision();
      } catch (cause) {
        if (cause instanceof SaveConflictError) {
          setSaveState("conflict");
          return;
        }
        setSaveState("error");
        setSaveError(cause instanceof Error ? cause.message : "保存失败");
      }
    },
    [root, path, bumpRevision],
  );

  function enterEdit() {
    setEditorOpened(true);
    setMode("edit");
    window.setTimeout(() => editorRef.current?.focus(), 0);
  }

  function enterView() {
    setDraft(dirtyRef.current ? (editorRef.current?.getValue() ?? null) : null);
    setMode("view");
  }

  function discardEdits() {
    if (loadedText === null) return;
    editorRef.current?.reset(loadedText);
    setDraft(null);
    setSaveState("idle");
  }

  function reloadFromDisk() {
    dirtyRef.current = false;
    setDraft(null);
    setDraftText(tabId, null);
    setSaveState("idle");
    bumpRevision();
  }

  const Body = selected?.Body;
  const showEditor = editorOpened && loadedText !== null;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex items-center gap-1 border-b border-line px-3 py-1.5">
        <span className="min-w-0 flex-1 truncate text-[12px] text-faint" title={path}>
          {dirty ? <span className="mr-1 inline-block size-1.5 rounded-full bg-ink align-middle" /> : null}
          {relativeTo(root, path)}
        </span>
        {saveState === "saved" ? <span className="shrink-0 text-[11px] text-muted">已保存</span> : null}
        {current && !dirty && saveState !== "saved" ? (
          <span className="shrink-0 text-[11px] text-faint">{formatBytes(current.size)}</span>
        ) : null}
        {selected && candidates.length > 1 && mode === "view" && !dirty ? (
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
        {selected?.wrap || mode === "edit" ? (
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
        {dirty ? (
          <button
            type="button"
            className="flex size-7 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
            title="放弃修改"
            aria-label="放弃修改"
            onClick={discardEdits}
          >
            <RotateCcw className="size-3.5" />
          </button>
        ) : null}
        {editable || showEditor ? (
          mode === "edit" ? (
            <>
              <button
                type="button"
                className="flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[12px] text-muted hover:bg-hover hover:text-ink"
                title="预览"
                onClick={enterView}
              >
                <Eye className="size-3.5" />
                预览
              </button>
              <button
                type="button"
                disabled={!dirty || saveState === "saving"}
                className="flex h-7 shrink-0 items-center gap-1 rounded-md bg-accent px-2 text-[12px] text-on-accent hover:bg-accent-hover disabled:opacity-40"
                title="保存（Ctrl+S）"
                onClick={() => void save()}
              >
                {saveState === "saving" ? <Loader2 className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
                保存
              </button>
            </>
          ) : (
            <button
              type="button"
              className="flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[12px] text-muted hover:bg-hover hover:text-ink"
              title="编辑"
              onClick={enterEdit}
            >
              <Pencil className="size-3.5" />
              编辑
            </button>
          )
        ) : null}
        {onClose ? (
          <button
            type="button"
            className="flex size-7 shrink-0 items-center justify-center rounded-md text-faint hover:bg-hover hover:text-ink"
            title="关闭侧边预览"
            aria-label="关闭侧边预览"
            onClick={onClose}
          >
            <X className="size-3.5" />
          </button>
        ) : null}
      </div>
      {saveState === "conflict" ? (
        <div className="flex flex-wrap items-center gap-2 border-b border-warn-line bg-warn-soft px-3 py-1.5 text-[12px] text-warn">
          <span className="min-w-0 flex-1">磁盘上的文件在你编辑期间被改过了。</span>
          <button type="button" className="rounded-md px-2 py-0.5 hover:bg-warn-hover" onClick={reloadFromDisk}>
            载入磁盘版本
          </button>
          <button
            type="button"
            className="rounded-md bg-warn px-2 py-0.5 text-on-warn hover:bg-warn-strong"
            onClick={() => void save(true)}
          >
            覆盖保存
          </button>
        </div>
      ) : null}
      {saveState === "error" && saveError ? (
        <p className="border-b border-line px-3 py-1.5 text-[12px] text-danger">{saveError}</p>
      ) : null}
      {error ? (
        <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>
      ) : !current || !Body || !selected ? (
        <PreviewLoading />
      ) : (
        <div className="relative min-h-0 flex-1 overflow-hidden" ref={previewRef}>
          {showEditor ? (
            <div className={cn("h-full", mode === "edit" ? "" : "hidden")}>
              <Suspense fallback={<PreviewLoading />}>
                <CodeEditor
                  path={path}
                  initial={carriedDraft ?? loadedText}
                  wrap={wrap}
                  editorRef={editorRef}
                  onDirtyChange={onDirtyChange}
                  onSave={() => void save()}
                />
              </Suspense>
            </div>
          ) : null}
          {mode === "view" ? (
            <Body
              root={root}
              path={path}
              name={fileNameOf(path)}
              content={draft !== null ? { kind: "text", text: draft } : current.content}
              wrap={wrap}
            />
          ) : null}
          {/* 预览态读浏览器选区；编辑态读 CodeMirror 自己的选区。 */}
          {mode === "view" ? (
            <SelectionQuote containerRef={previewRef} />
          ) : showEditor ? (
            <SelectionQuote
              containerRef={previewRef}
              external={{
                read: () => editorRef.current?.getSelection() ?? null,
                root,
                path,
              }}
            />
          ) : null}
        </div>
      )}
    </div>
  );
}
