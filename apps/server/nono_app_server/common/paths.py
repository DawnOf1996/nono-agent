"""工作区路径：把用户传入的路径解析成目录，并判断目标是否落在根目录里面。

文件、Git、终端都用同一套规则，避免各功能各自放行越界路径。
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException


def home() -> Path:
    return Path.home()


def system_root() -> str:
    """系统根目录，用来给"回到根目录"按钮一个可跳转的目标。

    Windows 没有单一根目录，磁盘列表才是它的根，所以返回空串走 list_directory 的盘符分支；
    Linux 与 macOS 一样，系统根就是 /，其他卷挂在 /Volumes、/mnt 下面。
    """
    return "" if os.name == "nt" else os.sep


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


def workspace_key(root: Path | str) -> str:
    """工作区的规范键：把资源（终端等）归属到工作区时用它分组。

    键只依赖解析后的路径与平台大小写规则，所以同一个工作区无论客户端传绝对
    路径、相对路径还是带 ``..`` 的路径，得到的键都相同。
    """
    return os.path.normcase(str(root))
