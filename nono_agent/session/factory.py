"""Transcript Writer 工厂 —— 按配置创建存储后端。

支持 ``file``（NDJSON）与 ``sqlite`` 两种后端，便于 CLI/Server 切换。
"""

from __future__ import annotations

from pathlib import Path

from nono_agent.session.db_transcript import SQLiteTranscriptWriter
from nono_agent.session.transcript import LocalFileTranscriptWriter, TranscriptWriter


def create_transcript_writer(
    kind: str,
    path: str | Path,
    *,
    eager_flush: bool = False,
) -> TranscriptWriter:
    """按 kind 创建 TranscriptWriter。

    Args:
        kind: ``"file"`` 或 ``"sqlite"``
        path: 文件路径（NDJSON 文件或 SQLite 文件）
    """
    p = Path(path)
    if kind == "file":
        return LocalFileTranscriptWriter(p, eager_flush=eager_flush)
    if kind == "sqlite":
        return SQLiteTranscriptWriter(p)
    raise ValueError(
        f"Unknown transcript backend '{kind}'. Available: 'file', 'sqlite'."
    )
