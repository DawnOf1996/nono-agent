"""Tests for engine ↔ memory integration (retrieval-injection)."""

from nono_agent.core.config import EngineConfig
from nono_agent.core.engine import QueryEngine
from nono_agent.memory import MemoryManager, SQLiteMemoryStore


def test_engine_retrieves_memories_into_prompt(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "mem.db")
    manager = MemoryManager(store)
    manager.add_observation(
        "Isabella likes to write at the cafe", agent_id="isabella", importance=9.0, keys=["cafe"]
    )
    manager.add_observation(
        "unrelated weather memory", agent_id="other", importance=5.0, keys=["weather"]
    )

    config = EngineConfig(model="claude-test", agent_id="isabella")
    engine = QueryEngine(config, memory_store=store, memory_manager=manager)

    prompt = engine._build_system_prompt("cafe writing")
    assert "Isabella likes to write at the cafe" in prompt
    # 作用域隔离：其它 agent 的记忆不注入
    assert "unrelated weather memory" not in prompt


def test_engine_memory_manager_property(tmp_path):
    store = SQLiteMemoryStore(tmp_path / "mem.db")
    engine = QueryEngine(EngineConfig(model="claude-test"), memory_store=store)
    assert isinstance(engine.memory_manager, MemoryManager)
    assert engine.memory_store is store
