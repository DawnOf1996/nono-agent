import type { FileDiff, GitCommitDetail, TurnChanges } from "@nono/protocol";
import {
  ChevronDown,
  ChevronRight,
  Columns2,
  FileText,
  Minus,
  Plus,
  RefreshCw,
  Rows2,
  UnfoldVertical,
  Undo2,
  WrapText,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import {
  SaveConflictError,
  fetchGitCommit,
  fetchGitDiff,
  fetchTurnDiff,
  gitDiscard,
  gitStage,
  gitUnstage,
  revertTurnFile,
} from "@/api/client";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
import { DiffStat, StatusBadge, statusLetter } from "@/features/files/diff/DiffStat";
import { DiffView } from "@/features/files/diff/DiffView";
import { useDiffPrefs } from "@/features/files/diff/prefs";
import { usePanelStore, type DiffTarget } from "@/features/files/panel-store";
import { PreviewLoading } from "@/features/files/preview/Loading";
import { baseName, parentPath } from "@/lib/workspace";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

const FULL_CONTEXT = 100_000;

type Confirm = {
  title: string;
  description: string;
  confirmLabel: string;
  danger?: boolean;
  run: () => Promise<void>;
};

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

function ToolButton({
  title,
  active,
  disabled,
  onClick,
  children,
}: {
  title: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      aria-pressed={active}
      disabled={disabled}
      className={cn(
        "flex size-7 shrink-0 items-center justify-center rounded-md hover:bg-hover hover:text-ink disabled:opacity-40",
        active ? "text-ink" : "text-faint",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

function ActionButton({
  label,
  icon,
  danger,
  disabled,
  onClick,
}: {
  label: string;
  icon: ReactNode;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      className={cn(
        "flex h-7 shrink-0 items-center gap-1 rounded-md px-2 text-[12px] disabled:opacity-40",
        danger ? "text-danger hover:bg-danger/10" : "text-muted hover:bg-hover hover:text-ink",
      )}
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}

function useTurn(sessionId: string, turnId: string): TurnChanges | undefined {
  const turns = useChatStore((state) => state.turnChanges[sessionId]);
  const load = useChatStore((state) => state.loadTurnChanges);
  const turn = turns?.find((item) => item.id === turnId);
  useEffect(() => {
    if (!turns) void load(sessionId);
  }, [turns, load, sessionId]);
  return turn;
}

/** 单个文件的对比加载：切换目标、展开上下文或外部刷新（revision）时重新拉取。 */
function useFileDiff(
  load: ((context: number, signal: AbortSignal) => Promise<FileDiff>) | null,
  full: boolean,
  revision = 0,
): { diff: FileDiff | null; error: string | null; reload: () => void } {
  const [diff, setDiff] = useState<FileDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);
  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const shownFor = useRef(load);
  useEffect(() => {
    if (!load) return;
    if (shownFor.current !== load) {
      shownFor.current = load;
      setDiff(null);
    }
    const controller = new AbortController();
    setError(null);
    void load(full ? FULL_CONTEXT : 3, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setDiff(next);
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted) return;
        setError(errorText(cause, "无法读取对比"));
      });
    return () => controller.abort();
  }, [load, full, revision, nonce]);
  return { diff, error, reload };
}

function DiffBody({
  diff,
  error,
  layout,
  wrap,
}: {
  diff: FileDiff | null;
  error: string | null;
  layout: "unified" | "split";
  wrap: boolean;
}) {
  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (!diff) return <PreviewLoading />;
  return (
    <div className="min-h-0 flex-1 overflow-auto">
      <DiffView diff={diff} layout={layout} wrap={wrap} />
    </div>
  );
}

export function DiffTab({ tabId, target, visible }: { tabId: string; target: DiffTarget; visible: boolean }) {
  if (target.source === "commit") return <CommitDiff target={target} visible={visible} />;
  if (target.source === "turn") return <TurnDiff tabId={tabId} target={target} visible={visible} />;
  if (target.source === "op") return <OpDiff target={target} />;
  return <GitFileDiff tabId={tabId} target={target} visible={visible} />;
}

function OpDiff({ target }: { target: Extract<DiffTarget, { source: "op" }> }) {
  const prefs = useDiffPrefs();
  const openFile = usePanelStore((state) => state.openFile);
  if (target.errorText) {
    return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{target.errorText}</p>;
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1 border-b border-line px-2">
        <span className="min-w-0 flex-1 truncate text-[12px] text-faint" title={target.diff.path}>
          {target.diff.path}
        </span>
        <button
          type="button"
          className="flex h-7 items-center gap-1 rounded-md px-2 text-[12px] text-muted hover:bg-hover hover:text-ink"
          onClick={() => openFile(target.diff.path)}
        >
          <FileText className="size-3.5" />
          打开文件
        </button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <DiffView diff={target.diff} layout={prefs.layout} wrap={prefs.wrap} />
      </div>
    </div>
  );
}

function GitFileDiff({
  tabId,
  target,
  visible,
}: {
  tabId: string;
  target: Extract<DiffTarget, { source: "git" }>;
  visible: boolean;
}) {
  const prefs = useDiffPrefs();
  const [full, setFull] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const revision = usePanelStore((state) => state.revision);
  const openFile = usePanelStore((state) => state.openFile);
  const openDiff = usePanelStore((state) => state.openDiff);
  const close = usePanelStore((state) => state.close);
  const bumpRevision = usePanelStore((state) => state.bumpRevision);
  const load = useMemo(
    () => (context: number, signal: AbortSignal) =>
      fetchGitDiff(
        { root: target.root, repo: target.repo, path: target.path, staged: target.staged, context },
        signal,
      ),
    [target.root, target.repo, target.path, target.staged],
  );
  const { diff, error, reload } = useFileDiff(visible ? load : null, full, revision);

  async function run(action: () => Promise<void>, after?: () => void) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      bumpRevision();
      after?.();
    } catch (cause) {
      setActionError(errorText(cause, "操作失败"));
    } finally {
      setBusy(false);
    }
  }

  function switchSide(staged: boolean) {
    openDiff({ ...target, staged, untracked: staged ? false : target.untracked }, baseName(target.path));
    close(tabId);
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex min-h-9 flex-wrap items-center gap-1 border-b border-line px-3 py-1">
        <span className="min-w-0 flex-1 truncate text-[12px] text-faint" title={target.absPath}>
          {parentPath(target.path) ? `${parentPath(target.path)}/` : ""}
          <span className="text-ink">{baseName(target.path)}</span>
          <span className="ml-2 rounded bg-card px-1.5 py-0.5 text-[11px] text-muted">
            {target.staged ? "暂存区" : target.untracked ? "未跟踪" : "工作区"}
          </span>
        </span>
        {diff ? <DiffStat added={diff.added} deleted={diff.deleted} binary={diff.kind === "binary"} /> : null}
        <ToolButton title={prefs.layout === "split" ? "统一视图" : "并排视图"} onClick={prefs.toggleLayout}>
          {prefs.layout === "split" ? <Rows2 className="size-3.5" /> : <Columns2 className="size-3.5" />}
        </ToolButton>
        <ToolButton title={prefs.wrap ? "关闭自动换行" : "自动换行"} active={prefs.wrap} onClick={prefs.toggleWrap}>
          <WrapText className="size-3.5" />
        </ToolButton>
        <ToolButton title={full ? "只看改动附近" : "展开全部上下文"} active={full} onClick={() => setFull((v) => !v)}>
          <UnfoldVertical className="size-3.5" />
        </ToolButton>
        <ToolButton title="刷新" onClick={reload}>
          <RefreshCw className="size-3.5" />
        </ToolButton>
        <ToolButton title="打开文件" onClick={() => openFile(target.absPath)}>
          <FileText className="size-3.5" />
        </ToolButton>
      </div>
      <div className="flex items-center gap-1 border-b border-line px-2 py-1">
        {target.staged ? (
          <ActionButton
            label="取消暂存"
            icon={<Minus className="size-3.5" />}
            disabled={busy}
            onClick={() => void run(() => gitUnstage(target.root, target.repo, [target.path]), () => switchSide(false))}
          />
        ) : (
          <>
            <ActionButton
              label="暂存"
              icon={<Plus className="size-3.5" />}
              disabled={busy}
              onClick={() => void run(() => gitStage(target.root, target.repo, [target.path]), () => switchSide(true))}
            />
            <ActionButton
              label="丢弃改动"
              danger
              icon={<Undo2 className="size-3.5" />}
              disabled={busy}
              onClick={() =>
                setConfirm({
                  title: "丢弃改动？",
                  description: target.untracked
                    ? `将删除未跟踪的文件 ${target.path}，无法撤销。`
                    : `${target.path} 在工作区的改动会被丢弃，恢复成暂存区的版本，无法撤销。`,
                  confirmLabel: "丢弃",
                  danger: true,
                  run: () =>
                    run(
                      () =>
                        target.untracked
                          ? gitDiscard(target.root, target.repo, [], [target.path])
                          : gitDiscard(target.root, target.repo, [target.path], []),
                      () => close(tabId),
                    ),
                })
              }
            />
          </>
        )}
        {actionError ? <span className="min-w-0 truncate text-[12px] text-danger">{actionError}</span> : null}
      </div>
      <DiffBody diff={diff} error={error} layout={prefs.layout} wrap={prefs.wrap} />
      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ""}
        description={confirm?.description ?? ""}
        confirmLabel={confirm?.confirmLabel}
        danger={confirm?.danger}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          const current = confirm;
          setConfirm(null);
          void current?.run();
        }}
      />
    </div>
  );
}

