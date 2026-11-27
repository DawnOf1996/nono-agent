"""用 web_fetch 的同一条管线把本地 HTML 转成 Markdown。

``web_fetch`` 只允许 http/https，且 SSRF 闸门（``nono_agent.utils.net_guard``）
拦掉 127.0.0.0/8 —— 本地文件没法走网络那一跳。但抓取之后的处理是纯本地的：

    if looks_like_html(content_type, body):
        text = html_to_markdown(body)
    else:
        text = body

本脚本复刻这段逻辑，把本地文件当成一个静态服务器返回的 ``text/html`` 响应，
从而拿到与 ``web_fetch`` **完全一致**的转换结果（同一个 ``html_to_markdown``
实现、同一套 ``ConversionOptions``）。

用法：

    python runtime-test/html_to_md_probe.py <输入.html> <输出.md>
"""

from __future__ import annotations

import sys
from pathlib import Path

from nono_agent.utils.html_to_markdown import html_to_markdown, looks_like_html

# 静态服务器给 .html 返回的 content-type
_HTML_CONTENT_TYPE = "text/html; charset=utf-8"


def convert_file(src: Path, dst: Path) -> int:
    """把 src 按 web_fetch 的管线转成 Markdown，写进 dst，返回字符数。"""
    body = src.read_text(encoding="utf-8")
    if looks_like_html(_HTML_CONTENT_TYPE, body):
        text = html_to_markdown(body)
    else:
        text = body
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(text, encoding="utf-8", newline="\n")
    return len(text)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    if not src.is_file():
        print(f"input not found: {src}")
        return 1
    size = convert_file(src, dst)
    print(f"{src} -> {dst}: {size} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
