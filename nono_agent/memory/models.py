"""Memory 数据模型 —— 跨会话记忆（可治理的记忆记录）。

对照 ADPS 记忆模块白皮书，把单条记忆从「文件名 + 正文」升级为完整
envelope：身份、类型、作用域、来源、有效时间、版本关系、发布状态与召回条件。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

# kind 枚举（对齐 ADPS：working / episodic / semantic / procedural / meta；小镇场景增 persona）
KIND_WORKING = "working"
KIND_EPISODIC = "episodic"
KIND_SEMANTIC = "semantic"
KIND_PROCEDURAL = "procedural"
KIND_META = "meta"
KIND_PERSONA = "persona"
VALID_KINDS = {
    KIND_WORKING,
    KIND_EPISODIC,
    KIND_SEMANTIC,
    KIND_PROCEDURAL,
    KIND_META,
    KIND_PERSONA,
}

# trust.status 枚举
STATUS_CANDIDATE = "candidate"
STATUS_ACCEPTED = "accepted"
STATUS_REJECTED = "rejected"
STATUS_RETIRED = "retired"
VALID_STATUSES = {STATUS_CANDIDATE, STATUS_ACCEPTED, STATUS_REJECTED, STATUS_RETIRED}

# risk 枚举
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"
VALID_RISKS = {RISK_LOW, RISK_MEDIUM, RISK_HIGH}


def _new_memory_id() -> str:
    """生成稳定唯一 memory_id。"""
    return f"mem_{uuid.uuid4().hex[:12]}"


def _now() -> float:
    """当前 epoch 秒。"""
    return time.time()


@dataclass
class MemoryRecord:
    """单条记忆记录（envelope）。

    ``content`` 是正文/结论（用于注入与检索），其余字段是元数据，决定系统
    能否稳定处理冲突、权限、回滚与删除。
    """

    # —— 核心身份 ——
    memory_id: str = ""                       # 稳定唯一 id；add() 时自动生成
    name: str = ""                            # 可选人类可读标签（历史兼容）
    content: str = ""                         # 正文
    kind: str = KIND_SEMANTIC                 # working/episodic/semantic/procedural/meta/persona
    type: str | None = None                   # 历史兼容的类型约束（如 "preference"/"fact"）
    source: str = ""                          # 历史兼容的来源路径

    # —— 作用域 ——
    agent_id: str | None = None               # 所属 NPC / 用户（None=全局）
    session_id: str | None = None             # 来源会话
    project_id: str | None = None             # 项目/租户

    # —— 来源引用 ——
    source_ref: str | None = None             # 原文指针：trace://session/<sid>/seq/<n>

    # —— 有效时间与版本关系 ——
    valid_from: float | None = None           # 生效时间
    valid_to: float | None = None             # 失效时间（None=永久）
    supersedes: str | None = None             # 被本记忆取代的 memory_id
    superseded_by: str | None = None          # 取代本记忆的 memory_id

    # —— 发布状态 ——
    status: str = STATUS_ACCEPTED             # candidate/accepted/rejected/retired
    reviewed_by: str = "policy://memory-admission"  # 准入者（policy/model/human）

    # —— 检索打分与统计 ——
    importance: float = 5.0                   # 1–10；写入时评估
    created_at: float = 0.0                   # 创建时间（epoch 秒）
    last_access_at: float = 0.0               # 最近取回时间
    access_count: int = 0                     # 取回次数

    # —— 召回条件 ——
    keys: list[str] = field(default_factory=list)     # 关键词/实体标签
    embedding: list[float] | None = None      # P1 语义检索向量
    risk: str = RISK_LOW                      # low/medium/high

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """转为 JSON 兼容字典（embedding 保留原样）。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryRecord:
        """从字典构造；忽略未知字段以保持前向兼容。"""
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in data.items() if k in known})

    # ------------------------------------------------------------------
    # 判定辅助
    # ------------------------------------------------------------------

    def is_active(self, now: float | None = None) -> bool:
        """是否「当前有效且已发布」—— 用于检索过滤。"""
        if self.status != STATUS_ACCEPTED:
            return False
        now = _now() if now is None else now
        if self.valid_from is not None and now < self.valid_from:
            return False
        if self.valid_to is not None and now > self.valid_to:
            return False
        return True

    def ensure_identity(self) -> None:
        """补齐 identity 字段（add 前调用）。"""
        if not self.memory_id:
            self.memory_id = _new_memory_id()
        if not self.created_at:
            self.created_at = _now()
        if self.valid_from is None:
            self.valid_from = self.created_at
        if self.kind not in VALID_KINDS:
            self.kind = KIND_SEMANTIC
        if self.status not in VALID_STATUSES:
            self.status = STATUS_CANDIDATE
        if self.risk not in VALID_RISKS:
            self.risk = RISK_LOW


# 历史兼容别名：早期代码使用 ``Memory``。
Memory = MemoryRecord

__all__ = [
    "KIND_EPISODIC",
    "KIND_META",
    "KIND_PERSONA",
    "KIND_PROCEDURAL",
    "KIND_SEMANTIC",
    "KIND_WORKING",
    "Memory",
    "MemoryRecord",
    "RISK_HIGH",
    "RISK_LOW",
    "RISK_MEDIUM",
    "STATUS_ACCEPTED",
    "STATUS_CANDIDATE",
    "STATUS_REJECTED",
    "STATUS_RETIRED",
]
