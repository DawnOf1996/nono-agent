"""Glob 工具 —— 工作区文件匹配。

能力：
- 递归 glob（``**`` 支持）
- 尊重 Ignore（.gitignore / .nonoignore）
- 结果排序、截断
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase

# 结果上限
_MAX_RESULTS = 500


class GlobTool(ToolBase):
    """按 glob 模式匹配工作区文件。"""

    _name = "glob"
    _description = (
        "Find files matching a glob pattern (supports '**' for recursive matching). "
        "Returns relative paths, sorted. Files ignored by .gitignore/.nonoignore are excluded."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern, e.g. '**/*.py' or 'src/**/*.ts'.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Directory to search in (relative to cwd or absolute). Default: cwd."
                ),
            },
        },
        "required": ["pattern"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        pattern = input_params.get("pattern", "")
        if not pattern:
            return error_result(self._name, "No glob pattern provided.")

        base_raw = input_params.get("path") or context.cwd
        try:
            base = Path(resolve_path(context, base_raw))
        except ValueError as exc:
            return error_result(self._name, str(exc))

        if not base.exists():
            return error_result(self._name, f"Directory not found: {base}")
        if not base.is_dir():
            return error_result(self._name, f"Not a directory: {base}")

        ignore = getattr(context, "ignore", None)
        matches: list[str] = []
        try:
            for p in base.glob(pattern):
                if not p.is_file():
                    continue
                rel = p.relative_to(base).as_posix()
                if ignore is not None and ignore.is_ignored(rel):
                    continue
                matches.append(rel)
        except (ValueError, OSError) as exc:
            return error_result(self._name, f"Invalid glob pattern: {exc}")

        matches.sort()
        truncated = len(matches) > _MAX_RESULTS
        shown = matches[:_MAX_RESULTS]

        if not shown:
            return ok_result(self._name, "No files matched.")

        result = "\n".join(shown)
        if truncated:
            result += f"\n... [truncated, {len(matches)} total matches]"
        return ok_result(self._name, result)
