"""Prompt Section 协议与上下文 —— 对标 CC Prompt-Assembly-Pipeline。

系统提示被建模为「有序 section 列表」，而非单次字符串拼接。
每个 section 实现 ``render(ctx) -> str | None``，返回 None 表示跳过。

静态/动态边界：``is_dynamic=False`` 的 section 进入静态缓存前缀
（利于 prompt cache），``is_dynamic=True`` 的进入动态段。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

# 优先级常量 —— 数值越大越优先（同名覆盖时胜出）
DEFAULT_PRIORITY = 0
CUSTOM_PRIORITY = 100
AGENT_PRIORITY = 200
OVERRIDE_PRIORITY = 300


@dataclass
class PromptContext:
    """渲染 section 所需的上下文快照。"""

    model_id: str = ""
    cwd: str = "."
    env: dict[str, str] = field(default_factory=dict)
    rules_text: str = ""                       # 已合并的 Rules 文本
    skills_text: str = ""                      # 可用 Skills 清单文本
    memories_text: str = ""                    # 跨会话 Memory 文本
    custom_system_prompt: str | None = None    # 覆盖默认 identity
    append_system_prompt: str | None = None    # 永远追加
    output_style: str | None = None            # 输出风格（concise/detailed/...）
    tools: list[dict[str, Any]] = field(default_factory=list)
    permission_mode: str = "default"
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PromptSection(Protocol):
    """Prompt Section 协议 —— 每个 section 提供名字与渲染函数。"""

    @property
    def name(self) -> str:
        """唯一标识（用于去重 / 禁用 / 优先级合并）。"""
        ...

    @property
    def priority(self) -> int:
        """优先级 —— 同名 section 合并时更高者胜出。"""
        ...

    @property
    def is_dynamic(self) -> bool:
        """是否属于动态段（位于静态/动态边界之后）。"""
        ...

    def render(self, ctx: PromptContext) -> str | None:
        """渲染该 section 的文本；返回 None 表示跳过。"""
        ...


class PromptSectionBase:
    """PromptSection 便利基类 —— 提供默认优先级与静态属性。"""

    _name: str = ""
    _priority: int = DEFAULT_PRIORITY
    _is_dynamic: bool = False

    @property
    def name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def is_dynamic(self) -> bool:
        return self._is_dynamic

    def render(self, ctx: PromptContext) -> str | None:  # pragma: no cover - abstract
        raise NotImplementedError
