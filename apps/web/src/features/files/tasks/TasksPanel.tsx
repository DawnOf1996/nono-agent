import { Loader2, Square } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { killJob, watchJobs, type WorkspaceJob } from "@/api/client";
import { usePanelStore } from "@/features/files/panel-store";
import { cn } from "@/lib/utils";

const STATUS_LABEL: Record<string, string> = {
  running: "运行中",
  completed: "已完成",
  failed: "失败",
  killed: "已停止",
};

function JobRow({ job, sessionId, nested }: { job: WorkspaceJob; sessionId: string; nested?: boolean }) {
  const [stopping, setStopping] = useState(false);
  return (
    <article className={cn("rounded-lg border border-line", nested && "ml-3 mt-1")}>
      <div className="flex items-center gap-1.5 px-2 py-1.5">
        <span
          className={cn(
            "shrink-0 rounded px-1 text-[10px] leading-4",
            job.kind === "agent" ? "bg-[#e8eefc] text-[#2a4ea3]" : "bg-card text-muted",
          )}
        >
          {job.kind === "agent" ? "子代理" : "命令"}
        </span>
        <span
          className={cn(
            "size-2 shrink-0 rounded-full",
            job.status === "running" && "bg-[#1d7a3a]",
            job.status === "completed" && "bg-[#9aa0ab]",
            job.status === "failed" && "bg-[#d94841]",
            job.status === "killed" && "bg-[#d94841]",
          )}
          title={STATUS_LABEL[job.status] ?? job.status}
        />
        <span className="min-w-0 flex-1 truncate text-[12.5px]" title={job.title}>
          {job.title}
        </span>
        <span className={cn("shrink-0 text-[11px]", job.status === "failed" ? "text-danger" : "text-faint")}>
          {STATUS_LABEL[job.status] ?? job.status}
          {job.exit_code !== null && job.status !== "running" ? ` · ${job.exit_code}` : ""}
        </span>
        {job.status === "running" ? (
          <button
            type="button"
            className="flex size-6 shrink-0 items-center justify-center rounded-md text-faint hover:bg-danger/10 hover:text-danger disabled:opacity-40"
            title="强制停止"
            aria-label="强制停止"
            disabled={stopping}
            onClick={() => {
              setStopping(true);
              void killJob(sessionId, job.id).finally(() => setStopping(false));
            }}
          >
            {stopping ? <Loader2 className="size-3.5 animate-spin" /> : <Square className="size-3 fill-current" />}
          </button>
        ) : null}
      </div>
      {job.output ? (
        <pre className="max-h-40 overflow-auto border-t border-line bg-sidebar px-2 py-1.5 font-mono text-[11px] leading-5 whitespace-pre-wrap text-ink/80">
          {job.output}
        </pre>
      ) : null}
    </article>
  );
}

/** 当前会话里的子代理和命令。新任务出现时自动打开这一页。 */
export function TasksPanel({ sessionId }: { sessionId: string | null }) {
  const [jobs, setJobs] = useState<WorkspaceJob[]>([]);
  const [error, setError] = useState<string | null>(null);
  const seen = useRef<Set<string> | null>(null);
  const showTasks = usePanelStore((state) => state.showTasks);
  const setTaskCount = usePanelStore((state) => state.setTaskCount);

  useEffect(() => {
    seen.current = null;
    if (!sessionId) {
      setJobs([]);
      setTaskCount(0);
      return;
    }
    const controller = new AbortController();
    let stopped = false;
    const currentSession = sessionId;

    function apply(next: WorkspaceJob[]) {
      if (stopped) return;
      setJobs(next);
      setError(null);
      const running = next.filter((job) => job.status === "running");
      setTaskCount(running.length);
      if (seen.current) {
        const fresh = running.some((job) => !seen.current?.has(job.id));
        if (fresh) showTasks();
      }
      seen.current = new Set(next.map((job) => job.id));
    }

    async function run() {
      while (!stopped) {
        try {
          for await (const next of watchJobs(currentSession, controller.signal)) {
            apply(next);
          }
        } catch (cause) {
          if (controller.signal.aborted || stopped) return;
          setError(cause instanceof Error ? cause.message : "无法读取任务");
        }
        if (stopped) return;
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
    }

    void run();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [sessionId, setTaskCount, showTasks]);

  if (!sessionId) {
    return <p className="px-4 py-6 text-[13px] leading-6 text-muted">开始对话后，子代理和正在执行的命令会列在这里。</p>;
  }
  if (error && jobs.length === 0) {
    return <p className="px-4 py-6 text-[13px] leading-6 text-danger">{error}</p>;
  }
  if (jobs.length === 0) {
    return (
      <p className="px-4 py-6 text-[13px] leading-6 text-muted">
        还没有任务。模型执行命令或派出子代理时，会在这里显示实时输出，也可以强制停止。
      </p>
    );
  }

  const byParent = new Map<string, WorkspaceJob[]>();
  for (const job of jobs) {
    if (!job.parent_id) continue;
    const list = byParent.get(job.parent_id) ?? [];
    list.push(job);
    byParent.set(job.parent_id, list);
  }
  const roots = jobs.filter((job) => !job.parent_id || !jobs.some((item) => item.id === job.parent_id));
  const live = jobs.filter((job) => job.status === "running");

  return (
    <div className="min-h-0 flex-1 overflow-auto px-2 py-2">
      {live.length ? (
        <section className="mb-3">
          <h3 className="px-1 pb-1 text-[11px] font-medium text-muted">实时预览</h3>
          <div className={cn("grid gap-2", live.length > 1 && "grid-cols-2")}>
            {live.map((job) => (
              <div key={job.id} className="min-w-0 rounded-lg border border-line">
                <p className="truncate border-b border-line px-2 py-1 text-[12px] font-medium" title={job.title}>
                  {job.kind === "agent" ? "子代理" : "命令"} · {job.title}
                </p>
                <pre className="max-h-28 overflow-auto px-2 py-1.5 font-mono text-[11px] leading-5 whitespace-pre-wrap text-ink/80">
                  {job.output.trim() ? job.output.slice(-800) : "等待输出"}
                </pre>
              </div>
            ))}
          </div>
        </section>
      ) : null}
      <h3 className="px-1 pb-1 text-[11px] font-medium text-muted">拓扑</h3>
      <div className="flex flex-col gap-2">
        {roots.map((job) => (
          <div key={job.id} className="border-l border-line pl-2">
            <JobRow job={job} sessionId={sessionId} />
            {(byParent.get(job.id) ?? []).map((child) => (
              <JobRow key={child.id} job={child} sessionId={sessionId} nested />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
