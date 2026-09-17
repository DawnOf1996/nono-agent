"""Tests for the memory package (memory records + file/sqlite stores)."""

from nono_agent.memory import (
    FileSystemMemoryStore,
    Memory,
    MemoryRecord,
    MemoryStore,
    SQLiteMemoryStore,
)


class TestFileSystemMemoryStore:
    def test_add_load_delete(self, tmp_path):
        store = FileSystemMemoryStore(tmp_path / "memory")
        assert store.load() == []

        rec = store.add(MemoryRecord(name="pref", content="Use 4 spaces.", type="preference"))
        assert rec.memory_id.startswith("mem_")

        memories = store.load()
        assert len(memories) == 1
        assert memories[0].name == "pref"
        assert memories[0].type == "preference"
        assert "Use 4 spaces." in memories[0].content

        assert store.delete(rec.memory_id) is True
        assert store.delete(rec.memory_id) is False
        assert store.load() == []

    def test_empty_dir(self, tmp_path):
        store = FileSystemMemoryStore(tmp_path / "empty")
        assert store.load() == []

    def test_update_and_get(self, tmp_path):
        store = FileSystemMemoryStore(tmp_path / "memory")
        rec = store.add(MemoryRecord(content="old", importance=5.0))
        updated = store.update(rec.memory_id, content="new", importance=8.0)
        assert updated is not None
        assert updated.content == "new"
        assert updated.importance == 8.0
        assert store.get(rec.memory_id).content == "new"

    def test_list_candidates(self, tmp_path):
        store = FileSystemMemoryStore(tmp_path / "memory")
        store.add(MemoryRecord(content="a", status="candidate"))
        store.add(MemoryRecord(content="b", status="accepted"))
        assert len(store.list_candidates()) == 1
        assert store.list_candidates()[0].content == "a"


class TestSQLiteMemoryStore:
    def test_roundtrip(self, tmp_path):
        store = SQLiteMemoryStore(tmp_path / "m.db")
        rec = store.add(MemoryRecord(content="hello", importance=7.0))
        assert store.get(rec.memory_id).content == "hello"
        assert len(store.load()) == 1
        assert store.delete(rec.memory_id) is True
        assert store.load() == []
        store.close()

    def test_retire_and_accept(self, tmp_path):
        store = SQLiteMemoryStore(tmp_path / "m.db")
        rec = store.add(MemoryRecord(content="x", status="candidate"))
        assert store.accept(rec.memory_id) is True
        assert store.get(rec.memory_id).status == "accepted"
        assert store.retire(rec.memory_id) is True
        assert store.get(rec.memory_id).status == "retired"
        store.close()


class TestMemoryModel:
    def test_model(self):
        m = Memory(name="x", content="c", type="fact")
        assert m.name == "x"
        assert m.type == "fact"
        assert m.kind == "semantic"

    def test_envelope_identity(self):
        m = MemoryRecord(content="c", importance=9.0, keys=["a", "b"])
        m.ensure_identity()
        assert m.memory_id.startswith("mem_")
        assert m.created_at > 0
        assert m.valid_from == m.created_at
        assert m.is_active()

    def test_is_active_validity(self):
        import time

        expired = MemoryRecord(content="c", valid_to=time.time() - 10)
        assert not expired.is_active()

    def test_serialization_roundtrip(self):
        m = MemoryRecord(content="c", agent_id="npc-1", keys=["k"], embedding=[0.1, 0.2])
        m.ensure_identity()
        m2 = MemoryRecord.from_dict(m.to_dict())
        assert m2.content == "c"
        assert m2.agent_id == "npc-1"
        assert m2.keys == ["k"]
        assert m2.embedding == [0.1, 0.2]


def test_protocol_runtime_checkable():
    assert isinstance(FileSystemMemoryStore, type)
    assert issubclass(FileSystemMemoryStore, MemoryStore)
    assert issubclass(SQLiteMemoryStore, MemoryStore)
