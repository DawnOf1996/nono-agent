"""Server 适配器 —— HTTP API + SSE 流式推送。

使用 FastAPI + sse-starlette 将 agent 引擎作为 Web 服务暴露。
Server 与 CLI 消费同一个 submit_message() AsyncGenerator，
区别仅在于 Server 通过 SSE 推送给远程 Web 客户端。

特性：
- 多会话隔离（每个用户拥有独立的 QueryEngine）
- SSE 流式实时响应
- 会话事件流只读通道（``GET /session/{id}/events``，脱敏后推送）
- 健康检查端点
- 优雅关闭
- CORS 支持
"""

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.session.header import VersionMismatchError
from nono_agent.session.redact import redact_events
from nono_agent.session.transcript import (
    DEFAULT_SESSION_DIR,
    LocalFileTranscriptWriter,
    existing_transcript_path,
    peek_session_header,
)
from nono_agent.tools.context import PermissionChecker

logger = logging.getLogger(__name__)


def _deny_all_permission_checker(tool_name: str, input_params: dict[str, Any]) -> bool:
    """Server 默认权限检查器 —— 拒绝所有写操作（无人值守安全模式）。"""
    logger.warning(
        "Permission denied for tool '%s' (server mode, no interactive confirmation)",
        tool_name,
    )
    return False


async def _drain_to_queue(agen: Any, out: asyncio.Queue[Any]) -> None:
    """把 AsyncGenerator 产出全部倒入队列。"""
    try:
        async for item in agen:
            await out.put(item)
    except Exception:
        logger.exception("submit_message stream failed")
        raise


