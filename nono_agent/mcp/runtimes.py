"""共享托管运行时 —— 一套 Python、一套 Node，落在用户数据目录。

不借用系统站点包 / 全局 npm。HTTP MCP 不需要这层。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tarfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from nono_agent.paths import user_home

logger = logging.getLogger(__name__)

PYTHON_VERSION = "3.12"
NODE_MAJOR = 22
NODE_INDEX_URL = "https://nodejs.org/dist/index.json"
NODE_DIST_BASE = "https://nodejs.org/dist/"


class MCPRuntimeSetupError(RuntimeError):
    """托管运行时无法准备。"""


@dataclass
class HostedRuntimes:
    """已解析的共享解释器路径。"""

    python: Path | None = None
    node: Path | None = None
    errors: dict[str, str] = field(default_factory=dict)

    def as_status_lines(self) -> list[str]:
        lines = ["Hosted runtimes:"]
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
    return HostedRuntimes(python=python_executable(root), node=node_executable(root))


def ensure_hosted_runtimes(
    root: Path | None = None,
    *,
    python: bool = True,
    node: bool = True,
) -> HostedRuntimes:
    """准备共享 Python / Node。已存在则跳过。单个失败不阻断另一个。"""
    result = HostedRuntimes()
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


def ensure_python_runtime(root: Path | None = None) -> Path:
    """用 uv 在 ``runtimes/python`` 建隔离 venv（解释器由 uv 管理）。"""
    existing = python_executable(root)
    if existing is not None:
        return existing
    dest = runtimes_root(root) / "python"
    dest.parent.mkdir(parents=True, exist_ok=True)
    uv = _find_uv()
    _run(
        [uv, "venv", str(dest), "--python", PYTHON_VERSION],
        what="hosted Python venv",
    )
    created = python_executable(root)
    if created is None:
        raise MCPRuntimeSetupError(f"uv venv succeeded but python is missing in {dest}")
    return created


def ensure_node_runtime(root: Path | None = None) -> Path:
    """下载官方 Node 发行包到 ``runtimes/node``（含 npm/npx）。"""
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
        _download(url, archive)
        _extract_archive(archive, staging, archive_kind)
        unpacked = _find_unpacked_node(staging)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(unpacked), str(dest))
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    created = node_executable(root)
    if created is None:
        raise MCPRuntimeSetupError(f"Node {version} extracted but executable is missing in {dest}")
    (dest / ".nono-node-version").write_text(version, encoding="utf-8")
    return created


def _find_uv() -> str:
    found = shutil.which("uv")
    if found:
        return found
    raise MCPRuntimeSetupError(
        "uv is required to prepare the hosted Python runtime (install uv or put it on PATH)."
    )


def _run(argv: list[str], *, what: str) -> None:
    import subprocess

    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
        )
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


def _download(url: str, dest: Path) -> None:
    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.stream("GET", url, timeout=120.0, follow_redirects=True) as response:
            response.raise_for_status()
            with dest.open("wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
    except Exception as exc:
        raise MCPRuntimeSetupError(f"Failed to download {url}: {exc}") from exc


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


def _node_tool_names(name: str) -> list[str]:
    base = name.lower().removesuffix(".cmd").removesuffix(".exe")
    if sys.platform == "win32":
        if base == "node":
            return ["node.exe", "node"]
        return [f"{base}.cmd", f"{base}.exe", base]
    return [base]
