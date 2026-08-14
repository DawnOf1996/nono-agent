"""LS / Tree 工具 —— 目录列举（尊重 Ignore）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase

# 单目录列举条目上限
_MAX_ENTRIES = 200


class LSTool(ToolBase):
    """列出目录内容（文件与子目录）。"""

    _name = "list_directory"
    _description = (
        "List the contents of a directory. Returns file and subdirectory names "
        "(directories marked with a trailing '/'), sorted. "
        "Entries ignored by .gitignore/.nonoignore are excluded."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Directory to list (relative to cwd or absolute). Default: cwd.",
            },
        },
        "required": [],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw = input_params.get("path") or context.cwd
        try:
            base = Path(resolve_path(context, raw))
        except ValueError as exc:
            return error_result(self._name, str(exc))

        if not base.is_dir():
            return error_result(self._name, f"Directory not found: {base}")

        ignore = getattr(context, "ignore", None)
        entries: list[str] = []
        try:
            for child in sorted(base.iterdir()):
                rel = child.relative_to(base).as_posix()
                if ignore is not None and ignore.is_ignored(rel, is_dir=child.is_dir()):
                    continue
                entries.append(child.name + ("/" if child.is_dir() else ""))
        except OSError as exc:
            return error_result(self._name, f"Failed to list directory: {exc}")

        entries = entries[:_MAX_ENTRIES]
        if not entries:
            return ok_result(self._name, "(empty directory)")
        return ok_result(self._name, "\n".join(entries))
