"""Tests for the tool system."""

import pytest

from nono_agent.core.types import ToolResult, ToolUseBlock
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.executor import BatchToolExecutor
from nono_agent.tools.protocol import ToolBase
from nono_agent.tools.registry import ToolNotFound, ToolRegistry


class MockReadTool(ToolBase):
    _name = "read_file"
    _description = "Read a file"
    _input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params, context):
        return ToolResult(
            tool_use_id="",
            content=f"Contents of {input_params.get('path', 'unknown')}",
            tool_name="read_file",
        )


class MockWriteTool(ToolBase):
    _name = "write_file"
    _description = "Write a file"
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = True

    async def execute(self, input_params, context):
        return ToolResult(
            tool_use_id="",
            content=f"Written to {input_params.get('path')}",
            tool_name="write_file",
        )


class TestToolRegistry:
    def test_register_and_find(self):
        registry = ToolRegistry()
        tool = MockReadTool()
        registry.register(tool)

        found = registry.find("read_file")
        assert found is tool

    def test_register_duplicate(self):
        registry = ToolRegistry()
        registry.register(MockReadTool())

        with pytest.raises(ValueError):
            registry.register(MockReadTool())

    def test_find_not_found(self):
        registry = ToolRegistry()

        with pytest.raises(ToolNotFound):
            registry.find("nonexistent")

    def test_unregister(self):
        registry = ToolRegistry()
        registry.register(MockReadTool())

        removed = registry.unregister("read_file")
        assert removed is not None
        assert not registry.has("read_file")

    def test_to_api_schema(self):
        registry = ToolRegistry()
        registry.register(MockReadTool())
        registry.register(MockWriteTool())

        schemas = registry.to_api_schema()
        assert len(schemas) == 2
        names = {s["name"] for s in schemas}
        assert names == {"read_file", "write_file"}

    def test_partition_by_concurrency(self):
        registry = ToolRegistry()
        registry.register(MockReadTool())
        registry.register(MockWriteTool())

        tool_uses = [
            {"name": "read_file", "id": "1", "input": {"path": "a.txt"}},
            {"name": "write_file", "id": "2", "input": {"path": "b.txt", "content": "x"}},
            {"name": "read_file", "id": "3", "input": {"path": "c.txt"}},
            {"name": "unknown_tool", "id": "4", "input": {}},
        ]

        concurrent, sequential = registry.partition_by_concurrency(tool_uses)

        assert len(concurrent) == 2  # Two read_file calls
        assert len(sequential) == 2  # One write_file + one unknown (default fail-closed)


class TestToolUseContext:
    @pytest.mark.asyncio
    async def test_default_permission(self):
        ctx = ToolUseContext(cwd="/tmp")
        # No checker = permissive
        assert await ctx.check_permission("write_file", {}) is True

    @pytest.mark.asyncio
    async def test_custom_permission_checker(self):
        denied_tools = {"write_file"}

        def checker(tool_name, params):
            return tool_name not in denied_tools

        ctx = ToolUseContext(cwd="/tmp", permission_checker=checker)

        assert await ctx.check_permission("read_file", {}) is True
        assert await ctx.check_permission("write_file", {}) is False

    def test_with_extra(self):
        ctx = ToolUseContext(cwd="/tmp")
        ctx2 = ctx.with_extra(project_root="/home/user")

        assert ctx2.extra["project_root"] == "/home/user"
        assert ctx2.cwd == "/tmp"


class TestBatchToolExecutor:
    @pytest.mark.asyncio
    async def test_execute_multiple_tools(self):
        registry = ToolRegistry()
        registry.register(MockReadTool())

        executor = BatchToolExecutor(registry)
        ctx = ToolUseContext(cwd="/tmp")

        blocks = [
            ToolUseBlock(id="t1", name="read_file", input={"path": "a.txt"}),
            ToolUseBlock(id="t2", name="read_file", input={"path": "b.txt"}),
        ]

        results = await executor.execute(blocks, ctx)
        assert len(results) == 2
        assert all(not r.result.is_error for r in results)

    @pytest.mark.asyncio
    async def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        executor = BatchToolExecutor(registry)
        ctx = ToolUseContext(cwd="/tmp")

        blocks = [ToolUseBlock(id="t1", name="nonexistent", input={})]
        results = await executor.execute(blocks, ctx)

        assert len(results) == 1
        assert results[0].result.is_error
        assert "not found" in results[0].result.content

    @pytest.mark.asyncio
    async def test_execute_and_format(self):
        registry = ToolRegistry()
        registry.register(MockReadTool())

        executor = BatchToolExecutor(registry)
        ctx = ToolUseContext(cwd="/tmp")

        blocks = [ToolUseBlock(id="t1", name="read_file", input={"path": "test.py"})]
        formatted = await executor.execute_and_format(blocks, ctx)

        assert len(formatted) == 1
        assert formatted[0]["type"] == "tool_result"
        assert formatted[0]["tool_use_id"] == "t1"


class TestToolProtocol:
    def test_fail_closed_defaults(self):
        """Verify fail-closed security defaults."""
        tool = MockWriteTool()
        assert tool.is_concurrent_safe is False
        assert tool.has_write_side_effects is True
        assert tool.is_read_only is False

    def test_read_only_tool(self):
        tool = MockReadTool()
        assert tool.is_concurrent_safe is True
        assert tool.has_write_side_effects is False
        assert tool.is_read_only is True
