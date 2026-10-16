"""agent-browser 封装层的测试。

**不 spawn 真二进制**：CLI 的行为（daemon 吊住管道、ref 语义）靠 mock 验不住，
得靠真机冒烟。这里只钉住「哪些参数会被拼出去」和「输出怎么解析」——那两处
才是封装真正会写错的地方。

真机链路由 ``docs`` 里的手工步骤覆盖：对着本机 Electron 跑一遍
``tab list`` / ``tab <targetId>`` / ``snapshot -i`` / ``fill @e28 你好``。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from nono_agent.browser import cdp, cli, targets
from nono_agent.tools.builtin import browser as browser_tools
from nono_agent.tools.builtin.browser import (
    BrowserNavigateTool,
    BrowserReadTool,
    BrowserScrollTool,
    BrowserTypeTool,
    _act,
    _check_ref,
    _normalize_url,
)


@pytest.fixture(autouse=True)
def _reset_binding(monkeypatch):
    """绑定和 pin 状态都是进程级全局，测试之间必须复位。

    顺带把 ``cdp.list_webviews`` 钉成一份**合成**数据。``targets.resolve()`` 现在
    会验活（这正是它该做的 —— 缓存的 target 可能已经没了），不去 stub 的话测试
    就会去戳本机真实的 9222：机器上开没开着应用都能让同一份测试红，而且慢。
    """
    targets.clear()
    browser_tools._forget_pin()

    # stub 的是 ``list_targets``（更底层）而不是 ``list_webviews``：后者正是
    # TestWebviewFilter 要测的东西，在那一层打桩会把被测函数本身换掉。
    async def fake_targets():
        return [
            {"id": "T1", "type": "webview", "title": "T", "url": "https://x.dev"},
            {"id": "T0", "type": "page", "title": "主窗口", "url": "https://x.dev/"},
        ]

    monkeypatch.setattr(cdp, "list_targets", fake_targets)
    yield
    targets.clear()
    browser_tools._forget_pin()


def _script_cli(
    monkeypatch, script: list[cli.AgentBrowserResult], *, pin: cli.AgentBrowserResult | None = None
) -> list[list[str]]:
    """把 ``cli.run_async`` 换成剧本，并记录每次拼出去的参数。

    浏览器工具走的是 ``cli.run_async``（纯文本 stdout 那条路），所以替掉它就能
    在不 spawn 真二进制的前提下观察「有没有多发一条 ``tab````。

    ``tab`` 一律按成功返回，剧本只描述真正的动作 —— 否则每多一次重钉就要多写一
    个占位结果，测试会因下标错位而失败，却跟被测行为无关。

    ``pin`` 可以塞一个**失败**的 ``tab`` 回执，用来演练「钉标签失败」那条路径
    —— 那正是 ``_act`` 会把动作落到主窗口上的现场。
    """
    calls: list[list[str]] = []
    pending = list(script)
    pin_result = pin if pin is not None else _ok()

    async def fake_run_async(args, *, timeout_s: float = 30.0):
        calls.append(list(args))
        if args and args[0] == "tab":
            return pin_result
        return pending.pop(0) if pending else _ok()

    monkeypatch.setattr(cli, "run_async", fake_run_async)
    return calls


def _ok(stdout: str = "") -> cli.AgentBrowserResult:
    return cli.AgentBrowserResult(stdout=stdout, stderr="", exit_code=0)


def _failed(stderr: str, exit_code: int = 1) -> cli.AgentBrowserResult:
    return cli.AgentBrowserResult(stdout="", stderr=stderr, exit_code=exit_code)



class _FakeProc:
    pid = 4242
    returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


class TestBinaryResolution:
    def test_missing_env_explains_where_to_put_it(self, monkeypatch):
        monkeypatch.delenv(cdp.ENV_BINARY, raising=False)
        assert cdp.binary_path() == ""
        # 报错必须说清楚「去哪儿放」，否则模型和人都只能干瞪眼。
        with pytest.raises(cli.AgentBrowserError) as exc:
            cli.binary()
        assert "resources" in str(exc.value)

    def test_missing_file_is_reported(self, monkeypatch, tmp_path):
        monkeypatch.setenv(cdp.ENV_BINARY, str(tmp_path / "nope.exe"))
        with pytest.raises(cli.AgentBrowserError) as exc:
            cli.binary()
        assert "does not exist" in str(exc.value)

    def test_disabled_cdp_points_at_plugin_panel(self, monkeypatch):
        monkeypatch.delenv(cdp.ENV_ENABLED, raising=False)
        reason = cdp.unavailable_reason() or ""
        # 开关早就从设置页搬到插件面板了，文案没跟着搬会让人去找一个不存在的选项。
        assert "Settings" in reason
        assert "no bundled agent-browser executable" not in reason


class TestArgBuilding:
    def test_pin_tab_is_always_present(self, monkeypatch, tmp_path):
        """--pin-tab 是必需品：DevTools 一打开就会把 active target 抢走。"""
        exe = tmp_path / "ab.exe"
        exe.write_bytes(b"")
        monkeypatch.setenv(cdp.ENV_BINARY, str(exe))
        monkeypatch.setenv(cdp.ENV_PORT, "9222")

        seen: list[list[str]] = []

        def fake_spawn(argv, out_path, err_path, timeout_s):
            seen.append(list(argv))
            out_path.write_text("ok", encoding="utf-8")
            err_path.write_text("", encoding="utf-8")
            return cli.AgentBrowserResult(stdout="ok", stderr="", exit_code=0)

        monkeypatch.setattr(cli, "_spawn_and_wait", fake_spawn)
        asyncio.run(cli.run_async(["snapshot", "-i"]))

        assert "--pin-tab" in seen[0]
        assert "--cdp" in seen[0]
        assert seen[0][-2:] == ["snapshot", "-i"]

    def test_ref_is_validated_before_reaching_the_cli(self):
        assert _check_ref("e12") == "e12"
        assert _check_ref("@e12") == "e12"          # 模型很爱带 @ 前缀
        assert _check_ref(" e3 ") == "e3"
        # 这些必须被挡住：ref 会直接拼进 argv，能塞进 --cdp 9999 就等于
        # 给了模型一个「换个目标端」的办法。
        assert _check_ref("--cdp 9999") == ""
        assert _check_ref("e12; rm -rf /") == ""
        assert _check_ref("") == ""
        assert _check_ref(None) == ""


class TestJsonParsing:
    def test_plain_json(self):
        payload = cli._parse_json('{"success":true,"data":{"a":1}}')
        assert payload["data"] == {"a": 1}

    def test_ignores_progress_noise_before_json(self):
        payload = cli._parse_json('connecting...\n{"success":true,"data":{"a":1}}\n')
        assert payload["data"] == {"a": 1}

    def test_garbage_raises(self):
        with pytest.raises(cli.AgentBrowserError):
            cli._parse_json("not json at all")

    def test_tab_gone_gets_a_recovery_hint(self):
        message = cli._describe_error(
            {"error": {"code": "tab_gone", "message": "bound tab closed"}}
        )
        # 模型拿到裸英文错误基本只会重试，报错必须先告诉它下一步干什么。
        assert "browser_tabs" in message
        assert "browser_snapshot" in message

    def test_plain_string_error_is_passed_through(self):
        assert cli._describe_error({"error": "boom"}) == "boom"


class TestTargetMath:
    def test_new_since_is_exact_when_one_added(self):
        before = {"a", "b"}
        after = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert targets.new_since(before, after) == "c"

    def test_new_since_refuses_to_guess_when_ambiguous(self):
        before: set[str] = set()
        after = [{"id": "c"}, {"id": "d"}]
        # 两个同时冒出来 —— 宁可不绑，也不能把标签页认错。
        assert targets.new_since(before, after) == ""

    def test_new_since_returns_empty_when_none_added(self):
        assert targets.new_since({"a"}, [{"id": "a"}]) == ""

    def test_snapshot_ids_drops_empty(self):
        assert targets.snapshot_ids([{"id": "a"}, {}, {"id": ""}]) == {"a"}

    @pytest.mark.parametrize(
        ("left", "right"),
        [
            ("https://a.com", "https://a.com/"),
            ("https://a.com/", "https://a.com/#top"),
            ("https://a.com/x#y", "https://a.com/x"),
        ],
    )
    def test_same_page_ignores_trailing_slash_and_fragment(self, left, right):
        assert targets._same_page(left, right) is True

    def test_different_paths_are_not_same_page(self):
        assert targets._same_page("https://a.com/x", "https://a.com/y") is False


class TestBinding:
    def test_bound_roundtrip(self):
        targets.bind("tab-1", "TARGET")
        assert targets.bound() == ("tab-1", "TARGET")

    async def test_resolve_without_binding_explains_what_to_do(self):
        with pytest.raises(targets.TargetResolutionError) as exc:
            await targets.resolve()
        # 没有绑定时不能随便挑一个 target —— 那会让模型动一个用户没选过的页面。
        assert "browser_tabs" in str(exc.value)

    async def test_resolve_prefers_bound_target(self, monkeypatch):
        targets.bind("tab-1", "TARGET")

        async def _alive(tid: str) -> bool:
            return True

        monkeypatch.setattr(targets, "_alive", _alive)
        assert await targets.resolve() == "TARGET"

    async def test_resolve_drops_dead_binding(self, monkeypatch):
        targets.bind("tab-1", "GONE")

        async def _dead(tid: str) -> bool:
            return False

        async def _url_of_tab(tab_id: str) -> str:
            return ""

        async def _webviews() -> list[dict[str, Any]]:
            return [{"id": "OTHER", "url": "https://b.com/"}]

        monkeypatch.setattr(targets, "_alive", _dead)
        monkeypatch.setattr(targets, "_url_of_tab", _url_of_tab)
        monkeypatch.setattr(cdp, "list_webviews", _webviews)

        with pytest.raises(targets.TargetResolutionError) as exc:
            await targets.resolve("tab-1")
        # 绑定失效后要把「现在开着哪些」摊开，让模型自己重选。
        assert "https://b.com/" in str(exc.value)

    async def test_resolve_reports_duplicate_urls_instead_of_guessing(self, monkeypatch):
        async def _dead(tid: str) -> bool:
            return False

        async def _url_of_tab(tab_id: str) -> str:
            return "https://dup.com/"

        async def _webviews() -> list[dict[str, Any]]:
            return [
                {"id": "A", "url": "https://dup.com/"},
                {"id": "B", "url": "https://dup.com/"},
            ]

        monkeypatch.setattr(targets, "_alive", _dead)
        monkeypatch.setattr(targets, "_url_of_tab", _url_of_tab)
        monkeypatch.setattr(cdp, "list_webviews", _webviews)

        with pytest.raises(targets.TargetResolutionError) as exc:
            await targets.resolve("tab-2")
        assert "2 tabs" in str(exc.value)


class TestUrlNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("example.com", "https://example.com"),
            ("  https://a.com  ", "https://a.com"),
            ("ms-msdt:/id", ""),          # 自定义协议必须被挡住
            ("file:///etc/passwd", ""),
            ("javascript:alert(1)", ""),
        ],
    )
    def test_normalize(self, raw, expected):
        assert _normalize_url(raw) == expected


class TestWebviewFilter:
    async def test_only_webviews_are_returned(self, monkeypatch):
        """主窗口自己是 type=page —— 碰它就等于给了模型控制整个应用的能力。"""

        async def _targets() -> list[dict[str, Any]]:
            return [
                {"id": "1", "type": "page", "url": "http://127.0.0.1:5173/"},
                {"id": "2", "type": "webview", "url": "https://a.com/"},
            ]

        monkeypatch.setattr(cdp, "list_targets", _targets)
        result = await cdp.list_webviews()
        assert [item["id"] for item in result] == ["2"]


class TestPinIsNotRepeatedPerAction:
    """回归：``_act`` 以前每次动作前都重钉标签。

    ``tab <id>`` 会清空 snapshot 建立的 ref 缓存，所以每次都重钉等于让
    ``fill``/``click`` **恒定失败**（真机实测：snapshot 拿到 ``e25``，插一次
    ``tab``，紧接着的 ``fill e25`` 必报 ``Unknown ref: e25``）。这里钉住
    「同一个标签只钉一次」。
    """

    def test_second_action_on_same_target_does_not_repin(self, monkeypatch):
        calls = _script_cli(monkeypatch, [_ok("- textbox [ref=e25]"), _ok("✓ Done")])

        asyncio.run(_act("T1", ["snapshot", "-i"]))
        asyncio.run(_act("T1", ["fill", "e25", "你好"]))

        assert calls == [
            ["tab", "T1"],          # 第一次：换标签，要钉
            ["snapshot", "-i"],
            ["fill", "e25", "你好"],  # 第二次：同一个标签，**不能**再钉
        ]

    def test_switching_target_repins(self, monkeypatch):
        calls = _script_cli(monkeypatch, [_ok(), _ok(), _ok()])

        asyncio.run(_act("T1", ["snapshot", "-i"]))
        asyncio.run(_act("T2", ["snapshot", "-i"]))
        asyncio.run(_act("T1", ["snapshot", "-i"]))

        assert calls == [
            ["tab", "T1"],
            ["snapshot", "-i"],
            ["tab", "T2"],
            ["snapshot", "-i"],
            ["tab", "T1"],
            ["snapshot", "-i"],
        ]

    def test_failure_makes_the_next_call_repin(self, monkeypatch):
        """daemon 空闲自杀或被 ``_kill_tree`` 杀掉后绑定就没了，下一轮要补钉。"""
        calls = _script_cli(
            monkeypatch,
            [_failed("✗ Unknown ref: e25"), _ok("- textbox [ref=e7]")],
        )

        with pytest.raises(cli.AgentBrowserError):
            asyncio.run(_act("T1", ["fill", "e25", "你好"]))
        asyncio.run(_act("T1", ["snapshot", "-i"]))

        assert calls == [
            ["tab", "T1"],
            ["fill", "e25", "你好"],
            ["tab", "T1"],          # 失败后丢了 pin，这轮补回来
            ["snapshot", "-i"],
        ]


class TestActionFailureIsNotSuccess:
    """回归：``_act`` 以前只取 ``stdout.strip()``，把失败当成了成功。

    CLI 失败时 stdout 是空的、诊断写在 stderr、退出码非零。以前这一条被丢掉，
    ``browser_type`` 于是回一句「已把内容填进 e25」而实际什么都没发生 ——
    模型被这条假回执带着反复重试，最后只能绕道 URL。
    """

    def test_nonzero_exit_raises(self, monkeypatch):
        _script_cli(monkeypatch, [_failed("✗ Unknown ref: e25")])

        with pytest.raises(cli.AgentBrowserError) as excinfo:
            asyncio.run(_act("T1", ["fill", "e25", "你好"]))

        # 诊断必须原样透出去 —— 模型只有靠它才知道下一步该干什么。
        assert "Unknown ref: e25" in str(excinfo.value)

    def test_stderr_with_zero_exit_also_raises(self, monkeypatch):
        """保守判定：退出码 0 但 stderr 有内容，也当失败。"""
        _script_cli(monkeypatch, [cli.AgentBrowserResult("", "✗ boom", 0)])

        with pytest.raises(cli.AgentBrowserError):
            asyncio.run(_act("T1", ["fill", "e25", "你好"]))

    def test_unknown_ref_gets_a_recovery_hint(self, monkeypatch):
        _script_cli(monkeypatch, [_failed("✗ Unknown ref: e25")])

        with pytest.raises(cli.AgentBrowserError) as excinfo:
            asyncio.run(_act("T1", ["fill", "e25", "你好"]))

        message = str(excinfo.value)
        assert "browser_snapshot" in message   # 告诉模型怎么补救
        assert "✗" not in message              # CLI 的失败符号对模型没意义

    def test_browser_type_surfaces_the_failure(self, monkeypatch):
        """端到端：工具层必须返回 is_error，而不是一句「已把内容填进」。"""
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_failed("✗ Unknown ref: e25")])

        result = asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好"}, None))

        assert result.is_error is True
        assert "Typed the text into" not in result.content
        assert "Unknown ref: e25" in result.content


class TestPinFailureMustNotRunTheAction:
    """回归：钉标签失败时**绝不能**继续执行动作。

    这条曾经是真的 bug，而且后果很显眼：``_act`` 把 ``tab`` 的返回值丢了，不管
    成没成都记下 target 继续走。钉失败（target 已经不在了）时动作就落到 CLI
    **当前默认的那个 target** 上 —— 实测 ``tab list`` 里 ``t1`` 是 NoNo 主界面
    自己，于是 ``browser_navigate reload`` 把整个应用界面重载了一遍，用户看到
    的是主界面闪白。

    「先钉对、再动手」是这个模块的基本纪律。
    """

    def test_failed_pin_raises_before_the_action(self, monkeypatch):
        calls = _script_cli(monkeypatch, [_ok("body")], pin=_failed("✗ Unknown target: T9"))

        with pytest.raises(cli.AgentBrowserError) as excinfo:
            asyncio.run(_act("T9", ["reload"]))

        assert "Could not bind" in str(excinfo.value)
        # 动作本身绝不能发出去。
        assert calls == [["tab", "T9"]]

    def test_failed_pin_does_not_record_the_pin(self, monkeypatch):
        """钉失败还把 target 记进 pin 缓存的话，下一次会跳过重钉、直接裸奔。"""
        _script_cli(monkeypatch, [_ok("body")], pin=_failed("✗ Unknown target: T9"))

        with pytest.raises(cli.AgentBrowserError):
            asyncio.run(_act("T9", ["reload"]))

        assert browser_tools._pinned_target_id == ""

    def test_successful_pin_proceeds(self, monkeypatch):
        """守住另一头：钉成功不能被这次改动挡下。"""
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok("body")])

        asyncio.run(_act("T1", ["read"]))

        assert calls == [["tab", "T1"], ["read"]]


class TestResolveVerifiesTheCachedTarget:
    """``targets.resolve()`` 无参路径以前直接返回缓存，不验活。

    导航一次就可能换 target（关标签、跨站跳转、用户自己关掉页面），拿着一个
    死 target 去操作，正是「动作落到主窗口上」的起点。
    """

    async def test_dead_cached_target_is_not_handed_out(self, monkeypatch):
        async def only_other_targets():
            return [{"id": "T7", "type": "webview", "url": "https://a.com/"}]

        monkeypatch.setattr(cdp, "list_targets", only_other_targets)
        targets.bind("tab-1", "DEAD")

        with pytest.raises(targets.TargetResolutionError):
            await targets.resolve()

    async def test_live_cached_target_is_returned_directly(self, monkeypatch):
        """还活着就该走缓存，不额外去反查 —— 那是常见路径。"""
        targets.bind("tab-1", "T1")

        assert await targets.resolve() == "T1"


class TestActionFailureCarriesCdpDiagnosis:
    """动作失败时要顺手回答「端口到底通不通」。

    CLI 只会说 ``Operation timed out``，那句话分不清「页面慢」和「调试端口
    压根没绑上」。后者会让模型反复重试、反复超时，最后把整个会话耗在这上面。
    """

    def test_timeout_gets_the_port_diagnosis(self, monkeypatch):
        _script_cli(monkeypatch, [_failed("✗ Operation timed out.")])

        async def unreachable():
            return False

        monkeypatch.setattr(cdp, "is_reachable", unreachable)
        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.setenv(cdp.ENV_BINARY, "agent-browser.exe")

        with pytest.raises(cli.AgentBrowserError) as excinfo:
            asyncio.run(_act("T1", ["snapshot", "-i"]))

        message = str(excinfo.value)
        assert "Operation timed out" in message      # 原始诊断不丢
        assert "never bound" in message                  # 再补上真正的原因

    def test_no_diagnosis_when_the_port_is_fine(self, monkeypatch):
        """端口正常时不要硬塞一段诊断，那会把真正的原因淹掉。"""
        _script_cli(monkeypatch, [_failed("✗ Unknown ref: e25")])

        async def reachable():
            return True

        monkeypatch.setattr(cdp, "is_reachable", reachable)
        monkeypatch.setenv(cdp.ENV_ENABLED, "1")
        monkeypatch.setenv(cdp.ENV_BINARY, "agent-browser.exe")

        with pytest.raises(cli.AgentBrowserError) as excinfo:
            asyncio.run(_act("T1", ["fill", "e25", "你好"]))

        assert "never bound" not in str(excinfo.value)


class TestTypeUsesKeyboardPath:
    """回归：``fill <ref>`` 在 Electron webview 里静默无效。

    真机实测（对着本机 baidu webview 和一个纯 input 探针页）：``fill e25 你好``
    退出码 0、stdout「✓ Done」，而 ``get value`` 读回来还是空；换成 CSS 选择器
    ``fill "#kw"`` 就正常。同一批 ref 上 ``click`` 也正常 —— 坏的是「按 ref 定位
    之后再送按键」这一步。所以改成 focus → Control+a → inserttext 的键盘级路径。
    """

    def test_sends_focus_select_all_then_inserttext(self, monkeypatch):
        """顺序不能换：先 focus 才有焦点，Control+a 才能选中旧内容。"""
        targets.bind("tab-1", "T1")
        calls = _script_cli(
            monkeypatch, [_ok(), _ok(), _ok(), _ok("你好")]
        )

        asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好"}, None))

        assert [call for call in calls if call[0] != "tab"] == [
            ["focus", "e25"],
            ["press", "Control+a"],
            ["keyboard", "inserttext", "你好"],
            ["get", "value", "e25"],
        ]

    def test_pins_only_once_across_the_three_steps(self, monkeypatch):
        """中间再插一次 ``tab`` 会清掉焦点，整条路径就废了。"""
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok(), _ok(), _ok(), _ok("你好")])

        asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好"}, None))

        assert sum(1 for call in calls if call[0] == "tab") == 1

    def test_cjk_text_is_passed_through_verbatim(self, monkeypatch):
        """inserttext 是 IME 无关通道，中文不能被转义或截断。"""
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok(), _ok(), _ok(), _ok("你好，世界")])

        asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好，世界"}, None))

        assert ["keyboard", "inserttext", "你好，世界"] in calls

    def test_empty_readback_is_reported_not_silently_ok(self, monkeypatch):
        """三步都退出码 0 但回读为空 = 焦点被页面抢走，必须报错。"""
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_ok(), _ok(), _ok(), _ok("")])

        result = asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好"}, None))

        assert result.is_error is True
        assert "Typed the text into" not in result.content
        assert "空" in result.content or "empty" in result.content.lower()

    def test_failure_on_any_step_is_reported(self, monkeypatch):
        """三步里任何一步失败都必须如实报错，不能只回一句「已把内容填进」。"""
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_ok(), _failed("✗ Unknown ref: e25"), _ok(), _ok()])

        result = asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好"}, None))

        assert result.is_error is True
        assert "Typed the text into" not in result.content
        assert "Unknown ref: e25" in result.content

    def test_success_when_text_landed(self, monkeypatch):
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_ok(), _ok(), _ok(), _ok("你好")])

        result = asyncio.run(BrowserTypeTool().execute({"ref": "e25", "text": "你好"}, None))

        assert result.is_error is not True
        assert "Typed the text into e25" in result.content


class TestActionErrorTaxonomy:
    """``_ACTION_ERRORS`` 必须覆盖临时目录清理炸掉的 ``OSError``。

    ``cli.run_async`` 用 ``TemporaryDirectory`` 收每次调用的 stdout/stderr，而
    daemon 会继承这两个句柄不放 —— 清理时可能撞上
    ``[WinError 32] 另一个程序正在使用此文件``。那是 ``OSError`` 而不是
    ``AgentBrowserError``，不收进 except 就会裸冒到顶层。
    """

    def test_oserror_is_in_the_tuple(self):
        assert OSError in browser_tools._ACTION_ERRORS

    def test_snapshot_tool_reports_oserror_as_tool_error(self, monkeypatch):
        targets.bind("tab-1", "T1")

        async def boom(args, *, timeout_s: float = 30.0):
            raise OSError(32, "另一个程序正在使用此文件")

        monkeypatch.setattr(cli, "run_async", boom)

        result = asyncio.run(
            browser_tools.BrowserSnapshotTool().execute({}, None)
        )

        assert result.is_error is True
        assert "另一个程序正在使用此文件" in result.content


class TestBrowserRead:
    """``browser_read`` 是唯一能拿到正文的工具 —— 拼错参数等于这个工具不存在。"""

    def test_whole_page_uses_read_with_no_url(self, monkeypatch):
        """不带参数就该是裸 ``read``。

        ``read`` 不带 URL 时读的是**当前活动标签页的渲染 DOM**。一旦这里多传一个
        URL，CLI 就会另发 HTTP 请求去抓 —— 那是一条用户看不见、也绕开了侧边栏
        标签生命周期的旁路，整个插件的前提（动作发生在用户眼前）就破了。
        """
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok("正文内容")])

        result = asyncio.run(BrowserReadTool().execute({}, None))

        assert result.is_error is not True
        assert calls[-1] == ["read"]
        assert "正文内容" in result.content

    def test_outline_appends_flag(self, monkeypatch):
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok("# 标题")])

        result = asyncio.run(BrowserReadTool().execute({"outline": True}, None))

        assert result.is_error is not True
        assert calls[-1] == ["read", "--outline"]

    def test_contains_becomes_filter(self, monkeypatch):
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok("价格小节")])

        asyncio.run(BrowserReadTool().execute({"contains": "pricing"}, None))

        assert calls[-1] == ["read", "--filter", "pricing"]

    def test_ref_reads_that_element_only(self, monkeypatch):
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok("段落文本")])

        result = asyncio.run(BrowserReadTool().execute({"ref": "e12"}, None))

        assert result.is_error is not True
        assert calls[-1] == ["get", "text", "@e12"]

    def test_bad_ref_is_rejected_before_any_call(self, monkeypatch):
        calls = _script_cli(monkeypatch, [])

        result = asyncio.run(BrowserReadTool().execute({"ref": "12; rm -rf /"}, None))

        assert result.is_error is True
        assert calls == []

    def test_overlong_contains_is_rejected(self, monkeypatch):
        calls = _script_cli(monkeypatch, [])

        result = asyncio.run(
            BrowserReadTool().execute({"contains": "x" * 5000}, None)
        )

        assert result.is_error is True
        assert calls == []

    def test_empty_page_points_at_scroll(self, monkeypatch):
        """读不到字时要把下一步指出来，否则模型只会反复重试同一个工具。"""
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_ok("   ")])

        result = asyncio.run(BrowserReadTool().execute({}, None))

        assert result.is_error is not True
        assert "browser_scroll" in result.content

    def test_long_page_is_truncated_and_says_so(self, monkeypatch):
        """截断必须自报，否则模型会以为页面就这么多内容。"""
        targets.bind("tab-1", "T1")
        body = "字" * (browser_tools._MAX_READ_CHARS + 500)
        _script_cli(monkeypatch, [_ok(body)])

        result = asyncio.run(BrowserReadTool().execute({}, None))

        assert result.is_error is not True
        assert "Truncated" in result.content
        assert len(result.content) < len(body)

    def test_failure_surfaces_as_tool_error(self, monkeypatch):
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_failed("✗ no active tab")])

        result = asyncio.run(BrowserReadTool().execute({}, None))

        assert result.is_error is True


class TestBrowserScroll:
    def test_defaults_to_down_300(self, monkeypatch):
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok()])

        result = asyncio.run(BrowserScrollTool().execute({}, None))

        assert result.is_error is not True
        assert calls[-1] == ["scroll", "down", "300"]

    def test_explicit_direction_and_amount(self, monkeypatch):
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok()])

        asyncio.run(BrowserScrollTool().execute({"direction": "up", "amount": 200}, None))

        assert calls[-1] == ["scroll", "up", "200"]

    @pytest.mark.parametrize("bad", ["--cdp", "down 9999 --json", "", "sideways"])
    def test_bad_direction_never_reaches_the_argv(self, monkeypatch, bad):
        """方向会直接拼进 argv，白名单挡的就是被当成参数的那类串。"""
        calls = _script_cli(monkeypatch, [])

        result = asyncio.run(BrowserScrollTool().execute({"direction": bad}, None))

        assert result.is_error is True
        assert calls == []

    @pytest.mark.parametrize("bad", [0, -5, 99999, "many", 1.5j])
    def test_bad_amount_is_rejected(self, monkeypatch, bad):
        calls = _script_cli(monkeypatch, [])

        result = asyncio.run(BrowserScrollTool().execute({"amount": bad}, None))

        assert result.is_error is True
        assert calls == []


class TestBrowserNavigate:
    @pytest.mark.parametrize("action", ["back", "forward", "reload"])
    def test_each_action_maps_to_one_cli_command(self, monkeypatch, action):
        targets.bind("tab-1", "T1")
        calls = _script_cli(monkeypatch, [_ok()])

        result = asyncio.run(BrowserNavigateTool().execute({"action": action}, None))

        assert result.is_error is not True
        assert calls[-1] == [action]

    def test_result_warns_that_refs_died(self, monkeypatch):
        """导航作废所有 ref —— 不说这句，模型会拿着旧 ref 去点，会点到别的东西上。"""
        targets.bind("tab-1", "T1")
        _script_cli(monkeypatch, [_ok()])

        result = asyncio.run(BrowserNavigateTool().execute({"action": "back"}, None))

        assert "ref" in result.content
        assert "browser_snapshot" in result.content

    @pytest.mark.parametrize("bad", ["--cdp", "", "back; drop", "up"])
    def test_bad_action_never_reaches_the_argv(self, monkeypatch, bad):
        calls = _script_cli(monkeypatch, [])

        result = asyncio.run(BrowserNavigateTool().execute({"action": bad}, None))

        assert result.is_error is True
        assert calls == []

    def test_missing_action_is_an_error(self, monkeypatch):
        calls = _script_cli(monkeypatch, [])

        result = asyncio.run(BrowserNavigateTool().execute({}, None))

        assert result.is_error is True
        assert calls == []

