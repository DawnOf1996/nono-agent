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

import asyncio
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
from nono_agent.shell.jobs import STATUS_COMPLETED, STATUS_FAILED
from nono_agent.shell.spill import OutputDelta
from nono_agent.toolplugins.auth import append_query_auth, header_auth, redact
from nono_agent.toolplugins.spec import HttpToolSpec
from nono_agent.toolplugins.template import MISSING, TemplateError, render
from nono_agent.tools.builtin._common import error_result, ok_result, truncate_text
from nono_agent.tools.protocol import ToolBase
from nono_agent.utils.net_guard import http_proxy_enabled, is_url_blocked

logger = logging.getLogger(__name__)

_MAX_TEXT_CHARS = 30_000
# 单张参考图的字节上限。MiniMax H3-Image 允许单文件到 64 MiB，但 base64 编码
# 要膨胀约 4/3，而请求体总大小上限就是 64 MiB —— 按原图 48 MiB 卡，编码后
# 正好落在上限内。
_MAX_REFERENCE_BYTES = 48 * 1024 * 1024
_IMAGE_SUFFIXES = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"
# HEIC/HEIF 容器：第 4-8 字节是 "ftyp"，紧跟着的是 brand。
_HEIC_BRANDS = frozenset(
    {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"}
)

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

    **只认 MiniMax H3-Image 收的那几种**：JPG/PNG/WEBP/HEIC(HEIF)。收件
    标准由厂商定，认不出的类型落到下面的报错上才是对的。落盘侧要看更宽的
    类型集合，用 :func:`_sniff_suffix`。
    """
    if data.startswith(_PNG_MAGIC):
        return "image/png"
    if data.startswith(_JPEG_MAGIC):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return "image/heic"
    return None


def _sniff_suffix(data: bytes) -> str:
    """落盘用的后缀 —— base64 串里没有类型信息，只能先解码再嗅探魔数。

    早期版本在这里写死 ``.png``，于是厂商回 JPEG 时会落下一个装着 JPEG
    字节的 ``.png``：下一次再把它当参考图传回去，扩展名和内容各说各话。
    认不出就退回 ``.png``，与 :func:`_guess_suffix` 的兜底保持一致。
    """
    if data.startswith(_PNG_MAGIC):
        return ".png"
    if data.startswith(_JPEG_MAGIC):
        return ".jpg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIC_BRANDS:
        return ".heic"
    return ".png"


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

        # SSRF 判定里含 DNS 解析 —— 那是**同步阻塞**调用，留在事件循环里会把整个
        # sidecar 冻住（DNS 慢的时候能到好几秒，那期间任何接口都不响应，症状就是
        # 「一动某个工具，别的请求全卡」）。挪到线程里做。
        blocked = await asyncio.to_thread(
            is_url_blocked, url, skip_dns=http_proxy_enabled()
        )
        if blocked:
            return error_result(
                self._name, f"Request to {url} is blocked by the SSRF guard."
            )

        background = self._spec.background
        if background.enabled:
            return await self._run_background(url, headers, query, body, context, background)
        return await self._send_and_handle(url, headers, query, body, context)

    async def _send_and_handle(
        self,
        url: str,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: Any,
        context: Any,
    ) -> ToolResult:
        """发请求并把响应映射成工具结果。"""
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

    # ------------------------------------------------------------------
    # 后台化
    # ------------------------------------------------------------------

    async def _run_background(
        self,
        url: str,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: Any,
        context: Any,
        background: Any,
    ) -> ToolResult:
        """长请求转后台：这一轮立刻放行。

        等 ``wait_s`` 还没回来，就把请求**留在后台**继续跑（不取消 —— 厂商那边
        已经在生成并计费了，取消只是白花钱），同时登记成一个作业：模型可以用
        ``job_output`` 跟进，用户在任务板看得到进度。

        立刻放行的意义不在于"请求变快了"，而在于**对话那条连接被还回去了**：
        浏览器对同源并发连接有上限，长连接占满时文件树这类普通请求只能排队。
        """
        wait_s = self._resolve_wait_s(background)
        if wait_s <= 0:
            return await self._send_and_handle(url, headers, query, body, context)

        registry = _job_registry(context)
        process = _BackgroundProcess()
        job = None
        if registry is not None:
            job = registry.start(
                kind=background.kind, title=background.title or self._name
            )
            registry.adopt(job.id, process)

        task = asyncio.create_task(
            self._background_run(url, headers, query, body, context, registry, job, process),
            name=f"tool-bg:{self._name}",
        )
        process.attach(task)

        # 注意用 wait 而不是 wait_for：wait_for 超时会把 task **取消**掉，
        # 而这里的请求必须继续跑下去。
        done, _pending = await asyncio.wait({task}, timeout=wait_s)
        if task in done:
            return task.result()

        # 还没回来 —— 放这一轮走，把连接名额还给别的请求。
        signal = getattr(context, "signal", None)
        if registry is not None and job is not None and signal is not None:
            registry.attach_abort_watch(job.id, signal)
        return ok_result(self._name, self._pending_text(job, background))

    async def _background_run(
        self,
        url: str,
        headers: dict[str, Any],
        query: dict[str, Any],
        body: Any,
        context: Any,
        registry: Any,
        job: Any,
        process: _BackgroundProcess,
    ) -> ToolResult:
        """后台请求的收尾：拿到结果，落盘，写回作业。"""
        try:
            result = await self._send_and_handle(url, headers, query, body, context)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 —— 后台失败不该外溢
            result = error_result(
                self._name, self._redact(f"Background request failed: {exc}")
            )
        text = result.content if isinstance(result.content, str) else str(result.content)
        process.write(text)
        if registry is not None and job is not None:
            registry.set_preview(job.id, text)
            await registry.finish(
                job.id,
                status=STATUS_FAILED if result.is_error else STATUS_COMPLETED,
            )
        return result

    def _resolve_wait_s(self, background: Any) -> float:
        """把 ``wait_s`` 解析成秒数。允许写模板（如 ``${options.image_wait_s}``）。

        解析不出来（配置项被清空、模板写错）时回落到工具声明里的默认值 ——
        **不能因为配置面板清了一格就把后台化整个关掉**，那等于把「占着对话
        连接」的老毛病又放回来。
        """
        raw = background.wait_s
        if isinstance(raw, str):
            try:
                resolved = render(raw, {}, self._options)
            except TemplateError:
                return self._fallback_wait_s(background)
            if resolved is MISSING:
                return self._fallback_wait_s(background)
            raw = resolved
        if isinstance(raw, str) and not raw.strip():
            return self._fallback_wait_s(background)
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return self._fallback_wait_s(background)

    def _fallback_wait_s(self, background: Any) -> float:
        """兜底：取工具声明里那个 option 的默认值。"""
        key = str(getattr(background, "option_key", "") or "").strip()
        if not key:
            return 0.0
        raw = self._spec.defaults.get(key)
        if raw is None:
            return 0.0
        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return 0.0

    def _pending_text(self, job: Any, background: Any) -> str:
        """转后台时给模型的话 —— 说清三件事：还在跑、去哪看、结果落在哪。"""
        title = background.title or self._name
        target = self._render_text(
            str(self._spec.result.get("dir", "") or ""), self._options
        )
        lines = [f"{title} 还在后台进行中。"]
        if job is not None:
            lines.append(
                f"它是作业 {job.id}；用 job_output 读该作业的最终结果"
                f"（读一次即可，结束后仍会返回）。"
            )
        if target:
            lines.append(f"生成结果会保存到 {target}。")
        lines.append("这一轮不必等它 —— 先把能做的事做完，需要时再取结果。")
        return "\n".join(lines)

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
            detail = self._error_detail(response)
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

    def _error_detail(self, response: httpx.Response) -> str:
        """把厂商的错误体压成一行人能看的话。

        MiniMax 的错误体是 ``{"type": "error", "error": {"type", "message",
        "http_code"}}`` —— 直接回显整段 JSON，模型得自己从里面挑出那句话。
        """
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError):
            return self._redact(response.text[:500])
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                kind = str(error.get("type", "")).strip()
                message = str(error.get("message", "")).strip()
                detail = " — ".join(part for part in (kind, message) if part)
                if detail:
                    return self._redact(detail)
        return self._redact(response.text[:500])

    # ------------------------------------------------------------------
    # 入参映射
    # ------------------------------------------------------------------

    async def _apply_inputs(self, args: dict[str, Any], context: Any) -> dict[str, Any]:
        """渲染请求体**之前**先把本地文件变成 API 认的形式。

        模板层刻意不提供 ``${file:...}`` —— 那等于给清单开一个任意文件读的
        口子。需要读文件的工具用这里这个显式声明的映射器，读哪个入参、
        读多大、走不走沙箱，全部写在清单里。

        ``max_count`` 是**厂商**的限制，不是本地取舍：清单里写死多少就以
        厂商为准（MiniMax 的图片编辑收 1~16 张）。本地提前拦下来，模型
        拿到的是一句"怎么改"的提示，而不是对方一个语焉不详的 2013。

        ``object_key`` 决定每条参考图在请求体里的形状：

        - 有值 → ``{"image_url": "<url>"}``（OpenAI 风格，H3-Image 编辑接口）
        - 无值 → ``{"type": ..., "image_file": ...}``（旧式主体参考）
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
        max_total_bytes = int(spec.get("max_total_bytes", 0)) or None
        object_key = str(spec.get("object_key", "")).strip()
        subject_type = str(args.get(spec.get("type_arg", "subject_type")) or "character")

        if len(items) > max_count:
            raise InputError(
                f"at most {max_count} reference image(s) are accepted, got {len(items)}"
            )

        refs: list[Any] = []
        total = 0
        for item in items:
            reference, size = await self._as_image_reference(item, context, max_bytes)
            total += size
            if max_total_bytes is not None and total > max_total_bytes:
                raise InputError(
                    f"reference images total {total // (1024 * 1024)}MB, "
                    f"over the {max_total_bytes // (1024 * 1024)}MB limit"
                )
            if object_key:
                refs.append({object_key: reference})
            else:
                refs.append({"type": subject_type, "image_file": reference})
        return {**args, arg_name: refs}

    async def _as_image_reference(
        self, item: str, context: Any, max_bytes: int
    ) -> tuple[str, int]:
        """把一条参考图入参变成公网 URL / data URL。

        返回 ``(引用, 本地字节数)`` —— 字节数只对本地文件有意义（远端 URL
        拿不到也管不着），用来累加总大小。
        """
        value = str(item).strip()
        if not value:
            raise InputError("empty reference image")
        if value.startswith("data:image/") or value.startswith(("http://", "https://")):
            return value, 0

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
                f"reference image '{value}' is not a JPG, PNG, WEBP or HEIC file"
            )
        return f"data:{mime};base64,{base64.b64encode(data).decode()}", len(data)

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

        urls, blobs, failures = self._collect_images(payload)
        if not urls and not blobs:
            if failures:
                return error_result(
                    self._name, "No image was generated:\n" + "\n".join(failures)
                )
            return ok_result(self._name, "The service returned no images.")

        stamp = time.strftime("%Y%m%d-%H%M%S")
        written: list[str] = []
        try:
            for index, blob in enumerate(blobs):
                data = base64.b64decode(blob)
                written.append(
                    _write(base_dir, f"{stamp}-{index}{_sniff_suffix(data)}", data)
                )
            for offset, url in enumerate(urls):
                index = len(blobs) + offset
                suffix, data = await self._download(url)
                written.append(_write(base_dir, f"{stamp}-{index}{suffix}", data))
        except (OSError, ValueError, httpx.HTTPError) as exc:
            return error_result(self._name, self._redact(f"Failed to save images: {exc}"))

        lines = [f"Saved {len(written)} image(s) to {base_dir}:"]
        lines.extend(f"- {path}" for path in written)
        if failures:
            # 部分失败是常态（一张没过内容安全检查，其余照常出图）——
            # 藏起来等于骗模型说"都好了"。
            lines.append("")
            lines.append(f"{len(failures)} image(s) failed:")
            lines.extend(f"- {item}" for item in failures)
        result = ok_result(self._name, "\n".join(lines))
        result.ui = {"kind": "image", "images": [{"path": path} for path in written]}
        return result

    def _collect_images(
        self, payload: dict[str, Any]
    ) -> tuple[list[str], list[str], list[str]]:
        """从响应里挑出图片链接、base64 和失败原因。

        两种响应形状：

        - ``items_path`` —— OpenAI 风格的数组，每项一张图，成功给
          ``url`` 或 ``b64_json``，失败给 ``error``。
        - ``urls_path`` / ``base64_path`` —— 旧的扁平数组。
        """
        spec = self._spec.result
        urls: list[str] = []
        blobs: list[str] = []
        failures: list[str] = []

        items_path = str(spec.get("items_path", "")).strip()
        if items_path:
            items = _dig(payload, items_path)
            items = items if isinstance(items, list) else []
            url_key = str(spec.get("url_key", "url")) or "url"
            b64_key = str(spec.get("base64_key", "b64_json")) or "b64_json"
            for index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                error = item.get("error")
                if isinstance(error, dict):
                    failures.append(_describe_failure(index, error))
                    continue
                url = item.get(url_key)
                if isinstance(url, str) and url:
                    urls.append(url)
                blob = item.get(b64_key)
                if isinstance(blob, str) and blob:
                    blobs.append(blob)
            return urls, blobs, failures

        raw_urls = _dig(payload, str(spec.get("urls_path", "")))
        raw_blobs = _dig(payload, str(spec.get("base64_path", "")))
        if isinstance(raw_urls, list):
            urls = [str(u) for u in raw_urls if u]
        if isinstance(raw_blobs, list):
            blobs = [str(b) for b in raw_blobs if b]
        return urls, blobs, failures

    async def _download(self, url: str) -> tuple[str, bytes]:
        if is_url_blocked(url, skip_dns=http_proxy_enabled()):
            raise ValueError(f"image url {url} is blocked by the SSRF guard")
        async with self._client_factory(self._spec.timeout_s) as client:
            response = await client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            data = response.content
            # content-type 说不出所以然（或干脆没给）时，别凭空造一个后缀 ——
            # 手里已经有字节，直接嗅探比信一个缺失的头更可靠。
            suffix = _guess_suffix(response.headers.get("content-type"))
            if suffix == ".png" and not data.startswith(_PNG_MAGIC):
                suffix = _sniff_suffix(data)
            return suffix, data

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
    """翻译厂商返回的业务错误码。"""
    # OpenAI 风格错误体：{"type": "error", "error": {"type", "message", ...}}
    if str(payload.get("type", "")) == "error":
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", "")).strip() or "unknown error"
            kind = str(error.get("type", "")).strip()
            return f"API error: {kind}: {message}" if kind else f"API error: {message}"

    # 旧版 MiniMax 体：{"base_resp": {"status_code": N, "status_msg": ...}}
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


