"""本机目录浏览：列表、创建、用系统文件管理器打开。"""

from __future__ import annotations

import os
import string
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
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
