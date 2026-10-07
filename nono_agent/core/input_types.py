"""模型输入模态（输入类型）的取值与归一化。

单独成模块是为了避开循环依赖：``providers.catalog`` 依赖 ``core.config``，
所以这层既不能住在 catalog 也不能住在 config 里。

配置项名：模型目录里的 ``input_types``，取值 ``text`` / ``image``。
省略时按 ``["text"]`` 处理 —— 目录没声明就不发图。
"""

from __future__ import annotations

INPUT_TYPE_TEXT = "text"
INPUT_TYPE_IMAGE = "image"

#: 全部合法输入类型，按稳定顺序声明（归一化输出按此序排列）。
INPUT_TYPES: tuple[str, ...] = (INPUT_TYPE_TEXT, INPUT_TYPE_IMAGE)

#: 展示用中文标签（CLI / 前端共用同一份，避免两处各写一套中文名）。
INPUT_TYPE_LABELS: dict[str, str] = {
    INPUT_TYPE_TEXT: "文本",
    INPUT_TYPE_IMAGE: "图片",
}


def normalize_input_types(value: object) -> list[str]:
    """归一化输入类型列表：去空白、去重、按声明序稳定排序。

    空值（``None`` / 空列表 / 空串）返回默认的 ``["text"]``；未知类型抛
    ``ValueError``，让配置错误在加载时就暴露，而不是等到发图才失败。
    """
    if value is None or value == "":
        return [INPUT_TYPE_TEXT]

    if isinstance(value, str):
        items: list[str] = [value]
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        raise ValueError(f"input_types must be a list, got {value!r}")

    seen: set[str] = set()
    for raw in items:
        name = raw.strip().lower()
        if not name:
            continue
        if name not in INPUT_TYPES:
            raise ValueError(
                f"Unknown input type '{raw}'. Supported: {list(INPUT_TYPES)}"
            )
        seen.add(name)
    if not seen:
        return [INPUT_TYPE_TEXT]
    return [item for item in INPUT_TYPES if item in seen]


def supports_input_type(input_types: object, input_type: str) -> bool:
    """``input_types`` 是否包含 ``input_type``。空配置按仅文本处理。"""
    try:
        normalized = normalize_input_types(input_types)
    except ValueError:
        return False
    return input_type in normalized
