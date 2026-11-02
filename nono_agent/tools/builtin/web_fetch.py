"""WebFetch 工具 —— 网络抓取（HTML 转 Markdown + 超限落资产）。

仅允许 http/https；解析目标主机并拒绝回环/RFC1918/链路本地/云元数据（fail-closed）。
判定实现见 :mod:`nono_agent.utils.net_guard` —— 与工具插件等其它出网路径共用。

它是**原语**而不是厂商能力：零配置、永远可用，模型拿到一个 URL 就能读。
网页搜索/正文抽取这类厂商能力走工具插件（Tavily 等），默认关闭。

给模型看的东西有两个讲究：

1. **HTML 转 Markdown**（:mod:`nono_agent.utils.html_to_markdown`）—— 原始
   HTML 里 script/style 能占掉几万字符，正文反而被挤出截断线；
2. **超限落资产** —— 转换后仍超过 ``_MAX_CHARS`` 的内容写进资产目录
   （:mod:`nono_agent.assets`，与截图同目录同命名规则），模型只拿到开头预览
   + 资产路径，剩下的自己用 ``read_file`` 取。
"""

from __future__ import annotations

import re
import time
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from nono_agent.assets import AssetStore, get_asset_store
from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result
from nono_agent.tools.protocol import ToolBase
from nono_agent.utils.html_to_markdown import html_to_markdown, looks_like_html
from nono_agent.utils.net_guard import http_proxy_enabled, is_url_blocked

# 转换后的正文超过这个字符数就落盘
_MAX_CHARS = 20_000
# 落盘时给模型的开头预览长度
_PREVIEW_CHARS = 2_000
# 落盘资产的来源标记（写进 sidecar 与当天索引，便于排查）
_SOURCE_KIND = "web-fetch"

_DEFAULT_HEADERS = {
    "User-Agent": "NonoAgent/0.1 (desktop agent; +https://github.com/) httpx",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
}

_UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# 测试替换这个工厂函数来打桩网络层（httpx.MockTransport）。
ClientFactory = Callable[[float], httpx.AsyncClient]


def build_client(timeout_s: float) -> httpx.AsyncClient:
    """默认客户端工厂。

    跟随重定向：重定向后的目标要重新过一遍 SSRF 判定（见 :meth:`WebFetchTool.execute`），
    所以不能像工具插件那样把 ``follow_redirects`` 关掉。
    """
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=True,
        headers=_DEFAULT_HEADERS,
    )


class WebFetchTool(ToolBase):
    """抓取 URL 并返回正文（HTML 转 Markdown，超限落盘）。"""

    _name = "web_fetch"
    _description = (
        "Fetch the content of a URL over HTTP(S) and return its text. "
        "HTML pages are converted to Markdown -- scripts, styles, site "
        "navigation and forms are dropped, while the article body, headings, "
        "lists, code blocks and tables are kept. JSON and plain text are "
        "returned as-is. "
        "Private/loopback addresses are blocked (SSRF protection). "
        "Use this to read a page you already know the URL of; "
        "for searching the web, use a web search tool if one is enabled. "
        "A very long page is saved as an asset under the assets directory: "
        "you get the first 2000 characters plus the asset path, and can read "
        "the rest with read_file."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "The URL to fetch."},
        },
        "required": ["url"],
    }
    _is_concurrent_safe = True
    # 抓取是读操作，落盘只是超限时把同一次读的完整内容留在工作区。
    # 标成写副作用会让 plan 模式与无人值守的 server 模式直接拒绝这个工具 ——
    # 那等于为一次缓存掐掉主功能。落盘目录是工作区自己的 `.nono-agent/`。
    _has_write_side_effects = False

    def __init__(
        self,
        timeout: float = 20.0,
        *,
        client_factory: ClientFactory | None = None,
        store: AssetStore | None = None,
    ) -> None:
        self._timeout = timeout
        self._client_factory = client_factory or build_client
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        url = input_params.get("url", "")
        if not url:
            return error_result(self._name, "No URL provided.")

        skip_dns = http_proxy_enabled()
        if is_url_blocked(url, skip_dns=skip_dns):
            return error_result(self._name, f"URL blocked by SSRF policy: {url}")

        try:
            async with self._client_factory(self._timeout) as client:
                resp = await client.get(url)
                # 重定向后复检最终地址
                if is_url_blocked(str(resp.url), skip_dns=skip_dns):
                    return error_result(self._name, "Redirect target blocked by SSRF policy.")
                resp.raise_for_status()
                body = resp.text
                content_type = resp.headers.get("content-type")
        except httpx.HTTPError as exc:
            return error_result(self._name, f"Fetch failed: {exc}")

        if looks_like_html(content_type, body):
            text = html_to_markdown(body)
        else:
            text = body

        if len(text) <= _MAX_CHARS:
            return ok_result(self._name, text)

        return self._spill(text, url, context)

    # ------------------------------------------------------------------
    # 超限落资产
    # ------------------------------------------------------------------

    def _spill(self, text: str, url: str, context: Any) -> ToolResult:
        """正文太长：写进资产目录，模型只拿开头预览 + 资产路径。

        提示语放在**尾部**：工具结果还有一层 token 预算截断
        （``pipeline/tool_result_budget.py``，保头 500 / 尾 200 token），
        路径落在尾部才不会被二次截断吃掉。

        落资产目录而不是工作区：资产目录是数据目录（与截图同处），不污染
        用户仓库。代价是它在工作区沙箱之外，所以 ``read_file`` 专门认
        ``assets/`` 前缀与资产 ID（见 :class:`nono_agent.tools.builtin.read.ReadFileTool`）。
        """
        preview = _head_preview(text, _PREVIEW_CHARS)
        store = self._store or get_asset_store()
        try:
            ref = store.save_text(
                _front_matter(url) + text,
                media_type="text/markdown",
                source_kind=_SOURCE_KIND,
                session_id=getattr(context, "session_id", "") or None,
                file_name=_file_name(url),
            )
        except (OSError, ValueError) as exc:
            # 落盘失败不能让整个抓取失败：模型至少还能拿到开头。
            return ok_result(
                self._name,
                f"{preview}\n\n[content truncated: {len(text)} characters total; "
                f"saving failed: {exc}]",
            )
        return ok_result(
            self._name,
            f"{preview}\n\n[content truncated; full markdown saved to asset "
            f"{ref.asset_id} at assets/{ref.relative_path}]",
        )


def _head_preview(text: str, limit: int) -> str:
    """取开头预览。尽量断在行边界上。"""
    head = text[:limit]
    cut = head.rfind("\n")
    # 只在末尾 20% 内回退到行边界 —— 为了一行完整性丢掉几百字符不划算。
    if cut > int(limit * 0.8):
        head = head[:cut]
    return head


def _front_matter(url: str) -> str:
    """落盘文件的 front matter —— 记下这页是哪来的。"""
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return f"---\nsource: {url}\nfetched_at: {stamp}\n---\n\n"


def _file_name(url: str) -> str:
    """给资产文件起个能看出来源的名字：``<host>-<路径 slug>.md``。"""
    parsed = urllib.parse.urlparse(url)
    host = _UNSAFE_NAME_RE.sub("-", parsed.hostname or "page")
    slug = _UNSAFE_NAME_RE.sub("-", (parsed.path or "").strip("/")) or "index"
    slug = slug[-60:].strip("-") or "index"
    return f"{host}-{slug}.md"
