"""Grep 工具 —— 工作区内容搜索。

能力：
- 正则/字面量搜索
- include glob 过滤文件类型
- 尊重 Ignore
- 结果截断（文件数 / 每文件匹配数）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase

# 结果上限
_MAX_FILES = 100
_MAX_MATCHES_PER_FILE = 50
_MAX_TOTAL_MATCHES = 1000


class GrepTool(ToolBase):
    """在文件中搜索内容（正则或字面量）。"""

    _name = "grep"
    _description = (
        "Search file contents for a pattern (regular expression by default). "
        "Returns matching lines with file:line:content. "
        "Use 'include' to restrict to matching filenames (glob). "
        "Files ignored by .gitignore/.nonoignore are excluded."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": (
                    "Directory to search in (relative to cwd or absolute). Default: cwd."
                ),
            },
            "include": {
                "type": "string",
                "description": "Glob to filter files, e.g. '*.py' or '*.{js,ts}'.",
            },
            "ignore_case": {
                "type": "boolean",
                "description": "Case-insensitive search (default false).",
            },
        },
        "required": ["pattern"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        pattern = input_params.get("pattern", "")
        if not pattern:
            return error_result(self._name, "No search pattern provided.")

        try:
            flags = re.IGNORECASE if input_params.get("ignore_case") else 0
            regex = re.compile(pattern, flags)
        except re.error as exc:
            return error_result(self._name, f"Invalid regex: {exc}")

        base_raw = input_params.get("path") or context.cwd
        try:
            base = Path(resolve_path(context, base_raw))
        except ValueError as exc:
            return error_result(self._name, str(exc))

        if not base.is_dir():
            return error_result(self._name, f"Directory not found: {base}")

        include = input_params.get("include")
        include_re: re.Pattern[str] | None = None
        if include:
            include_re = re.compile(
                "^" + re.escape(include).replace(r"\*", ".*").replace(r"\?", ".") + "$"
            )

        ignore = getattr(context, "ignore", None)

        results: list[str] = []
        files_scanned = 0
        total_matches = 0

        try:
            for p in sorted(base.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(base).as_posix()
                if ignore is not None and ignore.is_ignored(rel):
                    continue
                if include_re is not None and not include_re.match(rel):
                    continue

                if files_scanned >= _MAX_FILES or total_matches >= _MAX_TOTAL_MATCHES:
                    break
                files_scanned += 1

                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue

                file_matches = 0
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if regex.search(line):
                        results.append(f"{rel}:{line_no}: {line}")
                        file_matches += 1
                        total_matches += 1
                        if (
                            file_matches >= _MAX_MATCHES_PER_FILE
                            or total_matches >= _MAX_TOTAL_MATCHES
                        ):
                            break
        except OSError as exc:
            return error_result(self._name, f"Search failed: {exc}")

        if not results:
            return ok_result(self._name, "No matches found.")

        out = "\n".join(results)
        if total_matches >= _MAX_TOTAL_MATCHES:
            out += f"\n... [truncated at {_MAX_TOTAL_MATCHES} matches]"
        return ok_result(self._name, out)
