"""Read 工具 —— 生产级文件读取。

能力：
- 行号输出（offset 起始行、limit 行数）
- 大文件分段（offset/limit 控制，避免一次性读爆）
- 二进制检测（含 NUL 字节时拒绝或摘要）
- 编码：UTF-8 优先，失败则回退本机默认编码 / gb18030
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import (
    decode_text,
    encoding_note,
    error_result,
    is_binary,
    ok_result,
    resolve_path,
)
from nono_agent.tools.protocol import ToolBase

# 单次读取的最大字节数（防止内存爆炸）
_MAX_BYTES = 200_000


class ReadFileTool(ToolBase):
    """读取文件内容（可选行号与分段）。"""

    _name = "read_file"
    _description = (
        "Read a file from the workspace. "
        "Returns content with line numbers. "
        "For large files, use 'offset' (1-based start line) and 'limit' (max lines) "
        "to read a specific range. Binary files are detected and rejected."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path (relative to working directory or absolute).",
            },
            "offset": {
                "type": "integer",
                "description": "1-based line number to start reading from (default 1).",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to read (default: whole file, capped).",
            },
        },
        "required": ["path"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw_path = input_params.get("path", "")
        if not raw_path:
            return error_result(self._name, "No file path provided.")

        try:
            file_path = resolve_path(context, raw_path)
        except ValueError as exc:
            return error_result(self._name, str(exc))

        import os

        if not os.path.exists(file_path):
            return error_result(self._name, f"File not found: {file_path}")
        if os.path.isdir(file_path):
            return error_result(self._name, f"Path is a directory, not a file: {file_path}")

        try:
            with open(file_path, "rb") as f:
                data = f.read(_MAX_BYTES + 1)
        except OSError as exc:
            return error_result(self._name, f"Failed to read '{file_path}': {exc}")

        if is_binary(data):
            return error_result(
                self._name,
                f"File appears to be binary ({len(data)} bytes); refusing to display.",
            )

        text, encoding = decode_text(data)
        truncated = len(data) > _MAX_BYTES

        lines = text.splitlines()
        offset = max(1, int(input_params.get("offset") or 1))
        limit = input_params.get("limit")
        if limit is not None:
            limit = max(1, int(limit))

        # 行号格式化
        selected = lines[offset - 1 :]
        if limit is not None:
            selected = selected[:limit]

        if not selected:
            return ok_result(self._name, f"(empty range at offset {offset})")

        width = len(str(offset + len(selected)))
        numbered = []
        for i, line in enumerate(selected):
            numbered.append(f"{offset + i:>{width}}  {line}")

        result = "\n".join(numbered)
        note = encoding_note(encoding)
        if note:
            result = f"{note}\n{result}"
        if truncated or len(selected) < len(lines):
            result += f"\n... [showing lines {offset}-{offset + len(selected) - 1} of {len(lines)}]"

        return ok_result(self._name, result)
