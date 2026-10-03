"""字节环形缓冲与溢出落盘。

模型能消费的输出有上限，但命令产生的字节流没有。超限的两种处置是「丢弃」或
「落盘并告知路径」——本模块选后者：内存保留尾部，超出部分写文件，并把文件路径
交给模型自行读取。DSH 的 ``maxOutputBytes`` / ``maxSpillBytes`` 双预算同义。

偏移（offset）空间是 **绝对字节偏移**：``[0, 已落盘字节数)`` 在文件里，
``[已落盘字节数, 总数)`` 在环形缓冲里。同一个游标在内存与文件之间无缝衔接，
后台作业从「晋升」转正时不需要重置游标。
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from nono_agent.shell.encoding import decode_output

# 会话文件权限对齐 session/transcript.py —— 事件流逐字保留工具输出，
# 落盘时同样必须挡住同机其它用户。
_SPILL_FILE_MODE = 0o600


@dataclass(frozen=True)
class OutputDelta:
    """一次增量读的结果。

    Attributes:
        text: 本次读到的文本。
        next_offset: 读完后的游标位置，传给下次 ``read_from``。
        start_offset: ``text`` 实际从哪里开始。``lossy`` 时大于请求的 offset
            —— 两者之差就是被环形缓冲淘汰、需要去文件里找的字节数。
        lossy: 请求的 offset 早于环形缓冲起点，中段数据已淘汰。
        spill_path: 完整输出的落盘路径（有过落盘才非 None）。
        truncated: 落盘也超了 ``max_spill_bytes``，尾部数据被永久丢弃。
    """

    text: str
    next_offset: int
    start_offset: int
    lossy: bool
    spill_path: Path | None
    truncated: bool


class SpillWriter:
    """字节环形缓冲 + 溢出落盘。

    落盘发生在**写入时**而非截断时：模型拿到路径的瞬间，文件已经包含全部历史输出。
    """

    def __init__(
        self,
        max_bytes: int,
        spill_path: Path | None,
        *,
        max_spill_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive, got {max_bytes}")
        if max_spill_bytes < 0:
            raise ValueError(f"max_spill_bytes must not be negative, got {max_spill_bytes}")
        self._max_bytes = max_bytes
        self._max_spill_bytes = max_spill_bytes
        self._spill_path = spill_path
        self._ring = bytearray()
        self._spilled = 0
        self._dropped = 0
        self._closed = False
        self._lock = threading.Lock()

    @property
    def spill_path(self) -> Path | None:
        return self._spill_path

    @property
    def total_bytes(self) -> int:
        """已观察到的总字节数（含已落盘与已丢弃）。"""
        with self._lock:
            return self._base_offset + len(self._ring)

    @property
    def _base_offset(self) -> int:
        """环形缓冲 [0] 对应的绝对偏移。

        被环形缓冲淘汰的字节（已落盘的 + 无处可放被丢弃的）都算作「已越过」——
        即使无处落盘，偏移空间也必须照常推进，否则调用方拿到的游标与文本
        实际起点不符，后续增量读会静默错位。
        """
        return self._spilled + self._dropped

    def write(self, data: bytes) -> None:
        """接收一块原始字节。线程安全。"""
        if not data or self._closed:
            return
        with self._lock:
            self._ring.extend(data)
            if len(self._ring) <= self._max_bytes:
                return
            overflow = bytes(self._ring[: len(self._ring) - self._max_bytes])
            del self._ring[: len(overflow)]
            if self._spill_path is None:
                self._dropped += len(overflow)  # 无落盘路径 → 纯丢弃
                return
            if self._spilled + len(overflow) > self._max_spill_bytes:
                self._dropped += len(overflow)  # 落盘也超限 → 停止写，保持已有内容
                return
            self._spill_path.parent.mkdir(parents=True, exist_ok=True)
            with self._spill_path.open("ab") as handle:
                handle.write(overflow)
            self._tighten_permissions()
            self._spilled += len(overflow)

    def read_from(self, offset: int = 0) -> OutputDelta:
        """从 ``offset`` 读。offset 早于环形起点时回吐能给的尾部并标 ``lossy``。

        解码走 :func:`~nono_agent.shell.encoding.decode_output` 的候选链而不是
        固定 utf-8：子进程不理会 PowerShell 的编码声明（见
        :mod:`nono_agent.shell.preamble`），中文 Windows 上按 CP936 输出，
        在这里换成任何单一编码都会把整段变成替换字符。
        """
        with self._lock:
            base = self._base_offset
            lossy = offset < base
            start = max(offset, base) - base
            raw = bytes(self._ring[start : start + self._max_bytes])
            return OutputDelta(
                text=decode_output(raw),
                next_offset=base + start + len(raw),
                start_offset=base + start,
                lossy=lossy,
                spill_path=self._spill_path if self._spilled else None,
                truncated=self._dropped > 0,
            )

    def close(self) -> Path | None:
        """结束写入。返回最终 spill 路径（没落过盘则为 None）。"""
        with self._lock:
            self._closed = True
            return self._spill_path if self._spilled else None

    def _tighten_permissions(self) -> None:
        """收紧落盘文件权限。Windows 上没有 POSIX 权限位，失败可忽略。"""
        if self._spill_path is None:
            return
        try:
            os.chmod(self._spill_path, _SPILL_FILE_MODE)
        except OSError:
            pass


__all__ = ["OutputDelta", "SpillWriter"]
