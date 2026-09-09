"""Tests for the skills package and SkillTool."""

import pytest

from nono_agent.adapters.testing import create_test_engine
from nono_agent.skills import FileSystemSkillLoader, Skill, SkillTool, parse_skill_markdown
from nono_agent.skills.loader import coerce_meta_str
from nono_agent.tools.context import ToolUseContext


class TestParseSkillMarkdown:
    def test_folded_description(self):
        text = (
            "---\n"
            "name: ppt-master\n"
            "description: >\n"
            "  AI-driven presentation workflow for generating editable PPTX decks.\n"
            "  Use when the user asks to create a presentation or mentions ppt-master.\n"
            "metadata:\n"
            '  version: "6.3.1"\n'
            "---\n"
            "# PPT Master\n"
        )
        meta, body = parse_skill_markdown(text)
        assert meta["name"] == "ppt-master"
        assert "AI-driven presentation workflow" in meta["description"]
        assert "mentions ppt-master" in meta["description"]
        assert ">" not in meta["description"]
        assert meta["metadata"]["version"] == "6.3.1"
        assert body.startswith("# PPT Master")
        assert "mentions ppt-master" in coerce_meta_str(meta["description"], "ppt-master")

    def test_literal_description(self):
        text = (
            "---\n"
            "name: demo\n"
            "description: |\n"
            "  first line\n"
            "  second line\n"
            "---\n"
            "Body here.\n"
        )
        meta, _body = parse_skill_markdown(text)
        assert "first line" in meta["description"]
        assert "second line" in meta["description"]
        assert coerce_meta_str(meta["description"], "demo") == "first line second line"

    def test_yaml_list_tools(self):
        text = (
            "---\n"
            "name: review\n"
            "description: Review code\n"
            "tools:\n"
            "  - read_file\n"
            "  - grep\n"
            "---\n"
            "Look for bugs.\n"
        )
        meta, _body = parse_skill_markdown(text)
        assert meta["tools"] == ["read_file", "grep"]

    def test_invalid_yaml_keeps_body(self):
        text = "---\nname: [unterminated\n---\nStill a body.\n"
        meta, body = parse_skill_markdown(text)
        assert meta == {}
        assert "Still a body" in body

    def test_no_frontmatter(self):
        meta, body = parse_skill_markdown("Just markdown.\n")
        assert meta == {}
        assert body == "Just markdown.\n"


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

    def test_load_folded_description(self, tmp_path):
        skill_dir = tmp_path / ".nono-agent" / "skills" / "ppt-master"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: ppt-master\ndescription: >\n"
            "  AI-driven presentation workflow for generating editable PPTX decks and slides,\n"
            "  reconstructing page visuals, creating reusable Brand/Style/Layout/Deck\n"
            "  workspaces. Use when the user mentions ppt-master.\n"
            "---\nPPT Master is a routed presentation workflow.\n"
        )
        loader = FileSystemSkillLoader(user_skills_dir=tmp_path / "no-user")
        skills = loader.load(tmp_path)
        assert len(skills) == 1
        assert skills[0].name == "ppt-master"
        assert skills[0].description.startswith("AI-driven presentation workflow")
        assert "mentions ppt-master" in skills[0].description
        assert skills[0].description != ">"

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
