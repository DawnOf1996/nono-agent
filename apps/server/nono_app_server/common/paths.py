"""工作区路径：把用户传入的路径解析成目录，并判断目标是否落在根目录里面。

文件、Git、终端都用同一套规则，避免各功能各自放行越界路径。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException


def home() -> Path:
    return Path.home()


def within_root(root: Path, target: Path) -> bool:
    root_key = os.path.normcase(str(root))
    target_key = os.path.normcase(str(target))
    if target_key == root_key:
        return True
    separator = os.path.normcase(os.sep)
    prefix = root_key if root_key.endswith(separator) else root_key + separator
    return target_key.startswith(prefix)


def resolve_dir(raw: str | None) -> Path:
    if not raw or raw.strip() in {"", "~"}:
        return home()
    try:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = (home() / path).resolve()
        else:
            path = path.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid path: {exc}") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    return path


def workspace_root(raw: str) -> Path:
    if not raw or not raw.strip():
        raise HTTPException(status_code=400, detail="缺少工作区路径")
    return resolve_dir(raw)
