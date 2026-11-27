"""ReadImage 工具 —— 把磁盘上的图片读进对话，让模型能"看见"它。

**为什么需要它。** 图片改成落盘之后，会话里只剩一个资产引用。模型想在后续
轮次重新看一眼某张图时（比如上下文压缩把图片挤掉了），需要一条能按路径或
资产 ID 把图片读回来的通道。``read_file`` 遇二进制直接拒绝，所以另开一个工具。

**图片怎么进上下文。** 工具结果里不放图片 —— OpenAI 的 tool role 只收字符串。
图片走 :attr:`ToolResult.images` 这条独立通道，由引擎作为一条带 image 块的
user 消息发出（三家 provider 都认这个形状）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nono_agent.assets import ASSET_ID_RE, AssetStore, get_asset_store
from nono_agent.assets.imageinfo import probe_size, sniff_media_type
from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result
from nono_agent.tools.protocol import ToolBase

logger = logging.getLogger(__name__)


class ReadImageTool(ToolBase):
    """读取图片文件并放进对话。"""

    _name = "read_image"
    _description = (
        "Read an image file and show it to the model. "
        "Accepts an asset id (asset_20260101-120000-000_ab12cd34ef56_0123abcd), "
        "a path under the assets directory (assets/2026/01/01/xxx.png), "
        "or a path in the workspace. "
        "Use this to look at an image that was shared earlier in the conversation, "
        "or at a screenshot/image file produced by another tool. "
        "The image is attached to the conversation, not described in text."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Asset id, assets-relative path, or workspace path of the image."
                ),
            },
        },
        "required": ["path"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, store: AssetStore | None = None) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw = str(input_params.get("path") or "").strip()
        if not raw:
            return error_result(self._name, "No image path provided.")

        store = self._store or get_asset_store()
        try:
            path = self._resolve(raw, context, store)
        except ValueError as exc:
            return error_result(self._name, str(exc))
        if path is None:
            return error_result(self._name, f"Image not found: {raw}")

        try:
            data = path.read_bytes()
        except OSError as exc:
            return error_result(self._name, f"Failed to read '{path}': {exc}")

        media_type = sniff_media_type(data)
        if media_type is None:
            return error_result(
                self._name,
                f"'{path}' is not a recognizable image file "
                "(supported: PNG / JPEG / GIF / WebP / BMP).",
            )

        # 资产目录里的文件直接引用（不复制）；工作区里的文件收编进资产目录，
        # 这样后面都走「资产引用」这一条路径。
        asset_id = store.asset_id_of(path) if store.contains(path) else None
        if asset_id is not None:
            ref = store.ref_for(asset_id)
        else:
            ref = None
        if ref is None:
            try:
                ref = store.save_bytes(
                    data,
                    source_kind="tool-output",
                    file_name=path.name,
                )
            except ValueError as exc:
                return error_result(self._name, str(exc))

        size = probe_size(data, media_type)
        dimensions = f"{size[0]}x{size[1]}" if size else "unknown"
        result = ok_result(
            self._name,
            f"Loaded image {ref.relative_path} "
            f"({media_type}, {ref.byte_size // 1024}KB, {dimensions}). "
            "It is now attached to the conversation.",
        )
        result.images = [{"type": "image", "source": ref.to_source()}]
        return result

    def _resolve(
        self,
        raw: str,
        context: Any,
        store: AssetStore,
    ) -> Path | None:
        """把入参解析成一个真实存在的图片文件路径。

        三条解析路径：资产 ID、资产目录相对路径、工作区路径（走沙箱）。
        解析不出来返回 ``None``；入参非法（越界等）抛 ``ValueError``。
        """
        # 1) 资产 ID
        if ASSET_ID_RE.match(raw):
            try:
                return store.path_for(raw)
            except (OSError, ValueError):
                return None

        # 2) 资产目录相对路径
        normalized = raw.replace("\\", "/")
        if normalized.startswith("assets/"):
            return store.resolve_relative(normalized)

        # 3) 工作区路径 —— 走沙箱，模型不能借此读到工作区外的文件
        try:
            path = Path(context.resolve_path(raw))
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if not path.is_file():
            return None
        return path


__all__ = ["ReadImageTool"]
