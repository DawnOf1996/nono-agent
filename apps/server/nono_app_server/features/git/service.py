"""仓库发现、状态解析，以及暂存、提交、历史这些 git 操作。"""

from __future__ import annotations

import hashlib
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


# ── 推送用的状态快照 ───────────────────────────────────────────────────
# 下面两个 payload 同时供一次性 HTTP 读取和 SSE 推送使用：走同一条实现，
# 客户端从轮询切到推送后，看到的数据不会因为换了条路而变形。


async def status_payload(root_raw: str, repo_raw: str | None) -> dict[str, Any]:
    """工作树状态：暂存区与工作区两侧的改动，含未跟踪文件和行数统计。"""
    workspace, selected, roots = await _resolve_repo(root_raw, repo_raw)
    if selected is None:
        return {
            "is_repo": False,
            "workspace": str(workspace),
            "repo": None,
            "repositories": [],
            "branch": None,
            "head": None,
            "upstream": None,
            "ahead": 0,
            "behind": 0,
            "entries": [],
            "truncated": False,
        }
    try:
        status = await run_git(
            selected, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all"
        )
        unstaged = await run_git(selected, "diff", "--no-ext-diff", "--numstat", "-z")
        staged = await run_git(selected, "diff", "--no-ext-diff", "--cached", "--numstat", "-z")
    except GitError as exc:
        raise _git_http_error(exc) from exc
    branch, parsed = _parse_status_v2(status.stdout)
    unstaged_stats = _parse_numstat(unstaged.stdout)
    staged_stats = _parse_numstat(staged.stdout)
    truncated = len(parsed) > STATUS_LIMIT
    entries: list[dict[str, Any]] = []
    counted = 0
    for item in parsed[:STATUS_LIMIT]:
        xy = item["xy"]
        index_letter = " " if xy[0] in ".?" else xy[0]
        tree_letter = "?" if xy == "??" else (" " if xy[1] == "." else xy[1])
        untracked = xy == "??"
        abs_path = selected / item["path"]
        unstaged_stat = _stat_dict(unstaged_stats.get(item["path"]))
        if untracked and counted < UNTRACKED_COUNT_LIMIT:
            counted += 1
            lines, binary = _count_lines(abs_path)
            unstaged_stat = (
                {"added": lines or 0, "deleted": 0, "binary": binary}
                if lines is not None or binary
                else None
            )
        entries.append(
            {
                "path": item["path"],
                "orig_path": item.get("orig_path"),
                "abs_path": str(abs_path),
                "index": index_letter,
                "worktree": tree_letter,
                "staged": index_letter != " ",
                "unstaged": tree_letter != " ",
                "untracked": untracked,
                "conflicted": bool(item.get("conflicted")),
                "staged_stat": _stat_dict(staged_stats.get(item["path"])),
                "unstaged_stat": unstaged_stat,
            }
        )
    return {
        "is_repo": True,
        "workspace": str(workspace),
        "repo": str(selected),
        "repositories": [str(item) for item in roots],
        **branch,
        "entries": entries,
        "truncated": truncated,
    }


async def worktrees_payload(root_raw: str) -> dict[str, Any]:
    """工作区里所有仓库的 checkout（主工作树 + linked worktree）及其改动数。"""
    _workspace, _selected, roots = await _resolve_repo(root_raw, None)
    items: list[dict[str, Any]] = []
    for repo_root in roots:
        trees = await _list_worktrees(repo_root) or [
            {"path": str(repo_root), "branch": "HEAD", "locked": False}
        ]
        for tree in trees:
            try:
                status = await run_git(
                    tree["path"], "status", "--porcelain", "-z", "--untracked-files=all", timeout=10
                )
                changes = len(
                    [
                        token
                        for token in status.stdout.split(b"\0")
                        if len(token) > 3 and token[2:3] == b" "
                    ]
                )
            except GitError:
                changes = 0
            items.append(
                {
                    "path": tree["path"],
                    "branch": tree["branch"],
                    "repo": str(repo_root),
                    "main": path_identity(tree["path"]) == path_identity(repo_root),
                    "locked": tree["locked"],
                    "changes": changes,
                }
            )
    return {"worktrees": items}


def _untracked_fingerprint(repo: Path, porcelain: bytes) -> str:
    """未跟踪文件的大小和 mtime。

    ``git status`` 对未跟踪文件只报路径，文件内容被改过时输出纹丝不动 ——
    但状态卡片上的行数是从文件内容数出来的，不带上这一项就会漏掉这种改动。
    """
    parts: list[str] = []
    for token in porcelain.split(b"\0"):
        if not token.startswith(b"??"):
            continue
        # porcelain v1 的未跟踪行是「?? 空格 路径」，切掉状态位和那个分隔空格。
        rel = token[3:].decode("utf-8", errors="replace")
        if not rel:
            continue
        try:
            info = (repo / rel).stat()
        except OSError:
            # 刚被删掉：路径本身已经在 porcelain 里，删掉这一项不会漏。
            continue
        parts.append(f"{rel}:{info.st_size}:{info.st_mtime_ns}")
    return "|".join(parts)


async def workspace_signature(root_raw: str) -> str:
    """工作区里所有工作树的廉价指纹，用来判断「要不要重算状态」。

    一次 ``git status --porcelain`` 就能盖住暂存区、工作区、未跟踪文件集、
    HEAD 和分支 —— 也就是状态卡片会显示的一切。算指纹只跑这一条命令，
    比真的要出状态（还要两条 diff、还要数未跟踪文件的行数）便宜得多。
    """
    workspace = _workspace_root(root_raw)
    roots = await _discover(workspace)
    if not roots:
        return ""
    parts: list[str] = []
    for repo_root in roots:
        trees = await _list_worktrees(repo_root) or [
            {"path": str(repo_root), "branch": "HEAD", "locked": False}
        ]
        for tree in trees:
            try:
                result = await run_git(
                    tree["path"],
                    "status",
                    "--porcelain",
                    "--branch",
                    "-z",
                    "--untracked-files=all",
                    timeout=10,
                )
            except GitError:
                # git 报错本身也是一种变化（仓库被删、索引损坏），换指纹重试。
                parts.append(f"{path_identity(tree['path'])}:error")
                continue
            parts.append(
                f"{path_identity(tree['path'])}:"
                f"{hashlib.sha1(result.stdout).hexdigest()}:"
                f"{_untracked_fingerprint(Path(tree['path']), result.stdout)}"
            )
    return "|".join(parts)
