"""Skills 数据模型 —— 按需能力包清单类型。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Skill:
    """单个 Skill（一个 SKILL.md 定义的按需能力）。"""

    name: str                       # 唯一标识（目录名或 frontmatter name）
    description: str                # 一句话描述（注入 prompt 供模型选择）
    content: str                    # SKILL.md 正文（激活后注入的指令）
    source: str                     # 来源路径或 "builtin"/"user"/"project"
    scope: str = "project"          # user | project | plugin | bundled
    tools: list[str] = field(default_factory=list)  # 工具白名单（激活后允许）
    enabled: bool = True            # 是否启用
