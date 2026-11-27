"""Tests for Enter/Exit worktree tools (requires git)."""

import shutil
import subprocess

import pytest

from nono_agent.tools.builtin import EnterWorktreeTool, ExitWorktreeTool
from nono_agent.tools.context import ToolUseContext

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is not available"
)


@pytest.fixture
def git_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "a.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "a.txt"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "init"], check=True)
    return tmp_path


class TestWorktreeTools:
    @pytest.mark.asyncio
    async def test_enter_and_exit(self, git_repo, tmp_path):
        ctx = ToolUseContext(cwd=str(git_repo))
        enter = EnterWorktreeTool()

        wt_path = tmp_path / "wt"
        result = await enter.execute({"path": str(wt_path), "branch": "feature"}, ctx)
        assert not result.is_error, result.content
        assert (wt_path / "a.txt").exists()

        exit_tool = ExitWorktreeTool()
        result = await exit_tool.execute({"path": str(wt_path)}, ctx)
        assert not result.is_error, result.content
        assert not wt_path.exists()

    @pytest.mark.asyncio
    async def test_exit_missing_fails(self, git_repo, tmp_path):
        ctx = ToolUseContext(cwd=str(git_repo))
        exit_tool = ExitWorktreeTool()
        result = await exit_tool.execute({"path": str(tmp_path / "nope")}, ctx)
        assert result.is_error
