"""内置子 Agent 定义（general-purpose / explore）。"""

from nono_agent.agents.definition import AgentDefinition


def create_builtin_agents() -> list[AgentDefinition]:
    """创建内置子 Agent 定义。"""
    return [
        AgentDefinition(
            name="general-purpose",
            description="A general-purpose sub-agent for focused, self-contained tasks.",
            system_prompt=(
                "You are a focused sub-agent. Complete the assigned task and "
                "return a concise final answer. Use the available tools as needed."
            ),
            source="builtin",
        ),
        AgentDefinition(
            name="explore",
            description="Read-only codebase exploration and summarization.",
            system_prompt=(
                "You are a code-exploration sub-agent. Investigate the codebase "
                "using read-only tools (read_file, glob, grep) and return a "
                "structured summary of your findings."
            ),
            tools=["read_file", "glob", "grep"],
            source="builtin",
        ),
    ]
