"""WebFetch 工具 —— 网络抓取（含 SSRF 防护）。

仅允许 http/https；解析目标主机并拒绝回环/RFC1918/链路本地/云元数据（fail-closed）。
判定实现见 :mod:`nono_agent.utils.net_guard` —— 与工具插件等其它出网路径共用。

它是**原语**而不是厂商能力：零配置、永远可用，模型拿到一个 URL 就能读。
网页搜索/正文抽取这类厂商能力走工具插件（Tavily 等），默认关闭。
"""

from __future__ import annotations

from typing import Any

import httpx

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, truncate_text
from nono_agent.tools.protocol import ToolBase
from nono_agent.utils.net_guard import http_proxy_enabled, is_url_blocked

# 默认响应上限（字符）
_MAX_CHARS = 20_000
_DEFAULT_HEADERS = {
    "User-Agent": "NonoAgent/0.1 (desktop agent; +https://github.com/) httpx",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}


class WebFetchTool(ToolBase):
    """抓取 URL 并返回文本内容。"""

    _name = "web_fetch"
    _description = (
        "Fetch the content of a URL over HTTP(S) and return its text. "
        "Private/loopback addresses are blocked (SSRF protection). "
        "Use this to read a page you already know the URL of; "
        "for searching the web, use a web search tool if one is enabled."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch."},
            "max_chars": {
                "type": "integer",
                "description": f"Maximum characters to return (default {_MAX_CHARS}).",
            },
        },
        "required": ["url"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, timeout: float = 20.0) -> None:
        self._timeout = timeout

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        url = input_params.get("url", "")
        if not url:
            return error_result(self._name, "No URL provided.")

        skip_dns = http_proxy_enabled()
        if is_url_blocked(url, skip_dns=skip_dns):
            return error_result(self._name, f"URL blocked by SSRF policy: {url}")

        max_chars = int(input_params.get("max_chars") or _MAX_CHARS)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=self._timeout,
                headers=_DEFAULT_HEADERS,
            ) as client:
                resp = await client.get(url)
                # 重定向后复检最终地址
                if is_url_blocked(str(resp.url), skip_dns=skip_dns):
                    return error_result(self._name, "Redirect target blocked by SSRF policy.")
                resp.raise_for_status()
                text = resp.text
        except httpx.HTTPError as exc:
            return error_result(self._name, f"Fetch failed: {exc}")

        return ok_result(self._name, truncate_text(text, max_chars))
