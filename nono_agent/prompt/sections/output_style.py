"""Output style section —— 可配置输出风格（P2）。"""

from __future__ import annotations

from nono_agent.prompt.section import PromptContext, PromptSectionBase

# 输出风格 → 指导文本
_STYLES: dict[str, str] = {
    "concise": (
        "Be concise and direct. Prefer short answers and bullet points where "
        "appropriate, and avoid unnecessary preamble."
    ),
    "detailed": (
        "Provide detailed, thorough explanations. Include rationale, edge cases, "
        "and step-by-step reasoning when relevant."
    ),
    "explanatory": (
        "Explain your reasoning as you work. Surface assumptions and trade-offs "
        "so the user can follow your decision process."
    ),
}


class OutputStyleSection(PromptSectionBase):
    """按配置注入输出风格指导（动态）。"""

    _name = "output_style"
    _is_dynamic = True

    def render(self, ctx: PromptContext) -> str | None:
        style = (ctx.output_style or "").strip().lower()
        guidance = _STYLES.get(style)
        if guidance is None:
            return None
        return f"<output_style>\n{guidance}\n</output_style>"
