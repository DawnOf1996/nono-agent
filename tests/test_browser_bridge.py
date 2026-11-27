"""内置浏览器通道（Agent ↔ 桌面端）的测试。

覆盖三件容易出错的事：没有桌面端时必须**立刻**失败、回执要能被正确配对、
以及 URL 归一不能放进非 http 协议。
"""

from __future__ import annotations

import asyncio

import pytest

from nono_agent.browser import bridge, cdp
from nono_agent.tools.builtin.browser import BrowserOpenTool, BrowserTabsTool, _normalize_url


@pytest.fixture(autouse=True)
def _clean_bridge():
    """总线是模块级状态，逐个测试之间必须清干净，否则会串味。"""
    yield
    bridge._queue.clear()
    bridge._pending.clear()
    bridge._listeners.clear()
    bridge._recent.clear()
    bridge._attached = 0


class TestNoDesktop:
    async def test_fails_immediately_when_not_attached(self):
        """web 版永远不连这条流，工具必须在毫秒级失败而不是干等。"""
        result = await bridge.request({"op": "open", "url": "https://example.com"})
        assert result["ok"] is False
        assert result["error"] == "no_desktop"

    async def test_tool_surfaces_the_reason(self):
        tool = BrowserOpenTool()
        result = await tool.execute({"url": "https://example.com"}, None)
        assert result.is_error
        assert "桌面端" in result.content


class TestRoundTrip:
    async def test_open_reaches_desktop_and_comes_back(self):
        detach = bridge.attach()
        seen: list[dict] = []

        def desk():
            for command in bridge.take_pending():
                seen.append(command)
                bridge.resolve(
                    command["request_id"],
                    {"ok": True, "tab_id": "browser:abc", "url": command.get("url", "")},
                )

        async def caller():
            task = asyncio.create_task(bridge.request({"op": "open", "url": "https://x.dev"}))
            await asyncio.sleep(0)
            desk()
            return await asyncio.wait_for(task, timeout=2)

        try:
            reply = await caller()
        finally:
            detach()

        assert reply["ok"] is True
        assert reply["tab_id"] == "browser:abc"
        assert seen[0]["op"] == "open"
        assert seen[0]["url"] == "https://x.dev"

    async def test_timeout_returns_error_not_hang(self):
        """桌面端在场但不应答：必须按上限失败，不能把会话永久挂住。"""
        detach = bridge.attach()
        try:
            reply = await bridge.request({"op": "open"}, timeout=0.1)
        finally:
            detach()
        assert reply["ok"] is False
        assert reply["error"] == "timeout"

    def test_resolve_unknown_id_is_false(self):
        assert bridge.resolve("不存在的 id", {"ok": True}) is False

    def test_take_pending_drains_queue(self):
        bridge._queue.append({"request_id": "a", "op": "open"})
        first = bridge.take_pending()
        assert len(first) == 1
        assert first[0]["seq"] == 0
        assert bridge.take_pending() == []


class TestBrowserTools:
    def test_open_reports_tab_id(self):
        tool = BrowserOpenTool()
        detach = bridge.attach()

        async def run():
            async def answer():
                await asyncio.sleep(0)
                for command in bridge.take_pending():
                    bridge.resolve(
                        command["request_id"],
                        {"ok": True, "tab_id": "browser:xyz", "title": "示例"},
                    )
            task = asyncio.create_task(tool.execute({"url": "https://example.com"}, None))
            await answer()
            return await asyncio.wait_for(task, timeout=2)

        try:
            result = asyncio.run(run())
        finally:
            detach()
        assert not result.is_error
        assert "browser:xyz" in result.content
        assert "示例" in result.content

    def test_rejects_non_http_url(self):
        result = asyncio.run(BrowserOpenTool().execute({"url": "file:///C:/x"}, None))
        assert result.is_error

    async def test_tabs_reports_empty_panel_clearly(self):
        """没有任何浏览器标签时要说人话，而不是返回空数组让模型自己猜。"""
        detach = bridge.attach()

        async def run():
            async def answer():
                await asyncio.sleep(0)
                for command in bridge.take_pending():
                    bridge.resolve(command["request_id"], {"ok": True, "tabs": []})
            task = asyncio.create_task(BrowserTabsTool().execute({}, None))
            await answer()
            return await asyncio.wait_for(task, timeout=2)

        try:
            result = await run()
        finally:
            detach()
        assert not result.is_error
        assert "no browser tabs open" in result.content


class TestNormalizeUrl:
    def test_bare_domain_gets_https(self):
        assert _normalize_url("example.com/x") == "https://example.com/x"

    def test_http_is_preserved(self):
        assert _normalize_url("http://127.0.0.1:3000") == "http://127.0.0.1:3000"

    def test_rejects_file_scheme(self):
        assert _normalize_url("file:///C:/secret.txt") == ""

    def test_rejects_custom_scheme(self):
        """自定义协议会触发注册过的外部程序，不是「开个网页」该有的能力。"""
        assert _normalize_url("ms-msdt:/id") == ""

    def test_blank_is_blank(self):
        assert _normalize_url("   ") == ""


