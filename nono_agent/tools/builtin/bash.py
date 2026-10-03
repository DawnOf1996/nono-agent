"""Bash / Shell 工具 —— 执行命令并把结果渲染给模型。

与 DSH（``@deepseek-ai/dsh-tool-pwsh``）的对应关系：

- **显式 argv，不走 ``shell=True``** —— 命令作为单个 argv 元素交给方言解析，
  没有中间 shell 的引号转义层（见 :mod:`nono_agent.shell.dialect`）
- **stdout / stderr 分离** —— 合并后模型无法区分命令的正常输出与诊断信息
- **超限落盘而非丢弃** —— 完整输出写文件并把路径回报给模型
- **非零退出不是错误** —— 以 ``[exit code: N]`` 标记上报，由模型判断后果；
  只有 spawn 失败、abort 这类基础设施故障才作为工具错误
- **超时晋升为后台** —— 命令继续跑，模型用 job_output / job_kill 跟进
- **能力缝** —— 本工具只描述执行（:class:`ShellSpec`）与渲染，进程机制由
  ``ctx.shell`` 执行器承担；沙箱部署换执行器即可，工具不用改
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from nono_agent.core.config import ShellConfig
from nono_agent.core.types import ToolResult
from nono_agent.jobs.board import registry_for
from nono_agent.jobs.context import jobs_parent, jobs_session
from nono_agent.permissions.bash_classify import BashClassifier, BashRisk
from nono_agent.session.transcript import active_session_dir
from nono_agent.shell.dialect import resolve_dialect
from nono_agent.shell.jobs import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_KILLED,
    JobRegistry,
)
from nono_agent.shell.local import LocalShellExecutor
from nono_agent.shell.streams import compose_output
from nono_agent.shell.types import (
    ON_EXPIRY_KILL,
    ON_EXPIRY_NONE,
    ShellExecutor,
    ShellOutcome,
    ShellProcess,
    ShellSpec,
)
from nono_agent.tools.builtin._common import ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase

logger = logging.getLogger(__name__)

# 工具描述里引用默认超时/上限，取自默认配置，避免与实现漂移。
_DEFAULTS = ShellConfig()


def _error(message: str) -> ToolResult:
    return ToolResult(tool_use_id="", content=f"Error: {message}", is_error=True, tool_name="bash")


def _spill_dir() -> Any:
    """落盘根目录：会话目录下的 ``spill/``。

    复用会话目录是仓库唯一的产物约定（``session/transcript.py``），因此
    测试里 ``conftest.py`` 对 ``DEFAULT_SESSION_DIR`` 的隔离自动覆盖到这里，
    不需要额外夹具。
    """
    return active_session_dir() / "spill"


class BashTool(ToolBase):
    """执行 shell 命令并返回输出。"""

    _name = "bash"
    _description = (
        "Execute a shell command and return its stdout/stderr. "
        "Use this for running build/test commands, inspecting the filesystem, "
        "or manipulating files. Prefer dedicated tools (read_file, write_file, "
        "edit_file, glob, grep) when they exist. "
        "Every call must carry a short `description` saying what the command "
        "does in the user's terms (5-10 words) — the conversation UI shows it "
        "next to the command, so it must read as prose, not as the command "
        "itself. "
        "A non-zero exit is reported as an `[exit code: N]` marker rather than an "
        "error: investigate it and decide whether to retry. "
        "Long output is truncated to its tail; when that happens the full output "
        "is written to a file whose path is reported — read it with read_file if "
        "you need the middle. "
        "A command that outruns its timeout is NOT killed: it moves to a background "
        "job and keeps running. Follow it with job_output and stop it with job_kill. "
        "Set `run_in_background: true` to get a job id straight away. "
        "Commands with destructive side effects (rm -rf, mkfs, shutdown, sudo, "
        "etc.) are classified as dangerous and require elevated permission."
    )
    # 方言相关的补充说明。按方言拼进描述里 —— 模型的命令语法必须匹配实际
    # 执行它的 shell，否则它会照着训练时的 POSIX/cmd 习惯写命令然后撞语法错误。
    _PS_NOTE = (
        " The shell is PowerShell, not cmd: use `Get-ChildItem` rather than `dir`, "
        "`$env:VAR` rather than `%VAR%`, and prefix a quoted executable path with "
        "the call operator — `& \"C:\\path\\tool.exe\" -c \"...\"`, not "
        "`\"C:\\path\\tool.exe\" -c`."
    )
    _DIALECT_NOTES = {
        "powershell": _PS_NOTE,
        "pwsh": _PS_NOTE,
        "cmd": (
            " The shell is cmd.exe: use `dir` rather than `Get-ChildItem` and "
            "`%VAR%` rather than `$env:VAR`."
        ),
    }
    _input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute (see the dialect note).",
            },
            "description": {
                "type": "string",
                "description": (
                    "Clear, concise description of what this command does, in 5-10 words, "
                    'shown in the UI next to the command. Examples: "ls" -> "List files in '
                    'the current directory"; "pytest -q" -> "Run the test suite quietly".'
                ),
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for the command (relative to cwd or absolute).",
            },
            "timeout": {
                "type": "integer",
                "description": (
                    f"Timeout in seconds (default {int(_DEFAULTS.timeout_s)}, "
                    f"max {int(_DEFAULTS.max_timeout_s)}). On expiry the command is moved "
                    "to a background job and keeps running."
                ),
            },
            "run_in_background": {
                "type": "boolean",
                "description": (
                    "Return a job id immediately without waiting. Read newer output with "
                    "job_output, stop the command with job_kill. Use this for long builds, "
                    "servers, and anything you expect to outlive one tool call."
                ),
            },
        },
        "required": ["command", "description"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    def __init__(
        self,
        classifier: BashClassifier | None = None,
        *,
        config: ShellConfig | None = None,
    ) -> None:
        self._classifier = classifier or BashClassifier()
        self._config = config or ShellConfig()

    def bind_shell_config(self, config: ShellConfig) -> None:
        """运行期下发配置。对齐 ``AgentTool.bind_base_config`` 的既有做法。"""
        self._config = config

    @property
    def description(self) -> str:
        """基础描述 + 当前方言的语法提示。"""
        return self._description + self._DIALECT_NOTES.get(
            resolve_dialect(self._config.dialect).name, ""
        )

    @property
    def shell_config(self) -> ShellConfig:
        return self._config

    def should_prompt_for_permission(
        self,
        input_params: dict[str, Any],
        context: Any,
    ) -> bool:
        """Bash 默认需要授权；纯读命令可免授权。"""
        risk = self._classifier.classify(input_params.get("command", ""))
        return risk in (BashRisk.WRITE, BashRisk.DANGEROUS)

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        command = (input_params.get("command") or "").strip()
        if not command:
            return _error("No command provided.")

        cfg = self._config
        timeout = cfg.clamp_timeout(_as_float(input_params.get("timeout")))
        workdir = resolve_path(context, input_params.get("workdir") or context.cwd)
        dialect = resolve_dialect(cfg.dialect)
        risk = self._classifier.classify(command)

        session_id = jobs_session.get() or getattr(context, "session_id", "") or ""
        registry = _registry(context, session_id)
        executor = _executor(context)
        job = registry.start(kind="command", title=command[:160],
                             parent_id=jobs_parent.get() or None)

        # 晋升模式：执行器不设 deadline，由本层限时等待；超时不杀而是转后台。
        # 关掉晋升即退回「超时即终止」。
        promote = cfg.promote_on_timeout
        spec = ShellSpec(
            command=command,
            dialect=dialect,
            workdir=Path(workdir),
            timeout_s=timeout,
            on_expiry=ON_EXPIRY_NONE if promote else ON_EXPIRY_KILL,
            env=dict(cfg.env_overrides),
            spill_dir=_spill_dir() / (session_id or "no-session") / job.id,
            max_output_bytes=cfg.max_output_bytes,
            max_spill_bytes=cfg.max_spill_bytes,
            merge_streams=cfg.merge_streams,
        )

        handle = await executor.execute(
            spec, getattr(context, "signal", None), on_output=registry.mark_dirty
        )
        registry.adopt(job.id, handle)

        if input_params.get("run_in_background") is True:
            registry.attach_abort_watch(job.id, getattr(context, "signal", None))
            _watch_completion(job.id, handle, registry)
            return ok_result(self._name, f"started background job {job.id}")

        if not promote:
            # on_expiry="kill"：超时由执行器负责终止
            outcome = await handle.wait()
            return await self._settle(job.id, registry, outcome, risk)

        try:
            outcome = await asyncio.wait_for(handle.wait(), timeout=timeout)
        except TimeoutError:
            # **不杀**。把 abort 监听的所有权交给注册表：execute 即将返回，
            # executor 的 task 随之结束，若无人继续监听，用户中断就杀不掉
            # 这个已经在后台的进程。
            registry.attach_abort_watch(job.id, getattr(context, "signal", None))
            _watch_completion(job.id, handle, registry)
            return self._render_promoted(registry, job, timeout, risk)
        registry.detach_abort_watch(job.id)
        return await self._settle(job.id, registry, outcome, risk)

    # ------------------------------------------------------------------
    # 渲染
    # ------------------------------------------------------------------

    async def _settle(
        self,
        job_id: str,
        registry: JobRegistry,
        outcome: ShellOutcome,
        risk: BashRisk,
    ) -> ToolResult:
        status = (
            STATUS_COMPLETED if outcome.status == "completed" else
            STATUS_FAILED if outcome.status == "failed" else STATUS_KILLED
        )
        await registry.finish(job_id, status=status, exit_code=outcome.exit_code)
        if outcome.timed_out:
            return self._render_timed_out(outcome, outcome.timeout_s, risk)
        if outcome.aborted or outcome.killed:
            return _error("Command aborted.")
        return self._render_foreground(outcome, risk)

    def _render_foreground(self, outcome: ShellOutcome, risk: BashRisk) -> ToolResult:
        prefix = f"[{risk.value}] " if risk is not BashRisk.SAFE else ""
        text = compose_output(outcome.stdout, outcome.stderr, exit_code=outcome.exit_code)
        return ok_result(self._name, f"{prefix}{text}".rstrip())

    def _render_timed_out(
        self, outcome: ShellOutcome, timeout: float, risk: BashRisk
    ) -> ToolResult:
        """超时仍然作为工具错误上报。

        超时与非零退出性质不同：非零退出是「命令跑完了、结果是这个」，
        由模型判断；超时是「命令没跑完」，拿不到结果。
        """
        prefix = f"[{risk.value}] " if risk is not BashRisk.SAFE else ""
        text = compose_output(
            outcome.stdout,
            outcome.stderr,
            exit_code=outcome.exit_code,
            timed_out=True,
            timeout_s=timeout,
        )
        return ToolResult(
            tool_use_id="",
            content=f"Error: {prefix}{text}".rstrip(),
            is_error=True,
            tool_name=self._name,
        )

    def _render_promoted(
        self, registry: JobRegistry, job: Any, timeout: float, risk: BashRisk
    ) -> ToolResult:
        """超时后转后台：命令继续跑，模型拿到作业 id 与读法。"""
        prefix = f"[{risk.value}] " if risk is not BashRisk.SAFE else ""
        delta = registry.read(job.id)
        head = delta.text if delta is not None else ""
        body = f"{head}\n" if head else ""
        text = (
            f"{prefix}{body}[still running after {timeout:g}s; moved to background job "
            f"{job.id}]\nThe command keeps running in the background. Read newer output "
            f"with job_output, stop it with job_kill."
        )
        return ok_result(self._name, text.rstrip())


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _registry(context: Any, session_id: str) -> JobRegistry:
    """取作业注册表。

    优先用引擎注入的实例（与 job_output / job_kill 工具同源）；未注入时
    （库用法直接调工具）退回到按会话分片的兼容门面。
    """
    registry: JobRegistry | None = getattr(context, "shell_jobs", None)
    return registry if registry is not None else registry_for(session_id)


def _executor(context: Any) -> ShellExecutor:
    """取 shell 执行器。未注入时自建本地执行器。"""
    executor: ShellExecutor | None = getattr(context, "shell", None)
    if executor is not None:
        return executor
    fallback: ShellExecutor = LocalShellExecutor()
    return fallback


def _watch_completion(job_id: str, handle: ShellProcess, registry: JobRegistry) -> None:
    """后台作业的收尾协程：等命令结束，把终态写回注册表。

    工具调用早已返回，这个 task 是作业在后台存活的唯一依靠，因此它自己
    必须吞掉所有异常 —— 否则一个未取回的 task 异常会污染事件循环日志。
    """
    async def _run() -> None:
        try:
            outcome = await handle.wait()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 —— 后台收尾失败不该外溢
            logger.debug("bash: background job %s failed to settle", job_id, exc_info=True)
            return
        status = {
            "completed": STATUS_COMPLETED,
            "failed": STATUS_FAILED,
        }.get(outcome.status, STATUS_KILLED)
        await registry.finish(job_id, status=status, exit_code=outcome.exit_code)
        registry.mark_dirty()

    asyncio.create_task(_run())


__all__ = ["BashTool"]
