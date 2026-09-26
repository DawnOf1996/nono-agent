"""本机目录浏览：列表、预览读取、创建、用系统文件管理器打开。"""

from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

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


_INVALID_FOLDER_CHARS = frozenset('<>:"/\\|?*')
_MAX_MARKDOWN_CHARS = 2_000_000
_MAX_PREVIEW_BYTES = 1_000_000
_MAX_BINARY_BYTES = 12_000_000
_IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    ".svg": "image/svg+xml",
}


def _valid_folder_name(name: str) -> bool:
    if not name or name in {".", ".."}:
        return False
    if any(char in _INVALID_FOLDER_CHARS for char in name):
        return False
    if name.endswith((" ", ".")):
        return False
    return True


def _markdown_file_name(name: str) -> str:
    """只允许在所选目录里写一个 .md 文件，拒绝路径穿越。"""
    cleaned = name.strip()
    if not cleaned or cleaned in {".", ".."}:
        raise HTTPException(status_code=400, detail="请输入文件名")
    if any(char in _INVALID_FOLDER_CHARS for char in cleaned) or cleaned.endswith((" ", ".")):
        raise HTTPException(status_code=400, detail="文件名不能包含 \\ / : * ? \" < > |")
    suffix = Path(cleaned).suffix.lower()
    if suffix and suffix != ".md":
        raise HTTPException(status_code=400, detail="只能导出 .md 文件")
    if not suffix:
        cleaned = f"{cleaned}.md"
    return cleaned


def _home() -> Path:
    return Path.home()


def _windows_drives() -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            drives.append({"name": f"{letter}:", "path": str(root), "is_dir": True})
    return drives


def _resolve_dir(raw: str | None) -> Path:
    if not raw or raw.strip() in {"", "~"}:
        return _home()
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (_home() / path).resolve()
        else:
            path = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    return path


def _parent_path(path: Path) -> str | None:
    parent = path.parent
    if parent == path:
        return None
    return str(parent)


def _list_entries(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        children = list(path.iterdir())
    except OSError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                entries.append({"name": child.name, "path": str(child), "is_dir": True})
        except OSError:
            continue
    entries.sort(key=lambda item: item["name"].lower())
    return entries


def _within_root(root: Path, target: Path) -> bool:
    root_key = os.path.normcase(str(root))
    target_key = os.path.normcase(str(target))
    if target_key == root_key:
        return True
    separator = os.path.normcase(os.sep)
    prefix = root_key if root_key.endswith(separator) else root_key + separator
    return target_key.startswith(prefix)


def _workspace_root(raw: str) -> Path:
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail="缺少工作区路径")
    return _resolve_dir(raw)


def _resolve_under_root(root_raw: str, path_raw: str | None) -> tuple[Path, Path]:
    """把路径解析到工作区内部。空 path 表示工作区根目录。"""
    root = _workspace_root(root_raw)
    if path_raw is None or not path_raw.strip():
        return root, root
    try:
        target = Path(path_raw).expanduser()
        if not target.is_absolute():
            target = root / target
        target = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="路径无效") from exc
    if not _within_root(root, target):
        raise HTTPException(status_code=403, detail="路径超出工作区")
    if not target.exists():
        raise HTTPException(status_code=404, detail="找不到这个路径")
    return root, target


def _list_workspace_entries(root: Path, directory: Path) -> list[dict[str, Any]]:
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise HTTPException(status_code=403, detail="无法读取这个文件夹") from exc
    entries: list[dict[str, Any]] = []
    for child in children:
        try:
            resolved = child.resolve()
            is_dir = child.is_dir()
        except OSError:
            continue
        if not _within_root(root, resolved):
            continue
        entries.append({"name": child.name, "path": str(child), "is_dir": is_dir})
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
    return entries


def _decode_text(data: bytes) -> str | None:
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("gb18030")
    except UnicodeDecodeError:
        return None
    sample = text[:4000]
    if not sample:
        return text
    weird = 0
    for char in sample:
        code = ord(char)
        if char in "\n\r\t":
            continue
        if code < 32 or code == 0xFFFD:
            weird += 1
    if weird / len(sample) >= 0.02:
        return None
    return text


def _preview_payload(path: Path, kind: str, content: str | None, size: int) -> dict[str, Any]:
    return {
        "path": str(path),
        "name": path.name,
        "kind": kind,
        "content": content,
        "size": size,
        "truncated": False,
    }


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
    return {
        "path": str(current),
        "root": str(workspace),
        "entries": _list_workspace_entries(workspace, current),
    }


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
    text = _decode_text(data)
    if text is None:
        return _preview_payload(target, "binary", None, size)
    return _preview_payload(target, "text", text, size)


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
    return FileResponse(target, media_type="application/octet-stream", headers={"Cache-Control": "no-store"})


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
