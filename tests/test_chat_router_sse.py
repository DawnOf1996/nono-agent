"""POST /chat 的 SSE 错误传播 —— 引擎抛出的错误必须变成 error 帧，而不是只有 done。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from nono_app_server.engine.routers.chat import chat
from nono_app_server.engine.schemas import ChatRequest


class _FakeEngine:
    """submit_message 必定抛错的引擎替身。"""

    def __init__(self) -> None:
        self.calls = 0

    # 真实的 submit_message 是 async generator 函数：调用它直接拿到生成器，
    # 不是 coroutine —— 所以这里必须是普通 def，不能是 async def。
    def submit_message(self, text: str, *, attachments: Any = None) -> Any:
        self.calls += 1

        async def _boom():
            raise ValueError("模型 x 不支持图片输入。")
            yield  # pragma: no cover - 让函数成为 async generator

        return _boom()


class _FakeManager:
    def __init__(self, engine: _FakeEngine) -> None:
        self._engine = engine
        self.ask_signal: dict[str, Any] = {}
        self.sessions: dict[str, Any] = {}
        self.lock = asyncio.Lock()
        self.cancelled: list[str] = []

    async def get_or_create(self, session_id: str, model: str | None = None) -> _FakeEngine:
        return self._engine

    def touch(self, session_id: str) -> None:
        return None

    def cancel_permissions(self, session_id: str) -> None:
        return None


def _http_request() -> Any:
    # app.state 上没有 turn_observer —— 走无观察者的分支。
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


async def _collect(response: Any) -> list[tuple[str, Any]]:
    """收集 SSE 帧。sse-starlette 交出的是已解析的 dict，不是编码后的文本。"""
    frames: list[tuple[str, Any]] = []
    async for chunk in response.body_iterator:
        if isinstance(chunk, dict):
            frames.append((str(chunk.get("event", "")), chunk.get("data", "")))
            continue
        text = chunk.decode() if isinstance(chunk, bytes) else str(chunk)
        for block in text.split("\n\n"):
            if not block.strip():
                continue
            event, data = "message", ""
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    data = line[len("data:"):].strip()
            frames.append((event, data))
    return frames


@pytest.mark.asyncio
async def test_engine_error_surfaces_as_error_frame():
    """回归：drain_to_queue 抛出的异常此前被 break 吞掉，客户端只收到 done。"""
    engine = _FakeEngine()
    manager = _FakeManager(engine)

    response = await chat(
        ChatRequest(message="看这张图", session_id="s1"),
        _http_request(),
        manager=manager,  # type: ignore[arg-type]
    )
    frames = await _collect(response)

    events = [name for name, _ in frames]
    assert "error" in events, f"引擎异常必须以 error 帧返回，实际收到 {events}"

    error_data = json.loads(next(data for name, data in frames if name == "error"))
    assert "不支持图片输入" in error_data["error"]
    # 流仍然要正常收尾，前端才不会一直转圈。
    assert events[-1] == "done"


@pytest.mark.asyncio
async def test_no_error_frame_when_stream_is_clean():
    """对照组：正常流不应凭空多出 error 帧。"""

    class _OkEngine:
        def submit_message(self, text: str, *, attachments: Any = None) -> Any:
            async def _gen():
                yield SimpleNamespace(
                    type="system",
                    model_dump=lambda: {"type": "system"},
                )

            return _gen()

    response = await chat(
        ChatRequest(message="你好", session_id="s2"),
        _http_request(),
        manager=_FakeManager(_OkEngine()),  # type: ignore[arg-type]
    )
    frames = await _collect(response)
    events = [name for name, _ in frames]
    assert "error" not in events
    assert events[-1] == "done"
