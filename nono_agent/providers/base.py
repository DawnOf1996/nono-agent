"""LLM Provider 协议与注册表。

定义所有模型提供商必须实现的 LLMProvider Protocol，
以及支持配置驱动和前缀回退两种路由模式的 ProviderRegistry。

路由优先级：
1. config.adapter 显式指定 → 按适配器类型创建
2. model_id 前缀匹配 → 向后兼容的默认行为（"claude-" → Anthropic, "gpt-" → OpenAI）

适配器矩阵（原厂 vs 兼容，兼容用于非原厂网关）：
- "anthropic"             → AnthropicProvider（Messages 原厂）
- "messages_compatible"   → MessagesCompatibleProvider（Messages 兼容，规划中）
- "openai"                → OpenAICompatibleProvider（Chat Completions 兼容）
- "responses"             → ResponsesProvider（Responses 原厂，占位）
- "responses_compatible"  → ResponsesCompatibleProvider（Responses 兼容，规划中）
详见 docs/ROADMAP.md §2.6。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any, Protocol, runtime_checkable

from nono_agent.core.config import ProviderConfig, ThinkingConfig
from nono_agent.core.types import Message, StreamEvent


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
    """提供商注册表 —— 支持配置驱动和自动推断两种路由模式。

    路由优先级：
    1. config.adapter 显式指定 → 按适配器类型创建 Provider
    2. model_id 前缀匹配 → 向后兼容的默认行为

    适配器类型映射：
    - "anthropic"            → AnthropicProvider（Messages 原厂）
    - "messages_compatible"  → MessagesCompatibleProvider（Messages 兼容，规划中）
    - "openai"               → OpenAICompatibleProvider（Chat Completions 兼容）
    - "responses"            → ResponsesProvider（Responses 原厂）
    - "responses_compatible" → ResponsesCompatibleProvider（Responses 兼容，规划中）
    """

    def __init__(self) -> None:
        self._providers: dict[str, type] = {}           # 前缀 → Provider 类（回退路由）
        self._instances: dict[str, LLMProvider] = {}     # 缓存的 Provider 实例
        self._adapter_map: dict[str, type] = {}          # 适配器名称 → Provider 类

    def register(self, prefix: str, provider_cls: type) -> None:
        """为 model_id 前缀注册 Provider 类（回退路由）。"""
        self._providers[prefix] = provider_cls

    def register_adapter(self, adapter_name: str, provider_cls: type) -> None:
        """注册适配器类型 → Provider 类的映射（配置驱动路由）。"""
        self._adapter_map[adapter_name] = provider_cls

    def resolve(
        self,
        model_id: str,
        config: ProviderConfig | None = None,
    ) -> LLMProvider:
        """根据配置解析 Provider 实例。

        路由优先级：
        1. config.adapter 显式指定 → 从 adapter_map 创建
        2. model_id 前缀匹配 → 从 providers 创建（向后兼容）
        """
        # 优先级 1: 显式 adapter 指定（配置驱动）
        if config and config.adapter:
            cls = self._adapter_map.get(config.adapter)
            if cls is None:
                available = list(self._adapter_map.keys())
                raise ValueError(
                    f"Unknown adapter type '{config.adapter}'. "
                    f"Available adapters: {available}"
                )
            # 配置驱动的 Provider 每次都创建新实例（因为配置可能不同）
            return cls(config=config)

        # 优先级 2: 缓存实例匹配
        for prefix, instance in self._instances.items():
            if model_id.startswith(prefix):
                return instance

        # 优先级 3: 前缀匹配（向后兼容）
        for prefix, cls in self._providers.items():
            if model_id.startswith(prefix):
                instance = cls(config=config) if config else cls()
                self._instances[prefix] = instance
                return instance

        raise ValueError(
            f"No provider registered for model '{model_id}'. "
            f"Registered prefixes: {list(self._providers.keys())}. "
            f"Available adapters: {list(self._adapter_map.keys())}. "
            f"Hint: set config.adapter to explicitly choose an API protocol."
        )

    def register_instance(self, prefix: str, instance: LLMProvider) -> None:
        """注册已配置的 Provider 实例。"""
        self._instances[prefix] = instance


def create_default_registry() -> ProviderRegistry:
    """创建默认注册表 —— 内置 Anthropic、OpenAI 和通用 OpenAI 兼容提供商。

    注册两套路由：
    1. 适配器映射（配置驱动）：
       anthropic / messages_compatible / openai / responses / responses_compatible
       （后两者与 messages_compatible 见 Roadmap；responses 现为占位）
    2. 前缀映射（向后兼容）："claude-" → Anthropic, "gpt-"/"o1"/"o3"/"o4" → OpenAI
    """
    registry = ProviderRegistry()

    # ------------------------------------------------------------------
    # 适配器映射 —— 配置驱动路由（用户通过 config.adapter 显式指定）
    # ------------------------------------------------------------------

    # Anthropic Messages API —— 原厂
    try:
        from nono_agent.providers.anthropic_provider import AnthropicProvider
        registry.register_adapter("anthropic", AnthropicProvider)
    except ImportError:
        pass

    # Anthropic Messages API —— 非原厂兼容网关
    try:
        from nono_agent.providers.messages_compatible import MessagesCompatibleProvider
        registry.register_adapter("messages_compatible", MessagesCompatibleProvider)
    except ImportError:
        pass

    # Chat Completions —— 通用兼容（MiniMax/DeepSeek/通义千问/智谱/Moonshot 等）
    try:
        from nono_agent.providers.openai_compatible import OpenAICompatibleProvider
        registry.register_adapter("openai", OpenAICompatibleProvider)
    except ImportError:
        pass

    # OpenAI Responses API —— 原厂（占位）
    try:
        from nono_agent.providers.responses_provider import ResponsesProvider
        registry.register_adapter("responses", ResponsesProvider)
    except ImportError:
        pass

    # TODO: responses_compatible —— Responses 非原厂兼容网关
    # registry.register_adapter("responses_compatible", ResponsesCompatibleProvider)
    # ------------------------------------------------------------------
    # 前缀映射 —— 向后兼容路由（不配置 adapter 时按 model_id 前缀自动推断）
    # ------------------------------------------------------------------

    try:
        from nono_agent.providers.anthropic_provider import AnthropicProvider
        registry.register("claude-", AnthropicProvider)
    except ImportError:
        pass

    try:
        from nono_agent.providers.openai_provider import OpenAIProvider
        registry.register("gpt-", OpenAIProvider)
        registry.register("o1", OpenAIProvider)
        registry.register("o3", OpenAIProvider)
        registry.register("o4", OpenAIProvider)
    except ImportError:
        pass

    return registry
