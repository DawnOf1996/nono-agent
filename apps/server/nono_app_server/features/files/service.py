"""工作区文件操作：列出、搜索、解析到工作区内部。"""

from __future__ import annotations

import string
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from nono_app_server.common.paths import (
    within_root as _within_root,
)
from nono_app_server.common.paths import (
    workspace_root as _workspace_root,
)
from nono_app_server.features.files.util import (
    _LIST_LIMIT,
    _SEARCH_MAX_MATCHES,
    _SEARCH_MAX_VISITED,
    _SEARCH_SKIP_DIRS,
)


def _windows_drives() -> list[dict[str, Any]]:
    drives: list[dict[str, Any]] = []
    for letter in string.ascii_uppercase:
        root = Path(f"{letter}:\\")
        if root.exists():
            drives.append({"name": f"{letter}:", "path": str(root), "is_dir": True})
    return drives


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


def _list_workspace_entries(root: Path, directory: Path) -> tuple[list[dict[str, Any]], bool]:
    """列出一层。软链接按目标类型展示，失效链接保留并标 broken，不因指向区外而隐藏。"""
    try:
        children = list(directory.iterdir())
    except OSError as exc:
        raise HTTPException(status_code=403, detail="无法读取这个文件夹") from exc
    entries: list[dict[str, Any]] = []
    truncated = len(children) > _LIST_LIMIT
    for child in children[:_LIST_LIMIT]:
        row = _workspace_row(root, child)
        if row is not None:
            entries.append(row)
    entries.sort(key=lambda item: (not item["is_dir"], item["name"].casefold()))
    return entries, truncated


def _workspace_row(root: Path, child: Path) -> dict[str, Any] | None:
    is_link = False
    try:
        is_link = child.is_symlink()
    except OSError:
        return None
    broken = False
    try:
        if is_link:
            child.stat()
        is_dir = child.is_dir()
    except OSError:
        if not is_link:
            return None
        is_dir = False
        broken = True
    if not is_link:
        try:
            resolved = child.resolve()
        except OSError:
            return None
        if not _within_root(root, resolved):
            return None
    return {
        "name": child.name,
        "path": str(child),
        "is_dir": is_dir and not broken,
        "is_symlink": is_link,
        "broken": broken,
        "hidden": child.name.startswith("."),
    }


def _search_names(root: Path, query: str) -> dict[str, Any]:
    needle = query.strip().casefold()
    if not needle:
        return {"matches": [], "truncated": False}
    matches: list[dict[str, Any]] = []
    visited = 0
    truncated = False

    def walk(directory: Path) -> None:
        nonlocal visited, truncated
        if truncated:
            return
        try:
            children = list(directory.iterdir())
        except OSError:
            return
        for child in children:
            if truncated:
                return
            visited += 1
            if visited > _SEARCH_MAX_VISITED:
                truncated = True
                return
            name = child.name
            try:
                is_link = child.is_symlink()
                target_is_dir = child.is_dir()
            except OSError:
                continue
            real_dir = target_is_dir and not is_link
            if real_dir and name.casefold() in _SEARCH_SKIP_DIRS:
                continue
            if needle in name.casefold():
                try:
                    relative = child.relative_to(root).as_posix()
                except ValueError:
                    continue
                matches.append({"path": relative, "is_dir": target_is_dir})
                if len(matches) >= _SEARCH_MAX_MATCHES:
                    truncated = True
                    return
            if real_dir:
                walk(child)

    walk(root)
    matches.sort(key=lambda item: (not item["is_dir"], item["path"].casefold()))
    return {"matches": matches, "truncated": truncated}
