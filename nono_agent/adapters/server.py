"""Server 适配器 —— HTTP API + SSE 流式推送。

使用 FastAPI + sse-starlette 将 agent 引擎作为 Web 服务暴露。
Server 与 CLI 消费同一个 submit_message() AsyncGenerator，
区别仅在于 Server 通过 SSE 推送给远程 Web 客户端。

特性：
- 多会话隔离（每个用户拥有独立的 QueryEngine）
- SSE 流式实时响应
- 健康检查端点
- 优雅关闭
- CORS 支持
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Any

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.tools.context import PermissionChecker

logger = logging.getLogger(__name__)


def _deny_all_permission_checker(tool_name: str, input_params: dict[str, Any]) -> bool:
    """Server 默认权限检查器 —— 拒绝所有写操作（无人值守安全模式）。"""
    logger.warning(
        "Permission denied for tool '%s' (server mode, no interactive confirmation)",
        tool_name,
    )
    return False


def create_server_app(
    config: EngineConfig | None = None,
    *,
    host: str = "0.0.0.0",
    port: int = 8000,
    cors_origins: list[str] | None = None,
    permission_checker: PermissionChecker | None = _deny_all_permission_checker,
) -> Any:
    """创建 FastAPI 应用实例 —— 提供 agent 的 HTTP API。

    Args:
        config: 默认引擎配置
        host: 服务器绑定地址
        port: 服务器绑定端口
        cors_origins: 允许的 CORS 来源列表
        permission_checker: 权限检查器，默认拒绝所有写操作。
            传入 None 表示不检查（宽松模式），或传入自定义函数。
    """
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
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

    default_config = config or EngineConfig()

    async def _get_or_create_engine(
        session_id: str,
        model: str | None = None,
    ) -> QueryEngine:
        """获取或创建引擎实例 —— 统一的初始化入口。"""
        async with session_lock:
            if session_id in sessions:
                return sessions[session_id]

            engine_config = EngineConfig(
                model=model or default_config.model,
                cwd=default_config.cwd,
                session_id=session_id,
                include_partial_messages=True,
            )
            engine = QueryEngine(
                engine_config, permission_checker=permission_checker
            )
            await engine.initialize()
            sessions[session_id] = engine
            return engine

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> Any:
        """应用生命周期管理 —— 启动时初始化，关闭时清理所有会话。"""
        logger.info("Nono Agent Server starting up...")
        yield
        # 优雅关闭：逐一关闭所有活跃会话，释放资源
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
        total_cost_usd: float    # 累计花费（美元）

    class AbortRequest(BaseModel):
        """中止请求 —— 客户端请求取消正在进行的查询。"""
        session_id: str              # 要中止的会话 ID
        reason: str | None = None    # 中止原因（可选，用于日志）

    class ResumeRequest(BaseModel):
        """恢复会话请求 —— 从 transcript 存储加载历史会话。"""
        session_id: str                              # 要恢复的会话 ID
        model: str | None = None                     # 模型覆盖（可选）
        skip_system: bool = False                    # 是否过滤临时系统消息

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

        async def event_generator() -> Any:
            """将引擎的 AsyncGenerator 响应转换为 SSE 事件流。

            遍历 submit_message() 产出的 SDKMessage，
            将每条消息序列化为 SSE event/data 对推送给客户端。
            """
            try:
                async for sdk_msg in engine.submit_message(
                    request.message,
                    attachments=request.attachments,
                ):
                    # 将 Pydantic 模型序列化为字典
                    msg_dict = sdk_msg.model_dump() if hasattr(sdk_msg, "model_dump") else {}
                    yield {
                        "event": sdk_msg.type,
                        "data": json.dumps(msg_dict, ensure_ascii=False),
                    }

                    # result 消息表示本轮查询完成
                    if hasattr(sdk_msg, "type") and sdk_msg.type == "result":
                        pass

            except Exception as exc:
                logger.exception("Error in chat stream for session %s", session_id)
                yield {
                    "event": "error",
                    "data": json.dumps({
                        "error": str(exc),
                        "session_id": session_id,
                    }),
                }
            finally:
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
            total_cost_usd=0.0,
        )

    @app.get("/sessions")
    async def list_sessions() -> dict[str, Any]:
        """列出所有活跃会话 —— 返回每个会话的 ID、模型、轮次和花费。"""
        result = []
        async with session_lock:
            for sid, engine in sessions.items():
                result.append(
                    SessionInfo(
                        session_id=sid,
                        model=engine._config.model,
                        turn_count=engine._turn_count,
                        total_cost_usd=engine.cost_tracker.get_total_cost(),
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

        engine_config = EngineConfig(
            model=req.model or default_config.model,
            cwd=default_config.cwd,
            session_id=session_id,
            include_partial_messages=True,
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
            total_cost_usd=0.0,
        )

    @app.post("/abort")
    async def abort_session(request: AbortRequest) -> dict[str, str]:
        """中止会话中正在进行的查询 —— 触发 AbortController 取消流式传输。"""
        async with session_lock:
            engine = sessions.get(request.session_id)
        if engine is None:
            raise HTTPException(status_code=404, detail="Session not found")
        engine.abort(request.reason)
        return {"status": "aborted", "session_id": request.session_id}

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
