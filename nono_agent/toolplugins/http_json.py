"""``http_json`` 工具执行器 —— 把一个声明式 HTTP 端点变成 Tool Protocol 工具。

三个必须守住的边界：

1. **密钥不落地**：只从 ``api_key_env`` 指向的凭据里取，且任何错误信息都经
   过脱敏，绝不回显。
2. **出网受检**：URL 允许被 ``options.base_url`` 改写，因此每次请求前都过
   一遍 SSRF 判定（复用 web_fetch 的同一份实现）。
3. **产物落盘**：图像类结果写进沙箱目录再把路径交回模型。工具结果里塞
   base64 图对当前 provider 链路没有意义 —— 只有 user 消息才会被解析成
   image 块。
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from nono_agent.core.types import ToolResult
from nono_agent.toolplugins.auth import append_query_auth, header_auth, redact
from nono_agent.toolplugins.spec import HttpToolSpec
from nono_agent.toolplugins.template import MISSING, TemplateError, render
from nono_agent.tools.builtin._common import error_result, ok_result, truncate_text
from nono_agent.tools.protocol import ToolBase
from nono_agent.utils.net_guard import http_proxy_enabled, is_url_blocked

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 30_000
# 参考图上限：MiniMax 侧要求单图 <10MB/JPG/PNG，且建议只传一张正面肖像。
# 这里默认只收一张 —— 一张 10MB 的图 base64 后就有 13MB 请求体，多传几张
# 只会让超时概率上升，不会让结果变好。需要更多就改清单里的 max_count。
_MAX_REFERENCE_BYTES = 10 * 1024 * 1024
_IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"

# 测试替换这个工厂函数来打桩网络层（httpx.MockTransport）。
ClientFactory = Callable[[float], httpx.AsyncClient]


def build_client(timeout_s: float) -> httpx.AsyncClient:
    """默认客户端工厂。不跟随重定向 —— SSRF 判定针对的是最初那个 URL。"""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s),
        follow_redirects=False,
    )


class InputError(ValueError):
    """入参映射失败 —— 报给模型看，因此消息必须说清「怎么改」。"""


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _sniff_image_mime(data: bytes) -> str | None:
    """按魔数判定图片类型。

    看内容不看扩展名：`.png` 结尾的文本文件会让对方 400，而报错会指向
    一个看起来完全无辜的参数。
    """
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    return None


class HttpJsonTool(ToolBase):
    """声明式 HTTP JSON 工具。"""

    _is_concurrent_safe = False
    _has_write_side_effects = True

    def __init__(
        self,
        spec: HttpToolSpec,
        options: dict[str, Any],
        api_key: str = "",
        *,
        client_factory: ClientFactory | None = None,
    ) -> None:
        self._spec = spec
        self._options = {**spec.defaults, **options}
        self._api_key = api_key
        self._client_factory = client_factory or build_client
        self._name = spec.tool_name
        self._description = spec.description
        self._input_schema = spec.input_schema

    @property
    def plugin_id(self) -> str:
        return self._spec.plugin_id

    @property
    def is_concurrent_safe(self) -> bool:
        return self._spec.concurrent_safe

    @property
    def has_write_side_effects(self) -> bool:
        return self._spec.write_side_effects

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        args = {str(k): v for k, v in input_params.items()}
        missing = [name for name in self._spec.required_args() if not args.get(name)]
        if missing:
            return error_result(
                self._name, f"Missing required argument(s): {', '.join(missing)}."
            )

        try:
            args = await self._apply_inputs(args, context)
        except InputError as exc:
            return error_result(self._name, str(exc))

        try:
            url = self._render_url(args)
            headers = self._render_headers(args)
            query = self._render_query(args)
            body = render(self._spec.body, args, self._options)
        except TemplateError as exc:
            return error_result(self._name, f"Invalid tool declaration: {exc}")

        if body is MISSING:
            body = None

        if self._spec.auth.needs_api_key and not self._api_key:
            return error_result(
                self._name,
                f"{self._spec.plugin_id} is not configured: missing API key "
                f"(${self._spec.auth.api_key_env}). Set it in the settings panel.",
            )

        if is_url_blocked(url, skip_dns=http_proxy_enabled()):
            return error_result(
                self._name, f"Request to {url} is blocked by the SSRF guard."
            )

        try:
            async with self._client_factory(self._spec.timeout_s) as client:
                response = await self._send(client, url, headers, query, body)
        except httpx.HTTPError as exc:
            return error_result(self._name, self._redact(f"Request failed: {exc}"))

        payload = self._parse(response)
        if isinstance(payload, ToolResult):
            return payload
        assert isinstance(payload, dict)
        return await self._handle_payload(payload, context)

    async def _send(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: Any,
    ) -> httpx.Response:
        headers = {**headers, **self._auth_headers()}
        url = self._apply_query_auth(url, query)
        if self._spec.method in {"GET", "HEAD"}:
            return await client.request(self._spec.method, url, headers=headers, params=query)
        return await client.request(
            self._spec.method, url, headers=headers, params=query, json=body
        )

    def _parse(self, response: httpx.Response) -> Any:
        if response.status_code >= 400:
            detail = self._redact(response.text[:500])
            return error_result(
                self._name,
                f"HTTP {response.status_code} from {self._spec.plugin_id}: {detail}",
            )
        try:
            return response.json()
        except (json.JSONDecodeError, ValueError):
            return error_result(
                self._name,
                self._redact(f"Non-JSON response: {response.text[:500]}"),
            )

    # ------------------------------------------------------------------
    # 入参映射
    # ------------------------------------------------------------------

    async def _apply_inputs(self, args: dict[str, Any], context: Any) -> dict[str, Any]:
        """渲染请求体**之前**先把本地文件变成 API 认的形式。

        模板层刻意不提供 ``${file:...}`` —— 那等于给清单开一个任意文件读的
        口子。需要读文件的工具用这里这个显式声明的映射器，读哪个入参、
        读多大、走不走沙箱，全部写在清单里。
        """
        spec = self._spec.inputs
        kind = str(spec.get("kind", ""))
        if not kind:
            return args
        if kind != "image_data_urls":
            raise InputError(f"unsupported input mapper {kind!r}")

        arg_name = str(spec.get("arg", "")).strip()
        if not arg_name:
            raise InputError("input mapper is missing 'arg'")

        raw = args.get(arg_name)
        items = _as_list(raw)
        max_count = int(spec.get("max_count", 1))
        max_bytes = int(spec.get("max_bytes", _MAX_REFERENCE_BYTES))
        subject_type = str(args.get(spec.get("type_arg", "subject_type")) or "character")

        if len(items) > max_count:
            raise InputError(
                f"at most {max_count} reference image(s) are accepted, got {len(items)}"
            )

        refs: list[dict[str, str]] = []
        for item in items:
            refs.append(
                {
                    "type": subject_type,
                    "image_file": await self._as_image_reference(item, context, max_bytes),
                }
            )
        return {**args, arg_name: refs}

    async def _as_image_reference(
        self, item: str, context: Any, max_bytes: int
    ) -> str:
        """把一条参考图入参变成公网 URL / data URL；本地文件读成 data URL。"""
        value = str(item).strip()
        if not value:
            raise InputError("empty reference image")
        if value.startswith("data:image/") or value.startswith(("http://", "https://")):
            return value

        # 本地文件：必须过沙箱，否则模型可以把工作区外的文件塞进请求体。
        try:
            path = Path(context.resolve_path(value))
        except Exception as exc:
            raise InputError(f"reference image '{value}' is outside the workspace: {exc}")
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise InputError(f"cannot read reference image '{value}': {exc}")

        if len(data) > max_bytes:
            raise InputError(
                f"reference image '{value}' is {len(data) // 1024}KB, "
                f"over the {max_bytes // (1024 * 1024)}MB limit"
            )
        mime = _sniff_image_mime(data)
        if mime is None:
            raise InputError(
                f"reference image '{value}' is not a JPG or PNG file"
            )
        return f"data:{mime};base64,{base64.b64encode(data).decode()}"

    # ------------------------------------------------------------------
    # 结果映射
    # ------------------------------------------------------------------

    async def _handle_payload(self, payload: dict[str, Any], context: Any) -> ToolResult:
        failure = _api_failure(payload)
        if failure is not None:
            return error_result(self._name, failure)

        kind = self._spec.result_kind
        if kind == "save_images":
            return await self._save_images(payload, context)
        return ok_result(
            self._name,
            truncate_text(json.dumps(payload, ensure_ascii=False, indent=2), _MAX_TEXT_CHARS),
        )

    async def _save_images(self, payload: dict[str, Any], context: Any) -> ToolResult:
        spec = self._spec.result
        out_dir_raw = str(spec.get("dir", "") or "").strip() or ".nono-agent/images"
        target = self._render_text(out_dir_raw, self._options) or ".nono-agent/images"

        try:
            base_dir = Path(context.resolve_path(target))
            base_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:  # 沙箱拒绝 / 无权限
            return error_result(self._name, f"Cannot write images to '{target}': {exc}")

        urls = _dig(payload, str(spec.get("urls_path", "")))
        blobs = _dig(payload, str(spec.get("base64_path", "")))
        urls = [str(u) for u in urls] if isinstance(urls, list) else []
        blobs = [str(b) for b in blobs] if isinstance(blobs, list) else []

        if not urls and not blobs:
            return ok_result(self._name, "The service returned no images.")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        written: list[str] = []
        try:
            for index, blob in enumerate(blobs):
                suffix = _guess_suffix(None)
                written.append(_write(base_dir, f"{stamp}-{index}{suffix}", base64.b64decode(blob)))
            for offset, url in enumerate(urls):
                index = len(blobs) + offset
                suffix, data = await self._download(url)
                written.append(_write(base_dir, f"{stamp}-{index}{suffix}", data))
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return error_result(self._name, self._redact(f"Failed to save images: {exc}"))

        lines = [f"Saved {len(written)} image(s) to {base_dir}:"]
        lines.extend(f"- {path}" for path in written)
        result = ok_result(self._name, "\n".join(lines))
        result.ui = {"kind": "image", "images": [{"path": path} for path in written]}
        return result

    async def _download(self, url: str) -> tuple[str, bytes]:
        if is_url_blocked(url, skip_dns=http_proxy_enabled()):
            raise ValueError(f"image url {url} is blocked by the SSRF guard")
        async with self._client_factory(self._spec.timeout_s) as client:
            response = await client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            return _guess_suffix(response.headers.get("content-type")), response.content

    # ------------------------------------------------------------------
    # 组装
    # ------------------------------------------------------------------

    def _render_url(self, args: dict[str, Any]) -> str:
        return self._render_text(self._spec.url, args, self._options, url=True)

    def _render_headers(self, args: dict[str, Any]) -> dict[str, Any]:
        return {
            key: self._render_text(value, args, self._options, url=True)
            for key, value in self._spec.headers.items()
        }

    def _render_query(self, args: dict[str, Any]) -> dict[str, Any]:
        rendered: dict[str, Any] = {}
        for key, value in self._spec.query.items():
            result = render(value, args, self._options)
            if result is not MISSING:
                rendered[key] = result
        return rendered

    def _render_text(
        self,
        raw: str,
        args: dict[str, Any] | None = None,
        options: dict[str, Any] | None = None,
        *,
        url: bool = False,
    ) -> str:
        result = render(raw, args or {}, options if options is not None else self._options)
        if result is MISSING:
            return ""
        return result if isinstance(result, str) else str(result)

    def _auth_headers(self) -> dict[str, str]:
        if not self._api_key or not self._spec.auth.needs_api_key:
            return {}
        auth = self._spec.auth
        if auth.type == "query":
            return {}
        return header_auth(auth.type, auth.header, auth.prefix, self._api_key)

    def _apply_query_auth(self, url: str, query: dict[str, Any]) -> str:
        auth = self._spec.auth
        if not self._api_key or auth.type != "query":
            return url
        return append_query_auth(url, auth.query_param, self._api_key)

    def _redact(self, text: str) -> str:
        return redact(text, self._api_key)


# ----------------------------------------------------------------------
# 响应约定与落盘
# ----------------------------------------------------------------------


def _api_failure(payload: dict[str, Any]) -> str | None:
    """翻译厂商返回的业务错误码（MiniMax 走 ``base_resp.status_code``）。"""
    base = payload.get("base_resp")
    if not isinstance(base, dict):
        return None
    try:
        code = int(base.get("status_code", 0))
    except (TypeError, ValueError):
        return None
    if code == 0:
        return None
    message = str(base.get("status_msg", "")).strip() or "unknown error"
    hints = {
        1002: "rate limited — retry later",
        1004: "authentication failed — check the API key",
        1008: "insufficient account balance",
        1026: "prompt rejected by content safety",
        2013: "invalid parameters",
        2049: "invalid API key",
    }
    hint = hints.get(code, "")
    return f"API error {code}: {message}" + (f" ({hint})" if hint else "")


def _dig(payload: Any, path: str) -> Any:
    """按点分路径取值（两段，不做通用 JSONPath）。"""
    if not path:
        return None
    node = payload
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _guess_suffix(content_type: str | None) -> str:
    if content_type:
        mime = content_type.split(";", 1)[0].strip().lower()
        if mime in _IMAGE_SUFFIXES:
            return _IMAGE_SUFFIXES[mime]
    return ".png"


def _write(base_dir: Path, filename: str, data: bytes) -> str:
    path = base_dir / re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    path.write_bytes(data)
    return str(path)
