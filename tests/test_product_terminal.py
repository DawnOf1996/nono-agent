"""工作区终端：shell 列表、目录限制，以及一条能回显的会话。"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.features.router import mount_product_routes  # noqa: E402
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
