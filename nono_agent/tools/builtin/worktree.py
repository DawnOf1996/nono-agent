"""Worktree 进出工具 —— git worktree 隔离的可操作面（§2.8）。

Enter 在仓库新建/检出一个 worktree，返回其绝对路径；
Exit 移除指定 worktree。工具运行在引擎 cwd 对应的 git 仓库。
"""

from __future__ import annotations

import asyncio
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.protocol import ToolBase
from nono_agent.utils.process import hidden_kwargs


async def _run_git(cwd: str, *args: str) -> tuple[int, str]:
    """运行 git 命令，返回 (exit_code, output)。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **hidden_kwargs(),
        )
    except (OSError, ValueError) as exc:
        return -1, str(exc)
    stdout, _ = await proc.communicate()
    code = proc.returncode if proc.returncode is not None else -1
    return code, (stdout or b"").decode("utf-8", errors="replace")


class EnterWorktreeTool(ToolBase):
    """新建/检出一个 git worktree。"""

    _name = "enter_worktree"
    _description = (
        "Create (or check out) an isolated git worktree at the given path. "
        "Use this to work on a change without touching the main checkout. "
        "Returns the absolute worktree path for subsequent tool calls."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory path for the new worktree (relative to cwd or absolute).",
            },
            "branch": {
                "type": "string",
                "description": "Optional new branch name (creates with -b).",
            },
            "description": {
                "type": "string",
                "description": "Optional worktree description.",
            },
        },
        "required": ["path"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        path = input_params.get("path", "")
        if not path:
            return ToolResult(
                tool_use_id="",
                content="Error: 'path' is required.",
                is_error=True,
                tool_name=self._name,
            )

        args = ["worktree", "add"]
        if input_params.get("branch"):
            args += ["-b", input_params["branch"]]
        args.append(path)

        code, output = await _run_git(context.cwd, *args)
        if code != 0:
            return ToolResult(
                tool_use_id="",
                content=f"Error: git worktree add failed:\n{output.strip()}",
                is_error=True,
                tool_name=self._name,
            )
        return ToolResult(
            tool_use_id="",
            content=f"Entered worktree at {context.resolve_path(path)}.",
            tool_name=self._name,
        )


class ExitWorktreeTool(ToolBase):
    """移除一个 git worktree。"""

    _name = "exit_worktree"
    _description = (
        "Remove a git worktree created earlier. Use 'force' to remove even if "
        "it has uncommitted changes."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "The worktree directory path to remove.",
            },
            "force": {
                "type": "boolean",
                "description": "Force removal (default false).",
            },
        },
        "required": ["path"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        path = input_params.get("path", "")
        if not path:
            return ToolResult(
                tool_use_id="",
                content="Error: 'path' is required.",
                is_error=True,
                tool_name=self._name,
            )

        args = ["worktree", "remove"]
        if input_params.get("force"):
            args.append("--force")
        args.append(path)

        code, output = await _run_git(context.cwd, *args)
        if code != 0:
            return ToolResult(
                tool_use_id="",
                content=f"Error: git worktree remove failed:\n{output.strip()}",
                is_error=True,
                tool_name=self._name,
            )
        return ToolResult(
            tool_use_id="",
            content=f"Exited worktree at {path}.",
            tool_name=self._name,
        )
