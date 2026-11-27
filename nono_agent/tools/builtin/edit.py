"""Edit 工具 —— 精确字符串替换（对标编辑器 edit）。

能力：
- old_string 唯一性校验（0 次或多次匹配时明确报错）
- replace_all 支持多处替换
- 保留文件其余内容不变
- 非 UTF-8 源文件按 locale/gb18030 读入，写回 UTF-8
- 路径沙箱校验
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import (
    LOSSY_ENCODING,
    decode_text,
    error_result,
    file_diff_ui,
    is_binary,
    is_utf8_family,
    ok_result,
    resolve_path,
)
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
            with open(file_path, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            return error_result(self._name, f"File not found: {file_path}")
        except OSError as exc:
            return error_result(self._name, f"Failed to read '{file_path}': {exc}")

        if is_binary(data):
            return error_result(
                self._name,
                f"File appears to be binary; refusing to edit '{file_path}'.",
            )

        original, encoding = decode_text(data)
        if encoding == LOSSY_ENCODING:
            return error_result(
                self._name,
                f"Could not decode '{file_path}' as UTF-8 or the locale encoding; "
                "refusing to edit to avoid corrupting the file.",
            )

        # 换行归一：模型看到的正文来自 read_file 的 splitlines()（不带 \r），
        # 磁盘上却是 CRLF。拿原样去比，跨行的 old_string 在 Windows 上永远匹配不上 ——
        # 模型会把 read_file 的输出原样贴回来，于是每次多行编辑都报"找不到"。
        # 匹配统一在 LF 空间里做，写回时按文件原有约定还原；混排的文件不动它。
        crlf = b"\r\n" in data and data.count(b"\r\n") == data.count(b"\n")
        target = original.replace("\r\n", "\n") if crlf else original
        needle = old_string.replace("\r\n", "\n")
        replacement = new_string.replace("\r\n", "\n")

        count = target.count(needle)
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

        updated = target.replace(needle, replacement)
        if crlf:
            updated = updated.replace("\n", "\r\n")

        try:
            with open(file_path, "w", encoding="utf-8", newline="") as f:
                f.write(updated)
        except OSError as exc:
            return error_result(self._name, f"Failed to write '{file_path}': {exc}")

        replaced = count if replace_all else 1
        # 结果文本保持一句话：命中行号和整文件差异都走 ui 附件，不占模型 token。
        # 差异必须是**整文件**的：卡片要显示"第几行被改了"，
        # 拿入参里的 old_string/new_string 自己算只能得到片段内的相对编号。
        result = ok_result(
            self._name,
            f"Edited {file_path}: replaced {replaced} occurrence(s).",
        )
        result.ui = file_diff_ui(file_path, target, updated)
        if not is_utf8_family(encoding):
            result.content += f" Rewrote as UTF-8 (was {encoding})."
        return result
