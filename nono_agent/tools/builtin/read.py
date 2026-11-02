"""Read 工具 —— 生产级文件读取。

能力：
- 行号输出（offset 起始行、limit 行数）
- 大文件分段（offset/limit 控制，避免一次性读爆）
- 二进制检测（含 NUL 字节时拒绝或摘要）
- 编码：UTF-8 优先，失败则回退本机默认编码 / gb18030
- 资产路径：资产 ID 或 ``assets/...`` 相对路径（资产在数据目录里，不在工作区）
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from nono_agent.assets import ASSET_ID_RE, AssetStore, get_asset_store
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
        "to read a specific range. Binary files are detected and rejected. "
        "Also accepts an asset id or a path under the assets directory "
        "(assets/2026/01/01/xxx.md) -- that is where tool outputs that are too "
        "long for the conversation are stored."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "File path (relative to working directory or absolute), "
                    "or an asset id / assets-relative path."
                ),
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

    def __init__(self, store: AssetStore | None = None) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw_path = input_params.get("path", "")
        if not raw_path:
            return error_result(self._name, "No file path provided.")

        try:
            file_path = self._resolve(str(raw_path), context)
        except ValueError as exc:
            return error_result(self._name, str(exc))

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

    def _resolve(self, raw: str, context: Any) -> str:
        """解析入参路径，返回一个真实存在的文件路径。

        **工作区优先，资产兜底。** 工作区里可能有同名目录（``assets/``），
        那种情况下读的应该是工作区里的文件 —— 所以先按沙箱解析，文件确实存在
        就用它；只有工作区里没有（或路径直接逃出了沙箱）才去资产目录里找。

        Raises:
            ValueError: 路径非法、逃出沙箱、或文件不存在。
        """
        sandbox_error: str | None = None
        workspace_path: str | None = None
        try:
            workspace_path = resolve_path(context, raw)
        except ValueError as exc:
            # 逃出工作区 —— 可能是资产路径，留给下面那条路。
            sandbox_error = str(exc)

        if workspace_path is not None and os.path.exists(workspace_path):
            return workspace_path

        asset_path = self._resolve_asset(raw)
        if asset_path is not None:
            return asset_path

        if workspace_path is None:
            raise ValueError(sandbox_error or f"Invalid path: {raw}")
        raise ValueError(f"File not found: {workspace_path}")

    def _resolve_asset(self, raw: str) -> str | None:
        """把入参解析成资产目录里的文件。

        与 :meth:`nono_agent.tools.builtin.read_image.ReadImageTool._resolve`
        同一套规则：资产 ID、``assets/`` 相对路径，外加资产目录里的绝对路径。

        Returns:
            命中的文件路径；入参不像资产路径时返回 None（交给工作区那条路）。

        Raises:
            ValueError: 看起来是资产路径却找不到 —— 别让模型以为是自己路径写错了。
        """
        store = self._store or get_asset_store()
        candidate = raw.strip()
        if ASSET_ID_RE.match(candidate):
            try:
                return str(store.path_for(candidate))
            except (OSError, ValueError) as exc:
                raise ValueError(f"Asset not found: {candidate} ({exc})") from exc

        normalized = candidate.replace("\\", "/")
        if normalized.startswith("assets/"):
            path = store.resolve_relative(normalized)
            if path is None:
                raise ValueError(f"Asset not found: {candidate}")
            return str(path)

        # 资产目录里的绝对路径
        absolute = Path(candidate)
        if absolute.is_absolute() and store.contains(absolute):
            return str(absolute)

        return None
