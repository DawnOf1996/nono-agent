"""会话事件流持久化 —— 抽象协议 + 本地文件实现（默认后端）。

架构设计：

- ``TranscriptWriter``：Protocol，定义**事件流**读写接口。落盘内容只有
  ``SessionEvent``；消息数组不落盘，由 ``derive_messages()`` 从事件推导。
- ``LocalFileTranscriptWriter``：默认把事件写成 ``<sid>.jsonl.zst``
  （zstd 独立帧、可追加）；既有未压缩的 ``<sid>.jsonl`` 在下一次追加
  （例如 resume 后写新事件）时整体迁成 ``.jsonl.zst``。``compress=False``
  时保持明文。
- 规划扩展：PostgreSQL / MySQL、Redis、S3 等远端/共享后端（实现同一协议后注入
  QueryEngine；详见 docs-site/docs/roadmap/index.md §2.5）。

事件只追加不改写：历史不可变，「修正」用新事件表达（compact 边界、tombstone），
因此没有全量覆写接口 —— 覆写会与追加交错并破坏事件流。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import aiofiles  # type: ignore[import-untyped]
import zstandard as zstd

from nono_agent.paths import user_home
from nono_agent.session.events import EVENT_TURN_START, SessionEvent
from nono_agent.session.header import (
    SessionHeader,
    VersionMismatchError,
    check_format_version,
)

logger = logging.getLogger(__name__)

# 默认会话文件存储目录。运行中的实际目录见 active_session_dir()。
DEFAULT_SESSION_DIR = user_home() / "sessions"

# 用户在设置里指定的目录。None 表示沿用 DEFAULT_SESSION_DIR（测试会改那个常量）。
_configured_session_dir: Path | None = None

# 会话元信息侧车文件后缀（与事件流同目录、同 session_id）
HEADER_SUFFIX = ".header.json"

# 明文 JSONL 与 zstd 压缩后的文件名后缀
JSONL_SUFFIX = ".jsonl"
JSONL_ZSTD_SUFFIX = ".jsonl.zst"

# zstd 帧魔数（小端 0xFD2FB528）
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

# 追加写用的压缩级别：3 是 zstd 默认档，JSON 重复键压缩比高、CPU 开销低。
ZSTD_LEVEL = 3

# 会话文件权限：仅所有者可读写。
# 事件流逐字保留提示词、工具输出与请求配置（脱敏在读取侧，见 session/redact.py），
# 因此落盘时就必须挡住同机其它用户。
SESSION_FILE_MODE = 0o600

# 会话目录权限：仅所有者可进入 —— 否则会话 ID 列表本身就是可枚举的。
SESSION_DIR_MODE = 0o700


# ------------------------------------------------------------------
# 路径 / 编解码
# ------------------------------------------------------------------

def active_session_dir() -> Path:
    """当前会话目录。未单独配置时就是 DEFAULT_SESSION_DIR。"""
    return _configured_session_dir if _configured_session_dir is not None else DEFAULT_SESSION_DIR


def use_session_dir(path: Path | None) -> None:
    """切换进程内使用的会话目录。None 表示回到 DEFAULT_SESSION_DIR。"""
    global _configured_session_dir
    _configured_session_dir = path


def load_session_dir_setting() -> Path:
    """从用户设置读会话目录。没配过就用默认目录。"""
    from nono_agent.settings.sources import load_user_settings

    raw = load_user_settings().get("session_dir")
    if isinstance(raw, str) and raw.strip():
        path = Path(raw).expanduser().resolve()
        use_session_dir(path)
        return path
    use_session_dir(None)
    return DEFAULT_SESSION_DIR


def relocate_session_files(old: Path, new: Path) -> int:
    """把旧目录里的会话文件迁到新目录。

    只搬事件流和元信息（``.jsonl`` / ``.jsonl.zst`` / ``.header.json``）。
    新目录里已有同名文件时整次放弃，已经搬走的会移回去。
    """
    source = old.expanduser().resolve()
    target = new.expanduser().resolve()
    if _same_path(source, target):
        return 0
    if source.exists() and not source.is_dir():
        raise ValueError("当前会话目录不是文件夹")
    if target.exists() and not target.is_dir():
        raise ValueError("新的保存路径已经是一个文件")
    if source.exists() and (_inside(source, target) or _inside(target, source)):
        raise ValueError("新目录不能放在当前会话目录里面，也不能把当前目录包进去")
    files = _session_files(source)
    collisions = [path.name for path in files if (target / path.name).exists()]
    if collisions:
        shown = "、".join(collisions[:3])
        raise FileExistsError(f"新目录里已经有同名会话文件：{shown}")
    target.mkdir(parents=True, exist_ok=True)
    try:
        target.chmod(SESSION_DIR_MODE)
    except OSError:
        pass
    moved: list[tuple[Path, Path]] = []
    try:
        for path in files:
            dest = target / path.name
            shutil.move(str(path), str(dest))
            moved.append((path, dest))
    except OSError:
        for src, dest in reversed(moved):
            if dest.exists() and not src.exists():
                shutil.move(str(dest), str(src))
        raise
    return len(moved)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _inside(parent: Path, child: Path) -> bool:
    parent_key = os.path.normcase(str(parent))
    child_key = os.path.normcase(str(child))
    if child_key == parent_key:
        return False
    prefix = parent_key if parent_key.endswith(os.sep) else parent_key + os.sep
    return child_key.startswith(prefix)


def _session_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for path in directory.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name.endswith(".header.json") or name.endswith(".jsonl.zst") or name.endswith(".jsonl"):
            files.append(path)
    files.sort(key=lambda item: item.name)
    return files


def transcript_session_id(path: Path) -> str:
    """从事件流文件名取出 session_id（兼容 ``.jsonl`` / ``.jsonl.zst``）。"""
    name = path.name
    if name.endswith(JSONL_ZSTD_SUFFIX):
        return name[: -len(JSONL_ZSTD_SUFFIX)]
    if name.endswith(JSONL_SUFFIX):
        return name[: -len(JSONL_SUFFIX)]
    return path.stem


def existing_transcript_path(session_dir: Path, session_id: str) -> Path | None:
    """查找已有事件流。明文 ``.jsonl`` 优先，避免同一会话两套文件并存时读错。"""
    jsonl = session_dir / f"{session_id}{JSONL_SUFFIX}"
    zst = session_dir / f"{session_id}{JSONL_ZSTD_SUFFIX}"
    if jsonl.is_file():
        return jsonl
    if zst.is_file():
        return zst
    return None


def resolve_transcript_path(
    session_file: Path, *, compress: bool | None = None
) -> tuple[Path, bool]:
    """决定写入器用哪一个文件、是否按 zstd 帧追加。

    规则：

    - 已有明文 ``.jsonl``：先指向该文件（读取仍可用）；写入器在首次追加时再迁成
      ``.jsonl.zst``（``compress=False`` 除外）。
    - 已有 ``.jsonl.zst``：按 zstd 独立帧追加。
    - 新会话：默认 ``.jsonl.zst``；``compress=False`` 时仍写明文 ``.jsonl``。
    """
    path = Path(session_file)
    session_id = transcript_session_id(path)
    directory = path.parent
    jsonl = directory / f"{session_id}{JSONL_SUFFIX}"
    zst = directory / f"{session_id}{JSONL_ZSTD_SUFFIX}"

    if jsonl.is_file():
        return jsonl, _starts_with_zstd(jsonl)
    if zst.is_file():
        return zst, True
    if compress is False:
        return jsonl, False
    return zst, True


def decode_transcript_bytes(data: bytes) -> str:
    """把事件流文件内容解码成 JSONL 文本。

    zstd 魔数开头的按独立帧解压（最后一帧不完整则跳过，对应写入中断）；
    否则按 UTF-8 明文 JSONL 处理。
    """
    if not data:
        return ""
    if data.startswith(ZSTD_MAGIC):
        return _decompress_zstd_frames(data).decode("utf-8")
    return data.decode("utf-8")


def read_transcript_text(path: Path) -> str:
    """同步读取事件流并解码为 JSONL 文本。"""
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    try:
        return decode_transcript_bytes(data)
    except UnicodeDecodeError:
        logger.debug("Transcript %s is not valid UTF-8", path)
        return ""


def _starts_with_zstd(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(len(ZSTD_MAGIC)) == ZSTD_MAGIC
    except OSError:
        return False


def _decompress_zstd_frames(data: bytes) -> bytes:
    """解压拼接的 zstd 独立帧；遇到截断的最后一帧则停，保留已解出的内容。"""
    dctx = zstd.ZstdDecompressor()
    chunks: list[bytes] = []
    remaining = data
    while remaining.startswith(ZSTD_MAGIC):
        dobj = dctx.decompressobj()
        try:
            chunk = dobj.decompress(remaining)
            tail = dobj.flush()
        except zstd.ZstdError:
            break
        chunks.append(chunk)
        if tail:
            chunks.append(tail)
        unused = dobj.unused_data
        if not unused or unused == remaining:
            break
        remaining = unused
    return b"".join(chunks)


# ------------------------------------------------------------------
# 共享工具函数
# ------------------------------------------------------------------

def list_session_files(session_dir: Path | None = None) -> list[dict[str, Any]]:
    """列出会话目录下的事件流文件（id + 事件数 + 最后修改时间）。

    用于 CLI ``sessions`` 命令 / Server 会话治理。

    Args:
        session_dir: 会话目录；None 时用 ``DEFAULT_SESSION_DIR``。

    Returns:
        每个会话一项的字典列表，按 session_id 排序。
    """
    directory = Path(session_dir) if session_dir is not None else active_session_dir()
    if not directory.is_dir():
        return []

    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    files: list[Path] = []
    for pattern in (f"*{JSONL_SUFFIX}", f"*{JSONL_ZSTD_SUFFIX}"):
        for file in sorted(directory.glob(pattern)):
            session_id = transcript_session_id(file)
            if session_id in seen:
                continue
            seen.add(session_id)
            files.append(file)

    for file in files:
        session_id = transcript_session_id(file)
        line_count = 0
        turn_count = 0
        try:
            text = read_transcript_text(file)
        except OSError:
            text = ""
        for line in text.splitlines():
            if not line.strip():
                continue
            line_count += 1
            try:
                event = parse_event_line(line)
            except VersionMismatchError:
                continue
            if event is not None and event.type == EVENT_TURN_START:
                turn_count += 1
        header = peek_session_header(session_id, directory)
        sessions.append(
            {
                "session_id": session_id,
                "event_count": line_count,
                "turn_count": turn_count,
                "last_modified": file.stat().st_mtime,
                "cwd": header.cwd if header else "",
                "summary": header.summary if header else "",
                "permission_mode": header.permission_mode if header else "default",
            }
        )
    sessions.sort(key=lambda item: str(item["session_id"]))
    return sessions


def peek_session_header(
    session_id: str, session_dir: Path | None = None
) -> SessionHeader | None:
    """同步读取 ``<sid>.header.json``，供 CLI ``--resume`` 在启动引擎前绑定约束。

    Args:
        session_id: 会话 ID。
        session_dir: 会话目录；None 时用 ``DEFAULT_SESSION_DIR``。

    Returns:
        解析出的 SessionHeader；文件不存在或不是对象时为 None。
    """
    directory = Path(session_dir) if session_dir is not None else active_session_dir()
    path = directory / f"{session_id}{HEADER_SUFFIX}"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    return SessionHeader.from_dict(data)


def parse_event_line(line: str) -> SessionEvent | None:
    """解析事件流中的一行。

    Args:
        line: JSONL 中的一行（已去除首尾空白）。

    Returns:
        解析出的事件；该行不是合法 JSON 对象（写入中断产生的残行）时返回 None。

    Raises:
        VersionMismatchError: 该行是合法 JSON 对象但缺少事件信封字段，
            说明文件是事件格式之前的消息副本 transcript。
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    if "seq" not in obj or "data" not in obj:
        raise VersionMismatchError(
            "transcript line is not a SessionEvent (legacy message-copy transcript); "
            "event-sourced sessions cannot be read from it"
        )
    return SessionEvent.model_validate(obj)


