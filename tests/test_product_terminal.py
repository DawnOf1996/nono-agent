"""工作区终端：shell 列表、目录限制，以及一条能回显的会话。"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pyte
import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.common.paths import workspace_key  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.features.router import mount_product_routes  # noqa: E402
from nono_app_server.features.terminal import service  # noqa: E402
from nono_app_server.features.terminal.service import shutdown_terminals  # noqa: E402


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
    )
    return fastapi_testclient.TestClient(app)


@pytest.fixture(autouse=True)
def _stop_terminals():
    yield
    shutdown_terminals()


def test_child_env_strips_frozen_bundle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nono_app_server.features.terminal import service

    bundle = tmp_path / "internal"
    other = tmp_path / "bin"
    bundle.mkdir()
    other.mkdir()
    monkeypatch.setattr(service.sys, "frozen", True, raising=False)
    monkeypatch.setattr(service.sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setenv("PATH", os.pathsep.join([str(bundle), str(other)]))
    monkeypatch.setenv("_MEIPASS2", str(bundle))
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", str(bundle))
    monkeypatch.setenv("SYSTEMROOT", str(tmp_path))

    env = service._child_env()

    assert env is not None
    assert os.path.normcase(str(bundle)) not in {
        os.path.normcase(item) for item in env["PATH"].split(os.pathsep)
    }
    assert str(other) in env["PATH"].split(os.pathsep)
    assert "_MEIPASS2" not in env
    assert "_PYI_APPLICATION_HOME_DIR" not in env
    assert env["SYSTEMROOT"] == str(tmp_path)
    assert os.environ["_MEIPASS2"] == str(bundle)


def _build_sidecar():
    import importlib.util

    script = Path(__file__).resolve().parents[1] / "scripts" / "build_sidecar.py"
    spec = importlib.util.spec_from_file_location("build_sidecar", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_winpty_layout_requires_host_beside_dll(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("ConPTY host is Windows-only")
    module = _build_sidecar()
    folder = tmp_path / "_internal" / "winpty"
    folder.mkdir(parents=True)
    for name in ("conpty.dll", "OpenConsole.exe", "winpty-agent.exe"):
        (folder / name).write_bytes(b"")
    module._require_winpty_layout(tmp_path)

    (folder / "OpenConsole.exe").unlink()
    (tmp_path / "OpenConsole.exe").write_bytes(b"")
    with pytest.raises(SystemExit):
        module._require_winpty_layout(tmp_path)


def test_hidden_console_is_allocated_and_hide_failure_is_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name != "nt":
        pytest.skip("console allocation is Windows-only")
    import ctypes

    from nono_app_server.features.terminal import service

    calls: list[str] = []
    state = {"gets": 0, "hidden": None}

    class _Fn:
        def __init__(self, impl) -> None:
            self._impl = impl
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            return self._impl(*args)

    class _Kernel32:
        def __init__(self) -> None:
            self.GetConsoleWindow = _Fn(self._get)
            self.AllocConsole = _Fn(self._alloc)

        def _get(self):
            state["gets"] += 1
            return 0 if state["gets"] == 1 else 42

        def _alloc(self):
            calls.append("alloc")
            return 1

    class _User32:
        def __init__(self) -> None:
            self.ShowWindow = _Fn(self._show)

        def _show(self, hwnd, cmd):
            state["hidden"] = (hwnd, cmd)
            return 0

    libs = {"kernel32": _Kernel32(), "user32": _User32()}
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, use_last_error=False: libs[name])

    service._ensure_hidden_console()

    assert calls == ["alloc"]
    assert state["hidden"] == (42, 0)


def test_spawn_retries_conpty_init_panic(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("ConPTY is Windows-only")
    import winpty

    from nono_app_server.features.terminal import service

    calls = {"n": 0}

    def spawn(argv, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise type("PanicException", (Exception,), {})(
                "called `Result::unwrap()` on an `Err` value: Error { code: HRESULT(0x00000000), message: ok }"
            )
        return "proc"

    monkeypatch.setattr(service, "_ensure_hidden_console", lambda: None)
    monkeypatch.setattr(service, "_require_conpty_host", lambda: None)
    monkeypatch.setattr(winpty.PtyProcess, "spawn", staticmethod(spawn))
    spec = service.ShellSpec("cmd", "Command Prompt", ["cmd.exe"])

    # _spawn 外面包一层 _WinPtyProcess，把 winpty 的 str 接口归一成字节。
    assert service._spawn(spec, str(tmp_path), 80, 24)._proc == "proc"
    assert calls["n"] == 2


def test_winpty_process_normalizes_read_to_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    """PTY 层的契约：``read()`` 一定返回字节，与平台无关。"""
    from nono_app_server.features.terminal.service import _WinPtyProcess

    class _Proc:
        def read(self, size: int) -> str:
            return "\u4f60\u597d"

    assert _WinPtyProcess(_Proc()).read(16) == "\u4f60\u597d".encode("utf-8")

    class _BytesProc:
        def read(self, size: int) -> bytes:
            return b"raw"

    assert _WinPtyProcess(_BytesProc()).read(16) == b"raw"


def test_child_env_unset_when_not_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    from nono_app_server.features.terminal import service

    monkeypatch.delattr(service.sys, "frozen", raising=False)
    monkeypatch.delattr(service.sys, "_MEIPASS", raising=False)
    assert service._child_env() is None


def test_git_bash_found_from_mingw64_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from nono_app_server.features.terminal import service

    root = tmp_path / "Git"
    git = root / "mingw64" / "bin" / "git.exe"
    bash = root / "bin" / "bash.exe"
    git.parent.mkdir(parents=True)
    bash.parent.mkdir(parents=True)
    git.write_bytes(b"")
    bash.write_bytes(b"")
    (root / "git-bash.exe").write_bytes(b"")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "missing"))
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing-x86"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(service.shutil, "which", lambda name: str(git) if name == "git" else None)

    assert service._git_bash_exe() == bash


def test_terminal_shells_and_rejects_escape(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    outside = tmp_path / "other"
    outside.mkdir()
    with _client(tmp_path) as client:
        listed = client.get("/app/terminal/shells")
        unknown = client.post(
            "/app/terminal",
            json={"root": str(root), "shell": "not-a-shell"},
        )
        escaped = client.post(
            "/app/terminal",
            json={"root": str(root), "cwd": str(outside), "shell": "cmd"},
        )
    assert listed.status_code == 200
    shells = listed.json()["shells"]
    assert shells
    assert all(item["label"].isascii() and item["label"] for item in shells)
    assert unknown.status_code == 400
    assert escaped.status_code == 403


def test_terminal_echo(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    with _client(tmp_path) as client:
        shells = client.get("/app/terminal/shells").json()["shells"]
        shell = next((item["id"] for item in shells if item["id"] == "cmd"), shells[0]["id"])
        created = client.post(
            "/app/terminal",
            json={"root": str(root), "shell": shell, "cols": 80, "rows": 24},
        )
        assert created.status_code == 200
        session_id = created.json()["id"]
        text = ""
        with client.websocket_connect(f"/app/terminal/{session_id}/ws") as socket:
            socket.send_text(json.dumps({"type": "input", "data": "echo NONO_TERM_OK\r"}))
            for _ in range(40):
                message = socket.receive()
                if "bytes" in message:
                    text += message["bytes"].decode("utf-8", "replace")
                elif message.get("text"):
                    text += message["text"]
                if "NONO_TERM_OK" in text:
                    break
        stopped = client.delete(f"/app/terminal/{session_id}")
    assert stopped.status_code == 200
    assert "NONO_TERM_OK" in text


# ----------------------------------------------------------------------
# 服务端屏幕：重连与切换工作区后视口无损
# ----------------------------------------------------------------------


def _open(client, root: Path) -> str:
    shells = client.get("/app/terminal/shells").json()["shells"]
    shell = next((item["id"] for item in shells if item["id"] == "cmd"), shells[0]["id"])
    created = client.post("/app/terminal", json={"root": str(root), "shell": shell, "cols": 80, "rows": 24})
    assert created.status_code == 200
    return created.json()["id"]


def _wait_for_screen(session_id: str, needle: str, timeout_s: float = 15.0) -> str:
    """轮询服务端屏幕直到出现目标串。

    不走 socket：``TestClient`` 的 ``receive()`` 是阻塞的，shell 空闲后没有帧
    就永久等待。TestClient 与被测应用同进程，所以直接读注册表里的屏幕，既确定
    又不依赖帧时序。
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        session = service._sessions.get(session_id)
        if session is not None:
            text = "\n".join(session.screen.display())
            if needle in text:
                return text
        time.sleep(0.05)
    session = service._sessions.get(session_id)
    raise AssertionError(f"{needle!r} never reached the server screen")


