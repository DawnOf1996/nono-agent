"""ContextCollapse 摘要器 —— 用 Provider 生成对话摘要。

供 ContextCollapseStage 使用：把旧消息压缩为紧凑摘要，节省上下文。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from nono_agent.core.types import AssistantMessage, UserMessage

# 摘要请求的上下文截断（字符）
_MAX_SUMMARY_INPUT_CHARS = 24_000


class ProviderSummarizer:
    """基于 LLM Provider 的对话摘要器。"""

    def __init__(
        self,
        provider_factory: Callable[[], Any],
        model: str,
    ) -> None:
        self._provider_factory = provider_factory
        self._model = model

    async def __call__(self, messages: list[dict[str, Any]], model_id: str) -> str:
        """生成对话摘要。

        Args:
            messages: 待摘要的旧消息字典列表
            model_id: 主模型 ID（未使用，保留以对齐 Summarizer 签名）
        """
        provider = self._provider_factory()

        content = json.dumps(messages, ensure_ascii=False)[:_MAX_SUMMARY_INPUT_CHARS]
        user = UserMessage(
            message={
                "role": "user",
                "content": (
                    "Summarize the following conversation into a concise summary that "
                    "preserves key facts, decisions, and outstanding tasks:\n\n"
                    f"{content}"
                ),
            }
        )

        system = (
            "You are a conversation summarizer. Produce a compact, factual summary "
            "suitable for replacing the original messages in an agent's context."
        )

        parts: list[str] = []
        async for event in provider.stream(
            messages=[user],
            system_prompt=system,
            tools=[],
            model=self._model,
        ):
            if isinstance(event, AssistantMessage):
                for block in event.message.get("content", []):
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))

        summary = "".join(parts).strip()
        return summary or "(summary unavailable)"
