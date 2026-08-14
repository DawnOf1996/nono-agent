"""Agentic Loop —— 代理的核心 while(true) 主循环。

本模块实现了 agent 的核心循环，负责：
1. 调用 LLM API（流式）
2. 累积流式事件和内容块
3. 组装 AssistantMessage 并执行工具调用
4. 决策是继续（Transition）还是终止（Terminal）
5. 通过 RecoveryChain 处理错误恢复

整体架构：
  agentic_loop() 是内部 AsyncGenerator，产出 InternalMessage；
  外层 QueryEngine 接收后转换为 SDKMessage 格式给消费者。

  主循环 7 阶段流程：
  Phase 1: 预处理消息 (PreprocessPipeline)
  Phase 2: 中间件预处理 (MiddlewareChain)
  Phase 3: LLM API 调用（流式）
  Phase 4: 组装 AssistantMessage
  Phase 5: Post-sampling 钩子
  Phase 6: Stop hooks（校验/拦截）
  Phase 7: 工具执行 + 转移决策
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from nono_agent.core.config import EngineConfig, QueryConfig
from nono_agent.core.state import (
    LoopState,
    TerminalReason,
    Transition,
    TransitionReason,
)
from nono_agent.core.types import (
    AssistantMessage,
    InternalMessage,
    Message,
    RequestStartEvent,
    StreamEvent,
    SystemMessage,
    ToolResult,
    ToolUseBlock,
    Usage,
    UserMessage,
)
from nono_agent.hooks.middleware import MiddlewareChain
from nono_agent.hooks.post_sampling import PostSamplingChain
from nono_agent.hooks.stop_hooks import StopHookExecutor
from nono_agent.observability.cost_tracker import CostTracker
from nono_agent.observability.metrics import METRIC_API_REQUESTS, record_counter
from nono_agent.pipeline.base import PipelineContext, PreprocessPipeline
from nono_agent.providers.base import FallbackTriggeredError
from nono_agent.recovery import max_output_tokens, prompt_too_long
from nono_agent.recovery.chain import RecoveryAction, RecoveryChain, RecoveryDecision
from nono_agent.session.store import MessageStore
from nono_agent.tools.executor import StreamingToolExecutor
from nono_agent.tools.registry import ToolRegistry
from nono_agent.utils.abort import AbortSignal
from nono_agent.utils.tokens import (
    estimate_messages_tokens,
)

logger = logging.getLogger(__name__)


async def agentic_loop(
    *,
    store: MessageStore,
    # Protocol 的 stream 以 `...` 为 body，mypy 视作协程而非 async generator；
    # 此处以 Any 标注，避免误判 async for 迭代。
    provider: Any,
    tool_registry: ToolRegistry,
    pipeline: PreprocessPipeline,
    middleware_chain: MiddlewareChain,
    stop_hook_executor: StopHookExecutor,
    post_sampling_chain: PostSamplingChain,
    recovery_chain: RecoveryChain,
    config: EngineConfig,
    query_config: QueryConfig,
    system_prompt: str,
    initial_state: LoopState,
    signal: AbortSignal,
    cost_tracker: CostTracker | None = None,
) -> AsyncGenerator[InternalMessage, None]:
    """主 Agentic 循环 —— while(True) 驱动的代理核心。

    每轮迭代代表一次 LLM 调用 + 工具执行的完整循环，
    产出 InternalMessage 供外层 QueryEngine 转换和消费。

    参数说明：
    - store: 消息存储（MessageStore），负责跨轮次的消息管理
    - provider: LLM Provider，提供流式 API 调用
    - tool_registry: 工具注册表，提供工具 Schema 和执行查找
    - pipeline: 预处理管线，在每轮 API 调用前压缩上下文
    - middleware_chain: 中间件链，提供生命周期钩子
    - stop_hook_executor: Stop hook 执行器，支持外部校验/拦截
    - post_sampling_chain: 采样后钩子链，如工具摘要生成
    - recovery_chain: 恢复链，API 错误时的智能重试决策
    - config: 引擎配置（不可变）
    - query_config: 查询快照（不可变）
    - system_prompt: 系统提示词
    - initial_state: 初始循环状态
    - signal: 取消信号（asyncio.Event 封装）

    这是内部生成器 —— 外层 QueryEngine 负责：
    - SDK 消息格式化
    - Transcript 持久化
    - 成本跟踪聚合
    """
    state = initial_state  # 当前循环状态（每轮迭代后构造新实例）
    current_model = config.effective_model  # 运行时当前模型（fallback 切换后可改变）
    used_fallback = False

    while True:
        # —— 前置检查: 用户取消 ——
        if signal.aborted:
            yield _make_terminal(
                store, state, TerminalReason.ABORTED_STREAMING
            )
            return

        # —— 前置检查: 最大轮次限制 ——
        if config.max_turns is not None and state.turn_count > config.max_turns:
            yield _make_terminal(
                store, state, TerminalReason.MAX_TURNS
            )
            return

        # —— 前置检查: 预算门控（美元 / token 预算在 loop 内生效）——
        if cost_tracker is not None:
            budget_reason = _check_budget(config, cost_tracker)
            if budget_reason is not None:
                yield _make_terminal(store, state, budget_reason)
                return

        # ------------------------------------------------------------------
        # Phase 1: 预处理消息 (PreprocessPipeline)
        # 将消息通过 5 级管线压缩:
        # ToolResultBudget → SnipCompact → MicroCompact → ContextCollapse → AutoCompact
        # 目的: 确保发给 LLM 的消息不超过上下文窗口限制
        # ------------------------------------------------------------------
        # 取边界后的消息快照（compact boundary 之后的消息）
        messages = store.snapshot_after_boundary()
        # 获取所有工具的 API Schema（用于 LLM function calling）
        api_tools = tool_registry.to_api_schema()

        logger.debug(
            "Turn %d: %d messages, %d tools, model=%s",
            state.turn_count, len(messages), len(api_tools), current_model,
        )

        # 构造管线上下文: 模型 ID + 估算 token 数
        pipeline_ctx = PipelineContext(
            model_id=current_model,
            estimated_tokens=estimate_messages_tokens(
                [m.message for m in messages if hasattr(m, "message")]
            ),
        )

        # 执行预处理管线，可能返回压缩后的消息
        pipeline_result = await pipeline.execute(messages, pipeline_ctx)
        messages = pipeline_result.messages

        # 如果管线创建了压缩边界，清理边界前的旧消息
        if pipeline_result.compact_boundary_created:
            store.replace(messages)
            store.gc_before_boundary()

        # ------------------------------------------------------------------
        # Phase 2: 中间件预处理 (MiddlewareChain)
        # 中间件可以修改消息、系统提示、工具列表（如注入额外的上下文或工具）
        # ------------------------------------------------------------------
        messages, system_prompt, api_tools = (
            await middleware_chain.run_before_api_call(
                messages=messages,
                system_prompt=system_prompt,
                tools=api_tools,
                state=state,
            )
        )

        # ------------------------------------------------------------------
        # Phase 3: LLM API 调用（流式）
        # 先发出 RequestStartEvent 信号通知 UI 开始“思考”，
        # 然后通过 provider.stream() 流式接收 LLM 响应。
        # 流式事件序列:
        # message_start -> content_block_start/delta/stop -> message_delta -> message_stop
        # ------------------------------------------------------------------
        yield RequestStartEvent()  # 通知消费者 API 请求开始
        record_counter(METRIC_API_REQUESTS, model=current_model)

        # 累积流式事件的状态
        assistant_content: list[dict[str, Any]] = []  # 累积的 assistant content blocks
        usage = Usage()                                # 累积的 token 用量
        stop_reason: str | None = None                 # 停止原因 (end_turn/tool_use/max_tokens)
        api_error: str | None = None                   # API 错误信息

        try:
            # 调用 provider 的流式 API，每次 yield 一个 StreamEvent 或 AssistantMessage
            async for event in provider.stream(
                messages=messages,
                system_prompt=system_prompt,
                tools=api_tools,
                thinking_config=config.thinking_config,
                model=current_model,
                fallback_model=config.fallback_model,
                max_tokens=state.max_output_tokens_override,
                signal=signal,
                json_schema=config.json_schema,
            ):
                # 将流式事件透传给消费者（CLI/Server 适配器）
                if isinstance(event, StreamEvent):
                    yield event

                    # 根据事件类型累积 assistant 内容和使用量
                    if hasattr(event, "type"):
                        if event.type == "content_block_start":
                            # 新内容块开始: 添加到 content 列表
                            block = getattr(event, "content_block", {})
                            if isinstance(block, dict):
                                assistant_content.append({**block})
                        elif event.type == "content_block_delta":
                            # 内容块增量: 将 delta 合并到当前块
                            delta = getattr(event, "delta", {})
                            idx = getattr(event, "index", -1)
                            if isinstance(delta, dict) and 0 <= idx < len(assistant_content):
                                _merge_delta(assistant_content[idx], delta)
                        elif event.type == "content_block_stop":
                            pass  # 内容块完成，内容已在 delta 中累积
                        elif event.type == "message_delta":
                            # 消息级增量: 包含 stop_reason 和输出 token 数
                            stop_reason = event.delta.get("stop_reason")
                            if event.usage:
                                usage = usage + Usage(**event.usage)
                        elif event.type == "message_start":
                            # 消息开始: 包含输入 token 数
                            if event.message.get("usage"):
                                msg_usage = event.message["usage"]
                                usage = Usage(
                                    input_tokens=msg_usage.get("input_tokens", 0),
                                    cache_read_input_tokens=msg_usage.get(
                                        "cache_read_input_tokens", 0
                                    ),
                                    cache_creation_input_tokens=msg_usage.get(
                                        "cache_creation_input_tokens", 0
                                    ),
                                )

                elif isinstance(event, AssistantMessage):
                    # 某些 provider 直接 yield 完整的 AssistantMessage（非流式场景）
                    assistant_content = event.message.get("content", [])
                    if event.message.get("usage"):
                        usage = Usage(**event.message["usage"])
                    stop_reason = event.message.get("stop_reason")

        except FallbackTriggeredError as exc:
            # 主模型过载 → 切换到 fallback_model 重试（仅一次）
            if config.fallback_model and not used_fallback:
                used_fallback = True
                current_model = exc.fallback_model
                logger.warning(
                    "Falling back from %s to %s", exc.original_model, exc.fallback_model
                )
                yield SystemMessage(
                    subtype="api_retry",
                    content=(
                        f"Model {exc.original_model} unavailable, "
                        f"falling back to {exc.fallback_model}..."
                    ),
                )
                continue
            api_error = str(exc)
            yield _make_terminal(store, state, TerminalReason.MODEL_ERROR, error=exc)
            return

        except Exception as exc:
            api_error = str(exc)
            logger.exception("API call failed")

            # API 调用失败 —— 尝试通过 RecoveryChain 恢复
            error_type = _classify_error(exc)  # 分类错误类型
            decision = recovery_chain.decide(error_type, state)  # 决策恢复策略

            recovery_result = await _apply_recovery(
                decision, store, state, messages
            )
            if recovery_result is not None:
                # 恢复成功 —— 产生了新的 Transition，用 continue 重试
                transition, new_state = recovery_result
                state = new_state
                yield SystemMessage(
                    subtype="api_retry",
                    content=f"Retrying after {error_type} error...",
                )
                continue  # 重试本轮

            # 恢复失败 —— 终止循环
            yield _make_terminal(
                store, state, TerminalReason.MODEL_ERROR, error=exc
            )
            return

        # ------------------------------------------------------------------
        # Phase 4: 组装 AssistantMessage
        # 将流式事件累积的内容块、usage、stop_reason 组装为完整的 AssistantMessage
        # ------------------------------------------------------------------
        # 调试日志: 输出累积的内容块类型摘要
        block_types = [b.get("type", "?") for b in assistant_content]
        logger.debug(
            "AssistantMessage: blocks=%s, stop_reason=%s, usage=%s",
            block_types, stop_reason, usage.model_dump(),
        )

        # 组装完整的 assistant 消息
        assistant_msg = AssistantMessage(
            message={
                "role": "assistant",
                "content": assistant_content,
                "usage": usage.model_dump(),
                "stop_reason": stop_reason,
            },
            api_error=api_error,
        )

        # 中间件后处理: 可以检查/修改 assistant 消息
        assistant_msg = await middleware_chain.run_after_api_call(
            assistant_message=assistant_msg,
            state=state,
        )

        # 将 assistant 消息存入 store 并 yield 给消费者
        store.append(assistant_msg)
        yield assistant_msg

        # ------------------------------------------------------------------
        # Phase 5: Post-sampling 钩子
        # 在工具执行前运行，典型场景: 当工具调用多时启动小模型摘要任务
        # ------------------------------------------------------------------
        # 提取 tool_use blocks，并运行 post-sampling 钩子
        tool_blocks = _extract_tool_use_blocks(assistant_msg)
        if tool_blocks:
            logger.debug(
                "Tool use detected: %d tools — %s",
                len(tool_blocks),
                [(b.name, b.id) for b in tool_blocks],
            )
        assistant_msg = await post_sampling_chain.run(
            assistant_msg, tool_blocks, {"model_id": current_model}
        )

        # ------------------------------------------------------------------
        # Phase 6: Stop hooks（外部校验/拦截）
        # Stop hook 可以在 LLM 响应后检查内容是否合法：
        # - blocking: 有错误但可重试（注入错误信息让 LLM 修正）
        # - preventing: 明确阻止继续（直接终止）
        # ------------------------------------------------------------------
        # 执行 stop hooks
        stop_result = await stop_hook_executor.execute(
            assistant_msg,
            {
                "cwd": config.cwd,
                "session_id": query_config.session_id,
                "turn_count": state.turn_count,
            },
        )

        if stop_result.is_preventing:
            # Stop hook 明确阻止继续（如安全拦截）—— 直接终止
            yield _make_terminal(
                store, state, TerminalReason.STOP_HOOK_PREVENTED
            )
            return

        if stop_result.is_blocking:
            # Stop hook 有阻塞错误 —— 注入错误信息让模型修正，然后 continue
            error_text = "\n".join(stop_result.blocking_errors)
            blocking_msg = UserMessage(
                message={
                    "role": "user",
                    "content": f"[Validation errors]\n{error_text}",
                },
                is_meta=True,  # 引擎合成的消息，不是真实用户输入
            )
            store.append(blocking_msg)
            yield blocking_msg

            transition = Transition(
                reason=TransitionReason.STOP_HOOK_BLOCKING,
                metadata={"errors": stop_result.blocking_errors},
            )

            # 中间件可以否决转移（返回 None 表示终止）
            vetoed = await middleware_chain.run_on_transition(
                transition=transition, state=state
            )
            if vetoed is None:  # 中间件否决，终止循环
                yield _make_terminal(
                    store, state, TerminalReason.HOOK_STOPPED
                )
                return

            # 构造新的 LoopState（frozen=True，必须构造新实例）
            state = LoopState(
                messages=store,
                tool_use_context=state.tool_use_context,
                auto_compact_tracking=state.auto_compact_tracking,
                max_output_tokens_recovery_count=0,  # 正常转移重置
                has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                max_output_tokens_override=state.max_output_tokens_override,
                turn_count=state.turn_count + 1,  # 递增轮次
                transition=transition,
            )
            continue  # 继续下一轮迭代

        # ------------------------------------------------------------------
        # Phase 7: 工具执行 + 转移决策
        # 如果 assistant 消息中没有 tool_use blocks，说明 LLM 已完成（end_turn）
        # ------------------------------------------------------------------
        if not tool_blocks:
            # 无工具调用 —— 模型已完成，终止循环
            yield _make_terminal(
                store, state, TerminalReason.COMPLETED
            )
            return

        # 有工具调用 —— 执行工具
        logger.info("Executing %d tool(s): %s", len(tool_blocks), [b.name for b in tool_blocks])
        executor = StreamingToolExecutor(tool_registry)
        tool_results: list[ToolResult] = []

        # 流式执行工具: 并发安全工具并行，非安全工具顺序
        async for exec_result in executor.execute(
            tool_blocks, state.tool_use_context
        ):
            # 中间件可以修改工具结果
            result = await middleware_chain.run_on_tool_result(
                tool_name=exec_result.tool_name,
                tool_use_id=exec_result.tool_use_id,
                result=exec_result.result,
                state=state,
            )
            # 确保 tool_use_id 与 ToolUseBlock.id 一致（工具 execute() 可能返回空值）
            result.tool_use_id = exec_result.tool_use_id
            tool_results.append(result)

            logger.debug(
                "Tool '%s' (id=%s) completed in %.0fms, is_error=%s",
                exec_result.tool_name, exec_result.tool_use_id,
                exec_result.duration_ms, exec_result.result.is_error,
            )

            # 为每个工具完成发出进度通知
            yield SystemMessage(
                subtype="tool_result",
                content=f"Tool '{exec_result.tool_name}' completed "
                        f"({exec_result.duration_ms:.0f}ms)",
            )

        # 工具执行完成后检查取消信号
        if signal.aborted:
            # 注入占位 tool_result —— 确保每个 tool_use 都有对应的 tool_result，
            # 避免下次查询时 API 报 "tool call result does not follow tool call"
            completed_ids = {r.tool_use_id for r in tool_results}
            for tb in tool_blocks:
                if tb.id not in completed_ids:
                    tool_results.append(ToolResult(
                        tool_use_id=tb.id,
                        content="[Aborted by user]",
                        is_error=True,
                        tool_name=tb.name,
                    ))
            tool_result_content = [
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_use_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
                for r in tool_results
            ]
            store.append(UserMessage(
                message={"role": "user", "content": tool_result_content},
                tool_use_result="aborted",
            ))

            yield _make_terminal(
                store, state, TerminalReason.ABORTED_TOOLS
            )
            return

        # 将工具结果包装为 UserMessage（API 协议要求 tool_result 在 user role 中）
        tool_result_content = [
            {
                "type": "tool_result",
                "tool_use_id": r.tool_use_id,
                "content": r.content,
                "is_error": r.is_error,
            }
            for r in tool_results
        ]

        # 构建 tool result 消息并存入 store
        tool_result_msg = UserMessage(
            message={
                "role": "user",
                "content": tool_result_content,
            },
            tool_use_result="completed",
        )
        store.append(tool_result_msg)
        yield tool_result_msg

        # 决策转移: 工具已完成，继续下一轮
        transition = Transition(reason=TransitionReason.NEXT_TURN)
        logger.debug("Transition: NEXT_TURN, next turn will be %d", state.turn_count + 1)

        # 中间件可以否决转移（返回 None 表示终止）
        vetoed = await middleware_chain.run_on_transition(
            transition=transition, state=state
        )
        if vetoed is None:  # 中间件否决，终止循环
            yield _make_terminal(
                store, state, TerminalReason.HOOK_STOPPED
            )
            return

        # 构造新的 LoopState（frozen=True，每次转移必须构造新实例）
        state = LoopState(
            messages=store,
            tool_use_context=state.tool_use_context,
            auto_compact_tracking=state.auto_compact_tracking,
            max_output_tokens_recovery_count=0,  # 正常 next_turn 重置恢复计数
            has_attempted_reactive_compact=False,  # 正常 next_turn 重置压缩防护
            max_output_tokens_override=state.max_output_tokens_override,
            turn_count=state.turn_count + 1,  # 递增轮次
            transition=transition,
        )
        # continue 回到 while True 顶部，开始下一轮迭代


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _extract_tool_use_blocks(msg: AssistantMessage) -> list[ToolUseBlock]:
    """从 AssistantMessage 中提取所有 tool_use 块。

    遍历 assistant 消息的 content 列表，找出 type="tool_use" 的块，
    转换为 ToolUseBlock 实例供工具执行器使用。

    注意：流式路径中 input 是累积的 JSON 字符串，需要解析为 dict。
    """
    content = msg.message.get("content", [])
    blocks: list[ToolUseBlock] = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                raw_input = block.get("input", {})
                # 流式累积时 input 是 JSON 字符串，需要解析
                if isinstance(raw_input, str):
                    try:
                        parsed_input = json.loads(raw_input) if raw_input.strip() else {}
                    except json.JSONDecodeError:
                        parsed_input = {}
                else:
                    parsed_input = raw_input if isinstance(raw_input, dict) else {}
                blocks.append(
                    ToolUseBlock(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        input=parsed_input,
                    )
                )
    return blocks


def _merge_delta(block: dict[str, Any], delta: dict[str, Any]) -> None:
    """将流式 delta 合并到内容块中。

    处理三种 delta 类型:
    - text_delta: 追加文本
    - input_json_delta: 追加部分 JSON（工具调用参数）
    - thinking_delta: 追加思考内容
    """
    delta_type = delta.get("type", "")

    if delta_type == "text_delta":
        block["text"] = block.get("text", "") + delta.get("text", "")
    elif delta_type == "input_json_delta":
        # input 在流式开始时可能是空 dict，需转为字符串再累积
        current = block.get("input", "")
        if isinstance(current, dict):
            current = ""
        block["input"] = current + delta.get("partial_json", "")
    elif delta_type == "thinking_delta":
        block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
    elif delta_type == "signature_delta":
        block["signature"] = delta.get("signature", "")


def _classify_error(exc: Exception) -> str:
    """将异常分类为错误类型字符串，供 RecoveryChain 决策使用。

    返回的错误类型：
    - "prompt_too_long": prompt 超过模型上限
    - "max_output_tokens": 输出 token 达到上限
    - "media_size": 图片/媒体文件过大
    - "model_error": 模型 API 错误（过载/限流）
    - "api_error": 其他 API 错误
    """
    error_str = str(exc).lower()
    if "prompt is too long" in error_str or "prompt_too_long" in error_str:
        return "prompt_too_long"
    if "max_tokens" in error_str or "max_output_tokens" in error_str:
        return "max_output_tokens"
    if "image" in error_str or "media" in error_str:
        return "media_size"
    if any(code in error_str for code in ("529", "overloaded", "rate_limit")):
        return "model_error"
    return "api_error"


async def _apply_recovery(
    decision: RecoveryDecision,
    store: MessageStore,
    state: LoopState,
    messages: list[Message],
) -> tuple[Transition, LoopState] | None:
    """应用恢复决策并返回新的 Transition + LoopState。

    返回 None 表示决策为 TERMINATE 或 PASS_THROUGH，无法恢复。

    4 种恢复策略：
    - REACTIVE_COMPACT_RETRY: 压缩上下文后重试（针对 prompt_too_long）
    - COLLAPSE_DRAIN_RETRY: 折叠旧消息后重试（针对 prompt_too_long 第二轮）
    - ESCALATE_MAX_TOKENS: 升级 max_output_tokens 上限（针对 max_output_tokens）
    - RECOVERY_PROMPT: 注入恢复提示让模型继续（针对 max_output_tokens 多轮）
    """
    if not decision.should_continue:  # TERMINATE 或 PASS_THROUGH
        return None

    match decision.action:  # Python 3.10+ 结构化模式匹配
        case RecoveryAction.REACTIVE_COMPACT_RETRY:
            # 反应式压缩: 剥离 thinking + 截断 tool_result，然后重试
            compacted, saved = await prompt_too_long.reactive_compact(messages)
            store.replace(compacted)
            transition = Transition(
                reason=TransitionReason.REACTIVE_COMPACT_RETRY,
                metadata={"tokens_saved": saved},
            )
            new_state = LoopState(
                messages=store,
                tool_use_context=state.tool_use_context,
                has_attempted_reactive_compact=True,
                turn_count=state.turn_count,
                transition=transition,
            )
            return transition, new_state

        case RecoveryAction.COLLAPSE_DRAIN_RETRY:
            # 上下文折叠排水: 移除最旧的 N 对消息，然后重试
            drained, saved = await prompt_too_long.context_collapse_drain(messages)
            store.replace(drained)
            transition = Transition(
                reason=TransitionReason.COLLAPSE_DRAIN_RETRY,
                metadata={"tokens_saved": saved},
            )
            new_state = LoopState(
                messages=store,
                tool_use_context=state.tool_use_context,
                has_attempted_reactive_compact=state.has_attempted_reactive_compact,
                turn_count=state.turn_count,
                transition=transition,
            )
            return transition, new_state

        case RecoveryAction.ESCALATE_MAX_TOKENS:
            # 升级 max_output_tokens 上限: 从默认 8k 升级到 64k
            new_max = (
                decision.metadata.get("new_max_tokens", 64_000)
                if decision.metadata
                else 64_000
            )
            transition = Transition(
                reason=TransitionReason.MAX_OUTPUT_TOKENS_ESCALATE,
                metadata={"new_max_tokens": new_max},
            )
            new_state = LoopState(
                messages=store,
                tool_use_context=state.tool_use_context,
                max_output_tokens_override=new_max,
                turn_count=state.turn_count,
                transition=transition,
            )
            return transition, new_state

        case RecoveryAction.RECOVERY_PROMPT:
            # 注入恢复提示: 告诉模型“你的回复被截断了，请继续”
            recovery_msg = max_output_tokens.create_recovery_message(
                state.max_output_tokens_recovery_count,
                decision.recovery_message,
            )
            store.append(recovery_msg)
            transition = Transition(
                reason=TransitionReason.MAX_OUTPUT_TOKENS_RECOVERY,
                metadata={
                    "recovery_count": state.max_output_tokens_recovery_count + 1,
                },
            )
            new_state = LoopState(
                messages=store,
                tool_use_context=state.tool_use_context,
                max_output_tokens_recovery_count=state.max_output_tokens_recovery_count + 1,
                max_output_tokens_override=state.max_output_tokens_override,
                turn_count=state.turn_count + 1,
                transition=transition,
            )
            return transition, new_state

        case _:  # TERMINATE / TOKEN_BUDGET_NUDGE / PASS_THROUGH 等不处理
            return None


def _check_budget(
    config: EngineConfig,
    cost_tracker: CostTracker,
) -> TerminalReason | None:
    """检查预算门控 —— 超出美元或 token 预算时返回终止原因。"""
    # 美元预算
    if config.max_budget_usd is not None:
        if cost_tracker.get_total_cost() >= config.max_budget_usd:
            logger.warning(
                "Budget exceeded: $%.4f >= $%.4f",
                cost_tracker.get_total_cost(),
                config.max_budget_usd,
            )
            return TerminalReason.MAX_BUDGET_USD

    # token 预算（task_budget 形如 {"total": 100000}）
    if config.task_budget is not None:
        total_limit = config.task_budget.get("total")
        if total_limit is not None:
            total_tokens = cost_tracker.get_total_usage().total_tokens
            if total_tokens >= total_limit:
                logger.warning(
                    "Token budget exceeded: %d >= %d",
                    total_tokens,
                    total_limit,
                )
                return TerminalReason.TASK_BUDGET_EXHAUSTED

    return None


def _make_terminal(
    store: MessageStore,
    state: LoopState,
    reason: TerminalReason,
    error: Exception | None = None,
) -> SystemMessage:
    """创建终止系统消息 —— 标记循环结束的原因。

    返回的 SystemMessage 通过 subtype="loop_terminal" 标识，
    QueryEngine 收到后会转换为 SDKResultMessage。
    """
    return SystemMessage(
        subtype="loop_terminal",
        content=f"Loop terminated: {reason.name}",
        error={"type": reason.name, "message": str(error)} if error else None,
    )
