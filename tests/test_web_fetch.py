"""WebFetch 工具测试 —— HTML 转 Markdown、超限落资产、SSRF 闸门。

URL 一律用**字面 IP**：:func:`is_url_blocked` 对字面 IP 不做 DNS 解析，
所以这些用例既不依赖网络也不会因为解析失败而走进不同的分支。
"""

from __future__ import annotations

from typing import Any

import httpx

from nono_agent.assets import AssetStore
from nono_agent.permissions.sandbox import PathSandbox
from nono_agent.tools.builtin.web_fetch import WebFetchTool
from nono_agent.tools.context import ToolUseContext
from nono_agent.utils.html_to_markdown import html_to_markdown, looks_like_html

# 字面公网 IP：解析这一步不查 DNS。
_URL = "https://93.184.216.34/page"

_HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>示例页</title>
  <style>.hidden { display: none; }</style>
</head>
<body>
  <nav><a href="/home">首页</a></nav>
  <h1>标题一</h1>
  <p>正文段落，带一个 <b>粗体</b> 和一个 <a href="https://example.com/x">链接</a>。</p>
  <h2>标题二</h2>
  <ul><li>第一项</li><li>第二项</li></ul>
  <pre><code class="language-python">print("hi")</code></pre>
  <table><tr><th>列 A</th><th>列 B</th></tr><tr><td>值 1</td><td>值 2</td></tr></table>
  <script>console.log("不该出现");</script>
