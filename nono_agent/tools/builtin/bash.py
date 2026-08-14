"""Bash / Shell 工具 —— 生产级命令执行。

能力：
- 超时（默认 120s，可配置）
- 工作目录（workdir 可选，默认引擎 cwd）
- 输出截断（stdout/stderr 合并，超长截断）
- abort 联动（AbortSignal 触发时 kill 子进程）
- 危险命令分类（BashClassifier 起步，与权限模式联动）
"""

from __future__ import annotations

import asyncio
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.permissions.bash_classify import BashClassifier, BashRisk
from nono_agent.tools.builtin._common import ok_result, resolve_path, truncate_text
from nono_agent.tools.protocol import ToolBase

# 默认输出上限（字符）
_MAX_OUTPUT_CHARS = 30_000
# 默认超时（秒）
_DEFAULT_TIMEOUT = 120


class BashTool(ToolBase):
    """执行 shell 命令并返回输出。"""

    _name = "bash"
    _description = (
        "Execute a shell command and return its output. "
        "Use this for running build/test commands, inspecting the filesystem, "
        "or manipulating files. Prefer dedicated tools (read_file, write_file, "
        "edit_file, glob, grep) when they exist. "
        "Commands with destructive side effects (rm -rf, mkfs, shutdown, sudo, "
        "etc.) are classified as dangerous and require elevated permission."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "workdir": {
                "type": "string",
                "description": "Working directory for the command (relative to cwd or absolute).",
            },
            "timeout": {
                "type": "integer",
                "description": f"Timeout in seconds (default {_DEFAULT_TIMEOUT}).",
            },
        },
        "required": ["command"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    def __init__(self, classifier: BashClassifier | None = None) -> None:
        self._classifier = classifier or BashClassifier()

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
            return ToolResult(
                tool_use_id="",
                content="Error: No command provided.",
                is_error=True,
                tool_name=self._name,
            )

        timeout = int(input_params.get("timeout") or _DEFAULT_TIMEOUT)
        workdir = resolve_path(context, input_params.get("workdir") or context.cwd)

        risk = self._classifier.classify(command)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=workdir,
            )
        except (OSError, ValueError) as exc:
            return ToolResult(
                tool_use_id="",
                content=f"Error: Failed to start command: {exc}",
                is_error=True,
                tool_name=self._name,
            )

        # abort 联动 —— 取消信号触发时 kill 子进程
        abort_task: asyncio.Task[None] | None = None
        if context.signal is not None:

            async def _watch_abort() -> None:
                await context.signal.wait()
                if proc.returncode is None:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass

            abort_task = asyncio.create_task(_watch_abort())

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.communicate()
            return ToolResult(
                tool_use_id="",
                content=f"Error: Command timed out after {timeout}s.",
                is_error=True,
                tool_name=self._name,
            )
        finally:
            if abort_task is not None:
                abort_task.cancel()

        output = (stdout or b"").decode("utf-8", errors="replace")
        output = truncate_text(output, _MAX_OUTPUT_CHARS)

        prefix = f"[{risk.value}] " if risk is not BashRisk.SAFE else ""
        exit_code = proc.returncode
        if exit_code != 0:
            return ToolResult(
                tool_use_id="",
                content=f"{prefix}Exit code {exit_code}\n{output}".rstrip(),
                is_error=True,
                tool_name=self._name,
            )
        return ok_result(self._name, f"{prefix}{output}".rstrip())
