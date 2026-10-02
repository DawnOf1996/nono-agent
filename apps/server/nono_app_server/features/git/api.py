"""Git HTTP：状态、对比、暂存、提交与历史，以及状态变化时的 SSE 推送。"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import StreamingResponse

from nono_app_server.common.gitcmd import (
    GitError,
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
    _git_http_error,
    _has_head,
    _is_untracked,
    _parse_log,
    _paths_args,
    _repo_relative,
    _require_repo,
    status_payload,
    workspace_signature,
    worktrees_payload,
)
from nono_app_server.features.git.watch import notify_changed
from nono_app_server.features.git.watch import subscribe as subscribe_changes

logger = logging.getLogger(__name__)

router = APIRouter()

# 签名检查的节奏。服务端自己知道改动时会立刻叫醒这条流（``notify_changed``），
# 所以这个间隔只是兜底：安静时放慢省 CPU，附近有动静时盯紧一点。
_ACTIVE_CHECK_S = 2.0
_IDLE_CHECK_S = 8.0
_ACTIVE_WINDOW_S = 15.0
_PING_S = 15.0


@router.get("/git/status")
async def git_status(root: str = Query(), repo: str | None = Query(default=None)) -> dict[str, Any]:
    """工作树状态：暂存区与工作区两侧的改动，含未跟踪文件和行数统计。"""
    return await status_payload(root, repo)


@router.get("/git/worktrees")
async def git_worktrees(root: str = Query()) -> dict[str, Any]:
    """工作区里所有仓库的 checkout（主工作树 + linked worktree）及其改动数。"""
    return await worktrees_payload(root)


@router.get("/git/stream")
async def stream_git(
    request: Request,
    root: str = Query(),
    repo: str | None = Query(default=None),
) -> StreamingResponse:
    """工作树状态有变化才推一条，浏览器不用自己问。

    判定变化靠一条 ``git status --porcelain`` 的指纹，不是定时全量重算：指纹
    没动就直接跳过；真正的状态（还要两条 diff、还要数未跟踪文件的行数）只在
    确实变了的时候才算一次。服务端自己知道改动时（对话轮结束、暂存、提交）
    会直接叫醒这条流，不用等下一次兜底检查。
    """

    async def events() -> AsyncIterator[str]:
        changed = asyncio.Event()
        loop = asyncio.get_running_loop()

        def on_change() -> None:
            # 工具可能在工作线程里回调过来，跨线程必须走回事件循环。
            try:
                loop.call_soon_threadsafe(changed.set)
            except RuntimeError:
                return

        unsubscribe = subscribe_changes(on_change)
        try:
            signature = await workspace_signature(root)
            yield _snapshot(await _snapshot_payload(root, repo))
            quiet_since = loop.time()
            while True:
                if await request.is_disconnected():
                    return
                # 刚有过动静就盯紧一点，安静下来就放慢：省的是没人看时的 CPU。
                active = loop.time() - quiet_since < _ACTIVE_WINDOW_S
                woken = False
                try:
                    await asyncio.wait_for(
                        changed.wait(), timeout=_ACTIVE_CHECK_S if active else _IDLE_CHECK_S
                    )
                    changed.clear()
                    woken = True
                except TimeoutError:
                    pass
                if loop.time() - quiet_since >= _PING_S:
                    # 纯注释帧：只为保活，不是数据。
                    yield ": ping\n\n"
                current = await workspace_signature(root)
                if current == signature:
                    # 通知来了但状态没变（刚跑完一条只读命令）：不算动静。
                    continue
                signature = current
                if woken:
                    quiet_since = loop.time()
                try:
                    payload = await _snapshot_payload(root, repo)
                except (GitError, HTTPException, OSError):
                    logger.warning("Git stream: recompute failed for %s", root, exc_info=True)
                    continue
                yield _snapshot(payload)
                quiet_since = loop.time()
        except asyncio.CancelledError:
            # 客户端断开：正常路径，不是错误。
            raise
        finally:
            unsubscribe()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _frame(event: str, payload: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _snapshot(payload: dict[str, Any]) -> str:
    return _frame("status", payload["status"]) + _frame("worktrees", payload["worktrees"])


async def _snapshot_payload(root: str, repo: str | None) -> dict[str, Any]:
    return {
        "status": await status_payload(root, repo),
        "worktrees": await worktrees_payload(root),
    }


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
    notify_changed()
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
    notify_changed()
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
    notify_changed()
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
    notify_changed()
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
