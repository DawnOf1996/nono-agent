"""用 PyInstaller 把 Python sidecar 打成 Windows onedir 可执行目录。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "apps" / "server"
ENTRY = SERVER_ROOT / "packaging" / "sidecar_entry.py"
DIST = ROOT / "dist" / "desktop-sidecar"
WORK = ROOT / "build" / "desktop-sidecar"
SPEC = WORK / "nono-app-server.spec"

SPEC_TEMPLATE = r'''# -*- mode: python ; coding: utf-8 -*-
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

ROOT = Path(r"{root}")
SERVER_ROOT = ROOT / "apps" / "server"
ENTRY = str(SERVER_ROOT / "packaging" / "sidecar_entry.py")

PACKAGES = {packages!r}
METADATA = {metadata!r}

hiddenimports: list[str] = [
    "nono_app_server.app",
    "nono_app_server.main",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan.on",
]
datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []

for name in PACKAGES:
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(name)
    except Exception:
        pkg_datas, pkg_binaries, pkg_hidden = [], [], collect_submodules(name)
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hidden)

for name in METADATA:
    try:
        distribution(name)
        datas.extend(copy_metadata(name))
    except PackageNotFoundError:
        pass

# 去重并保持顺序
hiddenimports = list(dict.fromkeys(item for item in hiddenimports if item))
datas = list(dict.fromkeys(datas))
binaries = list(dict.fromkeys(binaries))

a = Analysis(
    [ENTRY],
    pathex=[str(SERVER_ROOT), str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="nono-app-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="nono-app-server",
)
'''

PACKAGES = (
    "nono_agent",
    "nono_app_server",
    "uvicorn",
    "fastapi",
    "starlette",
    "sse_starlette",
    "pydantic",
    "pydantic_core",
    "anthropic",
    "openai",
    "mcp",
    "httpx",
    "httpcore",
    "anyio",
    "h11",
    "yaml",
    "zstandard",
    "structlog",
    "aiofiles",
    "certifi",
    "multipart",
    "jiter",
)

METADATA = (
    "nono-agent",
    "nono-app-server",
    "mcp",
    "openai",
    "anthropic",
    "pydantic",
    "fastapi",
    "starlette",
    "uvicorn",
    "httpx",
)


def main() -> None:
    if not ENTRY.is_file():
        raise SystemExit(f"missing sidecar entry: {ENTRY}")

    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    SPEC.write_text(
        SPEC_TEMPLATE.format(root=str(ROOT), packages=PACKAGES, metadata=METADATA),
        encoding="utf-8",
    )

    from PyInstaller.__main__ import run

    run(
        [
            str(SPEC),
            "--noconfirm",
            "--clean",
            f"--distpath={DIST}",
            f"--workpath={WORK}",
        ]
    )

    exe = DIST / "nono-app-server" / (
        "nono-app-server.exe" if os.name == "nt" else "nono-app-server"
    )
    if not exe.is_file():
        raise SystemExit(f"sidecar exe missing after build: {exe}")
    print(f"sidecar ready: {exe}")


if __name__ == "__main__":
    try:
        main()
    except ModuleNotFoundError as exc:
        if exc.name == "PyInstaller":
            print(
                "需要 PyInstaller：uv run --with pyinstaller python scripts/build_sidecar.py",
                file=sys.stderr,
            )
            raise SystemExit(1) from exc
        raise
