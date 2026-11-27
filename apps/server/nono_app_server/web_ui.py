"""打包后的桌面 UI：sidecar 托管 Vite dist。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

ENV_WEB_DIST = "NONO_WEB_DIST"


def resolve_web_dist() -> Path | None:
    """定位前端产物目录。环境变量优先，其次是冻结 exe 旁的 ``web/``。"""
    raw = os.environ.get(ENV_WEB_DIST, "").strip()
    candidates: list[Path] = []
    if raw:
        candidates.append(Path(raw).expanduser())
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        meipass = Path(getattr(sys, "_MEIPASS", exe_dir))
        candidates.extend(
            [
                exe_dir / "web",
                exe_dir.parent / "web",
                meipass / "web",
            ]
        )
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if (resolved / "index.html").is_file():
            return resolved
    return None


def mount_web_ui(app: Any, dist: Path) -> None:
    """把 Vite dist 挂到 ``/`` 与 ``/assets``，且不覆盖已有 API 路由。"""
    from fastapi import HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    dist_root = dist.resolve()
    index = dist_root / "index.html"
    assets = dist_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="web-assets")

    @app.get("/")
    async def web_index() -> FileResponse:
        return FileResponse(index)

    @app.get("/{full_path:path}")
    async def web_public_file(full_path: str) -> FileResponse:
        # Vite 会把 public/ 原样拷到 dist 根，例如 logo.png。
        candidate = (dist_root / full_path).resolve()
        try:
            candidate.relative_to(dist_root)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="Not Found") from exc
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(candidate)
