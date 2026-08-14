"""ToolSearch 工具 —— 工具过多时按需检索暴露（§2.1，P1）。"""

from __future__ import annotations

from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import ok_result
from nono_agent.tools.protocol import ToolBase

_MAX_RESULTS = 20


class ToolSearchTool(ToolBase):
    """按关键词检索可用工具。"""

    _name = "tool_search"
    _description = (
        "Search the available tools by keyword (matches tool name and description). "
        "Use this to discover which tool to call for a task."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Keyword(s) to search for."},
        },
        "required": ["query"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        query = (input_params.get("query") or "").lower()
        registry = getattr(context, "tool_registry", None)
        if registry is None:
            return ok_result(self._name, "(no tool registry available)")

        matches: list[str] = []
        for tool in registry.all():
            haystack = f"{tool.name} {tool.description}".lower()
            if query and query not in haystack:
                continue
            matches.append(f"- {tool.name}: {tool.description}")

        if not matches:
            return ok_result(self._name, f"No tools matched '{query}'.")

        return ok_result(self._name, "\n".join(matches[:_MAX_RESULTS]))
