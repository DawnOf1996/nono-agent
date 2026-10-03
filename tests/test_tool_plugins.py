"""Tests for the toolplugins package (declarative tool plugins)."""

from __future__ import annotations

import base64 as b64
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import nono_agent.toolplugins
from nono_agent.plugins.manifest import load_manifest
from nono_agent.providers.catalog import lookup_api_key, save_credential
from nono_agent.settings.models import Settings
from nono_agent.settings.sources import load_settings
from nono_agent.toolplugins.config_schema import (
    ConfigSchemaError,
    get_option,
    parse_config_schema,
    set_option,
)
from nono_agent.toolplugins.http_json import (
    HttpJsonTool,
    _sniff_image_mime,
    _sniff_suffix,
    build_client,
)
from nono_agent.toolplugins.policy import resolve_tool_policy
from nono_agent.toolplugins.registry import clear_plugin_dirs, registered_plugin_dirs
from nono_agent.toolplugins.runtime import (
    ToolPluginRuntime,
    builtin_plugin_dirs,
    discover_plugins,
    option_defaults,
    start_tool_plugin_runtime,
)
from nono_agent.toolplugins.spec import ToolSpecError, parse_tool_specs
from nono_agent.toolplugins.template import MISSING, TemplateError, render
from nono_agent.tools.context import ToolUseContext
from nono_app_server.features.toolplugins import register_builtin_plugins
from nono_app_server.features.toolplugins.schemas import ToolPluginApiKey, ToolPluginUpsert
from nono_app_server.features.toolplugins.service import (
    apply_upsert,
    public_tool_plugins,
    save_api_key,
)

_ENV_KEY = "NONO_TOOLPLUGIN_TEST_KEY"
_FAKE_PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture(autouse=True)
def _product_plugins_registered():
    """产品包的内置插件挂进引擎发现链 —— 与真实启动时 ``create_app()`` 做的一致。

    引擎自己不提供插件，所以这里必须显式注册；漏了就会看到空目录。
    """
    register_builtin_plugins()
    yield
    clear_plugin_dirs()


def _settings(values: dict[str, Any]) -> Settings:
    return load_settings(".", user_settings=values, project_settings={})


def _spec_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "http_json",
        "prefix": "demo",
        "description": "demo tool",
        "input_schema": {
            "type": "object",
            "properties": {"prompt": {"type": "string"}},
            "required": ["prompt"],
        },
        "auth": {"type": "bearer", "api_key_env": _ENV_KEY},
        "defaults": {"model": "image-01", "base_url": "https://example.com"},
        "url": "${options.base_url}/v1/image_generation",
        "headers": {"Content-Type": "application/json"},
        "body": {
            "model": "${options.model}",
            "prompt": "${args.prompt}",
            "n": "${args.n}",
        },
        "timeout_s": 5,
        "result": {"kind": "text"},
    }
    base.update(overrides)
    return base


def _tool(client_factory: Any = None, **overrides: Any) -> HttpJsonTool:
    spec = parse_tool_specs("demo", {"generate": _spec_dict(**overrides)})[0]
    return HttpJsonTool(
        spec, {}, "secret-key", client_factory=client_factory
    )  # type: ignore[arg-type]


