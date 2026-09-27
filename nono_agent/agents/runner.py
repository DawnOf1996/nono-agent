"""AgentRunner —— 子 Agent 独立 Engine/Store 运行实例。

按 AgentDefinition 构造受限工具集 + 独立引擎，运行 prompt 并把
最终结果回写给父会话。
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from nono_agent.agents.definition import AgentDefinition
from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import AssistantMessage, SDKResultMessage
from nono_agent.jobs.board import append_output, finish_job, open_job
from nono_agent.jobs.context import jobs_parent, jobs_session
from nono_agent.tools.registry import ToolRegistry


@dataclass
class AgentRunResult:
    """子 Agent 运行结果。"""

    text: str = ""
    is_error: bool = False
    num_turns: int = 0
    total_tokens: int = 0


class AgentRunner:
    """子 Agent 运行器 —— 独立引擎 + 受限工具集 + 预算封顶（可选）。"""

    def __init__(
        self,
        provider_registry: Any,
        tool_registry: ToolRegistry,
        base_config: EngineConfig,
    ) -> None:
        self._provider_registry = provider_registry
        self._tool_registry = tool_registry
        self._base_config = base_config

    def build_sub_tool_registry(self, definition: AgentDefinition) -> ToolRegistry:
        """按定义的工具白名单构建子工具注册表（空白名单=继承全部）。"""
        sub = ToolRegistry()
        if definition.tools:
            for name in definition.tools:
                if self._tool_registry.has(name):
                    sub.register(self._tool_registry.find(name))
        else:
            for tool in self._tool_registry.all():
                sub.register(tool)
        return sub

    def build_sub_config(self, definition: AgentDefinition) -> EngineConfig:
        """构建子引擎配置（独立 session_id，system prompt 来自定义）。"""
        return EngineConfig(
            model=definition.model or self._base_config.model,
            cwd=self._base_config.cwd,
            session_id=str(uuid.uuid4()),
            custom_system_prompt=definition.system_prompt,
            max_turns=self._base_config.max_turns,
        )

    async def run(
        self,
        definition: AgentDefinition,
        prompt: str,
        *,
        session_id: str = "",
        on_assistant: Callable[[str], None] | None = None,
    ) -> AgentRunResult:
        """运行子 Agent 并返回最终结果。"""
        sub_registry = self.build_sub_tool_registry(definition)
        sub_config = self.build_sub_config(definition)

        engine = QueryEngine(
            sub_config,
            provider_registry=self._provider_registry,
            tool_registry=sub_registry,
            register_agent_tool=False,  # 防止子 Agent 无界嵌套派发
        )
        root_session = jobs_session.get() or session_id
        job_id = open_job(
            session_id=root_session,
            kind="agent",
            title=definition.name,
            parent_id=jobs_parent.get() or None,
            kill=engine.abort,
        )
        session_token = jobs_session.set(root_session) if root_session else None
        parent_token = jobs_parent.set(job_id) if job_id else None

        result: SDKResultMessage | None = None
        saw_text = False
        try:
            async with engine:
                async for msg in engine.submit_message(prompt):
                    if isinstance(msg, AssistantMessage):
                        text = _assistant_text(msg)
                        if text:
                            append_output(job_id, text if not saw_text else f"\n{text}")
                            saw_text = True
                            if on_assistant is not None:
                                on_assistant(text)
                    elif isinstance(msg, SDKResultMessage):
                        result = msg
        except Exception as exc:  # 子引擎异常隔离，回写错误
            finish_job(job_id, status="failed")
            return AgentRunResult(text=f"Sub-agent error: {exc}", is_error=True)
        finally:
            if session_token is not None:
                jobs_session.reset(session_token)
            if parent_token is not None:
                jobs_parent.reset(parent_token)

        if result is None:
            finish_job(job_id, status="failed")
            return AgentRunResult(text="(no result)", is_error=True)

        finish_job(job_id, status="failed" if result.is_error else "completed")
        return AgentRunResult(
            text=result.result,
            is_error=result.is_error,
            num_turns=result.num_turns,
            total_tokens=result.usage.total_tokens,
        )


def _assistant_text(message: AssistantMessage) -> str:
    content = message.message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)
