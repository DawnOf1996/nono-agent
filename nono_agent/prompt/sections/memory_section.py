"""Memory section —— 注入跨会话记忆（消费 memory/ 结果，P2）。"""

from __future__ import annotations

from nono_agent.prompt.section import PromptContext, PromptSectionBase


class MemorySection(PromptSectionBase):
    """注入跨会话记忆（动态）。"""

    _name = "memory"
    _is_dynamic = True

    def render(self, ctx: PromptContext) -> str | None:
        memories = (ctx.memories_text or "").strip()
        if not memories:
            return None
        return (
            "<memory>\n"
            "The following persistent memories apply across sessions:\n"
            f"{memories}\n"
            "</memory>"
        )