def _describe_failure(index: int, error: dict[str, Any]) -> str:
    """把单张图片的失败原因压成一行。"""
    code = str(error.get("code", "")).strip()
    message = str(error.get("message", "")).strip()
    detail = " — ".join(part for part in (code, message) if part)
    return f"image {index + 1}: {detail}" if detail else f"image {index + 1}: failed"


# ---------------------------------------------------------------------------
# 后台作业适配
# ---------------------------------------------------------------------------


class _EmptyStream:
    """恒空的输出流（后台 HTTP 请求没有 stderr）。"""

    def read_from(self, offset: int = 0) -> OutputDelta:
        return OutputDelta(
            text="",
            next_offset=0,
            start_offset=offset,
            lossy=False,
            spill_path=None,
            truncated=False,
        )


class _MemoryStreams:
    """作业注册表要的「两条流」：stdout 是真的，stderr 恒空。"""

    def __init__(self, stdout: _BackgroundProcess) -> None:
        self.stdout = stdout
        self.stderr = _EmptyStream()

    def deltas(self) -> tuple[OutputDelta, OutputDelta]:
        return (self.stdout.read_from(0), _EmptyStream().read_from(0))


class _BackgroundProcess:
    """把一个 HTTP 请求伪装成「进程」，好让作业注册表与 job_output 认得它。

    作业注册表与 ``job_output`` 都按「进程 + stdout/stderr 两条流」取数据；HTTP
    请求没有进程，于是用一段内存文本冒充 stdout —— 模型轮询 ``job_output`` 时
    读到的就是这次请求的最终结果。
    """

    def __init__(self) -> None:
        self._text = ""
        self._task: asyncio.Task[Any] | None = None
        self.killed = False

    def attach(self, task: asyncio.Task[Any]) -> None:
        self._task = task

    @property
    def streams(self) -> _MemoryStreams:
        return _MemoryStreams(self)

    def write(self, text: str) -> None:
        self._text = text

    def read_from(self, offset: int = 0) -> OutputDelta:
        data = self._text.encode("utf-8")
        chunk = data[offset:].decode("utf-8", errors="replace")
        return OutputDelta(
            text=chunk,
            next_offset=len(data),
            start_offset=offset,
            lossy=False,
            spill_path=None,
            truncated=False,
        )

    async def kill(self) -> None:
        """中止后台请求 —— 用户在任务板上按停止，或这一轮被中断。"""
        self.killed = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()

    def watch_abort(self, signal: Any) -> asyncio.Task[None]:
        async def _watch() -> None:
            await signal.wait()
            await self.kill()

        return asyncio.create_task(_watch())


def _job_registry(context: Any) -> Any:
    """取会话的作业注册表。没有就返回 None —— 登记不了也不影响主流程。"""
    registry = getattr(context, "shell_jobs", None)
    if registry is not None:
        return registry
    try:
        from nono_agent.jobs.board import registry_for
    except Exception:  # pragma: no cover —— 作业板不可用不该拖垮工具
        return None
    session_id = str(getattr(context, "session_id", "") or "")
    return registry_for(session_id or None)


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
