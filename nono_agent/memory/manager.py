"""MemoryManager —— 记忆的准入、检索、反思编排。

在 MemoryStore（持久化）之上提供策略：
- 准入（importance 门槛 + 版本 supersedes）
- 检索打分（recency × importance × relevance，对齐 Generative Agents）
- 反思（把近期情景记忆归纳为高层 meta 记忆，复用可注入的 summarizer）
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from nono_agent.memory.models import (
    KIND_EPISODIC,
    KIND_META,
    STATUS_ACCEPTED,
    STATUS_REJECTED,
    STATUS_RETIRED,
    MemoryRecord,
)
from nono_agent.memory.store import MemoryStore

# 反思/摘要函数签名：([{...message dicts}], model_id) -> summary 文本
Summarizer = Callable[[list[dict[str, Any]], str], Awaitable[str]]

# 检索观察者签名：(query, 命中记录) -> None
RetrieveObserver = Callable[[str, list["MemoryRecord"]], None]

_ObserverT = TypeVar("_ObserverT")


def _register(
    observers: list[_ObserverT], observer: _ObserverT
) -> Callable[[], None]:
    """把观察者挂上并返回注销函数。"""
    observers.append(observer)

    def dispose() -> None:
        if observer in observers:
            observers.remove(observer)

    return dispose

_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    """轻量分词：英文单词 + CJK 单字（P0 启发式，无外部依赖）。"""
    words = set(_WORD_RE.findall(text.lower()))
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff":
            words.add(ch)
    return words


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.5  # 中性
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _estimate_tokens(text: str) -> int:
    """粗略 token 估算（无模型依赖）。"""
    return max(1, len(text) // 4)


class MemoryManager:
    """编排记忆的准入 / 检索 / 反思。"""

    def __init__(
        self,
        store: MemoryStore,
        *,
        alpha: float = 0.3,
        beta: float = 0.4,
        gamma: float = 0.3,
        recency_lambda: float = 86400.0,   # 半天默认（秒）
        default_k: int = 10,
        default_token_budget: int = 2000,
        min_importance: float = 3.0,
    ) -> None:
        self._store = store
        self._alpha = alpha
        self._beta = beta
        self._gamma = gamma
        self._recency_lambda = recency_lambda
        self._default_k = default_k
        self._default_token_budget = default_token_budget
        self._min_importance = min_importance
        self._write_observers: list[Callable[[MemoryRecord], None]] = []
        self._retrieve_observers: list[RetrieveObserver] = []

    @property
    def store(self) -> MemoryStore:
        return self._store

    # ------------------------------------------------------------------
    # 观察者 —— 供会话事件流记录 memory/write 与 memory/retrieve
    # ------------------------------------------------------------------

    def add_write_observer(
        self, observer: Callable[[MemoryRecord], None]
    ) -> Callable[[], None]:
        """注册写入回调，每次准入结束后（含 rejected）同步触发。

        Args:
            observer: 接收落库后 MemoryRecord 的回调。

        Returns:
            注销该回调的函数。
        """
        return _register(self._write_observers, observer)

    def add_retrieve_observer(
        self, observer: RetrieveObserver
    ) -> Callable[[], None]:
        """注册检索回调，每次 ``retrieve()`` 返回前同步触发。

        Args:
            observer: 接收 (query, 命中记录列表) 的回调。

        Returns:
            注销该回调的函数。
        """
        return _register(self._retrieve_observers, observer)

    # ------------------------------------------------------------------
    # 准入
    # ------------------------------------------------------------------

    def admit(self, record: MemoryRecord) -> MemoryRecord:
        """准入：重要性门槛 + 版本 supersedes + 落库。"""
        record.ensure_identity()
        if record.status != STATUS_ACCEPTED or record.importance < self._min_importance:
            record.status = STATUS_REJECTED
        else:
            record.status = STATUS_ACCEPTED

        # 版本链：supersedes 指向的旧记忆置 retired 并回填 superseded_by
        if record.supersedes:
            self._store.update(
                record.supersedes, status=STATUS_RETIRED, superseded_by=record.memory_id
            )
        stored = self._store.add(record)
        for observer in self._write_observers:
            observer(stored)
        return stored

    def add_observation(
        self,
        content: str,
        *,
        agent_id: str | None = None,
        importance: float = 5.0,
        kind: str = KIND_EPISODIC,
        keys: list[str] | None = None,
        session_id: str | None = None,
        source_ref: str | None = None,
        source_type: str = "observation",
    ) -> MemoryRecord:
        """把一条观察/事件写入候选 → 准入 → 落库。"""
        record = MemoryRecord(
            content=content,
            kind=kind,
            agent_id=agent_id,
            importance=importance,
            keys=keys or [],
            session_id=session_id,
            source_ref=source_ref,
            source=source_type,
            status=STATUS_ACCEPTED,
        )
        return self.admit(record)

    # ------------------------------------------------------------------
    # 检索
    # ------------------------------------------------------------------

    def retrieve(
        self,
        query: str = "",
        *,
        agent_id: str | None = None,
        k: int | None = None,
        token_budget: int | None = None,
        kinds: list[str] | None = None,
        now: float | None = None,
    ) -> list[MemoryRecord]:
        """检索打分取回 top-k（recency + importance + relevance）。"""
        now = time.time() if now is None else now
        k = self._default_k if k is None else k
        token_budget = self._default_token_budget if token_budget is None else token_budget
        query_tokens = _tokens(query)

        scored: list[tuple[float, MemoryRecord]] = []
        for m in self._store.load():
            if not m.is_active(now):
                continue
            if agent_id is not None and m.agent_id not in (None, agent_id):
                continue
            if kinds is not None and m.kind not in kinds:
                continue

            recency = (
                math.exp(-(now - m.created_at) / self._recency_lambda)
                if m.created_at
                else 0.0
            )
            importance = m.importance / 10.0
            relevance = _jaccard(query_tokens, _tokens(m.content) | set(m.keys))
            score = (
                self._alpha * recency
                + self._beta * importance
                + self._gamma * relevance
            )
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)

        result: list[MemoryRecord] = []
        used_tokens = 0
        for _, m in scored:
            if len(result) >= k:
                break
            cost = _estimate_tokens(m.content)
            if result and used_tokens + cost > token_budget:
                break
            result.append(m)
            used_tokens += cost

        for observer in self._retrieve_observers:
            observer(query, result)
        return result

    # ------------------------------------------------------------------
    # 反思
    # ------------------------------------------------------------------

    async def reflect(
        self,
        *,
        agent_id: str | None = None,
        summarizer: Summarizer | None = None,
        model_id: str = "",
        limit: int = 20,
    ) -> list[MemoryRecord]:
        """反思：把近期情景记忆归纳为高层 meta 记忆并回写。

        Args:
            summarizer: 可注入的摘要函数；为 None 时不做任何归纳（返回 []）。
        """
        if summarizer is None:
            return []

        candidates = [
            m for m in self._store.load()
            if m.kind == KIND_EPISODIC and m.is_active()
            and (agent_id is None or m.agent_id == agent_id)
        ]
        candidates.sort(key=lambda m: m.created_at)
        recent = candidates[-limit:]
        if not recent:
            return []

        messages = [
            {"role": "user", "content": m.content, "metadata": {"memory_id": m.memory_id}}
            for m in recent
        ]
        try:
            summary = await summarizer(messages, model_id)
        except Exception:
            return []
        summary = (summary or "").strip()
        if not summary:
            return []

        record = MemoryRecord(
            content=summary,
            kind=KIND_META,
            agent_id=agent_id,
            importance=8.0,  # 反思产物重要性高
            keys=["reflection"],
            source="reflection",
            status=STATUS_ACCEPTED,
        )
        return [self.admit(record)]


__all__ = ["MemoryManager", "RetrieveObserver", "Summarizer"]
