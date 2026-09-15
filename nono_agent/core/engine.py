"""QueryEngine —— 顶层编排层。

管理一次对话的完整生命周期：
1. 初始化所有子系统 (provider, tools, pipeline, middleware, hooks)
2. 提供 submit_message() —— 唯一的公开 API
3. 将内部循环消息转换为 SDK 消息供消费者使用
4. 管理 transcript 持久化和成本跟踪
5. 支持 async with 上下文管理器协议

双侧流式适配：
- CLI 和 Server 适配器消费同一个 submit_message() AsyncGenerator
- 区别仅在于交付方式：CLI 本地消费 + Rich 渲染，Server SSE/WebSocket 推送

用法示例：
    async with QueryEngine(config) as engine:
        async for sdk_msg in engine.submit_message("Hello!"):
            print(sdk_msg)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncGenerator, Callable
from typing import Any

from nono_agent.core.config import EngineConfig, ProviderConfig, QueryConfig, RuntimeGates
from nono_agent.core.loop import agentic_loop
from nono_agent.core.state import (
    AutoCompactTracking,
    LoopState,
)
from nono_agent.core.types import (
    AssistantMessage,
    InternalMessage,
    Message,
    RequestStartEvent,
    SDKAssistantMessage,
    SDKMessage,
    SDKResultMessage,
    SDKStreamEventMessage,
    SDKSystemMessage,
    SDKToolUseSummary,
    SDKUserMessageReplay,
    StreamEvent,
    SystemMessage,
    TombstoneMessage,
    ToolUseSummaryMessage,
    Usage,
    UserMessage,
)
from nono_agent.hooks.middleware import MiddlewareChain
from nono_agent.hooks.post_sampling import PostSamplingChain
from nono_agent.hooks.stop_hooks import StopHookExecutor
from nono_agent.memory.manager import MemoryManager
from nono_agent.memory.models import STATUS_ACCEPTED, MemoryRecord
from nono_agent.memory.store import FileSystemMemoryStore, MemoryStore
from nono_agent.observability.metrics import (
    METRIC_CONTEXT_TOKENS,
    METRIC_MESSAGE_COUNT,
    METRIC_SESSION_DESYNC,
    TAG_MODEL,
    TAG_SESSION_ID,
    record_counter,
    record_event_metric,
    record_gauge,
)
from nono_agent.observability.token_tracker import TokenTracker
from nono_agent.permissions.denial import Denial, DenialTracker
from nono_agent.permissions.policy import PermissionMode, PermissionPolicy
from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
from nono_agent.paths import resolve_session_cwd
from nono_agent.pipeline.auto_compact import AutoCompactStage
from nono_agent.pipeline.base import PreprocessPipeline
from nono_agent.pipeline.microcompact import MicroCompactStage
from nono_agent.pipeline.snip_compact import SnipCompactStage
from nono_agent.pipeline.tool_result_budget import ToolResultBudgetStage
from nono_agent.prompt.assembler import PromptAssembler
from nono_agent.prompt.registry import SectionRegistry
from nono_agent.prompt.section import PromptContext
from nono_agent.prompt.sections import create_default_sections
from nono_agent.providers.base import ProviderRegistry, create_default_registry
from nono_agent.recovery.chain import RecoveryChain
from nono_agent.rules.loader import FileSystemRuleLoader
from nono_agent.rules.merge import merge_rules
from nono_agent.session.derive import (
    ReconstructionError,
    derive_messages,
    derived_to_messages,
    fold_request_header,
    messages_to_derived,
    verify_reconstruction,
)
from nono_agent.session.events import (
    EVENT_ASSISTANT_CHUNK,
    EVENT_ASSISTANT_MESSAGE,
    EVENT_COMPACT_BOUNDARY,
    EVENT_MEMORY_RETRIEVE,
    EVENT_MEMORY_WRITE,
    EVENT_PERMISSION_CHANGED,
    EVENT_PERMISSION_DENIAL,
    EVENT_PERMISSION_ONCHANGE,
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_CALL,
    EVENT_TOOL_RESULT,
    EVENT_TURN_END,
    EVENT_TURN_START,
    EVENT_USAGE,
    EVENT_USER_INPUT,
    EVENT_USER_MESSAGE,
    SessionEvent,
    last_turn_number,
)
from nono_agent.session.fork import fork_transcript
from nono_agent.session.header import (
    SessionHeader,
    first_user_prompt,
    make_header,
    normalize_permission_mode,
    summarize_user_prompt,
)
from nono_agent.session.request_header import canonical_header, next_reason
from nono_agent.session.store import MessageStore
from nono_agent.session.transcript import (
    DEFAULT_SESSION_DIR,
    LocalFileTranscriptWriter,
    TranscriptWriter,
)
from nono_agent.skills.loader import FileSystemSkillLoader
from nono_agent.skills.models import Skill
from nono_agent.skills.skill_tool import SkillTool
from nono_agent.tools.context import PermissionChecker, ToolUseContext
from nono_agent.tools.registry import ToolRegistry
from nono_agent.utils.abort import AbortController
from nono_agent.utils.tokens import analyze_context_usage

logger = logging.getLogger(__name__)


class QueryEngine:
    """主代理引擎 —— 编排 agentic loop 的核心组件。

    设计原则：
    - 所有子系统可通过构造函数注入，也可自动创建默认实例
    - 支持 async with 上下文管理器，确保资源清理
    - submit_message() 是唯一的公开 API，返回 AsyncGenerator[SDKMessage]
    """

    def __init__(
        self,
        config: EngineConfig,
        *,
        provider_registry: ProviderRegistry | None = None,
        tool_registry: ToolRegistry | None = None,
        pipeline: PreprocessPipeline | None = None,
        middleware_chain: MiddlewareChain | None = None,
        stop_hook_executor: StopHookExecutor | None = None,
        post_sampling_chain: PostSamplingChain | None = None,
        recovery_chain: RecoveryChain | None = None,
        transcript_writer: TranscriptWriter | None = None,
        permission_checker: PermissionChecker | None = None,
        permission_policy: PermissionPolicy | None = None,
        denial_tracker: DenialTracker | None = None,
        rule_loader: FileSystemRuleLoader | None = None,
        prompt_assembler: PromptAssembler | None = None,
        ask_user: Any | None = None,
        skill_loader: FileSystemSkillLoader | None = None,
        agent_loader: Any | None = None,
        register_agent_tool: bool = True,
        memory_store: MemoryStore | None = None,
        memory_manager: MemoryManager | None = None,
        auto_load_mcp: bool = True,
    ) -> None:
        self._config = config
        self._initialized = False

        # 子系统：可注入或自动创建默认实例
        self._provider_registry = provider_registry or create_default_registry()
        self._tool_registry = tool_registry or ToolRegistry()
        self._pipeline = pipeline or self._create_default_pipeline()
        self._middleware_chain = middleware_chain or MiddlewareChain()
        self._stop_hook_executor = stop_hook_executor or StopHookExecutor()
        self._post_sampling_chain = post_sampling_chain or PostSamplingChain()
        self._recovery_chain = recovery_chain or RecoveryChain()

        # 运行时状态
        self._store = MessageStore()             # 消息存储
        self._token_tracker = TokenTracker()     # Token 跟踪器
        self._abort_controller = AbortController()  # 取消控制器
        self._transcript_writer = transcript_writer  # 事件流写入器（落盘只有事件）
        self._owns_writer = transcript_writer is None  # 默认写入器由引擎按会话 ID 创建
        self._permission_checker = permission_checker  # 权限检查器（CLI/Server 注入）

        # 权限体系（Phase 1）：策略 + 拒绝追踪 + 路径沙箱 + Ignore
        self._permission_policy = permission_policy or PermissionPolicy()
        self._applying_header = False
        self._session_header: SessionHeader | None = None
        self._header_persist_tasks: set[asyncio.Task[None]] = set()
        self._permission_policy.on_mode_change(self._on_permission_mode_change)
        self._permission_policy.on_mode_request(self._on_permission_mode_request)
        self._denial_tracker = denial_tracker or DenialTracker()
        self._sandbox = PathSandbox(config.cwd)
        self._ignore = IgnoreMatcher.from_workspace(config.cwd)

        # Prompt 组装 + Rules 发现（Phase 1）+ Skills（Phase 2）+ 子 Agent（Phase 3）
        self._rule_loader = rule_loader or FileSystemRuleLoader()
        self._prompt_assembler = prompt_assembler or self._create_default_assembler()
        self._skill_loader = skill_loader or FileSystemSkillLoader()
        self._skills: list[Skill] = []
        self._agent_loader = agent_loader
        self._register_agent_tool = register_agent_tool
        self._memory_store: MemoryStore = memory_store or FileSystemMemoryStore()
        self._memory_manager = memory_manager or MemoryManager(self._memory_store)

        # 事件溯源会话日志：待写事件队列（同步 stage + 异步 flush，保证追加顺序）
        self._pending_events: list[SessionEvent] = []
        self._header_logged = False           # 本实例是否已写过 request/header
        self._last_header: dict[str, Any] | None = None
        self._last_system_prompt: str = ""    # 最近一次组装的系统提示词（供上下文分析复用）
        self._observer_disposers: list[Callable[[], None]] = []
        self._register_event_observers()

        # AskUserQuestion 通道（Phase 1 CLI / Phase 3 Server）
        self._ask_user = ask_user
        self._auto_load_mcp = auto_load_mcp
        self._mcp_runtime: Any = None

        # 会话信息
        self._session_id = config.session_id
        self._turn_count = 0
        # 新会话只在内存里，直到首条用户消息（或接续已有 header）才写盘
        self._persisted = False

        if config.log_api_requests or (
            config.provider is not None and config.provider.log_api_requests
        ):
            from nono_agent.providers._request_log import enable_api_request_logging

            enable_api_request_logging()

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """初始化引擎 —— 必须在 submit_message 前调用。

        自动调用（通过 async with 或 submit_message 内部），
        幂等设计，多次调用无副作用。
        新会话此时不写磁盘；首条用户消息才会创建 header 与事件流。
        """
        if self._initialized:
            return

        # 设置事件流写入器（延迟初始化，使用 session_id 确定存储路径）
        writer = self._transcript_writer or self._create_default_writer()
        self._transcript_writer = writer

        # 新会话只把 SessionHeader 放在内存；首条用户消息 flush 时才写盘。
        # 接续已有会话则以磁盘 header 为准（cwd / agent / permission）。
        existing = await writer.read_header()
        if existing is None:
            self._session_header = make_header(
                self._session_id,
                cwd=self._config.cwd,
                agent_preset_id=self._config.agent_id,
                permission_mode=self._permission_policy.mode.value,
            )
            self._persisted = False
        else:
            await self._bind_existing_header(writer)
            self._persisted = True

        # 事件流就是会话本身：指向一条已有事件流意味着接着这条对话往下走。
        # 不重建就会一边追加事件、一边用空上下文请求模型，日志与状态从第一轮起就分叉。
        if not self._store.all_messages():
            await self._hydrate_from_log(writer)

        # 加载 Skills / 子 Agent / MCP（必须在 header 绑定 cwd 之后）
        self._load_workspace_extensions()
        if self._auto_load_mcp:
            await self._load_mcp()

        self._initialized = True
        logger.info("QueryEngine initialized (session=%s)", self._session_id)

    async def close(self) -> None:
        """清理资源 —— 排空待写事件、flush 事件流并注销事件观察者。

        从未落盘且没有用户回合的临时会话直接丢弃，不写 header / 事件流。
        """
        await self._close_mcp()
        ephemeral = not self._persisted and self._turn_count == 0
        if ephemeral:
            tasks = list(self._header_persist_tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self._pending_events.clear()
        else:
            await self._persist_permission_mode()
            if self._header_persist_tasks:
                await asyncio.gather(*self._header_persist_tasks, return_exceptions=True)
            if self._transcript_writer is not None:
                await self._flush_events()
                await self._transcript_writer.flush()
        while self._observer_disposers:
            self._observer_disposers.pop()()
        logger.info("QueryEngine closed (session=%s)", self._session_id)

    def apply_model_config(
        self,
        *,
        model: str,
        provider: ProviderConfig | None = None,
        model_context: int | None = None,
    ) -> None:
        """切换后续轮次使用的模型，不重建会话。"""
        self._config = self._config.model_copy(
            update={
                "model": model,
                "provider": provider,
                "model_context": model_context,
            }
        )

    async def __aenter__(self) -> QueryEngine:
        """async with 入口 —— 初始化并返回引擎实例。"""
        await self.initialize()
        return self

    async def __aexit__(self, *args: Any) -> None:
        """async with 出口 —— 清理资源。"""
        await self.close()

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def submit_message(
        self,
        user_input: str,
        *,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[SDKMessage, None]:
        """提交用户消息并 yield SDK 消息流 —— 引擎的核心公开 API。

        CLI 和 Server 适配器都消费这个生成器，区别仅在交付方式。

        内部流程 6 阶段：
        1. 快照配置 + 校验
        2. 添加用户消息到 store + 追加 user/message 事件
        3. 解析 Provider + 构建系统提示（写 request/header 事件）+ 初始化 LoopState
        4. 运行 agentic_loop（内部 AsyncGenerator），逐条追加 assistant/tool/usage 事件
        5. 将 InternalMessage 转换为 SDKMessage
        6. 追加 turn/end 事件并发出 SDKResultMessage（最终结果）
        """
        await self.initialize()  # 确保引擎已初始化

        # 重置取消控制器 —— 上一次查询的 abort() 状态不能污染本次查询
        self._abort_controller = AbortController()

        start_time = time.monotonic()  # 记录开始时间（用于统计耗时）
        self._turn_count += 1
        # 回合之间请求的权限模式在本 turn 生效
        self._commit_pending_permission("turn")
        self._stage_event(EVENT_TURN_START, turn=self._turn_count)

        # user/input —— 用户这一句的「原始输入」，紧跟在 turn/start 之后、step/start 之前。
        # 它是观测/审计资产（原话），不参与 derive；组装好的最终消息用 user/message 表达。
        input_data: dict[str, Any] = {"content": user_input}
        if attachments:
            input_data["attachments"] = attachments
        self._stage_event(EVENT_USER_INPUT, **input_data)

        logger.debug(
            "submit_message: session=%s, turn=%d, input_len=%d",
            self._session_id, self._turn_count, len(user_input),
        )

        # await 落盘 turn/start + user/input —— 确保 --resume 能看到本轮已开始
        await self._flush_events()
        await self._ensure_session_summary(user_input)

        # Phase 1: 快照配置 —— 冻结当前环境变量和门控状态
        query_config = QueryConfig(
            session_id=self._session_id,
            gates=RuntimeGates(),
        )

        # Phase 2: 组装用户消息（模型可见状态），但**不在 loop 前入库**。
        # 由 loop 在首个 step 的管线压缩之后追加进 store，避免它落入压缩边界；
        # user/message 事件由引擎在 step/start 之后（首个 RequestStartEvent）补写。
        user_msg = UserMessage(
            message={
                "role": "user",
                "content": user_input if not attachments else
                    [{"type": "text", "text": user_input}] + attachments,
            }
        )

        # 可选回放用户消息给消费者（Server 场景需要）
        if self._config.replay_user_messages:
            yield SDKUserMessageReplay(
                message=user_msg.message,
                session_id=self._session_id,
                uuid=user_msg.uuid,
            )

        # 解析 Provider：必须有显式 adapter（来自 YAML 目录的 api）
        provider = self._provider_registry.resolve(
            self._config.effective_model, self._config.provider_config_for_api()
        )
        logger.debug("Provider resolved: %s", type(provider).__name__)

        # 构建系统提示: 通过 PromptAssembler 组装（替代旧 _build_system_prompt）。
        # 期间会触发 memory/retrieve（观察者），因此该事件落在 user/input 之后、step/start 之前。
        system_prompt = self._build_system_prompt(user_input)

        # 计算 request/header（initial/resume/change），但不落盘 —— 推迟到首个
        # RequestStartEvent 与 user/message 一起写，保证它们在 step/start 之后。
        pending_header = await self._compute_request_header(system_prompt, provider)

        # 构建初始循环状态（携带权限策略 / 沙箱 / 拒绝追踪 / Ignore）
        tool_context = ToolUseContext(
            cwd=self._config.cwd,
            signal=self._abort_controller.signal,
            session_id=self._session_id,
            permission_checker=self._permission_checker,
            policy=self._permission_policy,
            sandbox=self._sandbox,
            denial_tracker=self._denial_tracker,
            ignore=self._ignore,
            ask_user=self._ask_user,
            tool_registry=self._tool_registry,
        )

        initial_state = LoopState(
            messages=self._store,
            tool_use_context=tool_context,
            auto_compact_tracking=AutoCompactTracking(
                turn_id=str(uuid.uuid4()),       # 当前轮次唯一 ID
                turn_counter=self._turn_count,    # 全局轮次计数
            ),
            turn_count=self._turn_count,
        )

        # Phase 3-4: 运行 agentic loop（内部 AsyncGenerator）
        # 内部消息流: stream events + assistant messages + tool results + terminal
        terminal_reason = None       # 终止原因
        terminal_error = None        # 终止异常
        # 最后的 assistant 消息（用于提取结果文本）
        last_assistant_msg: AssistantMessage | None = None
        # 本轮是否已在 step/start 之后写过 user/message + request/header
        prelude_staged = False

        def _stage_prelude() -> None:
            """在首个 step/start 之后写 user/message（组装好的最终用户信息）与 request/header。

            顺序固定为 step/start → user/message → request/header；loop 只会在
            ``step/start`` 之后 yield ``RequestStartEvent``，此刻同步入队即可保证顺序。
            """
            nonlocal prelude_staged
            if prelude_staged:
                return
            prelude_staged = True
            self._stage_event(EVENT_USER_MESSAGE, content=user_msg.message.get("content"))
            if pending_header is not None:
                header, reason = pending_header
                self._stage_event(EVENT_REQUEST_HEADER, header=header, reason=reason)
                self._last_header = header
                self._header_logged = True

        try:
            async for internal_msg in agentic_loop(
                store=self._store,
                provider=provider,
                tool_registry=self._tool_registry,
                pipeline=self._pipeline,
                middleware_chain=self._middleware_chain,
                stop_hook_executor=self._stop_hook_executor,
                post_sampling_chain=self._post_sampling_chain,
                recovery_chain=self._recovery_chain,
                config=self._config,
                query_config=query_config,
                system_prompt=system_prompt,
                initial_state=initial_state,
                signal=self._abort_controller.signal,
                token_tracker=self._token_tracker,
                event_sink=self._stage_event,
                commit_permission=self._commit_pending_permission,
                turn_user_message=user_msg,
            ):
                # 请求前一致性门禁 —— 此刻 loop 已把本 step 的状态事件全部上报。
                # 先补写 user/message + request/header（放在 step/start 之后），再校验。
                if isinstance(internal_msg, RequestStartEvent):
                    _stage_prelude()
                    # 确保 user/message + request/header 在 LLM 调用前落盘（resume 需要）
                    await self._flush_events()
                    await self._verify_log_matches_state()

                # Phase 5: 将内部消息转换为 SDK 格式
                sdk_msgs = self._to_sdk_messages(internal_msg)
                for sdk_msg in sdk_msgs:
                    yield sdk_msg

                # 流式增量事件（降级模式下只保留合并后的 assistant/message）
                if isinstance(internal_msg, StreamEvent):
                    if self._config.log_assistant_chunks:
                        self._stage_event(
                            EVENT_ASSISTANT_CHUNK,
                            chunk=internal_msg.model_dump(exclude_none=True),
                        )

                # 从 assistant 消息跟踪成本和用量
                if isinstance(internal_msg, AssistantMessage):
                    last_assistant_msg = internal_msg
                    self._stage_event(
                        EVENT_ASSISTANT_MESSAGE,
                        content=internal_msg.message.get("content"),
                    )
                    # 工具调用事件（从 assistant content 的 tool_use blocks 提取）
                    for block in internal_msg.message.get("content", []):
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            self._stage_event(
                                EVENT_TOOL_CALL,
                                call_id=block.get("id"),
                                name=block.get("name"),
                                args=block.get("input"),
                            )
                    usage_dict = internal_msg.message.get("usage", {})
                    if usage_dict:
                        usage = Usage(**usage_dict)
                        # 记录 token 用量（输入/输出/缓存命中/缓存创建）
                        self._token_tracker.record_usage(usage, self._config.effective_model)
                        # 同步记录上下文用量分析（总量/占比 + system/tools/messages 分项）
                        context_usage = self.analyze_context_usage()
                        self._token_tracker.record_context_usage(context_usage)
                        # 投影为 gauge 指标 —— 当前上下文占用与消息数
                        record_gauge(
                            METRIC_CONTEXT_TOKENS,
                            context_usage["total_tokens"],
                            **{
                                TAG_SESSION_ID: self._session_id,
                                TAG_MODEL: self._config.effective_model,
                            },
                        )
                        record_gauge(
                            METRIC_MESSAGE_COUNT,
                            context_usage["message_count"],
                            **{TAG_SESSION_ID: self._session_id},
                        )
                        self._stage_event(
                            EVENT_USAGE,
                            usage=usage_dict,
                            model=self._config.effective_model,
                        )
                    await self._flush_events()

                if isinstance(internal_msg, UserMessage):
                    if internal_msg.tool_use_result:
                        # 工具结果事件（从合成 UserMessage 的 tool_result blocks 提取）
                        for block in internal_msg.message.get("content", []):
                            if (
                                isinstance(block, dict)
                                and block.get("type") == "tool_result"
                            ):
                                self._stage_event(
                                    EVENT_TOOL_RESULT,
                                    call_id=block.get("tool_use_id"),
                                    result=block.get("content"),
                                    is_error=block.get("is_error", False),
                                )
                    else:
                        # 循环合成并入库的用户消息（stop hook 校验错误、截断恢复提示）
                        # —— 同样是模型可见输入，必须进事件流
                        self._stage_event(
                            EVENT_USER_MESSAGE,
                            content=internal_msg.message.get("content"),
                        )
                    await self._flush_events()

                # 检测终止信号: agentic loop 通过 subtype="loop_terminal" 通知结束
                if isinstance(internal_msg, SystemMessage):
                    if internal_msg.subtype == "loop_terminal":
                        terminal_reason = internal_msg.content.replace(
                            "Loop terminated: ", ""
                        )
                        terminal_error = internal_msg.error

        except ReconstructionError as exc:
            # 一致性门禁失败：运行时状态有一次变更没进事件流，日志已不可信。
            # 继续请求只会把偏差滚雪球，所以按 strict 语义终止本轮。
            logger.error("Session log invariant violated: %s", exc)
            terminal_reason = "log_reconstruction_desync"
            terminal_error = {"type": "reconstruction", "message": str(exc)}

        except Exception as exc:
            logger.exception("Query engine error during loop")
            terminal_reason = "error_during_execution"  # 未预期的异常
            terminal_error = {"type": "exception", "message": str(exc)}

        # Phase 6: 发出 SDKResultMessage（最终结果消息）
        elapsed = (time.monotonic() - start_time) * 1000  # 总耗时（毫秒）

        # 确定结果子类型: 正常完成 vs 错误终止
        is_error = terminal_reason not in (None, "COMPLETED")
        reason = terminal_reason or "unknown"
        result_subtype = "success" if not is_error else f"error_{reason.lower()}"

        # 从最后一条 assistant 消息提取结果文本
        result_text = ""
        if last_assistant_msg:
            content = last_assistant_msg.message.get("content", [])
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        result_text = block.get("text", "")
            elif isinstance(content, str):
                result_text = content

        # 结构化输出闭环：配置 json_schema 时尝试把结果文本解析为 JSON
        structured_output = self._parse_structured_output(result_text)

        # 事件流：回合结束（携带终止原因）。
        # 兜底：loop 若在开出任何 step 之前就终止（取消 / max_turns / token 预算耗尽），
        # 从未 yield 过 RequestStartEvent，也从未把用户消息追加进 store，这里补上入库，
        # 并补写 user/message + request/header，使事件流仍能重建出会话状态
        # （否则下一轮门禁会报 desync）。
        if not prelude_staged and not any(
            getattr(m, "uuid", None) == user_msg.uuid
            for m in self._store.all_messages()
        ):
            self._store.append(user_msg)
        _stage_prelude()

        self._stage_event(EVENT_TURN_END, turn=self._turn_count, reason=reason)
        await self._flush_events()

        # yield 最终结果消息 —— 客户端收到此消息即知本轮对话完成
        logger.debug(
            "Query complete: result=%s, elapsed=%.0fms, turns=%d, tokens=%d",
            result_subtype, elapsed, self._turn_count,
            self._token_tracker.get_total_tokens(),
        )
        yield SDKResultMessage(
            subtype=result_subtype,
            is_error=is_error,
            duration_ms=elapsed,
            duration_api_ms=self._token_tracker.get_total_api_duration(),
            num_turns=self._turn_count,
            result=result_text,
            session_id=self._session_id,
            usage=self._token_tracker.get_total_usage(),
            model_usage=self._token_tracker.get_model_usage(),
            context_usage=self._token_tracker.get_context_usage(),
            permission_denials=self._denial_tracker.list(),
            errors=[str(terminal_error)] if terminal_error else [],
            structured_output=structured_output,
        )

    def abort(self, reason: str | None = None) -> None:
        """中止当前查询 —— 设置 AbortSignal 通知循环和工具执行器停止。"""
        self._abort_controller.abort(reason)
        logger.info("Query aborted: %s", reason)

    def clear_session(self) -> None:
        """清空当前会话的消息存储（/clear 命令）。

        清空是一次状态替换，必须进事件流 —— 否则日志重建仍会带出被清掉的历史，
        请求前的一致性门禁会报 desync。事件在下一个 await 点随队列落盘。
        """
        self._store.replace([])
        self._stage_event(
            EVENT_COMPACT_BOUNDARY,
            content="[Session cleared.]",
            metadata={"strategy": "clear"},
            kept=[],
        )

    def set_skill_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用指定 Skill（返回是否命中）。"""
        for skill in self._skills:
            if skill.name == name:
                skill.enabled = enabled
                return True
        return False

    def compact(self, keep_recent: int = 6) -> int:
        """手动压缩（/compact）：保留最近 N 条消息，插入 compact boundary。

        边界事件携带**压缩后的完整窗口**，使 ``derive_messages()`` 能逐字重放出
        压缩后的上下文；事件在下一个 await 点（下一轮 submit_message 或 close）
        随队列落盘。

        Args:
            keep_recent: 保留的最近消息条数。

        Returns:
            被移除的消息条数。
        """
        messages = self._store.all_messages()
        if len(messages) <= keep_recent:
            return 0

        removed = len(messages) - keep_recent
        content = "[Manual compact: earlier conversation removed.]"
        boundary = SystemMessage(
            subtype="compact_boundary",
            content=content,
            compact_metadata={"removed": removed},
        )
        recent = messages[-keep_recent:]
        self._store.replace([boundary] + recent)
        self._stage_event(
            EVENT_COMPACT_BOUNDARY,
            content=content,
            metadata={"strategy": "manual", "removed": removed},
            kept=messages_to_derived(self._store.all_messages()),
        )
        logger.info("Manual compact removed %d messages", removed)
        return removed

    def analyze_context_usage(self) -> dict[str, Any]:
        """分析当前上下文使用情况 —— 总量、占比与系统提示词/工具/消息的分项。

        返回（键含 ``tokens`` 与 ``ratio`` 两套口径）:
          - ``context_window``: 生效窗口（provider.model_context > model_context > 默认值）
          - ``total_tokens`` / ``usage_ratio`` / ``remaining_tokens``
          - ``system_prompt_tokens`` / ``system_prompt_ratio``
          - ``tools_tokens`` / ``tools_count`` / ``tools_ratio``
          - ``messages_tokens`` / ``messages_ratio`` / ``message_count``
          - ``is_auto_compact_threshold`` / ``is_at_blocking_limit``
            （与压缩管线 80% / 95% 阈值同口径）

        只读操作，不修改会话状态；系统提示词复用最近一次组装结果，
        尚未发起请求时按当前配置即时组装。
        """
        messages = [
            m.message for m in self._store.all_messages() if hasattr(m, "message")
        ]
        system_prompt = self._last_system_prompt or self._build_system_prompt()
        tools = self._tool_registry.to_api_schema()
        return analyze_context_usage(
            messages,
            system_prompt=system_prompt,
            tools=tools,
            model_context=self._config.effective_model_context,
        )

    def _parse_structured_output(self, result_text: str) -> Any:
        """配置 json_schema 时把结果文本解析为 JSON（结构化输出闭环）。

        解析失败返回 None（SDK 消费者据此回退到 result 文本）。
        """
        if not self._config.json_schema:
            return None
        import json

        text = (result_text or "").strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
        logger.info("Session cleared (session=%s)", self._session_id)

    # ------------------------------------------------------------------
    # 属性访问器 —— 暴露子系统供外部配置
    # ------------------------------------------------------------------

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def message_store(self) -> MessageStore:
        """当前会话的内存消息状态（事件流重建的对象）。"""
        return self._store

    @property
    def token_tracker(self) -> TokenTracker:
        return self._token_tracker

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry

    @property
    def middleware_chain(self) -> MiddlewareChain:
        return self._middleware_chain

    @property
    def stop_hook_executor(self) -> StopHookExecutor:
        return self._stop_hook_executor

    @property
    def pipeline(self) -> PreprocessPipeline:
        return self._pipeline

    @property
    def permission_policy(self) -> PermissionPolicy:
        return self._permission_policy

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills)

    @property
    def rule_loader(self) -> FileSystemRuleLoader:
        return self._rule_loader

    @property
    def memory_store(self) -> MemoryStore:
        return self._memory_store

    @property
    def memory_manager(self) -> MemoryManager:
        return self._memory_manager

    @property
    def transcript_writer(self) -> TranscriptWriter | None:
        return self._transcript_writer

    @property
    def is_persisted(self) -> bool:
        """是否已把本会话写入事件流（header + 至少一次 append）。"""
        return self._persisted

    @property
    def mcp_runtime(self) -> Any:
        return self._mcp_runtime

    # ------------------------------------------------------------------
    # 事件溯源辅助
    # ------------------------------------------------------------------

    def _register_event_observers(self) -> None:
        """把权限拒绝与记忆读写接到事件流上。

        这两类事件发生在工具执行和记忆系统内部，不经过循环产出的消息流，
        只能由各自的追踪器/管理器回调上报。回调是同步的，与 ``_stage_event``
        的同步入队语义一致，落盘仍在最近的 await 点。
        """

        def on_denial(denial: Denial) -> None:
            self._stage_event(
                EVENT_PERMISSION_DENIAL,
                tool=denial.tool_name,
                rule=denial.reason,
                mode=denial.mode,
                path=denial.input_params.get("path"),
            )

        def on_memory_write(record: MemoryRecord) -> None:
            self._stage_event(
                EVENT_MEMORY_WRITE,
                memory_id=record.memory_id,
                kind=record.kind,
                adopted=record.status == STATUS_ACCEPTED,
                importance=record.importance,
            )

        def on_memory_retrieve(query: str, records: list[MemoryRecord]) -> None:
            self._stage_event(
                EVENT_MEMORY_RETRIEVE,
                query=query,
                memory_ids=[r.memory_id for r in records],
                kinds=sorted({r.kind for r in records}),
                adopted=bool(records),
            )

        self._observer_disposers.extend(
            (
                self._denial_tracker.add_observer(on_denial),
                self._memory_manager.add_write_observer(on_memory_write),
                self._memory_manager.add_retrieve_observer(on_memory_retrieve),
            )
        )

    def _stage_event(self, type_: str, /, *, ignorable: bool = False, **data: Any) -> None:
        """把一条事件放入待写队列。

        同步入队让 ``compact()`` 这类同步 API 也能记事件；队列保证追加顺序
        与调用顺序一致，实际写盘发生在最近的 ``_flush_events()``。

        Args:
            type_: 事件类型（见 session.events 常量）。
            ignorable: 新事件类型标 True，旧版本读日志时允许跳过。
            data: 事件载荷。
        """
        self._pending_events.append(
            SessionEvent.make(0, type_, ignorable=ignorable, **data)
        )
        record_event_metric(type_, data, session_id=self._session_id)

    async def _flush_events(self) -> None:
        """按序把待写事件 append 到事件流。

        整批交给 ``append_events``，JSONL 后端可以把多行打进同一 zstd 帧。
        失败时把本批放回队列头部，避免 await 期间新入队的事件被丢掉或重排。
        新会话第一次 flush 时才写入 SessionHeader。
        """
        if self._transcript_writer is None or not self._pending_events:
            return
        if not self._persisted:
            await self._write_header_to_disk()
        batch = self._pending_events
        self._pending_events = []
        try:
            await self._transcript_writer.append_events(batch)
            self._persisted = True
        except BaseException:
            self._pending_events = list(batch) + self._pending_events
            raise

    async def _verify_log_matches_state(self) -> None:
        """请求前一致性门禁 —— 校验事件流能重建出当前运行时消息状态。

        校验对象是**会话状态**（``MessageStore``），不是发给 LLM 的数组：
        预处理管线在深拷贝上工作，请求数组 = 管线(状态) 是状态的纯函数，
        因此状态可重建即整次请求可重放。任何绕过事件流的状态变更都会在这里暴露。

        Raises:
            ReconstructionError: ``session_invariant="strict"`` 且事件流与状态不一致。
        """
        mode = self._config.session_invariant
        if mode == "off" or self._transcript_writer is None:
            return

        # 门禁要读全量事件，队列里的必须先落盘，否则本 step 的事件还看不见
        await self._flush_events()
        events = await self._transcript_writer.read_events()
        try:
            verify_reconstruction(
                events, messages_to_derived(self._store.all_messages())
            )
        except ReconstructionError as exc:
            record_counter(
                METRIC_SESSION_DESYNC, 1.0, **{TAG_SESSION_ID: self._session_id}
            )
            if mode == "strict":
                raise
            logger.warning("Session log invariant violated (warn mode): %s", exc)

    async def _hydrate_from_log(self, writer: TranscriptWriter) -> None:
        """用事件流重建内存消息状态（初始化时的隐式接续）。

        Args:
            writer: 本会话的事件流写入器。
        """
        events = await writer.read_events()
        if not events:
            return
        self._store.replace(derived_to_messages(derive_messages(events)))
        self._restore_turn_count(events)
        # 接续已有事件流：下次请求写 reason=resume 的 header
        self._header_logged = False
        self._last_header = None
        logger.info(
            "Session state rebuilt from %d events (session=%s)",
            len(events),
            self._session_id,
        )

    async def _compute_request_header(
        self, system_prompt: str, provider: Any
    ) -> tuple[dict[str, Any], str] | None:
        """计算 request/header 快照（initial/resume/change），返回 ``(header, reason)`` 或 None。

        只计算、不落盘 —— 写入时机在首个 ``step/start`` 之后（由 ``submit_message`` 的
        ``_stage_prelude`` 完成），保证「step/start → user/message → request/header」顺序。

        载荷里的 ``config`` 携带可回溯所需的模型提供商信息：提供商标识（protocol 名）、
        协议（adapter）、模型 ID 与上下文窗口大小，配合 system / tools 完整表达
        「模型当时被告诉了什么」。

        Args:
            system_prompt: 本次请求组装好的 system prompt 全文。
            provider: 已解析的 LLM Provider 实例（取其 ``name`` 作为协议标识）。

        Returns:
            ``(header, reason)`` 当需要写一条 request/header；配置未变化返回 None。
        """
        model = self._config.effective_model
        provider_name = getattr(provider, "name", None)
        protocol = (
            self._config.provider.adapter
            if self._config.provider and self._config.provider.adapter
            else provider_name
        )
        header = canonical_header(
            config={
                "model": model,
                "agent_id": self._config.agent_id,
                "provider": provider_name,
                "protocol": protocol,
                # 生效的上下文窗口大小 —— 来自模型配置（model_context），
                # 与压缩管线使用的阈值基准一致
                "context_size": self._config.effective_model_context,
            },
            system=system_prompt,
            tools=self._tool_registry.to_api_schema(),
        )
        baseline = self._last_header
        if baseline is None and self._transcript_writer is not None:
            # 本实例首次请求：以日志中最后一条 header 作为基线，区分 initial / resume
            baseline = fold_request_header(await self._transcript_writer.read_events())
        reason = next_reason(baseline, header, instance_logged=self._header_logged)
        if reason is None:
            return None
        return (header, reason)

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    async def resume_session(
        self,
        session_id: str,
        *,
        skip_system: bool = False,
    ) -> list[Message]:
        """恢复之前的会话 —— 读事件流 + derive 确定性重建消息。

        将当前引擎切换到指定 session_id；引擎自建的默认写入器会重新指向目标会话，
        显式注入的写入器保持不变（由调用方决定读哪条事件流）。

        Args:
            session_id: 要恢复的会话 ID。
            skip_system: 是否过滤临时系统消息（api_retry / local_command）。

        Returns:
            重建出的消息列表（已写入内存 store）。
        """
        self._session_id = session_id

        if self._owns_writer:
            self._transcript_writer = self._create_default_writer()

        writer = self._transcript_writer
        events = await writer.read_events() if writer is not None else []
        messages = derived_to_messages(derive_messages(events))

        # 可选过滤临时系统消息
        if skip_system:
            _ephemeral_subtypes = {"api_retry", "local_command"}
            messages = [
                m for m in messages
                if not (isinstance(m, SystemMessage) and m.subtype in _ephemeral_subtypes)
            ]

        self._store.replace(messages)
        self._restore_turn_count(events)
        # 换实例接续同一条事件流：下次请求写 reason=resume 的 header
        self._header_logged = False
        self._last_header = None
        if writer is not None:
            existing = await writer.read_header()
            if existing is not None:
                await self._bind_existing_header(writer)
                self._persisted = True
            self._load_workspace_extensions()
            if self._auto_load_mcp:
                await self._load_mcp()
        logger.info("Resumed session %s with %d messages", session_id, len(messages))
        return messages

    async def fork_session(
        self,
        boundary_seq: int,
        new_session_id: str,
        *,
        target: TranscriptWriter | None = None,
    ) -> TranscriptWriter:
        """在事件流 ``boundary_seq`` 处剪一刀复制成新会话。

        新会话继承剪切点之前的历史，元信息记录 ``parent_session``。
        当前引擎仍留在原会话上。

        Args:
            boundary_seq: 剪切点（含此 seq）。
            new_session_id: 新会话 ID。
            target: 新会话的事件流写入器；None 时按默认后端为新 ID 创建。

        Returns:
            新会话的事件流写入器。

        Raises:
            RuntimeError: 引擎尚未初始化（没有可读的事件流）。
        """
        if self._transcript_writer is None:
            raise RuntimeError("fork_session requires an initialized engine")

        await self._flush_events()
        destination = target or self._create_writer_for(new_session_id)
        await fork_transcript(
            self._transcript_writer, destination, boundary_seq, new_session_id
        )
        logger.info(
            "Forked session %s at seq=%d into %s",
            self._session_id, boundary_seq, new_session_id,
        )
        return destination

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _create_default_writer(self) -> LocalFileTranscriptWriter:
        """为当前 session_id 创建默认本地事件流写入器。"""
        return self._create_writer_for(self._session_id)

    @staticmethod
    def _create_writer_for(session_id: str) -> LocalFileTranscriptWriter:
        """为指定 session_id 创建默认本地事件流写入器（JSONL / zstd）。"""
        return LocalFileTranscriptWriter(DEFAULT_SESSION_DIR / f"{session_id}.jsonl")

    def _restore_turn_count(self, events: list[SessionEvent]) -> None:
        """从事件流恢复 ``_turn_count``，使 resume 后 ``turn/start`` 接着编号。"""
        self._turn_count = last_turn_number(events)

    def _apply_session_header(self, header: SessionHeader) -> bool:
        """用已有 SessionHeader 约束引擎运行时（cwd / agent），覆盖构造时的 CLI 默认值。

        相对路径（如 ``.``）会收成绝对路径。返回是否改写了 header.cwd（调用方应回写磁盘）。
        """
        rewritten = False
        updates: dict[str, Any] = {}
        if header.cwd:
            absolute = resolve_session_cwd(header.cwd)
            if absolute != header.cwd:
                header.cwd = absolute
                rewritten = True
            updates["cwd"] = absolute
        if header.agent_preset_id:
            updates["agent_id"] = header.agent_preset_id
        self._restore_permission_mode(header.permission_mode)
        if not updates:
            return rewritten
        cwd_changed = "cwd" in updates and updates["cwd"] != self._config.cwd
        self._config = self._config.model_copy(update=updates)
        if cwd_changed:
            self._sandbox = PathSandbox(self._config.cwd)
            self._ignore = IgnoreMatcher.from_workspace(self._config.cwd)
        return rewritten

    async def _bind_existing_header(self, writer: TranscriptWriter) -> None:
        """读取 header、绑定约束；相对 cwd 升级为绝对路径后写回。"""
        existing = await writer.read_header()
        if existing is None:
            return
        rewritten = self._apply_session_header(existing)
        self._session_header = existing
        if rewritten:
            await writer.write_header(existing)
        await self._backfill_session_summary()

    def _restore_permission_mode(self, raw: str) -> None:
        """从 SessionHeader 恢复权限模式，不回写磁盘。"""
        try:
            mode = PermissionMode(normalize_permission_mode(raw))
        except ValueError:
            mode = PermissionMode.DEFAULT
        self._applying_header = True
        try:
            self._permission_policy.set_mode(mode)
        finally:
            self._applying_header = False

    def _on_permission_mode_request(
        self, from_mode: PermissionMode, to_mode: PermissionMode
    ) -> None:
        """用户请求切换权限：入队 ``permission/onchange``，已落盘会话立刻写 header。

        运行时 ``policy.mode`` 仍保持旧值，直到下一次 turn/step 的 ``commit_pending``。
        已保存的会话把 header 先写成目标值，中途退出再 resume 也不会丢切换；
        尚未落盘的临时会话只更新内存，等首条用户消息一并写入。
        """
        self._stage_event(
            EVENT_PERMISSION_ONCHANGE,
            ignorable=True,
            **{"from": from_mode.value, "to": to_mode.value},
        )
        if self._applying_header:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._flush_permission_onchange(to_mode.value))
        self._header_persist_tasks.add(task)
        task.add_done_callback(self._header_persist_tasks.discard)

    def _commit_pending_permission(self, at: str) -> None:
        """在下一次 turn 或 step 边界应用挂起的权限模式，并写 ``permission/changed``。"""
        result = self._permission_policy.commit_pending()
        if result is None:
            return
        from_mode, to_mode = result
        self._stage_event(
            EVENT_PERMISSION_CHANGED,
            ignorable=True,
            at=at,
            **{"from": from_mode.value, "to": to_mode.value},
        )

    def _on_permission_mode_change(self, mode: PermissionMode) -> None:
        """模式真正生效时把值写进 header（restore / commit / 直接 ``set_mode``）。"""
        if self._applying_header:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._persist_permission_mode(mode.value))
        self._header_persist_tasks.add(task)
        task.add_done_callback(self._header_persist_tasks.discard)

    async def _flush_permission_onchange(self, mode: str) -> None:
        """已落盘时把 ``permission/onchange`` 写入事件流，并更新 header。"""
        if self._persisted:
            await self._flush_events()
        await self._persist_permission_mode(mode)

    async def _write_header_to_disk(self) -> None:
        """把内存中的 SessionHeader 写入事件流后端。"""
        writer = self._transcript_writer
        if writer is None:
            return
        header = self._session_header
        if header is None:
            pending = self._permission_policy.pending_mode
            header = make_header(
                self._session_id,
                cwd=self._config.cwd,
                agent_preset_id=self._config.agent_id,
                permission_mode=(pending or self._permission_policy.mode).value,
            )
            self._session_header = header
        await writer.write_header(header)

    async def _persist_permission_mode(self, mode: str | None = None) -> None:
        """把当前（或挂起、或指定）权限模式写入 SessionHeader。

        未指定 ``mode`` 时优先写 ``pending_mode``，避免 ``/permission`` 后直接
        退出、尚未跑下一 turn 时把 header 写回旧的运行时模式。
        临时会话只改内存，等首次用户消息一起落盘。
        """
        writer = self._transcript_writer
        header = self._session_header
        if header is None and writer is not None:
            header = await writer.read_header()
        if header is None:
            return
        if mode is not None:
            next_mode = normalize_permission_mode(mode)
        elif self._permission_policy.pending_mode is not None:
            next_mode = normalize_permission_mode(
                self._permission_policy.pending_mode.value
            )
        else:
            next_mode = normalize_permission_mode(self._permission_policy.mode.value)
        if header.permission_mode == next_mode:
            self._session_header = header
            return
        header.permission_mode = next_mode
        self._session_header = header
        if not self._persisted or writer is None:
            return
        await writer.write_header(header)

    def _load_workspace_extensions(self) -> None:
        """按当前 cwd 加载 Skills / 子 Agent（header 绑定之后、以及 resume 换会话时）。"""
        self._skills = self._skill_loader.load(self._config.cwd)
        if self._skills:
            if self._tool_registry.has("skill"):
                tool = self._tool_registry.find("skill")
                if isinstance(tool, SkillTool):
                    tool.update_skills(self._skills)
            else:
                self._tool_registry.register(SkillTool(self._skills))

        if not (self._register_agent_tool and self._agent_loader is not None):
            return
        from nono_agent.agents import (
            AgentRunner,
            AgentTool,
            create_builtin_agents,
        )

        definitions = create_builtin_agents() + self._agent_loader.load(self._config.cwd)
        if not definitions:
            return
        if self._tool_registry.has("agent"):
            tool = self._tool_registry.find("agent")
            if isinstance(tool, AgentTool):
                tool.update_definitions(definitions)
                tool.bind_base_config(self._config)
            return
        runner = AgentRunner(
            self._provider_registry, self._tool_registry, self._config
        )
        self._tool_registry.register(AgentTool(definitions, runner))

    async def _load_mcp(self) -> None:
        """按当前 cwd 启动 MCP 并把工具注册进表。失败隔离在 runtime 内完成。"""
        from nono_agent.mcp.runtime import start_mcp_runtime

        await self._close_mcp()
        runtime = await start_mcp_runtime(self._config.cwd)
        self._mcp_runtime = runtime
        for tool in runtime.tools:
            if self._tool_registry.has(tool.name):
                self._tool_registry.unregister(tool.name)
            self._tool_registry.register(tool)

    async def _close_mcp(self) -> None:
        runtime = self._mcp_runtime
        self._mcp_runtime = None
        if runtime is None:
            return
        for tool in list(self._tool_registry.all()):
            if tool.name.startswith("mcp__") and self._tool_registry.has(tool.name):
                self._tool_registry.unregister(tool.name)
        await runtime.close()

    async def _ensure_session_summary(self, user_input: str) -> None:
        """若 header 尚无摘要，写入用户第一句话的前 20 字（只写一次）。"""
        writer = self._transcript_writer
        if writer is None or not self._persisted:
            return
        header = self._session_header or await writer.read_header()
        if header is None:
            return
        self._session_header = header
        if header.summary:
            return
        summary = summarize_user_prompt(user_input)
        if not summary:
            return
        header.summary = summary
        await writer.write_header(header)

    async def _backfill_session_summary(self) -> None:
        """接续旧会话时，若 header 没有摘要则从事件流第一句用户输入补写。"""
        writer = self._transcript_writer
        if writer is None:
            return
        header = self._session_header or await writer.read_header()
        if header is None or header.summary:
            self._session_header = header
            return
        prompt = first_user_prompt(await writer.read_events())
        if not prompt:
            return
        header.summary = summarize_user_prompt(prompt)
        self._session_header = header
        await writer.write_header(header)

    def _create_default_assembler(self) -> PromptAssembler:
        """创建默认 PromptAssembler（注册内置 section）。"""
        registry = SectionRegistry()
        for section in create_default_sections():
            registry.register(section)
        return PromptAssembler(registry)

    def _build_system_prompt(self, query: str = "") -> str:
        """通过 PromptAssembler 组装系统提示 —— 替代旧的一次性字符串拼接。

        静态段（identity / tools_guidance）+ 动态段（env_info / rules / memory）。
        custom_system_prompt 覆盖 identity；append_system_prompt 永远追加。
        Memory 经 MemoryManager 检索注入（top-k + token 预算），替代全量 load。
        """
        import os

        rules_text = merge_rules(self._rule_loader.load(self._config.cwd))
        skills_text = "\n".join(
            f"- {s.name}: {s.description}" for s in self._skills if s.enabled
        )
        retrieved = self._memory_manager.retrieve(
            query,
            agent_id=self._config.agent_id,
            k=self._config.memory_retrieval_k,
            token_budget=self._config.memory_retrieval_token_budget,
        )
        memories_text = "\n".join(
            f"- [{m.kind}] {m.content}" for m in retrieved
        )

        ctx = PromptContext(
            model_id=self._config.effective_model,
            cwd=self._config.cwd,
            env=dict(os.environ),
            rules_text=rules_text,
            skills_text=skills_text,
            memories_text=memories_text,
            custom_system_prompt=self._config.custom_system_prompt,
            append_system_prompt=self._config.append_system_prompt,
            output_style=self._config.output_style,
            tools=self._tool_registry.to_api_schema(),
            permission_mode=self._permission_policy.mode.value,
        )

        assembled = self._prompt_assembler.assemble(ctx)
        parts: list[str] = []
        if assembled.full:
            parts.append(assembled.full)
        if self._config.append_system_prompt:
            parts.append(self._config.append_system_prompt)

        system_prompt = "\n\n".join(parts)
        self._last_system_prompt = system_prompt  # 缓存供上下文分析复用
        return system_prompt

    def _to_sdk_messages(self, msg: InternalMessage) -> list[SDKMessage]:
        """将内部消息转换为 SDK 消息格式。

        转换规则：
        - AssistantMessage → SDKAssistantMessage
        - UserMessage → SDKUserMessageReplay（可选）
        - SystemMessage → SDKSystemMessage
        - StreamEvent → SDKStreamEventMessage（可选）
        - TombstoneMessage → 无 SDK 对应（过滤）
        - RequestStartEvent → SDKSystemMessage(subtype="request_start")
        """
        if isinstance(msg, AssistantMessage):
            return [
                SDKAssistantMessage(
                    message=msg.message,
                    session_id=self._session_id,
                    uuid=msg.uuid,
                )
            ]

        if isinstance(msg, UserMessage):
            if self._config.replay_user_messages:
                return [
                    SDKUserMessageReplay(
                        message=msg.message,
                        session_id=self._session_id,
                        uuid=msg.uuid,
                    )
                ]
            return []

        if isinstance(msg, SystemMessage):
            return [
                SDKSystemMessage(
                    subtype=msg.subtype,
                    session_id=self._session_id,
                    uuid=msg.uuid,
                    compact_metadata=msg.compact_metadata,
                    error=msg.error.get("message") if msg.error else None,
                    content=msg.content if msg.content else None,
                )
            ]

        if isinstance(msg, ToolUseSummaryMessage):
            return [
                SDKToolUseSummary(
                    summary=msg.summary,
                    preceding_tool_use_ids=msg.preceding_tool_use_ids,
                    session_id=self._session_id,
                    uuid=msg.uuid,
                )
            ]

        if isinstance(msg, TombstoneMessage):
            # Tombstone 无 SDK 对应格式，仅内部使用
            return []

        # Stream events
        if isinstance(msg, StreamEvent):
            if self._config.include_partial_messages:
                return [
                    SDKStreamEventMessage(
                        event=msg.model_dump(),
                        session_id=self._session_id,
                    )
                ]
            return []

        # RequestStartEvent
        if isinstance(msg, RequestStartEvent):
            return [
                SDKSystemMessage(
                    subtype="request_start",
                    session_id=self._session_id,
                )
            ]

        return []

    def _create_default_pipeline(self) -> PreprocessPipeline:
        """创建默认预处理管线 —— 4 个内置 stage + 可选 ContextCollapse。"""
        pipeline = PreprocessPipeline()
        pipeline.register(ToolResultBudgetStage())  # priority=10: 截断超长工具结果
        pipeline.register(SnipCompactStage())        # priority=20: 零成本滑动窗口丢弃
        pipeline.register(MicroCompactStage())       # priority=110: 剥离旧 thinking + 截断长文本

        # ContextCollapse（LLM 摘要折叠）默认未启用；配置开关开启后注入
        if self._config.context_collapse_enabled:
            from nono_agent.pipeline.context_collapse import ContextCollapseStage
            from nono_agent.pipeline.summarizer import ProviderSummarizer

            summary_model = self._config.context_collapse_model or self._config.effective_model
            summarizer = ProviderSummarizer(
                provider_factory=lambda: self._provider_registry.resolve(
                    summary_model, self._config.provider_config_for_api()
                ),
                model=summary_model,
            )
            pipeline.register(ContextCollapseStage(summarizer=summarizer))

        pipeline.register(AutoCompactStage())        # priority=200: 安全网（80%/95% 阈值）
        return pipeline
