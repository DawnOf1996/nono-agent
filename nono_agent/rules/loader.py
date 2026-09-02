"""Rules 发现与加载 —— RuleLoader 协议 + 文件系统实现。

发现约定（docs/ROADMAP.md §2.2）：
- ``.nono-agent/rules/*.md``（项目级，CWD 向上 walk）
- ``AGENTS.md`` / ``CLAUDE.md``（项目根，CLAUDE.md 兼容读）
- ``~/.nono-agent/rules/*.md``（用户全局）

支持 frontmatter 中的 ``globs`` 字段实现条件启用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from nono_agent.paths import CONFIG_DIR_NAME, project_dir, user_home
from nono_agent.rules.models import Rule

# 项目根指令文件名（兼容 Cursor/CC 约定）
ROOT_RULE_FILES = ("AGENTS.md", "CLAUDE.md")


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """解析 ``---`` 包裹的 YAML-ish frontmatter（仅支持简单键值）。"""
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
                meta[key] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
            else:
                meta[key] = value.strip("'\"")
    return meta, body


@runtime_checkable
class RuleLoader(Protocol):
    """Rule 加载器协议 —— 从工作目录加载规则清单。"""

    def load(self, cwd: str | Path) -> list[Rule]:
        """加载并返回规则列表（已去重、未合并文本）。"""
        ...


class FileSystemRuleLoader:
    """文件系统规则加载器 —— 发现项目/用户/根指令文件。"""

    def __init__(self, user_rules_dir: str | Path | None = None) -> None:
        self._user_rules_dir = (
            Path(user_rules_dir)
            if user_rules_dir is not None
            else user_home() / "rules"
        )

    def load(self, cwd: str | Path) -> list[Rule]:
        rules: list[Rule] = []
        rules.extend(self._load_user_rules())
        rules.extend(self._load_project_rules(Path(cwd)))
        return rules

    # ------------------------------------------------------------------
    # 用户全局
    # ------------------------------------------------------------------

    def _load_user_rules(self) -> list[Rule]:
        rules: list[Rule] = []
        if not self._user_rules_dir.is_dir():
            return rules
        for file in sorted(self._user_rules_dir.glob("*.md")):
            rule = self._rule_from_file(file, scope="user", base_priority=0)
            if rule is not None:
                rules.append(rule)
        return rules

    # ------------------------------------------------------------------
    # 项目级（CWD 向上 walk）
    # ------------------------------------------------------------------

    def _load_project_rules(self, cwd: Path) -> list[Rule]:
        cwd = cwd.resolve()
        rules: list[Rule] = []
        seen: set[str] = set()

        # 从 cwd 向上 walk，逐层收集 .nono-agent/rules 与根指令文件
        for directory in _walk_up(cwd):
            # 1. .nono-agent/rules/*.md
            rules_dir = project_dir(directory) / "rules"
            if rules_dir.is_dir():
                for file in sorted(rules_dir.glob("*.md")):
                    rule = self._rule_from_file(file, scope="project", base_priority=10)
                    if rule is not None and rule.name not in seen:
                        seen.add(rule.name)
                        rules.append(rule)

            # 2. 根指令文件（AGENTS.md / CLAUDE.md 兼容读）
            for name in ROOT_RULE_FILES:
                root_file = directory / name
                if root_file.is_file():
                    rule = self._rule_from_file(root_file, scope="project", base_priority=5)
                    if rule is not None and rule.name not in seen:
                        seen.add(rule.name)
                        rules.append(rule)

            # 仅在仓库根停下（有 .git 或 .nono-agent 目录视为根）
            if (directory / ".git").exists() or (directory / CONFIG_DIR_NAME).exists():
                break

        # 越靠上（根）的规则优先级更高 → 反转保证根级优先
        rules.reverse()
        return rules

    # ------------------------------------------------------------------
    # 单文件解析
    # ------------------------------------------------------------------

    def _rule_from_file(
        self,
        file: Path,
        *,
        scope: str,
        base_priority: int,
    ) -> Rule | None:
        try:
            text = file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

        meta, body = _parse_frontmatter(text)
        body = body.strip()
        if not body:
            return None

        globs = meta.get("globs", [])
        if isinstance(globs, str):
            globs = [globs]

        priority = base_priority
        try:
            priority += int(meta.get("priority", 0))
        except (TypeError, ValueError):
            pass

        return Rule(
            name=file.name,
            content=body,
            source=str(file),
            scope=scope,
            globs=list(globs),
            priority=priority,
        )


def _walk_up(start: Path) -> list[Path]:
    """返回从 start 向上到文件系统根的目录列表。"""
    dirs: list[Path] = []
    current = start
    while True:
        dirs.append(current)
        if current.parent == current:
            break
        current = current.parent
    return dirs
