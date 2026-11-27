"""Rules 层级合并 —— 全局 < 项目 < 路径级，globs 条件启用。"""

from __future__ import annotations

from nono_agent.rules.models import Rule


def merge_rules(
    rules: list[Rule],
    active_paths: list[str] | None = None,
) -> str:
    """把规则列表合并为最终注入文本。

    排序：priority 降序（越高越靠前），同优先级保持稳定。
    过滤：有 globs 的规则仅在 active_paths 命中时启用。
    """
    applicable = [r for r in rules if r.applies_to(active_paths)]
    ordered = sorted(applicable, key=lambda r: -r.priority)

    parts: list[str] = []
    for rule in ordered:
        header = f"<!-- source: {rule.source} -->"
        parts.append(f"{header}\n{rule.content}")
    return "\n\n".join(parts)


def list_effective_rules(
    rules: list[Rule],
    active_paths: list[str] | None = None,
) -> list[Rule]:
    """返回生效的规则列表（用于 /rules 调试）。"""
    applicable = [r for r in rules if r.applies_to(active_paths)]
    return sorted(applicable, key=lambda r: -r.priority)
