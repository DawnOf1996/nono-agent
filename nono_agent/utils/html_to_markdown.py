"""HTML → Markdown —— 把抓回来的网页转成模型读得动的 Markdown。

底层是 ``html-to-markdown``：**Rust 写的核心 + Python 绑定**（PyPI 上的
``cp310-abi3`` 平台 wheel，零 Python 依赖）。16 种语言共用同一个渲染器，
输出逐字节一致。选它而不是纯 Python 方案，是因为网页动辄几百 KB，
解析开销必须够低 —— 实测 1.1 MB 的 HTML 约 200 ms 转完。

为什么不把原始 HTML 直接丢给模型：

- ``<script>`` / ``<style>`` / 内联 JSON 在页面源码里常常占掉几万字符，
  正文反而被挤出截断线；
- 标题层级、列表、表格埋在标签堆里，模型要自己还原结构，又慢又容易漏。

**只做转换，不做摘要。** 摘要与正文抽取是厂商能力（``tavily_extract``）的活，
这里坚持零配置、本机直连、不把 URL 发给第三方 —— 定位与
:mod:`nono_agent.tools.builtin.web_fetch` 一致。
"""

from __future__ import annotations

import re

from html_to_markdown import (
    ConversionError,
    ConversionOptions,
    PreprocessingOptions,
    convert,
)

# 转换输入上限（字符）。超过就报错而不是硬转 —— 一个几百 MB 的响应不该
# 把内存吃光。正常网页离这个上限很远。
_MAX_INPUT_CHARS = 16 * 1024 * 1024

# 明确是 HTML 的 MIME。``application/xhtml+xml`` 也是 —— XHTML 是 XML 语法，
# 但标签结构与 HTML 相同，转法一致。
_HTML_MIMES = frozenset(
    {
        "text/html",
        "application/xhtml+xml",
        "application/xhtml",
        "application/vnd.wap.xhtml+xml",
    }
)

# 明确不是 HTML 的 MIME —— 走了也不会更差，转了反而会丢信息（JSON 里的
# 大括号、纯文本里的星号与下划线都会被改写）。
_NON_HTML_MIMES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/xml",
        "text/xml",
        "text/plain",
        "text/csv",
        "text/markdown",
        "text/x-markdown",
        "text/rtf",
        "text/calendar",
        "application/javascript",
        "text/javascript",
        "application/ecmascript",
        "text/ecmascript",
        "application/x-yaml",
        "text/yaml",
        "application/yaml",
        "application/pdf",
        "application/rtf",
        "application/octet-stream",
        "application/zip",
        "application/gzip",
        "application/x-tar",
    }
)

# content-type 缺失或不认识时，嗅探正文开头这几个标记。
_HTML_HINT_RE = re.compile(
    r"<\s*(!doctype\s+html|html|head|body|main|article|section|div|p|span|h[1-6]|ul|ol|table)\b",
    re.IGNORECASE,
)
_SNIFF_CHARS = 4096

# 转换选项。``ConversionOptions`` 是 frozen dataclass，可以安全复用。
_OPTIONS = ConversionOptions(
    # 不提取元数据：提取模式会把 <title> 渲染成一段 YAML front matter，
    # 那是我们落盘时自己写的东西，两份 front matter 叠在一起纯属噪音。
    extract_metadata=False,
    # ATX 标题（``# 标题``）—— 模型读起来比 setext 的下划线形式更稳。
    heading_style="atx",
    # standard 预设去掉站点导航、表单与侧栏，留正文。实测正文、标题、
    # 列表、代码块语言标注、表格都完整保留。
    preprocessing=PreprocessingOptions(
        enabled=True,
        preset="standard",
        remove_forms=True,
        remove_navigation=True,
    ),
    max_input_size=_MAX_INPUT_CHARS,
)


def _mime_of(content_type: str | None) -> str:
    """取 content-type 的 MIME 部分（小写、去掉参数）。"""
    if not content_type:
        return ""
    return content_type.split(";", 1)[0].strip().lower()


def looks_like_html(content_type: str | None, body: str) -> bool:
    """判断这份响应该不该按 HTML 处理。

    判据顺序是「先信 content-type，再看正文」：

    - content-type 明确是 HTML → 是；
    - content-type 明确是别的 → 不是（``application/octet-stream`` 这类
      万能类型除外，它只是没说实话）；
    - 缺失或不认识 → 嗅探正文开头有没有 HTML 标记。

    Args:
        content_type: 响应头里的 ``Content-Type``，可为空。
        body: 响应正文（已解码为文本）。

    Returns:
        True 表示应当走 HTML → Markdown 转换。
    """
    mime = _mime_of(content_type)
    if mime in _HTML_MIMES:
        return True
    if mime in _NON_HTML_MIMES:
        return False
    if mime.startswith(("image/", "audio/", "video/", "font/")):
        return False
    return bool(_HTML_HINT_RE.search(body[:_SNIFF_CHARS]))


def html_to_markdown(html: str) -> str:
    """把 HTML 转成 Markdown。

    转不动就原样返回 —— 转换是锦上添花，丢了内容才是事故。

    Args:
        html: HTML 源码。

    Returns:
        Markdown 文本；转换失败或转出来是空的时候退回原文。
    """
    if not html:
        return ""
    try:
        text = convert(html, _OPTIONS).content
    except ConversionError:
        # 解析炸了（超限、不可解析等）：退回原文，至少内容还在。
        return html
    if not text or not text.strip():
        # 转出来是空的：页面本身没有可读文本（纯前端渲染、或内容全在
        # script 里）。退回原文，让模型自己判断这页是什么形态。
        return html
    return text.strip()


__all__ = ["html_to_markdown", "looks_like_html"]
