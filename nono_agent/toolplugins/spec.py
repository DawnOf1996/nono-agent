"""工具插件声明模型 —— 把 plugin.json 的 ``tools`` 段解析成强类型规格。

清单里允许三种声明：

- ``http_json``：一个 HTTP JSON 端点。请求体是模板，模板里可以引用
  ``${args.<name>}``（模型传进来的参数）与 ``${options.<key>}``（用户在
  settings 里配的值）。
- ``mcp``：启用本插件 ``mcp`` 段里已声明的 server，并按需注入密钥、
  过滤要暴露的工具。
- ``builtin``：把引擎里已写好的内置工具挂到这个插件的开关下。

三种声明都是**纯数据**：这里不 import 任何插件代码，也不解析可执行路径。
``builtin`` 尤其如此 —— 它只按名字去 :data:`nono_agent.tools.builtin.
PLUGIN_MANAGED_TOOL_FACTORIES` 里那张白名单表取工厂，清单点名不到别的。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: ``${options.<key>}`` —— 后台等待秒数允许写成模板，好让配置面板能调。
_OPTION_TEMPLATE = re.compile(r"\$\{options\.([a-zA-Z_][A-Za-z0-9_.]*)\}")


class ToolSpecError(ValueError):
    """工具声明不合法。"""


@dataclass(frozen=True)
class AuthSpec:
    """密钥注入方式。

    ``api_key_env`` 指向 ``~/.nono-agent/credentials`` 里的键名（或同名环境
    变量），**不是**密钥本身 —— 清单和 settings 都不落明文。
    """

    type: str = "none"          # none | bearer | header | query
    api_key_env: str = ""
    header: str = "Authorization"
    prefix: str = "Bearer "     # type=bearer 时的前缀
    query_param: str = ""       # type=query 时的参数名

    @property
    def needs_api_key(self) -> bool:
        return self.type != "none" and bool(self.api_key_env)

    @classmethod
    def from_dict(cls, data: Any) -> AuthSpec:
        if not isinstance(data, dict):
            return cls()
        auth_type = str(data.get("type", "none")).strip().lower()
        if auth_type not in {"none", "bearer", "header", "query"}:
            raise ToolSpecError(f"unsupported auth type {auth_type!r}")
        return cls(
            type=auth_type,
            api_key_env=str(data.get("api_key_env", "")).strip(),
            header=str(data.get("header", "Authorization")).strip() or "Authorization",
            prefix=str(data.get("prefix", "Bearer ")),
            query_param=str(data.get("query_param", "")).strip(),
        )


@dataclass(frozen=True)
class BackgroundSpec:
    """后台化声明 —— 请求超过 ``wait_s`` 还没回来就转后台。

    **为什么需要它**：这类请求（图像生成最长 600 秒）会把**对话那一条连接**一直
    占着。浏览器对同一 origin 的并发连接有上限（Chromium 是 6），而应用本身
    常驻着好几条 SSE（control / browser / stats / fs-watch…）。名额占满之后，
    文件树这类普通请求只能排队 —— 表现就是「图片生成期间界面全卡住」。

    转后台后这一轮立刻放行，连接名额还给别的请求；请求本身**不取消**（厂商那边
    已经在生成并计费了，取消只是白花钱），而是登记成作业继续跑。
    """

    wait_s: Any = 0.0            # 数字，或 "${options.x}" 模板
    kind: str = "task"           # 作业类型（任务板上用来选标签）
    title: str = ""              # 作业标题
    option_key: str = ""         # wait_s 是模板时，模板里引用的 option 键

    @property
    def enabled(self) -> bool:
        return bool(self.wait_s)

    @classmethod
    def from_dict(cls, data: Any) -> BackgroundSpec:
        if not isinstance(data, dict):
            return cls()
        wait_s: Any = data.get("wait_s", 0.0)
        option_key = ""
        if isinstance(wait_s, str):
            # 模板：留到运行时按 options 解析（配置面板的数值项是字符串）
            wait_s = wait_s.strip()
            match = _OPTION_TEMPLATE.fullmatch(wait_s)
            if match is not None:
                option_key = match.group(1)
        else:
            try:
                wait_s = float(wait_s)
            except (TypeError, ValueError):
                wait_s = 0.0
            wait_s = max(0.0, wait_s)
        return cls(
            wait_s=wait_s,
            kind=str(data.get("kind", "task")).strip() or "task",
            title=str(data.get("title", "")).strip(),
            option_key=option_key,
        )


@dataclass(frozen=True)
class HttpToolSpec:
    """``kind: "http_json"`` —— 一个 HTTP JSON 端点即一个工具。"""

    plugin_id: str
    name: str                    # 插件内的短名
    prefix: str                  # 工具名前缀，最终名 = f"{prefix}__{name}"
    description: str
    input_schema: dict[str, Any]
    method: str
    url: str                     # 模板
    headers: dict[str, str]      # 值可以是模板
    body: Any | None             # 模板化请求体
    query: dict[str, str]        # 查询参数模板
    timeout_s: float
    inputs: dict[str, Any]          # 入参映射器（{"kind": "image_data_urls", ...}）
    result: dict[str, Any]       # 结果映射器（{"kind": "text"|"save_images", ...}）
    auth: AuthSpec
    defaults: dict[str, Any]     # options 的默认值
    concurrent_safe: bool
    write_side_effects: bool
    background: BackgroundSpec = field(default_factory=BackgroundSpec)

    @property
    def tool_name(self) -> str:
        return f"{self.prefix}__{self.name}" if self.prefix else self.name

    @property
    def api_key_env(self) -> str:
        return self.auth.api_key_env

    @property
    def result_kind(self) -> str:
        return str(self.result.get("kind", "text"))

    def required_args(self) -> list[str]:
        required = self.input_schema.get("required")
        if not isinstance(required, list):
            return []
        return [str(item) for item in required]


@dataclass(frozen=True)
class McpToolSpec:
    """``kind: "mcp"`` —— 启用一个已声明的 MCP server 并筛工具。"""

    plugin_id: str
    name: str
    server: str                  # 对应 manifest.mcp 的键
    description: str = ""
    allowed_tools: frozenset[str] = frozenset()  # 空 = 全部暴露
    auth: AuthSpec = field(default_factory=AuthSpec)
    defaults: dict[str, Any] = field(default_factory=dict)

    @property
    def tool_name(self) -> str:
        return f"mcp__{self.server}__{self.name}"

    @property
    def api_key_env(self) -> str:
        return self.auth.api_key_env


@dataclass(frozen=True)
class BuiltinToolSpec:
    """``kind: "builtin"`` —— 挂一个引擎内建工具，工具实现在 Python 里。

    用在「实现是我们的、但要不要给用户用得由他说了算」的能力上，比如内置浏览器
    控制：它不是第三方服务，不能塞进 http_json；但它背后是一条无认证的本机
    调试端口，默认必须关着，所以也不该无条件出现在工具表里。

    ``tool`` 是工具名，必须能在 ``PLUGIN_MANAGED_TOOL_FACTORIES`` 里查到。
    """

    plugin_id: str
    name: str                    # 插件内的短名，仅用于报错定位
    tool: str                    # 引擎内建工具的真实名字
    description: str = ""
    defaults: dict[str, Any] = field(default_factory=dict)
    auth: AuthSpec = field(default_factory=AuthSpec)

    @property
    def tool_name(self) -> str:
        return self.tool

    @property
    def api_key_env(self) -> str:
        return self.auth.api_key_env


ToolSpec = HttpToolSpec | McpToolSpec | BuiltinToolSpec


def parse_tool_specs(plugin_id: str, raw: dict[str, Any]) -> list[ToolSpec]:
    """把 ``tools`` 段解析成规格列表；任一条不合法即抛 :class:`ToolSpecError`。

    整插件失败而不是跳过单条：半截可用的工具声明比没有更容易让人误以为功能已就位。
    """
    specs: list[ToolSpec] = []
    for name in sorted(raw):
        data = raw[name]
        if not isinstance(data, dict):
            raise ToolSpecError(f"tools.{name} must be an object")
        kind = str(data.get("kind", "")).strip()
        defaults = data.get("defaults")
        defaults = dict(defaults) if isinstance(defaults, dict) else {}
        auth = AuthSpec.from_dict(data.get("auth"))

        if kind == "http_json":
            specs.append(_parse_http(plugin_id, name, data, defaults, auth))
        elif kind == "mcp":
            specs.append(_parse_mcp(plugin_id, name, data, defaults, auth))
        elif kind == "builtin":
            specs.append(_parse_builtin(plugin_id, name, data, defaults))
        else:
            raise ToolSpecError(f"tools.{name}.kind {kind!r} is not supported")
    return specs


def _parse_http(
    plugin_id: str,
    name: str,
    data: dict[str, Any],
    defaults: dict[str, Any],
    auth: AuthSpec,
) -> HttpToolSpec:
    url = str(data.get("url", "")).strip()
    if not url:
        raise ToolSpecError(f"tools.{name}.url is required for kind=http_json")
    method = str(data.get("method", "POST")).strip().upper()
    schema = data.get("input_schema")
    if schema is not None and not isinstance(schema, dict):
        raise ToolSpecError(f"tools.{name}.input_schema must be an object")
    headers = data.get("headers")
    query = data.get("query")
    inputs = data.get("inputs")
    result = data.get("result")
    try:
        timeout_s = float(data.get("timeout_s", 60.0))
    except (TypeError, ValueError) as exc:
        raise ToolSpecError(f"tools.{name}.timeout_s must be a number") from exc
    if timeout_s <= 0:
        raise ToolSpecError(f"tools.{name}.timeout_s must be positive")

    return HttpToolSpec(
        plugin_id=plugin_id,
        name=name,
        prefix=str(data.get("prefix", plugin_id)).strip().replace("-", "_"),
        description=str(data.get("description", "")),
        input_schema=schema
        if isinstance(schema, dict)
        else {"type": "object", "properties": {}},
        method=method,
        url=url,
        headers={str(k): str(v) for k, v in headers.items()}
        if isinstance(headers, dict)
        else {},
        body=data.get("body"),
        query={str(k): str(v) for k, v in query.items()} if isinstance(query, dict) else {},
        timeout_s=timeout_s,
        inputs=dict(inputs) if isinstance(inputs, dict) else {},
        result=dict(result) if isinstance(result, dict) else {"kind": "text"},
        auth=auth,
        defaults=defaults,
        concurrent_safe=bool(data.get("is_concurrent_safe", False)),
        write_side_effects=bool(data.get("has_write_side_effects", True)),
        background=BackgroundSpec.from_dict(data.get("background")),
    )


def _parse_mcp(
    plugin_id: str,
    name: str,
    data: dict[str, Any],
    defaults: dict[str, Any],
    auth: AuthSpec,
) -> McpToolSpec:
    server = str(data.get("server", "")).strip()
    if not server:
        raise ToolSpecError(f"tools.{name}.server is required for kind=mcp")
    allowed = data.get("allowed_tools")
    if allowed is None:
        allowed_set: frozenset[str] = frozenset()
    elif isinstance(allowed, list):
        allowed_set = frozenset(str(item).strip() for item in allowed if str(item).strip())
    else:
        raise ToolSpecError(f"tools.{name}.allowed_tools must be a list of strings")
    return McpToolSpec(
        plugin_id=plugin_id,
        name=name,
        server=server,
        description=str(data.get("description", "")),
        allowed_tools=allowed_set,
        auth=auth,
        defaults=defaults,
    )


def _parse_builtin(
    plugin_id: str,
    name: str,
    data: dict[str, Any],
    defaults: dict[str, Any],
) -> BuiltinToolSpec:
    tool = str(data.get("tool", "")).strip()
    if not tool:
        raise ToolSpecError(f"tools.{name}.tool is required for kind=builtin")
    return BuiltinToolSpec(
        plugin_id=plugin_id,
        name=name,
        tool=tool,
        description=str(data.get("description", "")),
        defaults=defaults,
    )
