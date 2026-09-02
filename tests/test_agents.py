"""Tests for the sub-agent triple (AgentDefinition + AgentTool + AgentRunner)."""

import pytest

from nono_agent.adapters.testing import MockProvider, MockTool
from nono_agent.agents import (
    AgentDefinition,
    AgentRunner,
    AgentTool,
    FileSystemAgentLoader,
    create_builtin_agents,
)
from nono_agent.core.config import EngineConfig
from nono_agent.providers.base import ProviderRegistry
from nono_agent.tools.context import ToolUseContext
from nono_agent.tools.registry import ToolRegistry


class TestAgentDefinition:
    def test_builtin_agents(self):
        agents = create_builtin_agents()
        names = {a.name for a in agents}
        assert "general-purpose" in names
        assert "explore" in names
        assert agents[1].tools == ["read_file", "glob", "grep"]

    def test_from_dict(self):
        d = AgentDefinition.from_dict(
            {"name": "x", "description": "desc", "system_prompt": "SP", "tools": ["a", "b"]}
        )
        assert d.name == "x"
        assert d.tools == ["a", "b"]
        assert d.system_prompt == "SP"


class TestFileSystemAgentLoader:
    def test_load_agent(self, tmp_path):
        agents_dir = tmp_path / ".nono-agent" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text(
            "---\nname: reviewer\ndescription: Review\ntools: ['read_file', 'grep']\n---\n"
            "You are a reviewer.\n"
        )
        loader = FileSystemAgentLoader(user_agents_dir=tmp_path / "no-user")
        agents = loader.load(tmp_path)
        assert len(agents) == 1
        assert agents[0].name == "reviewer"
        assert agents[0].tools == ["read_file", "grep"]
        assert "reviewer" in agents[0].system_prompt


def _make_runner() -> AgentRunner:
    provider = MockProvider(responses=[{"text": "SUB RESULT", "tool_calls": []}])
    registry = ProviderRegistry()
    registry.register_instance("test-model", provider)

    tool_registry = ToolRegistry()
    tool_registry.register(
        MockTool(name="read_file", concurrent_safe=True, write_side_effects=False)
    )

    base_config = EngineConfig(model="test-model")
    return AgentRunner(registry, tool_registry, base_config)


class TestAgentRunner:
    @pytest.mark.asyncio
    async def test_run(self):
        runner = _make_runner()
        definition = AgentDefinition(
            name="general-purpose",
            description="d",
            system_prompt="You are a sub-agent.",
        )
        result = await runner.run(definition, "do a subtask")
        assert not result.is_error
        assert "SUB RESULT" in result.text

    @pytest.mark.asyncio
    async def test_whitelist(self):
        runner = _make_runner()
        definition = AgentDefinition(
            name="readonly",
            description="d",
            system_prompt="sp",
            tools=["read_file"],
        )
        sub = runner.build_sub_tool_registry(definition)
        assert sub.names() == ["read_file"]


class TestAgentTool:
    @pytest.mark.asyncio
    async def test_dispatch(self):
        runner = _make_runner()
        definitions = [
            AgentDefinition(name="general-purpose", description="d", system_prompt="sp")
        ]
        tool = AgentTool(definitions=definitions, runner=runner)
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute(
            {"subagent_type": "general-purpose", "prompt": "do it"}, ctx
        )
        assert not result.is_error
        assert "SUB RESULT" in result.content

    @pytest.mark.asyncio
    async def test_unknown_agent(self):
        tool = AgentTool(definitions=[], runner=None)
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute(
            {"subagent_type": "nope", "prompt": "x"}, ctx
        )
        assert result.is_error
        assert "Unknown sub-agent" in result.content
