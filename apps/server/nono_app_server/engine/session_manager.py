"""会话引擎生命周期：创建、TTL、AskUser 通道、事件流读取。"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.paths import resolve_session_cwd
from nono_agent.providers.catalog import load_catalog
from nono_agent.session.transcript import (
    DEFAULT_SESSION_DIR,
    LocalFileTranscriptWriter,
    existing_transcript_path,
    peek_session_header,
)
from nono_agent.tools.context import PermissionChecker

logger = logging.getLogger(__name__)


def deny_all_permission_checker(tool_name: str, input_params: dict[str, Any]) -> bool:
    """Server 默认权限检查器 —— 拒绝所有写操作（无人值守安全模式）。"""
    logger.warning(
        "Permission denied for tool '%s' (server mode, no interactive confirmation)",
        tool_name,
    )
    return False


class SessionManager:
    """每个 session_id 对应一个独立的 QueryEngine。"""

    def __init__(
        self,
        default_config: EngineConfig,
        permission_checker: PermissionChecker | None,
        *,
        session_ttl: float | None = None,
        session_ttl_check_interval: float = 60.0,
    ) -> None:
        self.default_config = default_config
        self.permission_checker = permission_checker
        self.session_ttl = session_ttl
        self.session_ttl_check_interval = session_ttl_check_interval
        self.sessions: dict[str, QueryEngine] = {}
        self.lock = asyncio.Lock()
        self.last_activity: dict[str, float] = {}
        self.pending_questions: dict[
            str, tuple[list[dict[str, Any]], asyncio.Future[dict[str, Any]]]
        ] = {}
        self.ask_signal: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def touch(self, session_id: str) -> None:
        self.last_activity[session_id] = time.monotonic()

    def make_ask_user(self, session_id: str) -> Any:
        """构建该会话的 AskUser 回调 —— 发出 SSE 事件并等待答案。"""

        async def ask_user(questions: list[dict[str, Any]]) -> dict[str, Any]:
            loop = asyncio.get_running_loop()
            future: asyncio.Future[dict[str, Any]] = loop.create_future()
            self.pending_questions[session_id] = (questions, future)
            queue = self.ask_signal.setdefault(session_id, asyncio.Queue())
            await queue.put({"type": "ask_user", "questions": questions})
            try:
                return await asyncio.wait_for(future, timeout=300)
            except TimeoutError:
                raise TimeoutError("AskUserQuestion timed out.") from None
            finally:
                self.pending_questions.pop(session_id, None)

        return ask_user

    def resolve_session_config(
        self,
        model: str | None = None,
        cwd: str | None = None,
        *,
        session_id: str | None = None,
    ) -> EngineConfig:
        """按会话覆盖 cwd / 模型，并从 YAML 目录解析对应 Provider。"""
        header = peek_session_header(session_id) if session_id else None
        resolved_cwd = resolve_session_cwd(
            cwd or (header.cwd if header and header.cwd else self.default_config.cwd)
        )
        resolved_model = model or self.default_config.model
        provider = self.default_config.provider
        try:
            catalog = load_catalog(resolved_cwd)
            provider = catalog.resolve(model_id=resolved_model)
            resolved_model = provider.model_id
        except Exception:
            logger.debug(
                "Catalog resolve skipped for model=%s cwd=%s",
                resolved_model,
                resolved_cwd,
            )
        return EngineConfig(
            model=resolved_model,
            cwd=resolved_cwd,
            model_context=(
                provider.model_context
                if provider is not None
                else self.default_config.model_context
            ),
            session_id=session_id or self.default_config.session_id,
            include_partial_messages=True,
            provider=provider,
            log_api_requests=self.default_config.log_api_requests,
        )

    async def get_or_create(
        self,
        session_id: str,
        model: str | None = None,
        cwd: str | None = None,
    ) -> QueryEngine:
        """获取或创建引擎实例 —— 统一的初始化入口。"""
        async with self.lock:
            if session_id in self.sessions:
                self.touch(session_id)
                return self.sessions[session_id]

            engine_config = self.resolve_session_config(model, cwd, session_id=session_id)
            engine = QueryEngine(
                engine_config,
                permission_checker=self.permission_checker,
                ask_user=self.make_ask_user(session_id),
            )
            await engine.initialize()
            self.sessions[session_id] = engine
            self.touch(session_id)
            return engine

    async def ttl_cleanup_task(self) -> None:
        """后台任务 —— 周期清理空闲超时的会话。"""
        while True:
            await asyncio.sleep(self.session_ttl_check_interval)
            now = time.monotonic()
            expired: list[str] = []
            async with self.lock:
                for sid in list(self.sessions):
                    if (
                        self.session_ttl is not None
                        and now - self.last_activity.get(sid, now) > self.session_ttl
                    ):
                        expired.append(sid)
                for sid in expired:
                    engine = self.sessions.pop(sid, None)
                    if engine is not None:
                        try:
                            await engine.close()
                        except Exception:
                            logger.exception("Error closing expired session %s", sid)
                        self.last_activity.pop(sid, None)
                        logger.info("Session %s expired (TTL)", sid)

    def events_writer(self, session_id: str) -> Any | None:
        """取该会话的事件流读取器 —— 活跃会话复用其写入器，否则按默认路径打开。"""
        engine = self.sessions.get(session_id)
        if engine is not None and engine.transcript_writer is not None:
            return engine.transcript_writer
        session_file = existing_transcript_path(DEFAULT_SESSION_DIR, session_id)
        if session_file is None:
            return None
        return LocalFileTranscriptWriter(session_file)

    async def resume(
        self,
        session_id: str,
        *,
        model: str | None = None,
        skip_system: bool = False,
    ) -> tuple[QueryEngine, list[Any]]:
        """关闭旧实例（若有），从 transcript 恢复会话。"""
        async with self.lock:
            old_engine = self.sessions.pop(session_id, None)
        if old_engine:
            await old_engine.close()

        header = peek_session_header(session_id)
        engine_config = self.resolve_session_config(
            model, header.cwd if header else None, session_id=session_id
        )
        engine = QueryEngine(engine_config, permission_checker=self.permission_checker)
        await engine.initialize()
        messages = await engine.resume_session(session_id, skip_system=skip_system)

        async with self.lock:
            self.sessions[session_id] = engine
        return engine, messages
