"""控制通道与 git 推送流：服务端一变就推，浏览器不再自己问。"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from starlette.requests import Request  # noqa: E402

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.common.gitcmd import path_identity  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.features.control.api import control_stream  # noqa: E402
from nono_app_server.features.git import service as git_module  # noqa: E402
from nono_app_server.features.git.api import stream_git  # noqa: E402
from nono_app_server.features.git.service import workspace_signature  # noqa: E402
from nono_app_server.features.git.watch import notify_changed  # noqa: E402
from nono_app_server.features.permissions import service as permissions  # noqa: E402
from nono_app_server.features.router import mount_product_routes  # noqa: E402

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def _request(path: str, query: bytes = b"") -> Request:
    """造一个「连着但没消息」的请求。

    ``is_disconnected`` 要有一个永远不返回的 receive 才能装作还连着 —— 真的
    断开的话流会立刻结束，就测不到「服务端推了一帧」这件事了。
    """

    async def receive() -> dict[str, str]:
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": [],
            "client": ("127.0.0.1", 123),
            "server": ("127.0.0.1", 8000),
        },
        receive=receive,
    )


def _frames(chunk: str) -> list[tuple[str, str]]:
    """把一段 SSE 文本拆成 (event, data)；注释帧（`: ping`）不算数据。"""
    out: list[tuple[str, str]] = []
    for block in chunk.strip().split("\n\n"):
        event = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
        if event:
            out.append((event, "\n".join(data)))
    return out


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
    (path / "app.py").write_text("a = 1\n", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture(autouse=True)
def _clear_pending() -> None:
    permissions._pending.clear()
    yield
    for _info, future in list(permissions._pending.values()):
        if not future.done():
            future.cancel()
    permissions._pending.clear()


@pytest.fixture
def manager(tmp_path: Path):
    app = create_server_app(EngineConfig(cwd=str(tmp_path)))
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
        EngineConfig(cwd=str(tmp_path)),
        db_path=tmp_path / "app.db",
        import_engine_index=False,
    )
    return app.state.engine_sessions


# ── 控制通道 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_control_stream_opens_with_current_values(manager) -> None:
    response = await control_stream(_request("/app/control/stream"), manager)
    chunks = response.body_iterator
    try:
        first = _frames(await anext(chunks))
        assert first == [("health", json.dumps({"status": "ok", "sessions": 0}))]
        second = _frames(await anext(chunks))
        assert second[0][0] == "permissions"
        assert json.loads(second[0][1]) == {"permissions": []}
    finally:
        await chunks.aclose()


@pytest.mark.asyncio
async def test_control_stream_pushes_permission_without_polling(manager) -> None:
    """挂起一条授权后应当立刻收到一帧 —— 客户端没有发起任何请求。"""
    response = await control_stream(_request("/app/control/stream"), manager)
    chunks = response.body_iterator
    try:
        await anext(chunks)  # health
        assert _frames(await anext(chunks))[0][0] == "permissions"

        asking = asyncio.create_task(permissions.request_permission("s1", "edit_file", {"p": 1}))
        await asyncio.sleep(0)  # 让它先登记进 _pending

        pushed = _frames(await anext(chunks))
        assert pushed[0][0] == "permissions"
        payload = json.loads(pushed[0][1])["permissions"]
        assert [item["tool_name"] for item in payload] == ["edit_file"]
        assert payload[0]["session_id"] == "s1"

        permissions.answer_permission(payload[0]["request_id"], True)
        assert await asking is True
    finally:
        await chunks.aclose()


@pytest.mark.asyncio
async def test_control_stream_pushes_empty_list_after_answer(manager) -> None:
    response = await control_stream(_request("/app/control/stream"), manager)
    chunks = response.body_iterator
    try:
        await anext(chunks)
        await anext(chunks)
        asking = asyncio.create_task(permissions.request_permission("s1", "bash", {"cmd": "ls"}))
        await asyncio.sleep(0)
        pending = json.loads(_frames(await anext(chunks))[0][1])["permissions"]
        permissions.answer_permission(pending[0]["request_id"], False)
        assert await asking is False
        # 等待方在 finally 里摘掉条目，那一次变化同样要推。
        cleared = _frames(await anext(chunks))
        assert json.loads(cleared[0][1]) == {"permissions": []}
    finally:
        await chunks.aclose()


# ── git 推送流 ─────────────────────────────────────────────────────────


@needs_git
@pytest.mark.asyncio
async def test_git_stream_sends_snapshot_then_pushes_change(tmp_path: Path) -> None:
    root = _repo(tmp_path / "work")
    git_module._discovery_cache.clear()

    response = await stream_git(_request("/app/git/stream"), str(root), None)
    chunks = response.body_iterator
    try:
        opening = _frames(await anext(chunks))
        assert [event for event, _ in opening] == ["status", "worktrees"]
        status = json.loads(opening[0][1])
        assert status["is_repo"] is True and status["entries"] == []
        trees = json.loads(opening[1][1])["worktrees"]
        assert path_identity(trees[0]["path"]) == path_identity(root)

        # 服务端自己知道改动时（这里直接模拟），应当立刻推一帧。
        (root / "app.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
        notify_changed()

        pushed = _frames(await anext(chunks))
        assert [event for event, _ in pushed] == ["status", "worktrees"]
        changed = json.loads(pushed[0][1])
        assert [entry["path"] for entry in changed["entries"]] == ["app.py"]
        assert changed["entries"][0]["unstaged_stat"] == {
            "added": 1,
            "deleted": 0,
            "binary": False,
        }
    finally:
        await chunks.aclose()


@needs_git
@pytest.mark.asyncio
async def test_git_stream_skips_push_when_signature_unchanged(tmp_path: Path) -> None:
    """只读命令结束时也会通知，但状态没变就不该推 —— 否则白刷新一屏。"""
    root = _repo(tmp_path / "work")
    git_module._discovery_cache.clear()

    response = await stream_git(_request("/app/git/stream"), str(root), None)
    chunks = response.body_iterator
    try:
        await anext(chunks)  # 连上就先给一份当前状态
        notify_changed()
        # 没有新状态可推：这里拿不到任何一帧。
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(anext(chunks), timeout=1.0)
    finally:
        await chunks.aclose()


@needs_git
@pytest.mark.asyncio
async def test_git_stream_reports_non_repo(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    git_module._discovery_cache.clear()

    response = await stream_git(_request("/app/git/stream"), str(plain), None)
    chunks = response.body_iterator
    try:
        opening = _frames(await anext(chunks))
        assert json.loads(opening[0][1])["is_repo"] is False
        assert json.loads(opening[1][1]) == {"worktrees": []}
    finally:
        await chunks.aclose()


# ── 指纹 ───────────────────────────────────────────────────────────────


@needs_git
@pytest.mark.asyncio
async def test_signature_tracks_content_of_untracked_file(tmp_path: Path) -> None:
    """未跟踪文件被改内容时 git status 的输出纹丝不动 —— 指纹必须带上大小/mtime。"""
    root = _repo(tmp_path / "work")
    git_module._discovery_cache.clear()
    draft = root / "draft.md"
    draft.write_text("one\n", encoding="utf-8")

    before = await workspace_signature(str(root))
    assert before != ""
    draft.write_text("one\ntwo\n", encoding="utf-8")
    assert await workspace_signature(str(root)) != before


@needs_git
@pytest.mark.asyncio
async def test_signature_tracks_commit_and_new_file(tmp_path: Path) -> None:
    root = _repo(tmp_path / "work")
    git_module._discovery_cache.clear()
    before = await workspace_signature(str(root))

    (root / "extra.txt").write_text("x\n", encoding="utf-8")
    after_add = await workspace_signature(str(root))
    assert after_add != before

    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "add extra")
    assert await workspace_signature(str(root)) != after_add
