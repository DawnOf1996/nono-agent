"""会话引擎生命周期：创建、TTL、AskUser 通道、事件流读取。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.paths import resolve_session_cwd
from nono_agent.providers.catalog import load_catalog
from nono_agent.session.transcript import (
    LocalFileTranscriptWriter,
    active_session_dir,
    existing_transcript_path,
    peek_session_header,
)
from nono_agent.tools.builtin import create_builtin_tools
from nono_agent.tools.context import PermissionChecker
from nono_agent.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def deny_all_permission_checker(tool_name: str, input_params: dict[str, Any]) -> bool:
    """Server 默认权限检查器 —— 拒绝所有写操作（无人值守安全模式）。"""
    # 停自己起的进程不放大攻击面：只能作用于本会话的作业，模型无法凭空
    # 造出别的会话的 job_id。因此 job_kill 放行，否则晋升到后台的进程一旦
    # 不想要就只能等它自己跑完。
    if tool_name in _SESSION_SCOPED_TOOLS:
        return True
    logger.warning(
        "Permission denied for tool '%s' (server mode, no interactive confirmation)",
        tool_name,
    )
    return False


# 只作用于「本会话自己创建的资源」的工具，可无人值守放行。
_SESSION_SCOPED_TOOLS = frozenset({"job_kill"})


def build_session_tool_registry(config: EngineConfig | None = None) -> ToolRegistry:
    """每个 Web 会话一份内置工具，与 CLI 的 `create_builtin_tools()` 对齐。"""
    registry = ToolRegistry()
    for tool in create_builtin_tools(config):
        registry.register(tool)
    return registry


class SessionManager:
    """每个 session_id 对应一个独立的 QueryEngine。"""

    def __init__(
        self,
        default_config: EngineConfig,
        permission_checker: PermissionChecker | None,
        *,
        permission_checker_factory: Callable[[str], PermissionChecker] | None = None,
        permission_canceller: Callable[[str], Any] | None = None,
        session_ttl: float | None = None,
        session_ttl_check_interval: float = 60.0,
    ) -> None:
        self.default_config = default_config
        self.permission_checker = permission_checker
        self.permission_checker_factory = permission_checker_factory
        self.permission_canceller = permission_canceller
        self.session_ttl = session_ttl
        self.session_ttl_check_interval = session_ttl_check_interval
        self.sessions: dict[str, QueryEngine] = {}
        self.lock = asyncio.Lock()
        self.last_activity: dict[str, float] = {}
        # 每个会话的统计订阅者。引擎每落一步事件就回调这里扇出，
        # 消费端（HTTP SSE）据此推送，消费端不需要轮询。
        self._stats_subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = {}
        self._stats_disposers: dict[str, Callable[[], None]] = {}
        self.pending_questions: dict[
            str, tuple[list[dict[str, Any]], asyncio.Future[dict[str, Any]]]
        ] = {}
        self.ask_signal: dict[str, asyncio.Queue[dict[str, Any]]] = {}

    def _attach_stats_listener(self, session_id: str, engine: QueryEngine) -> None:
        """把引擎的统计变化接到本会话的订阅者上。

        引擎是懒建的：先开订阅、之后才发消息的话，``get_or_create`` 会在这里补挂，
        已经建立的流不用重连。
        """
        if not self._stats_subscribers.get(session_id):
            # 当前没人订阅，等真有订阅者时再挂（省一个常驻回调）。
            return
        if self._stats_disposers.get(session_id) is not None:
            return
        self._stats_disposers[session_id] = engine.add_stats_listener(
            lambda snapshot: self._fanout_stats(session_id, snapshot)
        )

    def _fanout_stats(self, session_id: str, snapshot: dict[str, Any]) -> None:
        """把快照推给该会话的所有订阅者。

        满了就丢最旧的一条：订阅者跟不上时，宁可给它旧的也不能反压对话。
        """
        for queue in list(self._stats_subscribers.get(session_id, ())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(snapshot)

    def subscribe_stats(self, session_id: str) -> asyncio.Queue[dict[str, Any]] | None:
        """订阅某会话的统计推送。返回 None 表示该会话既没有引擎也没有日志。"""
        if not self.sessions.get(session_id) and self.events_writer(session_id) is None:
            return None
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=8)
        self._stats_subscribers.setdefault(session_id, set()).add(queue)
        engine = self.sessions.get(session_id)
        if engine is not None:
            self._stats_disposers[session_id] = engine.add_stats_listener(
                lambda snapshot: self._fanout_stats(session_id, snapshot)
            )
        return queue

    def unsubscribe_stats(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """退订；该会话没人订阅了就把引擎上的回调摘掉。"""
        subscribers = self._stats_subscribers.get(session_id)
        if not subscribers:
            return
        subscribers.discard(queue)
        if subscribers:
            return
        self._stats_subscribers.pop(session_id, None)
        dispose = self._stats_disposers.pop(session_id, None)
        if dispose is not None:
            dispose()

    def touch(self, session_id: str) -> None:
        self.last_activity[session_id] = time.monotonic()

    def checker_for(self, session_id: str) -> PermissionChecker | None:
        """按会话取权限检查器 —— 挂起某个会话不会波及其他会话。"""
        if self.permission_checker_factory is not None:
            return self.permission_checker_factory(session_id)
        return self.permission_checker

    def cancel_permissions(self, session_id: str) -> None:
        """放掉该会话挂起中的授权请求（中止 / 关闭会话时调用）。"""
        if self.permission_canceller is not None:
            self.permission_canceller(session_id)

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

    def apply_model(self, engine: QueryEngine, model: str | None) -> str:
        """把模型切换应用到已有引擎，返回生效的模型 ID。"""
        requested = (model or "").strip()
        if not requested or requested == engine._config.effective_model:
            return engine._config.effective_model
        next_config = self.resolve_session_config(
            requested, engine._config.cwd, session_id=engine._config.session_id
        )
        engine.apply_model_config(
            model=next_config.model,
            provider=next_config.provider,
            model_context=next_config.model_context,
        )
        return engine._config.effective_model

    async def get_or_create(
        self,
        session_id: str,
        model: str | None = None,
        cwd: str | None = None,
    ) -> QueryEngine:
        """获取或创建引擎实例 —— 统一的初始化入口。"""
        async with self.lock:
            if session_id in self.sessions:
                engine = self.sessions[session_id]
                self.touch(session_id)
                if model:
                    self.apply_model(engine, model)
                return engine

            engine_config = self.resolve_session_config(model, cwd, session_id=session_id)
            engine = QueryEngine(
                engine_config,
                permission_checker=self.checker_for(session_id),
                tool_registry=build_session_tool_registry(engine_config),
                ask_user=self.make_ask_user(session_id),
            )
            await engine.initialize()
            self.sessions[session_id] = engine
            self._attach_stats_listener(session_id, engine)
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
                        # 引擎没了，这个会话的推送不会再有；把回调摘掉，
                        # 订阅者留在原地（等重新建引擎时由 _attach_stats_listener 补挂）。
                        dispose = self._stats_disposers.pop(sid, None)
                        if dispose is not None:
                            dispose()
                        logger.info("Session %s expired (TTL)", sid)

    async def reload_extensions_for_cwd(
        self, cwd: str | None, *, mcp: bool = True, tool_plugins: bool = True
    ) -> None:
        """工作区开关变化后，刷新同一 cwd 下的活跃引擎。

        MCP 与工具插件分成两个开关：内置工具插件的开关只该动工具插件，
        不该顺带把用户的 MCP server 重连一遍（那会打断正在跑的会话）。
        """
        if not cwd or not str(cwd).strip():
            return
        resolved = resolve_session_cwd(cwd)
        async with self.lock:
            engines = [
                engine
                for engine in self.sessions.values()
                if resolve_session_cwd(engine._config.cwd) == resolved
            ]
        await self._reload_engines(engines, resolved, mcp=mcp, tool_plugins=tool_plugins)

    async def reload_extensions_all(
        self, *, mcp: bool = True, tool_plugins: bool = True
    ) -> None:
        """全局开关变化后，刷新**所有**活跃引擎。

        工具插件的开关与参数写在用户级 settings 里，对每个工作区一视同仁
        —— 只刷 cwd 那一个会话的话，别的会话要等到下次新建才看到新状态，
        用户会以为开关没生效。这里仍然只刷工具插件，不碰 MCP。
        """
        async with self.lock:
            engines = list(self.sessions.values())
        await self._reload_engines(engines, "<all>", mcp=mcp, tool_plugins=tool_plugins)

    async def _reload_engines(
        self,
        engines: list[Any],
        label: str,
        *,
        mcp: bool,
        tool_plugins: bool,
    ) -> None:
        for engine in engines:
            if mcp:
                try:
                    await engine.reload_mcp()
                except Exception:
                    logger.exception(
                        "Failed to reload MCP for session %s cwd=%s",
                        engine._config.session_id,
                        label,
                    )
            if tool_plugins:
                try:
                    await engine.reload_tool_plugins()
                except Exception:
                    logger.exception(
                        "Failed to reload tool plugins for session %s cwd=%s",
                        engine._config.session_id,
                        label,
                    )

    async def reload_mcp_for_cwd(self, cwd: str | None) -> None:
        """工作区 MCP 开关变化后，刷新同一 cwd 下的活跃引擎。"""
        await self.reload_extensions_for_cwd(cwd, mcp=True, tool_plugins=False)

    def events_writer(self, session_id: str) -> Any | None:
        """取该会话的事件流读取器 —— 活跃会话复用其写入器，否则按默认路径打开。"""
        engine = self.sessions.get(session_id)
        if engine is not None and engine.transcript_writer is not None:
            return engine.transcript_writer
        session_file = existing_transcript_path(active_session_dir(), session_id)
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
        engine = QueryEngine(
            engine_config,
            permission_checker=self.checker_for(session_id),
            tool_registry=build_session_tool_registry(engine_config),
            ask_user=self.make_ask_user(session_id),
        )
        await engine.initialize()
        messages = await engine.resume_session(session_id, skip_system=skip_system)

        async with self.lock:
            self.sessions[session_id] = engine
        self._attach_stats_listener(session_id, engine)
        return engine, messages
