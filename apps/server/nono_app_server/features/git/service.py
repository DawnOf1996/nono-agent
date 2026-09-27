"""仓库发现、状态解析，以及暂存、提交、历史这些 git 操作。"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from nono_app_server.common.gitcmd import (
    GitError,
    path_identity,
    repo_toplevel,
    run_git,
)
from nono_app_server.common.paths import (
    within_root as _within_root,
)
from nono_app_server.common.paths import (
    workspace_root as _workspace_root,
)
from nono_app_server.common.textdiff import (
    decode_text,
    is_binary,
)

DISCOVERY_LIMIT = 200

DISCOVERY_TTL_S = 30.0

STATUS_LIMIT = 2000

UNTRACKED_COUNT_LIMIT = 200

UNTRACKED_COUNT_MAX_BYTES = 512 * 1024

DIFF_MAX_BYTES = 2 * 1024 * 1024

COMMIT_FILES_LIMIT = 300

MAX_CONTEXT = 100_000

_HASH_RE = re.compile(r"^[0-9a-fA-F]{4,64}$")

_discovery_cache: dict[str, tuple[float, list[Path]]] = {}


def _git_http_error(exc: GitError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc) or "git 操作失败")


async def _discover(workspace: Path) -> list[Path]:
    """工作区所在的仓库；工作区本身不是仓库时，找一层子目录里的仓库。"""
    key = path_identity(workspace)
    cached = _discovery_cache.get(key)
    now = time.monotonic()
    if cached is not None and cached[0] > now:
        return cached[1]
    top = await repo_toplevel(workspace)
    roots: list[Path] = []
    if top is not None:
        roots = [top]
    else:
        try:
            children = sorted(
                (child for child in workspace.iterdir() if child.is_dir()),
                key=lambda item: item.name.casefold(),
            )
        except OSError:
            children = []
        for child in children[:DISCOVERY_LIMIT]:
            if child.name.startswith(".") or child.name == "node_modules":
                continue
            if not (child / ".git").exists():
                continue
            found = await repo_toplevel(child)
            if found is not None and all(
                path_identity(found) != path_identity(item) for item in roots
            ):
                roots.append(found)
    _discovery_cache[key] = (now + DISCOVERY_TTL_S, roots)
    return roots


def _parse_worktrees(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    sep = "\0" if "\0" in output else "\n"
    for line in output.split(sep):
        if line == "":
            if current is not None:
                rows.append(current)
            current = None
            continue
        if line.startswith("worktree "):
            if current is not None:
                rows.append(current)
            current = {
                "path": line[len("worktree ") :],
                "branch": "HEAD",
                "locked": False,
                "prunable": False,
                "bare": False,
            }
            continue
        if current is None:
            continue
        if line.startswith("branch refs/heads/"):
            current["branch"] = line[len("branch refs/heads/") :]
        elif line == "bare":
            current["bare"] = True
        elif line == "locked" or line.startswith("locked "):
            current["locked"] = True
        elif line == "prunable" or line.startswith("prunable "):
            current["prunable"] = True
    if current is not None:
        rows.append(current)
    return [row for row in rows if not row["prunable"] and not row["bare"]]


async def _list_worktrees(repo: Path) -> list[dict[str, Any]]:
    try:
        result = await run_git(repo, "worktree", "list", "--porcelain", "-z", timeout=10)
    except GitError:
        try:
            result = await run_git(repo, "worktree", "list", "--porcelain", timeout=10)
        except GitError:
            return []
    return _parse_worktrees(result.text)


async def _resolve_repo(
    root_raw: str, repo_raw: str | None
) -> tuple[Path, Path | None, list[Path]]:
    workspace = _workspace_root(root_raw)
    roots = await _discover(workspace)
    if not roots:
        return workspace, None, []
    if not repo_raw or not repo_raw.strip():
        return workspace, roots[0], roots
    wanted = path_identity(repo_raw)
    for root in roots:
        if path_identity(root) == wanted:
            return workspace, root, roots
    for root in roots:
        for tree in await _list_worktrees(root):
            if path_identity(tree["path"]) == wanted:
                return workspace, Path(tree["path"]), roots
    raise HTTPException(status_code=403, detail="这个目录不属于当前工作区的仓库")


async def _require_repo(root_raw: str, repo_raw: str | None) -> Path:
    _workspace, repo, _roots = await _resolve_repo(root_raw, repo_raw)
    if repo is None:
        raise HTTPException(status_code=400, detail="当前工作区不是 Git 仓库")
    return repo


def _repo_relative(repo: Path, raw: str) -> str:
    """把客户端给的仓库内相对路径规整成 git 路径，拒绝越界。"""
    cleaned = raw.replace("\\", "/").strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="缺少文件路径")
    candidate = Path(cleaned)
    target = candidate if candidate.is_absolute() else repo / candidate
    try:
        resolved = target.resolve()
    except OSError as exc:
        raise HTTPException(status_code=400, detail="路径无效") from exc
    repo_resolved = repo.resolve()
    if not _within_root(repo_resolved, resolved):
        raise HTTPException(status_code=403, detail="路径超出仓库")
    relative = resolved.relative_to(repo_resolved) if resolved != repo_resolved else Path(".")
    return relative.as_posix()


def _parse_status_v2(raw: bytes) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    branch: dict[str, Any] = {
        "head": None,
        "branch": None,
        "upstream": None,
        "ahead": 0,
        "behind": 0,
    }
    entries: list[dict[str, Any]] = []
    tokens = raw.split(b"\0")
    index = 0
    while index < len(tokens):
        token = tokens[index].decode("utf-8", errors="replace")
        index += 1
        if not token:
            continue
        if token.startswith("# "):
            parts = token[2:].split(" ", 1)
            name, value = parts[0], parts[1] if len(parts) > 1 else ""
            if name == "branch.oid":
                branch["head"] = None if value == "(initial)" else value[:10]
            elif name == "branch.head":
                branch["branch"] = None if value == "(detached)" else value
            elif name == "branch.upstream":
                branch["upstream"] = value
            elif name == "branch.ab":
                for piece in value.split():
                    if piece.startswith("+"):
                        branch["ahead"] = int(piece[1:] or 0)
                    elif piece.startswith("-"):
                        branch["behind"] = int(piece[1:] or 0)
            continue
        kind = token[0]
        if kind == "1":
            parts = token.split(" ", 8)
            if len(parts) == 9:
                entries.append({"xy": parts[1], "path": parts[8], "orig_path": None})
        elif kind == "2":
            parts = token.split(" ", 9)
            orig = tokens[index].decode("utf-8", errors="replace") if index < len(tokens) else None
            index += 1
            if len(parts) == 10:
                entries.append({"xy": parts[1], "path": parts[9], "orig_path": orig})
        elif kind == "u":
            parts = token.split(" ", 10)
            if len(parts) == 11:
                entries.append(
                    {"xy": parts[1], "path": parts[10], "orig_path": None, "conflicted": True}
                )
        elif kind == "?":
            entries.append({"xy": "??", "path": token[2:], "orig_path": None})
    return branch, entries


def _parse_numstat(raw: bytes) -> dict[str, tuple[int | None, int | None]]:
    stats: dict[str, tuple[int | None, int | None]] = {}
    tokens = raw.split(b"\0")
    index = 0
    while index < len(tokens):
        token = tokens[index].decode("utf-8", errors="replace")
        index += 1
        if not token:
            continue
        parts = token.split("\t", 2)
        if len(parts) < 3:
            continue
        added = None if parts[0] == "-" else int(parts[0] or 0)
        deleted = None if parts[1] == "-" else int(parts[1] or 0)
        path = parts[2]
        if path == "":
            # 重命名：后面跟着原路径与新路径两个字段。
            index += 1
            path = tokens[index].decode("utf-8", errors="replace") if index < len(tokens) else ""
            index += 1
        stats[path] = (added, deleted)
    return stats


def _count_lines(path: Path) -> tuple[int | None, bool]:
    try:
        if path.stat().st_size > UNTRACKED_COUNT_MAX_BYTES:
            return None, False
        data = path.read_bytes()
    except OSError:
        return None, False
    if is_binary(data):
        return None, True
    text = decode_text(data)
    if text is None:
        return None, True
    return len(text.splitlines()), False


def _stat_dict(stat: tuple[int | None, int | None] | None) -> dict[str, Any] | None:
    if stat is None:
        return None
    added, deleted = stat
    if added is None or deleted is None:
        return {"added": 0, "deleted": 0, "binary": True}
    return {"added": added, "deleted": deleted, "binary": False}


async def _is_untracked(repo: Path, rel: str) -> bool:
    result = await run_git(
        repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", rel
    )
    return any(token.startswith(b"??") for token in result.stdout.split(b"\0"))


async def _has_head(repo: Path) -> bool:
    try:
        await run_git(repo, "rev-parse", "--verify", "-q", "HEAD", timeout=10)
        return True
    except GitError:
        return False


def _paths_args(repo: Path, paths: list[str] | None) -> list[str]:
    if paths is None:
        return ["--", "."]
    rels = [_repo_relative(repo, item) for item in paths]
    if not rels:
        raise HTTPException(status_code=400, detail="没有选择文件")
    return ["--", *rels]


def _parse_log(output: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in output.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x1f")
        if len(parts) < 6:
            continue
        rows.append(
            {
                "hash": parts[0],
                "hash_full": parts[1],
                "subject": parts[2],
                "author": parts[3],
                "date": parts[4],
                "refs": parts[5],
            }
        )
    return rows
