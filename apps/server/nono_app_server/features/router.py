"""组装 /app 产品路由。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI

from nono_agent.core.config import EngineConfig
from nono_agent.session.transcript import list_session_files
from nono_app_server.common.store import AppStore, default_db_path
from nono_app_server.config import SidecarSettings
from nono_app_server.features.catalog.api import router as catalog_router
from nono_app_server.features.changes.api import router as changes_router
from nono_app_server.features.changes.service import WorkspaceChangesService
from nono_app_server.features.files.api import router as filesystem_router
from nono_app_server.features.git.api import router as git_router
from nono_app_server.features.info.api import router as info_router
from nono_app_server.features.jobs.api import router as jobs_router
from nono_app_server.features.mcp.api import router as mcp_router
from nono_app_server.features.permissions.api import router as permissions_router
from nono_app_server.features.sessions.api import router as sessions_router
from nono_app_server.features.terminal.api import router as terminal_router
from nono_app_server.features.workspaces.api import router as workspaces_router


def mount_product_routes(
    app: FastAPI,
    settings: SidecarSettings,
    config: EngineConfig,
    *,
    db_path: Path | None = None,
    import_engine_index: bool = True,
    changes_root: Path | None = None,
) -> None:
    """把 /app/* 挂到引擎 FastAPI 实例上。"""
    store = AppStore(db_path or default_db_path())
    if import_engine_index and not store.has_imported_engine_index():
        store.import_engine_index(list_session_files())
    app.state.sidecar = settings
    app.state.engine_config = config
    app.state.app_store = store
    app.state.turn_observer = WorkspaceChangesService(changes_root)
    router = APIRouter(prefix="/app", tags=["app"])
    router.include_router(info_router)
    router.include_router(catalog_router)
    router.include_router(mcp_router)
    router.include_router(permissions_router)
    router.include_router(filesystem_router)
    router.include_router(git_router)
    router.include_router(jobs_router)
    router.include_router(changes_router)
    router.include_router(workspaces_router)
    router.include_router(sessions_router)
    router.include_router(terminal_router)
    app.include_router(router)
