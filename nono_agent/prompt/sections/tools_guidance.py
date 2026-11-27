"""Tools guidance section —— 工具使用规范与权限/hooks 说明。"""

from __future__ import annotations

from nono_agent.prompt.section import PromptContext, PromptSectionBase


class ToolsGuidanceSection(PromptSectionBase):
    """工具使用规范说明。"""

    _name = "tools_guidance"
    _is_dynamic = False

    def render(self, ctx: PromptContext) -> str | None:
        return (
            "You have access to a set of tools. Use them to read, edit, search, "
            "and run commands in the user's workspace.\n"
            "- Prefer dedicated tools (read_file, write_file, edit_file, glob, grep) "
            "over shell commands when a purpose-built tool exists.\n"
            "- Tool inputs are JSON objects validated against each tool's schema.\n"
            "- Write operations and shell commands may require user permission; "
            "a denial is authoritative and must not be retried without a change in approach."
        )
