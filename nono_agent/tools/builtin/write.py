"""Write 工具 —— 原子写入文件。

能力：
- 原子写（先写临时文件再 rename，避免半写状态）
- 自动创建父目录
- 路径沙箱校验（注入 sandbox 时）
- 写前/写后的整文件差异作为 UI 附件返回（对话里的 diff 卡片靠它渲染真实行号）
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import (
    error_result,
    file_diff_ui,
    ok_result,
    read_baseline,
    resolve_path,
)
from nono_agent.tools.protocol import ToolBase


# 覆盖写时附带的差异上限。这段 diff 是给对话卡片看的：模型自己刚写完这份内容，
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
        if not isinstance(content, str):
            return error_result(self._name, "'content' must be a string.")

        try:
            file_path = resolve_path(context, raw_path)
        except ValueError as exc:
            return error_result(self._name, str(exc))

        target = Path(file_path)
        # 写之前先把现场留下来：对话里的 diff 卡片要的是"整个文件变了哪几行"，
        # 拿写前正文跟写后正文比才有真实行号；基线取不到就照常写，只是卡片没有差异。
        existed = target.exists()
        before = read_baseline(file_path) if existed else None
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

        # 结果文本回到一句话：差异走 ui 附件，不占模型的 token。
        result = ok_result(self._name, f"Successfully wrote {len(content)} bytes to {file_path}")
        if existed and before is not None:
            result.ui = file_diff_ui(file_path, before, content)
        elif not existed:
            result.ui = file_diff_ui(file_path, None, content)
        return result
