"""终端 HTTP / WebSocket。

WebSocket 的首帧永远是服务端屏幕快照，之后是 PTY 原始字节；断线重连、切换工作区
再回来、慢客户端补发，走的都是同一条路径——客户端把服务端屏幕当作真源。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from nono_app_server.common.paths import workspace_key
from nono_app_server.features.terminal.service import (
    RESYNC,
    TerminalSession,
    _clamp,
    _cwd,
    _sessions,
    _shell,
    _spawn,
    available_shells,
    list_for_root,
    live_count,
    settings,
    start_reader,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class StartBody(BaseModel):
    root: str
    cwd: str | None = None
    shell: str | None = None
    cols: int = 80
    rows: int = 24


@router.get("/terminal/shells")
async def list_shells() -> dict[str, Any]:
    return {"shells": [{"id": spec.id, "label": spec.label} for spec in available_shells()]}


@router.get("/terminal")
async def list_terminals(root: str) -> dict[str, Any]:
    """列出该工作区仍然存活的终端。

    服务端是归属关系的真源：客户端切走再切回时以这个列表为准，本地存储只负责
    保留标题和布局。已退出的终端也在列表里（带退出码），由客户端决定怎么呈现。
    """
    return {"terminals": [session.info() for session in list_for_root(workspace_key(root))]}


@router.post("/terminal")
async def start_terminal(body: StartBody) -> dict[str, Any]:
    config = settings()
    root, cwd = _cwd(body.root, body.cwd)
    key = workspace_key(root)
    if live_count(key) >= config.max_per_workspace:
        raise HTTPException(status_code=429, detail="这个工作区的终端太多，先关掉几个")
    if len(_sessions) >= config.max_total:
        raise HTTPException(status_code=429, detail="打开的终端太多，先关掉几个")
    spec = _shell(body.shell)
    cols = _clamp(body.cols, 2, config.max_cols)
    rows = _clamp(body.rows, 1, config.max_rows)
    try:
        proc = _spawn(spec, str(cwd), cols, rows)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法启动终端：{exc}") from exc
    session = TerminalSession(
        id=uuid.uuid4().hex,
        root=key,
        shell=spec,
        cwd=str(cwd),
        proc=proc,
        cols=cols,
        rows=rows,
        title=spec.label,
    )
    _sessions[session.id] = session
    start_reader(session, asyncio.get_running_loop())
    return session.info()


@router.delete("/terminal/{session_id}")
async def stop_terminal(session_id: str) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if session is not None:
        # 终止进程会阻塞，放到工作线程；会话状态与注册表只在事件循环线程改。
        code = await asyncio.to_thread(session.terminate_process)
        session.settle(code)
        session._sessions_drop()  # noqa: SLF001 —— 同模块的注册表操作
    return {"ok": True}


@router.websocket("/terminal/{session_id}/ws")
async def terminal_socket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    session = _sessions.get(session_id)
    if session is None:
        await websocket.send_text(json.dumps({"type": "missing"}))
        await websocket.close(code=4404)
        return
    # 入队与首帧在同一个同步块里取，之后输出只会往后排，不会插到快照前面。
    follower, first = session.attach()

    async def outgoing() -> None:
        await websocket.send_text(json.dumps(first, ensure_ascii=False))
        while True:
            item = await follower.queue.get()
            if item is None:
                await websocket.send_text(json.dumps({"type": "exit", "code": session.exit_code}))
                return
            if item is RESYNC:
                fresh = session.resynced_frame(follower)
                if fresh is not None:
                    await websocket.send_text(json.dumps(fresh, ensure_ascii=False))
                continue
            await websocket.send_bytes(item)
            session.consume(follower, item)

    async def incoming() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            raw = message.get("text")
            if not raw:
                data = message.get("bytes")
                if data:
                    await session.write(data.decode("utf-8", "replace"))
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                await session.write(raw)
                continue
            if not isinstance(payload, dict):
                continue
            kind = payload.get("type")
            if kind == "input":
                await session.write(str(payload.get("data") or ""))
            elif kind == "resize":
                rows, cols = _dimensions(payload)
                await session.resize(cols, rows)

    tasks = [asyncio.create_task(outgoing()), asyncio.create_task(incoming())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                # 记下来而不是静默吞掉：PTY 已死、客户端发来坏 JSON 这两类故障
                # 过去都表现为一次没有原因的对端关闭。
                logger.warning("terminal ws task ended: %r", exc, exc_info=exc)
        for task in pending:
            task.cancel()
    finally:
        for task in tasks:
            task.cancel()
        session.detach(follower)


def _dimensions(payload: dict[str, Any]) -> tuple[int, int]:
    """解析 resize 帧，非法值退回默认值而不是让连接崩掉。"""
    config = settings()
    try:
        rows = int(payload.get("rows") or 24)
        cols = int(payload.get("cols") or 80)
    except (TypeError, ValueError):
        logger.warning("terminal resize frame with non-numeric size: %r", payload)
        return 24, 80
    return _clamp(rows, 1, config.max_rows), _clamp(cols, 2, config.max_cols)
