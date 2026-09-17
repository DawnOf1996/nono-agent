"""Tests for MemoryManager (admission / retrieval / reflection)."""

import pytest

from nono_agent.memory import (
    KIND_META,
    FileSystemMemoryStore,
    MemoryManager,
    MemoryRecord,
    SQLiteMemoryStore,
)


@pytest.fixture(params=["file", "sqlite"])
def manager(request, tmp_path):
    if request.param == "file":
        store = FileSystemMemoryStore(tmp_path / "mem")
    else:
        store = SQLiteMemoryStore(tmp_path / "mem.db")
    return MemoryManager(store)


class TestAdmission:
    def test_importance_gate_rejects_low(self, manager):
        rec = manager.add_observation("trivial", importance=1.0)
        assert rec.status == "rejected"

    def test_importance_gate_accepts(self, manager):
        rec = manager.add_observation("important", importance=7.0)
        assert rec.status == "accepted"
        assert rec.memory_id.startswith("mem_")

    def test_supersedes_retires_old(self, manager):
        old = manager.add_observation("old fact", importance=6.0)
        new = MemoryRecord(
            content="new fact", importance=6.0, status="accepted", supersedes=old.memory_id
        )
        manager.admit(new)
        assert manager.store.get(old.memory_id).status == "retired"
        assert manager.store.get(old.memory_id).superseded_by == new.memory_id


class TestRetrieval:
    def _seed(self, manager):
        manager.add_observation(
            "Isabella likes to write at the cafe in the morning",
            agent_id="isabella",
            importance=8.0,
            keys=["cafe", "writing"],
        )
        manager.add_observation(
            "Sam told me the cafe prices will go up next week",
            agent_id="isabella",
            importance=7.0,
            keys=["cafe", "prices"],
        )
        manager.add_observation(
            "unrelated memory about the weather",
            agent_id="other",
            importance=5.0,
            keys=["weather"],
        )

    def test_retrieval_filters_by_agent(self, manager):
        self._seed(manager)
        results = manager.retrieve("cafe", agent_id="isabella")
        assert all(r.agent_id == "isabella" for r in results)
        assert len(results) >= 2

    def test_relevance_ordering(self, manager):
        self._seed(manager)
        results = manager.retrieve("cafe writing", agent_id="isabella")
        assert results, "expected hits"
        assert results[0].content.startswith("Isabella likes to write")

    def test_top_k_cap(self, manager):
        self._seed(manager)
        results = manager.retrieve("", agent_id="isabella", k=1)
        assert len(results) == 1

    def test_token_budget_cap(self, manager):
        self._seed(manager)
        results = manager.retrieve("", token_budget=1)
        assert len(results) <= 1

    def test_kinds_filter(self, manager):
        self._seed(manager)
        results = manager.retrieve("", kinds=[KIND_META])
        assert results == []


class TestReflection:
    async def _fake_summarizer(self, messages, model_id):
        return "Isabella avoids conflict with Klaus"

    def test_reflect_requires_summarizer(self, manager):
        import asyncio

        assert asyncio.run(manager.reflect()) == []

    def test_reflect_produces_meta(self, manager):
        import asyncio

        manager.add_observation("argued with Klaus", agent_id="isabella", importance=6.0)
        manager.add_observation(
            "felt anxious after the argument", agent_id="isabella", importance=6.0
        )

        results = asyncio.run(
            manager.reflect(agent_id="isabella", summarizer=self._fake_summarizer, model_id="m")
        )
        assert len(results) == 1
        assert results[0].kind == KIND_META
        assert results[0].status == "accepted"
        assert "Klaus" in results[0].content

    def test_reflect_scope_isolation(self, manager):
        import asyncio

        manager.add_observation("only in other agent", agent_id="other", importance=6.0)

        results = asyncio.run(
            manager.reflect(agent_id="isabella", summarizer=self._fake_summarizer, model_id="m")
        )
        assert results == []  # 无 isabella 的情景记忆