def create_server_app(
    config: EngineConfig | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    cors_origins: list[str] | None = None,
    permission_checker: PermissionChecker | None = _deny_all_permission_checker,
    api_key: str | None = None,
    session_ttl: float | None = None,
    session_ttl_check_interval: float = 60.0,
) -> Any:
    """创建 FastAPI 应用实例 —— 提供 agent 的 HTTP API。

    Args:
        config: 默认引擎配置
        host: 服务器绑定地址
        port: 服务器绑定端口
        cors_origins: 允许的 CORS 来源列表
        permission_checker: 权限检查器，默认拒绝所有写操作。
            传入 None 表示不检查（宽松模式），或传入自定义函数。
        api_key: 可选鉴权密钥。设置后除 /health 外均需
            ``Authorization: Bearer <key>`` 或 ``X-API-Key: <key>``。
        session_ttl: 会话空闲超时（秒）。超过后后台任务清理并关闭会话。
        session_ttl_check_interval: TTL 清理扫描间隔（秒）。
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import JSONResponse
        from pydantic import BaseModel
    except ImportError:
        raise ImportError(
            "FastAPI is required for the server adapter. "
            "Install with: pip install fastapi uvicorn sse-starlette"
        )

    try:
        from sse_starlette.sse import EventSourceResponse
    except ImportError:
        raise ImportError(
            "sse-starlette is required for SSE streaming. "
            "Install with: pip install sse-starlette"
        )

    # ---------------------------------------------------------------------------
    # 会话管理 —— 每个 session_id 对应一个独立的 QueryEngine 实例
    # ---------------------------------------------------------------------------
    sessions: dict[str, QueryEngine] = {}
    session_lock = asyncio.Lock()  # 保护 sessions 字典的并发访问
    last_activity: dict[str, float] = {}  # 会话最后活跃时间（TTL 用）
    # AskUser 通道：session_id -> (questions, future)
    pending_questions: dict[str, tuple[list[dict[str, Any]], asyncio.Future[dict[str, Any]]]] = {}
    ask_signal: dict[str, asyncio.Queue[dict[str, Any]]] = {}  # session_id -> 信号队列

    default_config = config or EngineConfig()

    def _touch(session_id: str) -> None:
        last_activity[session_id] = time.monotonic()

    def _make_ask_user(session_id: str) -> Any:
        """构建该会话的 AskUser 回调 —— 发出 SSE 事件并等待答案。"""

        async def ask_user(questions: list[dict[str, Any]]) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            pending_questions[session_id] = (questions, future)
            queue = ask_signal.setdefault(session_id, asyncio.Queue())
            await queue.put({"type": "ask_user", "questions": questions})
            try:
                return await asyncio.wait_for(future, timeout=300)
            except TimeoutError:
                raise TimeoutError("AskUserQuestion timed out.") from None
            finally:
                pending_questions.pop(session_id, None)

        return ask_user

    async def _get_or_create_engine(
        session_id: str,
        model: str | None = None,
    ) -> QueryEngine:
        """获取或创建引擎实例 —— 统一的初始化入口。"""
        async with session_lock:
            if session_id in sessions:
                _touch(session_id)
                return sessions[session_id]

            header = peek_session_header(session_id)
            engine_config = EngineConfig(
                model=model or default_config.model,
                cwd=(header.cwd if header and header.cwd else default_config.cwd),
                session_id=session_id,
                include_partial_messages=True,
                provider=default_config.provider,
                log_api_requests=default_config.log_api_requests,
            )
            engine = QueryEngine(
                engine_config,
                permission_checker=permission_checker,
                ask_user=_make_ask_user(session_id),
            )
            await engine.initialize()
            sessions[session_id] = engine
            _touch(session_id)
            return engine

    async def _ttl_cleanup_task() -> None:
        """后台任务 —— 周期清理空闲超时的会话。"""
        while True:
            await asyncio.sleep(session_ttl_check_interval)
            now = time.monotonic()
            expired: list[str] = []
            async with session_lock:
                for sid in list(sessions):
                    if session_ttl is not None and now - last_activity.get(sid, now) > session_ttl:
                        expired.append(sid)
                for sid in expired:
                    engine = sessions.pop(sid, None)
                    if engine is not None:
                        try:
                            await engine.close()
                        except Exception:
                            logger.exception("Error closing expired session %s", sid)
                        last_activity.pop(sid, None)
                        logger.info("Session %s expired (TTL)", sid)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        """应用生命周期管理 —— 启动时初始化，关闭时清理所有会话。"""
        logger.info("Nono Agent Server starting up...")
        ttl_task: asyncio.Task[None] | None = None
        if session_ttl is not None:
            ttl_task = asyncio.create_task(_ttl_cleanup_task())
        yield
        # 优雅关闭：逐一关闭所有活跃会话，释放资源
        if ttl_task is not None:
            ttl_task.cancel()
        logger.info("Shutting down — closing %d sessions...", len(sessions))
        async with session_lock:
            for sid, engine in list(sessions.items()):
                try:
                    await engine.close()
                except Exception:
                    logger.exception("Error closing session %s", sid)
                del sessions[sid]
        logger.info("Server shutdown complete.")

    app = FastAPI(
        title="Nono Agent Server",
        description="AI Agent Engine API — streaming responses via SSE",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 鉴权中间件：除 /health 外均需 API key（若配置）
    @app.middleware("http")
    async def auth_middleware(request: Any, call_next: Any) -> Any:
        if api_key is not None and request.url.path != "/health":
            auth = request.headers.get("authorization", "")
            token = (
                auth[7:] if auth.startswith("Bearer ") else request.headers.get("x-api-key", "")
            )
            if token != api_key:
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
        return await call_next(request)

    # CORS
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # ------------------------------------------------------------------
    # 请求 / 响应 Pydantic 模型
    # ------------------------------------------------------------------

    class ChatRequest(BaseModel):
        """聊天请求 —— 客户端发送消息的入口。"""
        message: str                                    # 用户消息文本
        session_id: str | None = None                   # 会话 ID（可选，不传则自动创建）
        model: str | None = None                        # 模型覆盖（可选）
        attachments: list[dict[str, Any]] | None = None # 附件列表（可选）

    class SessionInfo(BaseModel):
        """会话信息 —— 用于 API 响应的会话元数据。"""
        session_id: str          # 会话唯一标识
        model: str               # 当前使用的模型
        turn_count: int          # 已完成的对话轮次
        total_tokens: int        # 累计 token 数

    class AbortRequest(BaseModel):
        """中止请求 —— 客户端请求取消正在进行的查询。"""
        session_id: str              # 要中止的会话 ID
        reason: str | None = None    # 中止原因（可选，用于日志）

    class ResumeRequest(BaseModel):
        """恢复会话请求 —— 从 transcript 存储加载历史会话。"""
        session_id: str                              # 要恢复的会话 ID
        model: str | None = None                     # 模型覆盖（可选）
        skip_system: bool = False                    # 是否过滤临时系统消息

    class AnswerRequest(BaseModel):
        """AskUser 回答请求 —— 客户端对 ask_user 事件的回应。"""
        answers: dict[str, Any] = {}                 # {"question_text": selected_label(s)}

    # ------------------------------------------------------------------
    # API 端点
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, Any]:
        """健康检查端点 —— 返回服务状态和活跃会话数。"""
        return {"status": "ok", "sessions": len(sessions)}

    @app.post("/chat")
    async def chat(request: ChatRequest) -> Any:
        """发送消息并以 SSE 流式返回响应。

        响应是 Server-Sent Events 流，每个事件包含一个 JSON 编码的 SDK 消息。
        事件类型：
        - data: SDK 消息（assistant, system, stream_event, result）
        - error: 错误消息
        - done: 流式传输完成
        """
        session_id = request.session_id or str(uuid.uuid4())
        engine = await _get_or_create_engine(session_id, model=request.model)
        _touch(session_id)

        async def event_generator() -> Any:
            """将引擎 SDK 流与 AskUser 信号合并为 SSE 事件流。"""
            submit = engine.submit_message(
                request.message,
                attachments=request.attachments,
            )
            queue = ask_signal.setdefault(session_id, asyncio.Queue())
            sdk_queue: asyncio.Queue[Any] = asyncio.Queue()
            sdk_task: asyncio.Task[None] = asyncio.create_task(
                _drain_to_queue(submit, sdk_queue)
            )
            try:
                while True:
                    # 优先消费 AskUser 信号
                    try:
                        signal = queue.get_nowait()
                        yield {
                            "event": "ask_user",
                            "data": json.dumps(signal, ensure_ascii=False),
                        }
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    # 消费 SDK 消息（非阻塞）
                    try:
                        sdk_msg = sdk_queue.get_nowait()
                        yield {
                            "event": sdk_msg.type,
                            "data": json.dumps(sdk_msg.model_dump(), ensure_ascii=False),
                        }
                        continue
                    except asyncio.QueueEmpty:
                        pass

                    # 都空：SDK 流结束则排空退出，否则让出事件循环
                    if sdk_task.done():
                        while not sdk_queue.empty():
                            sdk_msg = sdk_queue.get_nowait()
                            yield {
                                "event": sdk_msg.type,
                                "data": json.dumps(sdk_msg.model_dump(), ensure_ascii=False),
                            }
                        break

                    await asyncio.sleep(0.01)
            except Exception as exc:
                logger.exception("Error in chat stream for session %s", session_id)
                yield {
                    "event": "error",
                    "data": json.dumps({"error": str(exc), "session_id": session_id}),
                }
            finally:
                if not sdk_task.done():
                    sdk_task.cancel()
                yield {"event": "done", "data": ""}

        return EventSourceResponse(event_generator())

    @app.post("/session/new")
    async def new_session(
        model: str | None = None,
        cwd: str | None = None,
    ) -> SessionInfo:
        """创建新会话并返回其 ID —— 不发送消息，仅初始化引擎。"""
        session_id = str(uuid.uuid4())
        engine = await _get_or_create_engine(session_id, model=model)

        return SessionInfo(
            session_id=session_id,
            model=engine._config.model,
            turn_count=0,
            total_tokens=0,
        )

    @app.get("/sessions")
    async def list_sessions() -> dict[str, Any]:
        """列出所有活跃会话 —— 返回每个会话的 ID、模型、轮次和 token 数。"""
        result = []
        async with session_lock:
            for sid, engine in sessions.items():
                result.append(
                    SessionInfo(
                        session_id=sid,
                        model=engine._config.model,
                        turn_count=engine._turn_count,
                        total_tokens=engine.token_tracker.get_total_tokens(),
                    )
                )
        return {"sessions": result}

    @app.delete("/session/{session_id}")
    async def close_session(session_id: str) -> dict[str, str]:
        """关闭并移除指定会话 —— 释放该会话的引擎资源。"""
        async with session_lock:
            engine = sessions.pop(session_id, None)
        if engine:
            await engine.close()
            return {"status": "closed", "session_id": session_id}
        raise HTTPException(status_code=404, detail="Session not found")

    @app.post("/session/{session_id}/resume")
    async def resume(session_id: str, request: ResumeRequest | None = None) -> SessionInfo:
        """恢复历史会话 —— 通过 TranscriptWriter 协议加载并继续对话。

        创建新的引擎实例，从存储后端读取历史消息加载到 store，
        然后可以像新会话一样通过 /chat 继续对话。
        """
        req = request or ResumeRequest(session_id=session_id)

        # 如果已有活跃实例，先关闭
        async with session_lock:
            old_engine = sessions.pop(session_id, None)
        if old_engine:
            await old_engine.close()

        header = peek_session_header(session_id)
        engine_config = EngineConfig(
            model=req.model or default_config.model,
            cwd=(header.cwd if header and header.cwd else default_config.cwd),
            session_id=session_id,
            include_partial_messages=True,
            provider=default_config.provider,
            log_api_requests=default_config.log_api_requests,
        )
        engine = QueryEngine(engine_config, permission_checker=permission_checker)
        await engine.initialize()
        messages = await engine.resume_session(
            session_id, skip_system=req.skip_system
        )

        async with session_lock:
            sessions[session_id] = engine

        return SessionInfo(
            session_id=session_id,
            model=engine._config.model,
            turn_count=len(messages),
            total_tokens=0,
        )

    @app.get("/session/{session_id}/events")
    async def session_events(
        session_id: str,
        after_seq: int = 0,
        follow: bool = True,
        poll_interval: float = 0.5,
    ) -> Any:
        """按 SSE 推送该会话的事件流 —— 可观测性的读取通道。

        载荷经 ``redact_events`` 脱敏后再出网：磁盘上的事件流逐字真实，
        外发副本不带凭据。信封字段（seq/type/timestamp）保持原值，
        客户端可用 ``after_seq`` 断点续读。

        Args:
            session_id: 目标会话 ID。
            after_seq: 只推送 ``seq > after_seq`` 的事件（断点续读游标）。
            follow: 是否在追平后继续等待新事件；False 表示推完历史即结束。
            poll_interval: follow 模式下的轮询间隔（秒）。
        """
        writer = _events_writer(session_id)

        async def event_generator() -> Any:
            cursor = after_seq
            while True:
                try:
                    events = await writer.read_events_from(cursor + 1)
                except VersionMismatchError as exc:
                    yield {"event": "error", "data": json.dumps({"error": str(exc)})}
                    return

                for event in redact_events(events):
                    cursor = event.seq
                    yield {
                        "event": "session_event",
                        "id": str(event.seq),
                        "data": event.model_dump_json(),
                    }

                if not follow:
                    yield {"event": "done", "data": json.dumps({"last_seq": cursor})}
                    return
                await asyncio.sleep(poll_interval)

        return EventSourceResponse(event_generator())

    def _events_writer(session_id: str) -> Any:
        """取该会话的事件流读取器 —— 活跃会话复用其写入器，否则按默认路径打开。"""
        engine = sessions.get(session_id)
        if engine is not None and engine.transcript_writer is not None:
            return engine.transcript_writer
        session_file = existing_transcript_path(DEFAULT_SESSION_DIR, session_id)
        if session_file is None:
            raise HTTPException(status_code=404, detail="Session log not found")
        return LocalFileTranscriptWriter(session_file)

    @app.post("/abort")
    async def abort_session(request: AbortRequest) -> dict[str, str]:
        """中止会话中正在进行的查询 —— 触发 AbortController 取消流式传输。"""
        async with session_lock:
            engine = sessions.get(request.session_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Session not found")
        engine.abort(request.reason)
        return {"status": "aborted", "session_id": request.session_id}

    @app.post("/session/{session_id}/answer")
    async def answer(session_id: str, body: AnswerRequest) -> dict[str, str]:
        """回应用户对 AskUser 事件的回答，唤醒等待中的流。"""
        pending = pending_questions.get(session_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="No pending question for session")
        _, future = pending
        if future.done():
            raise HTTPException(status_code=409, detail="Question already answered")
        future.set_result(body.answers)
        return {"status": "answered", "session_id": session_id}

    return app


def run_server(
    config: EngineConfig | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    cors_origins: list[str] | None = None,
) -> None:
    """便捷函数 —— 创建并启动 uvicorn HTTP 服务器。

    Args:
        config: 默认引擎配置
        host: 服务器绑定地址
        port: 服务器绑定端口
        cors_origins: 允许的 CORS 来源列表
    """
    try:
        import uvicorn
    except ImportError:
        raise ImportError(
            "uvicorn is required to run the server. "
            "Install with: pip install uvicorn"
        )

    app = create_server_app(config, host=host, port=port, cors_origins=cors_origins)
    uvicorn.run(app, host=host, port=port)
