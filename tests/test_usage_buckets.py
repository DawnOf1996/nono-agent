"""Tests for cross-provider usage bucket normalization (统一四桶口径)."""

from types import SimpleNamespace

from nono_agent.providers._openai_chat_completions_utils import (
    openai_chat_usage_buckets,
    usage_to_buckets,
)
from nono_agent.providers._openai_responses_utils import responses_usage_buckets


class TestUsageToBuckets:
    def test_no_cache(self):
        assert usage_to_buckets(1000, 200, None) == {
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }

    def test_with_cache_read(self):
        # 总输入 1000，其中 600 命中缓存 → 新鲜输入 400
        assert usage_to_buckets(1000, 200, 600) == {
            "input_tokens": 400,
            "output_tokens": 200,
            "cache_read_input_tokens": 600,
            "cache_creation_input_tokens": 0,
        }

    def test_cached_exceeds_total_clamps_fresh_to_zero(self):
        assert usage_to_buckets(500, 100, 700)["input_tokens"] == 0
        assert usage_to_buckets(500, 100, 700)["cache_read_input_tokens"] == 700

    def test_none_inputs(self):
        assert usage_to_buckets(None, None, None) == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }


class TestOpenAIChatUsageBuckets:
    def test_sdk_object_with_cache(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            prompt_tokens_details=SimpleNamespace(cached_tokens=600),
        )
        assert openai_chat_usage_buckets(usage) == {
            "input_tokens": 400,
            "output_tokens": 200,
            "cache_read_input_tokens": 600,
            "cache_creation_input_tokens": 0,
        }

    def test_missing_details_treats_as_no_cache(self):
        usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=200)
        assert openai_chat_usage_buckets(usage)["input_tokens"] == 1000
        assert openai_chat_usage_buckets(usage)["cache_read_input_tokens"] == 0


class TestResponsesUsageBuckets:
    def test_dict_with_cache(self):
        u = {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 700},
        }
        assert responses_usage_buckets(u) == {
            "input_tokens": 300,
            "output_tokens": 200,
            "cache_read_input_tokens": 700,
            "cache_creation_input_tokens": 0,
        }

    def test_sdk_object_with_cache(self):
        u = SimpleNamespace(
            input_tokens=1000,
            output_tokens=200,
            input_tokens_details=SimpleNamespace(cached_tokens=700),
        )
        assert responses_usage_buckets(u)["input_tokens"] == 300
        assert responses_usage_buckets(u)["cache_read_input_tokens"] == 700

    def test_missing_details_treats_as_no_cache(self):
        assert responses_usage_buckets({"input_tokens": 100, "output_tokens": 5}) == {
            "input_tokens": 100,
            "output_tokens": 5,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        }
