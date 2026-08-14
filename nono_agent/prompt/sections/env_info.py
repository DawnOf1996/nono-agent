"""Env / git info section —— 动态段（工作目录、系统、git 状态）。"""

from __future__ import annotations

import datetime
import platform
import subprocess

from nono_agent.prompt.section import PromptContext, PromptSectionBase


class EnvInfoSection(PromptSectionBase):
    """注入环境与 git 信息（动态）。"""

    _name = "env_info"
    _is_dynamic = True

    def render(self, ctx: PromptContext) -> str | None:
        lines: list[str] = ["<environment>"]
        lines.append(f"Working directory: {ctx.cwd}")
        lines.append(f"OS: {platform.system()} {platform.release()}")
        lines.append(f"Date: {datetime.date.today().isoformat()}")
        lines.append(f"Python: {platform.python_version()}")

        git_branch = self._git_branch(ctx.cwd)
        if git_branch:
            lines.append(f"Git branch: {git_branch}")
        lines.append("</environment>")
        return "\n".join(lines)

    @staticmethod
    def _git_branch(cwd: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            branch = result.stdout.strip()
            return branch if branch and result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None
