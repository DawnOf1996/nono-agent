"""Tests for production builtin tools (bash / read / write / edit / glob / grep)."""

import pytest

from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox
from nono_agent.tools.builtin import (
    BashTool,
    EditFileTool,
    GlobTool,
    GrepTool,
    ReadFileTool,
    WriteFileTool,
    create_builtin_tools,
)
from nono_agent.tools.context import ToolUseContext


@pytest.fixture
def ctx(tmp_path):
    return ToolUseContext(
        cwd=str(tmp_path),
        sandbox=PathSandbox(tmp_path),
        ignore=IgnoreMatcher.from_workspace(tmp_path),
    )


class TestBuiltinFactory:
    def test_creates_seven_tools(self):
        tools = create_builtin_tools()
        names = {t.name for t in tools}
        assert names == {
            "bash",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "ask_user_question",
        }


class TestWriteAndRead:
    async def test_write_then_read_with_line_numbers(self, ctx, tmp_path):
        w = WriteFileTool()
        r = ReadFileTool()

        result = await w.execute(
            {"path": "a.py", "content": "def f():\n    return 1\n"}, ctx
        )
        assert not result.is_error

        result = await r.execute({"path": "a.py"}, ctx)
        assert "1  def f():" in result.content
        assert "2      return 1" in result.content

    async def test_read_offset_limit(self, ctx, tmp_path):
        (tmp_path / "b.txt").write_text("line1\nline2\nline3\nline4\n")
        r = ReadFileTool()
        result = await r.execute({"path": "b.txt", "offset": 2, "limit": 2}, ctx)
        assert "line2" in result.content
        assert "line3" in result.content
        assert "line4" not in result.content

    async def test_read_binary_rejected(self, ctx, tmp_path):
        (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02\x00")
        r = ReadFileTool()
        result = await r.execute({"path": "bin.dat"}, ctx)
        assert result.is_error
        assert "binary" in result.content

    async def test_write_atomic(self, ctx, tmp_path):
        w = WriteFileTool()
        await w.execute({"path": "sub/dir/x.txt", "content": "hi"}, ctx)
        assert (tmp_path / "sub/dir/x.txt").read_text() == "hi"


class TestEdit:
    async def test_edit_unique_match(self, ctx, tmp_path):
        (tmp_path / "f.txt").write_text("hello world\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "world", "new_string": "there"}, ctx
        )
        assert not result.is_error
        assert (tmp_path / "f.txt").read_text() == "hello there\n"

    async def test_edit_ambiguous(self, ctx, tmp_path):
        (tmp_path / "f.txt").write_text("aa bb aa\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "aa", "new_string": "cc"}, ctx
        )
        assert result.is_error
        assert "appears 2 times" in result.content

    async def test_edit_replace_all(self, ctx, tmp_path):
        (tmp_path / "f.txt").write_text("aa bb aa\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "aa", "new_string": "cc", "replace_all": True},
            ctx,
        )
        assert not result.is_error
        assert (tmp_path / "f.txt").read_text() == "cc bb cc\n"


class TestGlob:
    async def test_glob(self, ctx, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src/a.py").write_text("")
        (tmp_path / "src/b.ts").write_text("")
        g = GlobTool()
        result = await g.execute({"pattern": "**/*.py"}, ctx)
        assert "src/a.py" in result.content
        assert "b.ts" not in result.content

    async def test_glob_respects_ignore(self, ctx, tmp_path):
        (tmp_path / "build").mkdir()
        (tmp_path / "build/x.py").write_text("")
        (tmp_path / ".gitignore").write_text("build/\n")
        ctx = ToolUseContext(
            cwd=str(tmp_path),
            sandbox=PathSandbox(tmp_path),
            ignore=IgnoreMatcher.from_workspace(tmp_path),
        )
        g = GlobTool()
        result = await g.execute({"pattern": "**/*.py"}, ctx)
        assert "build/x.py" not in result.content


class TestGrep:
    async def test_grep(self, ctx, tmp_path):
        (tmp_path / "a.py").write_text("def foo():\n    return 1\n")
        g = GrepTool()
        result = await g.execute({"pattern": "foo"}, ctx)
        assert "a.py:1" in result.content

    async def test_grep_include(self, ctx, tmp_path):
        (tmp_path / "a.py").write_text("needle\n")
        (tmp_path / "b.txt").write_text("needle\n")
        g = GrepTool()
        result = await g.execute({"pattern": "needle", "include": "*.py"}, ctx)
        assert "a.py" in result.content
        assert "b.txt" not in result.content

    async def test_grep_no_match(self, ctx):
        g = GrepTool()
        result = await g.execute({"pattern": "zzz_nothing"}, ctx)
        assert "No matches" in result.content


class TestBash:
    async def test_bash_echo(self, ctx):
        b = BashTool()
        result = await b.execute({"command": "echo hello"}, ctx)
        assert not result.is_error
        assert "hello" in result.content

    async def test_bash_timeout(self, ctx):
        b = BashTool()
        result = await b.execute({"command": "sleep 5", "timeout": 1}, ctx)
        assert result.is_error
        assert "timed out" in result.content

    async def test_bash_error_exit_code(self, ctx):
        b = BashTool()
        result = await b.execute({"command": "exit 3"}, ctx)
        assert result.is_error
        assert "Exit code 3" in result.content

    def test_bash_classification(self):
        b = BashTool()
        assert b.should_prompt_for_permission({"command": "ls -la"}, None) is False
        assert b.should_prompt_for_permission({"command": "rm -rf /tmp/x"}, None) is True


class TestPathEscapeBlocked:
    async def test_read_escape_blocked(self, ctx):
        r = ReadFileTool()
        result = await r.execute({"path": "../secret.txt"}, ctx)
        assert result.is_error
        assert "escapes" in result.content
