"""Rules 数据模型 —— 常驻指令清单类型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Rule:
    """单条常驻指令。

    来自文件（.nono-agent/rules/*.md、AGENTS.md、CLAUDE.md、用户全局）或代码注入。
    """

    name: str                       # 唯一标识（通常为文件名或标题）
    content: str                    # 指令文本（去除 frontmatter）
    source: str                     # 来源路径或 "builtin"/"user-global"
    scope: str = "project"          # user | project | path
    globs: list[str] = field(default_factory=list)  # 条件启用 globs（空=常驻）
    priority: int = 0               # 数值越大越靠前/越优先

    def applies_to(self, paths: list[str] | None) -> bool:
        """判断该规则是否适用于当前路径集合。

        无 globs 时始终适用；有 globs 时任一路径命中即适用。
        """
        if not self.globs:
            return True
        if not paths:
            return False
        import fnmatch

        for path in paths:
            for pattern in self.globs:
                if fnmatch.fnmatch(path.replace("\\", "/"), pattern.replace("\\", "/")):
                    return True
        return False
