"""插件配置面板 schema —— 把 ``plugin.json`` 的 ``config`` 段解析成可渲染的字段。

**分工**：清单只声明「哪些键可以配、怎么渲染、叫什么」；值本身来自两处，
按 ``settings.tools.<id>.options`` ← 工具声明 ``defaults`` 的顺序回落。
所以默认值只在 ``tools.<name>.defaults`` 里写一遍，schema 不重复。

密钥是另一条路：它永远不进入 schema 的字段值，只有一个「往哪个凭据名里填」
的指针（``api_key.env``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 渲染器支持的控件类型。刻意只有这五种：每多一种，前端就要多一套分支，
# 而插件作者真正需要的区分无非是「一行文本 / 一个下拉 / 一个开关 / 一把钥匙」。
KNOWN_FIELD_TYPES = frozenset({"string", "number", "boolean", "enum", "password"})


class ConfigSchemaError(ValueError):
    """配置声明不合法。"""


@dataclass(frozen=True)
class ConfigField:
    """一个可配置项。"""

    key: str                    # 对应 options 里的键
    label: str
    type: str = "string"
    hint: str = ""
    placeholder: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # (value, label)，仅 enum 用
    secret: bool = False        # True 的值走凭据文件，不进 options

    def coerce(self, raw: Any) -> Any:
        """把用户提交的值收敛成该类型的值；非法则抛 :class:`ConfigSchemaError`。"""
        if self.type == "boolean":
            return bool(raw)
        if self.type == "number":
            try:
                return int(str(raw).strip())
            except (TypeError, ValueError) as exc:
                raise ConfigSchemaError(f"'{self.label}' 需要是一个整数") from exc
        text = str(raw if raw is not None else "").strip()
        if self.type == "enum" and text and text not in {v for v, _ in self.choices}:
            raise ConfigSchemaError(f"'{self.label}' 的取值不在候选列表里")
        return text


@dataclass(frozen=True)
class ConfigSchema:
    """插件的配置面板 schema。"""

    fields: tuple[ConfigField, ...] = field(default_factory=tuple)
    api_key_label: str = "API Key"
    api_key_hint: str = ""
    api_key_placeholder: str = ""

    @property
    def has_fields(self) -> bool:
        return bool(self.fields)


EMPTY_SCHEMA = ConfigSchema()


class _Unset:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<UNSET>"


UNSET: _Unset = _Unset()


def get_option(options: dict[str, Any], key: str, default: Any = None) -> Any:
    """按点分路径读 options（``server.transport`` → ``options["server"]["transport"]``）。

    支持点分路径是因为有些连接字段天生是嵌套的（MCP 的 transport / url / headers
    都挂在 ``server`` 下）。写成扁平键会存进去但运行时读不到 —— 面板上看着
    保存成功了，实际不生效。
    """
    node: Any = options
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


def set_option(options: dict[str, Any], key: str, value: Any) -> None:
    """按点分路径写 options，中间层不存在就建出来。"""
    parts = key.split(".")
    node = options
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def parse_config_schema(raw: Any) -> ConfigSchema:
    """解析 ``config`` 段；缺省或损坏时返回空 schema（面板退化成只有开关）。"""
    if not isinstance(raw, dict):
        return EMPTY_SCHEMA

    parsed: list[ConfigField] = []
    for entry in raw.get("fields", []) or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key", "")).strip()
        if not key:
            continue
        field_type = str(entry.get("type", "string")).strip().lower()
        if field_type not in KNOWN_FIELD_TYPES:
            raise ConfigSchemaError(
                f"config.fields.{key}.type {field_type!r} is not supported"
            )
        choices: list[tuple[str, str]] = []
        for choice in entry.get("choices", []) or []:
            if isinstance(choice, dict) and choice.get("value") is not None:
                choices.append(
                    (str(choice["value"]), str(choice.get("label", choice["value"])))
                )
            elif choice is not None:
                choices.append((str(choice), str(choice)))
        parsed.append(
            ConfigField(
                key=key,
                label=str(entry.get("label", key)),
                type=field_type,
                hint=str(entry.get("hint", "")),
                placeholder=str(entry.get("placeholder", "")),
                choices=tuple(choices),
                secret=bool(entry.get("secret", False)),
            )
        )

    key_block = raw.get("api_key")
    key_block = key_block if isinstance(key_block, dict) else {}
    return ConfigSchema(
        fields=tuple(parsed),
        api_key_label=str(key_block.get("label", "API Key")),
        api_key_hint=str(key_block.get("hint", "")),
        api_key_placeholder=str(key_block.get("placeholder", "")),
    )