def _first_frame(client, session_id: str) -> dict:
    """重连并取首帧。服务端总是先发 snapshot，所以一次 receive 即可。"""
    with client.websocket_connect(f"/app/terminal/{session_id}/ws") as socket:
        message = socket.receive()
        assert message.get("text"), f"expected a control frame, got {message!r}"
        return json.loads(message["text"])


def test_reconnect_restores_screen_from_server(tmp_path: Path) -> None:
    """切换工作区的等价场景：断开 WS、进程继续跑、重连拿到逐格一致的视口。"""
    root = tmp_path / "ws"
    root.mkdir()
    with _client(tmp_path) as client:
        session_id = _open(client, root)
        with client.websocket_connect(f"/app/terminal/{session_id}/ws") as socket:
            first = json.loads(socket.receive()["text"])
            assert first["type"] == "snapshot" and first["state"] == "running"
            socket.send_text(json.dumps({"type": "input", "data": "echo NONO_KEEP_ALIVE\r"}))

        # WS 已关闭但进程继续跑 —— 切走工作区不清终端。
        _wait_for_screen(session_id, "NONO_KEEP_ALIVE")

        frame = _first_frame(client, session_id)
        assert frame["type"] == "snapshot"
        assert frame["state"] == "running"
        assert frame["cols"] == 80 and frame["rows"] == 24
        replay = pyte.Screen(frame["cols"], frame["rows"])
        pyte.ByteStream(replay).feed(frame["screen"].encode("utf-8"))
        assert any("NONO_KEEP_ALIVE" in line for line in replay.display)
        client.delete(f"/app/terminal/{session_id}")


