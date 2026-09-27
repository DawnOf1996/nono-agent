"""多会话并发：权限请求按会话挂起，互不干扰。"""

from __future__ import annotations

import asyncio

import pytest

from nono_agent.core.config import EngineConfig
from nono_app_server.engine.session_manager import SessionManager
from nono_app_server.features.permissions import service as perms


@pytest.fixture(autouse=True)
def _clear_pending():
    perms._pending.clear()
    yield
    perms._pending.clear()


async def _wait_for_pending(session_id: str, timeout: float = 1.0) -> dict:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        items = perms.list_pending(session_id)
        if items:
            return items[0]
        await asyncio.sleep(0.01)
    raise AssertionError(f"no pending permission for {session_id}")


class TestSessionScopedPermissions:
    @pytest.mark.asyncio
    async def test_request_is_tagged_with_session(self):
        checker = perms.make_permission_checker("sess-a")
        task = asyncio.create_task(checker("bash", {"command": "ls"}))
        info = await _wait_for_pending("sess-a")

        assert info["session_id"] == "sess-a"
        assert info["tool_name"] == "bash"
        assert perms.list_pending("sess-b") == []

        assert perms.answer_permission(info["request_id"], True)
        assert await task is True

    @pytest.mark.asyncio
    async def test_one_suspended_session_does_not_block_another(self):
        """A 挂起等待授权时，B 仍能拿到自己的授权结果并继续。"""
        task_a = asyncio.create_task(perms.make_permission_checker("A")("bash", {}))
        info_a = await _wait_for_pending("A")

        task_b = asyncio.create_task(perms.make_permission_checker("B")("write_file", {}))
        info_b = await _wait_for_pending("B")

        # 只回答 B —— A 继续挂着，不受影响。
        assert perms.answer_permission(info_b["request_id"], True)
        assert await asyncio.wait_for(task_b, timeout=1) is True
        assert not task_a.done()
        assert [item["session_id"] for item in perms.list_pending("A")] == ["A"]

        # 切回 A 确认后，A 才继续执行。
        assert perms.answer_permission(info_a["request_id"], True)
        assert await asyncio.wait_for(task_a, timeout=1) is True

    @pytest.mark.asyncio
    async def test_pending_waits_instead_of_timing_out(self, monkeypatch):
        """默认不超时：一直挂到用户回来确认。"""
        monkeypatch.delenv(perms.ENV_PERMISSION_TIMEOUT, raising=False)
        task = asyncio.create_task(perms.make_permission_checker("slow")("bash", {}))
        info = await _wait_for_pending("slow")

        await asyncio.sleep(0.2)
        assert not task.done()

        perms.answer_permission(info["request_id"], False)
        assert await asyncio.wait_for(task, timeout=1) is False

    @pytest.mark.asyncio
    async def test_cancel_only_targets_one_session(self):
        task_a = asyncio.create_task(perms.make_permission_checker("A")("bash", {}))
        await _wait_for_pending("A")
        task_b = asyncio.create_task(perms.make_permission_checker("B")("bash", {}))
        info_b = await _wait_for_pending("B")

        assert perms.cancel_session_permissions("A") == 1
        assert await asyncio.wait_for(task_a, timeout=1) is False
        assert not task_b.done()

        perms.answer_permission(info_b["request_id"], True)
        assert await asyncio.wait_for(task_b, timeout=1) is True

    @pytest.mark.asyncio
    async def test_env_timeout_denies(self, monkeypatch):
        monkeypatch.setenv(perms.ENV_PERMISSION_TIMEOUT, "0.05")
        allowed = await perms.make_permission_checker("t")("bash", {})
        assert allowed is False
        assert perms.list_pending("t") == []


class TestManagerPermissionWiring:
    def test_factory_binds_checker_per_session(self, tmp_path):
        seen: list[str] = []

        def factory(session_id: str):
            seen.append(session_id)
            return lambda tool, params: True

        manager = SessionManager(
            EngineConfig(cwd=str(tmp_path)),
            None,
            permission_checker_factory=factory,
        )
        first = manager.checker_for("s1")
        second = manager.checker_for("s2")

        assert seen == ["s1", "s2"]
        assert first is not second

    def test_falls_back_to_static_checker(self, tmp_path):
        def static(tool: str, params: dict) -> bool:
            return False

        manager = SessionManager(EngineConfig(cwd=str(tmp_path)), static)
        assert manager.checker_for("s1") is static

    def test_cancel_permissions_uses_hook(self, tmp_path):
        cancelled: list[str] = []
        manager = SessionManager(
            EngineConfig(cwd=str(tmp_path)),
            None,
            permission_canceller=cancelled.append,
        )
        manager.cancel_permissions("s9")
        assert cancelled == ["s9"]

    def test_cancel_permissions_without_hook_is_noop(self, tmp_path):
        manager = SessionManager(EngineConfig(cwd=str(tmp_path)), None)
        manager.cancel_permissions("s9")
