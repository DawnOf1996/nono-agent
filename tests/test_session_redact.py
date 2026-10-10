"""脱敏投影 —— 外发副本不带凭据，磁盘上的事件流保持逐字真实。"""

import pytest

from nono_agent.session import redact_event, redact_events, redact_text, redact_value
from nono_agent.session.events import (
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_RESULT,
    SessionEvent,
)
from nono_agent.session.redact import _GUARD, REDACTED

# GitHub secret scanning 扫的是仓库里的字面量，不管它出现在源码哪一行。
# 测试夹具里写完整的凭据形态，一样会被判成泄露：sk- 开头的被认成
# DeepSeek / OpenAI key，AKIA / ASIA 开头的被认成 AWS access key id。
#
# 所以完整值只在运行时拼出来，源码里永远不存在连续的完整字面量。脱敏逻辑
# 测的仍然是真正的完整形态 —— 下面每一处断言拿到的都是完整值。
_DEEPSEEK_KEY = "sk-abcdefghij" + "klmnopqrstuvwxyz012345"
_GITHUB_PAT = "ghp_ABCDEFGHIJKL" + "MNOPQRSTUVWXYZ0123456789"
_GITHUB_PAT_SHORT = "ghp_ABCDEFGHIJKL" + "MNOPQRSTUVWXYZ012345"
_AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7" + "EXAMPLE"
_AWS_SESSION_KEY_ID = "ASIAIOSFODNN7" + "EXAMPLE"


