"""AgentTool —— 模型可调用的子 Agent 派发入口（对标 CC AgentTool）。"""

from __future__ import annotations

from typing import Any

from nono_agent.agents.definition import AgentDefinition
from nono_agent.agents.runner import AgentRunner
from nono_agent.core.types import ToolResult
from nono_agent.tools.protocol import ToolBase


class AgentTool(ToolBase):
    """派发一个子 Agent 执行子任务并返回结果。"""

    _name = "agent"
    _description = (
        "Delegate a self-contained subtask to a specialized sub-agent by name, "
        "and return its final result. Use this to offload focused work (e.g. "
        "'explore', 'general-purpose') without consuming the main context."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "subagent_type": {
                "type": "string",
                "description": "The name of the sub-agent definition to use.",
            },
            "prompt": {
                "type": "string",
                "description": "The complete task for the sub-agent.",
            },
            "description": {
                "type": "string",
                "description": "Optional one-line summary of the task (for logging).",
            },
            "model": {
                "type": "string",
                "description": "Optional model override for the sub-agent.",
            },
        },
        "required": ["subagent_type", "prompt"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = False

    def __init__(
        self,
        definitions: list[AgentDefinition] | None = None,
        runner: AgentRunner | None = None,
    ) -> None:
        self._definitions: dict[str, AgentDefinition] = {}
        for d in definitions or []:
            self._definitions[d.name] = d
        self._runner = runner

    def available_names(self) -> list[str]:
        return sorted(self._definitions.keys())

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        name = input_params.get("subagent_type", "")
        prompt = input_params.get("prompt", "")

        if not prompt:
            return ToolResult(
                tool_use_id="",
                content="Error: 'prompt' is required.",
                is_error=True,
                tool_name=self._name,
            )

        definition = self._definitions.get(name)
        if definition is None:
            available = ", ".join(self.available_names()) or "(none)"
            return ToolResult(
                tool_use_id="",
                content=f"Unknown sub-agent '{name}'. Available: {available}.",
                is_error=True,
                tool_name=self._name,
            )

        if self._runner is None:
            return ToolResult(
                tool_use_id="",
                content="Error: no AgentRunner is attached to the AgentTool.",
                is_error=True,
                tool_name=self._name,
            )

        # 模型覆盖（一次性，不修改 definition）
        if input_params.get("model"):
            definition = AgentDefinition(
                name=definition.name,
                description=definition.description,
                system_prompt=definition.system_prompt,
                tools=definition.tools,
                model=input_params["model"],
                source=definition.source,
                allowed_isolations=definition.allowed_isolations,
            )

        result = await self._runner.run(definition, prompt)

        if result.is_error:
            return ToolResult(
                tool_use_id="",
                content=f"[Sub-agent '{name}' failed]\n{result.text}",
                is_error=True,
                tool_name=self._name,
            )
        return ToolResult(
            tool_use_id="",
            content=f"[Sub-agent '{name}' result]\n{result.text}",
            tool_name=self._name,
        )