def _stub_factory(handler: Any) -> Any:
    """返回 httpx.MockTransport 支撑的客户端工厂（测试里替掉真实网络）。"""

    def factory(timeout_s: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return factory


def _image_payload() -> dict[str, Any]:
    return {
        "data": {"image_base64": [b64.b64encode(_FAKE_PNG).decode()]},
        "base_resp": {"status_code": 0},
    }


class TestTemplate:
    def test_whole_string_preserves_type(self):
        assert render("${args.n}", {"n": 3}, {}) == 3
        assert render("${args.flag}", {"flag": True}, {}) is True

    def test_missing_arg_drops_field(self):
        body = render({"model": "${args.model}", "n": "${args.n}"}, {}, {})
        assert body == {}  # 两个引用都缺 → 两个字段都消失，不送 null

    def test_partial_substitution(self):
        rendered = render("${options.base_url}/v1", {}, {"base_url": "https://a"})
        assert rendered == "https://a/v1"

    def test_partial_with_missing_drops_whole_string(self):
        assert render("${options.b}/v1", {}, {}) is MISSING

    def test_unknown_namespace_rejected(self):
        with pytest.raises(TemplateError):
            render("${env.SECRET}", {}, {})

    def test_list_drops_missing_items(self):
        assert render(["${args.a}", "static", "${args.b}"], {"a": "x"}, {}) == ["x", "static"]


class TestSpec:
    def test_http_tool_name(self):
        spec = parse_tool_specs("demo", {"generate": _spec_dict()})[0]
        assert spec.tool_name == "demo__generate"

    def test_unknown_kind_rejected(self):
        with pytest.raises(ToolSpecError):
            parse_tool_specs("demo", {"generate": {"kind": "python"}})

    def test_missing_url_rejected(self):
        payload = _spec_dict()
        del payload["url"]
        with pytest.raises(ToolSpecError):
            parse_tool_specs("demo", {"generate": payload})

    def test_mcp_spec_requires_server(self):
        with pytest.raises(ToolSpecError):
            parse_tool_specs("demo", {"search": {"kind": "mcp"}})


class TestPolicy:
    def test_undeclared_is_disabled(self):
        policy = resolve_tool_policy(_settings({}), "minimax-image")
        assert policy.enabled is False

    def test_enabled_with_options(self):
        settings = _settings(
            {"tools": {"minimax-image": {"enabled": True, "options": {"model": "x"}}}}
        )
        policy = resolve_tool_policy(settings, "minimax-image")
        assert policy.enabled is True
        assert policy.options == {"model": "x"}

    def test_default_api_key_env_survives_missing_block(self):
        policy = resolve_tool_policy(_settings({}), "minimax-image", default_api_key_env="E")
        assert policy.api_key_env == "E"


class TestHttpJsonTool:
    async def test_optional_args_are_omitted_from_body(self):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})

        tool = _tool(client_factory=_stub_factory(handler))
        result = await tool.execute({"prompt": "a cat"}, ToolUseContext(cwd="."))
        assert result.is_error is False
        assert seen == {"model": "image-01", "prompt": "a cat"}

    async def test_bearer_header_injected(self):
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return httpx.Response(200, json={})

        tool = _tool(client_factory=_stub_factory(handler))
        await tool.execute({"prompt": "x"}, ToolUseContext(cwd="."))
        assert seen["authorization"] == "Bearer secret-key"

    async def test_missing_required_arg_fails_closed(self):
        tool = _tool()
        result = await tool.execute({}, ToolUseContext(cwd="."))
        assert result.is_error is True
        assert "prompt" in result.content

    async def test_missing_api_key_gives_actionable_error(self):
        spec = parse_tool_specs("demo", {"generate": _spec_dict()})[0]
        tool = HttpJsonTool(spec, {}, "")  # type: ignore[arg-type]
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd="."))
        assert result.is_error is True
        assert _ENV_KEY in result.content

    async def test_api_key_never_echoed_on_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="boom secret-key")

        tool = _tool(client_factory=_stub_factory(handler))
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd="."))
        assert result.is_error is True
        assert "secret-key" not in result.content
        assert "***" in result.content

    async def test_api_error_code_translated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"base_resp": {"status_code": 1004, "status_msg": "auth failed"}},
            )

        tool = _tool(client_factory=_stub_factory(handler))
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd="."))
        assert result.is_error is True
        assert "API key" in result.content

    async def test_image_result_written_to_disk(self, tmp_path):
        tool = _tool(
            client_factory=_stub_factory(
                lambda request: httpx.Response(200, json=_image_payload())
            ),
            result={
                "kind": "save_images",
                "base64_path": "data.image_base64",
                "dir": ".nono-agent/images",
            },
        )
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd=str(tmp_path)))
        assert result.is_error is False
        saved = sorted((tmp_path / ".nono-agent" / "images").glob("*.png"))
        assert len(saved) == 1
        assert saved[0].read_bytes() == _FAKE_PNG
        assert result.ui is not None and result.ui["kind"] == "image"

    async def test_base64_suffix_follows_magic_not_png_default(self, tmp_path):
        """厂商回 JPEG 时不能落一个装着 JPEG 字节的 .png。

        早期版本无条件用 .png，于是这类文件再作为参考图传回去时，扩展名
        和内容各说各话。
        """
        jpeg = b"\xff\xd8\xff\xe0" + b"fake-jpeg"
        tool = _tool(
            client_factory=_stub_factory(
                lambda request: httpx.Response(
                    200,
                    json={
                        "data": {"image_base64": [b64.b64encode(jpeg).decode()]},
                        "base_resp": {"status_code": 0},
                    },
                )
            ),
            result={
                "kind": "save_images",
                "base64_path": "data.image_base64",
                "dir": ".nono-agent/images",
            },
        )
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd=str(tmp_path)))
        assert result.is_error is False
        out = tmp_path / ".nono-agent" / "images"
        assert not list(out.glob("*.png"))  # 没有冒充 PNG 的文件
        saved = list(out.glob("*.jpg"))
        assert len(saved) == 1 and saved[0].read_bytes() == jpeg

    @pytest.mark.parametrize(
        ("data", "suffix"),
        [
            (b"\x89PNG\r\n\x1a\n", ".png"),
            (b"\xff\xd8\xff\xe0", ".jpg"),
            (b"GIF89a", ".gif"),
            (b"RIFF\x00\x00\x00\x00WEBPVP8 ", ".webp"),
            (b"not an image at all", ".png"),  # 认不出就退回，行为同 _guess_suffix
        ],
    )
    def test_sniff_suffix(self, data, suffix):
        assert _sniff_suffix(data) == suffix

    def test_input_mapper_still_rejects_non_jpg_png(self):
        """落盘放宽了格式，入参侧不能跟着放宽 —— MiniMax 只收 JPG/PNG。"""
        assert _sniff_image_mime(b"\x89PNG\r\n\x1a\n") == "image/png"
        assert _sniff_image_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"
        assert _sniff_image_mime(b"GIF89a") is None
        assert _sniff_image_mime(b"RIFF\x00\x00\x00\x00WEBP") is None
        assert _sniff_image_mime(b"nope") is None

    async def test_image_url_result_downloaded(self, tmp_path, monkeypatch):
        # cdn.example.com 解析不出来，SSRF 闸门（fail-closed）会挡下它；
        # 这里只测「拿到 URL 之后确实下载落盘」，闸门本身另有一条用例。
        monkeypatch.setattr(
            "nono_agent.toolplugins.http_json.is_url_blocked", lambda *a, **k: False
        )

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".png"):
                return httpx.Response(
                    200, content=_FAKE_PNG, headers={"content-type": "image/png"}
                )
            return httpx.Response(
                200,
                json={
                    "data": {"image_urls": ["https://cdn.example.com/a.png"]},
                    "base_resp": {"status_code": 0},
                },
            )

        tool = _tool(
            client_factory=_stub_factory(handler),
            result={"kind": "save_images", "urls_path": "data.image_urls"},
        )
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd=str(tmp_path)))
        assert result.is_error is False
        assert len(list((tmp_path / ".nono-agent" / "images").glob("*.png"))) == 1

    async def test_ssrf_guard_blocks_unreachable_host(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "data": {"image_urls": ["https://internal.invalid/a.png"]},
                    "base_resp": {"status_code": 0},
                },
            )

        tool = _tool(
            client_factory=_stub_factory(handler),
            result={"kind": "save_images", "urls_path": "data.image_urls"},
        )
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd=str(tmp_path)))
        assert result.is_error is True
        assert "SSRF" in result.content

    async def test_ssrf_guard_blocks_base_url_override(self, monkeypatch):
        """options 可以改 base_url —— 因此每次请求都要重新过闸门。"""
        monkeypatch.setattr(
            "nono_agent.toolplugins.http_json.is_url_blocked", lambda *a, **k: True
        )
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json={})

        tool = _tool(client_factory=_stub_factory(handler))
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd="."))
        assert result.is_error is True
        assert "SSRF" in result.content
        assert called is False  # 闸门在前，网络层根本没被触达

    async def test_sandbox_rejects_output_dir(self):
        class _DenySandbox:
            def validate(self, path: str) -> Path:
                raise PermissionError("escapes workspace")

        tool = _tool(
            client_factory=_stub_factory(
                lambda request: httpx.Response(200, json=_image_payload())
            ),
            result={"kind": "save_images", "base64_path": "data.image_base64"},
        )
        ctx = ToolUseContext(cwd=".", sandbox=_DenySandbox())  # type: ignore[arg-type]
        result = await tool.execute({"prompt": "x"}, ctx)
        assert result.is_error is True

    def test_fail_closed_defaults(self):
        spec = parse_tool_specs("demo", {"generate": _spec_dict()})[0]
        tool = HttpJsonTool(spec, {})  # type: ignore[arg-type]
        assert tool.is_concurrent_safe is False
        assert tool.has_write_side_effects is True