</body>
</html>"""


def _stub_factory(handler: Any) -> Any:
    """返回 httpx.MockTransport 支撑的客户端工厂（测试里替掉真实网络）。"""

    def factory(timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def _respond(body: str, content_type: str = "text/html; charset=utf-8") -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=body,
            headers={"content-type": content_type},
            request=request,
        )

    return _stub_factory(handler)


def _ctx(tmp_path) -> ToolUseContext:
    return ToolUseContext(cwd=str(tmp_path), sandbox=PathSandbox(tmp_path))


def _store(tmp_path) -> AssetStore:
    """资产存储 —— 指向临时目录，测试不碰真实数据目录。"""
    return AssetStore(tmp_path / "assets")


class TestLooksLikeHtml:
    def test_explicit_html_mime(self):
        assert looks_like_html("text/html; charset=utf-8", "")
        assert looks_like_html("application/xhtml+xml", "")

    def test_explicit_non_html_mime(self):
        assert not looks_like_html("application/json", '{"a": 1}')
        assert not looks_like_html("text/plain", "hello")
        assert not looks_like_html("application/pdf", "")

    def test_binary_mime_is_not_html(self):
        assert not looks_like_html("image/png", "")

    def test_sniff_when_mime_missing(self):
        assert looks_like_html(None, "<!DOCTYPE html><html><body>hi</body></html>")
        assert looks_like_html("", "<div>片段</div>")

    def test_plain_text_is_not_html(self):
        assert not looks_like_html(None, "just some prose, no markup")
        assert not looks_like_html(None, '{"json": true}')


class TestHtmlToMarkdown:
    def test_headings_use_atx(self):
        md = html_to_markdown("<h1>标题一</h1><h2>标题二</h2>")
        assert "# 标题一" in md
        assert "## 标题二" in md

    def test_script_and_style_are_dropped(self):
        md = html_to_markdown(_HTML_PAGE)
        assert "console.log" not in md
        assert "display: none" not in md

    def test_links_are_preserved(self):
        md = html_to_markdown(_HTML_PAGE)
        assert "[链接](https://example.com/x)" in md

    def test_lists_and_tables(self):
        md = html_to_markdown(_HTML_PAGE)
        assert "- 第一项" in md
        assert "| 列 A | 列 B |" in md

    def test_code_block_keeps_language(self):
        md = html_to_markdown(_HTML_PAGE)
        assert "```python" in md
        assert 'print("hi")' in md

    def test_site_chrome_is_dropped(self):
        """导航与表单是站点样板，不是正文 —— 留着只会挤占模型的上下文。"""
        md = html_to_markdown(
            "<html><body><nav><a href='/'>首页</a></nav>"
            "<form><input name='q'></form>"
            "<article><h1>正文标题</h1><p>正文内容</p></article></body></html>"
        )
        assert "正文标题" in md
        assert "正文内容" in md
        assert "首页" not in md

    def test_no_html_tags_leak(self):
        md = html_to_markdown(_HTML_PAGE)
        assert "<h1>" not in md
        assert "<script" not in md

    def test_plain_text_passes_through(self):
        assert html_to_markdown("hello world") == "hello world"

    def test_empty_input_does_not_raise(self):
        assert html_to_markdown("") == ""


class TestWebFetchToolSchema:
    def test_max_chars_is_not_a_parameter(self):
        # 长度不再由模型决定：超限就落盘，参数留给模型只会让它把阈值当旋钮拧。
        props = WebFetchTool()._input_schema["properties"]
        assert set(props) == {"url"}
        assert "max_chars" not in props


class TestWebFetchTool:
    async def test_html_is_converted_to_markdown(self, tmp_path):
        tool = WebFetchTool(client_factory=_respond(_HTML_PAGE), store=_store(tmp_path))
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))
        assert not result.is_error
        assert "# 标题一" in result.content
        assert "console.log" not in result.content
        assert "<h1>" not in result.content

    async def test_json_is_returned_verbatim(self, tmp_path):
        payload = '{"answer": 42}'
        tool = WebFetchTool(
            client_factory=_respond(payload, "application/json"), store=_store(tmp_path)
        )
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))
        assert result.content.strip() == payload

    async def test_short_page_is_not_saved_as_asset(self, tmp_path):
        store = _store(tmp_path)
        tool = WebFetchTool(client_factory=_respond(_HTML_PAGE), store=store)
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))
        assert "saved to asset" not in result.content
        assert not store.root.exists()

    async def test_long_page_is_saved_as_asset(self, tmp_path):
        store = _store(tmp_path)
        long_html = "<html><body>" + "".join(
            f"<p>第 {i} 段正文内容，用来把长度顶过阈值。</p>" for i in range(2000)
        ) + "</body></html>"
        tool = WebFetchTool(client_factory=_respond(long_html), store=store)
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))
        assert not result.is_error
        assert "full markdown saved to asset" in result.content

        # 落在资产目录里，按 assets/YYYY/MM/DD 分层（与截图同一套目录）
        files = list(store.root.rglob("*.md"))
        assert len(files) == 1
        saved = files[0].read_text(encoding="utf-8")
        assert "第 1999 段正文内容" in saved
        # front matter 记下来源 URL，模型读回文件时知道这页是哪来的
        assert saved.startswith("---\nsource: ")
        assert _URL in saved

        # 返回给模型的是 assets/ 相对路径，read_file 认这个前缀
        relative = files[0].relative_to(store.root).as_posix()
        assert f"assets/{relative}" in result.content

    async def test_asset_date_layout(self, tmp_path):
        store = _store(tmp_path)
        long_html = "<html><body>" + ("<p>内容</p>" * 5000) + "</body></html>"
        tool = WebFetchTool(client_factory=_respond(long_html), store=store)
        await tool.execute({"url": _URL}, _ctx(tmp_path))
        parts = store.day_dir().relative_to(store.root).parts
        assert len(parts) == 3
        assert len(parts[0]) == 4 and parts[0].isdigit()
        assert len(parts[1]) == 2 and parts[1].isdigit()
        assert len(parts[2]) == 2 and parts[2].isdigit()

    async def test_saved_asset_is_readable_by_read_file(self, tmp_path):
        """资产在数据目录里，不在工作区沙箱内 —— read_file 得能按 assets/ 路径读回来。"""
        from nono_agent.tools.builtin.read import ReadFileTool

        store = _store(tmp_path)
        long_html = (
            "<html><body>" + "".join(f"<p>段落 {i}</p>" for i in range(5000)) + "</body></html>"
        )
        tool = WebFetchTool(client_factory=_respond(long_html), store=store)
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))

        asset_path = result.content.split(" at assets/")[1].strip().rstrip("]")
        read_result = await ReadFileTool(store=store).execute(
            {"path": f"assets/{asset_path}"}, _ctx(tmp_path)
        )
        assert not read_result.is_error
        assert "段落 1999" in read_result.content

    async def test_saved_asset_is_readable_by_asset_id(self, tmp_path):
        from nono_agent.tools.builtin.read import ReadFileTool

        store = _store(tmp_path)
        long_html = (
            "<html><body>" + "".join(f"<p>段落 {i}</p>" for i in range(5000)) + "</body></html>"
        )
        tool = WebFetchTool(client_factory=_respond(long_html), store=store)
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))

        asset_id = result.content.split("saved to asset ")[1].split(" at ")[0].strip()
        read_result = await ReadFileTool(store=store).execute({"path": asset_id}, _ctx(tmp_path))
        assert not read_result.is_error
        assert "段落 1999" in read_result.content

    async def test_preview_is_capped(self, tmp_path):
        long_html = "<html><body>" + ("<p>" + "字" * 500 + "</p>") * 100 + "</body></html>"
        tool = WebFetchTool(client_factory=_respond(long_html), store=_store(tmp_path))
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))
        preview = result.content.split("\n\n[content truncated")[0]
        assert len(preview) <= 2000

    async def test_truncation_note_is_at_the_tail(self, tmp_path):
        """工具结果还有一层 token 预算截断（保头 500 / 尾 200 token），
        路径落在尾部才不会被二次截断吃掉。"""
        long_html = "<html><body>" + "<p>内容段落</p>" * 5000 + "</body></html>"
        tool = WebFetchTool(client_factory=_respond(long_html), store=_store(tmp_path))
        result = await tool.execute({"url": _URL}, _ctx(tmp_path))
        assert result.content.rstrip().endswith("]")

    async def test_private_address_is_blocked(self, tmp_path):
        tool = WebFetchTool(client_factory=_respond(_HTML_PAGE), store=_store(tmp_path))
        result = await tool.execute({"url": "http://127.0.0.1/x"}, _ctx(tmp_path))
        assert result.is_error
        assert "SSRF" in result.content

    async def test_missing_url(self, tmp_path):
        tool = WebFetchTool(client_factory=_respond(_HTML_PAGE), store=_store(tmp_path))
        result = await tool.execute({}, _ctx(tmp_path))
        assert result.is_error
