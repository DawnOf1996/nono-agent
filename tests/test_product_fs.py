"""产品层本机目录浏览与新建文件夹。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.features.router import mount_product_routes  # noqa: E402


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


def test_mkdir_creates_folder(tmp_path: Path) -> None:
    parent = tmp_path / "ws"
    parent.mkdir()
    with _client(tmp_path) as client:
        resp = client.post("/app/fs/mkdir", json={"parent": str(parent), "name": "proj"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "proj"
    assert (parent / "proj").is_dir()


def test_mkdir_rejects_invalid_name(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        nested = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": "a/b"})
        colon = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": "a:b"})
        dot = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": ".."})
    assert nested.status_code == 400
    assert colon.status_code == 400
    assert dot.status_code == 400


def test_write_markdown(tmp_path: Path) -> None:
    folder = tmp_path / "ws"
    folder.mkdir()
    with _client(tmp_path) as client:
        resp = client.post(
            "/app/fs/write",
            json={"directory": str(folder), "name": "笔记", "content": "# 你好\n"},
        )
        again = client.post(
            "/app/fs/write",
            json={"directory": str(folder), "name": "笔记.md", "content": "x"},
        )
        nested = client.post(
            "/app/fs/write",
            json={"directory": str(folder), "name": "a/b.md", "content": "x"},
        )
        other = client.post(
            "/app/fs/write",
            json={"directory": str(folder), "name": "note.txt", "content": "x"},
        )
    assert resp.status_code == 200
    assert (folder / "笔记.md").read_text(encoding="utf-8") == "# 你好\n"
    assert again.status_code == 409
    assert nested.status_code == 400
    assert other.status_code == 400


def test_list_directory_skips_files(tmp_path: Path) -> None:
    (tmp_path / "keep").mkdir()
    (tmp_path / "note.md").write_text("x", encoding="utf-8")
    with _client(tmp_path) as client:
        resp = client.get("/app/fs", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    names = [item["name"] for item in resp.json()["entries"]]
    assert "keep" in names
    assert "note.md" not in names


def test_list_directory_exposes_home_and_system_root(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        resp = client.get("/app/fs", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["home"] == str(Path.home())
    if os.name == "nt":
        # Windows 没有单一根目录，"系统根"就是磁盘列表本身。
        assert body["root"] == ""
        with _client(tmp_path) as client:
            drives = client.get("/app/fs").json()
        assert drives["path"] == ""
        assert drives["parent"] is None
        assert all(item["name"].endswith(":") for item in drives["entries"])
    else:
        # Linux 与 macOS 的系统根都是 /。
        assert body["root"] == os.sep
        with _client(tmp_path) as client:
            listing = client.get("/app/fs", params={"path": body["root"]})
        assert listing.status_code == 200
        assert listing.json()["path"] == os.sep
        assert listing.json()["parent"] is None


def test_workspace_entries_and_preview(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "note.md").write_text("hello", encoding="utf-8")
    (root / ".gitignore").write_text("x\n", encoding="utf-8")
    (root / "src").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "pic.png").write_bytes(b"\x89PNG\r\n")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    (root / "doc.pdf").write_bytes(b"%PDF-1.4\n")
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    with _client(tmp_path) as client:
        listing = client.get("/app/fs/entries", params={"root": str(root)})
        nested = client.get("/app/fs/entries", params={"root": str(root), "path": str(root / "src")})
        outside = client.get("/app/fs/entries", params={"root": str(root), "path": str(tmp_path)})
        escaped = client.get("/app/fs/entries", params={"root": str(root), "path": str(root / "..")})
        preview = client.get("/app/fs/file", params={"root": str(root), "path": str(root / "note.md")})
        image = client.get("/app/fs/file", params={"root": str(root), "path": str(root / "pic.png")})
        binary = client.get("/app/fs/file", params={"root": str(root), "path": str(root / "blob.bin")})
        leaked = client.get("/app/fs/file", params={"root": str(root), "path": str(secret)})
        raw = client.get("/app/fs/raw", params={"root": str(root), "path": str(root / "pic.png")})
        raw_text = client.get("/app/fs/raw", params={"root": str(root), "path": str(root / "note.md")})
        pdf_bytes = client.get("/app/fs/bytes", params={"root": str(root), "path": str(root / "doc.pdf")})
        leaked_bytes = client.get("/app/fs/bytes", params={"root": str(root), "path": str(secret)})
        dir_bytes = client.get("/app/fs/bytes", params={"root": str(root), "path": str(root / "src")})
    assert listing.status_code == 200
    names = {item["name"]: item["is_dir"] for item in listing.json()["entries"]}
    assert names["src"] is True
    assert names["note.md"] is False
    assert names[".gitignore"] is False
    assert [item["name"] for item in listing.json()["entries"] if item["is_dir"]] == ["src"]
    assert nested.status_code == 200
    assert nested.json()["entries"][0]["name"] == "a.py"
    assert outside.status_code == 403
    assert escaped.status_code == 403
    assert preview.status_code == 200
    assert preview.json()["kind"] == "text"
    assert preview.json()["content"] == "hello"
    assert image.json()["kind"] == "image"
    assert image.json()["content"] is None
    assert binary.json()["kind"] == "binary"
    assert leaked.status_code == 403
    assert raw.status_code == 200
    assert raw.headers["content-type"].startswith("image/png")
    assert raw_text.status_code == 415
    assert pdf_bytes.status_code == 200
    assert pdf_bytes.content.startswith(b"%PDF")
    assert leaked_bytes.status_code == 403
    assert dir_bytes.status_code == 400


def test_workspace_file_too_large(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "big.txt").write_bytes(b"a" * (5_000_000 + 1))
    with _client(tmp_path) as client:
        resp = client.get("/app/fs/file", params={"root": str(root), "path": str(root / "big.txt")})
    assert resp.status_code == 200
    assert resp.json()["kind"] == "too_large"
    assert resp.json()["content"] is None


def test_workspace_bytes_too_large(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "big.pdf").write_bytes(b"a" * (50_000_000 + 1))
    with _client(tmp_path) as client:
        resp = client.get("/app/fs/bytes", params={"root": str(root), "path": str(root / "big.pdf")})
    assert resp.status_code == 413


def test_mkdir_conflict(tmp_path: Path) -> None:
    existing = tmp_path / "taken"
    existing.mkdir()
    with _client(tmp_path) as client:
        resp = client.post("/app/fs/mkdir", json={"parent": str(tmp_path), "name": "taken"})
    assert resp.status_code == 409


def _symlink(link: Path, target: Path, *, directory: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"当前环境不能创建软链接: {exc}")


def test_workspace_entries_keep_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    real = root / "real"
    real.mkdir()
    (real / "a.txt").write_text("a", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    _symlink(root / "link", real, directory=True)
    _symlink(root / "gone", root / "missing", directory=False)
    _symlink(root / "out", outside, directory=False)
    with _client(tmp_path) as client:
        listing = client.get("/app/fs/entries", params={"root": str(root)})
        nested = client.get(
            "/app/fs/entries", params={"root": str(root), "path": str(root / "link")}
        )
    assert listing.status_code == 200
    rows = {item["name"]: item for item in listing.json()["entries"]}
    assert rows["link"]["is_symlink"] is True
    assert rows["link"]["is_dir"] is True
    assert rows["link"]["broken"] is False
    assert rows["gone"]["is_symlink"] is True
    assert rows["gone"]["broken"] is True
    assert rows["gone"]["is_dir"] is False
    assert rows["out"]["is_symlink"] is True
    assert rows["out"]["broken"] is False
    assert nested.status_code == 200
    assert nested.json()["entries"][0]["name"] == "a.txt"


def test_workspace_search_skips_noise_and_symlink_dirs(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    (root / "src" / "KeepMe.ts").write_text("x", encoding="utf-8")
    modules = root / "node_modules" / "pkg"
    modules.mkdir(parents=True)
    (modules / "KeepMe.ts").write_text("x", encoding="utf-8")
    with _client(tmp_path) as client:
        found = client.get("/app/fs/search", params={"root": str(root), "q": "keepme"})
        empty = client.get("/app/fs/search", params={"root": str(root), "q": "   "})
    assert found.status_code == 200
    paths = [item["path"] for item in found.json()["matches"]]
    assert paths == ["src/KeepMe.ts"]
    assert found.json()["truncated"] is False
    assert empty.json() == {"matches": [], "truncated": False}
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "KeepMe.ts").write_text("x", encoding="utf-8")
    _symlink(root / "alias", linked, directory=True)
    with _client(tmp_path) as client:
        again = client.get("/app/fs/search", params={"root": str(root), "q": "keepme"})
    assert [item["path"] for item in again.json()["matches"]] == ["src/KeepMe.ts"]


def test_workspace_upload_and_reject_escape(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    (root / "src").mkdir()
    with _client(tmp_path) as client:
        ok = client.post(
            "/app/fs/upload",
            data={
                "root": str(root),
                "directory": str(root / "src"),
                "relpaths": ["nested/note.txt", "plain.txt"],
            },
            files=[
                ("files", ("note.txt", b"hello", "text/plain")),
                ("files", ("plain.txt", b"x", "text/plain")),
            ],
        )
        escaped = client.post(
            "/app/fs/upload",
            data={"root": str(root), "directory": str(root), "relpaths": ["../secret.txt"]},
            files=[("files", ("secret.txt", b"nope", "text/plain"))],
        )
    assert ok.status_code == 200
    assert ok.json()["count"] == 2
    assert (root / "src" / "nested" / "note.txt").read_bytes() == b"hello"
    assert (root / "src" / "plain.txt").read_bytes() == b"x"
    assert escaped.status_code == 400
    assert not (tmp_path / "secret.txt").exists()


async def test_watch_reports_directory_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import nono_app_server.features.files.api as filesystem

    monkeypatch.setattr(filesystem, "_WATCH_POLL_S", 0.01)
    root = tmp_path / "ws"
    root.mkdir()

    class Request:
        def __init__(self) -> None:
            self.calls = 0

        async def is_disconnected(self) -> bool:
            self.calls += 1
            return self.calls > 4

    response = await filesystem.watch_workspace_directories(
        filesystem.WatchBody(root=str(root), paths=[str(root), str(tmp_path / "missing")]),
        Request(),  # type: ignore[arg-type]
    )
    chunks = response.body_iterator
    assert await anext(chunks) == ": ok\n\n"
    (root / "created.txt").write_text("hi", encoding="utf-8")
    change = await anext(chunks)
    await chunks.aclose()
    assert "event: change" in change
    assert str(root).replace("\\", "\\\\") in change
