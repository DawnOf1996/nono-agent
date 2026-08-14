"""Write 工具 —— 原子写入文件。

能力：
- 原子写（先写临时文件再 rename，避免半写状态）
- 自动创建父目录
- 路径沙箱校验（注入 sandbox 时）
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase


class WriteFileTool(ToolBase):
    """写入内容到文件（覆盖或新建，原子写）。"""

    _name = "write_file"
    _description = (
        "Write content to a file, creating it if it doesn't exist or overwriting "
        "it if it does. The write is atomic (temp file + rename). "
        "This tool has write side effects and requires permission in restricted modes."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path (relative to working directory or absolute).",
            },
            "content": {
                "type": "string",
                "description": "The full content to write to the file.",
            },
        },
        "required": ["path", "content"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw_path = input_params.get("path", "")
        content = input_params.get("content", "")

        if not raw_path:
            return error_result(self._name, "No file path provided.")

        try:
            file_path = resolve_path(context, raw_path)
        except ValueError as exc:
            return error_result(self._name, str(exc))

        target = Path(file_path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)

            # 原子写：同目录临时文件 + rename
            fd, tmp_name = tempfile.mkstemp(
                dir=str(target.parent), prefix=f".{target.name}.", suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_name, target)
            except BaseException:
                # 失败时清理临时文件
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
                raise
        except OSError as exc:
            return error_result(self._name, f"Failed to write '{file_path}': {exc}")

        return ok_result(
            self._name,
            f"Successfully wrote {len(content)} bytes to {file_path}",
        )
