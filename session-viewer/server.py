"""Nono Agent Session Viewer —— 会话轨迹可视化前端服务。

一个极简、无重依赖的后端：把 ``~/.nono-agent/sessions/`` 里的事件流 JSONL
读出来，通过几个 JSON API 提供给同目录 ``static/`` 下的纯前端页面渲染。

本项目自带 ``pyproject.toml``，用 ``uv`` 管理虚拟环境::

    cd session-viewer
    uv sync
    uv run python server.py
    # 然后打开 http://127.0.0.1:8765

前端既能按 ``session_id`` 从本服务拉取会话，也能拖入/选择一个离线的
``<sid>.jsonl`` 文件，全部解析与渲染都在浏览器内完成。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

# 默认会话目录（nono-agent 的事件流落盘位置）
DEFAULT_SESSION_DIR = Path.home() / ".nono-agent" / "sessions"

app = FastAPI(title="Nono Agent Session Viewer", version="0.1.0")


def _session_dir() -> Path:
    """会话目录。默认读 ``~/.nono-agent/sessions/``，可用环境变量覆盖便于测试。"""
    override = os.environ.get("NONO_SESSION_DIR")
    return Path(override).expanduser() if override else DEFAULT_SESSION_DIR


def _read_header(session_id: str) -> dict | None:
    """读取会话元信息侧车文件（``<sid>.header.json``），没有则返回 None。"""
    header_file = _session_dir() / f"{session_id}.header.json"
    if not header_file.is_file():
        return None
    try:
        obj = json.loads(header_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return obj if isinstance(obj, dict) else None


def _read_events(session_id: str) -> list[dict]:
    """逐行读取事件流，跳过写入中断产生的残行，返回原始事件字典列表。"""
    session_file = _session_dir() / f"{session_id}.jsonl"
    if not session_file.is_file():
        raise HTTPException(status_code=404, detail=f"session not found: {session_id}")

    try:
        text = session_file.read_text(encoding="utf-8")
    except OSError as exc:  # 权限/IO 错误
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    events: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue  # 残行自愈
        if isinstance(obj, dict) and "seq" in obj and "data" in obj:
            events.append(obj)
    return events


def _list_sessions(session_dir: Path | None = None) -> list[dict]:
    """列出会话目录下的事件流文件（id + 事件数 + 最后修改时间 + 元信息）。"""
    directory = session_dir or _session_dir()
    if not directory.is_dir():
        return []

    sessions: list[dict] = []
    for file in sorted(directory.glob("*.jsonl")):
        try:
            line_count = sum(1 for line in file.open(encoding="utf-8") if line.strip())
        except OSError:
            line_count = 0
        sessions.append(
            {
                "session_id": file.stem,
                "event_count": line_count,
                "last_modified": file.stat().st_mtime,
            }
        )
    return sessions


@app.get("/api/sessions")
def api_sessions() -> list[dict]:
    """列出本地会话（id + 事件数 + 元信息）。"""
    sessions = _list_sessions()
    for item in sessions:
        item["header"] = _read_header(item["session_id"])
    return sessions


@app.get("/api/session/{session_id}")
def api_session(session_id: str) -> dict:
    """返回单个会话的元信息与完整事件流。"""
    return {
        "session_id": session_id,
        "header": _read_header(session_id),
        "events": _read_events(session_id),
    }


@app.get("/api/session/{session_id}/events")
def api_session_events(session_id: str) -> list[dict]:
    """仅返回事件流（供前端按需刷新）。"""
    return _read_events(session_id)


@app.get("/api/health")
def api_health() -> dict:
    """健康检查 + 会话目录可达性提示。"""
    directory = _session_dir()
    return {
        "ok": True,
        "session_dir": str(directory),
        "exists": directory.is_dir(),
        "sessions": len(_list_sessions()),
    }


# 前端入口 + 静态资源
@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    """启动服务（uvicorn）。"""
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8765)


if __name__ == "__main__":
    main()
