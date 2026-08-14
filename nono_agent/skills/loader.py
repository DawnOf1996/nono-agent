"""Skills 发现与加载 —— SkillLoader 协议 + 文件系统实现。

发现约定（docs/ROADMAP.md §2.3）：
- 项目级 ``.nono/skills/<name>/SKILL.md``
- 用户级 ``~/.nono/skills/<name>/SKILL.md``
- 每个 skill 是一个目录，含 ``SKILL.md``（YAML-ish frontmatter + 正文）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nono_agent.skills.models import Skill

_SKILL_FILE = "SKILL.md"


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    """解析 SKILL.md 的 frontmatter 与正文。"""
    if not text.startswith("---"):
        return {}, text
    rest = text[3:]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    meta_block = rest[:end]
    body = rest[end + 4 :].lstrip("\n")

    meta: dict[str, Any] = {}
    for line in meta_block.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            key = key.strip()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                meta[key] = [
                    v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()
                ]
            else:
                meta[key] = value.strip("'\"")
    return meta, body


@runtime_checkable
class SkillLoader(Protocol):
    """Skill 加载器协议 —— 从工作目录加载技能清单。"""

    def load(self, cwd: str | Path) -> list[Skill]:
        """加载并返回技能列表。"""
        ...


class FileSystemSkillLoader:
    """文件系统 Skill 加载器 —— 发现项目/用户技能目录。"""

    def __init__(self, user_skills_dir: str | Path | None = None) -> None:
        self._user_skills_dir = (
            Path(user_skills_dir)
            if user_skills_dir is not None
            else Path.home() / ".nono" / "skills"
        )

    def load(self, cwd: str | Path) -> list[Skill]:
        skills: list[Skill] = []
        skills.extend(self._load_dir(self._user_skills_dir, scope="user"))
        skills.extend(self._load_dir(Path(cwd).resolve() / ".nono" / "skills", scope="project"))
        return skills

    def _load_dir(self, skills_dir: Path, *, scope: str) -> list[Skill]:
        skills: list[Skill] = []
        if not skills_dir.is_dir():
            return skills
        for skill_dir in sorted(skills_dir.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill_file = skill_dir / _SKILL_FILE
            if not skill_file.is_file():
                continue
            skill = self._load_one(skill_file, skill_dir.name, scope)
            if skill is not None:
                skills.append(skill)
        return skills

    def _load_one(self, file: Path, fallback_name: str, scope: str) -> Skill | None:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        meta, body = parse_skill_markdown(text)
        body = body.strip()
        if not body:
            return None

        name = str(meta.get("name") or fallback_name)
        description = str(meta.get("description") or name)
        tools_raw = meta.get("tools", [])
        tools = [str(t) for t in tools_raw] if isinstance(tools_raw, list) else []

        return Skill(
            name=name,
            description=description,
            content=body,
            source=str(file),
            scope=scope,
            tools=tools,
        )