def test_snapshot_is_idempotent_across_resync() -> None:
    """快照自身可再快照：RESYNC 补发的帧与首次帧形状一致。"""
    from nono_app_server.features.terminal.screen import TerminalScreen

    for payload in (
        b"\x1b[1;32mgreen\x1b[0m plain\r\n",
        "\u4f60\u597d\u4e16\u754c".encode(),
        b"\x1b[38;5;196mR\x1b[38;2;1;2;3mT\x1b[0m\x1b[7mV",
        b"".join(("row%02d\r\n" % i).encode() for i in range(50)),
    ):
        first = TerminalScreen(20, 6, history=50)
        first.feed(payload)
        once = first.snapshot()
        second = TerminalScreen(20, 6, history=50)
        second.feed(once.encode("utf-8"))
        assert second.snapshot() == once, payload


# ----------------------------------------------------------------------
# 附着与终态
# ----------------------------------------------------------------------


def _session(tmp_path: Path, root: Path) -> "service.TerminalSession":
    spec = service.ShellSpec("cmd", "Command Prompt", ["cmd.exe"])
    return service.TerminalSession(
        id="s1", root=workspace_key(root), shell=spec, cwd=str(root),
        proc=object(), cols=20, rows=4, title="t",
    )


def test_attach_queues_before_snapshot_so_no_output_is_lost(tmp_path: Path) -> None:
    """attach 必须先入队再取快照：并发到达的输出不能落在缝隙里。"""
    root = tmp_path / "ws"
    root.mkdir()
    session = _session(tmp_path, root)
    session.push(b"before")
    follower, frame = session.attach()
    session.push(b"after")

    assert "before" in frame["screen"]
    assert follower.queue.get_nowait() == b"after"


