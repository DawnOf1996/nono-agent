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

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from nono_agent.core.config import EngineConfig, QueryConfig, RuntimeGates
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
from nono_agent.memory.store import FileSystemMemoryStore
from nono_agent.observability.cost_tracker import CostTracker, estimate_cost
from nono_agent.permissions.denial import DenialTracker
from nono_agent.permissions.policy import PermissionPolicy
from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
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
from nono_agent.session.store import MessageStore
from nono_agent.session.transcript import LocalFileTranscriptWriter, TranscriptWriter
from nono_agent.skills.loader import FileSystemSkillLoader
from nono_agent.skills.models import Skill
from nono_agent.skills.skill_tool import SkillTool
from nono_agent.tools.context import PermissionChecker, ToolUseContext
from nono_agent.tools.registry import ToolRegistry
from nono_agent.utils.abort import AbortController

logger = logging.getLogger(__name__)

# 用于反序列化 Message 判别联合类型的 TypeAdapter
_message_adapter: TypeAdapter[Message] = TypeAdapter(Message)

# 默认会话文件存储目录
DEFAULT_SESSION_DIR = Path.home() / ".nono-agent" / "sessions"


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
        memory_store: FileSystemMemoryStore | None = None,
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
        self._cost_tracker = CostTracker()       # 成本跟踪器
        self._abort_controller = AbortController()  # 取消控制器
        self._transcript_writer = transcript_writer  # 磁盘持久化写入器
        self._permission_checker = permission_checker  # 权限检查器（CLI/Server 注入）

        # 权限体系（Phase 1）：策略 + 拒绝追踪 + 路径沙箱 + Ignore
        self._permission_policy = permission_policy or PermissionPolicy()
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
        self._memory_store = memory_store or FileSystemMemoryStore()

        # AskUserQuestion 通道（Phase 1 CLI / Phase 3 Server）
        self._ask_user = ask_user

        # 会话信息
        self._session_id = config.session_id
        self._turn_count = 0

    # ------------------------------------------------------------------
    # 生命周期管理
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """初始化引擎 —— 必须在 submit_message 前调用。

        自动调用（通过 async with 或 submit_message 内部），
        幂等设计，多次调用无副作用。
        """
        if self._initialized:
            return

        # 设置 transcript 写入器（延迟初始化，使用 session_id 确定存储路径）
        self._transcript_writer = self._transcript_writer or self._create_default_writer()

        # 加载 Skills 并注册 SkillTool（Phase 2）
        self._skills = self._skill_loader.load(self._config.cwd)
        if self._skills and not self._tool_registry.has("skill"):
            self._tool_registry.register(SkillTool(self._skills))

        # 加载子 Agent 定义并注册 AgentTool（Phase 3）
        if self._register_agent_tool and self._agent_loader is not None:
            from nono_agent.agents import (
                AgentRunner,
                AgentTool,
                create_builtin_agents,
            )

            definitions = create_builtin_agents() + self._agent_loader.load(self._config.cwd)
            if definitions and not self._tool_registry.has("agent"):
                runner = AgentRunner(
                    self._provider_registry, self._tool_registry, self._config
                )
                self._tool_registry.register(AgentTool(definitions, runner))

        self._initialized = True
        logger.info("QueryEngine initialized (session=%s)", self._session_id)

    async def close(self) -> None:
        """清理资源 —— flush 待写入的 transcript 数据。"""
        await self._transcript_writer.flush() if self._transcript_writer else None
        logger.info("QueryEngine closed (session=%s)", self._session_id)

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

        内部流程 7 阶段：
        1. 快照配置 + 校验
        2. 添加用户消息到 store + 写入 transcript
        3. 解析 Provider + 构建系统提示 + 初始化 LoopState
        4. 运行 agentic_loop（内部 AsyncGenerator）
        5. 将 InternalMessage 转换为 SDKMessage
        6. 持久化 transcript
        7. 发出 SDKResultMessage（最终结果）
        """
        await self.initialize()  # 确保引擎已初始化

        # 重置取消控制器 —— 上一次查询的 abort() 状态不能污染本次查询
        self._abort_controller = AbortController()

        start_time = time.monotonic()  # 记录开始时间（用于统计耗时）
        self._turn_count += 1

        logger.debug(
            "submit_message: session=%s, turn=%d, input_len=%d",
            self._session_id, self._turn_count, len(user_input),
        )

        # Phase 1: 快照配置 —— 冻结当前环境变量和门控状态
        query_config = QueryConfig(
            session_id=self._session_id,
            gates=RuntimeGates(),
        )

        # Phase 2: 添加用户消息到 store
        user_msg = UserMessage(
            message={
                "role": "user",
                "content": user_input if not attachments else
                    [{"type": "text", "text": user_input}] + attachments,
            }
        )
        self._store.append(user_msg)

        # 写入 transcript（await —— 确保 --resume 时可用）
        if self._transcript_writer:
            await self._transcript_writer.record_append(user_msg)

        # 可选回放用户消息给消费者（Server 场景需要）
        if self._config.replay_user_messages:
            yield SDKUserMessageReplay(
                message=user_msg.message,
                session_id=self._session_id,
                uuid=user_msg.uuid,
            )

        # 解析 Provider: 根据 model ID 前缀路由到对应的 LLM 实现
        provider = self._provider_registry.resolve(
            self._config.model, self._config.provider
        )
        logger.debug("Provider resolved: %s", type(provider).__name__)

        # 构建系统提示: 通过 PromptAssembler 组装（替代旧 _build_system_prompt）
        system_prompt = self._build_system_prompt()

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
                cost_tracker=self._cost_tracker,
            ):
                # Phase 5: 将内部消息转换为 SDK 格式
                sdk_msgs = self._to_sdk_messages(internal_msg)
                for sdk_msg in sdk_msgs:
                    yield sdk_msg

                # 从 assistant 消息跟踪成本和用量
                if isinstance(internal_msg, AssistantMessage):
                    last_assistant_msg = internal_msg
                    usage_dict = internal_msg.message.get("usage", {})
                    if usage_dict:
                        usage = Usage(**usage_dict)
                        # 记录 token 用量
                        self._cost_tracker.record_usage(usage, self._config.effective_model)
                        # 估算美元费用
                        cost = estimate_cost(usage, self._config.effective_model)
                        self._cost_tracker.record_cost(cost)  # 记录累计成本

                    # 持久化 assistant 消息（fire-and-forget，不阻塞主循环）
                    if self._transcript_writer:
                        await self._transcript_writer.record_append(internal_msg)

                # 检测终止信号: agentic loop 通过 subtype="loop_terminal" 通知结束
                if isinstance(internal_msg, SystemMessage):
                    if internal_msg.subtype == "loop_terminal":
                        terminal_reason = internal_msg.content.replace(
                            "Loop terminated: ", ""
                        )
                        terminal_error = internal_msg.error

        except Exception as exc:
            logger.exception("Query engine error during loop")
            terminal_reason = "error_during_execution"  # 未预期的异常
            terminal_error = {"type": "exception", "message": str(exc)}

        # Phase 6: 持久化完整 transcript（作为最终快照）
        if self._transcript_writer:
            await self._transcript_writer.record(self._store.all_messages())

        # Phase 7: 发出 SDKResultMessage（最终结果消息）
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

        # yield 最终结果消息 —— 客户端收到此消息即知本轮对话完成
        logger.debug(
            "Query complete: result=%s, elapsed=%.0fms, turns=%d, cost=$%.4f",
            result_subtype, elapsed, self._turn_count,
            self._cost_tracker.get_total_cost(),
        )
        yield SDKResultMessage(
            subtype=result_subtype,
            is_error=is_error,
            duration_ms=elapsed,
            duration_api_ms=self._cost_tracker.get_total_api_duration(),
            num_turns=self._turn_count,
            result=result_text,
            session_id=self._session_id,
            total_cost_usd=self._cost_tracker.get_total_cost(),
            usage=self._cost_tracker.get_total_usage(),
            model_usage=self._cost_tracker.get_model_usage(),
            permission_denials=self._denial_tracker.list(),
            errors=[str(terminal_error)] if terminal_error else [],
            structured_output=structured_output,
        )

    def abort(self, reason: str | None = None) -> None:
        """中止当前查询 —— 设置 AbortSignal 通知循环和工具执行器停止。"""
        self._abort_controller.abort(reason)
        logger.info("Query aborted: %s", reason)

    def clear_session(self) -> None:
        """清空当前会话的消息存储（/clear 命令）。"""
        self._store.replace([])

    def set_skill_enabled(self, name: str, enabled: bool) -> bool:
        """启用/禁用指定 Skill（返回是否命中）。"""
        for skill in self._skills:
            if skill.name == name:
                skill.enabled = enabled
                return True
        return False

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
    def cost_tracker(self) -> CostTracker:
        return self._cost_tracker

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

    # ------------------------------------------------------------------
    # 会话管理
    # ------------------------------------------------------------------

    async def resume_session(
        self,
        session_id: str,
        *,
        skip_system: bool = False,
    ) -> list[Message]:
        """恢复之前的会话 —— 通过 TranscriptWriter 协议统一读取。

        将当前引擎切换到指定 session_id（包括 writer 和 SDK 消息的 session_id），
        使得后续的读写都走同一个 transcript 存储。

        Args:
            session_id: 要恢复的会话 ID
            skip_system: 是否过滤临时系统消息（api_retry / local_command）
        """
        # 切换到目标 session_id
        self._session_id = session_id

        # 重建 writer 指向目标会话文件
        self._transcript_writer = self._create_default_writer()

        # 通过协议统一读取 → 反序列化
        raw_messages = await self._transcript_writer.read()
        messages: list[Message] = []
        for raw in raw_messages:
            try:
                msg = _message_adapter.validate_python(raw)
                messages.append(msg)
            except Exception:
                continue  # 跳过无法反序列化的消息

        # 可选过滤临时系统消息
        if skip_system:
            _ephemeral_subtypes = {"api_retry", "local_command"}
            messages = [
                m for m in messages
                if not (isinstance(m, SystemMessage) and m.subtype in _ephemeral_subtypes)
            ]

        self._store.replace(messages)
        logger.info("Resumed session %s with %d messages", session_id, len(messages))
        return messages

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _create_default_writer(self) -> LocalFileTranscriptWriter:
        """为当前 session_id 创建默认本地文件 TranscriptWriter。"""
        session_file = DEFAULT_SESSION_DIR / f"{self._session_id}.jsonl"
        return LocalFileTranscriptWriter(session_file)

    def _create_default_assembler(self) -> PromptAssembler:
        """创建默认 PromptAssembler（注册内置 section）。"""
        registry = SectionRegistry()
        for section in create_default_sections():
            registry.register(section)
        return PromptAssembler(registry)

    def _build_system_prompt(self) -> str:
        """通过 PromptAssembler 组装系统提示 —— 替代旧的一次性字符串拼接。

        静态段（identity / tools_guidance）+ 动态段（env_info / rules）。
        custom_system_prompt 覆盖 identity；append_system_prompt 永远追加。
        """
        import os

        rules_text = merge_rules(self._rule_loader.load(self._config.cwd))
        skills_text = "\n".join(
            f"- {s.name}: {s.description}" for s in self._skills if s.enabled
        )
        memories_text = "\n".join(
            f"- [{m.type}] {m.content}" if m.type else f"- {m.content}"
            for m in self._memory_store.load()
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

        return "\n\n".join(parts)

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
                    summary_model, self._config.provider
                ),
                model=summary_model,
            )
            pipeline.register(ContextCollapseStage(summarizer=summarizer))

        pipeline.register(AutoCompactStage())        # priority=200: 安全网（80%/95% 阈值）
        return pipeline
