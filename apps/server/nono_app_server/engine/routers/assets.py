"""POST /asset、GET /asset/{asset_id} —— 图片资产的落盘与回读。

前端在发消息前先把图片传上来（``POST /asset``），拿到资产 ID 之后再把
ID 塞进 ``/chat`` 的附件里。这样会话事件流里留的只是一个引用，base64
既不过 ``/chat`` 的 JSON，也不进磁盘上的会话文件。

``GET /asset/{asset_id}`` 供界面回放历史消息时把图重新显示出来。

**路径为什么是 ``/asset`` 而不是 ``/assets``。** 打包后的前端由 sidecar 托管，
Vite 产物里的 JS/CSS chunk 就挂在 ``/assets``（见 ``nono_app_server.web_ui``）。
同名会把 chunk 请求吃掉，前端直接白屏。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from nono_agent.assets import MAX_ASSET_BYTES, get_asset_store
from nono_agent.assets.imageinfo import SUPPORTED_MEDIA_TYPES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["assets"])


class AssetSource(BaseModel):
    """资产引用 —— 消息内容块里 ``source`` 的形状。"""

    type: str = "asset"
    asset_id: str
    media_type: str
    path: str
    sha256: str
    bytes: int
    width: int | None = None
    height: int | None = None


class AssetResponse(BaseModel):
    """上传结果。"""

    asset_id: str
    media_type: str
    byte_size: int
    sha256: str
    width: int | None = None
    height: int | None = None
    relative_path: str
    source: AssetSource


@router.post("/asset")
async def upload_asset(file: UploadFile = File(...)) -> AssetResponse:
    """上传一张图片 —— 落进资产目录，返回资产引用。

    只认图片（按魔数判定，不信扩展名），超限直接拒绝。
    """
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="图片内容为空")
    if len(data) > MAX_ASSET_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"图片超过 {MAX_ASSET_BYTES // (1024 * 1024)}MB 上限",
        )
    store = get_asset_store()
    try:
        ref = store.save_bytes(
            data,
            source_kind="user-paste",
            file_name=file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ref.media_type not in SUPPORTED_MEDIA_TYPES:
        raise HTTPException(status_code=415, detail="不支持的图片格式")
    return AssetResponse(
        asset_id=ref.asset_id,
        media_type=ref.media_type,
        byte_size=ref.byte_size,
        sha256=ref.sha256,
        width=ref.width,
        height=ref.height,
        relative_path=ref.relative_path,
        source=AssetSource(**ref.to_source()),
    )


@router.get("/asset/{asset_id}")
async def read_asset(asset_id: str) -> FileResponse:
    """取回一张资产的原始字节 —— 界面回放历史消息时用它显示图片。"""
    store = get_asset_store()
    try:
        path = store.path_for(asset_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="图片不存在") from exc
    try:
        ref = store.ref_for(asset_id)
    except OSError:
        ref = None
    media_type = ref.media_type if ref is not None else "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})
