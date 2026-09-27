"""Git HTTP：状态、对比、暂存、提交与历史。"""

from __future__ import annotations

from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
)

from nono_app_server.common.gitcmd import (
    GitError,
    path_identity,
    run_git,
)
from nono_app_server.common.textdiff import (
    diff_contents,
    parse_unified_diff,
)
from nono_app_server.features.git.schemas import CommitBody, DiscardBody, PathsBody
from nono_app_server.features.git.service import (
    _HASH_RE,
    COMMIT_FILES_LIMIT,
    DIFF_MAX_BYTES,
    MAX_CONTEXT,
    STATUS_LIMIT,
    UNTRACKED_COUNT_LIMIT,
    _count_lines,
    _git_http_error,
    _has_head,
    _is_untracked,
    _list_worktrees,
    _parse_log,
    _parse_numstat,
    _parse_status_v2,
    _paths_args,
    _repo_relative,
    _require_repo,
    _resolve_repo,
    _stat_dict,
)

router = APIRouter()


@router.get("/git/status")
async def git_status(root: str = Query(), repo: str | None = Query(default=None)) -> dict[str, Any]:
    """工作树状态：暂存区与工作区两侧的改动，含未跟踪文件和行数统计。"""
    workspace, selected, roots = await _resolve_repo(root, repo)
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


@router.get("/git/worktrees")
async def git_worktrees(root: str = Query()) -> dict[str, Any]:
    """工作区里所有仓库的 checkout（主工作树 + linked worktree）及其改动数。"""
    _workspace, _selected, roots = await _resolve_repo(root, None)
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


@router.get("/git/diff")
async def git_diff(
    root: str = Query(),
    path: str = Query(),
    repo: str | None = Query(default=None),
    staged: bool = Query(default=False),
    context: int = Query(default=3, ge=0, le=MAX_CONTEXT),
) -> dict[str, Any]:
    """一个文件的对比：staged=true 是暂存区对 HEAD，否则是工作区对暂存区。"""
    repo_path = await _require_repo(root, repo)
    rel = _repo_relative(repo_path, path)
    try:
        if not staged and await _is_untracked(repo_path, rel):
            target = repo_path / rel
            try:
                size = target.stat().st_size
            except OSError as exc:
                raise HTTPException(status_code=404, detail="找不到这个文件") from exc
            if size > DIFF_MAX_BYTES:
                return {**diff_contents(rel, None, b""), "kind": "oversized"}
            return diff_contents(rel, None, target.read_bytes(), context=context)
        args = ["diff", "--no-ext-diff", f"-U{context}"]
        if staged:
            args.append("--cached")
        result = await run_git(repo_path, *args, "--", rel)
    except GitError as exc:
        raise _git_http_error(exc) from exc
    files = parse_unified_diff(result.text)
    if not files:
        return diff_contents(rel, b"", b"")
    return files[0]


@router.post("/git/stage")
async def git_stage(body: PathsBody) -> dict[str, Any]:
    repo = await _require_repo(body.root, body.repo)
    try:
        await run_git(repo, "add", "-A", *_paths_args(repo, body.paths))
    except GitError as exc:
        raise _git_http_error(exc) from exc
    return {"ok": True}


@router.post("/git/unstage")
async def git_unstage(body: PathsBody) -> dict[str, Any]:
    repo = await _require_repo(body.root, body.repo)
    try:
        if await _has_head(repo):
            await run_git(repo, "reset", "-q", *_paths_args(repo, body.paths))
        else:
            await run_git(repo, "rm", "--cached", "-r", "-q", *_paths_args(repo, body.paths))
    except GitError as exc:
        raise _git_http_error(exc) from exc
    return {"ok": True}


@router.post("/git/discard")
async def git_discard(body: DiscardBody) -> dict[str, Any]:
    """丢弃工作区改动：已跟踪文件恢复成暂存区版本，未跟踪文件直接删除。"""
    repo = await _require_repo(body.root, body.repo)
    try:
        if body.paths:
            await run_git(repo, "checkout", *_paths_args(repo, body.paths))
        if body.untracked:
            await run_git(repo, "clean", "-f", "-q", *_paths_args(repo, body.untracked))
    except GitError as exc:
        raise _git_http_error(exc) from exc
    return {"ok": True}


@router.post("/git/commit")
async def git_commit(body: CommitBody) -> dict[str, Any]:
    """用暂存区内容提交；stage_all 时先暂存全部改动。沿用用户自己的 git 身份。"""
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="请填写提交说明")
    repo = await _require_repo(body.root, body.repo)
    try:
        if body.stage_all:
            await run_git(repo, "add", "-A")
        await run_git(repo, "commit", "-q", "-m", message)
        head = await run_git(repo, "rev-parse", "--short", "HEAD", timeout=10)
    except GitError as exc:
        raise _git_http_error(exc) from exc
    return {"ok": True, "head": head.text.strip()}


@router.get("/git/log")
async def git_log(
    root: str = Query(),
    repo: str | None = Query(default=None),
    count: int = Query(default=30, ge=1, le=200),
    skip: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    repo_path = await _require_repo(root, repo)
    if not await _has_head(repo_path):
        return {"entries": []}
    try:
        result = await run_git(
            repo_path,
            "log",
            "-n",
            str(count),
            "--skip",
            str(skip),
            "--decorate=short",
            "--pretty=format:%h%x1f%H%x1f%s%x1f%an%x1f%aI%x1f%D%x1e",
        )
    except GitError as exc:
        raise _git_http_error(exc) from exc
    return {"entries": _parse_log(result.text)}


@router.get("/git/commit")
async def git_commit_detail(
    root: str = Query(),
    hash: str = Query(),
    repo: str | None = Query(default=None),
) -> dict[str, Any]:
    """一个提交的元信息和逐文件对比（合并提交对第一个父提交）。"""
    if not _HASH_RE.match(hash):
        raise HTTPException(status_code=400, detail="提交号无效")
    repo_path = await _require_repo(root, repo)
    try:
        result = await run_git(
            repo_path,
            "show",
            "--no-ext-diff",
            "-U3",
            "-m",
            "--first-parent",
            "--format=%H%x1f%an%x1f%aI%x1f%s%x1f%b%x1e",
            hash,
        )
    except GitError as exc:
        raise _git_http_error(exc) from exc
    text = result.text
    header, _sep, patch = text.partition("\x1e")
    parts = header.split("\x1f")
    files = parse_unified_diff(patch)
    return {
        "hash": parts[0].strip() if parts else hash,
        "author": parts[1] if len(parts) > 1 else "",
        "date": parts[2] if len(parts) > 2 else "",
        "subject": parts[3] if len(parts) > 3 else "",
        "body": parts[4].strip() if len(parts) > 4 else "",
        "files": files[:COMMIT_FILES_LIMIT],
        "total": len(files),
    }
