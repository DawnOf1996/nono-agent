---
title: 新增 Provider 适配器
---

# 新增 Provider 适配器

Nono Agent 按 **API 形态**分协议，不按厂商分。已有三类：

| `config.adapter` | 底层 SDK | 说明 |
|---|---|---|
| `openai_chat_completions` | `AsyncOpenAI.chat.completions` | 绝大多数 OpenAI 兼容端点 |
| `openai_responses` | `AsyncOpenAI.responses` | OpenAI Responses 协议 |
| `anthropic_messages` | `AsyncAnthropic.messages` | Anthropic Messages 协议 |

!!! info "接一个新厂商，通常不需要新适配器"
    如果新厂商兼容其中一种 API 形态，只要在 `~/.nono-agent/config.yaml` 里加一段供应商目录
    （填 `api`、`base_url`、`api_key_env`、模型列表）就能用。**只有当它的 API 形态与三者都不同**
    （事件结构、流式协议、工具调用格式都不一样）才需要新写适配器。

---

## 1. 实现

在 `nono_agent/providers/` 新建文件。参考 `openai_responses.py` 的结构：主类薄，事件映射与归一化拆到 `_xxx_utils.py`。

```python
# nono_agent/providers/acme_messages.py
"""Acme Messages 协议 —— 事件映射与归一化。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from nono_agent.core.config import ProviderConfig, ThinkingConfig
from nono_agent.core.types import AssistantMessage, Message, StreamEvent


class AcmeMessagesProvider:
    """Acme Messages API 适配器。"""

    def __init__(self, config: ProviderConfig | None = None) -> None:
        self.config = config
        self.name = "acme_messages"
        self.supports_thinking = True
        self.supports_tool_choice = True
        # 真实实现里在这里建 SDK client：
        #   from acme import AsyncAcme
        #   self._client = AsyncAcme(api_key=config.api_key, base_url=config.base_url)

    async def stream(
        self,
        *,
        messages: list[Message],
        system_prompt: str,
        tools: list[dict[str, Any]],
        thinking_config: ThinkingConfig | None = None,
        model: str = "",
        max_tokens: int | None = None,
        signal: Any = None,
        fallback_model: str | None = None,
        on_streaming_fallback: Any = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamEvent | Message, None]:
        params = self._build_params(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
        )
        async for chunk in self._client.messages.create(stream=True, **params):
            event = map_acme_event(chunk)      # → 内部 StreamEvent
            if event is not None:
                yield event
        yield MessageStopEvent()               # ← 收束事件；AssistantMessage 由引擎组装

    def count_tokens(self, messages, system_prompt, tools) -> int:
        return (
            estimate_messages_tokens([m.message for m in messages if hasattr(m, "message")])
            + len(system_prompt) // 4
            + estimate_tools_tokens(tools)
        )

    def normalize_messages(self, messages: list[Message]) -> list[dict[str, Any]]:
        return to_acme_format(messages, system_prompt=...)
```

> `count_tokens` 的算法抄 `nono_agent/providers/anthropic_messages.py`：`estimate_messages_tokens` + 系统提示按
> `len // 4` 估算 + `estimate_tools_tokens`。`utils/tokens.py` 里没有叫 `estimate_tokens` 的函数，别自己造一个。

### 必须满足的契约

| 契约 | 要求 |
|---|---|
| `stream()` 是异步生成器 | yield 内部 `StreamEvent`（增量），末尾以 `MessageStopEvent()` 收束。**不要**把 provider 的原生 chunk 漏给上层，**也不要**自己 yield `AssistantMessage` —— 引擎在 `core/loop.py` 里组装它。 |
| fallback | 主模型不可用时抛 `FallbackTriggeredError(original_model, fallback_model, reason)`，不要自己静默重试。 |
| 取消 | 透传 `signal`；收到中止要尽快 break 出生成器。 |
| 归一化 | `normalize_messages()` 是纯函数，同样输入永远同样输出。 |
| token 估算 | 走 `nono_agent/utils/tokens.py` 的公共估算，别自己造一套。 |

---

## 2. 注册

三处都要改，缺一处就装不上：

**① 协议名常量**（`nono_agent/providers/base.py`）：

```python
CANONICAL_ADAPTERS: tuple[str, ...] = (
    "openai_chat_completions",
    "openai_responses",
    "anthropic_messages",
    "acme_messages",        # ← 新增
)
```

**② 默认注册表**（同文件 `create_default_registry()`）：

```python
try:
    from nono_agent.providers.acme_messages import AcmeMessagesProvider

    registry.register_adapter("acme_messages", AcmeMessagesProvider)
except ImportError:
    pass
```

沿用现有的 `try/except ImportError` 模式 —— SDK 没装时该 adapter 不可用，但不影响其它协议。

**③ 目录校验**：`catalog.py` 会用 `CANONICAL_ADAPTERS` 校验 YAML 里 `api` 字段的取值，加进常量就自动生效。

---

## 3. 路由语义

`ProviderRegistry.resolve(model_id, config)` 的判定顺序：

1. `config.adapter` 有值 → 查 `_adapter_map`；查不到抛 `ValueError` 并列出可用 adapter。
2. 没有 adapter → 只匹配 `register(prefix)` / `register_instance()` 显式注册过的 `model_id` 前缀（测试与自定义 Provider 用）。
3. 都不中 → `ValueError`。

**默认注册表不注册任何前缀**，所以生产路径上 `adapter` 必须显式指定（YAML 目录的 `api` 字段）。测试里可以：

```python
registry = create_default_registry()
registry.register("fake-", FakeProvider)
```

---

## 4. 测试

```python
# tests/test_providers.py
import pytest

from nono_agent.providers.acme_messages import AcmeMessagesProvider
from nono_agent.providers.base import CANONICAL_ADAPTERS, create_default_registry


def test_adapter_is_canonical():
    assert "acme_messages" in CANONICAL_ADAPTERS


def test_registry_resolves_adapter():
    registry = create_default_registry()
    provider = registry.resolve("acme-1", ProviderConfig(adapter="acme_messages"))
    assert provider.name == "acme_messages"


def test_unknown_adapter_raises():
    registry = create_default_registry()
    with pytest.raises(ValueError, match="Unknown adapter"):
        registry.resolve("acme-1", ProviderConfig(adapter="nope"))


def test_missing_adapter_raises():
    registry = create_default_registry()
    with pytest.raises(ValueError):
        registry.resolve("acme-1", ProviderConfig())   # 无 adapter、无前缀注册
```

用 `tests/helpers.py` 的 `MockProvider` / `FixtureReplayProvider` 跑流式路径，不要打真实 API。

```bash
uv run pytest tests/test_providers.py -v
uv run mypy nono_agent/providers/
```

---

## 5. 检查清单

- [ ] 新建 `providers/acme_messages.py` + `_acme_messages_utils.py`（事件映射 / 归一化）
- [ ] `CANONICAL_ADAPTERS` 加名字
- [ ] `create_default_registry()` 里 `register_adapter`
- [ ] `stream()` 抛 `FallbackTriggeredError` 而不是自吞
- [ ] `normalize_messages()` 是纯函数
- [ ] token 估算走 `utils/tokens.py`
- [ ] `tests/test_providers.py` 补解析与异常路径用例
- [ ] 在 [配置与密钥](../getting-started/configuration.md) 的 `api` 取值表加一行

---

## 相关页面

- [配置与密钥](../getting-started/configuration.md)
- [开发环境与目录结构](index.md)
- [测试与代码规范](testing-and-style.md)
