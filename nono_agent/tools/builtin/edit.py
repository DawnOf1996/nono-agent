"""Edit 工具 —— 精确字符串替换（对标编辑器 edit）。

能力：
- old_string 唯一性校验（0 次或多次匹配时明确报错）
- replace_all 支持多处替换
- 保留文件其余内容不变
- 路径沙箱校验
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase


class EditFileTool(ToolBase):
    """在文件中做精确字符串替换。"""

    _name = "edit_file"
    _description = (
        "Make a precise edit to a file by replacing an exact string. "
        "The 'old_string' must appear exactly once in the file (unless replace_all "
        "is true). This tool has write side effects and requires permission."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path (relative to working directory or absolute).",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to replace.",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement text.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all occurrences (default false; requires unique match).",
            },
        },
        "required": ["path", "old_string", "new_string"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw_path = input_params.get("path", "")
        old_string = input_params.get("old_string", "")
        new_string = input_params.get("new_string", "")
        replace_all = bool(input_params.get("replace_all", False))

        if not raw_path:
            return error_result(self._name, "No file path provided.")
        if old_string == "":
            return error_result(self._name, "old_string must not be empty.")

        try:
            file_path = resolve_path(context, raw_path)
        except ValueError as exc:
            return error_result(self._name, str(exc))

        try:
            with open(file_path, encoding="utf-8") as f:
                original = f.read()
        except FileNotFoundError:
            return error_result(self._name, f"File not found: {file_path}")
        except OSError as exc:
            return error_result(self._name, f"Failed to read '{file_path}': {exc}")

        count = original.count(old_string)
        if count == 0:
            return error_result(
                self._name,
                f"old_string not found in '{file_path}'.",
            )
        if not replace_all and count > 1:
            return error_result(
                self._name,
                f"old_string appears {count} times in '{file_path}'. "
                "Provide a more specific old_string or set replace_all=true.",
            )

        updated = original.replace(old_string, new_string)

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(updated)
        except OSError as exc:
            return error_result(self._name, f"Failed to write '{file_path}': {exc}")

        replaced = count if replace_all else 1
        return ok_result(
            self._name,
            f"Edited {file_path}: replaced {replaced} occurrence(s).",
        )
