"""Tests for the memory package (memdir)."""

from nono_agent.memory import FileSystemMemoryStore, Memory, MemoryStore


class TestFileSystemMemoryStore:
    def test_add_load_delete(self, tmp_path):
        store = FileSystemMemoryStore(tmp_path / "memory")
        assert store.load() == []

        store.add("pref", "Use 4 spaces.", type_="preference")
        memories = store.load()
        assert len(memories) == 1
        assert memories[0].name == "pref"
        assert memories[0].type == "preference"
        assert "Use 4 spaces." in memories[0].content

        assert store.delete("pref") is True
        assert store.delete("pref") is False
        assert store.load() == []

    def test_empty_dir(self, tmp_path):
        store = FileSystemMemoryStore(tmp_path / "empty")
        assert store.load() == []


class TestMemoryModel:
    def test_model(self):
        m = Memory(name="x", content="c", type="fact")
        assert m.name == "x"
        assert m.type == "fact"


def test_protocol_runtime_checkable():
    assert isinstance(FileSystemMemoryStore, type)
    assert issubclass(FileSystemMemoryStore, MemoryStore)
