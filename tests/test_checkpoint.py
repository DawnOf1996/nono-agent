"""Tests for the checkpoint store (file snapshot/rewind)."""

import pytest

from nono_agent.checkpoint import CheckpointStore, FileCheckpointStore


class TestFileCheckpointStore:
    def test_snapshot_and_rewind(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("original\n")

        store = FileCheckpointStore(tmp_path)
        cid = store.snapshot(["a.txt"])

        f.write_text("modified\n")
        assert f.read_text() == "modified\n"

        store.rewind(cid)
        assert f.read_text() == "original\n"

    def test_rewind_restores_deleted(self, tmp_path):
        f = tmp_path / "b.txt"
        f.write_text("hello\n")

        store = FileCheckpointStore(tmp_path)
        cid = store.snapshot(["b.txt"])
        f.unlink()

        store.rewind(cid)
        assert f.read_text() == "hello\n"

    def test_rewind_removes_new_file(self, tmp_path):
        store = FileCheckpointStore(tmp_path)
        cid = store.snapshot(["new.txt"])  # 不存在
        (tmp_path / "new.txt").write_text("x")

        store.rewind(cid)
        assert not (tmp_path / "new.txt").exists()

    def test_list_and_drop(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        store = FileCheckpointStore(tmp_path)
        c1 = store.snapshot(["a.txt"])
        c2 = store.snapshot(["a.txt"])
        assert store.list_ids() == [c1, c2]
        assert store.drop(c1) is True
        assert store.drop(c1) is False

    def test_unknown_rewind_raises(self, tmp_path):
        store = FileCheckpointStore(tmp_path)
        with pytest.raises(KeyError):
            store.rewind("nope")


def test_protocol():
    assert issubclass(FileCheckpointStore, CheckpointStore)
