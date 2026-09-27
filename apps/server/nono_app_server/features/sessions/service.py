"""产品会话列表和引擎 session 之间的映射、分叉与清理。"""

from __future__ import annotations

from pathlib import Path
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
    relocate_session_files,
    use_session_dir,
)
from nono_agent.settings.sources import patch_user_settings
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.engine.views import session_info
from nono_app_server.features.permissions.service import cancel_session_permissions


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


async def change_session_directory(raw: str, manager: SessionManager) -> dict[str, Any]:
    """改会话保存目录，并把已有会话文件从旧目录迁过去。"""
    cleaned = raw.strip()
    if not cleaned:
        raise ValueError("请填写保存目录")
    new = Path(cleaned).expanduser()
    try:
        new = new.resolve()
    except OSError as exc:
        raise ValueError("这个路径无效") from exc
    old = active_session_dir()
    async with manager.lock:
        writers = [
            engine.transcript_writer
            for engine in manager.sessions.values()
            if isinstance(engine.transcript_writer, LocalFileTranscriptWriter)
        ]
        for writer in writers:
            await writer._write_lock.acquire()
        try:
            moved = relocate_session_files(old, new)
            try:
                patch_user_settings({"session_dir": str(new)})
            except Exception:
                relocate_session_files(new, old)
                raise
            for writer in writers:
                writer.retarget(new)
            use_session_dir(new)
        finally:
            for writer in reversed(writers):
                writer._write_lock.release()
    return {"session_dir": str(new), "moved": moved}
