"""Tests for the Task tool family."""

import pytest

from nono_agent.tools.builtin.task import TaskStatus, TaskStore, create_task_tools
from nono_agent.tools.context import ToolUseContext


@pytest.fixture
def store():
    return TaskStore()


@pytest.fixture
def ctx():
    return ToolUseContext(cwd="/tmp")


class TestTaskStore:
    def test_lifecycle(self, store):
        task = store.create("do a thing")
        assert task.id == "1"
        assert task.status == TaskStatus.PENDING

        store.update(task.id, status=TaskStatus.RUNNING)
        assert store.get(task.id).status == TaskStatus.RUNNING

        store.stop(task.id)
        assert store.get(task.id).status == TaskStatus.STOPPED

    def test_list(self, store):
        store.create("a")
        store.create("b")
        assert len(store.list()) == 2


class TestTaskTools:
    @pytest.mark.asyncio
    async def test_create_list_get(self, store, ctx):
        tools = {t.name: t for t in create_task_tools(store)}
        r = await tools["task_create"].execute({"description": "build"}, ctx)
        assert "Created task #1" in r.content

        r = await tools["task_list"].execute({}, ctx)
        assert "build" in r.content

        r = await tools["task_get"].execute({"task_id": "1"}, ctx)
        assert "build" in r.content

    @pytest.mark.asyncio
    async def test_update_and_stop(self, store, ctx):
        tools = {t.name: t for t in create_task_tools(store)}
        store.create("job")
        r = await tools["task_update"].execute(
            {"task_id": "1", "status": "completed", "output": "done"}, ctx
        )
        assert "completed" in r.content

        store.create("job2")
        r = await tools["task_stop"].execute({"task_id": "2"}, ctx)
        assert "stopped" in r.content

    @pytest.mark.asyncio
    async def test_get_missing(self, store, ctx):
        tools = {t.name: t for t in create_task_tools(store)}
        r = await tools["task_get"].execute({"task_id": "99"}, ctx)
        assert r.is_error
