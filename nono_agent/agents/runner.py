"""AgentRunner —— 子 Agent 独立 Engine/Store 运行实例。

按 AgentDefinition 构造受限工具集 + 独立引擎，运行 prompt 并把
最终结果回写给父会话。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from nono_agent.agents.definition import AgentDefinition
from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.core.types import SDKResultMessage
from nono_agent.tools.registry import ToolRegistry


@dataclass
class AgentRunResult:
    """子 Agent 运行结果。"""

    text: str = ""
    is_error: bool = False
    num_turns: int = 0
    cost_usd: float = 0.0


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
            max_budget_usd=self._base_config.max_budget_usd,
        )

    async def run(
        self,
        definition: AgentDefinition,
        prompt: str,
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

        result: SDKResultMessage | None = None
        try:
            async with engine:
                async for msg in engine.submit_message(prompt):
                    if isinstance(msg, SDKResultMessage):
                        result = msg
        except Exception as exc:  # 子引擎异常隔离，回写错误
            return AgentRunResult(text=f"Sub-agent error: {exc}", is_error=True)

        if result is None:
            return AgentRunResult(text="(no result)", is_error=True)

        return AgentRunResult(
            text=result.result,
            is_error=result.is_error,
            num_turns=result.num_turns,
            cost_usd=result.total_cost_usd,
        )
