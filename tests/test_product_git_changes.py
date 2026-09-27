"""工作区文件栏的后端：文本对比、Git 视图、每轮变更记录与文件保存。"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_agent.core.types import AssistantMessage  # noqa: E402
from nono_agent.hooks.middleware import MiddlewareChain  # noqa: E402
from nono_agent.session.transcript import LocalFileTranscriptWriter  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.product import git as git_module  # noqa: E402
from nono_app_server.product.changes import WorkspaceChangesService  # noqa: E402
from nono_app_server.product.router import mount_product_routes  # noqa: E402
from nono_app_server.product.textdiff import (  # noqa: E402
    count_changes,
    diff_contents,
    diff_texts,
    parse_unified_diff,
)
from tests.helpers import MockTool, create_test_engine  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Nono Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "nono@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Nono Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "nono@example.com")
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(snapshots))
    git_module._discovery_cache.clear()


def _leftover_snapshots(tmp_path: Path) -> list[Path]:
    return list((tmp_path / "snapshots").glob("nono-changes-*"))


def _client(tmp_path: Path):
    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        SidecarSettings(
            host="127.0.0.1",
            port=8000,
            cwd=str(tmp_path),
            cors_origins=[],
            catalog_ready=False,
            catalog_error=None,
        ),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=False,
        changes_root=tmp_path / "changes-store",
    )
    return fastapi_testclient.TestClient(app)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "core.autocrlf=false", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout


def _repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "core.autocrlf", "false")
    (path / "app.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
    (path / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


# ── textdiff ────────────────────────────────────────────────────────────


def test_diff_texts_hunks_and_counts() -> None:
    old = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    new = old.replace("line 3\n", "line three\n").replace("line 18\n", "")
    hunks, added, deleted, coarse = diff_texts(old, new)
    assert (added, deleted, coarse) == (1, 2, False)
    assert len(hunks) == 2
    assert hunks[0]["old_start"] == 1
    assert "-line 3" in hunks[0]["lines"] and "+line three" in hunks[0]["lines"]
    assert hunks[1]["lines"].count("-line 18") == 1


def test_bom_change_is_visible_in_diff_and_counts() -> None:
    before = b"\xef\xbb\xbfdef f():\n    return 1\n"
    after = b"def f():\n    return 1\n"
    diff = diff_contents("a.py", before, after)
    assert (diff["added"], diff["deleted"]) == (1, 1)
    assert diff["hunks"][0]["lines"][0] == "-\ufeffdef f():"
    assert count_changes(before, after) == (1, 1, False)


def test_parse_unified_diff_shapes() -> None:
    patch = "\n".join(
        [
            "diff --git a/src/a.py b/src/a.py",
            "index 1111111..2222222 100644",
            "--- a/src/a.py",
            "+++ b/src/a.py",
            "@@ -1,2 +1,2 @@ def f():",
            " keep",
            "-old",
            "+new",
            "diff --git a/new.txt b/new.txt",
            "new file mode 100644",
            "--- /dev/null",
            "+++ b/new.txt",
            "@@ -0,0 +1 @@",
            "+hello",
            "diff --git a/gone.txt b/gone.txt",
            "deleted file mode 100644",
            "--- a/gone.txt",
            "+++ /dev/null",
            "@@ -1 +0,0 @@",
            "-bye",
            "diff --git a/old name.txt b/new name.txt",
            "similarity index 100%",
            "rename from old name.txt",
            "rename to new name.txt",
            "diff --git a/pic.png b/pic.png",
            "Binary files a/pic.png and b/pic.png differ",
            "",
        ]
    )
    files = parse_unified_diff(patch)
    by_path = {item["path"]: item for item in files}
    assert by_path["src/a.py"]["status"] == "modified"
    assert by_path["src/a.py"]["hunks"][0]["header"] == "def f():"
    assert (by_path["src/a.py"]["added"], by_path["src/a.py"]["deleted"]) == (1, 1)
    assert by_path["new.txt"]["status"] == "added" and by_path["new.txt"]["before"] is False
    assert by_path["gone.txt"]["status"] == "deleted" and by_path["gone.txt"]["after"] is False
    assert by_path["new name.txt"]["status"] == "renamed"
    assert by_path["new name.txt"]["old_path"] == "old name.txt"
    assert by_path["pic.png"]["kind"] == "binary"


# ── /app/git ────────────────────────────────────────────────────────────


@needs_git
def test_git_status_diff_stage_commit_log(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "ws")
    (repo / "app.py").write_text("a = 1\nb = 20\nc = 3\n", encoding="utf-8")
    (repo / "notes.txt").write_text("one\ntwo\n", encoding="utf-8")
    root = str(repo)
    with _client(tmp_path) as client:
        status = client.get("/app/git/status", params={"root": root}).json()
        assert status["is_repo"] is True
        assert status["branch"] == "main"
        entries = {item["path"]: item for item in status["entries"]}
        assert entries["app.py"]["unstaged"] and not entries["app.py"]["staged"]
        assert entries["app.py"]["unstaged_stat"] == {"added": 1, "deleted": 1, "binary": False}
        assert entries["notes.txt"]["untracked"] is True
        assert entries["notes.txt"]["unstaged_stat"]["added"] == 2

        diff = client.get("/app/git/diff", params={"root": root, "path": "app.py"}).json()
        assert diff["kind"] == "text"
        assert "-b = 2" in diff["hunks"][0]["lines"] and "+b = 20" in diff["hunks"][0]["lines"]
        full = client.get(
            "/app/git/diff", params={"root": root, "path": "app.py", "context": 100000}
        ).json()
        assert len(full["hunks"][0]["lines"]) == 4
        untracked = client.get("/app/git/diff", params={"root": root, "path": "notes.txt"}).json()
        assert untracked["status"] == "added" and untracked["hunks"][0]["lines"] == ["+one", "+two"]

        assert (
            client.post("/app/git/stage", json={"root": root, "paths": ["app.py"]}).status_code
            == 200
        )
        staged = client.get(
            "/app/git/diff", params={"root": root, "path": "app.py", "staged": True}
        ).json()
        assert staged["added"] == 1
        status = client.get("/app/git/status", params={"root": root}).json()
        entries = {item["path"]: item for item in status["entries"]}
        assert entries["app.py"]["staged"] and not entries["app.py"]["unstaged"]

        assert (
            client.post("/app/git/unstage", json={"root": root, "paths": ["app.py"]}).status_code
            == 200
        )
        assert client.post("/app/git/stage", json={"root": root}).status_code == 200
        empty = client.post("/app/git/commit", json={"root": root, "message": "  "})
        assert empty.status_code == 400
        committed = client.post("/app/git/commit", json={"root": root, "message": "second"})
        assert committed.status_code == 200, committed.text

        log = client.get("/app/git/log", params={"root": root}).json()["entries"]
        assert [item["subject"] for item in log] == ["second", "init"]
        assert "main" in log[0]["refs"]
        detail = client.get(
            "/app/git/commit", params={"root": root, "hash": log[0]["hash_full"]}
        ).json()
        assert detail["subject"] == "second"
        assert {item["path"] for item in detail["files"]} == {"app.py", "notes.txt"}
        bad = client.get("/app/git/commit", params={"root": root, "hash": "HEAD;rm"})
        assert bad.status_code == 400


@needs_git
def test_git_discard_and_path_fence(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "ws")
    (repo / "app.py").write_text("changed\n", encoding="utf-8")
    (repo / "junk.txt").write_text("x\n", encoding="utf-8")
    root = str(repo)
    other = _repo(tmp_path / "other")
    with _client(tmp_path) as client:
        resp = client.post(
            "/app/git/discard", json={"root": root, "paths": ["app.py"], "untracked": ["junk.txt"]}
        )
        assert resp.status_code == 200
        escaped = client.get("/app/git/diff", params={"root": root, "path": "../other/app.py"})
        foreign = client.get("/app/git/status", params={"root": root, "repo": str(other)})
    assert (repo / "app.py").read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
    assert not (repo / "junk.txt").exists()
    assert escaped.status_code == 403
    assert foreign.status_code == 403


@needs_git
def test_git_discovers_child_repos_and_worktrees(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    first = _repo(workspace / "alpha")
    _repo(workspace / "beta")
    linked = tmp_path / "alpha-feature"
    _git(first, "worktree", "add", "-q", "-b", "feature", str(linked))
    (linked / "app.py").write_text("feature\n", encoding="utf-8")
    with _client(tmp_path) as client:
        status = client.get("/app/git/status", params={"root": str(workspace)}).json()
        trees = client.get("/app/git/worktrees", params={"root": str(workspace)}).json()[
            "worktrees"
        ]
        linked_status = client.get(
            "/app/git/status", params={"root": str(workspace), "repo": str(linked)}
        )
    assert status["is_repo"] is True
    assert len(status["repositories"]) == 2
    branches = {Path(item["path"]).name: item for item in trees}
    assert branches["alpha-feature"]["branch"] == "feature"
    assert branches["alpha-feature"]["changes"] == 1
    assert branches["alpha"]["main"] is True
    assert linked_status.status_code == 200
    assert linked_status.json()["branch"] == "feature"


def test_git_status_outside_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    with _client(tmp_path) as client:
        status = client.get("/app/git/status", params={"root": str(plain)}).json()
    assert status["is_repo"] is False
    assert status["entries"] == []


# ── 每轮变更 ────────────────────────────────────────────────────────────


def _engine(cwd: Path) -> SimpleNamespace:
    return SimpleNamespace(
        _config=SimpleNamespace(cwd=str(cwd)), middleware_chain=MiddlewareChain()
    )


def _tool_call(name: str, **params: str) -> AssistantMessage:
    return AssistantMessage(
        message={
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "t1", "name": name, "input": params}],
        }
    )


@needs_git
def test_turn_changes_in_git_repo(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "ws")
    (repo / "README.md").write_text("# demo\nuser edit before the turn\n", encoding="utf-8")
    (repo / ".gitignore").write_text("build/\n", encoding="utf-8")
    (repo / "build").mkdir()
    (repo / "build" / "out.txt").write_text("v1\n", encoding="utf-8")
    service = WorkspaceChangesService(tmp_path / "store")
    engine = _engine(repo)

    async def scenario() -> dict:
        record = await service.begin("s1", engine)
        assert record is not None
        await engine.middleware_chain.run_after_api_call(
            assistant_message=_tool_call("write_file", path="build/out.txt", content="v2\n"),
            state=None,
        )
        (repo / "app.py").write_text("a = 1\nb = 2\nc = 30\nd = 4\n", encoding="utf-8")
        (repo / "new.txt").write_text("hello\n", encoding="utf-8")
        (repo / "build" / "out.txt").write_text("v2\n", encoding="utf-8")
        summary = await service.finish(record)
        assert summary is not None
        return summary

    summary = asyncio.run(scenario())
    files = {item["path"]: item for item in summary["files"]}
    assert set(files) == {"app.py", "build/out.txt", "new.txt"}
    assert (files["app.py"]["added"], files["app.py"]["deleted"]) == (2, 1)
    assert files["app.py"]["source"] == "git"
    assert files["new.txt"]["status"] == "added"
    assert files["build/out.txt"]["source"] == "tool"
    assert summary["total"] == 3
    assert not _leftover_snapshots(tmp_path)

    turns = service.list_turns("s1")
    assert [turn["id"] for turn in turns] == [summary["id"]]
    index = [item["path"] for item in summary["files"]].index("app.py")
    diff = service.file_diff("s1", summary["id"], index)
    assert diff is not None and diff["kind"] == "text"
    assert "+c = 30" in diff["hunks"][0]["lines"]

    with pytest.raises(FileExistsError):
        (repo / "app.py").write_text("later edit\n", encoding="utf-8")
        service.revert_file("s1", summary["id"], index)
    service.revert_file("s1", summary["id"], index, force=True)
    assert (repo / "app.py").read_text(encoding="utf-8") == "a = 1\nb = 2\nc = 3\n"
    added_index = [item["path"] for item in summary["files"]].index("new.txt")
    service.revert_file("s1", summary["id"], added_index)
    assert not (repo / "new.txt").exists()


def test_turn_changes_without_git_use_tool_captures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "plain"
    workspace.mkdir()
    (workspace / "doc.md").write_text("one\ntwo\n", encoding="utf-8")
    monkeypatch.setattr("nono_app_server.product.changes.git_available", lambda: False)
    service = WorkspaceChangesService(tmp_path / "store")
    engine = _engine(workspace)

    async def scenario() -> dict | None:
        record = await service.begin("s2", engine)
        assert record is not None
        await engine.middleware_chain.run_after_api_call(
            assistant_message=_tool_call(
                "edit_file", path="doc.md", old_string="two", new_string="TWO"
            ),
            state=None,
        )
        (workspace / "doc.md").write_text("one\nTWO\nthree\n", encoding="utf-8")
        return await service.finish(record)

    summary = asyncio.run(scenario())
    assert summary is not None
    assert summary["files"][0]["path"] == "doc.md"
    assert (summary["added"], summary["deleted"]) == (2, 1)

    async def unchanged() -> dict | None:
        record = await service.begin("s2", engine)
        assert record is not None
        await engine.middleware_chain.run_after_api_call(
            assistant_message=_tool_call(
                "edit_file", path="doc.md", old_string="x", new_string="y"
            ),
            state=None,
        )
        return await service.finish(record)

    assert asyncio.run(unchanged()) is None
    assert len(service.list_turns("s2")) == 1


@needs_git
def test_changes_routes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "ws")
    with _client(tmp_path) as client:
        service = client.app.state.turn_observer
        assert isinstance(service, WorkspaceChangesService)

        async def scenario() -> dict:
            record = await service.begin("s3", _engine(repo))
            assert record is not None
            await record.snapshot()
            (repo / "README.md").write_text("# demo\nmore\n", encoding="utf-8")
            summary = await service.finish(record)
            assert summary is not None
            return summary

        summary = asyncio.run(scenario())
        listed = client.get("/app/changes/s3").json()["turns"]
        diff = client.get(f"/app/changes/s3/{summary['id']}/diff", params={"index": 0})
        missing = client.get(f"/app/changes/s3/{summary['id']}/diff", params={"index": 5})
        revert = client.post(f"/app/changes/s3/{summary['id']}/revert", json={"index": 0})
    assert listed[0]["id"] == summary["id"]
    assert diff.status_code == 200 and diff.json()["added"] == 1
    assert missing.status_code == 404
    assert revert.status_code == 200
    assert (repo / "README.md").read_text(encoding="utf-8") == "# demo\n"


class _WritingTool(MockTool):
    """名为 write_file、真的写文件的模拟工具。"""

    def __init__(self, cwd: Path) -> None:
        super().__init__(name="write_file", concurrent_safe=False, write_side_effects=True)
        self._cwd = cwd

    async def execute(self, input_params, context):  # type: ignore[override]
        target = self._cwd / str(input_params["path"])
        target.write_text(str(input_params["content"]), encoding="utf-8")
        return await super().execute(input_params, context)


@needs_git
def test_chat_stream_emits_workspace_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "ws")
    engine = create_test_engine(
        responses=[
            {
                "text": "",
                "tool_calls": [
                    {
                        "name": "write_file",
                        "id": "tc_1",
                        "input": {"path": "app.py", "content": "a = 1\n"},
                    }
                ],
            },
            {"text": "done", "tool_calls": []},
        ],
        tools=[_WritingTool(repo)],
        transcript_writer=LocalFileTranscriptWriter(tmp_path / "e2e.jsonl"),
        config_overrides={"cwd": str(repo), "session_id": "e2e"},
    )
    with _client(tmp_path) as client:
        client.app.state.engine_sessions.sessions["e2e"] = engine
        with client.stream("POST", "/chat", json={"session_id": "e2e", "message": "edit"}) as resp:
            body = "".join(resp.iter_text())
        turns = client.get("/app/changes/e2e").json()["turns"]
    assert "event: workspace_changes" in body
    assert body.index("event: workspace_changes") < body.index("event: done")
    assert len(turns) == 1
    assert turns[0]["files"][0]["path"] == "app.py"
    assert (turns[0]["files"][0]["added"], turns[0]["files"][0]["deleted"]) == (0, 2)
    assert turns[0]["end_seq"] > turns[0]["start_seq"]
    assert not _leftover_snapshots(tmp_path)


# ── 文件保存 ────────────────────────────────────────────────────────────


def test_save_file_keeps_encoding_and_detects_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    crlf = root / "win.txt"
    crlf.write_bytes(b"one\r\ntwo\r\n")
    gbk = root / "gbk.txt"
    gbk.write_bytes("你好\n".encode("gb18030"))
    bom = root / "bom.txt"
    bom.write_bytes(b"\xef\xbb\xbfhi\n")
    with _client(tmp_path) as client:
        loaded = client.get("/app/fs/file", params={"root": str(root), "path": str(crlf)}).json()
        assert loaded["hash"]
        saved = client.put(
            "/app/fs/file",
            json={
                "root": str(root),
                "path": str(crlf),
                "content": "one\ntwo\nthree\n",
                "base_hash": loaded["hash"],
            },
        )
        stale = client.put(
            "/app/fs/file",
            json={
                "root": str(root),
                "path": str(crlf),
                "content": "x\n",
                "base_hash": loaded["hash"],
            },
        )
        gbk_saved = client.put(
            "/app/fs/file", json={"root": str(root), "path": str(gbk), "content": "你好，世界\n"}
        )
        bom_saved = client.put(
            "/app/fs/file", json={"root": str(root), "path": str(bom), "content": "hello\n"}
        )
        outside = client.put(
            "/app/fs/file",
            json={"root": str(root), "path": str(tmp_path / "x.txt"), "content": "x"},
        )
    assert saved.status_code == 200
    assert crlf.read_bytes() == b"one\r\ntwo\r\nthree\r\n"
    assert saved.json()["hash"] != loaded["hash"]
    assert stale.status_code == 409
    assert gbk_saved.status_code == 200
    assert gbk.read_bytes() == "你好，世界\n".encode("gb18030")
    assert bom_saved.status_code == 200
    assert bom.read_bytes() == b"\xef\xbb\xbfhello\n"
    assert outside.status_code in (403, 404)
