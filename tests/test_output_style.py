"""Tests for the Output Styles prompt section."""

from nono_agent.prompt import (
    OutputStyleSection,
    PromptAssembler,
    PromptContext,
    SectionRegistry,
)


def _assemble(style: str | None) -> str:
    registry = SectionRegistry()
    registry.register(OutputStyleSection())
    assembler = PromptAssembler(registry)
    ctx = PromptContext(output_style=style)
    return assembler.assemble(ctx).full


class TestOutputStyle:
    def test_concise(self):
        out = _assemble("concise")
        assert "concise" in out

    def test_detailed(self):
        out = _assemble("detailed")
        assert "detailed" in out

    def test_unknown_style_renders_nothing(self):
        assert _assemble("nonsense") == ""

    def test_none_renders_nothing(self):
        assert _assemble(None) == ""