def restrict_permissions(path: Path) -> None:
    """把会话文件及其所在目录收紧到仅所有者可访问。

    每次写入后调用（而不是只在创建时）：目录可能是既有的宽权限目录，
    文件也可能由更早的版本创建。``chmod`` 在不支持 POSIX 权限位的平台上
    是无操作，失败仅代表无法收紧，不影响会话写入本身。

    Args:
        path: 刚写入的会话文件路径。
    """
    try:
        path.parent.chmod(SESSION_DIR_MODE)
        path.chmod(SESSION_FILE_MODE)
    except OSError:
        # 只读挂载、网络文件系统或 Windows ACL 下无法设置权限位；
        # 这不影响事件写入，也没有更强的收紧手段可退回。
        logger.debug("Cannot restrict permissions on %s", path)


# ------------------------------------------------------------------
# Protocol 协议类
# ------------------------------------------------------------------

@runtime_checkable
class TranscriptWriter(Protocol):
    """会话事件流持久化协议。

    所有存储后端（文件、数据库、Redis、对象存储等）都实现此协议。
    CLI 或 Server 可在创建 QueryEngine 时显式指定实现：

    用法示例::

        # 默认使用本地文件事件流
        engine = QueryEngine(config)

        # 显式指定其它后端
        engine = QueryEngine(config, transcript_writer=SQLiteTranscriptWriter(path))
    """

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        """追加一条事件。

        Args:
            event: 待写入事件；``seq`` 由后端分配，调用方传入的值被忽略。

        Returns:
            带已分配 ``seq`` 的同一事件对象。
        """
        ...

    async def append_events(self, events: Sequence[SessionEvent]) -> list[SessionEvent]:
        """按序追加一批事件（一次落盘，便于 JSONL zstd 把多行打进同一帧）。

        Args:
            events: 待写入事件；``seq`` 由后端分配。

        Returns:
            带已分配 ``seq`` 的事件列表（与输入顺序一致）。
        """
        ...

    async def read_events(self) -> list[SessionEvent]:
        """读取全部事件（按 seq 升序）。

        Returns:
            事件列表。

        Raises:
            VersionMismatchError: 会话格式版本不符，或文件不是事件流。
        """
        ...

    async def read_events_from(self, start_seq: int) -> list[SessionEvent]:
        """读取 ``seq >= start_seq`` 的事件。

        Args:
            start_seq: 起始序号（含）。

        Returns:
            事件列表（按 seq 升序）。
        """
        ...

    async def write_header(self, header: SessionHeader) -> None:
        """写入（或覆盖）会话元信息。

        Args:
            header: 会话元信息。
        """
        ...

    async def read_header(self) -> SessionHeader | None:
        """读取会话元信息。

        Returns:
            会话元信息；尚未写入时为 None。
        """
        ...

    async def flush(self) -> None:
        """确保已提交的写入落盘。"""
        ...


