"""Prompt Section 组装管线 —— 对标 CC Prompt-Assembly-Pipeline。

系统提示由有序 section 列表组装，而非单次字符串拼接。
"""

from nono_agent.prompt.assembler import DYNAMIC_BOUNDARY, AssembledPrompt, PromptAssembler
from nono_agent.prompt.registry import SectionRegistry
from nono_agent.prompt.section import (
    AGENT_PRIORITY,
    CUSTOM_PRIORITY,
    DEFAULT_PRIORITY,
    OVERRIDE_PRIORITY,
    PromptContext,
    PromptSection,
    PromptSectionBase,
)
from nono_agent.prompt.sections import (
    EnvInfoSection,
    IdentitySection,
    RulesSection,
    ToolsGuidanceSection,
    create_default_sections,
)

__all__ = [
    "AGENT_PRIORITY",
    "CUSTOM_PRIORITY",
    "DEFAULT_PRIORITY",
    "OVERRIDE_PRIORITY",
    "AssembledPrompt",
    "DYNAMIC_BOUNDARY",
    "EnvInfoSection",
    "IdentitySection",
    "PromptAssembler",
    "PromptContext",
    "PromptSection",
    "PromptSectionBase",
    "RulesSection",
    "SectionRegistry",
    "ToolsGuidanceSection",
    "create_default_sections",
]