function TurnDiff({
  tabId,
  target,
  visible,
}: {
  tabId: string;
  target: Extract<DiffTarget, { source: "turn" }>;
  visible: boolean;
}) {
  const prefs = useDiffPrefs();
  const [full, setFull] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const openFile = usePanelStore((state) => state.openFile);
  const retarget = usePanelStore((state) => state.retarget);
  const bumpRevision = usePanelStore((state) => state.bumpRevision);
  const turn = useTurn(target.sessionId, target.turnId);
  const file = turn?.files[target.index];
  const load = useMemo(
    () => (context: number, signal: AbortSignal) =>
      fetchTurnDiff(target.sessionId, target.turnId, target.index, context, signal),
    [target.sessionId, target.turnId, target.index],
  );
  const { diff, error } = useFileDiff(visible ? load : null, full);

  function choose(index: number) {
    const next = turn?.files[index];
    setMenuOpen(false);
    setFull(false);
    setActionError(null);
    retarget(tabId, { ...target, index }, next ? baseName(next.path) : "本轮改动");
  }

  async function revert(force: boolean) {
    setBusy(true);
    setActionError(null);
    try {
      await revertTurnFile(target.sessionId, target.turnId, target.index, force);
      bumpRevision();
      setActionError("已还原到这一轮之前的内容");
    } catch (cause) {
      if (cause instanceof SaveConflictError && !force) {
        setConfirm({
          title: "文件之后又被改过",
          description: "这个文件在这一轮结束后又有改动。仍然还原，会丢掉这些后续改动。",
          confirmLabel: "仍然还原",
          danger: true,
          run: () => revert(true),
        });
      } else {
        setActionError(errorText(cause, "还原失败"));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="flex min-h-9 items-center gap-1 border-b border-line px-2 py-1">
        {turn && turn.files.length > 1 ? (
          <Dropdown
            open={menuOpen}
            onOpenChange={setMenuOpen}
            panelClassName="max-w-[min(420px,90vw)]"
            trigger={
              <button
                type="button"
                className="flex h-7 min-w-0 max-w-full items-center gap-1 rounded-md px-1.5 text-[12px] text-ink hover:bg-hover"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                title={file?.display}
                onClick={() => setMenuOpen((value) => !value)}
              >
                <span className="truncate">{file?.display ?? "选择文件"}</span>
                <span className="shrink-0 text-faint">
                  {target.index + 1}/{turn.files.length}
                </span>
                <ChevronDown className="size-3 shrink-0 text-faint" />
              </button>
            }
          >
            {turn.files.map((item, index) => (
              <DropdownItem
                key={`${item.display}-${index}`}
                selected={index === target.index}
                className="items-center text-[12px]"
                onClick={() => choose(index)}
              >
                <StatusBadge letter={statusLetter(item.status)} />
                <span className="min-w-0 flex-1 truncate">{item.display}</span>
                <DiffStat added={item.added} deleted={item.deleted} binary={item.binary} oversized={item.oversized} />
              </DropdownItem>
            ))}
          </Dropdown>
        ) : (
          <span className="min-w-0 truncate px-1.5 text-[12px] text-ink" title={file?.abs_path}>
            {file?.display ?? "本轮改动"}
          </span>
        )}
        <span className="flex-1" />
        {diff ? <DiffStat added={diff.added} deleted={diff.deleted} binary={diff.kind === "binary"} /> : null}
        <ToolButton title={prefs.layout === "split" ? "统一视图" : "并排视图"} onClick={prefs.toggleLayout}>
          {prefs.layout === "split" ? <Rows2 className="size-3.5" /> : <Columns2 className="size-3.5" />}
        </ToolButton>
        <ToolButton title={prefs.wrap ? "关闭自动换行" : "自动换行"} active={prefs.wrap} onClick={prefs.toggleWrap}>
          <WrapText className="size-3.5" />
        </ToolButton>
        <ToolButton title={full ? "只看改动附近" : "展开全部上下文"} active={full} onClick={() => setFull((v) => !v)}>
          <UnfoldVertical className="size-3.5" />
        </ToolButton>
        <ToolButton
          title="打开文件"
          disabled={!file || file.status === "deleted"}
          onClick={() => file && openFile(file.abs_path)}
        >
          <FileText className="size-3.5" />
        </ToolButton>
      </div>
      <div className="flex items-center gap-1 border-b border-line px-2 py-1">
        <span className="px-1 text-[11px] text-faint">这一轮对话的改动</span>
        <span className="flex-1" />
        {actionError ? <span className="min-w-0 truncate text-[12px] text-muted">{actionError}</span> : null}
        <ActionButton
          label="还原此文件"
          danger
          icon={<Undo2 className="size-3.5" />}
          disabled={busy || !file || file.oversized}
          onClick={() =>
            setConfirm({
              title: "还原这个文件？",
              description: `${file?.display ?? ""} 会恢复成这一轮对话开始前的内容${
                file?.status === "added" ? "（这一轮新建的文件会被删除）" : ""
              }。`,
              confirmLabel: "还原",
              danger: true,
              run: () => revert(false),
            })
          }
        />
      </div>
      <DiffBody diff={diff} error={error} layout={prefs.layout} wrap={prefs.wrap} />
      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ""}
        description={confirm?.description ?? ""}
        confirmLabel={confirm?.confirmLabel}
        danger={confirm?.danger}
        onCancel={() => setConfirm(null)}
        onConfirm={() => {
          const current = confirm;
          setConfirm(null);
          void current?.run();
        }}
      />
    </div>
  );
}

function CommitDiff({ target, visible }: { target: Extract<DiffTarget, { source: "commit" }>; visible: boolean }) {
  const prefs = useDiffPrefs();
  const [detail, setDetail] = useState<GitCommitDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<number>>(new Set());
  const openFile = usePanelStore((state) => state.openFile);
  // 底部的预览会复用同一个组件：换提交时要把上一条清掉再取新的，否则旧内容会一直顶着。
  // 这个 ref 只用来判断「换目标了没有」，不能拿来挡掉这次取数——StrictMode 下 effect 挂载时
  // 会跑两遍（第一遍的请求被 cleanup 中止），在这里提前 return 就没有第二次请求了。
  const shownFor = useRef("");

  useEffect(() => {
    if (!visible) return;
    if (shownFor.current !== target.hash) {
      shownFor.current = target.hash;
      setCollapsed(new Set());
    }
    const controller = new AbortController();
    setDetail(null);
    setError(null);
    void fetchGitCommit(target.root, target.repo, target.hash, controller.signal)
      .then((next) => {
        if (!controller.signal.aborted) setDetail(next);
      })
      .catch((cause: unknown) => {
        if (!controller.signal.aborted) setError(errorText(cause, "无法读取提交"));
      });
    return () => controller.abort();
  }, [target.root, target.repo, target.hash, visible]);

  const totals = useMemo(
    () =>
      detail?.files.reduce(
        (sum, file) => ({ added: sum.added + file.added, deleted: sum.deleted + file.deleted }),
        { added: 0, deleted: 0 },
      ) ?? { added: 0, deleted: 0 },
    [detail],
  );

  if (error) return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  if (!detail) return <PreviewLoading />;
  const repoRoot = target.repo ?? target.root;

  return (
    <div className="flex min-h-0 flex-1 flex-col bg-bg">
      <div className="shrink-0 border-b border-line px-3 py-2">
        <div className="flex items-start gap-2">
          <p className="min-w-0 flex-1 text-[13px] font-medium leading-5">{detail.subject}</p>
          <ToolButton title={prefs.layout === "split" ? "统一视图" : "并排视图"} onClick={prefs.toggleLayout}>
            {prefs.layout === "split" ? <Rows2 className="size-3.5" /> : <Columns2 className="size-3.5" />}
          </ToolButton>
          <ToolButton title={prefs.wrap ? "关闭自动换行" : "自动换行"} active={prefs.wrap} onClick={prefs.toggleWrap}>
            <WrapText className="size-3.5" />
          </ToolButton>
        </div>
        {detail.body ? (
          <p className="mt-1 whitespace-pre-wrap text-[12px] leading-5 text-muted">{detail.body}</p>
        ) : null}
        <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[11px] text-faint">
          <span className="font-mono">{detail.hash.slice(0, 10)}</span>
          <span>{detail.author}</span>
          <span>{new Date(detail.date).toLocaleString()}</span>
          <span>{detail.total} 个文件</span>
          <DiffStat added={totals.added} deleted={totals.deleted} />
        </p>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {detail.files.map((file, index) => {
          const open = !collapsed.has(index);
          return (
            <section key={`${file.path}-${index}`} className="border-b border-line">
              <div className="sticky top-0 z-10 flex items-center gap-1.5 bg-sidebar px-2 py-1.5 text-[12px]">
                <button
                  type="button"
                  className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  aria-expanded={open}
                  onClick={() =>
                    setCollapsed((current) => {
                      const next = new Set(current);
                      if (next.has(index)) next.delete(index);
                      else next.add(index);
                      return next;
                    })
                  }
                >
                  {open ? (
                    <ChevronDown className="size-3 shrink-0 text-faint" />
                  ) : (
                    <ChevronRight className="size-3 shrink-0 text-faint" />
                  )}
                  <StatusBadge letter={statusLetter(file.status)} />
                  <span className="min-w-0 truncate" title={file.path}>
                    {file.path}
                  </span>
                </button>
                <DiffStat added={file.added} deleted={file.deleted} binary={file.kind === "binary"} />
                {file.after ? (
                  <ToolButton
                    title="打开文件"
                    onClick={() => openFile(`${repoRoot.replace(/[\\/]+$/, "")}\\${file.path.replace(/\//g, "\\")}`)}
                  >
                    <FileText className="size-3.5" />
                  </ToolButton>
                ) : null}
              </div>
              {open ? <DiffView diff={file} layout={prefs.layout} wrap={prefs.wrap} /> : null}
            </section>
          );
        })}
        {detail.total > detail.files.length ? (
          <p className="px-3 py-3 text-[12px] text-faint">只显示前 {detail.files.length} 个文件。</p>
        ) : null}
      </div>
    </div>
  );
}