class TestImageInputMapper:
    """图生图：本地路径 → data URL。"""

    @staticmethod
    def _i2i_tool(handler: Any, **overrides: Any) -> HttpJsonTool:
        spec_dict = _spec_dict(
            **{
                "input_schema": {
                    "type": "object",
                    "properties": {"prompt": {}, "reference_images": {}},
                    "required": ["prompt", "reference_images"],
                },
                "body": {
                    "model": "${options.model}",
                    "subject_reference": "${args.reference_images}",
                },
                "inputs": {
                    "kind": "image_data_urls",
                    "arg": "reference_images",
                    "type_arg": "subject_type",
                    "max_count": 1,
                },
                "result": {"kind": "save_images", "base64_path": "data.image_base64"},
                **overrides,
            }
        )
        spec = parse_tool_specs("demo", {"edit": spec_dict})[0]
        return HttpJsonTool(spec, {}, "k", client_factory=_stub_factory(handler))

    async def test_local_file_becomes_data_url(self, tmp_path):
        ref = tmp_path / "portrait.png"
        ref.write_bytes(_FAKE_PNG)
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_image_payload())

        tool = self._i2i_tool(handler)
        result = await tool.execute(
            {"prompt": "in a garden", "reference_images": ["portrait.png"]},
            ToolUseContext(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        refs = seen["subject_reference"]
        assert len(refs) == 1
        assert refs[0]["type"] == "character"
        assert refs[0]["image_file"].startswith("data:image/png;base64,")
        assert b64.b64decode(refs[0]["image_file"].split(",", 1)[1]) == _FAKE_PNG

    async def test_https_url_passes_through_untouched(self, tmp_path):
        seen: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(json.loads(request.content))
            return httpx.Response(200, json=_image_payload())

        tool = self._i2i_tool(handler)
        await tool.execute(
            {"prompt": "x", "reference_images": ["https://cdn.example.com/a.png"]},
            ToolUseContext(cwd=str(tmp_path)),
        )
        assert seen["subject_reference"][0]["image_file"] == (
            "https://cdn.example.com/a.png"
        )

    async def test_sandbox_blocks_reference_outside_workspace(self, tmp_path):
        secret = tmp_path / "secret.png"
        secret.write_bytes(_FAKE_PNG)

        class _DenySandbox:
            def validate(self, path: str) -> Path:
                raise PermissionError("escapes workspace")

        tool = self._i2i_tool(lambda request: httpx.Response(200, json=_image_payload()))
        ctx = ToolUseContext(cwd=str(tmp_path), sandbox=_DenySandbox())  # type: ignore[arg-type]
        result = await tool.execute(
            {"prompt": "x", "reference_images": ["secret.png"]}, ctx
        )
        assert result.is_error is True
        assert "outside the workspace" in result.content

    async def test_non_image_file_rejected_by_magic_bytes(self, tmp_path):
        fake = tmp_path / "actually-a-text.png"
        fake.write_text("not an image at all", encoding="utf-8")

        tool = self._i2i_tool(lambda request: httpx.Response(200, json=_image_payload()))
        result = await tool.execute(
            {"prompt": "x", "reference_images": ["actually-a-text.png"]},
            ToolUseContext(cwd=str(tmp_path)),
        )
        assert result.is_error is True
        assert "not a JPG or PNG" in result.content

    async def test_oversize_reference_rejected(self, tmp_path):
        big = tmp_path / "big.png"
        big.write_bytes(_FAKE_PNG + b"0" * 2048)
        tool = self._i2i_tool(
            lambda request: httpx.Response(200, json=_image_payload()),
            inputs={
                "kind": "image_data_urls",
                "arg": "reference_images",
                "max_count": 1,
                "max_bytes": 512,
            },
        )
        result = await tool.execute(
            {"prompt": "x", "reference_images": ["big.png"]},
            ToolUseContext(cwd=str(tmp_path)),
        )
        assert result.is_error is True
        assert "over the" in result.content

    async def test_too_many_references_rejected(self, tmp_path):
        ref = tmp_path / "p.png"
        ref.write_bytes(_FAKE_PNG)
        tool = self._i2i_tool(lambda request: httpx.Response(200, json=_image_payload()))
        result = await tool.execute(
            {"prompt": "x", "reference_images": ["p.png", "p.png"]},
            ToolUseContext(cwd=str(tmp_path)),
        )
        assert result.is_error is True
        assert "at most 1" in result.content

    async def test_missing_reference_fails_on_required_arg(self, tmp_path):
        tool = self._i2i_tool(lambda request: httpx.Response(200, json=_image_payload()))
        result = await tool.execute({"prompt": "x"}, ToolUseContext(cwd=str(tmp_path)))
        assert result.is_error is True
        assert "reference_images" in result.content

    async def test_network_not_touched_when_input_invalid(self, tmp_path):
        called = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal called
            called = True
            return httpx.Response(200, json=_image_payload())

        tool = self._i2i_tool(handler)
        result = await tool.execute(
            {"prompt": "x", "reference_images": ["missing.png"]},
            ToolUseContext(cwd=str(tmp_path)),
        )
        assert result.is_error is True
        assert called is False


class TestBuiltinPlugins:
    """内置插件属于**产品包**，引擎不打包它们 —— 这里验的是那条边界。"""

    def test_engine_package_ships_no_concrete_plugins(self):
        """引擎目录里不该再有 plugin.json：厂商集成是产品定义。"""
        engine_dir = Path(nono_agent.toolplugins.__file__).parent
        assert list(engine_dir.rglob("plugin.json")) == []

    def test_product_plugin_dir_ships_two(self):
        assert {p.name for p in builtin_plugin_dirs()} == {"minimax_image", "tavily"}

    def test_registration_is_idempotent(self):
        before = len(registered_plugin_dirs())
        register_builtin_plugins()
        register_builtin_plugins()
        assert len(registered_plugin_dirs()) == before

    def test_minimax_declaration_parses(self):
        directory = next(p for p in builtin_plugin_dirs() if p.name == "minimax_image")
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        assert manifest["name"] == "minimax-image"
        specs = parse_tool_specs(manifest["name"], manifest["tools"])
        assert [s.tool_name for s in specs] == [
            "minimax__edit_image",
            "minimax__generate_image",
        ]
        # 厂商数据值（端点、模型名、密钥变量名）归产品所有、随时会调，
        # 这里只断言结构，不断言具体值 —— 写死值等于让插件一改就得改测试。
        assert specs[1].defaults["model"]
        assert specs[1].defaults["base_url"].startswith("https://")
        assert specs[1].api_key_env.endswith("_API_KEY")

    def test_minimax_image_to_image_declaration(self):
        directory = next(p for p in builtin_plugin_dirs() if p.name == "minimax_image")
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        spec = parse_tool_specs(manifest["name"], manifest["tools"])[0]
        assert spec.tool_name == "minimax__edit_image"
        # 与文生图同端点，差别只在多一个 subject_reference
        assert spec.url.endswith("/v1/image_generation")
        assert "subject_reference" in spec.body
        assert spec.inputs["kind"] == "image_data_urls"
        assert spec.inputs["arg"] == "reference_images"
        assert spec.required_args() == ["prompt", "reference_images"]

    def test_tavily_declaration_parses(self):
        directory = next(p for p in builtin_plugin_dirs() if p.name == "tavily")
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        specs = parse_tool_specs(manifest["name"], manifest["tools"])
        assert specs[0].server == "tavily"
        assert "tavily_search" in specs[0].allowed_tools
        assert manifest["mcp"]["tavily"]["url"] == "https://mcp.tavily.com/mcp/"

    def test_tavily_key_travels_in_header_not_url(self):
        """密钥走 Authorization 头，不进 URL。

        官方文档给的是 ``?tavilyApiKey=``，但同时也支持 Authorization 头。
        选头是有意的：URL 会进服务端访问日志、浏览器历史、错误回显，
        而 header 不会。实测远程网关认这个头（无凭证时回 401 + OAuth
        挑战，带头则直接握手成功）。

        这条断言是为了防止有人"照官方示例"把 Key 改回 URL —— 那是个安全
        回退，功能上照样能跑。
        """
        directory = next(p for p in builtin_plugin_dirs() if p.name == "tavily")
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        spec = parse_tool_specs(manifest["name"], manifest["tools"])[0]
        assert spec.auth.type == "bearer"
        assert spec.auth.api_key_env.endswith("_API_KEY")
        assert "tavilyApiKey" not in manifest["mcp"]["tavily"]["url"]
        assert "tavilyApiKey" not in json.dumps(manifest)

    def test_tavily_whitelist_is_deliberately_narrow(self):
        """远程端点实际暴露 6 个工具，我们只放行 2 个。

        tavily_feedback 是给人/评测用的反馈通道（描述里还要求"必须逐条打分"），
        放进模型的工具表会让它去刷反馈调用；crawl/map/research 是重操作，
        按需再放开。
        """
        directory = next(p for p in builtin_plugin_dirs() if p.name == "tavily")
        manifest = json.loads((directory / "plugin.json").read_text(encoding="utf-8"))
        spec = parse_tool_specs(manifest["name"], manifest["tools"])[0]
        assert set(spec.allowed_tools) == {"tavily_search", "tavily_extract"}

    def test_discovered_after_registration(self, tmp_path):
        assert {m.name: source for m, source in discover_plugins(tmp_path)} == {
            "minimax-image": "bundled",
            "tavily": "bundled",
        }

    def test_project_plugin_overrides_bundled(self, tmp_path):
        plugin_dir = tmp_path / ".nono-agent" / "plugins" / "tavily"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "tavily", "version": "9.9.9"}), encoding="utf-8"
        )
        versions = {m.name: m.version for m, _ in discover_plugins(tmp_path)}
        assert versions["tavily"] == "9.9.9"


