"""Task 工具族 —— 轻量后台任务生命周期（Create/Get/Update/List/Stop）。

会话级内存任务注册表，跨轮次共享（工具实例挂在 registry，随引擎生命周期）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result
from nono_agent.tools.protocol import ToolBase


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    STOPPED = "stopped"


@dataclass
class Task:
    """单个后台任务。"""

    id: str
    description: str
    status: str = TaskStatus.PENDING
    output: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskStore:
    """会话级内存任务注册表。"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}
        self._counter = 0

    def create(self, description: str, **metadata: Any) -> Task:
        self._counter += 1
        task = Task(id=str(self._counter), description=description, metadata=metadata)
        self._tasks[task.id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def update(self, task_id: str, **fields: Any) -> Task | None:
        task = self._tasks.get(task_id)
        if task is None:
            return None
        for key, value in fields.items():
            if hasattr(task, key):
                setattr(task, key, value)
        return task

    def list(self) -> list[Task]:
        return list(self._tasks.values())

    def stop(self, task_id: str) -> Task | None:
        return self.update(task_id, status=TaskStatus.STOPPED)


def _fmt(task: Task) -> str:
    return f"[{task.status}] #{task.id}: {task.description}" + (
        f"\n  output: {task.output}" if task.output else ""
    )


class TaskCreateTool(ToolBase):
    _name = "task_create"
    _description = "Create a lightweight background task and return its id."
    _input_schema = {
        "type": "object",
        "properties": {"description": {"type": "string"}},
        "required": ["description"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        task = self._store.create(input_params.get("description", ""))
        return ToolResult(tool_use_id="", content=f"Created task #{task.id}.", tool_name=self._name)


class TaskListTool(ToolBase):
    _name = "task_list"
    _description = "List all tasks."
    _input_schema = {"type": "object", "properties": {}}
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        tasks = self._store.list()
        if not tasks:
            return ok_result(self._name, "(no tasks)")
        return ok_result(self._name, "\n".join(_fmt(t) for t in tasks))


class TaskGetTool(ToolBase):
    _name = "task_get"
    _description = "Get a single task by id."
    _input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        task = self._store.get(str(input_params.get("task_id", "")))
        if task is None:
            return error_result(self._name, "Error: task not found.")
        return ok_result(self._name, _fmt(task))


class TaskUpdateTool(ToolBase):
    _name = "task_update"
    _description = "Update a task's status/output."
    _input_schema = {
        "type": "object",
        "properties": {
            "task_id": {"type": "string"},
            "status": {
                "type": "string",
                "enum": ["pending", "running", "completed", "stopped"],
            },
            "output": {"type": "string"},
        },
        "required": ["task_id"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        fields = {
            k: v
            for k, v in input_params.items()
            if k in ("status", "output") and v is not None
        }
        task = self._store.update(str(input_params.get("task_id", "")), **fields)
        if task is None:
            return error_result(self._name, "Error: task not found.")
        return ok_result(self._name, _fmt(task))


class TaskStopTool(ToolBase):
    _name = "task_stop"
    _description = "Stop a task."
    _input_schema = {
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = False

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        task = self._store.stop(str(input_params.get("task_id", "")))
        if task is None:
            return error_result(self._name, "Error: task not found.")
        return ok_result(self._name, _fmt(task))


def create_task_tools(store: TaskStore) -> list[ToolBase]:
    """创建共享同一 TaskStore 的 Task 工具族。"""
    return [
        TaskCreateTool(store),
        TaskListTool(store),
        TaskGetTool(store),
        TaskUpdateTool(store),
        TaskStopTool(store),
    ]
