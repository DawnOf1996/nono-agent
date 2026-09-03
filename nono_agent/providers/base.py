"""LLM Provider 协议与注册表。

定义所有模型提供商必须实现的 LLMProvider Protocol，以及 ProviderRegistry。

``config.adapter`` 必须显式指定（YAML 目录的 ``api`` 字段）。无 adapter 则失败
（``register(prefix)`` 仍可供测试与自定义 Provider 注入）。

协议只有三类，一律走对应 SDK client（供应商由 api_key + base_url 决定）：
- ``openai_chat_completions`` → OpenAI SDK ``AsyncOpenAI.chat.completions``
- ``openai_responses``        → OpenAI SDK ``AsyncOpenAI.responses``
- ``anthropic_messages``      → Anthropic SDK ``AsyncAnthropic.messages``
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol, cast, runtime_checkable

from nono_agent.core.config import ProviderConfig, ThinkingConfig
from nono_agent.core.types import Message, StreamEvent

# 三类协议（按 API 形态；供应商由 api_key + base_url 决定）
CANONICAL_ADAPTERS: tuple[str, ...] = (
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
)


class FallbackTriggeredError(Exception):
    """Fallback 触发异常 —— 主模型不可用时切换到备用模型。"""

    def __init__(
        self,
        original_model: str,
        fallback_model: str,
        reason: str = "",
    ) -> None:
        self.original_model = original_model
        self.fallback_model = fallback_model
        self.reason = reason
        super().__init__(
            f"Fallback from {original_model} to {fallback_model}: {reason}"
        )


@runtime_checkable
class LLMProvider(Protocol):
    """所有 LLM 提供商必须实现的协议。

    职责：
    1. 将内部 Message 格式转换为提供商的 API 格式
    2. 发起流式 API 调用
    3. 将提供商的流事件转换为内部 StreamEvent 格式
    4. Token 计数（提供商特定的 tokenizer）
    """

    name: str
    supports_thinking: bool
    supports_tool_choice: bool

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: ThinkingConfig | None = None,
        model: str = "",
        max_tokens: int | None = None,
        signal: Any = None,  # AbortSignal
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,  # Callable[[], None]
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent | Message, None]:
        """流式调用 LLM API —— yield StreamEvent 和 AssistantMessage。

        主模型不可用时抛出 FallbackTriggeredError。
        """
        ...

    def count_tokens(
        self,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
    ) -> int:
        """使用提供商特定的 tokenizer 计算 token 数。"""
        ...

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        """将内部消息转换为提供商的 API 格式。"""
        ...


class ProviderRegistry:
    """提供商注册表 —— 配置驱动适配器路由。

    ``config.adapter`` 必须显式指定（YAML 目录的 ``api`` 字段）。
    ``register(prefix)`` 仍可供测试与自定义 Provider 按 model_id 前缀注入。
    """

    def __init__(self) -> None:
        self._providers: dict[str, type] = {}           # 前缀 → Provider 类（自定义/测试）
        self._instances: dict[str, LLMProvider] = {}     # 缓存的 Provider 实例
        self._adapter_map: dict[str, type] = {}          # 适配器名称 → Provider 类

    def register(self, prefix: str, provider_cls: type) -> None:
        """为 model_id 前缀注册 Provider 类（测试与自定义注入）。"""
        self._providers[prefix] = provider_cls

    def register_adapter(self, adapter_name: str, provider_cls: type) -> None:
        """注册适配器类型 → Provider 类的映射（配置驱动路由）。"""
        self._adapter_map[adapter_name] = provider_cls

    def _available_adapter_names(self) -> str:
        return str([n for n in CANONICAL_ADAPTERS if n in self._adapter_map])

    def resolve(
        self,
        model_id: str,
        config: ProviderConfig | None = None,
    ) -> LLMProvider:
        """根据配置解析 Provider 实例。

        ``config.adapter`` 优先；未指定时仅匹配已 register 的前缀（测试/自定义）。
        """
        if config and config.adapter:
            cls = self._adapter_map.get(config.adapter)
            if cls is None:
                raise ValueError(
                    f"Unknown adapter type '{config.adapter}'. "
                    f"Available adapters: {self._available_adapter_names()}"
                )
            return cast(LLMProvider, cls(config=config))

        for prefix, instance in self._instances.items():
            if model_id.startswith(prefix):
                return instance

        for prefix, cls in self._providers.items():
            if model_id.startswith(prefix):
                instance = cast(LLMProvider, cls(config=config) if config else cls())
                self._instances[prefix] = instance
                return instance

        raise ValueError(
            f"No provider registered for model '{model_id}'. "
            f"Registered prefixes: {list(self._providers.keys())}. "
            f"Available adapters: {self._available_adapter_names()}. "
            f"Hint: set config.adapter to explicitly choose an API protocol."
        )

    def register_instance(self, prefix: str, instance: LLMProvider) -> None:
        """注册已配置的 Provider 实例。"""
        self._instances[prefix] = instance


def create_default_registry() -> ProviderRegistry:
    """创建默认注册表 —— 三类协议均走对应 SDK client。"""
    registry = ProviderRegistry()

    try:
        from nono_agent.providers.anthropic_messages import AnthropicMessagesProvider

        registry.register_adapter("anthropic_messages", AnthropicMessagesProvider)
    except ImportError:
        pass

    try:
        from nono_agent.providers.openai_chat_completions import OpenAIChatCompletionsProvider

        registry.register_adapter("openai_chat_completions", OpenAIChatCompletionsProvider)
    except ImportError:
        pass

    try:
        from nono_agent.providers.openai_responses import OpenAIResponsesProvider

        registry.register_adapter("openai_responses", OpenAIResponsesProvider)
    except ImportError:
        pass

    return registry
