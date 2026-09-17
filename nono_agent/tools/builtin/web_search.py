"""WebSearch 工具 —— 可配置搜索端点（含 SSRF 防护）。

默认从环境变量 ``NONO_SEARCH_URL`` / ``NONO_SEARCH_API_KEY`` 读取搜索端点；
也可通过 ``search_fn`` 注入自定义实现（测试/厂商适配）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result
from nono_agent.tools.builtin.web_fetch import _http_proxy_enabled, is_url_blocked
from nono_agent.tools.protocol import ToolBase

SearchFn = Callable[[str, int], Awaitable[list[dict[str, str]]]]


class WebSearchTool(ToolBase):
    """网络搜索。"""

    _name = "web_search"
    _description = (
        "Search the web and return a list of results (title/url/snippet). "
        "Requires a search endpoint to be configured."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "The search query."},
            "max_results": {"type": "integer", "description": "Max results (default 5)."},
        },
        "required": ["query"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, search_fn: SearchFn | None = None) -> None:
        self._search_fn = search_fn

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        query = (input_params.get("query") or "").strip()
        if not query:
            return error_result(self._name, "No query provided.")
        max_results = int(input_params.get("max_results") or 5)

        results = await self._run(query, max_results)
        if results is None:
            return error_result(
                self._name,
                "Web search is not configured. Set NONO_SEARCH_URL and "
                "NONO_SEARCH_API_KEY, or inject a search function.",
            )

        if not results:
            return ok_result(self._name, "No results.")

        lines = [
            f"{i + 1}. {r.get('title', '')} ({r.get('url', '')})\n   {r.get('snippet', '')}"
            for i, r in enumerate(results[:max_results])
        ]
        return ok_result(self._name, "\n".join(lines))

    async def _run(self, query: str, max_results: int) -> list[dict[str, str]] | None:
        if self._search_fn is not None:
            return await self._search_fn(query, max_results)

        url = os.environ.get("NONO_SEARCH_URL")
        key = os.environ.get("NONO_SEARCH_API_KEY")
        if not url:
            return None
        if is_url_blocked(url, skip_dns=_http_proxy_enabled()):
            return None

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    url,
                    json={"query": query, "max_results": max_results},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return None

        results = data.get("results", data) if isinstance(data, dict) else data
        if isinstance(results, list):
            return [r for r in results if isinstance(r, dict)]
        return None
