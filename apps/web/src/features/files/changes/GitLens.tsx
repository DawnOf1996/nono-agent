import type { GitLogEntry, GitStatus, GitStatusEntry, GitWorktree } from "@nono/protocol";
import {
  ArrowDown,
  ArrowUp,
  Check,
  ChevronDown,
  ChevronRight,
  FileText,
  GitBranch,
  GitCommitHorizontal,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
  Undo2,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import {
  fetchGitLog,
  fetchGitStatus,
  gitCommit,
  gitDiscard,
  gitStage,
  gitUnstage,
  watchGitStatus,
} from "@/api/client";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Dropdown, DropdownItem } from "@/components/ui/dropdown";
import { DiffStat, StatusBadge } from "@/features/files/diff/DiffStat";
import { usePanelStore } from "@/features/files/panel-store";
import { formatRelativeTime } from "@/lib/format";
import { baseName, parentPath, sameWorkspace } from "@/lib/workspace";
import { cn } from "@/lib/utils";

const LOG_BATCH = 30;

type Confirm = { title: string; description: string; confirmLabel: string; run: () => Promise<void> };

function errorText(cause: unknown, fallback: string): string {
  return cause instanceof Error && cause.message ? cause.message : fallback;
}

function ago(iso: string): string {
  const text = formatRelativeTime(Date.parse(iso) / 1000);
  if (!text) return iso;
  return text === "刚刚" ? text : `${text}前`;
}

function refNames(refs: string): string[] {
  return [
    ...new Set(
      refs
        .split(",")
        .map((ref) => ref.trim())
        .filter(Boolean)
        .map((ref) => (ref.includes(" -> ") ? ref.slice(ref.indexOf(" -> ") + 4) : ref))
        .map((ref) => (ref.startsWith("tag: ") ? ref.slice(5) : ref)),
    ),
  ];
}

function IconButton({
  title,
  onClick,
  disabled,
  danger,
  children,
}: {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      className={cn(
        "flex size-6 shrink-0 items-center justify-center rounded-md text-faint disabled:opacity-40",
        danger ? "hover:bg-danger/10 hover:text-danger" : "hover:bg-active hover:text-ink",
      )}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
    >
      {children}
    </button>
  );
}

