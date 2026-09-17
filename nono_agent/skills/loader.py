"""Skills 发现与加载 —— SkillLoader 协议 + 文件系统实现。

发现约定：
- 项目级 ``.nono-agent/skills/<name>/SKILL.md``
- 用户级 ``~/.nono-agent/skills/<name>/SKILL.md``
- 每个 skill 是一个目录，含 ``SKILL.md``（YAML frontmatter + 正文）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml

from nono_agent.paths import project_dir, user_home
from nono_agent.skills.models import Skill

_SKILL_FILE = "SKILL.md"


def parse_skill_markdown(text: str) -> tuple[dict[str, Any], str]:
    """解析 SKILL.md 的 YAML frontmatter 与正文。

    支持块标量（``>`` / ``|``）、嵌套 mapping 与 YAML 列表。
    frontmatter 非法或不是 mapping 时返回空 meta，正文仍按结束分隔符切分。
    """
    if text.startswith("\ufeff"):
        text = text[1:]
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return {}, text

    closing: int | None = None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip("\r\n") == "---":
            closing = i
            break
    if closing is None:
        return {}, text

    meta_block = "".join(lines[1:closing])
    body = "".join(lines[closing + 1 :]).lstrip("\n")
    try:
        loaded = yaml.safe_load(meta_block)
    except yaml.YAMLError:
        return {}, body
    if loaded is None:
        return {}, body
    if not isinstance(loaded, dict):
        return {}, body
    return loaded, body


def coerce_meta_str(value: Any, fallback: str) -> str:
    """把 YAML 标量收成单行文本。folded/literal 块标量常带首尾换行。"""
    if value is None:
        return fallback
    text = " ".join(str(value).split())
    return text or fallback


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
            else user_home() / "skills"
        )

    def load(self, cwd: str | Path) -> list[Skill]:
        skills: list[Skill] = []
        skills.extend(self._load_dir(self._user_skills_dir, scope="user"))
        skills.extend(self._load_dir(project_dir(cwd) / "skills", scope="project"))
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

        name = coerce_meta_str(meta.get("name"), fallback_name)
        description = coerce_meta_str(meta.get("description"), name)
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