class TestRuntime:
    async def test_disabled_plugin_registers_nothing(self, tmp_path):
        runtime = await start_tool_plugin_runtime(tmp_path, settings=_settings({}))
        assert runtime.tools == []
        assert {s.id for s in runtime.statuses} == {"minimax-image", "tavily"}
        assert all(s.enabled is False for s in runtime.statuses)

    async def test_enabled_http_plugin_builds_tool(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_ENV_KEY, raising=False)
        settings = _settings(
            {"tools": {"minimax-image": {"enabled": True, "api_key_env": _ENV_KEY}}}
        )
        runtime = await start_tool_plugin_runtime(tmp_path, settings=settings)
        assert [t.name for t in runtime.tools] == [
            "minimax__edit_image",
            "minimax__generate_image",
        ]
        status = next(s for s in runtime.statuses if s.id == "minimax-image")
        assert status.running is True
        # 没给 Key 时工具仍注册 —— 失败点在调用时给出可执行的报错，
        # 而不是「开关开了但工具凭空消失」。
        assert status.configured is False

    async def test_settings_override_api_key_env(self, tmp_path, monkeypatch):
        """settings 里的 api_key_env 覆盖清单指针 —— 取值与报错都用它。"""
        monkeypatch.setenv(_ENV_KEY, "from-override")
        settings = _settings(
            {"tools": {"minimax-image": {"enabled": True, "api_key_env": _ENV_KEY}}}
        )
        runtime = await start_tool_plugin_runtime(tmp_path, settings=settings)
        status = next(s for s in runtime.statuses if s.id == "minimax-image")
        assert status.api_key_env == _ENV_KEY
        assert status.configured is True
        assert runtime.tools[0]._api_key == "from-override"

    async def test_enabled_mcp_plugin_without_key_is_skipped(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NONO_TAVILY_API_KEY", raising=False)
        settings = _settings(
            {
                "tools": {
                    "tavily": {"enabled": True, "api_key_env": "NONO_TAVILY_API_KEY"}
                }
            }
        )
        runtime = await start_tool_plugin_runtime(tmp_path, settings=settings)
        assert runtime.tools == []
        status = next(s for s in runtime.statuses if s.id == "tavily")
        assert status.error is not None
        assert "NONO_TAVILY_API_KEY" in status.error

    async def test_global_switch_disables_everything(self, tmp_path):
        settings = _settings(
            {"tools": {"enabled": False, "minimax-image": {"enabled": True}}}
        )
        runtime = await start_tool_plugin_runtime(tmp_path, settings=settings)
        assert runtime.enabled is False
        assert runtime.tools == []

    async def test_options_override_defaults(self, tmp_path, monkeypatch):
        monkeypatch.delenv(_ENV_KEY, raising=False)
        settings = _settings(
            {
                "tools": {
                    "minimax-image": {
                        "enabled": True,
                        "api_key_env": _ENV_KEY,
                        "options": {"model": "image-01-live"},
                    }
                }
            }
        )
        runtime = await start_tool_plugin_runtime(tmp_path, settings=settings)
        options = runtime.tools[0]._options
        assert options["model"] == "image-01-live"
        # 未覆盖的键仍取清单里的默认值
        assert options["base_url"].startswith("https://")

    async def test_bad_declaration_isolated(self, tmp_path):
        plugin_dir = tmp_path / ".nono-agent" / "plugins" / "broken"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "broken",
                    "version": "1.0.0",
                    "tools": {"t": {"kind": "http_json"}},
                }
            ),
            encoding="utf-8",
        )
        settings = _settings({"tools": {"broken": {"enabled": True}}})
        runtime = await start_tool_plugin_runtime(tmp_path, settings=settings)
        assert runtime.tools == []
        status = next(s for s in runtime.statuses if s.id == "broken")
        assert status.error is not None and "url" in status.error

    async def test_close_is_idempotent(self):
        runtime = ToolPluginRuntime()
        await runtime.close()
        assert runtime.tools == []