class TestCdpGate:
    """端口开关的判定。环境变量由主进程在拉起 sidecar 时注入。"""

    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(cdp.ENV_ENABLED, raising=False)
        monkeypatch.delenv(cdp.ENV_PORT, raising=False)
        assert cdp.is_enabled() is False
        assert cdp.unavailable_reason() is not None

    def test_enabled_when_main_process_says_so(self, monkeypatch):
        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.setenv(cdp.ENV_PORT, "9333")
        # 二进制是**可选**依赖：缺它只是读不了页面，开标签照样能用。所以这里
        # 得把它一起备上，unavailable_reason 才应该是 None。
        monkeypatch.setenv(cdp.ENV_BINARY, "agent-browser.exe")
        assert cdp.is_enabled() is True
        assert cdp.unavailable_reason() is None
        assert cdp.port() == 9333

    def test_missing_binary_is_reported_separately(self, monkeypatch):
        """端口开着但没有二进制 —— 要说清楚是「少个文件」，不是「开关没开」。

        这两种情况的解法完全不同：前者去补依赖，后者去拨开关。混为一谈的话
        用户会去设置里反复找那个根本不存在的开关。
        """
        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.delenv(cdp.ENV_BINARY, raising=False)
        reason = cdp.unavailable_reason() or ""
        assert "agent-browser" in reason
        assert "Settings" not in reason

    def test_port_falls_back_when_garbage(self, monkeypatch):
        monkeypatch.delenv(cdp.ENV_PORT, raising=False)
        assert cdp.port() == cdp.DEFAULT_PORT
        monkeypatch.setenv(cdp.ENV_PORT, "不是数字")
        assert cdp.port() == cdp.DEFAULT_PORT

    async def test_diagnose_short_circuits_before_touching_the_network(self, monkeypatch):
        """静态层就不通过时，压根不该去探端口 —— 没开的端口探了也是白探。"""
        monkeypatch.delenv(cdp.ENV_ENABLED, raising=False)

        async def must_not_run():
            raise AssertionError("静态层已经判定不可用，不该再探端口")

        monkeypatch.setattr(cdp, "is_reachable", must_not_run)

        assert "Settings" in (await cdp.diagnose() or "")

    async def test_diagnose_flags_a_port_that_never_bound(self, monkeypatch):
        """开关开着、二进制也在，但端口连不上 —— 必须是**另一种**说法。

        这正是上一个没退干净的进程占住 9222 时的现场：主进程说开了，
        ``/json/version`` 却没人应答。旧实现到这儿就只剩 CLI 那句
        ``Operation timed out``，模型会当成网页坏了反复重试。
        """
        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.setenv(cdp.ENV_BINARY, "agent-browser.exe")
        monkeypatch.setenv(cdp.ENV_PORT, "9222")

        async def unreachable():
            return False

        monkeypatch.setattr(cdp, "is_reachable", unreachable)

        reason = await cdp.diagnose() or ""
        assert "never bound" in reason
        assert "Quit the app fully" in reason
        # 不能把它说成「开关没开」——解法完全不同，那样用户会去面板里白找。
        assert "Settings" not in reason

    async def test_diagnose_is_silent_when_everything_works(self, monkeypatch):
        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.setenv(cdp.ENV_BINARY, "agent-browser.exe")

        async def reachable():
            return True

        monkeypatch.setattr(cdp, "is_reachable", reachable)

        assert await cdp.diagnose() is None

    async def test_no_targets_blames_the_port_not_the_tabs(self, monkeypatch):
        """「一个 target 都没有」要区分端口没绑上 / 真的一个标签都没开。"""
        from nono_agent.browser import targets

        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.setenv(cdp.ENV_BINARY, "agent-browser.exe")

        async def unreachable():
            return False

        async def empty_targets():
            return []

        monkeypatch.setattr(cdp, "is_reachable", unreachable)
        monkeypatch.setattr(cdp, "list_webviews", empty_targets)

        with pytest.raises(targets.TargetResolutionError) as excinfo:
            await targets.resolve("s1", "tab-1")

        assert "never bound" in str(excinfo.value)

    async def test_open_tool_says_why_it_cannot_drive(self, monkeypatch):
        """端口没开时开标签照样成功，但要主动说清「只能开、还不能操作」。

        模型最怕的是「开了标签却什么都没发生」——它会以为是网页坏了然后瞎试。
        """
        monkeypatch.delenv(cdp.ENV_ENABLED, raising=False)
        detach = bridge.attach()

        async def run():
            async def answer():
                await asyncio.sleep(0)
                for command in bridge.take_pending():
                    bridge.resolve(command["request_id"], {"ok": True, "tab_id": "browser:x"})
            task = asyncio.create_task(BrowserOpenTool().execute({"url": "https://x.dev"}, None))
            await answer()
            return await asyncio.wait_for(task, timeout=2)

        try:
            result = await run()
        finally:
            detach()
        assert not result.is_error
        assert "Settings" in result.content
        assert "restart" in result.content
