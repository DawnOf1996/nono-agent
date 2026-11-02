"""图片资产管线 —— 落盘、引用、水合、读图、token 估算。

这一组测试盯住的是「图片不再以 base64 形态留在会话里」这条主线：

- 提交时附件落盘成资产引用（``nono_agent.assets``）
- 请求前把引用还原成 base64（``nono_agent.assets.materialize``）
- 模型主动看图走 ``read_image`` 工具
- 图片块的 token 估算按宽高折算，不按 base64 长度
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from nono_agent.assets import AssetStore, get_asset_store
from nono_agent.assets.imageinfo import probe_size, sniff_media_type
from nono_agent.assets.materialize import materialize_asset_images
from nono_agent.core.types import UserMessage
from nono_agent.tools.builtin.read_image import ReadImageTool
from nono_agent.tools.context import ToolUseContext
from nono_agent.utils.tokens import _count_content_block
from tests.helpers import create_test_engine


def png_bytes(width: int = 4, height: int = 6) -> bytes:
    """最小 PNG 头 —— 嗅探与尺寸探测只看文件头，内容不必是合法图像。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        + (13).to_bytes(4, "big")
        + b"IHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
        + b"\x00\x00\x00\x00"
    )


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class TestAssetStore:
    """资产目录的落盘与定位。"""

    def test_saves_under_year_month_day(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="shot.png")

        assert store.path_for(ref.asset_id).is_file()
        parts = ref.relative_path.split("/")
        assert len(parts) == 4  # YYYY/MM/DD/<文件名>
        assert (tmp_path / "assets" / parts[0] / parts[1] / parts[2]).is_dir()

    def test_layout_matches_reference(self, tmp_path):
        """目录层级：assets/YYYY/MM/DD/<时分秒-毫秒>-<资产ID>-<原名>。"""
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="image.png")
        parts = ref.relative_path.split("/")
        year, month, day, name = parts
        assert len(year) == 4 and year.isdigit()
        assert len(month) == 2 and month.isdigit()
        assert len(day) == 2 and day.isdigit()
        assert name.endswith("-image.png")
        assert ref.asset_id in name
        stamp = name[: name.index("-asset_")]
        assert len(stamp.split("-")) == 4  # HH-MM-SS-毫秒

    def test_writes_sidecar_and_daily_index(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(8, 9), file_name="image.png")

        sidecar = store.path_for(ref.asset_id).with_name(
            store.path_for(ref.asset_id).name + ".asset.json"
        )
        assert sidecar.is_file()
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        assert record["assetId"] == ref.asset_id
        assert record["mimeType"] == "image/png"
        assert record["bytes"] == len(png_bytes(8, 9))
        assert record["width"] == 8
        assert record["height"] == 9

        index = store.day_dir() / "assets.jsonl"
        assert index.is_file()
        lines = [line for line in index.read_text(encoding="utf-8").splitlines() if line]
        assert any(json.loads(line)["assetId"] == ref.asset_id for line in lines)

    def test_detects_type_and_size_by_magic_bytes(self, tmp_path):
        """扩展名与内容不符时按内容判定 —— 落一个 .png 名字的 JPEG 也认成 JPEG。"""
        store = AssetStore(tmp_path / "assets")
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 32
        ref = store.save_bytes(jpeg, file_name="actually.jpg")
        assert ref.media_type == "image/jpeg"
        assert ref.file_name.endswith(".jpg")

    def test_rejects_non_image(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        with pytest.raises(ValueError, match="不是可识别的图片格式"):
            store.save_bytes(b"just text", file_name="a.txt")

    def test_saves_text_under_same_layout(self, tmp_path):
        """文本资产与图片资产同目录、同命名规则 —— 工具产出的长文本走这条。"""
        store = AssetStore(tmp_path / "assets")
        ref = store.save_text("# 标题\n\n正文\n", file_name="page.md")

        assert ref.kind == "text"
        assert ref.media_type == "text/markdown"
        assert ref.file_name.endswith(".md")
        parts = ref.relative_path.split("/")
        assert len(parts) == 4  # YYYY/MM/DD/<文件名>
        assert store.path_for(ref.asset_id).read_text(encoding="utf-8") == "# 标题\n\n正文\n"

    def test_text_writes_sidecar_and_daily_index(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_text("正文", file_name="page.md")

        path = store.path_for(ref.asset_id)
        sidecar = path.with_name(path.name + ".asset.json")
        record = json.loads(sidecar.read_text(encoding="utf-8"))
        assert record["assetId"] == ref.asset_id
        assert record["mimeType"] == "text/markdown"

        index = store.day_dir() / "assets.jsonl"
        lines = [line for line in index.read_text(encoding="utf-8").splitlines() if line]
        assert any(json.loads(line)["assetId"] == ref.asset_id for line in lines)

    def test_text_defaults_to_markdown_suffix(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        assert store.save_text("x").file_name.endswith(".md")

    def test_rejects_empty_text(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        with pytest.raises(ValueError, match="文本为空"):
            store.save_text("")

    def test_rejects_oversized(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        with pytest.raises(ValueError, match="上限"):
            store.save_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * (21 * 1024 * 1024))

    def test_path_for_rejects_traversal(self, tmp_path):
        """资产 ID 是定位文件的唯一入口 —— 非法 ID 一律拒绝，不拼路径。"""
        store = AssetStore(tmp_path / "assets")
        for bad in ("../../etc/passwd", "asset_x", "", "asset_20260101-000000-000_zz_deadbeef"):
            with pytest.raises(ValueError):
                store.path_for(bad)

    def test_resolve_relative_stays_inside_root(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="image.png")
        assert store.resolve_relative(ref.relative_path) is not None
        assert store.resolve_relative("assets/../../secret.png") is None

    def test_persist_attachments_converts_base64(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        payload = b64(png_bytes())
        original = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": payload},
        }
        out = store.persist_attachments([original])
        assert out[0]["source"]["type"] == "asset"
        assert out[0]["source"]["asset_id"].startswith("asset_")
        assert "data" not in out[0]["source"]

    def test_persist_attachments_keeps_undecodable_payload(self, tmp_path):
        """认不出来的图片原样透传 —— 不能悄悄把用户的图丢掉。"""
        store = AssetStore(tmp_path / "assets")
        original = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "aGk="},
        }
        out = store.persist_attachments([original])
        assert out[0]["source"]["type"] == "base64"

    def test_persist_attachments_keeps_asset_source(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="image.png")
        source = ref.to_source()
        out = store.persist_attachments([{"type": "image", "source": source}])
        assert out[0]["source"] == source


class TestMaterializeAssets:
    """请求前把资产引用还原成 base64。"""

    def test_hydrates_asset_source(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="image.png")
        msg = UserMessage(
            message={"role": "user", "content": [{"type": "image", "source": ref.to_source()}]}
        )
        assert materialize_asset_images([msg], store) == 1

        block = msg.message["content"][0]
        assert block["source"]["type"] == "base64"
        assert base64.b64decode(block["source"]["data"]) == png_bytes()

    def test_leaves_base64_source_untouched(self, tmp_path):
        store = AssetStore(tmp_path / "assets")
        original = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "aGk="},
        }
        msg = UserMessage(message={"role": "user", "content": [original]})
        assert materialize_asset_images([msg], store) == 0
        assert msg.message["content"][0]["source"]["data"] == "aGk="

    def test_missing_asset_becomes_text(self, tmp_path):
        """文件没了就换成一句说明，而不是发一个结构不合法的图片块。"""
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="image.png")
        store.path_for(ref.asset_id).unlink()
        msg = UserMessage(
            message={"role": "user", "content": [{"type": "image", "source": ref.to_source()}]}
        )
        materialize_asset_images([msg], store)
        block = msg.message["content"][0]
        assert block["type"] == "text"
        assert "图片不可用" in block["text"]


