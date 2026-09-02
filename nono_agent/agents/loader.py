"""AgentDefinition 加载器 —— 多 source（user / project / builtin）。"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from nono_agent.agents.definition import AgentDefinition
from nono_agent.paths import project_dir, user_home
from nono_agent.skills.loader import parse_skill_markdown  # 复用 frontmatter 解析


@runtime_checkable
class AgentLoader(Protocol):
    """Agent 定义加载器协议。"""

    def load(self, cwd: str | Path) -> list[AgentDefinition]:
        ...


class FileSystemAgentLoader:
    """从用户/项目目录加载 AgentDefinition markdown。"""

    def __init__(self, user_agents_dir: str | Path | None = None) -> None:
        self._user_agents_dir = (
            Path(user_agents_dir)
            if user_agents_dir is not None
            else user_home() / "agents"
        )

    def load(self, cwd: str | Path) -> list[AgentDefinition]:
        agents: list[AgentDefinition] = []
        agents.extend(self._load_dir(self._user_agents_dir, scope="user"))
        agents.extend(self._load_dir(project_dir(cwd) / "agents", scope="project"))
        return agents

    def _load_dir(self, agents_dir: Path, *, scope: str) -> list[AgentDefinition]:
        agents: list[AgentDefinition] = []
        if not agents_dir.is_dir():
            return agents
        for file in sorted(agents_dir.glob("*.md")):
            agent = self._load_one(file, scope)
            if agent is not None:
                agents.append(agent)
        return agents

    def _load_one(self, file: Path, scope: str) -> AgentDefinition | None:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        meta, body = parse_skill_markdown(text)
        body = body.strip()
        if not body:
            return None

        name = str(meta.get("name") or file.stem)
        description = str(meta.get("description") or name)
        tools_raw = meta.get("tools", [])
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []
        model = meta.get("model")
        model_str = str(model) if model else None

        return AgentDefinition(
            name=name,
            description=description,
            system_prompt=body,
            tools=tools,
            model=model_str,
            source=str(file),
        )
