"""SkillTool —— 模型可调用的技能激活入口（对标 CC SkillTool）。

模型按需调用 ``skill`` 工具，传入技能名，工具返回该技能的使用说明。
"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.skills.models import Skill
from nono_agent.tools.protocol import ToolBase


class SkillTool(ToolBase):
    """激活一个技能并返回其说明。"""

    _name = "skill"
    _description = (
        "Activate a reusable skill by name and receive its instructions. "
        "Use this when a task matches a known skill (e.g. code review, specific "
        "workflow). Passing an unknown name returns the list of available skills."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name to activate.",
            },
        },
        "required": ["skill"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, skills: list[Skill] | None = None) -> None:
        self._skills: dict[str, Skill] = {}
        for s in skills or []:
            self._skills[s.name] = s

    def update_skills(self, skills: list[Skill]) -> None:
        """更新技能集合（用于动态重载）。"""
        self._skills = {s.name: s for s in skills}

    def available_names(self) -> list[str]:
        return sorted(self._skills.keys())

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        name = input_params.get("skill", "")
        skill = self._skills.get(name)
        if skill is None:
            available = ", ".join(self.available_names()) or "(none)"
            return ToolResult(
                tool_use_id="",
                content=f"Unknown skill '{name}'. Available skills: {available}.",
                is_error=True,
                tool_name=self._name,
            )
        return ToolResult(
            tool_use_id="",
            content=f"[Skill: {skill.name}]\n{skill.content}",
            tool_name=self._name,
        )
