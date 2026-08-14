"""Skills section —— 注入可用技能清单（供模型选择激活）。"""

from __future__ import annotations

from nono_agent.prompt.section import PromptContext, PromptSectionBase


class SkillsSection(PromptSectionBase):
    """列出可用技能（动态）。"""

    _name = "skills"
    _is_dynamic = True

    def render(self, ctx: PromptContext) -> str | None:
        skills = (ctx.skills_text or "").strip()
        if not skills:
            return None
        return (
            "<available_skills>\n"
            "The following reusable skills are available. Activate one with the "
            "'skill' tool when a task matches.\n"
            f"{skills}\n"
            "</available_skills>"
        )