class TestClientFactory:
    async def test_default_client_does_not_follow_redirects(self):
        client = build_client(1.0)
        try:
            assert client.follow_redirects is False
        finally:
            await client.aclose()


class TestConfigSchema:
    """配置面板 schema：清单声明怎么渲染，值从 settings/defaults 回落。"""

    def test_parse_fields_and_choices(self):
        schema = parse_config_schema(
            {
                "api_key": {"label": "API Key", "hint": "去官网拿"},
                "fields": [
                    {"key": "model", "label": "模型", "type": "string"},
                    {
                        "key": "server.transport",
                        "label": "连接方式",
                        "type": "enum",
                        "choices": [
                            {"value": "http", "label": "远程"},
                            {"value": "stdio", "label": "本地"},
                        ],
                    },
                ],
            }
        )
        assert [f.key for f in schema.fields] == ["model", "server.transport"]
        assert schema.fields[1].choices == (("http", "远程"), ("stdio", "本地"))
        assert schema.api_key_label == "API Key"
        assert schema.api_key_hint == "去官网拿"

    def test_unknown_type_rejected(self):
        with pytest.raises(ConfigSchemaError):
            parse_config_schema({"fields": [{"key": "a", "type": "color-picker"}]})

    def test_coerce_number_and_enum(self):
        schema = parse_config_schema(
            {"fields": [{"key": "n", "type": "number"}, {"key": "t", "type": "enum"}]}
        )
        assert schema.fields[0].coerce("3") == 3
        with pytest.raises(ConfigSchemaError):
            schema.fields[0].coerce("three")

    def test_dotted_option_read_write(self):
        options: dict[str, Any] = {}
        set_option(options, "server.transport", "stdio")
        assert options == {"server": {"transport": "stdio"}}
        assert get_option(options, "server.transport") == "stdio"
        assert get_option(options, "server.url", "fallback") == "fallback"
        assert get_option(options, "missing", "d") == "d"

    def test_option_defaults_merged_from_tool_specs(self):
        directory = next(p for p in builtin_plugin_dirs() if p.name == "minimax_image")
        manifest = load_manifest(directory)
        assert manifest is not None
        defaults = option_defaults(manifest)
        # 两个工具声明同一组默认值，面板只应看到一份
        assert defaults["model"] == "image-01"
        assert defaults["output_dir"] == ".nono-agent/images"


