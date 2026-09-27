"""终端 HTTP / WebSocket。"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel

from nono_app_server.features.terminal.service import (
    _MAX_SESSIONS,
    TerminalSession,
    _clamp,
    _cwd,
    _read_loop,
    _sessions,
    _shell,
    _spawn,
    available_shells,
)

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


@router.post("/terminal")
async def start_terminal(body: StartBody) -> dict[str, Any]:
    if len(_sessions) >= _MAX_SESSIONS:
        raise HTTPException(status_code=429, detail="打开的终端太多")
    _root, cwd = _cwd(body.root, body.cwd)
    spec = _shell(body.shell)
    cols = _clamp(body.cols, 2, 500)
    rows = _clamp(body.rows, 1, 200)
    try:
        proc = _spawn(spec, str(cwd), cols, rows)
    except OSError as exc:
        raise HTTPException(status_code=400, detail=f"无法启动终端：{exc}") from exc
    session = TerminalSession(id=uuid.uuid4().hex, shell=spec, cwd=str(cwd), proc=proc)
    _sessions[session.id] = session
    thread = threading.Thread(
        target=_read_loop,
        args=(session, asyncio.get_running_loop()),
        daemon=True,
    )
    thread.start()
    return {"id": session.id, "shell": spec.id, "label": spec.label, "cwd": str(cwd)}


@router.delete("/terminal/{session_id}")
async def stop_terminal(session_id: str) -> dict[str, Any]:
    session = _sessions.get(session_id)
    if session is not None:
        await asyncio.to_thread(session.kill)
    return {"ok": True}


@router.websocket("/terminal/{session_id}/ws")
async def terminal_socket(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    session = _sessions.get(session_id)
    if session is None or session.closed:
        await websocket.send_text(json.dumps({"type": "missing"}))
        await websocket.close(code=4404)
        return
    if session.backlog:
        await websocket.send_bytes(session.backlog.encode("utf-8"))
    queue = session.attach()

    async def outgoing() -> None:
        while True:
            item = await queue.get()
            if item is None:
                payload = json.dumps({"type": "exit", "code": session.exit_code})
                await websocket.send_text(payload)
                return
            await websocket.send_bytes(item.encode("utf-8"))

    async def incoming() -> None:
        while True:
            try:
                message = await websocket.receive()
            except WebSocketDisconnect:
                return
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
            kind = payload.get("type")
            if kind == "input":
                await session.write(str(payload.get("data") or ""))
            elif kind == "resize":
                rows = _clamp(int(payload.get("rows") or 24), 1, 200)
                cols = _clamp(int(payload.get("cols") or 80), 2, 500)
                await session.resize(rows, cols)

    outgoing_task = asyncio.create_task(outgoing())
    incoming_task = asyncio.create_task(incoming())
    try:
        done, _pending = await asyncio.wait(
            {outgoing_task, incoming_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in done:
            task.result()
    except Exception:
        pass
    finally:
        for task in (outgoing_task, incoming_task):
            task.cancel()
        session.detach(queue)
