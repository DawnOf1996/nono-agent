"""产品会话列表和引擎 session 之间的映射、分叉与清理。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from nono_agent.session.events import (
    EVENT_ASSISTANT_MESSAGE,
    SessionEvent,
)
from nono_agent.session.transcript import (
    HEADER_SUFFIX,
    JSONL_SUFFIX,
    JSONL_ZSTD_SUFFIX,
    LocalFileTranscriptWriter,
    active_session_dir,
    existing_transcript_path,
)
from nono_app_server.common.store import AppStore
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.views import session_info
from nono_app_server.features.permissions.service import cancel_session_permissions

# 叉分会话标题写成「序号 - 家族名」，原会话不带序号 —— 有序号的就是叉分。
FORK_UNTITLED = "新会话"
FORK_LEGACY_SUFFIX = " 副本"  # 旧版叉分的标题后缀，读到就还原成家族名


def _purge_session_files(engine_session_id: str) -> None:
    directory = active_session_dir()
    candidates = [
        existing_transcript_path(directory, engine_session_id),
        directory / f"{engine_session_id}{HEADER_SUFFIX}",
        directory / f"{engine_session_id}{JSONL_SUFFIX}",
        directory / f"{engine_session_id}{JSONL_ZSTD_SUFFIX}",
    ]
    seen: set[Any] = set()
    for path in candidates:
        if path is None or path in seen:
            continue
        seen.add(path)
        if path.is_file():
            path.unlink()


async def _close_engine(manager: SessionManager, engine_session_id: str) -> None:
    cancel_session_permissions(engine_session_id)
    async with manager.lock:
        engine = manager.sessions.pop(engine_session_id, None)
    if engine is not None:
        try:
            await engine.close()
        except Exception:
            pass


def _enrich(row: dict[str, Any], manager: SessionManager) -> dict[str, Any]:
    engine = manager.sessions.get(row["engine_session_id"])
    if engine is None:
        return row
    info = session_info(engine)
    return {
        **row,
        "model": info.model or row["model"],
        "permission_mode": info.permission_mode or row["permission_mode"],
        "cwd": info.cwd or row["cwd"],
    }


def _fork_boundary(events: list[Any], requested: int | None) -> int:
    last = events[-1].seq if events else 0
    if requested is None:
        return last
    if requested < 0:
        raise HTTPException(status_code=400, detail="boundary_seq must be >= 0")
    return min(requested, last)


def _fork_root(store: AppStore, source: dict[str, Any]) -> dict[str, Any]:
    """顺 ``fork_root`` 回到谱系根会话。

    根被删了就把自己当根：剩下的叉分另起一个家族，不至于跟别人的编号串上。
    """
    row = source
    seen = {str(row["id"])}
    while True:
        parent = str(row.get("fork_root") or "").strip()
        if not parent or parent in seen:
            return row
        loaded = store.get_session(parent)
        if loaded is None:
            return row
        row = loaded
        seen.add(str(row["id"]))


def _fork_title(store: AppStore, source: dict[str, Any]) -> tuple[str, str]:
    """算出新叉分的 (标题, 谱系根 id)。

    家族名取根会话的标题，编号 = 同谱系已有叉分数 + 1。叉分「1 - 根名」，
    再叉分是「2 - 根名」，原会话永远不带序号 —— 一眼分得出原文和叉分。
    """
    root = _fork_root(store, source)
    root_id = str(root["id"])
    base = str(root["title"] or "").strip()
    if base.endswith(FORK_LEGACY_SUFFIX):
        base = base[: -len(FORK_LEGACY_SUFFIX)].strip()
    index = len(store.list_forks(root_id)) + 1
    return f"{index} - {base or FORK_UNTITLED}", root_id


async def _note_interrupted(engine_session_id: str, text: str) -> None:
    """把主会话里还没落盘的回合冻成一条 interrupted 助手消息，只写在侧边会话上。"""
    cleaned = text.strip()
    if not cleaned:
        return
    path = existing_transcript_path(active_session_dir(), engine_session_id)
    if path is None:
        return
    writer = LocalFileTranscriptWriter(path)
    await writer.append_events(
        [
            SessionEvent(
                type=EVENT_ASSISTANT_MESSAGE,
                data={
                    "content": [
                        {"type": "text", "text": f"{cleaned}\n\n[interrupted]"},
                    ]
                },
            )
        ]
    )
