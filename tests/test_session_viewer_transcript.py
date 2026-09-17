"""session-viewer 读取明文 JSONL 与 zstd 事件流。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from nono_agent.session import LocalFileTranscriptWriter, SessionEvent
from nono_agent.session.events import EVENT_USER_MESSAGE

_VIEWER_SERVER = Path(__file__).resolve().parents[1] / "session-viewer" / "server.py"


def _load_viewer():
    spec = importlib.util.spec_from_file_location("session_viewer_server", _VIEWER_SERVER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_viewer_reads_zst_and_plain(tmp_path, monkeypatch):
    viewer = _load_viewer()
    monkeypatch.setenv("NONO_SESSION_DIR", str(tmp_path))

    zst = LocalFileTranscriptWriter(tmp_path / "comp.jsonl")
    await zst.append_event(SessionEvent.make(0, EVENT_USER_MESSAGE, content="compressed"))
    plain = LocalFileTranscriptWriter(tmp_path / "plain.jsonl", compress=False)
    await plain.append_event(SessionEvent.make(0, EVENT_USER_MESSAGE, content="plain"))

    compressed_events = viewer._read_events("comp")
    assert compressed_events[0]["data"]["content"] == "compressed"

    plain_events = viewer._read_events("plain")
    assert plain_events[0]["data"]["content"] == "plain"

    listed = {item["session_id"]: item["event_count"] for item in viewer._list_sessions(tmp_path)}
    assert listed == {"comp": 1, "plain": 1}
