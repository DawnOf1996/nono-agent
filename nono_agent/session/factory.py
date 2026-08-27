"""Transcript Writer 工厂 —— 按配置创建事件流后端。

支持 ``file``（JSONL 事件流，默认）与 ``sqlite``（events 表）两种后端，
便于 CLI/Server 切换。
"""

from __future__ import annotations

from pathlib import Path

from nono_agent.session.db_transcript import SQLiteTranscriptWriter
from nono_agent.session.transcript import LocalFileTranscriptWriter, TranscriptWriter

# 默认后端标识（LocalFile JSONL 事件流）
DEFAULT_TRANSCRIPT_BACKEND = "file"


def create_transcript_writer(
    kind: str = DEFAULT_TRANSCRIPT_BACKEND,
    path: str | Path = "",
) -> TranscriptWriter:
    """按 kind 创建事件流写入器。

    Args:
        kind: ``"file"``（JSONL 事件流）或 ``"sqlite"``（events 表）。
        path: 事件流文件路径（JSONL 文件或 SQLite 文件）。

    Returns:
        对应后端实例。

    Raises:
        ValueError: kind 不是已知后端。
    """
    p = Path(path)
    if kind == "file":
        return LocalFileTranscriptWriter(p)
    if kind == "sqlite":
        return SQLiteTranscriptWriter(p)
    raise ValueError(
        f"Unknown transcript backend '{kind}'. Available: 'file', 'sqlite'."
    )


__all__ = ["DEFAULT_TRANSCRIPT_BACKEND", "create_transcript_writer"]
