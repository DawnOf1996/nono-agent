"""模型侧的作业控制工具：``job_output`` / ``job_kill``。

bash 工具把超时或显式 ``run_in_background`` 的命令登记为后台作业后，
模型需要两条腿才能跟进：读增量输出、终止不想要的进程。没有这两个工具，
「晋升」只是把问题从「命令被杀」换成「进程泄漏到没人管」。

两个工具与 bash **共享同一个 JobRegistry 实例**（经 ``ToolUseContext`` 注入），
否则模型读不到自己起的进程。
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.jobs.board import registry_for
from nono_agent.shell.jobs import STATUS_RUNNING, JobRegistry
from nono_agent.shell.spill import OutputDelta
from nono_agent.shell.streams import compose_output
from nono_agent.tools.builtin._common import error_result, ok_result
from nono_agent.tools.protocol import ToolBase


def _registry(context: Any) -> JobRegistry:
    """取作业注册表。与 bash 工具同源，优先用上下文注入的实例。"""
    registry: JobRegistry | None = getattr(context, "shell_jobs", None)
    if registry is not None:
        return registry
    return registry_for(getattr(context, "session_id", "") or None)


def _known_ids(registry: JobRegistry) -> str:
    ids = ", ".join(sorted(j.id for j in registry.list_ids())) or "(none)"
    return f"Known jobs in this session: {ids}"


class JobOutputTool(ToolBase):
    """读取后台作业的新输出。"""

    _name = "job_output"
    _description = (
        "Read newer output from a background job started by the bash tool. "
        "Returns only what has appeared since the last read, so it is safe to "
        "poll: call it again to pick up more. A finished job keeps reporting its "
        "status, so you can tell 'still running' from 'already exited'. "
        "For output that was too large to keep in memory, the result names a file "
        "holding the full text — read that with read_file."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "The job id returned by bash (e.g. 'job-1a2b3c4d5e6f').",
            },
            "from_offset": {
                "type": "integer",
                "description": (
                    "Read from this byte offset instead of continuing from the last "
                    "read. Omit to get only what is new since the previous call."
                ),
            },
        },
        "required": ["job_id"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        registry = _registry(context)
        job_id = str(input_params.get("job_id") or "").strip()
        if not job_id:
            return error_result(self._name, "job_id is required.")

        job = registry.get(job_id)
        if job is None:
            return error_result(
                self._name, f"Unknown job '{job_id}'. {_known_ids(registry)}"
            )

        offset = input_params.get("from_offset")
        delta = registry.read(
            job_id, int(offset) if isinstance(offset, int) else None
        )
        if delta is None:
            return error_result(self._name, f"Job '{job_id}' has no process attached.")
        stderr = registry.stderr_delta(job_id)

        text = compose_output(delta, stderr or _EMPTY_DELTA)
        # 状态放在最前面：模型先要知道「还能不能等」，再读输出。
        head = f"[job {job_id} status: {job.status}"
        if job.exit_code is not None:
            head += f", exit code: {job.exit_code}"
        head += "]"
        return ok_result(self._name, f"{head}\n{text}".rstrip())


class JobKillTool(ToolBase):
    """终止后台作业。"""

    _name = "job_kill"
    _description = (
        "Stop a background job started by the bash tool, killing the whole process "
        "tree. Use it when a command is no longer wanted — a runaway build, a "
        "dev server you are done with, a process waiting on input that will never "
        "come. It only affects jobs from this session."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "job_id": {
                "type": "string",
                "description": "The job id returned by bash.",
            },
            "reason": {
                "type": "string",
                "description": "Why you are stopping it (shown in the job list).",
            },
        },
        "required": ["job_id"],
    }
    _is_concurrent_safe = True
    # 终止进程是状态变更，因此仍需授权（server 模式的白名单见 apps/server）。
    _has_write_side_effects = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        registry = _registry(context)
        job_id = str(input_params.get("job_id") or "").strip()
        if not job_id:
            return error_result(self._name, "job_id is required.")

        job = registry.get(job_id)
        if job is None:
            return error_result(
                self._name, f"Unknown job '{job_id}'. {_known_ids(registry)}"
            )
        if job.status != STATUS_RUNNING:
            # 幂等：重复 kill 已结束的作业不该报错，模型重试时很常见
            return ok_result(self._name, f"Job '{job_id}' is already {job.status}.")

        killed = await registry.kill(
            job_id, str(input_params.get("reason") or "stopped by model")
        )
        if not killed:
            return ok_result(self._name, f"Job '{job_id}' is already {job.status}.")
        return ok_result(self._name, f"Stopped job {job_id}.")


# job_output 在 stderr 为空时的占位增量
_EMPTY_DELTA = OutputDelta(
    text="", next_offset=0, start_offset=0, lossy=False, spill_path=None, truncated=False
)


__all__ = ["JobKillTool", "JobOutputTool"]