class TestConfigPanelPayload:
    """服务端出参：面板拿到的就是它要渲染的全部信息。"""

    def _snapshot(self, tmp_path: Any) -> dict[str, Any]:
        manager = MagicMock()
        manager.sessions = {}
        return public_tool_plugins(str(tmp_path), manager)

    def test_fields_carry_resolved_values(self, tmp_path):
        payload = self._snapshot(tmp_path)
        plugin = next(p for p in payload["plugins"] if p["id"] == "minimax-image")
        values = {f["key"]: f["value"] for f in plugin["fields"]}
        # 没写过任何配置时回落到工具声明的 defaults（具体值由产品定义决定）
        assert set(values) == {"model", "base_url", "output_dir"}
        assert all(value not in (None, "") for value in values.values())
        assert plugin["api_key"]["env"].endswith("_API_KEY")
        assert "value" not in plugin["api_key"]  # 密钥只报"配没配"

    def test_settings_value_overrides_default(self, tmp_path):
        (tmp_path / ".nono-agent").mkdir()
        (tmp_path / ".nono-agent" / "settings.json").write_text(
            json.dumps({"tools": {"minimax-image": {"options": {"model": "image-01-live"}}}}),
            encoding="utf-8",
        )
        payload = self._snapshot(tmp_path)
        plugin = next(p for p in payload["plugins"] if p["id"] == "minimax-image")
        values = {f["key"]: f["value"] for f in plugin["fields"]}
        assert values["model"] == "image-01-live"
        # 没配的键仍回落默认值（具体值由产品定义决定，这里只验"有值且未被覆盖"）
        assert isinstance(values["base_url"], str) and values["base_url"]

    def test_payload_keys_match_frontend_contract(self, tmp_path):
        """出参的键名必须与 @nono/protocol 的类型对得上，否则前端静默拿到 undefined。"""
        payload = self._snapshot(tmp_path)
        assert set(payload) == {"enabled", "credentials_path", "plugins"}
        plugin = next(p for p in payload["plugins"] if p["id"] == "minimax-image")
        assert set(plugin) == {
            "id",
            "version",
            "description",
            "source",
            "enabled",
            "api_key",
            "fields",
            "tools",
            "running",
            "error",
        }
        assert set(plugin["api_key"]) == {
            "env",
            "required",
            "configured",
            "label",
            "hint",
            "placeholder",
        }
        assert set(plugin["fields"][0]) == {
            "key",
            "label",
            "type",
            "hint",
            "placeholder",
            "choices",
            "value",
        }

    def test_secret_never_present_in_payload(self, tmp_path):
        # 先真的写进去一把钥匙，再断言接口出参里没有它
        save_credential("NONO_TAVILY_API_KEY", "tvly-SUPERSECRET-VALUE")
        payload = self._snapshot(tmp_path)
        text = json.dumps(payload, ensure_ascii=False)
        assert "tvly-SUPERSECRET-VALUE" not in text
        plugin = next(p for p in payload["plugins"] if p["id"] == "tavily")
        assert plugin["api_key"]["configured"] is True   # 只回答配没配
        assert plugin["api_key"]["env"] == "NONO_TAVILY_API_KEY"


