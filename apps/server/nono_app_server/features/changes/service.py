"""每轮文件变更：一轮对话改了哪些文件、各改了多少行，以及每个文件轮前轮后的对比。

两路来源：

- Git 快照：轮开始和轮结束时，把工作区写进一个私有索引和临时对象库（仓库自己的
  对象库只作只读 alternate），两棵树相减就是这一轮的改动。仓库的索引、对象、工作树、
  引用都不会被碰到，轮前已有的未提交改动也不会算进来。Shell 命令改的文件同样覆盖。
- 文件工具前像：`edit_file` / `write_file` 执行前先拷一份文件原内容（每轮每个路径一次），
  轮结束再读一次。覆盖 git 管不到的路径：非仓库工作区、被忽略的文件、仓库外的文件。

每个改动文件的轮前、轮后内容按 SHA-1 存进 ``~/.nono-agent/workspace-changes/<session>/blobs``，
摘要追加到同目录的 ``turns.jsonl``，所以重启后历史轮次的卡片和对比依然可用。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nono_agent.hooks.middleware import MiddlewareBase
from nono_agent.paths import user_home
from nono_app_server.common.gitcmd import GitError, git_available, git_path, repo_toplevel, run_git
from nono_app_server.common.textdiff import count_changes, diff_contents

logger = logging.getLogger(__name__)

MAX_FILES = 300
MAX_FILE_BYTES = 2 * 1024 * 1024
SNAPSHOT_TIMEOUT_S = 60.0
EDIT_TOOLS = frozenset({"edit_file", "write_file"})
MIDDLEWARE_NAME = "nono-app/workspace-changes"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class _Oversized:
    """文件大于 MAX_FILE_BYTES：不保存内容，只知道它存在。"""


OVERSIZED = _Oversized()
Content = bytes | None | _Oversized


def changes_root() -> Path:
    return user_home() / "workspace-changes"


def remove_tree(path: Path) -> None:
    """删除目录；git 的松散对象是只读文件，Windows 上要先去掉只读属性才删得掉。"""
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                os.chmod(os.path.join(root, name), stat.S_IWRITE)
            except OSError:
                pass
    shutil.rmtree(path, ignore_errors=True)


def _read_content(path: Path) -> Content:
    try:
        if not path.is_file():
            return None
        if path.stat().st_size > MAX_FILE_BYTES:
            return OVERSIZED
        return path.read_bytes()
    except OSError:
        return None


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _inside(root: Path, target: Path) -> bool:
    root_key = _key(root).rstrip("\\/")
    target_key = _key(target)
    return target_key == root_key or target_key.startswith(root_key + os.sep)


def _display(cwd: Path, target: Path) -> str:
    if _inside(cwd, target):
        rel = os.path.relpath(target, cwd)
        return Path(rel).as_posix()
    return str(target)


@dataclass
class GitSnapshot:
    top: Path
    prefix: str
    tmp: Path
    env: dict[str, str]
    tree: str

    def cleanup(self) -> None:
        remove_tree(self.tmp)


async def _write_tree(cwd: Path, env: dict[str, str]) -> str:
    await run_git(
        cwd,
        "add",
        "-A",
        "--ignore-errors",
        "--",
        ".",
        env=env,
        timeout=SNAPSHOT_TIMEOUT_S,
        ok_codes=(0, 1),
    )
    result = await run_git(cwd, "write-tree", env=env, timeout=SNAPSHOT_TIMEOUT_S)
    return result.text.strip()


async def take_git_baseline(cwd: Path) -> GitSnapshot | None:
    """轮开始时的工作区快照；不在仓库里或 git 不可用时返回 None。"""
    if not git_available():
        return None
    top = await repo_toplevel(cwd)
    if top is None:
        return None
    prefix = (await run_git(cwd, "rev-parse", "--show-prefix", timeout=10)).text.strip()
    objects = await git_path(cwd, "objects")
    index = await git_path(cwd, "index")
    tmp = Path(tempfile.mkdtemp(prefix="nono-changes-"))
    (tmp / "objects").mkdir()
    if index.is_file():
        shutil.copyfile(index, tmp / "index")
    env = {
        "GIT_INDEX_FILE": str(tmp / "index"),
        "GIT_OBJECT_DIRECTORY": str(tmp / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(objects),
    }
    try:
        tree = await _write_tree(cwd, env)
    except BaseException:
        remove_tree(tmp)
        raise
    return GitSnapshot(top=top, prefix=prefix, tmp=tmp, env=env, tree=tree)


@dataclass
class _TreeChange:
    path: str
    old_blob: str | None
    new_blob: str | None
    added: int | None = None
    deleted: int | None = None


async def _diff_trees(snap: GitSnapshot, after: str) -> list[_TreeChange]:
    args = [
        "diff-tree",
        "-r",
        "-z",
        "--no-renames",
        "--no-abbrev",
        "--raw",
        "--numstat",
        snap.tree,
        after,
    ]
    if snap.prefix:
        args += ["--", snap.prefix]
    result = await run_git(snap.top, *args, env=snap.env, timeout=SNAPSHOT_TIMEOUT_S)
    tokens = result.stdout.split(b"\0")
    changes: dict[str, _TreeChange] = {}
    index = 0
    zero = "0" * 40
    while index < len(tokens):
        token = tokens[index].decode("utf-8", errors="replace")
        index += 1
        if not token:
            continue
        if token.startswith(":"):
            meta = token[1:].split(" ")
            path = tokens[index].decode("utf-8", errors="replace") if index < len(tokens) else ""
            index += 1
            if len(meta) < 5 or not path:
                continue
            old_mode, new_mode, old_sha, new_sha = meta[0], meta[1], meta[2], meta[3]
            if "160000" in (old_mode, new_mode):
                continue
            changes[path] = _TreeChange(
                path=path,
                old_blob=None if set(old_sha) == {"0"} or old_sha == zero else old_sha,
                new_blob=None if set(new_sha) == {"0"} or new_sha == zero else new_sha,
            )
            continue
        parts = token.split("\t", 2)
        if len(parts) == 3 and parts[2] in changes:
            change = changes[parts[2]]
            change.added = None if parts[0] == "-" else int(parts[0] or 0)
            change.deleted = None if parts[1] == "-" else int(parts[1] or 0)
    return list(changes.values())


async def _read_blobs(snap: GitSnapshot, shas: set[str]) -> dict[str, Content]:
    """批量读出对象内容；超过 MAX_FILE_BYTES 的只标记为过大。"""
    if not shas:
        return {}
    ordered = sorted(shas)
    check = await run_git(
        snap.top,
        "cat-file",
        "--batch-check",
        env=snap.env,
        input="\n".join(ordered).encode() + b"\n",
    )
    sizes: dict[str, int] = {}
    for line in check.text.splitlines():
        parts = line.split(" ")
        if len(parts) == 3 and parts[1] == "blob":
            sizes[parts[0]] = int(parts[2])
    out: dict[str, Content] = {
        sha: OVERSIZED for sha, size in sizes.items() if size > MAX_FILE_BYTES
    }
    wanted = [sha for sha in ordered if sha in sizes and sizes[sha] <= MAX_FILE_BYTES]
    if not wanted:
        return out
    batch = await run_git(
        snap.top, "cat-file", "--batch", env=snap.env, input="\n".join(wanted).encode() + b"\n"
    )
    data = batch.stdout
    pos = 0
    while pos < len(data):
        newline = data.find(b"\n", pos)
        if newline < 0:
            break
        header = data[pos:newline].decode("utf-8", errors="replace").split(" ")
        pos = newline + 1
        if len(header) != 3:
            break
        size = int(header[2])
        out[header[0]] = data[pos : pos + size]
        pos += size + 1
    return out


async def _ignored(snap: GitSnapshot, paths: list[Path]) -> set[str]:
    rels = []
    for path in paths:
        try:
            rels.append(Path(os.path.relpath(path, snap.top)).as_posix())
        except ValueError:
            continue
    if not rels:
        return set()
    result = await run_git(
        snap.top,
        "check-ignore",
        "-z",
        "--stdin",
        input="\0".join(rels).encode() + b"\0",
        ok_codes=(0, 1),
    )
    return {_key(snap.top / item) for item in result.text.split("\0") if item}


@dataclass
class TurnRecord:
    id: str
    session_id: str
    cwd: Path
    started_at: float
    start_seq: int
    baseline: asyncio.Task[GitSnapshot | None] | None
    captures: dict[str, tuple[Path, Content]] = field(default_factory=dict)

    async def snapshot(self) -> GitSnapshot | None:
        if self.baseline is None:
            return None
        try:
            return await asyncio.shield(self.baseline)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 快照失败只退回到文件工具前像
            logger.warning(
                "workspace changes: git baseline failed for %s: %s", self.session_id, exc
            )
            return None

    async def before_tools(self, content: Any) -> None:
        """工具执行前：等轮前快照落定，再拷下文件工具将要改动的文件。"""
        await self.snapshot()
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if block.get("name") not in EDIT_TOOLS:
                continue
            params = block.get("input")
            if isinstance(params, str):
                try:
                    params = json.loads(params) if params.strip() else {}
                except json.JSONDecodeError:
                    params = {}
            if not isinstance(params, dict):
                continue
            raw = params.get("path") or params.get("file_path")
            if not isinstance(raw, str) or not raw.strip():
                continue
            target = Path(raw).expanduser()
            if not target.is_absolute():
                target = self.cwd / target
            try:
                target = target.resolve()
            except OSError:
                continue
            key = _key(target)
            if key not in self.captures:
                self.captures[key] = (target, _read_content(target))


class _CaptureMiddleware(MiddlewareBase):
    _name = MIDDLEWARE_NAME

    def __init__(self, service: WorkspaceChangesService, session_id: str) -> None:
        self._service = service
        self._session_id = session_id

    async def on_after_api_call(self, *, assistant_message: Any, state: Any) -> Any:
        record = self._service.active(self._session_id)
        if record is not None:
            message = getattr(assistant_message, "message", None)
            content = message.get("content") if isinstance(message, dict) else None
            try:
                await record.before_tools(content)
            except Exception:  # noqa: BLE001 - 记录失败不能影响对话
                logger.exception("workspace changes: capture failed")
        return assistant_message


async def last_event_seq(manager: Any, session_id: str) -> int:
    writer = manager.events_writer(session_id) if manager is not None else None
    if writer is None:
        return 0
    try:
        events = await writer.read_events()
    except Exception:  # noqa: BLE001
        return 0
    return max((int(getattr(event, "seq", 0)) for event in events), default=0)


class WorkspaceChangesService:
    """记录每轮改动并提供摘要、对比和还原。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or changes_root()
        self._active: dict[str, TurnRecord] = {}
        self._pending: set[asyncio.Task[Any]] = set()

    # ── 记录 ──────────────────────────────────────────────────────────
    def active(self, session_id: str) -> TurnRecord | None:
        return self._active.get(session_id)

    def attach(self, session_id: str, engine: Any) -> None:
        chain = getattr(engine, "middleware_chain", None)
        if chain is None:
            return
        chain.remove(MIDDLEWARE_NAME)
        chain.add(_CaptureMiddleware(self, session_id))

    async def begin(self, session_id: str, engine: Any, manager: Any = None) -> TurnRecord | None:
        config = getattr(engine, "_config", None)
        raw_cwd = getattr(config, "cwd", None)
        if not raw_cwd:
            return None
        cwd = Path(raw_cwd)
        if not cwd.is_dir():
            return None
        start_seq = await last_event_seq(manager, session_id)
        baseline = asyncio.create_task(take_git_baseline(cwd))
        record = TurnRecord(
            id=uuid.uuid4().hex[:16],
            session_id=session_id,
            cwd=cwd.resolve(),
            started_at=time.time(),
            start_seq=start_seq,
            baseline=baseline,
        )
        self._active[session_id] = record
        self.attach(session_id, engine)
        return record

    def finish_later(self, record: TurnRecord, manager: Any = None) -> None:
        """客户端断开时在后台收尾，这一轮的改动仍会被记下。"""
        task = asyncio.create_task(self.finish(record, manager))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def finish(self, record: TurnRecord, manager: Any = None) -> dict[str, Any] | None:
        if self._active.get(record.session_id) is record:
            self._active.pop(record.session_id, None)
        try:
            return await self._finish(record, manager)
        except Exception:  # noqa: BLE001
            logger.exception("workspace changes: failed to record turn for %s", record.session_id)
            return None

    async def _finish(self, record: TurnRecord, manager: Any) -> dict[str, Any] | None:
        snap = await record.snapshot()
        files: list[dict[str, Any]] = []
        blobs: dict[str, bytes] = {}
        covered: set[str] = set()
        try:
            if snap is not None:
                try:
                    files.extend(await self._git_files(record, snap, blobs, covered))
                except GitError as exc:
                    logger.warning(
                        "workspace changes: git snapshot failed for %s: %s", record.session_id, exc
                    )
                    snap_ok = False
                else:
                    snap_ok = True
                if snap_ok and record.captures:
                    inside = [
                        path
                        for path, _before in record.captures.values()
                        if _inside(snap.top, path)
                    ]
                    try:
                        ignored = await _ignored(snap, inside)
                    except GitError:
                        ignored = {_key(path) for path in inside}
                    covered.update(_key(path) for path in inside if _key(path) not in ignored)
            files.extend(self._captured_files(record, blobs, covered))
        finally:
            if snap is not None:
                snap.cleanup()
        if not files:
            return None
        files.sort(key=lambda item: item["display"])
        summary = {
            "id": record.id,
            "session_id": record.session_id,
            "cwd": str(record.cwd),
            "started_at": record.started_at,
            "ended_at": time.time(),
            "start_seq": record.start_seq,
            "end_seq": await last_event_seq(manager, record.session_id),
            "files": files[:MAX_FILES],
            "total": len(files),
            "added": sum(item["added"] for item in files),
            "deleted": sum(item["deleted"] for item in files),
        }
        self._persist(record.session_id, summary, blobs)
        return summary

    async def _git_files(
        self,
        record: TurnRecord,
        snap: GitSnapshot,
        blobs: dict[str, bytes],
        covered: set[str],
    ) -> list[dict[str, Any]]:
        after_tree = await _write_tree(record.cwd, snap.env)
        if after_tree == snap.tree:
            return []
        changes = await _diff_trees(snap, after_tree)
        shas = {sha for change in changes for sha in (change.old_blob, change.new_blob) if sha}
        contents = await _read_blobs(snap, shas)
        files: list[dict[str, Any]] = []
        for change in changes:
            target = snap.top / change.path
            covered.add(_key(target.resolve()))
            before = contents.get(change.old_blob, OVERSIZED) if change.old_blob else None
            after = contents.get(change.new_blob, OVERSIZED) if change.new_blob else None
            entry = self._entry(record, target, before, after, blobs, source="git")
            if (
                change.added is not None
                and change.deleted is not None
                and not entry.get("oversized")
            ):
                entry["added"], entry["deleted"] = change.added, change.deleted
            elif change.added is None:
                entry["binary"] = True
                entry["added"] = entry["deleted"] = 0
            files.append(entry)
        return files

    def _captured_files(
        self, record: TurnRecord, blobs: dict[str, bytes], covered: set[str]
    ) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        for key, (target, before) in record.captures.items():
            if key in covered:
                continue
            after = _read_content(target)
            if before is None and after is None:
                continue
            if isinstance(before, bytes) and isinstance(after, bytes) and before == after:
                continue
            files.append(self._entry(record, target, before, after, blobs, source="tool"))
        return files

    def _entry(
        self,
        record: TurnRecord,
        target: Path,
        before: Content,
        after: Content,
        blobs: dict[str, bytes],
        *,
        source: str,
    ) -> dict[str, Any]:
        oversized = isinstance(before, _Oversized) or isinstance(after, _Oversized)

        def store(content: Content) -> str | None:
            if not isinstance(content, bytes):
                return None
            digest = hashlib.sha1(content).hexdigest()
            blobs[digest] = content
            return digest

        entry: dict[str, Any] = {
            "path": _display(record.cwd, target),
            "display": _display(record.cwd, target),
            "abs_path": str(target),
            "status": "added" if before is None else ("deleted" if after is None else "modified"),
            "added": 0,
            "deleted": 0,
            "source": source,
            "before_blob": store(before),
            "after_blob": store(after),
        }
        if oversized:
            entry["oversized"] = True
            return entry
        added, deleted, binary = count_changes(
            before if isinstance(before, bytes) else None,
            after if isinstance(after, bytes) else None,
        )
        entry["added"] = added
        entry["deleted"] = deleted
        if binary:
            entry["binary"] = True
        return entry

    # ── 存储 ──────────────────────────────────────────────────────────
    def _session_dir(self, session_id: str) -> Path:
        name = (
            session_id
            if _SAFE_ID.match(session_id)
            else hashlib.sha1(session_id.encode()).hexdigest()
        )
        return self._root / name

    def _persist(self, session_id: str, summary: dict[str, Any], blobs: dict[str, bytes]) -> None:
        directory = self._session_dir(session_id)
        blob_dir = directory / "blobs"
        blob_dir.mkdir(parents=True, exist_ok=True)
        for digest, content in blobs.items():
            target = blob_dir / digest
            if not target.exists():
                target.write_bytes(content)
        with (directory / "turns.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(summary, ensure_ascii=False) + "\n")

    def _blob(self, session_id: str, digest: str | None) -> bytes | None:
        if not digest or not re.fullmatch(r"[0-9a-f]{40}", digest):
            return None
        try:
            return (self._session_dir(session_id) / "blobs" / digest).read_bytes()
        except OSError:
            return None

    def list_turns(self, session_id: str) -> list[dict[str, Any]]:
        path = self._session_dir(session_id) / "turns.jsonl"
        if not path.is_file():
            return []
        turns: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict) and isinstance(item.get("files"), list):
                turns.append(item)
        return turns

    def get_turn(self, session_id: str, turn_id: str) -> dict[str, Any] | None:
        for turn in self.list_turns(session_id):
            if turn.get("id") == turn_id:
                return turn
        return None

    def file_diff(
        self, session_id: str, turn_id: str, index: int, context: int = 3
    ) -> dict[str, Any] | None:
        turn = self.get_turn(session_id, turn_id)
        if turn is None or not 0 <= index < len(turn["files"]):
            return None
        file = turn["files"][index]
        display = file.get("display") or file.get("path") or ""
        if file.get("oversized"):
            return {
                **diff_contents(display, b"", b""),
                "kind": "oversized",
                "status": file.get("status", "modified"),
                "before": file.get("status") != "added",
                "after": file.get("status") != "deleted",
            }
        before = self._blob(session_id, file.get("before_blob"))
        after = self._blob(session_id, file.get("after_blob"))
        if file.get("status") != "added" and before is None:
            before = b""
        if file.get("status") != "deleted" and after is None:
            after = b""
        result = diff_contents(display, before, after, context=context)
        result["abs_path"] = file.get("abs_path")
        return result

    def revert_file(
        self, session_id: str, turn_id: str, index: int, *, force: bool = False
    ) -> dict[str, Any]:
        """把文件恢复成这一轮开始前的内容。

        磁盘内容已不是本轮结束时的样子时，除非 force 否则拒绝。
        """
        turn = self.get_turn(session_id, turn_id)
        if turn is None or not 0 <= index < len(turn["files"]):
            raise LookupError("找不到这条改动")
        file = turn["files"][index]
        if file.get("oversized"):
            raise ValueError("文件过大，无法还原")
        target = Path(file["abs_path"])
        status = file.get("status")
        before = self._blob(session_id, file.get("before_blob")) if status != "added" else None
        after = self._blob(session_id, file.get("after_blob")) if status != "deleted" else None
        if status != "added" and before is None:
            raise ValueError("缺少轮前内容，无法还原")
        current = _read_content(target)
        if not force and current != after:
            raise FileExistsError("文件在这一轮之后又被改过")
        if before is None:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before)
        return {"ok": True, "path": str(target)}

    def forget_session(self, session_id: str) -> None:
        remove_tree(self._session_dir(session_id))
