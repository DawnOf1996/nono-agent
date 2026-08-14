"""Memory 数据模型 —— 跨会话记忆。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Memory:
    """单条记忆。"""

    name: str                       # 唯一标识（文件名，不含扩展）
    content: str                    # 记忆内容
    type: str | None = None         # 类型约束（如 "preference" / "fact"）
    source: str = ""                # 来源路径
