"""鉴权注入 —— 三种方式共用一套实现，别在两个地方各写一遍。

| auth.type | 注入位置 | 适用 |
|-----------|----------|------|
| ``bearer`` | ``Authorization: Bearer <key>`` | 绝大多数 MCP 服务（含 Tavily 远程） |
| ``header`` | 自定义头 | 厂商自定义，如 ``X-Api-Key`` |
| ``query``  | ``?<param>=<key>`` | 把 Key 设计在 URL 上的服务 |

**query 形式是兜底，不是首选。** Key 进了 URL 就会进访问日志、代理日志和异常
消息，所以这里统一做两件事：按 URL 规则转义（否则带 ``&``/``+``/``/`` 的 Key
会把查询串截断成半截），以及在任何对外文本里把它抹掉。
"""

from __future__ import annotations

from urllib.parse import quote, urlsplit, urlunsplit

REDACTED = "***"

#: 低于这个长度的"密钥"不做子串替换 —— 否则 ``k`` 会把 ``skipped`` 变成
#: ``s***ipped``，报错文本直接失去意义。真实 API Key 几乎都远超这个长度，
#: 而 URL 里的精确脱敏走的是 :func:`redact_url`（按参数名匹配），不受影响。
MIN_REDACT_LEN = 6


def append_query_auth(url: str, param: str, secret: str) -> str:
    """把 ``param=secret`` 追加到 URL 查询串上（secret 按 URL 规则转义）。

    不能直接 f-string 拼：Key 里出现 ``&``、``=``、``+`` 时会把查询串截断，
    服务端收到的是半截 Key，报错还很难看懂。
    """
    if not param or not secret:
        return url
    parts = urlsplit(url)
    query = parts.query
    encoded = f"{quote(param, safe='')}={quote(secret, safe='')}"
    joined = f"{query}&{encoded}" if query else encoded
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, joined, parts.fragment)
    )


def header_auth(auth_type: str, header: str, prefix: str, secret: str) -> dict[str, str]:
    """按类型产出请求头；``query`` 类型不走这里（返回空）。"""
    if not secret or auth_type not in {"bearer", "header"}:
        return {}
    if auth_type == "bearer":
        return {header or "Authorization": f"{prefix or 'Bearer '}{secret}"}
    return {header or "Authorization": secret}


def redact(text: str, secret: str) -> str:
    """把 ``secret`` 从文本里抹掉 —— 用于任何要往日志/接口/界面冒的字符串。

    这是**兜底**：精确的做法是按参数名脱敏（见 server 侧 ``redact_url``），
    这里只能按子串撞。转义形式一并替换：URL 里出现的是 ``quote`` 后的样子，
    只抹原文等于没抹。

    过短的 secret 直接放弃替换 —— 宁可漏一把没意义的短串，也不要把正常
    报错糊成 ``s***ipped``。
    """
    if not secret or len(secret) < MIN_REDACT_LEN:
        return text
    out = text.replace(secret, REDACTED)
    escaped = quote(secret, safe="")
    if escaped != secret:
        out = out.replace(escaped, REDACTED)
    return out
