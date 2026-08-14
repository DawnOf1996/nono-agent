"""Tests for the prompt assembly pipeline."""

from nono_agent.prompt import (
    AGENT_PRIORITY,
    DYNAMIC_BOUNDARY,
    PromptAssembler,
    PromptContext,
    PromptSectionBase,
    SectionRegistry,
    create_default_sections,
)


class FakeSection(PromptSectionBase):
    def __init__(self, name, text, dynamic=False, priority=0):
        self._name = name
        self._text = text
        self._is_dynamic = dynamic
        self._priority = priority

    def render(self, ctx):
        return self._text


class TestSectionRegistry:
    def test_register_order(self):
        registry = SectionRegistry()
        registry.register(FakeSection("a", "A"))
        registry.register(FakeSection("b", "B"))
        assert registry.names() == ["a", "b"]

    def test_register_before_after(self):
        registry = SectionRegistry()
        registry.register(FakeSection("a", "A"))
        registry.register(FakeSection("b", "B"), before="a")
        assert registry.names() == ["b", "a"]

    def test_duplicate_allowed_for_priority_merge(self):
        # 同名 section 允许重复注册（来自不同优先级来源），由 assembler 去重
        registry = SectionRegistry()
        registry.register(FakeSection("a", "A", priority=0))
        registry.register(FakeSection("a", "B", priority=100))
        assembler = PromptAssembler(registry)
        result = assembler.assemble(PromptContext())
        assert result.full == "B"

    def test_disable(self):
        registry = SectionRegistry()
        registry.register(FakeSection("a", "A"))
        registry.disable("a")
        assembler = PromptAssembler(registry)
        result = assembler.assemble(PromptContext())
        assert result.full == ""


class TestAssembler:
    def test_static_dynamic_boundary(self):
        registry = SectionRegistry()
        registry.register(FakeSection("static", "STATIC", dynamic=False))
        registry.register(FakeSection("dynamic", "DYNAMIC", dynamic=True))
        assembler = PromptAssembler(registry)

        result = assembler.assemble(PromptContext())
        assert result.static_prefix == "STATIC"
        assert result.dynamic_suffix == "DYNAMIC"
        assert DYNAMIC_BOUNDARY in result.full
        assert result.full.startswith("STATIC")

    def test_priority_merge(self):
        registry = SectionRegistry()
        # same name, different priority — higher wins
        registry.register(FakeSection("x", "low", priority=0))
        registry.register(FakeSection("x", "high", priority=AGENT_PRIORITY))
        assembler = PromptAssembler(registry)

        result = assembler.assemble(PromptContext())
        assert result.full == "high"

    def test_none_render_skipped(self):
        class NoneSection(PromptSectionBase):
            _name = "none"

            def render(self, ctx):
                return None

        registry = SectionRegistry()
        registry.register(FakeSection("a", "A"))
        registry.register(NoneSection())
        assembler = PromptAssembler(registry)
        result = assembler.assemble(PromptContext())
        assert result.full == "A"
        assert "none" not in result.section_names


class TestDefaultSections:
    def test_default_identity(self):
        registry = SectionRegistry()
        for s in create_default_sections():
            registry.register(s)
        assembler = PromptAssembler(registry)

        ctx = PromptContext(model_id="claude-test", cwd=".", rules_text="")
        result = assembler.assemble(ctx)
        assert "Nono Agent" in result.static_prefix
        # rules section renders nothing when rules_text empty
        assert "rules" not in result.dynamic_suffix

    def test_rules_injected(self):
        registry = SectionRegistry()
        for s in create_default_sections():
            registry.register(s)
        assembler = PromptAssembler(registry)

        ctx = PromptContext(rules_text="Always write tests.")
        result = assembler.assemble(ctx)
        assert "Always write tests." in result.dynamic_suffix

    def test_custom_overrides_identity(self):
        registry = SectionRegistry()
        for s in create_default_sections():
            registry.register(s)
        assembler = PromptAssembler(registry)

        ctx = PromptContext(custom_system_prompt="CUSTOM IDENTITY")
        result = assembler.assemble(ctx)
        assert "CUSTOM IDENTITY" in result.static_prefix