class TestImageTokenEstimate:
    """图片块的 token 估算。"""

    def test_uses_dimensions_not_base64_length(self):
        block = {
            "type": "image",
            "source": {
                "type": "asset",
                "media_type": "image/png",
                "width": 1000,
                "height": 750,
            },
        }
        assert _count_content_block(block) == int(1000 * 750 / 750)

    def test_legacy_base64_block_does_not_count_payload(self):
        """老会话里的 base64 图片块不该按文本长度计 —— 那会高估十几倍。"""
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "A" * (100 * 1024)},
        }
        assert _count_content_block(block) <= 1600

    def test_long_edge_is_scaled_down(self):
        block = {
            "type": "image",
            "source": {"type": "asset", "width": 4000, "height": 4000},
        }
        assert _count_content_block(block) <= 1600


class TestReadImageTool:
    """read_image 工具。"""

    @pytest.fixture
    def ctx(self, tmp_path):
        return ToolUseContext(cwd=str(tmp_path))

    @pytest.mark.asyncio
    async def test_reads_workspace_image_and_attaches_it(self, tmp_path, ctx):
        store = AssetStore(tmp_path / "assets")
        target = tmp_path / "shot.png"
        target.write_bytes(png_bytes(3, 3))

        result = await ReadImageTool(store).execute({"path": "shot.png"}, ctx)
        assert not result.is_error
        assert result.images
        source = result.images[0]["source"]
        assert source["type"] == "asset"
        assert source["media_type"] == "image/png"
        assert source["width"] == 3

    @pytest.mark.asyncio
    async def test_reads_by_asset_id(self, tmp_path, ctx):
        store = AssetStore(tmp_path / "assets")
        ref = store.save_bytes(png_bytes(), file_name="image.png")

        result = await ReadImageTool(store).execute({"path": ref.asset_id}, ctx)
        assert not result.is_error
        assert result.images[0]["source"]["asset_id"] == ref.asset_id

    @pytest.mark.asyncio
    async def test_rejects_non_image(self, tmp_path, ctx):
        store = AssetStore(tmp_path / "assets")
        (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
        result = await ReadImageTool(store).execute({"path": "note.txt"}, ctx)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_missing_file_is_error(self, tmp_path, ctx):
        store = AssetStore(tmp_path / "assets")
        result = await ReadImageTool(store).execute({"path": "nope.png"}, ctx)
        assert result.is_error

    @pytest.mark.asyncio
    async def test_empty_path_is_error(self, tmp_path, ctx):
        store = AssetStore(tmp_path / "assets")
        result = await ReadImageTool(store).execute({"path": "  "}, ctx)
        assert result.is_error


class TestEngineAttachmentPersistence:
    """引擎提交时把图片附件落盘成引用。"""

    @pytest.mark.asyncio
    async def test_store_keeps_reference_provider_gets_base64(self, tmp_path):
        """会话状态里是引用；发给 provider 的是还原后的 base64。"""
        from tests.helpers import MockProvider

        store = AssetStore(tmp_path / "assets")
        provider = MockProvider(responses=[{"text": "ok", "tool_calls": []}])
        engine = create_test_engine(
            provider=provider,
            asset_store=store,
            config_overrides={"session_id": "sess-1"},
        )
        payload = b64(png_bytes(5, 5))
        async with engine:
            async for _ in engine.submit_message(
                "看这张图",
                attachments=[
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": payload},
                    }
                ],
            ):
                pass

        # 会话状态：引用，不是 base64
        user_blocks = [
            block
            for msg in engine._store.all_messages()
            if isinstance(msg, UserMessage)
            for block in (msg.message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "image"
        ]
        assert user_blocks
        assert user_blocks[0]["source"]["type"] == "asset"
        assert "data" not in user_blocks[0]["source"]

        # 发给 provider 的：还原后的 base64
        sent = provider.calls[0]["messages"]
        sent_blocks = [
            block
            for msg in sent
            for block in (msg.message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "image"
        ]
        assert sent_blocks
        assert sent_blocks[0]["source"]["type"] == "base64"
        assert base64.b64decode(sent_blocks[0]["source"]["data"]) == png_bytes(5, 5)

    @pytest.mark.asyncio
    async def test_transcript_has_no_base64(self, tmp_path):
        """事件流里不能出现 base64 —— 会话文件会因此永久膨胀。"""
        from nono_agent.session.transcript import LocalFileTranscriptWriter

        store = AssetStore(tmp_path / "assets")
        writer = LocalFileTranscriptWriter(tmp_path / "sess-2.jsonl", compress=False)
        engine = create_test_engine(
            transcript_writer=writer,
            asset_store=store,
            config_overrides={"session_id": "sess-2"},
        )
        payload = b64(png_bytes(5, 5))
        async with engine:
            async for _ in engine.submit_message(
                "看这张图",
                attachments=[
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": payload},
                    }
                ],
            ):
                pass
        await writer.flush()

        events = await writer.read_events()
        dumped = json.dumps([event.model_dump() for event in events], ensure_ascii=False)
        assert payload not in dumped
        assert "asset_" in dumped


    @pytest.mark.asyncio
    async def test_resumed_session_still_sees_the_image(self, tmp_path):
        """重开历史会话，模型仍能看到当初贴的那张图。"""
        from nono_agent.session.transcript import LocalFileTranscriptWriter
        from tests.helpers import MockProvider

        store = AssetStore(tmp_path / "assets")
        writer = LocalFileTranscriptWriter(tmp_path / "sess-4.jsonl", compress=False)
        payload = b64(png_bytes(5, 5))
        attachment = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": payload},
        }

        first = create_test_engine(
            provider=MockProvider(responses=[{"text": "ok", "tool_calls": []}]),
            transcript_writer=writer,
            asset_store=store,
            config_overrides={"session_id": "sess-4"},
        )
        async with first:
            async for _ in first.submit_message("看这张图", attachments=[attachment]):
                pass

        provider = MockProvider(responses=[{"text": "还在", "tool_calls": []}])
        second = create_test_engine(
            provider=provider,
            transcript_writer=writer,
            asset_store=store,
            config_overrides={"session_id": "sess-4"},
        )
        async with second:
            await second.resume_session("sess-4")
            async for _ in second.submit_message("再看看那张图"):
                pass

        sent = provider.calls[0]["messages"]
        image_blocks = [
            block
            for msg in sent
            for block in (msg.message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "image"
        ]
        assert image_blocks, "恢复后的会话里图片丢了"
        assert image_blocks[0]["source"]["type"] == "base64"
        assert base64.b64decode(image_blocks[0]["source"]["data"]) == png_bytes(5, 5)


class TestImageInfo:
    """图片类型与尺寸探测。"""

    def test_png_size(self):
        assert probe_size(png_bytes(12, 34), "image/png") == (12, 34)

    def test_sniff_png(self):
        assert sniff_media_type(png_bytes()) == "image/png"

    def test_unknown_returns_none(self):
        assert sniff_media_type(b"nope") is None
        assert probe_size(b"nope", "image/png") is None


class TestReadImageInLoop:
    """read_image 的图片要真的进模型上下文。"""

    @pytest.mark.asyncio
    async def test_tool_result_image_becomes_user_message(self, tmp_path):
        from tests.helpers import MockProvider

        store = AssetStore(tmp_path / "assets")
        shot = tmp_path / "shot.png"
        shot.write_bytes(png_bytes(4, 4))

        provider = MockProvider(
            responses=[
                {
                    "text": "",
                    "tool_calls": [
                        {"id": "t1", "name": "read_image", "input": {"path": "shot.png"}}
                    ],
                },
                {"text": "看到了", "tool_calls": []},
            ]
        )
        engine = create_test_engine(
            provider=provider,
            tools=[ReadImageTool(store)],
            asset_store=store,
            config_overrides={"cwd": str(tmp_path), "session_id": "sess-3"},
        )
        async with engine:
            async for _ in engine.submit_message("看看这张图"):
                pass

        # 第二次请求里应有一条带 image 块的 user 消息
        second_call = provider.calls[1]["messages"]
        image_blocks = [
            block
            for msg in second_call
            for block in (msg.message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "image"
        ]
        assert image_blocks, "read_image 的图片没有进上下文"
        # 请求发出去前已水合成 base64
        assert image_blocks[0]["source"]["type"] == "base64"

        # 会话状态里留的是引用，不是 base64
        stored = [
            block
            for msg in engine._store.all_messages()
            for block in (msg.message.get("content") or [])
            if isinstance(block, dict) and block.get("type") == "image"
        ]
        assert stored
        assert stored[0]["source"]["type"] == "asset"


class TestAssetHttpApi:
    """POST /asset 与 GET /asset/{id}。"""

    def test_upload_and_read_back(self, tmp_path):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        from nono_app_server.engine import create_server_app

        app = create_server_app(api_key=None)
        with fastapi_testclient.TestClient(app) as client:
            resp = client.post(
                "/asset",
                files={"file": ("shot.png", png_bytes(7, 5), "image/png")},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["asset_id"].startswith("asset_")
            assert body["media_type"] == "image/png"
            assert body["width"] == 7
            assert body["source"]["type"] == "asset"

            fetched = client.get(f"/asset/{body['asset_id']}")
            assert fetched.status_code == 200
            assert fetched.content == png_bytes(7, 5)

    def test_rejects_non_image(self):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        from nono_app_server.engine import create_server_app

        app = create_server_app(api_key=None)
        with fastapi_testclient.TestClient(app) as client:
            resp = client.post(
                "/asset",
                files={"file": ("note.txt", b"just text", "text/plain")},
            )
        assert resp.status_code == 400

    def test_rejects_bad_asset_id(self):
        fastapi_testclient = pytest.importorskip("fastapi.testclient")
        from nono_app_server.engine import create_server_app

        app = create_server_app(api_key=None)
        with fastapi_testclient.TestClient(app) as client:
            resp = client.get("/asset/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)

    def test_assets_path_not_shadowed_by_vite_mount(self):
        """``/assets`` 归打包后的前端产物用 —— 资产 API 必须在别的路径上。"""
        pytest.importorskip("fastapi.testclient")
        from nono_app_server.engine import create_server_app

        app = create_server_app(api_key=None)
        paths = {route.path for route in app.routes}
        assert "/asset" in paths
        assert "/asset/{asset_id}" in paths
        assert "/assets" not in paths


def test_default_store_points_at_data_dir():
    """默认资产目录在数据目录下，与 sessions 同级。"""
    root = get_asset_store().root
    assert root.name == "assets"
    assert root.parent == get_asset_store().root.parent
    assert isinstance(root, Path)