class TestConfigPanelWrites:
    """面板的写路径：开关 / 参数 / 密钥三条。"""

    async def _manager(self) -> Any:
        manager = MagicMock()
        manager.sessions = {}
        manager.reload_extensions_for_cwd = AsyncMock()
        return manager

    async def test_toggle_and_options_persist(self, tmp_path):
        manager = await self._manager()
        payload = await apply_upsert(
            "minimax-image",
            ToolPluginUpsert(cwd=str(tmp_path), enabled=True, options={"model": "image-01-live"}),
            manager,
        )
        plugin = next(p for p in payload["plugins"] if p["id"] == "minimax-image")
        assert plugin["enabled"] is True
        values = {f["key"]: f["value"] for f in plugin["fields"]}
        assert values["model"] == "image-01-live"

        saved = json.loads((tmp_path / ".nono-agent" / "settings.json").read_text(encoding="utf-8"))
        assert saved["tools"]["minimax-image"]["enabled"] is True
        # 面板这次只提交了 model，base_url 仍走默认 —— 不该被写成空值
        assert saved["tools"]["minimax-image"]["options"] == {"model": "image-01-live"}
        manager.reload_extensions_for_cwd.assert_awaited_once()

    async def test_dotted_option_key_nests(self, tmp_path):
        manager = await self._manager()
        await apply_upsert(
            "tavily",
            ToolPluginUpsert(
                cwd=str(tmp_path), enabled=True, options={"server.transport": "stdio"}
            ),
            manager,
        )
        saved = json.loads((tmp_path / ".nono-agent" / "settings.json").read_text(encoding="utf-8"))
        # 扁平键会存进去但运行时读不到 —— 必须是嵌套的
        assert saved["tools"]["tavily"]["options"] == {"server": {"transport": "stdio"}}

    async def test_enum_value_outside_choices_rejected(self, tmp_path):
        manager = await self._manager()
        with pytest.raises(ValueError, match="候选列表"):
            await apply_upsert(
                "tavily",
                ToolPluginUpsert(cwd=str(tmp_path), options={"server.transport": "carrier-pigeon"}),
                manager,
            )

    async def test_secret_looking_option_rejected(self, tmp_path):
        manager = await self._manager()
        with pytest.raises(ValueError, match="api-key endpoint"):
            await apply_upsert(
                "minimax-image",
                ToolPluginUpsert(cwd=str(tmp_path), options={"api_key": "sk-oops"}),
                manager,
            )

    async def test_api_key_written_without_settings_entry(self, tmp_path):
        """首次配置时 settings 里还没有任何东西 —— 密钥指针要从清单回落。"""
        manager = await self._manager()
        payload = await save_api_key(
            "minimax-image",
            ToolPluginApiKey(cwd=str(tmp_path), api_key="key-from-panel"),
            manager,
        )
        plugin = next(p for p in payload["plugins"] if p["id"] == "minimax-image")
        assert plugin["api_key"]["configured"] is True
        assert "key-from-panel" not in json.dumps(payload)
        # 写进了清单声明的那个凭据名（产品可改名，测试不该钉死）
        assert lookup_api_key(plugin["api_key"]["env"]) == "key-from-panel"

    async def test_api_key_clear(self, tmp_path):
        manager = await self._manager()
        await save_api_key(
            "minimax-image",
            ToolPluginApiKey(cwd=str(tmp_path), api_key="to-be-cleared"),
            manager,
        )
        payload = await save_api_key(
            "minimax-image",
            ToolPluginApiKey(cwd=str(tmp_path), api_key=""),
            manager,
        )
        plugin = next(p for p in payload["plugins"] if p["id"] == "minimax-image")
        assert plugin["api_key"]["configured"] is False
