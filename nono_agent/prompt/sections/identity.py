"""Identity section —— 助手身份与基础行为约束。"""

from __future__ import annotations

from nono_agent.prompt.section import CUSTOM_PRIORITY, PromptContext, PromptSectionBase


class IdentitySection(PromptSectionBase):
    """助手身份说明；支持 custom_system_prompt 覆盖。"""

    _name = "identity"
    _priority = CUSTOM_PRIORITY
    _is_dynamic = False

    def render(self, ctx: PromptContext) -> str | None:
        if ctx.custom_system_prompt:
            return ctx.custom_system_prompt
        return (
            "You are Nono Agent, an AI assistant that helps users accomplish "
            "tasks by reasoning and using the tools available to you."
        )
