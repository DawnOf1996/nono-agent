"""任务板：登记、实时输出、完成后保留，以及强制停止。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from starlette.requests import Request  # noqa: E402

from nono_agent.core.config import EngineConfig  # noqa: E402
from nono_agent.jobs.board import (  # noqa: E402
    append_output,
    finish_job,
    kill_job,
    list_jobs,
    open_job,
    reset_jobs,
    subscribe,
)
from nono_app_server.config import SidecarSettings  # noqa: E402
from nono_app_server.engine import create_server_app  # noqa: E402
from nono_app_server.features.jobs.api import stream_jobs  # noqa: E402
from nono_app_server.features.router import mount_product_routes  # noqa: E402


def setup_function() -> None:
    reset_jobs()


def teardown_function() -> None:
    reset_jobs()


def test_job_output_and_finish() -> None:
    job_id = open_job(session_id="s1", kind="command", title="echo hello")
    append_output(job_id, "hello\n")
    finish_job(job_id, status="completed", exit_code=0)
    rows = list_jobs("s1")
    assert rows[0]["output"] == "hello\n"
    assert rows[0]["status"] == "completed"
    assert rows[0]["exit_code"] == 0
    assert kill_job("s1", job_id) is False


def test_kill_marks_job_and_invokes_callback() -> None:
    calls: list[str] = []
    job_id = open_job(
        session_id="s1",
        kind="agent",
        title="explore",
        kill=lambda: calls.append("stop"),
    )
    child = open_job(session_id="s1", kind="command", title="ls", parent_id=job_id)
    assert kill_job("other", job_id) is False
    assert kill_job("s1", job_id) is True
    assert calls == ["stop"]
    finish_job(job_id, status="completed", exit_code=0)
    rows = {item["id"]: item for item in list_jobs("s1")}
    assert rows[job_id]["status"] == "killed"
    assert rows[child]["parent_id"] == job_id


def test_jobs_route_lists_and_stops(tmp_path: Path) -> None:
    stopped: list[str] = []

    def _stop() -> None:
        stopped.append("x")

    job_id = open_job(session_id="sess", kind="command", title="sleep", kill=_stop)
    config = EngineConfig(cwd=str(tmp_path))
    app = create_server_app(config)
    mount_product_routes(
        app,
        SidecarSettings(
            host="127.0.0.1",
            port=8000,
            cwd=str(tmp_path),
            cors_origins=[],
            catalog_ready=False,
            catalog_error=None,
        ),
        config,
        db_path=tmp_path / "app.db",
        import_engine_index=False,
    )
    with fastapi_testclient.TestClient(app) as client:
        listed = client.get("/app/jobs", params={"session_id": "sess"})
        killed = client.post(f"/app/jobs/{job_id}/kill", params={"session_id": "sess"})
        again = client.post(f"/app/jobs/{job_id}/kill", params={"session_id": "sess"})
    assert listed.status_code == 200
    assert listed.json()["jobs"][0]["title"] == "sleep"
    assert killed.status_code == 200
    assert stopped == ["x"]
    assert again.status_code == 404


def test_subscribe_pushes_snapshot_after_change() -> None:
    seen: list[list[dict]] = []
    unsubscribe = subscribe("s1", seen.append)
    try:
        open_job(session_id="s1", kind="command", title="echo")
        deadline = time.time() + 1
        while not seen and time.time() < deadline:
            time.sleep(0.02)
        assert seen[-1][0]["title"] == "echo"
        append_output(seen[-1][0]["id"], "hi")
        deadline = time.time() + 1
        while seen[-1][0]["output"] != "hi" and time.time() < deadline:
            time.sleep(0.02)
        assert seen[-1][0]["output"] == "hi"
    finally:
        unsubscribe()


@pytest.mark.asyncio
async def test_jobs_stream_sends_current_list() -> None:
    open_job(session_id="sess", kind="command", title="sleep")
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/jobs/stream",
        "raw_path": b"/jobs/stream",
        "query_string": b"session_id=sess",
        "headers": [],
        "client": ("127.0.0.1", 123),
        "server": ("127.0.0.1", 8000),
    }
    response = await stream_jobs(Request(scope), "sess")
    chunks = response.body_iterator
    first = await anext(chunks)
    await chunks.aclose()
    assert "event: jobs" in first
    assert "sleep" in first
