"""会话目录：展示、更改，以及把已有会话文件迁走。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nono_agent.session.transcript import (
    relocate_session_files,
    use_session_dir,
)
from nono_agent.settings import sources as settings_sources


def teardown_function() -> None:
    use_session_dir(None)


def test_relocate_moves_session_files_only(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    (old / "abc.jsonl.zst").write_bytes(b"events")
    (old / "abc.header.json").write_text("{}", encoding="utf-8")
    (old / "notes.txt").write_text("keep", encoding="utf-8")

    moved = relocate_session_files(old, new)

    assert moved == 2
    assert (new / "abc.jsonl.zst").read_bytes() == b"events"
    assert (new / "abc.header.json").is_file()
    assert (old / "notes.txt").read_text(encoding="utf-8") == "keep"
    assert not (old / "abc.jsonl.zst").exists()


def test_relocate_refuses_name_collision(tmp_path: Path) -> None:
    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    new.mkdir()
    (old / "abc.jsonl").write_text("a", encoding="utf-8")
    (new / "abc.jsonl").write_text("b", encoding="utf-8")

    with pytest.raises(FileExistsError):
        relocate_session_files(old, new)
    assert (old / "abc.jsonl").read_text(encoding="utf-8") == "a"


def test_relocate_refuses_nested_directory(tmp_path: Path) -> None:
    old = tmp_path / "sessions"
    old.mkdir()
    (old / "abc.jsonl").write_text("a", encoding="utf-8")

    with pytest.raises(ValueError):
        relocate_session_files(old, old / "nested")
    assert (old / "abc.jsonl").is_file()


def test_change_session_directory_persists_and_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nono_agent.core.config import EngineConfig
    from nono_app_server.config import SidecarSettings
    from nono_app_server.engine import create_server_app
    from nono_app_server.features.router import mount_product_routes

    old = tmp_path / "old"
    new = tmp_path / "new"
    old.mkdir()
    (old / "abc.jsonl.zst").write_bytes(b"events")
    (old / "abc.header.json").write_text("{}", encoding="utf-8")
    use_session_dir(old)
    monkeypatch.setattr(settings_sources, "user_home", lambda: tmp_path)

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
    fastapi = pytest.importorskip("fastapi.testclient")
    with fastapi.TestClient(app) as client:
        response = client.post("/app/session-dir", json={"path": str(new)})
        info = client.get("/app/info")
    assert response.status_code == 200
    assert response.json()["moved"] == 2
    assert Path(response.json()["session_dir"]) == new.resolve()
    assert (new / "abc.jsonl.zst").read_bytes() == b"events"
    assert info.json()["session_dir"] == str(new.resolve())
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["session_dir"] == str(new.resolve())
