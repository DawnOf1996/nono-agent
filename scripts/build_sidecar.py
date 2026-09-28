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

# pywinpty 的 _winpty.pyd 用 LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR 加载同目录的 conpty.dll，
# 而 conpty.dll 再启动旁边的 OpenConsole.exe。这两个 exe 不在导入表里，必须显式放进 winpty/。
_WINPTY_FILES = {winpty_binaries!r}
for src, dest in _WINPTY_FILES:
    binaries.append((src, dest))
if _WINPTY_FILES:
    hiddenimports.append("winpty")

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

# 必须和 _winpty.pyd 落在同一目录，conpty.dll 只在自己旁边找 OpenConsole.exe。
_WINPTY_BINARIES = (
    "conpty.dll",
    "winpty.dll",
    "OpenConsole.exe",
    "winpty-agent.exe",
)


def _winpty_binaries() -> list[tuple[str, str]]:
    if os.name != "nt":
        return []
    import winpty

    directory = Path(winpty.__file__).resolve().parent
    missing = [name for name in _WINPTY_BINARIES if not (directory / name).is_file()]
    if missing:
        raise SystemExit(
            f"pywinpty 缺少打包文件: {', '.join(missing)} ({directory})"
        )
    return [(str(directory / name), "winpty") for name in _WINPTY_BINARIES]


def _require_winpty_layout(dist_root: Path) -> None:
    if os.name != "nt":
        return
    dlls = list(dist_root.rglob("conpty.dll"))
    hosts = list(dist_root.rglob("OpenConsole.exe"))
    agents = list(dist_root.rglob("winpty-agent.exe"))
    paired = any(dll.parent == host.parent for dll in dlls for host in hosts)
    if not paired or not agents:
        raise SystemExit(
            "sidecar 里 conpty.dll 与 OpenConsole.exe 不在同一目录，打包后的终端无法启动"
        )


def main() -> None:
    if not ENTRY.is_file():
        raise SystemExit(f"missing sidecar entry: {ENTRY}")

    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    SPEC.write_text(
        SPEC_TEMPLATE.format(
            root=str(ROOT),
            packages=PACKAGES,
            metadata=METADATA,
            winpty_binaries=_winpty_binaries(),
        ),
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
    _require_winpty_layout(exe.parent)
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
