"""声明式模板渲染 —— ``${args.x}`` / ``${options.y}``。

模板是工具声明与「用户配置 / 模型入参」之间的唯一接口，两边都不需要知道
对方的结构：

- ``${args.<name>}`` 取模型传进来的工具入参；缺省时**整个字段被丢弃**，
  而不是渲染成 ``null`` —— 请求体里多一个 ``null`` 常常就是 400。
- ``${options.<key>}`` 取用户在 settings ``tools.<id>.options`` 里配的值，
  值域来自插件声明的 ``defaults``。

刻意不支持表达式、函数调用与条件分支：能算的东西越少，清单里能藏的
东西就越少。
"""

from __future__ import annotations

import re
from typing import Any, Final

_TEMPLATE: Final[re.Pattern[str]] = re.compile(r"\$\{([a-zA-Z_][A-Za-z0-9_.]*)\}")


class TemplateError(ValueError):
    """模板无法解析（通常是引用了不存在的命名空间）。"""


class _Missing:
    """渲染结果缺失的哨兵。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<MISSING>"


MISSING: Final[_Missing] = _Missing()


def _lookup(path: str, args: dict[str, Any], options: dict[str, Any]) -> Any:
    namespace, _, key = path.partition(".")
    if not key:
        raise TemplateError(f"template {path!r} must be 'args.<name>' or 'options.<key>'")
    if namespace == "args":
        source: dict[str, Any] = args
    elif namespace == "options":
        source = options
    else:
        raise TemplateError(f"template {path!r} has unknown namespace {namespace!r}")
    value = source.get(key)
    return MISSING if value is None else value


def render(value: Any, args: dict[str, Any], options: dict[str, Any]) -> Any:
    """渲染模板；无法解析的字段返回 :data:`MISSING`（由调用方决定丢弃）。"""
    if isinstance(value, str):
        return _render_str(value, args, options)
    if isinstance(value, dict):
        rendered: dict[str, Any] = {}
        for key, item in value.items():
            result = render(item, args, options)
            if result is not MISSING:
                rendered[str(key)] = result
        return rendered
    if isinstance(value, list):
        items = [render(item, args, options) for item in value]
        return [item for item in items if item is not MISSING]
    return value


def _render_str(text: str, args: dict[str, Any], options: dict[str, Any]) -> Any:
    matches = list(_TEMPLATE.finditer(text))
    if not matches:
        return text

    # 整串就是一个引用时保留原始类型（int / bool / dict 都要原样送出）
    if len(matches) == 1 and matches[0].span() == (0, len(text)):
        return _lookup(matches[0].group(1), args, options)

    parts: list[str] = []
    cursor = 0
    for match in matches:
        resolved = _lookup(match.group(1), args, options)
        if resolved is MISSING:
            return MISSING
        parts.append(text[cursor : match.start()])
        parts.append(resolved if isinstance(resolved, str) else str(resolved))
        cursor = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def has_template(text: str) -> bool:
    """字符串是否含模板引用。"""
    return bool(_TEMPLATE.search(text))
