"""图片输入门禁 —— 引擎按模型声明的 input_types 决定是否放行附件。"""

import pytest

from nono_agent.core.types import SDKResultMessage
from tests.helpers import create_test_engine


class TestImageInputGate:
    """发图片前必须按模型目录里声明的 input_types 放行。"""

    @staticmethod
    def _image_attachment() -> dict:
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "aGk="},
        }

    @staticmethod
    def _engine(input_types: list[str] | None):
        from nono_agent.core.config import ProviderConfig

        overrides: dict = {}
        if input_types is not None:
            # 不设 adapter —— 测试用 ProviderRegistry 只注册了 mock 实例，
            # 门禁只读 input_types，不需要走协议适配器解析。
            overrides["provider"] = ProviderConfig(
                model_id="claude-test",
                api_key="sk-test",
                input_types=input_types,
            )
        return create_test_engine(
            responses=[{"text": "ok", "tool_calls": []}],
            config_overrides=overrides,
        )

    @pytest.mark.asyncio
    async def test_image_rejected_when_model_is_text_only(self):
        engine = self._engine(["text"])
        async with engine:
            with pytest.raises(ValueError, match="未声明支持图片输入"):
                async for _ in engine.submit_message(
                    "看这张图", attachments=[self._image_attachment()]
                ):
                    pass

    @pytest.mark.asyncio
    async def test_image_allowed_when_model_declares_image(self):
        engine = self._engine(["text", "image"])
        async with engine:
            messages = [
                msg
                async for msg in engine.submit_message(
                    "看这张图", attachments=[self._image_attachment()]
                )
            ]
        assert isinstance(messages[-1], SDKResultMessage)
        assert not messages[-1].is_error

    @pytest.mark.asyncio
    async def test_openai_style_image_url_block_also_gated(self):
        engine = self._engine(["text"])
        async with engine:
            with pytest.raises(ValueError, match="未声明支持图片输入"):
                async for _ in engine.submit_message(
                    "看这张图",
                    attachments=[{"type": "image_url", "image_url": {"url": "https://x/y.png"}}],
                ):
                    pass

    @pytest.mark.asyncio
    async def test_text_only_message_unaffected_by_gate(self):
        engine = self._engine(["text"])
        async with engine:
            messages = [msg async for msg in engine.submit_message("纯文本")]
        assert isinstance(messages[-1], SDKResultMessage)
        assert not messages[-1].is_error

    @pytest.mark.asyncio
    async def test_no_provider_config_still_allows_image(self):
        # 直接构造引擎（没走 YAML 目录）时无从判断能力，保持旧行为放行。
        engine = self._engine(None)
        async with engine:
            messages = [
                msg
                async for msg in engine.submit_message(
                    "看这张图", attachments=[self._image_attachment()]
                )
            ]
        assert isinstance(messages[-1], SDKResultMessage)
        assert not messages[-1].is_error

    @pytest.mark.asyncio
    async def test_gate_fires_before_any_event_is_written(self):
        # 拒绝必须发生在落盘之前：不能留下一个只有 turn/start 的半截会话。
        engine = self._engine(["text"])
        async with engine:
            with pytest.raises(ValueError):
                async for _ in engine.submit_message(
                    "看这张图", attachments=[self._image_attachment()]
                ):
                    pass
        assert engine._turn_count == 0
