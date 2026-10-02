"""tiktoken 编码器加载 —— 重点是「拿不到编码器时不能炸」。

tiktoken 是声明的硬依赖，但它的 BPE 词表不在 wheel 里，首次
``get_encoding()`` 要联网下载。桌面端离线/走代理时这步会抛 requests 异常，
而 ``count_tokens`` 每条 assistant 消息都会被调用 —— 冒上去就是整轮对话崩掉。
"""

from __future__ import annotations

import pytest

from nono_agent.utils import tokens as tokens_mod
from nono_agent.utils.tokens import analyze_context_usage, count_tokens


@pytest.fixture(autouse=True)
def _reset_encoder_state():
    """每个用例前后都把模块级缓存复位，避免互相污染。"""
    saved = (tokens_mod._tiktoken_encoder, tokens_mod._tiktoken_unavailable)
    tokens_mod._tiktoken_encoder = None
    tokens_mod._tiktoken_unavailable = False
    yield
    tokens_mod._tiktoken_encoder, tokens_mod._tiktoken_unavailable = saved


class TestEncoderLoading:
    def test_encoder_is_available_by_default(self):
        # 硬依赖：默认环境必须能拿到编码器。
        assert tokens_mod._get_encoder() is not None

    def test_counts_real_tokens(self):
        assert count_tokens("hello world") == 2

    def test_json_is_counted_above_the_crude_fallback(self):
        # 工具 schema 那种标点密集的 JSON，len//4 明显低估 —— 这正是引入
        # tiktoken 的原因。构造一段密度高的 JSON 验证精确计数更高。
        blob = '{"a":1,"b":[1,2,3],"c":"x/y","d":{"e":null,"f":true}}' * 20
        assert count_tokens(blob) > len(blob) // 4


class TestEncoderDegradation:
    def test_import_error_falls_back(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "tiktoken":
                raise ImportError("no tiktoken")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert tokens_mod._get_encoder() is None
        # 退回了启发式，但没抛
        assert count_tokens("hello world") >= 1

    def test_download_error_falls_back(self, monkeypatch):
        # 这是真实故障模式：词表下载失败抛的是 requests 异常，不是 ImportError。
        import tiktoken

        def boom(name):
            raise RuntimeError("HTTPSConnectionPool: Max retries exceeded")

        monkeypatch.setattr(tiktoken, "get_encoding", boom)
        assert tokens_mod._get_encoder() is None
        assert count_tokens("hello world") >= 1

    def test_failure_is_remembered_and_not_retried(self, monkeypatch):
        # count_tokens 每轮要被调几百次；不记住失败就会每次都重发一次 HTTP。
        import tiktoken

        calls = []

        def boom(name):
            calls.append(name)
            raise RuntimeError("offline")

        monkeypatch.setattr(tiktoken, "get_encoding", boom)
        for _ in range(50):
            count_tokens("x" * 100)
        assert len(calls) == 1, f"get_encoding 被重试了 {len(calls)} 次"

    def test_recovery_after_failure_is_impossible_by_design(self, monkeypatch):
        # 标记置位后就不再尝试：宁可一直用估算，也不要在对话中途反复联网。
        tokens_mod._tiktoken_unavailable = True
        assert tokens_mod._get_encoder() is None

    def test_analyze_context_usage_survives_broken_encoder(self, monkeypatch):
        # 最要紧的一条：引擎每条 assistant 消息都会调它，不能让它抛。
        import tiktoken

        def boom(name):
            raise RuntimeError("offline")

        monkeypatch.setattr(tiktoken, "get_encoding", boom)
        usage = analyze_context_usage(
            [{"role": "user", "content": "hi"}],
            system_prompt="sys",
            tools=[{"name": "bash"}],
        )
        assert usage["total_tokens"] > 0
        assert usage["total_tokens"] == (
            usage["system_prompt_tokens"]
            + usage["tools_tokens"]
            + usage["messages_tokens"]
        )
