"""用 PyInstaller 把 Python sidecar 打成 Windows onedir 可执行目录。"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = ROOT / "apps" / "server"
ENTRY = SERVER_ROOT / "packaging" / "sidecar_entry.py"
TIKTOKEN_HOOK = SERVER_ROOT / "packaging" / "tiktoken_runtime_hook.py"
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
TIKTOKEN_HOOK = str(SERVER_ROOT / "packaging" / "tiktoken_runtime_hook.py")

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

# tiktoken 的 BPE 词表：构建期预取、随包分发，运行期由 runtime hook 指向这里。
# 目录可能为空（构建期断网），仍然要建出来 —— 空目录进包不影响启动。
_TIKTOKEN_CACHE = r"{tiktoken_cache}"
datas.append((_TIKTOKEN_CACHE, "tiktoken_cache"))

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
    runtime_hooks=[TIKTOKEN_HOOK],
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
    # token 估算走 tiktoken；regex 是它的快路径依赖。
    "tiktoken",
    "regex",
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
    "tiktoken",
)

# tiktoken 的编码名 → 词表在缓存里的文件名（sha1(blobpath)），见 tiktoken.load.read_file_cached。
TIKTOKEN_ENCODINGS = {
    "cl100k_base": "9b5ad71b2ce5302211f9c61530b329a4922fc6a4",
}


def _tiktoken_cache(target: Path) -> Path:
    """把 cl100k_base 词表预取到 ``target``，供离线运行。

    词表不在 wheel 里，只能在构建期联网下一份打进包里；运行期由
    ``packaging/tiktoken_runtime_hook.py`` 把 TIKTOKEN_CACHE_DIR 指到这里。
    构建期拿不到（断网）就跳过 —— 运行期会自动退回 len//4 启发式，
    不会因此让打包失败。
    """
    target.mkdir(parents=True, exist_ok=True)
    name = TIKTOKEN_ENCODINGS["cl100k_base"]
    cached = target / name
    if cached.is_file() and cached.stat().st_size > 0:
        return target
    try:
        import tiktoken

        # 触发一次 get_encoding，tiktoken 会把词表写进它自己的缓存目录。
        tiktoken.get_encoding("cl100k_base")
    except Exception as exc:  # 断网 / 代理不通
        print(f"  ! tiktoken 词表预取失败，打包后离线将退回估算：{exc}")
        return target
    for directory in (
        os.environ.get("TIKTOKEN_CACHE_DIR"),
        os.path.join(tempfile.gettempdir(), "data-gym-cache"),
    ):
        if not directory:
            continue
        source = Path(directory) / name
        if source.is_file():
            shutil.copyfile(source, cached)
            print(f"  + tiktoken 词表已随包分发 ({cached.stat().st_size} bytes)")
            return target
    print("  ! 未找到 tiktoken 缓存文件，打包后离线将退回估算")
    return target

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


def _report_tiktoken_cache(dist_root: Path) -> None:
    """报告词表是否进了包 —— 缺了不致命（运行期会退回估算），但要让人知道。"""
    vocab = list(dist_root.rglob(TIKTOKEN_ENCODINGS["cl100k_base"]))
    if vocab:
        print(f"  + tiktoken 词表随包分发: {vocab[0]}")
    else:
        print("  ! sidecar 里没有 tiktoken 词表：离线运行将退回 ~4 字符/token 估算")


def _force_utf8_console() -> None:
    """让中文输出在任何代码页下都不炸。

    stdout/stderr 的编码是跟着系统 locale 走的：中文 Windows 是 cp936，中文编得下，
    所以这个脚本本地一直没事；但 GitHub 的 windows runner 默认代码页是 cp1252，
    那里 print 一个中文字面量就会抛 UnicodeEncodeError，把整个构建打断。

    必须在任何输出之前换掉流，而且 errors="replace" 兜底 —— 宁可让某个字显示成
    问号，也不能让一次编码错误把 PyInstaller 弄挂。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # 流被重定向成了不支持 reconfigure 的对象。顶多输出难看，不该中断构建。
            pass


def main() -> None:
    _force_utf8_console()

    if not ENTRY.is_file():
        raise SystemExit(f"missing sidecar entry: {ENTRY}")

    WORK.mkdir(parents=True, exist_ok=True)
    DIST.mkdir(parents=True, exist_ok=True)
    tiktoken_cache = _tiktoken_cache(WORK / "tiktoken_cache")
    SPEC.write_text(
        SPEC_TEMPLATE.format(
            root=str(ROOT),
            packages=PACKAGES,
            metadata=METADATA,
            winpty_binaries=_winpty_binaries(),
            tiktoken_cache=str(tiktoken_cache),
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
    _report_tiktoken_cache(exe.parent)
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