function Section({
  title,
  count,
  open,
  onToggle,
  actions,
  children,
}: {
  title: string;
  count: number;
  open: boolean;
  onToggle: () => void;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="mt-1">
      <div className="group flex h-7 items-center gap-1 px-2">
        <button
          type="button"
          className="flex min-w-0 flex-1 items-center gap-1 text-left text-[11px] font-medium text-muted"
          aria-expanded={open}
          onClick={onToggle}
        >
          {open ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
          <span className="truncate">{title}</span>
          <span className="rounded-full bg-card px-1.5 text-[10px] tabular-nums text-faint">{count}</span>
        </button>
        <div className="flex items-center opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
          {actions}
        </div>
      </div>
      {open ? children : null}
    </section>
  );
}

function EntryRow({
  entry,
  staged,
  selected,
  busy,
  onOpenDiff,
  onOpenFile,
  onStage,
  onUnstage,
  onDiscard,
}: {
  entry: GitStatusEntry;
  staged: boolean;
  selected: boolean;
  busy: boolean;
  onOpenDiff: () => void;
  onOpenFile: () => void;
  onStage: () => void;
  onUnstage: () => void;
  onDiscard: () => void;
}) {
  const letter = staged ? entry.index : entry.worktree;
  const stat = staged ? entry.staged_stat : entry.unstaged_stat;
  const dir = parentPath(entry.path);
  const deleted = letter === "D";
  return (
    <div
      role="button"
      tabIndex={0}
      title={entry.orig_path ? `${entry.orig_path} → ${entry.path}` : entry.path}
      className={cn(
        "group flex h-7 cursor-pointer items-center gap-1.5 rounded-md pl-6 pr-1.5 text-[12.5px]",
        selected ? "bg-active" : "hover:bg-hover",
      )}
      onClick={onOpenDiff}
      onKeyDown={(event) => {
        if (event.key === "Enter") onOpenDiff();
      }}
    >
      <span className={cn("min-w-0 shrink truncate", deleted && "text-muted line-through")}>{baseName(entry.path)}</span>
      {dir ? <span className="min-w-0 flex-1 truncate text-[11px] text-faint">{dir}</span> : <span className="flex-1" />}
      <div className="hidden items-center group-hover:flex group-focus-within:flex">
        {!deleted ? (
          <IconButton title="打开文件" onClick={onOpenFile}>
            <FileText className="size-3.5" />
          </IconButton>
        ) : null}
        {staged ? (
          <IconButton title="取消暂存" disabled={busy} onClick={onUnstage}>
            <Minus className="size-3.5" />
          </IconButton>
        ) : (
          <>
            <IconButton title="丢弃改动" danger disabled={busy} onClick={onDiscard}>
              <Undo2 className="size-3.5" />
            </IconButton>
            <IconButton title="暂存" disabled={busy} onClick={onStage}>
              <Plus className="size-3.5" />
            </IconButton>
          </>
        )}
      </div>
      {stat ? (
        <DiffStat added={stat.added} deleted={stat.deleted} binary={stat.binary} className="group-hover:hidden" />
      ) : null}
      <StatusBadge letter={entry.conflicted ? "!" : letter} title={entry.conflicted ? "冲突" : undefined} />
    </div>
  );
}

export function GitLens({ root, visible, onShowSession }: { root: string; visible: boolean; onShowSession: () => void }) {
  const [repo, setRepo] = useState<string | null>(null);
  const [status, setStatus] = useState<GitStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [worktrees, setWorktrees] = useState<GitWorktree[]>([]);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [stagedOpen, setStagedOpen] = useState(true);
  const [changesOpen, setChangesOpen] = useState(true);
  // 提交历史默认折叠，但照样预取一批：折叠标题上要能看到"有 n 条"，不然像没有历史。
  const [historyOpen, setHistoryOpen] = useState(false);
  const [log, setLog] = useState<GitLogEntry[]>([]);
  const [logEnded, setLogEnded] = useState(false);
  const [logLoading, setLogLoading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const tabs = usePanelStore((state) => state.tabs);
  const active = usePanelStore((state) => state.active);
  const setChangePreview = usePanelStore((state) => state.setChangePreview);
  const changePreview = usePanelStore((state) => state.changePreview);
  const openFile = usePanelStore((state) => state.openFile);
  const bumpRevision = usePanelStore((state) => state.bumpRevision);
  const setGitCount = usePanelStore((state) => state.setGitCount);
  const chosenByUser = useRef(false);
  const inFlight = useRef(false);
  const generation = useRef(0);

  useEffect(() => {
    generation.current += 1;
    chosenByUser.current = false;
    setRepo(null);
    setStatus(null);
    setWorktrees([]);
    setLog([]);
    setLogEnded(false);
  }, [root]);

  const refresh = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    const current = generation.current;
    try {
      const next = await fetchGitStatus(root, repo);
      if (current !== generation.current) return;
      setStatus(next);
      setError(null);
      setGitCount(next.entries.length);
    } catch (cause) {
      if (current === generation.current) setError(errorText(cause, "无法读取 Git 状态"));
    } finally {
      inFlight.current = false;
    }
  }, [root, repo, setGitCount]);

  const loadLog = useCallback(
    async (reset: boolean) => {
      if (!status?.is_repo) return;
      setLogLoading(true);
      try {
        const skip = reset ? 0 : log.length;
        const entries = await fetchGitLog(root, status.repo, LOG_BATCH, skip);
        setLog((prev) => (reset ? entries : [...prev, ...entries]));
        setLogEnded(entries.length < LOG_BATCH);
      } catch (cause) {
        setActionError(errorText(cause, "无法读取提交历史"));
      } finally {
        setLogLoading(false);
      }
    },
    [root, status?.is_repo, status?.repo, log.length],
  );

  // 状态与 worktree 列表都等服务端推：连接时先各给一帧，之后只在索引真的动了才来，
  // 断线由 watchGitStatus 自己重连，所以这里不必也不该再挂定时器。
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    const current = generation.current;
    void watchGitStatus(root, repo, controller.signal, {
      onStatus: (next) => {
        if (current !== generation.current) return;
        setStatus(next);
        setError(null);
        setGitCount(next.entries.length);
      },
      onWorktrees: (listed) => {
        if (current !== generation.current) return;
        setWorktrees(listed);
      },
    });
    return () => controller.abort();
  }, [visible, root, repo, setGitCount]);

  // 智能体常在 linked worktree 里干活而会话仍停在干净的主工作树：只有一个脏的 worktree 时自动切过去。
  useEffect(() => {
    if (chosenByUser.current || !status?.repo || worktrees.length < 2) return;
    const current = worktrees.find((item) => sameWorkspace(item.path, status.repo ?? ""));
    const dirty = worktrees.filter((item) => !sameWorkspace(item.path, status.repo ?? "") && item.changes > 0);
    if (current && current.changes === 0 && dirty.length === 1) {
      setRepo(dirty[0].path);
    }
  }, [status?.repo, worktrees]);

  useEffect(() => {
    if (!status?.is_repo || logLoading) return;
    if (log.length === 0 && !logEnded) void loadLog(true);
  }, [status?.is_repo, log.length, logEnded, logLoading, loadLog]);

  async function run(action: () => Promise<void>, after?: () => void) {
    setBusy(true);
    setActionError(null);
    try {
      await action();
      after?.();
      bumpRevision();
    } catch (cause) {
      setActionError(errorText(cause, "操作失败"));
    } finally {
      setBusy(false);
      void refresh();
    }
  }

  function chooseRepo(path: string) {
    chosenByUser.current = true;
    generation.current += 1;
    setMenuOpen(false);
    setRepo(path);
    setStatus(null);
    setLog([]);
    setLogEnded(false);
  }

  if (error && !status) {
    return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  }
  if (!status) {
    return (
      <div className="flex items-center gap-2 px-4 py-6 text-[13px] text-faint">
        <Loader2 className="size-3.5 animate-spin" />
        正在读取 Git 状态
      </div>
    );
  }
  if (!status.is_repo) {
    return (
      <div className="px-4 py-6 text-[13px] leading-6 text-muted">
        <p>当前工作区不是 Git 仓库。</p>
        <p className="mt-1 text-faint">智能体每轮改动的文件仍会在「对话」里按轮次列出。</p>
        <button type="button" className="mt-3 text-[13px] text-ink underline underline-offset-2" onClick={onShowSession}>
          查看对话改动
        </button>
      </div>
    );
  }

  const repoPath = status.repo;
  const staged = status.entries.filter((entry) => entry.staged);
  const unstaged = status.entries.filter((entry) => entry.unstaged);
  const checkouts: GitWorktree[] =
    worktrees.length > 0
      ? worktrees
      : status.repositories.map((path) => ({ path, branch: "", repo: path, main: true, locked: false, changes: 0 }));
  const activeDiff = tabs.find((tab) => tab.id === active);

  function selected(entry: GitStatusEntry, isStaged: boolean): boolean {
    const preview = changePreview?.target;
    if (preview?.source === "git") {
      return sameWorkspace(preview.absPath, entry.abs_path) && preview.staged === isStaged;
    }
    if (!activeDiff || activeDiff.kind !== "diff" || activeDiff.target.source !== "git") return false;
    return sameWorkspace(activeDiff.target.absPath, entry.abs_path) && activeDiff.target.staged === isStaged;
  }

  function showDiff(entry: GitStatusEntry, isStaged: boolean) {
    setChangePreview(
      {
        source: "git",
        root,
        repo: repoPath,
        path: entry.path,
        absPath: entry.abs_path,
        staged: isStaged,
        untracked: entry.untracked && !isStaged,
      },
      baseName(entry.path),
    );
  }

  function discardEntries(entries: GitStatusEntry[]) {
    const tracked = entries.filter((entry) => !entry.untracked).map((entry) => entry.path);
    const untracked = entries.filter((entry) => entry.untracked).map((entry) => entry.path);
    const single = entries.length === 1 ? entries[0].path : null;
    setConfirm({
      title: single ? "丢弃这个文件的改动？" : `丢弃 ${entries.length} 个文件的改动？`,
      description: `${single ?? "这些文件"}在工作区的改动会被丢弃${
        untracked.length ? "，未跟踪的文件会被删除" : ""
      }。此操作无法撤销。`,
      confirmLabel: "丢弃",
      run: () => run(() => gitDiscard(root, repoPath, tracked, untracked)),
    });
  }

  function commit() {
    const text = message.trim();
    if (!text) {
      setActionError("请填写提交说明");
      return;
    }
    const stageAll = staged.length === 0;
    void run(
      () => gitCommit(root, repoPath, text, stageAll),
      () => {
        setMessage("");
        setLog([]);
        setLogEnded(false);
      },
    );
  }

  const canCommit = staged.length > 0 || unstaged.length > 0;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex h-9 shrink-0 items-center gap-1.5 border-b border-line px-2">
        {checkouts.length > 1 ? (
          <Dropdown
            open={menuOpen}
            onOpenChange={setMenuOpen}
            panelClassName="min-w-60 max-w-[min(420px,90vw)]"
            trigger={
              <button
                type="button"
                className="flex h-7 min-w-0 items-center gap-1 rounded-md px-1.5 text-[12px] hover:bg-hover"
                aria-haspopup="menu"
                aria-expanded={menuOpen}
                title={repoPath ?? ""}
                onClick={() => setMenuOpen((value) => !value)}
              >
                <span className="truncate font-medium">{baseName(repoPath ?? "")}</span>
                <ChevronDown className="size-3 shrink-0 text-faint" />
              </button>
            }
          >
            {checkouts.map((item) => (
              <DropdownItem
                key={item.path}
                selected={sameWorkspace(item.path, repoPath ?? "")}
                className="items-center text-[12px]"
                title={item.path}
                onClick={() => chooseRepo(item.path)}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{baseName(item.path)}</span>
                  <span className="block truncate text-[11px] text-faint">
                    {item.branch || "仓库"}
                    {item.main ? "" : " · worktree"}
                  </span>
                </span>
                {item.changes > 0 ? (
                  <span className="rounded-full bg-card px-1.5 text-[10px] tabular-nums text-muted">{item.changes}</span>
                ) : null}
              </DropdownItem>
            ))}
          </Dropdown>
        ) : null}
        <span className="flex min-w-0 items-center gap-1 text-[12px] text-muted" title={status.upstream ?? undefined}>
          <GitBranch className="size-3.5 shrink-0" />
          <span className="truncate">{status.branch ?? `分离于 ${status.head ?? "?"}`}</span>
        </span>
        {status.ahead ? (
          <span className="flex items-center text-[11px] text-faint" title="领先远端的提交">
            <ArrowUp className="size-3" />
            {status.ahead}
          </span>
        ) : null}
        {status.behind ? (
          <span className="flex items-center text-[11px] text-faint" title="落后远端的提交">
            <ArrowDown className="size-3" />
            {status.behind}
          </span>
        ) : null}
        <span className="flex-1" />
        <IconButton title="刷新" onClick={() => void refresh()}>
          <RefreshCw className="size-3.5" />
        </IconButton>
      </div>

      <div className="min-h-0 flex-1 overflow-auto px-1.5 pb-3">
        <div className="px-1 pt-2">
          <div className="rounded-lg border border-line focus-within:border-line-strong">
            <textarea
              value={message}
              rows={2}
              placeholder={`提交说明（Ctrl+Enter 提交到 ${status.branch ?? "HEAD"}）`}
              className="block w-full resize-none rounded-lg bg-transparent px-2.5 py-2 text-[12.5px] leading-5 outline-none placeholder:text-faint"
              onChange={(event) => setMessage(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
                  event.preventDefault();
                  commit();
                }
              }}
            />
          </div>
          <button
            type="button"
            disabled={busy || !canCommit || !message.trim()}
            className="mt-1.5 flex h-8 w-full items-center justify-center gap-1.5 rounded-lg bg-accent text-[12.5px] font-medium text-on-accent hover:bg-accent-hover disabled:opacity-40"
            onClick={commit}
          >
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            {staged.length > 0 ? `提交 ${staged.length} 个文件` : "暂存全部并提交"}
          </button>
          {actionError ? <p className="mt-1.5 text-[12px] leading-5 text-danger">{actionError}</p> : null}
        </div>

        {staged.length > 0 ? (
          <Section
            title="暂存的更改"
            count={staged.length}
            open={stagedOpen}
            onToggle={() => setStagedOpen((value) => !value)}
            actions={
              <IconButton title="全部取消暂存" disabled={busy} onClick={() => void run(() => gitUnstage(root, repoPath, null))}>
                <Minus className="size-3.5" />
              </IconButton>
            }
          >
            {staged.map((entry) => (
              <EntryRow
                key={`s:${entry.path}`}
                entry={entry}
                staged
                busy={busy}
                selected={selected(entry, true)}
                onOpenDiff={() => showDiff(entry, true)}
                onOpenFile={() => openFile(entry.abs_path)}
                onStage={() => undefined}
                onUnstage={() => void run(() => gitUnstage(root, repoPath, [entry.path]))}
                onDiscard={() => undefined}
              />
            ))}
          </Section>
        ) : null}

        <Section
          title="更改"
          count={unstaged.length}
          open={changesOpen}
          onToggle={() => setChangesOpen((value) => !value)}
          actions={
            unstaged.length ? (
              <>
                <IconButton title="全部丢弃" danger disabled={busy} onClick={() => discardEntries(unstaged)}>
                  <Undo2 className="size-3.5" />
                </IconButton>
                <IconButton title="全部暂存" disabled={busy} onClick={() => void run(() => gitStage(root, repoPath, null))}>
                  <Plus className="size-3.5" />
                </IconButton>
              </>
            ) : null
          }
        >
          {unstaged.length === 0 ? (
            <p className="py-1.5 pl-6 text-[12px] text-faint">工作区没有未暂存的改动</p>
          ) : (
            unstaged.map((entry) => (
              <EntryRow
                key={`u:${entry.path}`}
                entry={entry}
                staged={false}
                busy={busy}
                selected={selected(entry, false)}
                onOpenDiff={() => showDiff(entry, false)}
                onOpenFile={() => openFile(entry.abs_path)}
                onStage={() => void run(() => gitStage(root, repoPath, [entry.path]))}
                onUnstage={() => undefined}
                onDiscard={() => discardEntries([entry])}
              />
            ))
          )}
          {status.truncated ? (
            <p className="py-1.5 pl-6 text-[12px] text-faint">改动太多，只列出前 {status.entries.length} 个。</p>
          ) : null}
        </Section>

        <Section title="提交历史" count={log.length} open={historyOpen} onToggle={() => setHistoryOpen((v) => !v)}>
          {log.map((entry) => {
            const refs = refNames(entry.refs);
            return (
              <button
                key={entry.hash_full}
                type="button"
                title={`${entry.hash} ${entry.subject}`}
                className="flex w-full min-w-0 items-start gap-1.5 rounded-md py-1 pl-6 pr-1.5 text-left hover:bg-hover"
                onClick={() =>
                  setChangePreview(
                    { source: "commit", root, repo: repoPath, hash: entry.hash_full, subject: entry.subject },
                    entry.subject || entry.hash,
                  )
                }
              >
                <GitCommitHorizontal className="mt-0.5 size-3.5 shrink-0 text-faint" />
                <span className="min-w-0 flex-1">
                  <span className="flex min-w-0 items-center gap-1">
                    <span className="truncate text-[12.5px]">{entry.subject}</span>
                    {refs.slice(0, 2).map((ref) => (
                      <span key={ref} className="shrink-0 rounded bg-card px-1 text-[10px] text-muted">
                        {ref}
                      </span>
                    ))}
                  </span>
                  <span className="block truncate text-[11px] text-faint">
                    {entry.author} · {ago(entry.date)} · {entry.hash}
                  </span>
                </span>
              </button>
            );
          })}
          {logLoading ? (
            <p className="flex items-center gap-2 py-1.5 pl-6 text-[12px] text-faint">
              <Loader2 className="size-3 animate-spin" />
              正在读取
            </p>
          ) : !logEnded && log.length > 0 ? (
            <button type="button" className="py-1.5 pl-6 text-[12px] text-muted hover:text-ink" onClick={() => void loadLog(false)}>
              加载更多
            </button>
          ) : log.length === 0 ? (
            <p className="py-1.5 pl-6 text-[12px] text-faint">还没有提交</p>
          ) : null}
        </Section>
      </div>
      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ""}
        description={confirm?.description ?? ""}
        confirmLabel={confirm?.confirmLabel}
        danger
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
