"""Tests for the skills package and SkillTool."""

import pytest

from nono_agent.adapters.testing import create_test_engine
from nono_agent.skills import FileSystemSkillLoader, Skill, SkillTool
from nono_agent.tools.context import ToolUseContext


class TestFileSystemSkillLoader:
    def test_load_skill(self, tmp_path):
        skill_dir = tmp_path / ".nono-agent" / "skills" / "code-review"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: code-review\ndescription: Review code\ntools: ['read_file', 'grep']\n---\n"
            "Look for bugs and style issues.\n"
        )

        loader = FileSystemSkillLoader(user_skills_dir=tmp_path / "no-user")
        skills = loader.load(tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "code-review"
        assert skills[0].description == "Review code"
        assert skills[0].tools == ["read_file", "grep"]
        assert "bugs" in skills[0].content

    def test_empty(self, tmp_path):
        loader = FileSystemSkillLoader(user_skills_dir=tmp_path / "no-user")
        assert loader.load(tmp_path) == []


class TestSkillTool:
    @pytest.mark.asyncio
    async def test_activate(self):
        skills = [Skill(name="review", description="d", content="INSTRUCTIONS", source="x")]
        tool = SkillTool(skills)
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute({"skill": "review"}, ctx)
        assert not result.is_error
        assert "INSTRUCTIONS" in result.content

    @pytest.mark.asyncio
    async def test_unknown_skill_lists_available(self):
        skills = [Skill(name="review", description="d", content="c", source="x")]
        tool = SkillTool(skills)
        ctx = ToolUseContext(cwd="/tmp")
        result = await tool.execute({"skill": "nope"}, ctx)
        assert result.is_error
        assert "review" in result.content


class TestEngineSkillToggle:
    def test_set_skill_enabled(self):
        engine = create_test_engine()
        engine._skills = [Skill(name="review", description="d", content="c", source="x")]
        assert engine.set_skill_enabled("review", False) is True
        assert engine.skills[0].enabled is False
        assert engine.set_skill_enabled("nope", False) is False
