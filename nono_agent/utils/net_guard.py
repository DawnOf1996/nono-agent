"""出网 SSRF 闸门 —— 所有「让 agent 主动访问某个 URL」的地方都要过这里。

单独成模块而不是挂在某个工具下：`web_fetch`、工具插件的 HTTP 执行器、
将来任何新的出网工具都共用同一份判定。放在工具模块里的后果是：删一个工具
就把安全边界一起删了（``web_search`` 当年就因为 import 了 ``web_fetch``
的私有函数而绑死在它身上）。

刻意不用 ``is_private`` / ``is_reserved``：
- ``is_private`` 含 198.18.0.0/15（Clash / sing-box 的 fake-ip）
- ``is_reserved`` 含 ::/8，会把 ::ffff:公网IPv4 全部拦掉
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse
from collections.abc import Callable

# 真正有 SSRF 风险的网段。用 IPv4Network / IPv6Network 显式构造而不是
# ip_network()：后者无论字面量多明确都返回联合类型，mypy 收不住。
_BLOCKED_V4: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("0.0.0.0/8"),
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("127.0.0.0/8"),
    ipaddress.IPv4Network("169.254.0.0/16"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("224.0.0.0/4"),
)
_BLOCKED_V6: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("::/128"),
    ipaddress.IPv6Network("::1/128"),
    ipaddress.IPv6Network("fc00::/7"),
    ipaddress.IPv6Network("fe80::/10"),
    ipaddress.IPv6Network("ff00::/8"),
)
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")

_PROXY_ENV_KEYS = (
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)


def http_proxy_enabled() -> bool:
    """是否配置了 HTTP 代理。

    有代理时本机不直接连目标主机，DNS 由代理去做，所以本地解析结果
    不足以判定风险 —— 调用方应把它折进 :func:`is_url_blocked` 的
    ``skip_dns``。
    """
    return any(os.environ.get(key) for key in _PROXY_ENV_KEYS)


def is_url_blocked(
    url: str,
    resolver: Callable[[str], list[str]] | None = None,
    *,
    skip_dns: bool = False,
) -> bool:
    """SSRF 防护：判断 URL 是否被阻止。

    阻止：非 http/https、回环/RFC1918/链路本地/未指定/组播、无法解析的主机。
    ``skip_dns=True`` 时（例如已配置 HTTP 代理）只检查字面 IP，主机名交给下游解析。

    默认 **fail-closed**：解析不出结果一律当作该拦。
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


def _canonical_ip(
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """把 IPv4-mapped / NAT64 形式的 IPv6 折回 IPv4 再比对网段。"""
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return mapped
        if addr in _NAT64_PREFIX:
            return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    return addr


def _is_blocked_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    canonical = _canonical_ip(addr)
    blocked = _BLOCKED_V4 if canonical.version == 4 else _BLOCKED_V6
    return any(canonical in network for network in blocked)
