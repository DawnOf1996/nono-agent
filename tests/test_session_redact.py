"""脱敏投影 —— 外发副本不带凭据，磁盘上的事件流保持逐字真实。"""

from nono_agent.session import redact_event, redact_events, redact_text, redact_value
from nono_agent.session.events import (
    EVENT_REQUEST_HEADER,
    EVENT_TOOL_RESULT,
    SessionEvent,
)
from nono_agent.session.redact import REDACTED


class TestRedactText:
    def test_masks_openai_style_key(self):
        out = redact_text("export DEEPSEEK_API_KEY=sk-abcdefghijklmnopqrstuvwxyz012345")
        assert "sk-abcdefghijklmnop" not in out
        assert REDACTED.format(kind="api_key") in out

    def test_masks_github_token(self):
        out = redact_text("remote uses ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        assert "ghp_ABCDEFGH" not in out

    def test_masks_aws_access_key(self):
        assert "AKIAIOSFODNN7EXAMPLE" not in redact_text("key AKIAIOSFODNN7EXAMPLE here")

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


class TestRedactValue:
    def test_sensitive_key_replaces_whole_value(self):
        out = redact_value({"api_key": "anything-at-all", "model": "deepseek-chat"})
        assert out["api_key"] == REDACTED.format(kind="secret")
        assert out["model"] == "deepseek-chat"

    def test_walks_nested_structures(self):
        out = redact_value(
            {"env": [{"name": "TOKEN", "value": "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"}]}
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
            header={"system": "use token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"},
            reason="initial",
        )
        assert "ghp_ABCDEFGH" not in str(redact_events([event])[0].data)
