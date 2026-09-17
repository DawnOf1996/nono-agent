"""AgentDefinition —— 子 Agent 配置数据（对标 CC Subagent-Architecture）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentDefinition:
    """子 Agent 定义 —— tools 白名单、model、system prompt。

    来源：``built-in`` / ``user``（``~/.nono-agent/agents``）/ ``project``
    （``.nono-agent/agents``）/ ``plugin`` / ``flag``。
    """

    name: str                       # 唯一标识
    description: str                # 一句话描述（供父 Agent 选择）
    system_prompt: str              # 子 Agent 系统提示正文
    tools: list[str] = field(default_factory=list)  # 工具白名单（空=继承父全部）
    model: str | None = None        # 模型覆盖（None=继承父模型）
    source: str = "builtin"         # 来源
    allowed_isolations: list[str] = field(default_factory=lambda: ["none"])

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentDefinition:
        """从字典构建（用于 frontmatter/JSON 配置）。"""
        return cls(
            name=str(data["name"]),
            description=str(data.get("description", data["name"])),
            system_prompt=str(data.get("system_prompt", data.get("content", ""))),
            tools=(
                [str(t) for t in data.get("tools", [])]
                if isinstance(data.get("tools"), list)
                else []
            ),
            model=data.get("model"),
            source=str(data.get("source", "builtin")),
            allowed_isolations=list(data.get("allowed_isolations", ["none"])),
        )
