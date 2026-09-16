"""WebFetch 工具 —— 网络抓取（含 SSRF 防护）。

仅允许 http/https；解析目标主机并拒绝回环/RFC1918/链路本地/云元数据（fail-closed）。

不把 IANA 特殊登记段一股脑当成私网：Clash / sing-box 的 fake-ip（198.18.0.0/15）
以及 Python ``ipaddress.is_reserved`` 覆盖的大量公网 IPv6 都不是 SSRF 目标。
"""

from __future__ import annotations

import ipaddress
import os
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
_DEFAULT_HEADERS = {
    "User-Agent": "NonoAgent/0.1 (desktop agent; +https://github.com/) httpx",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

# 真正有 SSRF 风险的网段。刻意不用 is_private / is_reserved：
# - is_private 含 198.18.0.0/15（代理 fake-ip）
# - is_reserved 含 ::/8，会把 ::ffff:公网IPv4 全部拦掉
_BLOCKED_V4 = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("224.0.0.0/4"),
)
_BLOCKED_V6 = (
    ipaddress.ip_network("::/128"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("ff00::/8"),
)
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def is_url_blocked(
    url: str,
    resolver: Callable[[str], list[str]] | None = None,
    *,
    skip_dns: bool = False,
) -> bool:
    """SSRF 防护：判断 URL 是否被阻止。

    阻止：非 http/https、回环/RFC1918/链路本地/未指定/组播、无法解析的主机。
    ``skip_dns=True`` 时（例如已配置 HTTP 代理）只检查字面 IP，主机名交给下游解析。
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

    if skip_dns:
        return False

    # DNS 解析（可注入 resolver 便于测试）
    if resolver is not None:
        ips = resolver(host)
    else:
        try:
            infos = socket.getaddrinfo(host, None)
        except OSError:
            return True  # 无法解析 → fail-closed
        ips = [str(info[4][0]) for info in infos]

    if not ips:
        return True

    for ip in ips:
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            continue
        if _is_blocked_ip(addr):
            return True
    return False


def _http_proxy_enabled() -> bool:
    keys = (
        "HTTPS_PROXY",
        "https_proxy",
        "HTTP_PROXY",
        "http_proxy",
        "ALL_PROXY",
        "all_proxy",
    )
    return any(os.environ.get(key) for key in keys)


def _canonical_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return mapped
        if addr in _NAT64_PREFIX:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    addr = _canonical_ip(addr)
    networks: tuple[ipaddress.IPv4Network, ...] | tuple[ipaddress.IPv6Network, ...]
    networks = _BLOCKED_V4 if addr.version == 4 else _BLOCKED_V6
    return any(addr in network for network in networks)


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

        skip_dns = _http_proxy_enabled()
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
