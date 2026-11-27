"""任务板 HTTP：列出当前会话的命令和子代理，变化时用 SSE 推送，并强制停止仍在跑的任务。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from nono_agent.jobs.board import kill_job, list_jobs, subscribe

router = APIRouter()

_PING_S = 15.0


def _frame(jobs: list[dict[str, Any]]) -> str:
    payload = json.dumps({"jobs": jobs}, ensure_ascii=False)
    return f"event: jobs\ndata: {payload}\n\n"


@router.get("/jobs")
async def jobs(session_id: str = Query()) -> dict[str, Any]:
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="缺少会话")
    return {"jobs": list_jobs(session_id)}


@router.get("/jobs/stream")
async def stream_jobs(request: Request, session_id: str = Query()) -> StreamingResponse:
    """任务列表有变化才推一条。空闲时只发注释心跳，不再轮询。"""
    if not session_id.strip():
        raise HTTPException(status_code=400, detail="缺少会话")

    async def events() -> AsyncIterator[str]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue(maxsize=1)

        def listener(rows: list[dict[str, Any]]) -> None:
            def put() -> None:
                if queue.full():
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                try:
                    queue.put_nowait(rows)
                except asyncio.QueueFull:
                    pass

            try:
                loop.call_soon_threadsafe(put)
            except RuntimeError:
                return

        unsubscribe = subscribe(session_id, listener)
        try:
            yield _frame(list_jobs(session_id))
            while True:
                if await request.is_disconnected():
                    return
                try:
                    rows = await asyncio.wait_for(queue.get(), timeout=_PING_S)
                except TimeoutError:
                    yield ": ping\n\n"
                    continue
                yield _frame(rows)
        finally:
            unsubscribe()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/jobs/{job_id}/kill")
async def stop_job(job_id: str, session_id: str = Query()) -> dict[str, Any]:
    if not kill_job(session_id, job_id):
        raise HTTPException(status_code=404, detail="任务不在运行")
    return {"ok": True}
