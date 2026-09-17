"""内置 Prompt Section 实现。"""

from nono_agent.prompt.section import PromptSection
from nono_agent.prompt.sections.env_info import EnvInfoSection
from nono_agent.prompt.sections.identity import IdentitySection
from nono_agent.prompt.sections.memory_section import MemorySection
from nono_agent.prompt.sections.output_style import OutputStyleSection
from nono_agent.prompt.sections.rules_section import RulesSection
from nono_agent.prompt.sections.skills_section import SkillsSection
from nono_agent.prompt.sections.tools_guidance import ToolsGuidanceSection

__all__ = [
    "EnvInfoSection",
    "IdentitySection",
    "MemorySection",
    "OutputStyleSection",
    "RulesSection",
    "SkillsSection",
    "ToolsGuidanceSection",
    "create_default_sections",
]


def create_default_sections() -> list[PromptSection]:
    """创建默认内置 section 列表（按期望顺序）。"""
    return [
        IdentitySection(),
        ToolsGuidanceSection(),
        EnvInfoSection(),
        RulesSection(),
        SkillsSection(),
        MemorySection(),
        OutputStyleSection(),
    ]