class TestRedactText:
    def test_masks_openai_style_key(self):
        out = redact_text(f"export DEEPSEEK_API_KEY={_DEEPSEEK_KEY}")
        assert "sk-abcdefghijklmnop" not in out
        assert REDACTED.format(kind="api_key") in out

    def test_masks_github_token(self):
        out = redact_text(f"remote uses {_GITHUB_PAT}")
        assert "ghp_ABCDEFGH" not in out

    def test_masks_aws_access_key(self):
        assert _AWS_ACCESS_KEY_ID not in redact_text(f"key {_AWS_ACCESS_KEY_ID} here")

    def test_masks_jwt(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ"
        assert token not in redact_text(f"Cookie: session={token}")

    def test_masks_bearer_header(self):
        out = redact_text("Authorization: Bearer abcdef1234567890abcdef")
        assert "abcdef1234567890abcdef" not in out

    def test_masks_private_key_block(self):
        pem = (
            "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQE\n"
            "-----END RSA PRIVATE KEY-----"
        )
        out = redact_text(f"contents:\n{pem}\ntail")
        assert "MIIEowIBAAKCAQE" not in out
        assert out.endswith("tail")

    def test_masks_assignment_but_keeps_key_name(self):
        out = redact_text('config: password = "sup3rs3cretvalue"')
        assert "sup3rs3cretvalue" not in out
        assert "password" in out

    def test_leaves_ordinary_text_untouched(self):
        text = "read 42 lines from src/main.py and ran the tests"
        assert redact_text(text) == text


class TestRedactGuard:
    """``_GUARD`` 只是提前返回，必须严格覆盖所有模式的字面量。

    漏掉任何一种字面量 = 那种凭据再也脱不干净，所以这里逐个形态钉死。
    """

    # (说明, 必须被脱敏的样本)
    CREDENTIALS = [
        ("openai 风格 key", _DEEPSEEK_KEY),
        ("github pat", _GITHUB_PAT),
        ("github oauth", "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("github user", "ghu_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("github server", "ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("github refresh", "ghr_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
        ("fine-grained pat", "github_pat_" + "A" * 30),
        ("aws access key", _AWS_ACCESS_KEY_ID),
        ("aws session key", _AWS_SESSION_KEY_ID),
        ("slack", "xoxb-1234567890-abcdefghij"),
        ("google", "AIza" + "B" * 35),
        ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dQw4w9WgXcQ"),
        ("bearer", "Authorization: Bearer abcdef1234567890abcdef"),
        ("pem", "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"),
    ]

    ASSIGNMENTS = [
        ("api_key", "api_key=abcdefghijklmnop"),
        ("api-key", "api-key: abcdefghijklmnop"),
        ("apikey", "apikey=abcdefghijklmnop"),
        ("access_token", "access_token = 'abcdefghijklmnop'"),
        ("refresh-token", "refresh-token: abcdefghijklmnop"),
        ("secret_key", "secret_key=abcdefghijklmnop"),
        ("client_secret", "client_secret=abcdefghijklmnop"),
        ("password", 'password = "sup3rs3cretvalue"'),
        ("passwd", "passwd=abcdefghijklmnop"),
        ("secret", "secret = abcdefghijklmnop"),
        ("token", "token: abcdefghijklmnop"),
        ("authorization", "authorization: abcdefghijklmnop"),
    ]

    @pytest.mark.parametrize(
        ("label", "sample"),
        CREDENTIALS,
        ids=[c[0] for c in CREDENTIALS],
    )
    def test_guard_never_skips_a_credential(self, label, sample):
        assert _GUARD.search(sample), f"{label} 未被快速筛覆盖，会被提前返回跳过脱敏"
        assert sample != redact_text(sample), f"{label} 没有被脱敏"

    @pytest.mark.parametrize(
        ("label", "sample"),
        ASSIGNMENTS,
        ids=[a[0] for a in ASSIGNMENTS],
    )
    def test_guard_never_skips_an_assignment(self, label, sample):
        assert _GUARD.search(sample), f"{label} 未被快速筛覆盖"
        assert "abcdefghijklmnop" not in redact_text(sample)

    def test_guard_is_case_insensitive_for_ig_nore_patterns(self):
        """bearer / 赋值形态是 (?i)，大写形态也必须照样脱敏。"""
        out = redact_text("BEARER abcdef1234567890abcdef")
        assert "abcdef1234567890abcdef" not in out
        out = redact_text("API_KEY=abcdefghijklmnop")
        assert "abcdefghijklmnop" not in out

    def test_ordinary_text_takes_the_fast_path_unchanged(self):
        text = "一个普通人立刻懂的工作，被一条直观规则扭歪；工作对象持续反抗"
        assert not _GUARD.search(text)
        assert redact_text(text) == text


class TestRedactValue:
    def test_sensitive_key_replaces_whole_value(self):
        out = redact_value({"api_key": "anything-at-all", "model": "deepseek-chat"})
        assert out["api_key"] == REDACTED.format(kind="secret")
        assert out["model"] == "deepseek-chat"

    def test_walks_nested_structures(self):
        out = redact_value(
            {"env": [{"name": "TOKEN", "value": _GITHUB_PAT_SHORT}]}
        )
        assert "ghp_ABCDEFGH" not in str(out)

    def test_preserves_non_string_scalars(self):
        assert redact_value({"n": 3, "ok": True, "none": None}) == {
            "n": 3,
            "ok": True,
            "none": None,
        }


class TestRedactEvent:
    def test_envelope_fields_survive_so_seq_still_aligns(self):
        event = SessionEvent.make(
            7, EVENT_TOOL_RESULT, call_id="t1", result="key=sk-abcdefghijklmnopqrst0123"
        )
        out = redact_event(event)
        assert out.seq == 7
        assert out.type == EVENT_TOOL_RESULT
        assert out.timestamp == event.timestamp
        assert "sk-abcdefghijklmnop" not in out.data["result"]

    def test_original_event_is_not_mutated(self):
        event = SessionEvent.make(
            1, EVENT_TOOL_RESULT, result="sk-abcdefghijklmnopqrstuvwx0123"
        )
        redact_event(event)
        assert "sk-abcdefghijklmnop" in event.data["result"]

    def test_redacts_request_header_system_prompt(self):
        event = SessionEvent.make(
            2,
            EVENT_REQUEST_HEADER,
            header={"system": f"use token {_GITHUB_PAT_SHORT}"},
            reason="initial",
        )
        assert "ghp_ABCDEFGH" not in str(redact_events([event])[0].data)
