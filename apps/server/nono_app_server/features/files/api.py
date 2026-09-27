"""文件服务 HTTP：目录浏览、预览、保存、上传与监听。"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import (
    FileResponse,
    StreamingResponse,
)
from pydantic import BaseModel

from nono_app_server.common.paths import (
    home as _home,
)
from nono_app_server.common.paths import (
    resolve_dir as _resolve_dir,
)
from nono_app_server.common.paths import (
    within_root as _within_root,
)
from nono_app_server.common.paths import (
    workspace_root as _workspace_root,
)
from nono_app_server.common.textdiff import decode_text
from nono_app_server.features.files.service import (
    _list_entries,
    _list_workspace_entries,
    _parent_path,
    _resolve_under_root,
    _search_names,
    _windows_drives,
)
from nono_app_server.features.files.util import (
    _IMAGE_TYPES,
    _MAX_BINARY_BYTES,
    _MAX_MARKDOWN_CHARS,
    _MAX_PREVIEW_BYTES,
    _MAX_SAVE_CHARS,
    _MAX_WATCH_DIRS,
    _UPLOAD_MAX_FILE_BYTES,
    _UPLOAD_MAX_FILES,
    _UPLOAD_MAX_TOTAL_BYTES,
    _WATCH_POLL_S,
    _content_hash,
    _directory_signature,
    _markdown_file_name,
    _preview_payload,
    _safe_upload_relative,
    _text_encoding,
    _valid_folder_name,
)

router = APIRouter()


class MkdirBody(BaseModel):
    parent: str
    name: str


class OpenPathBody(BaseModel):
    path: str


class WriteMarkdownBody(BaseModel):
    directory: str
    name: str
    content: str


class SaveFileBody(BaseModel):
    root: str
    path: str
    content: str
    base_hash: str | None = None


class WatchBody(BaseModel):
    root: str
    paths: list[str] = []


@router.get("/fs")
async def list_directory(path: str | None = Query(default=None)) -> dict[str, Any]:
    """列出目录下的子文件夹，供工作区选择器浏览。"""
    raw = (path or "").strip()
    if os.name == "nt" and raw in {"", "/", "\\"}:
        return {
            "path": "",
            "parent": None,
            "home": str(_home()),
            "entries": _windows_drives(),
        }
    current = _resolve_dir(path)
    return {
        "path": str(current),
        "parent": _parent_path(current),
        "home": str(_home()),
        "entries": _list_entries(current),
    }


@router.get("/fs/entries")
async def list_workspace_entries(
    root: str = Query(),
    path: str | None = Query(default=None),
) -> dict[str, Any]:
    """列出工作区内的文件和文件夹。path 必须落在 root 里面。"""
    workspace, current = _resolve_under_root(root, path)
    if not current.is_dir():
        raise HTTPException(status_code=400, detail="这不是文件夹")
    entries, truncated = _list_workspace_entries(workspace, current)
    return {
        "path": str(current),
        "root": str(workspace),
        "entries": entries,
        "truncated": truncated,
    }


@router.get("/fs/search")
async def search_workspace_entries(
    root: str = Query(),
    q: str = Query(default=""),
) -> dict[str, Any]:
    """按文件名（不含路径）做不区分大小写的子串搜索。不进入软链接目录，跳过依赖和构建目录。"""
    workspace = _workspace_root(root)
    return await asyncio.to_thread(_search_names, workspace, q)


@router.post("/fs/watch")
async def watch_workspace_directories(body: WatchBody, request: Request) -> StreamingResponse:
    """监听已经展开的目录。目录项变化时推一条 change，文件内容变化不推。"""
    watched: list[Path] = []
    for raw in body.paths[:_MAX_WATCH_DIRS]:
        try:
            _workspace, target = _resolve_under_root(body.root, raw)
        except HTTPException:
            continue
        if target.is_dir():
            watched.append(target)

    async def events() -> AsyncIterator[str]:
        signatures = {path: _directory_signature(path) for path in watched}
        yield ": ok\n\n"
        while True:
            if await request.is_disconnected():
                return
            await asyncio.sleep(_WATCH_POLL_S)
            for path in watched:
                signature = _directory_signature(path)
                if signature == signatures.get(path):
                    continue
                signatures[path] = signature
                payload = json.dumps({"dir": str(path)}, ensure_ascii=False)
                yield f"event: change\ndata: {payload}\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/fs/upload")
async def upload_workspace_files(
    root: str = Form(),
    directory: str = Form(),
    relpaths: list[str] = Form(default=[]),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, Any]:
    """把文件写进工作区里的某个目录。relpaths 是相对 directory 的路径，文件夹上传时带上子目录。"""
    workspace, target_dir = _resolve_under_root(root, directory)
    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="这不是文件夹")
    if not files:
        raise HTTPException(status_code=400, detail="没有要上传的文件")
    if len(files) > _UPLOAD_MAX_FILES:
        raise HTTPException(status_code=400, detail="一次上传的文件太多")
    if relpaths and len(relpaths) != len(files):
        raise HTTPException(status_code=400, detail="上传路径和文件数量不一致")
    written: list[str] = []
    total = 0
    try:
        for index, upload in enumerate(files):
            raw_name = relpaths[index] if relpaths else (upload.filename or "file")
            relative = _safe_upload_relative(raw_name)
            destination = (target_dir / relative).resolve()
            inside = _within_root(workspace, destination) and _within_root(target_dir, destination)
            if not inside:
                raise HTTPException(status_code=403, detail="上传路径超出工作区")
            if destination.exists() and destination.is_dir():
                raise HTTPException(status_code=409, detail=f"{relative.as_posix()} 已是文件夹")
            data = await upload.read()
            total += len(data)
            if len(data) > _UPLOAD_MAX_FILE_BYTES or total > _UPLOAD_MAX_TOTAL_BYTES:
                raise HTTPException(status_code=413, detail="上传内容过大")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            written.append(str(destination))
    finally:
        for upload in files:
            await upload.close()
    return {"written": written, "count": len(written)}


@router.get("/fs/file")
async def read_workspace_file(root: str = Query(), path: str = Query()) -> dict[str, Any]:
    """读取工作区内的文件，供预览。图片只返回类型，二进制和超大文件不回正文。"""
    _workspace, target = _resolve_under_root(root, path)
    if target.is_dir():
        raise HTTPException(status_code=400, detail="这不是文件")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=400, detail="无法读取这个文件") from exc
    suffix = target.suffix.lower()
    if suffix in _IMAGE_TYPES:
        kind = "too_large" if size > _MAX_BINARY_BYTES else "image"
        return _preview_payload(target, kind, None, size)
    if size > _MAX_PREVIEW_BYTES:
        return _preview_payload(target, "too_large", None, size)
    try:
        data = target.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="无法读取这个文件") from exc
    text = decode_text(data)
    if text is None:
        return _preview_payload(target, "binary", None, size)
    return _preview_payload(target, "text", text, size, _content_hash(data))


@router.put("/fs/file")
async def save_workspace_file(body: SaveFileBody) -> dict[str, Any]:
    """保存编辑后的文本文件。

    保持原文件的编码、BOM 与换行风格；带 base_hash 时，磁盘内容已被别处改过就拒绝覆盖。
    """
    if len(body.content) > _MAX_SAVE_CHARS:
        raise HTTPException(status_code=400, detail="内容过长，无法保存")
    _workspace, target = _resolve_under_root(body.root, body.path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="这不是文件")
    try:
        current = target.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="无法读取这个文件") from exc
    if body.base_hash and body.base_hash != _content_hash(current):
        raise HTTPException(status_code=409, detail="文件已在磁盘上被修改")
    encoding = _text_encoding(current) if current else "utf-8"
    if encoding is None:
        raise HTTPException(status_code=400, detail="不能以文本方式保存这个文件")
    content = body.content.replace("\r\n", "\n")
    if b"\r\n" in current:
        content = content.replace("\n", "\r\n")
    try:
        data = content.encode(encoding)
    except UnicodeEncodeError as exc:
        raise HTTPException(status_code=400, detail=f"内容无法用 {encoding} 编码保存") from exc
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            shutil.copymode(target, tmp_name)
        except OSError:
            pass
        os.replace(tmp_name, target)
    except OSError as exc:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise HTTPException(status_code=400, detail=f"保存失败：{exc}") from exc
    return {"path": str(target), "size": len(data), "hash": _content_hash(data)}


@router.get("/fs/raw")
async def raw_workspace_file(root: str = Query(), path: str = Query()) -> FileResponse:
    """把工作区内的图片原样返回，供预览区用 img 显示。"""
    _workspace, target = _resolve_under_root(root, path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="这不是文件")
    suffix = target.suffix.lower()
    media = _IMAGE_TYPES.get(suffix)
    if media is None:
        raise HTTPException(status_code=415, detail="不能预览这个文件")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=400, detail="无法读取这个文件") from exc
    if size > _MAX_BINARY_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，无法预览")
    return FileResponse(target, media_type=media, headers={"Cache-Control": "no-store"})


@router.get("/fs/bytes")
async def workspace_file_bytes(root: str = Query(), path: str = Query()) -> FileResponse:
    """返回工作区内文件的完整字节，供 PDF、HTML 等按字节预览。超限拒绝，不截断。"""
    _workspace, target = _resolve_under_root(root, path)
    if not target.is_file():
        raise HTTPException(status_code=400, detail="这不是文件")
    try:
        size = target.stat().st_size
    except OSError as exc:
        raise HTTPException(status_code=400, detail="无法读取这个文件") from exc
    if size > _MAX_BINARY_BYTES:
        raise HTTPException(status_code=413, detail="文件过大，无法预览")
    return FileResponse(
        target,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/fs/mkdir")
async def make_directory(body: MkdirBody) -> dict[str, Any]:
    name = body.name.strip()
    if not _valid_folder_name(name):
        raise HTTPException(status_code=400, detail="Invalid folder name")
    parent = _resolve_dir(body.parent)
    target = parent / name
    try:
        target.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="Folder already exists") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(target), "parent": str(parent), "name": name}


@router.post("/fs/write")
async def write_markdown(body: WriteMarkdownBody) -> dict[str, Any]:
    """把一段 Markdown 写到用户选中的目录。已存在则拒绝覆盖。"""
    if len(body.content) > _MAX_MARKDOWN_CHARS:
        raise HTTPException(status_code=400, detail="内容过长，无法导出")
    directory = _resolve_dir(body.directory)
    name = _markdown_file_name(body.name)
    target = (directory / name).resolve()
    if target.parent != directory:
        raise HTTPException(status_code=400, detail="Invalid file name")
    try:
        with target.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(body.content)
    except FileExistsError as exc:
        raise HTTPException(status_code=409, detail="文件已存在，请换一个文件名") from exc
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(target), "name": name}


@router.post("/fs/open")
async def open_directory(body: OpenPathBody) -> dict[str, Any]:
    """在系统文件管理器中打开目录。"""
    target = _resolve_dir(body.path)
    try:
        if os.name == "nt":
            os.startfile(target)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except OSError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"path": str(target), "opened": True}
