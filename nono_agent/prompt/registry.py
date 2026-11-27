"""Prompt Section 注册表 —— 有序注册、可禁用。

允许同名 section 多次注册（来自不同优先级来源，如 default/custom/agent/override），
由 PromptAssembler 在组装时按优先级去重（override > agent > custom > default）。
"""

from __future__ import annotations

from collections.abc import Iterator

from nono_agent.prompt.section import PromptSection


class SectionRegistry:
    """有序的 PromptSection 注册表（允许同名，assembler 负责优先级合并）。"""

    def __init__(self) -> None:
        self._sections: list[PromptSection] = []
        self._disabled: set[str] = set()

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(
        self,
        section: PromptSection,
        *,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        """注册 section —— 支持 before/after 指定插入位置，默认追加到末尾。

        同名 section 允许重复注册（不同优先级来源），由 assembler 去重。
        """
        if before is not None:
            for i, s in enumerate(self._sections):
                if s.name == before:
                    self._sections.insert(i, section)
                    return
        elif after is not None:
            for i, s in enumerate(self._sections):
                if s.name == after:
                    self._sections.insert(i + 1, section)
                    return
        self._sections.append(section)

    def unregister(self, name: str) -> PromptSection | None:
        """移除首个同名 section；不存在返回 None。"""
        for i, s in enumerate(self._sections):
            if s.name == name:
                return self._sections.pop(i)
        return None

    def disable(self, name: str) -> None:
        """禁用所有同名 section（渲染时跳过）。"""
        self._disabled.add(name)

    def enable(self, name: str) -> None:
        """启用 section。"""
        self._disabled.discard(name)

    def is_disabled(self, name: str) -> bool:
        return name in self._disabled

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, name: str) -> PromptSection | None:
        """返回首个同名 section。"""
        for s in self._sections:
            if s.name == name:
                return s
        return None

    def names(self) -> list[str]:
        return [s.name for s in self._sections]

    def sections(self) -> list[PromptSection]:
        return list(self._sections)

    def __iter__(self) -> Iterator[PromptSection]:
        return iter(self._sections)

    def __len__(self) -> int:
        return len(self._sections)

    def __contains__(self, name: str) -> bool:
        return any(s.name == name for s in self._sections)
