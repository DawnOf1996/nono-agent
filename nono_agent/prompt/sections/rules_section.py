"""Rules section —— 注入已合并的常驻指令（消费 rules/ 结果）。"""

from __future__ import annotations

from nono_agent.prompt.section import PromptContext, PromptSectionBase


class RulesSection(PromptSectionBase):
    """把合并后的 Rules 文本注入系统提示（动态）。"""

    _name = "rules"
    _is_dynamic = True

    def render(self, ctx: PromptContext) -> str | None:
        rules = (ctx.rules_text or "").strip()
        if not rules:
            return None
        return (
            "<rules>\n"
            "The following project/user instructions always apply:\n"
            f"{rules}\n"
            "</rules>"
        )
