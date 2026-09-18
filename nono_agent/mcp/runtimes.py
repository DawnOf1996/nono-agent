"""共享托管运行时 —— 一套 uv、一套 Python、一套 Node，落在用户数据目录。

stdio 命令若是 ``python`` / ``uv`` / ``uvx`` / ``node`` / ``npm`` / ``npx``，
走这里，不借用系统全局安装。写了绝对路径则仍用用户指定的程序。
HTTP MCP 不需要这层。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tarfile
import threading
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from nono_agent.paths import user_home
from nono_agent.utils.process import hidden_kwargs

logger = logging.getLogger(__name__)

PYTHON_VERSION = "3.12"
NODE_MAJOR = 22
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_DIST_BASE = "https://nodejs.org/dist/"
UV_DIST_BASE = "https://github.com/astral-sh/uv/releases/latest/download/"

ProgressFn = Callable[[str, int | None], None]
_SETUP_LOCK = threading.RLock()

HOSTED_STEMS: dict[str, str] = {
    "python": "python",
    "python3": "python",
    "py": "python",
    "uv": "uv",
    "uvx": "uvx",
    "node": "node",
    "npm": "npm",
    "npx": "npx",
}


class MCPRuntimeSetupError(RuntimeError):
    """托管运行时无法准备。"""


@dataclass
class HostedRuntimes:
    """已解析的共享解释器路径。"""

    python: Path | None = None
    node: Path | None = None
    uv: Path | None = None
    errors: dict[str, str] = field(default_factory=dict)

    def as_status_lines(self) -> list[str]:
        lines = ["Hosted runtimes:"]
        lines.append(f"- uv: {self.uv or 'missing'}")
        lines.append(f"- python: {self.python or 'missing'}")
        lines.append(f"- node: {self.node or 'missing'}")
        for name, err in self.errors.items():
            lines.append(f"- {name} error: {err}")
        return lines


def runtimes_root(override: Path | None = None) -> Path:
    """``~/.nono-agent/runtimes``，或测试注入的目录。"""
    return Path(override) if override is not None else user_home() / "runtimes"


def python_executable(root: Path | None = None) -> Path | None:
    venv = runtimes_root(root) / "python"
    for candidate in (venv / "Scripts" / "python.exe", venv / "bin" / "python"):
        if candidate.is_file():
            return candidate
    return None


def uv_home(root: Path | None = None) -> Path:
    return runtimes_root(root) / "uv"


def uv_executable(root: Path | None = None) -> Path | None:
    home = uv_home(root)
    for candidate in (home / "uv.exe", home / "uv"):
        if candidate.is_file():
            return candidate
    return None


def uvx_executable(root: Path | None = None) -> Path | None:
    home = uv_home(root)
    for candidate in (home / "uvx.exe", home / "uvx"):
        if candidate.is_file():
            return candidate
    return None


def node_home(root: Path | None = None) -> Path:
    return runtimes_root(root) / "node"


def node_executable(root: Path | None = None) -> Path | None:
    home = node_home(root)
    for candidate in (home / "node.exe", home / "bin" / "node", home / "node"):
        if candidate.is_file():
            return candidate
    return None


def node_tool(name: str, root: Path | None = None) -> Path | None:
    """托管 Node 目录里的 ``node`` / ``npm`` / ``npx``。"""
    home = node_home(root)
    names = _node_tool_names(name)
    search_dirs = [home, home / "bin"]
    for directory in search_dirs:
        for candidate_name in names:
            candidate = directory / candidate_name
            if candidate.is_file():
                return candidate
    return None


def inspect_hosted_runtimes(root: Path | None = None) -> HostedRuntimes:
    return HostedRuntimes(
        python=python_executable(root),
        node=node_executable(root),
        uv=uv_executable(root),
    )


def ensure_hosted_runtimes(
    root: Path | None = None,
    *,
    python: bool = True,
    node: bool = True,
    uv: bool = True,
) -> HostedRuntimes:
    """准备共享 uv / Python / Node。已存在则跳过。单个失败不阻断另一个。"""
    result = HostedRuntimes()
    if uv:
        try:
            result.uv = ensure_uv_runtime(root)
        except Exception as exc:
            logger.warning("Failed to prepare hosted uv: %s", exc)
            result.errors["uv"] = str(exc)
            result.uv = uv_executable(root)
    else:
        result.uv = uv_executable(root)
    if python:
        try:
            result.python = ensure_python_runtime(root)
        except Exception as exc:
            logger.warning("Failed to prepare hosted Python: %s", exc)
            result.errors["python"] = str(exc)
            result.python = python_executable(root)
    else:
        result.python = python_executable(root)
    if node:
        try:
            result.node = ensure_node_runtime(root)
        except Exception as exc:
            logger.warning("Failed to prepare hosted Node: %s", exc)
            result.errors["node"] = str(exc)
            result.node = node_executable(root)
    else:
        result.node = node_executable(root)
    return result


def ensure_uv_runtime(
    root: Path | None = None, *, progress: ProgressFn | None = None
) -> Path:
    """下载官方 uv 独立包到 ``runtimes/uv``（含 uvx）。"""
    existing = uv_executable(root)
    if existing is not None:
        return existing
    with _SETUP_LOCK:
        existing = uv_executable(root)
        if existing is not None:
            return existing
        dest = uv_home(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        filename, archive_kind = _uv_asset()
        url = urljoin(UV_DIST_BASE, filename)
        staging = dest.parent / ".uv-staging"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        archive = staging / filename
        try:
            _emit(progress, "正在下载内置 uv…")
            _download(url, archive, progress=progress, label="uv")
            _emit(progress, "正在解压 uv…")
            _extract_archive(archive, staging, archive_kind)
            unpacked = _find_unpacked_uv(staging)
            if dest.exists():
                shutil.rmtree(dest)
            dest.mkdir(parents=True)
            for child in unpacked.iterdir():
                if child.is_file() and child.stem.lower() in {"uv", "uvx"}:
                    shutil.copy2(child, dest / child.name)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        created = uv_executable(root)
        if created is None:
            raise MCPRuntimeSetupError(f"uv extracted but executable is missing in {dest}")
        return created


def ensure_python_runtime(
    root: Path | None = None, *, progress: ProgressFn | None = None
) -> Path:
    """用托管 uv 在 ``runtimes/python`` 建隔离 venv（解释器由 uv 管理）。"""
    existing = python_executable(root)
    if existing is not None:
        return existing
    with _SETUP_LOCK:
        existing = python_executable(root)
        if existing is not None:
            return existing
        dest = runtimes_root(root) / "python"
        dest.parent.mkdir(parents=True, exist_ok=True)
        uv = str(ensure_uv_runtime(root, progress=progress))
        _emit(progress, f"正在安装内置 Python {PYTHON_VERSION}…")
        _run(
            [uv, "venv", str(dest), "--python", PYTHON_VERSION],
            what="hosted Python venv",
            env=apply_hosted_env(os.environ.copy(), root),
        )
        created = python_executable(root)
        if created is None:
            raise MCPRuntimeSetupError(
                f"uv venv succeeded but python is missing in {dest}"
            )
        return created


def ensure_node_runtime(
    root: Path | None = None, *, progress: ProgressFn | None = None
) -> Path:
    """下载官方 Node 发行包到 ``runtimes/node``（含 npm/npx）。"""
    existing = node_executable(root)
    if existing is not None:
        return existing
    with _SETUP_LOCK:
        existing = node_executable(root)
        if existing is not None:
            return existing
        dest = node_home(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        version, filename, archive_kind = _resolve_node_asset()
        url = urljoin(NODE_DIST_BASE, f"v{version}/{filename}")
        staging = dest.parent / f".node-staging-{version}"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        archive = staging / filename
        try:
            _emit(progress, f"正在下载内置 Node.js {version}…")
            _download(url, archive, progress=progress, label="Node.js")
            _emit(progress, "正在解压 Node.js…")
            _extract_archive(archive, staging, archive_kind)
            unpacked = _find_unpacked_node(staging)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.move(str(unpacked), str(dest))
        finally:
            shutil.rmtree(staging, ignore_errors=True)
        created = node_executable(root)
        if created is None:
            raise MCPRuntimeSetupError(
                f"Node {version} extracted but executable is missing in {dest}"
            )
        (dest / ".nono-node-version").write_text(version, encoding="utf-8")
        return created


def command_stem(command: str) -> str:
    """``uvx.exe`` / ``npx.cmd`` → ``uvx`` / ``npx``。"""
    name = Path(command).name.lower()
    for suffix in (".exe", ".cmd", ".bat", ".ps1"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def is_explicit_path(command: str) -> bool:
    """绝对路径或带分隔符的相对路径：用户指定了具体程序。"""
    text = command.strip()
    if not text:
        return False
    if Path(text).is_absolute():
        return True
    return "/" in text or "\\" in text


def hosted_path_dirs(root: Path | None = None) -> list[Path]:
    base = runtimes_root(root)
    return [
        uv_home(root),
        base / "python" / "Scripts",
        base / "python" / "bin",
        node_home(root),
        node_home(root) / "bin",
        base / "uv-tools" / "bin",
    ]


def apply_hosted_env(env: dict[str, str], root: Path | None = None) -> dict[str, str]:
    """给托管进程加上隔离缓存目录，并把内置 bin 插到 PATH 前面。"""
    out = dict(env)
    base = runtimes_root(root)
    extras = {
        "UV_CACHE_DIR": str(base / "uv-cache"),
        "UV_PYTHON_INSTALL_DIR": str(base / "python-versions"),
        "UV_TOOL_DIR": str(base / "uv-tools"),
        "UV_TOOL_BIN_DIR": str(base / "uv-tools" / "bin"),
        "npm_config_cache": str(base / "npm-cache"),
    }
    for key, value in extras.items():
        if key not in out:
            Path(value).mkdir(parents=True, exist_ok=True)
            out[key] = value
    # uvx / uv tool install 默认会拉最新 CPython（目前是 3.14）。
    # 必须覆盖环境里的 UV_PYTHON，否则宿主进程的设置会把托管运行时带偏。
    out["UV_PYTHON"] = PYTHON_VERSION
    prefix = os.pathsep.join(str(path) for path in hosted_path_dirs(root) if path.is_dir())
    if prefix:
        path_key = next((key for key in out if key.upper() == "PATH"), "PATH")
        old = out.get(path_key, "")
        out[path_key] = os.pathsep.join(item for item in (prefix, old) if item)
    return out


def hosted_kind_for_command(command: str) -> str | None:
    """裸命令对应的托管工具；绝对路径返回 None。"""
    text = command.strip()
    if not text or is_explicit_path(text):
        return None
    return HOSTED_STEMS.get(command_stem(text))


def first_package_arg(args: list[str]) -> str | None:
    """从 uvx/npx 参数里取出要预取的包名。"""
    takes_value = {"--from", "--package", "--python", "--with", "-p"}
    package_keys = {"--from", "--package"}
    i = 0
    while i < len(args):
        item = args[i]
        if item in {"-y", "--yes", "-q", "--quiet", "-n"}:
            i += 1
            continue
        if item in takes_value:
            if item in package_keys and i + 1 < len(args):
                value = args[i + 1].strip()
                return value or None
            i += 2
            continue
        if item.startswith("-"):
            i += 1
            continue
        if not item.strip():
            i += 1
            continue
        if item.startswith("@"):
            return item
        if "/" in item or "\\" in item or item.lower().endswith(
            (".py", ".js", ".mjs", ".cjs", ".ts")
        ):
            return None
        return item
    return None


def prepare_stdio_runtime(
    command: str,
    args: list[str] | None = None,
    *,
    root: Path | None = None,
    progress: ProgressFn | None = None,
    prefetch: bool = True,
) -> dict[str, Any]:
    """配置 MCP 时准备托管运行时，并尽量预取 uvx/npx 包。"""
    text = command.strip()
    if not text:
        raise MCPRuntimeSetupError("stdio MCP 需要填写 command")
    kind = hosted_kind_for_command(text)
    if kind is None:
        resolved = _verify_external_command(text)
        _emit(progress, "命令已就绪")
        return {
            "ok": True,
            "hosted": False,
            "kind": None,
            "executable": resolved,
        }
    _emit(progress, f"正在准备内置 {kind} 运行时…")
    executable = str(_resolve_hosted_tool(kind, root, progress=progress))
    if prefetch:
        _prefetch_packages(kind, list(args or []), root=root, progress=progress)
    _emit(progress, "运行时已就绪", 100)
    return {
        "ok": True,
        "hosted": True,
        "kind": kind,
        "executable": executable,
    }


def _verify_external_command(command: str) -> str:
    path = Path(command)
    if path.is_file():
        return str(path)
    found = shutil.which(command)
    if found:
        return found
    raise MCPRuntimeSetupError(f"找不到命令 {command}")


def _prefetch_packages(
    kind: str,
    args: list[str],
    *,
    root: Path | None,
    progress: ProgressFn | None,
) -> None:
    package = first_package_arg(args)
    if not package:
        return
    env = apply_hosted_env(os.environ.copy(), root)
    if kind == "uvx":
        uv = uv_executable(root)
        if uv is None:
            return
        _emit(progress, f"正在预取 Python 包 {package}…")
        _run(
            [
                str(uv),
                "tool",
                "install",
                "--quiet",
                "--python",
                PYTHON_VERSION,
                package,
            ],
            what=f"uv tool install {package}",
            env=env,
        )
        return
    if kind == "npx":
        npx = node_tool("npx", root)
        if npx is None:
            return
        _emit(progress, f"正在预取 npm 包 {package}…")
        _run(
            [str(npx), "--yes", "--package", package, "node", "-e", "process.exit(0)"],
            what=f"npx prefetch {package}",
            env=env,
        )


def rewrite_stdio_command(command: str, *, root: Path | None = None) -> tuple[str, bool]:
    """裸 ``python`` / ``uvx`` / ``npx`` 等换成托管可执行文件。

    绝对路径保持原样。托管运行时尚未准备好时退回原命令。
    """
    text = command.strip()
    if not text or is_explicit_path(text):
        return command, False
    kind = HOSTED_STEMS.get(command_stem(text))
    if kind is None:
        return command, False
    try:
        return str(_resolve_hosted_tool(kind, root)), True
    except MCPRuntimeSetupError as exc:
        logger.warning("Hosted runtime for %s unavailable (%s); using PATH %s", kind, exc, command)
        return command, False


def _resolve_hosted_tool(
    kind: str, root: Path | None, *, progress: ProgressFn | None = None
) -> Path:
    if kind == "python":
        return ensure_python_runtime(root, progress=progress)
    if kind in {"uv", "uvx"}:
        ensure_uv_runtime(root, progress=progress)
        exe = uv_executable(root) if kind == "uv" else uvx_executable(root)
        if exe is None:
            raise MCPRuntimeSetupError(f"hosted {kind} is missing")
        return exe
    if kind in {"node", "npm", "npx"}:
        ensure_node_runtime(root, progress=progress)
        exe = node_tool(kind, root)
        if exe is None:
            raise MCPRuntimeSetupError(f"hosted {kind} is missing")
        return exe
    raise MCPRuntimeSetupError(f"unsupported hosted tool {kind}")


def _emit(progress: ProgressFn | None, message: str, percent: int | None = None) -> None:
    if progress is not None:
        progress(message, percent)


def _run(
    argv: list[str],
    *,
    what: str,
    env: dict[str, str] | None = None,
) -> None:
    import subprocess

    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=900,
            **hidden_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise MCPRuntimeSetupError(f"Timed out while preparing {what}") from exc
    except OSError as exc:
        raise MCPRuntimeSetupError(f"Failed to start {what}: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise MCPRuntimeSetupError(f"Failed to prepare {what}: {detail or argv}")


def _node_platform() -> str:
    machine = os.environ.get("PROCESSOR_ARCHITECTURE", "").lower()
    if sys.platform == "win32":
        wow64 = os.environ.get("PROCESSOR_ARCHITEW6432", "").lower()
        if machine in {"arm64", "aarch64"} or wow64 in {"arm64", "aarch64"}:
            return "win-arm64"
        return "win-x64"
    if sys.platform == "darwin":
        return "darwin-arm64" if os.uname().machine == "arm64" else "darwin-x64"
    if os.uname().machine in {"aarch64", "arm64"}:
        return "linux-arm64"
    return "linux-x64"


def _uv_asset() -> tuple[str, str]:
    """返回 ``(filename, zip|tar)``。"""
    platform = _node_platform()
    mapping = {
        "win-x64": ("uv-x86_64-pc-windows-msvc.zip", "zip"),
        "win-arm64": ("uv-aarch64-pc-windows-msvc.zip", "zip"),
        "darwin-x64": ("uv-x86_64-apple-darwin.tar.gz", "tar"),
        "darwin-arm64": ("uv-aarch64-apple-darwin.tar.gz", "tar"),
        "linux-x64": ("uv-x86_64-unknown-linux-gnu.tar.gz", "tar"),
        "linux-arm64": ("uv-aarch64-unknown-linux-gnu.tar.gz", "tar"),
    }
    asset = mapping.get(platform)
    if asset is None:
        raise MCPRuntimeSetupError(f"No uv build for platform {platform}")
    return asset


def _resolve_node_asset() -> tuple[str, str, str]:
    """返回 ``(version, filename, zip|tar)``。"""
    import httpx

    try:
        response = httpx.get(NODE_INDEX_URL, timeout=30.0, follow_redirects=True)
        response.raise_for_status()
        rows: Any = response.json()
    except Exception as exc:
        raise MCPRuntimeSetupError(f"Failed to query Node releases: {exc}") from exc
    if not isinstance(rows, list):
        raise MCPRuntimeSetupError("Unexpected Node release index.")
    version = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        raw = str(row.get("version") or "").lstrip("v")
        if raw.startswith(f"{NODE_MAJOR}."):
            version = raw
            break
    if not version:
        raise MCPRuntimeSetupError(f"No Node {NODE_MAJOR}.x release found.")
    platform = _node_platform()
    if platform.startswith("win-"):
        return version, f"node-v{version}-{platform}.zip", "zip"
    return version, f"node-v{version}-{platform}.tar.gz", "tar"


def _download(
    url: str,
    dest: Path,
    *,
    progress: ProgressFn | None = None,
    label: str = "文件",
) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, timeout=300.0, follow_redirects=True) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length") or 0)
            done = 0
            last_percent = -1
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
                    done += len(chunk)
                    percent = int(done * 99 / total) if total else None
                    if percent is not None and percent < last_percent + 2 and done < total:
                        continue
                    if percent is not None:
                        last_percent = percent
                    size = _format_bytes(done)
                    suffix = f" / {_format_bytes(total)}" if total else ""
                    _emit(progress, f"正在下载 {label}（{size}{suffix}）…", percent)
    except Exception as exc:
        raise MCPRuntimeSetupError(f"Failed to download {url}: {exc}") from exc


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _extract_archive(archive: Path, dest: Path, kind: str) -> None:
    if kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(dest)
        return
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(dest)


def _find_unpacked_node(staging: Path) -> Path:
    for child in staging.iterdir():
        if child.is_dir() and child.name.startswith("node-v"):
            return child
    raise MCPRuntimeSetupError("Downloaded Node archive had no node-v* directory.")


def _find_unpacked_uv(staging: Path) -> Path:
    names = {"uv", "uv.exe", "uvx", "uvx.exe"}
    for child in staging.rglob("*"):
        if child.is_file() and child.name.lower() in names:
            return child.parent
    raise MCPRuntimeSetupError("Downloaded uv archive had no uv executable.")


def _node_tool_names(name: str) -> list[str]:
    base = name.lower().removesuffix(".cmd").removesuffix(".exe")
    if sys.platform == "win32":
        if base == "node":
            return ["node.exe", "node"]
        return [f"{base}.cmd", f"{base}.exe", base]
    return [base]
