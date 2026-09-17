"""打包后 sidecar 托管 Vite dist。"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_app_server.web_ui import mount_web_ui, resolve_web_dist  # noqa: E402


def _web_dist(tmp_path: Path) -> Path:
    dist = tmp_path / "web"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html>desktop-ui</html>", encoding="utf-8")
    (dist / "logo.png").write_bytes(b"png")
    (assets / "app.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def test_resolve_web_dist_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NONO_WEB_DIST", raising=False)
    assert resolve_web_dist() is None


def test_resolve_web_dist_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = _web_dist(tmp_path)
    monkeypatch.setenv("NONO_WEB_DIST", str(dist))
    assert resolve_web_dist() == dist.resolve()


def test_mount_web_ui_keeps_api_routes(tmp_path: Path) -> None:
    dist = _web_dist(tmp_path)
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    mount_web_ui(app, dist)
    with fastapi_testclient.TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        home = client.get("/")
        assert home.status_code == 200
        assert "desktop-ui" in home.text
        asset = client.get("/assets/app.js")
        assert asset.status_code == 200
        assert "console.log" in asset.text
        logo = client.get("/logo.png")
        assert logo.status_code == 200
        assert logo.content == b"png"
        missing = client.get("/not-a-file.png")
        assert missing.status_code == 404
