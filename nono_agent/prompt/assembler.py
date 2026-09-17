"""Prompt 组装器 —— 优先级合并 + 静态/动态边界。

优先级合并（同名 section）：
    override > agent > custom > default

即优先级数值更高者胜出；``append_*`` 段永远追加。

静态/动态边界：
    静态段（identity、工具规范等）形成可缓存前缀；
    动态段（env/git、Rules、Memory 等）位于边界之后。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nono_agent.prompt.registry import SectionRegistry
from nono_agent.prompt.section import PromptContext, PromptSection

# 静态/动态边界标记（对标 SYSTEM_PROMPT_DYNAMIC_BOUNDARY）
DYNAMIC_BOUNDARY = "\n\n<!-- NONO_AGENT_DYNAMIC_BOUNDARY -->"


@dataclass
class AssembledPrompt:
    """组装结果 —— 拆分静态前缀与动态后缀。"""

    static_prefix: str = ""          # 边界之前的静态段（可缓存）
    dynamic_suffix: str = ""         # 边界之后的动态段
    full: str = ""                   # 完整系统提示
    section_names: list[str] = field(default_factory=list)  # 实际渲染的 section

    @property
    def boundary_index(self) -> int:
        """静态/动态边界在 full 中的字符索引（无动态段时为 -1）。"""
        if not self.dynamic_suffix:
            return -1
        return len(self.static_prefix)


class PromptAssembler:
    """把注册表里的 section 组装为最终系统提示。"""

    def __init__(self, registry: SectionRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> SectionRegistry:
        return self._registry

    def assemble(self, ctx: PromptContext) -> AssembledPrompt:
        """按顺序渲染所有启用的 section，并切分静态/动态边界。"""
        static_parts: list[str] = []
        dynamic_parts: list[str] = []
        rendered_names: list[str] = []

        for section in self._ordered_sections():
            if self._registry.is_disabled(section.name):
                continue
            try:
                text = section.render(ctx)
            except Exception:
                # 单个 section 渲染失败不中断整体组装
                continue
            if text is None or not text.strip():
                continue
            if section.is_dynamic:
                dynamic_parts.append(text)
            else:
                static_parts.append(text)
            rendered_names.append(section.name)

        static_prefix = "\n\n".join(static_parts)
        dynamic_suffix = "\n\n".join(dynamic_parts)

        full_parts: list[str] = []
        if static_prefix:
            full_parts.append(static_prefix)
        if dynamic_suffix:
            full_parts.append(DYNAMIC_BOUNDARY + "\n" + dynamic_suffix)

        return AssembledPrompt(
            static_prefix=static_prefix,
            dynamic_suffix=dynamic_suffix,
            full="\n\n".join(full_parts),
            section_names=rendered_names,
        )

    def _ordered_sections(self) -> list[PromptSection]:
        """返回按「注册顺序 + 优先级去重」排序的 section 列表。

        同名 section 只保留优先级最高者。
        """
        by_name: dict[str, PromptSection] = {}
        for section in self._registry.sections():
            existing = by_name.get(section.name)
            if existing is None or section.priority > existing.priority:
                by_name[section.name] = section
        return list(by_name.values())