# ------------------------------------------------------------------
# 本地文件实现
# ------------------------------------------------------------------

class LocalFileTranscriptWriter:
    """本地 JSONL 事件流写入器（默认存储后端）。

    新会话默认 ``<sid>.jsonl.zst``：每次追加写一帧 zstd（一帧可含多行 JSONL），
    帧与帧可拼接，进程被杀最多丢掉正在写的最后一帧。既有明文 ``.jsonl``
    在首次追加时整体压成 ``.jsonl.zst`` 再继续写；只读不会改文件。
    """

    def __init__(self, session_file: Path, *, compress: bool | None = None) -> None:
        """初始化本地事件流写入器。

        Args:
            session_file: 事件流路径（``<sid>.jsonl`` 或 ``<sid>.jsonl.zst``）。
                传入 ``.jsonl`` 时，若该明文文件尚不存在，默认改写 ``.jsonl.zst``。
            compress: ``True`` 强制新会话走 zstd；``False`` 强制明文 JSONL、也不迁移；
                ``None``（默认）按 :func:`resolve_transcript_path` 自动选择，
                并在首次追加时把既有明文 ``.jsonl`` 迁成 ``.jsonl.zst``。
        """
        resolved, compressed = resolve_transcript_path(session_file, compress=compress)
        self._session_file = resolved
        self._compressed = compressed
        self._write_lock = asyncio.Lock()  # 防止并发写入交错
        self._next_seq: int | None = None  # 惰性从既有文件恢复，避免重启后 seq 冲突
        self._compressor = zstd.ZstdCompressor(level=ZSTD_LEVEL) if compressed else None
        self._migrate_to_zst = (
            compress is not False
            and not compressed
            and resolved.is_file()
            and resolved.name.endswith(JSONL_SUFFIX)
            and not resolved.name.endswith(JSONL_ZSTD_SUFFIX)
        )

    def retarget(self, directory: Path) -> None:
        """把后续读写改到新目录下的同名文件。调用前文件应已经迁走，且没有正在进行的写入。"""
        self._session_file = directory / self._session_file.name

    @property
    def session_file(self) -> Path:
        """事件流文件路径。"""
        return self._session_file

    @property
    def compressed(self) -> bool:
        """当前文件是否按 zstd 帧追加。"""
        return self._compressed

    @property
    def header_file(self) -> Path:
        """会话元信息侧车文件路径。"""
        session_id = transcript_session_id(self._session_file)
        return self._session_file.parent / (session_id + HEADER_SUFFIX)

    async def append_event(self, event: SessionEvent) -> SessionEvent:
        """追加一条事件；seq 在既有文件最大值之后继续分配。"""
        written = await self.append_events([event])
        return written[0]

    async def append_events(self, events: Sequence[SessionEvent]) -> list[SessionEvent]:
        """追加一批事件；压缩模式下整批打成一帧 zstd，明文模式按行拼接一次写入。"""
        if not events:
            return []
        async with self._write_lock:
            self._maybe_migrate_jsonl_to_zst()
            if self._next_seq is None:
                self._next_seq = self._scan_max_seq() + 1
            start = self._next_seq
            assigned: list[SessionEvent] = []
            for offset, event in enumerate(events):
                event.seq = start + offset
                assigned.append(event)
            payload = "".join(event.model_dump_json() + "\n" for event in assigned).encode(
                "utf-8"
            )
            if self._compressor is not None:
                payload = self._compressor.compress(payload)

            self._session_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self._session_file, "ab") as f:
                await f.write(payload)
            restrict_permissions(self._session_file)
            self._next_seq = start + len(assigned)
        return assigned

    async def read_events(self) -> list[SessionEvent]:
        """读取全部事件（按 seq 升序），跳过写入中断产生的残行 / 残帧。"""
        check_format_version(await self.read_header())
        if not self._session_file.exists():
            return []

        events: list[SessionEvent] = []
        async with self._write_lock:
            async with aiofiles.open(self._session_file, "rb") as f:
                data = await f.read()
        for raw in decode_transcript_bytes(data).splitlines():
            line = raw.strip()
            if not line:
                continue
            event = parse_event_line(line)
            if event is not None:
                events.append(event)
        events.sort(key=lambda e: e.seq)
        return events

    async def read_events_from(self, start_seq: int) -> list[SessionEvent]:
        """读取 ``seq >= start_seq`` 的事件。"""
        return [e for e in await self.read_events() if e.seq >= start_seq]

    async def write_header(self, header: SessionHeader) -> None:
        """把会话元信息写入侧车文件。"""
        async with self._write_lock:
            self.header_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(self.header_file, "w", encoding="utf-8") as f:
                await f.write(
                    json.dumps(header.to_dict(), ensure_ascii=False, indent=2) + "\n"
                )
            restrict_permissions(self.header_file)

    async def read_header(self) -> SessionHeader | None:
        """读取侧车文件中的会话元信息。"""
        if not self.header_file.exists():
            return None
        async with aiofiles.open(self.header_file, encoding="utf-8") as f:
            text = await f.read()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise VersionMismatchError(
                f"session header {self.header_file} is not valid JSON"
            ) from exc
        return SessionHeader.from_dict(data)

    async def flush(self) -> None:
        """等待在途写入结束（每次追加已开闭文件，无额外缓冲）。"""
        async with self._write_lock:
            pass

    def _maybe_migrate_jsonl_to_zst(self) -> None:
        """把既有明文 JSONL 一次压成 ``.jsonl.zst``，再删掉明文文件。

        先写临时文件再 replace，中断时明文仍在，下次追加会重试。
        只读路径不调用本方法，避免 list / viewer 改用户磁盘。
        """
        if not self._migrate_to_zst:
            return
        src = self._session_file
        if not src.is_file():
            self._migrate_to_zst = False
            return
        session_id = transcript_session_id(src)
        dest = src.parent / f"{session_id}{JSONL_ZSTD_SUFFIX}"
        payload = read_transcript_text(src).encode("utf-8")
        compressor = zstd.ZstdCompressor(level=ZSTD_LEVEL)
        compressed = compressor.compress(payload)
        tmp = dest.with_name(dest.name + ".tmp")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(compressed)
        restrict_permissions(tmp)
        tmp.replace(dest)
        restrict_permissions(dest)
        try:
            src.unlink()
        except OSError:
            logger.debug("Cannot remove plaintext transcript %s after zstd migrate", src)
            try:
                dest.unlink()
            except OSError:
                pass
            return
        self._session_file = dest
        self._compressed = True
        self._compressor = compressor
        self._migrate_to_zst = False

    def _scan_max_seq(self) -> int:
        """扫描既有文件的最大 seq；文件不存在或为空返回 0。"""
        if not self._session_file.exists():
            return 0
        max_seq = 0
        for raw in read_transcript_text(self._session_file).splitlines():
            line = raw.strip()
            if not line:
                continue
            event = parse_event_line(line)
            if event is not None and event.seq > max_seq:
                max_seq = event.seq
        return max_seq


__all__ = [
    "DEFAULT_SESSION_DIR",
    "HEADER_SUFFIX",
    "JSONL_SUFFIX",
    "JSONL_ZSTD_SUFFIX",
    "SESSION_DIR_MODE",
    "SESSION_FILE_MODE",
    "ZSTD_MAGIC",
    "LocalFileTranscriptWriter",
    "TranscriptWriter",
    "decode_transcript_bytes",
    "existing_transcript_path",
    "list_session_files",
    "parse_event_line",
    "peek_session_header",
    "read_transcript_text",
    "resolve_transcript_path",
    "restrict_permissions",
    "transcript_session_id",
]
