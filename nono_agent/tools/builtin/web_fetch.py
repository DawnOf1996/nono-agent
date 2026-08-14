"""WebFetch 工具 —— 网络抓取（含 SSRF 防护）。

仅允许 http/https；解析目标主机并拒绝私网/回环/链路本地地址（fail-closed）。
"""

from __future__ import annotations

import ipaddress
import socket
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, truncate_text
from nono_agent.tools.protocol import ToolBase

# 默认响应上限（字符）
_MAX_CHARS = 20_000


def is_url_blocked(
    url: str,
    resolver: Callable[[str], list[str]] | None = None,
) -> bool:
    """SSRF 防护：判断 URL 是否被阻止。

    阻止：非 http/https、私网/回环/链路本地/未指定/组播地址、无法解析的主机。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return True
    host = parsed.hostname
    if not host:
        return True

    # 字面 IP 地址
    try:
        addr = ipaddress.ip_address(host)
        return _is_blocked_ip(addr)
    except ValueError:
        pass

    # DNS 解析（可注入 resolver 便于测试）
    if resolver is not None:
        ips = resolver(host)
    else:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return True  # 无法解析 → fail-closed
        ips = [str(info[4][0]) for info in infos]

    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            return True
    return False


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr.is_multicast
        or addr.is_reserved
    )


class WebFetchTool(ToolBase):
    """抓取 URL 并返回文本内容。"""

    _name = "web_fetch"
    _description = (
        "Fetch the content of a URL over HTTP(S) and return its text. "
        "Private/loopback addresses are blocked (SSRF protection). "
        "For web search, use the web_search tool if available."
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

        if is_url_blocked(url):
            return error_result(self._name, f"URL blocked by SSRF policy: {url}")

        max_chars = int(input_params.get("max_chars") or _MAX_CHARS)

        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=self._timeout
            ) as client:
                resp = await client.get(url)
                # 重定向后复检最终地址
                if is_url_blocked(str(resp.url)):
                    return error_result(self._name, "Redirect target blocked by SSRF policy.")
                resp.raise_for_status()
                text = resp.text
        except httpx.HTTPError as exc:
            return error_result(self._name, f"Fetch failed: {exc}")

        return ok_result(self._name, truncate_text(text, max_chars))
