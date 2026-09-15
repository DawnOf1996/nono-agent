"""仓库测试夹具 —— Mock Provider、Fixture 回放与测试工具集（不进入引擎包）。

提供无需真实 API 调用即可测试 agent 引擎的全套工具：
- MockProvider: 返回脚本化的响应，支持多轮对话
- FixtureReplayProvider: 回放录制的 API 响应，用于确定性集成测试
- MockTool: 模拟工具实现，可配置并发安全性和写副作用
- create_test_engine: 工厂函数，创建预配置的测试用 QueryEngine
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import (
    AssistantMessage,
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    Message,
    MessageDeltaEvent,
    MessageStartEvent,
    MessageStopEvent,
    StreamEvent,
)
from nono_agent.providers.base import ProviderRegistry
from nono_agent.tools.protocol import ToolBase
from nono_agent.tools.registry import ToolRegistry

# ---------------------------------------------------------------------------
# Mock Provider —— 脚本化响应的模拟 LLM Provider
# ---------------------------------------------------------------------------


class MockProvider:
    """模拟 LLM Provider —— 返回预设的脚本化响应。

    支持多轮对话：按调用顺序依次返回 responses 列表中的响应，
    当脚本耗尽时回退到 default_response。

    用法::

        provider = MockProvider(responses=[
            {"text": "Hello!", "tool_calls": []},
            {"text": "I'll help with that.", "tool_calls": [...]},
        ])
    """

    def __init__(
        self,
        responses: list[dict[str, Any]] | None = None,
        default_response: str = "I'm a mock provider. How can I help?",
    ) -> None:
        """初始化 MockProvider。

        Args:
            responses: 脚本化响应列表，按顺序返回。每项包含 text/tool_calls/stop_reason
            default_response: 脚本耗尽时的默认响应文本
        """
        self._responses = responses or []     # 脚本化响应队列
        self._default_response = default_response  # 回退响应
        self._call_count = 0                   # 已调用次数（用于按序取响应）
        self.calls: list[dict[str, Any]] = []  # 调用记录（用于测试断言）

    @property
    def name(self) -> str:
        """Provider 名称标识。"""
        return "mock"

    @property
    def supports_thinking(self) -> bool:
        """不支持思考模式。"""
        return False

    @property
    def supports_tool_choice(self) -> bool:
        """支持工具选择。"""
        return True

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: Any = None,
        model: str = "",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent | Message, None]:
        """以流式事件序列返回脚本化响应。

        模拟真实 Provider 的流式事件序列：
        message_start → content_block_start/delta/stop → message_delta → message_stop
        最后额外产出一个完整的 AssistantMessage。
        """
        # 记录调用参数，供测试断言使用
        self.calls.append({
            "messages": messages,
            "system_prompt": system_prompt,
            "tools": tools,
            "model": model,
        })

        # 按调用顺序获取脚本化响应，耗尽时使用默认响应
        if self._call_count < len(self._responses):
            response = self._responses[self._call_count]
        else:
            response = {"text": self._default_response, "tool_calls": []}
        self._call_count += 1

        text = response.get("text", self._default_response)
        tool_calls = response.get("tool_calls", [])
        stop_reason = response.get("stop_reason", "end_turn")

        # 构建内容块列表 —— 文本块 + 工具调用块
        content_blocks: list[dict[str, Any]] = []
        if text:
            content_blocks.append({"type": "text", "text": text})
        for tc in tool_calls:
            content_blocks.append({
                "type": "tool_use",
                "id": tc.get("id", f"toolu_{self._call_count}"),
                "name": tc["name"],
                "input": tc.get("input", {}),
            })

        # 发射流式事件序列（模拟真实 API 的事件顺序）
        # 1. message_start —— 包含初始 usage 信息
        yield MessageStartEvent(
            message={"usage": {"input_tokens": 10, "output_tokens": 0}}
        )

        # 2. 每个内容块的 start → delta → stop 序列
        for i, block in enumerate(content_blocks):
            yield ContentBlockStartEvent(
                index=i,
                content_block=block,
            )
            if block["type"] == "text":
                # 文本块额外发射 text_delta 事件
                yield ContentBlockDeltaEvent(
                    index=i,
                    delta={"type": "text_delta", "text": block["text"]},
                )
            yield ContentBlockStopEvent(index=i)

        # 3. message_delta —— 包含 stop_reason 和输出 token 计数
        # 如果有工具调用，stop_reason 应为 "tool_use"（触发 agentic loop 继续）
        if tool_calls:
            stop_reason = "tool_use"

        yield MessageDeltaEvent(
            delta={"stop_reason": stop_reason},
            usage={"output_tokens": len(text.split())},
        )
        # 4. message_stop —— 标记流式传输结束
        yield MessageStopEvent()

        # 5. 额外产出完整的 AssistantMessage（便于引擎直接消费）
        yield AssistantMessage(
            message={
                "role": "assistant",
                "content": content_blocks,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": len(text.split()),
                },
                "stop_reason": stop_reason,
            }
        )

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """粗略估算 token 数 —— 按字符数/4 近似。"""
        return sum(len(str(m)) for m in messages) // 4

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """将 Message 对象标准化为字典列表。"""
        return [m.message if hasattr(m, "message") else {} for m in messages]


# ---------------------------------------------------------------------------
# Fixture Replay Provider —— 从文件回放录制的 API 响应
# ---------------------------------------------------------------------------


class FixtureReplayProvider:
    """回放录制的 API 响应 —— 从 fixture 文件加载并依次返回。

    用于确定性集成测试：将真实 API 响应录制为 JSON 文件，
    测试时按调用顺序重放，确保结果可预测。
    """

    def __init__(self, fixture_path: str | Path) -> None:
        """初始化 FixtureReplayProvider。

        Args:
            fixture_path: fixture JSON 文件路径，内容为 API 响应数组
        """
        self._fixture_path = Path(fixture_path)
        self._responses: list[dict[str, Any]] = []  # 加载的响应序列
        self._call_count = 0                         # 已回放次数
        self._load_fixture()

    def _load_fixture(self) -> None:
        """从 JSON 文件加载录制的响应序列。"""
        if self._fixture_path.exists():
            with open(self._fixture_path) as f:
                self._responses = json.load(f)

    @property
    def name(self) -> str:
        """Provider 名称标识。"""
        return "fixture_replay"

    @property
    def supports_thinking(self) -> bool:
        """不支持思考模式。"""
        return False

    @property
    def supports_tool_choice(self) -> bool:
        """支持工具选择。"""
        return True

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: Any = None,
        model: str = "",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent | Message, None]:
        """按顺序回放录制的响应。

        与 MockProvider 不同，这里直接返回完整的 AssistantMessage，
        不模拟流式事件序列（因为 fixture 中已是最终响应格式）。
        """
        if self._call_count < len(self._responses):
            # 按顺序返回下一个录制的响应
            resp = self._responses[self._call_count]
            self._call_count += 1

            yield AssistantMessage(
                message={
                    "role": "assistant",
                    "content": resp.get("content", []),
                    "usage": resp.get("usage", {"input_tokens": 10, "output_tokens": 10}),
                    "stop_reason": resp.get("stop_reason", "end_turn"),
                }
            )
        else:
            # fixture 耗尽 —— 返回占位消息避免崩溃
            yield AssistantMessage(
                message={
                    "role": "assistant",
                    "content": [{"type": "text", "text": "(fixture exhausted)"}],
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                    "stop_reason": "end_turn",
                }
            )

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """固定返回 100 —— fixture 模式下不做真实 token 计数。"""
        return 100

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """将 Message 对象标准化为字典列表。"""
        return [m.message if hasattr(m, "message") else {} for m in messages]


# ---------------------------------------------------------------------------
# Mock Tool —— 模拟工具实现
# ---------------------------------------------------------------------------


class MockTool(ToolBase):
    """模拟工具 —— 可配置的测试用工具实现。

    支持配置名称、描述、响应文本、并发安全性和写副作用，
    并记录调用次数和最后一次输入参数。
    """

    def __init__(
        self,
        name: str = "mock_tool",
        description: str = "A mock tool for testing",
        response: str = "Mock tool executed successfully",
        concurrent_safe: bool = True,
        write_side_effects: bool = False,
    ) -> None:
        """初始化 MockTool。

        Args:
            name: 工具名称
            description: 工具描述（LLM 用此决定是否调用）
            response: 工具执行后返回的文本内容
            concurrent_safe: 是否并发安全（影响 BatchExecutor 策略）
            write_side_effects: 是否有写副作用（影响并发分组）
        """
        self._name = name
        self._description = description
        self._response = response
        self._concurrent_safe = concurrent_safe
        self._write_side_effects = write_side_effects
        self.call_count = 0                       # 调用计数器
        self.last_input: dict[str, Any] | None = None  # 最后一次调用参数

    @property
    def name(self) -> str:
        """工具名称标识。"""
        return self._name

    @property
    def description(self) -> str:
        """工具描述 —— LLM 根据此描述决定是否调用。"""
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        """工具输入 schema —— JSON Schema 格式。"""
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Input query"},
            },
            "required": ["query"],
        }

    @property
    def is_concurrent_safe(self) -> bool:
        """是否可并发执行 —— 决定 BatchExecutor 是否并行调用。"""
        return self._concurrent_safe

    @property
    def has_write_side_effects(self) -> bool:
        """是否有写副作用 —— 影响工具执行的并发分组策略。"""
        return self._write_side_effects

    async def execute(self, input_params: dict[str, Any], context: Any) -> Any:
        """执行工具 —— 记录调用并返回预设响应。"""
        from nono_agent.core.types import ToolResult
        self.call_count += 1             # 递增调用计数
        self.last_input = input_params   # 记录输入参数供断言
        return ToolResult(
            tool_use_id="",
            content=self._response,
            tool_name=self._name,
        )


# ---------------------------------------------------------------------------
# 测试引擎工厂 —— 创建预配置的 QueryEngine 实例
# ---------------------------------------------------------------------------


def create_test_engine(
    responses: list[dict[str, Any]] | None = None,
    *,
    tools: list[ToolBase] | None = None,
    model: str = "claude-test",
    transcript_writer: Any | None = None,
    provider: Any | None = None,
    config_overrides: dict[str, Any] | None = None,
    **engine_kwargs: Any,
) -> QueryEngine:
    """创建预配置的测试用 QueryEngine。

    内部自动完成：
    1. 创建 MockProvider 并注册到 ProviderRegistry
    2. 创建 ToolRegistry 并注册提供的工具
    3. 构造 QueryEngine 实例（跳过 initialize()，由调用者决定）

    Args:
        responses: MockProvider 的脚本化响应列表
        tools: 要注册的工具列表
        model: 模型名称（用于 Provider 路由）
        transcript_writer: 事件流写入器；None 时引擎按 session_id 建默认本地事件流
        provider: 替换默认 MockProvider 的 Provider 实例（测试错误恢复路径用）
        config_overrides: 覆盖 EngineConfig 字段（如 session_invariant）
        engine_kwargs: 直接透传给 QueryEngine 的其它依赖注入参数
    """
    registry = ProviderRegistry()
    registry.register_instance(model, provider or MockProvider(responses=responses))  # type: ignore

    # 创建 ToolRegistry 并注册提供的工具
    tool_registry = ToolRegistry()
    if tools:
        for tool in tools:
            tool_registry.register(tool)

    # 构造引擎实例（使用测试配置）
    config = EngineConfig(
        model=model,
        include_partial_messages=True,
        **(config_overrides or {}),
    )

    engine_kwargs.setdefault("auto_load_mcp", False)
    return QueryEngine(
        config,
        provider_registry=registry,
        tool_registry=tool_registry,
        transcript_writer=transcript_writer,
        **engine_kwargs,
    )