def test_attach_to_exited_session_receives_sentinel(tmp_path: Path) -> None:
    """已退出的会话也补哨兵，否则客户端会把死掉的 shell 当成运行中。"""
    root = tmp_path / "ws"
    root.mkdir()
    session = _session(tmp_path, root)
    session.settle(3)
    follower, frame = session.attach()

    assert frame["state"] == "exited" and frame["exitCode"] == 3
    assert follower.queue.get_nowait() is None


def test_resize_reports_new_dimensions(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    session = _session(tmp_path, root)

    class _Proc:
        def setwinsize(self, rows: int, cols: int) -> None:
            self.seen = (rows, cols)

    session.proc = _Proc()
    asyncio.run(session.resize(100, 30))
    assert session.proc.seen == (30, 100)
    assert (session.screen.cols, session.screen.rows) == (100, 30)
    assert session.snapshot_frame()["cols"] == 100


# ----------------------------------------------------------------------
# 配额与回收
# ----------------------------------------------------------------------


def test_quota_counts_only_live_sessions(tmp_path: Path) -> None:
    """退出的终端不占配额：否则反复开关终端会把工作区永久锁死。"""
    root = tmp_path / "ws"
    root.mkdir()
    key = workspace_key(root)
    sessions = []
    for index in range(service._settings.max_per_workspace):
        item = _session(tmp_path, root)
        item.id = f"s{index}"
        service._sessions[item.id] = item
        sessions.append(item)
    assert service.live_count(key) == service._settings.max_per_workspace
    for item in sessions:
        item.settle(0)
    assert service.live_count(key) == 0
    for item in sessions:
        service._sessions.pop(item.id, None)


def test_reaper_respects_attachments_and_ttls(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    idle = _session(tmp_path, root)
    idle.id = "idle"
    exited = _session(tmp_path, root)
    exited.id = "exited"
    exited.settle(0)
    held = _session(tmp_path, root)
    held.id = "held"
    service._sessions.update({"idle": idle, "exited": exited, "held": held})

    now = 10_000.0
    idle.last_output_at = now - service._settings.idle_ttl_s - 1
    exited.finished_at = now - service._settings.exit_ttl_s - 1
    held.last_output_at = now - service._settings.idle_ttl_s - 1
    held.attach()  # 有客户端附着，永不回收

    removed = service.reap(now=now)
    assert set(removed) == {"idle", "exited"}
    assert set(service._sessions) == {"held"}
    service._sessions.pop("held", None)


def test_workspace_list_survives_workspace_switch(tmp_path: Path) -> None:
    """切走再切回：服务端仍然持有该工作区的终端。"""
    first = tmp_path / "a"
    second = tmp_path / "b"
    first.mkdir()
    second.mkdir()
    with _client(tmp_path) as client:
        one = _open(client, first)
        two = _open(client, second)
        assert {item["id"] for item in client.get(f"/app/terminal?root={first}").json()["terminals"]} == {one}
        assert {item["id"] for item in client.get(f"/app/terminal?root={second}").json()["terminals"]} == {two}
        client.delete(f"/app/terminal/{one}")
        client.delete(f"/app/terminal/{two}")


def test_missing_session_reports_missing_then_closes(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    with _client(tmp_path) as client:
        with client.websocket_connect("/app/terminal/deadbeef/ws") as socket:
            payload = json.loads(socket.receive()["text"])
            assert payload == {"type": "missing"}
