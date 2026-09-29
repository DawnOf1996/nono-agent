"""Tests for production builtin tools (bash / read / write / edit / glob / grep)."""

import sys

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
    def test_creates_twenty_two_tools(self):
        tools = create_builtin_tools()
        names = {t.name for t in tools}
        assert names == {
            "bash",
            "read_file",
            "write_file",
            "edit_file",
            "glob",
            "grep",
            "list_directory",
            "lsp",
            "web_fetch",
            "web_search",
            "tool_search",
            "ask_user_question",
            "todo_write",
            "enter_plan_mode",
            "exit_plan_mode",
            "enter_worktree",
            "exit_worktree",
            "task_create",
            "task_list",
            "task_get",
            "task_update",
            "task_stop",
        }


class TestDecodeText:
    def test_utf8_roundtrip(self):
        from nono_agent.tools.builtin._common import decode_text, encoding_note, is_utf8_family

        text, enc = decode_text("你好".encode())
        assert text == "你好"
        assert is_utf8_family(enc)
        assert encoding_note(enc) is None

    def test_utf8_bom(self):
        from nono_agent.tools.builtin._common import decode_text, is_utf8_family

        text, enc = decode_text("你好".encode("utf-8-sig"))
        assert text == "你好"
        assert is_utf8_family(enc)

    def test_gbk_chinese(self):
        from nono_agent.tools.builtin._common import decode_text, encoding_note, is_utf8_family

        text, enc = decode_text("你好".encode("gbk"))
        assert text == "你好"
        assert not is_utf8_family(enc)
        note = encoding_note(enc)
        assert note is not None
        assert "decoded as" in note

    def test_command_output_gbk_volume_line(self):
        from nono_agent.tools.builtin._common import decode_command_output

        raw = "驱动器 D 中的卷是 新加卷".encode("gbk")
        assert decode_command_output(raw) == "驱动器 D 中的卷是 新加卷"
        assert "\ufffd" not in decode_command_output(raw)

    def test_command_output_keeps_split_utf8(self):
        from nono_agent.tools.builtin._common import decode_command_output

        raw = "你好".encode()
        assert decode_command_output(raw[:-1]) == "你"
        assert decode_command_output(raw) == "你好"

    def test_lossy_fallback(self, monkeypatch):
        from nono_agent.tools.builtin import _common as common

        monkeypatch.setattr(common, "_decode_candidates", lambda: ["utf-8"])
        text, enc = common.decode_text(b"\x80")
        assert enc == common.LOSSY_ENCODING
        assert "\ufffd" in text


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

    async def test_read_gbk_file(self, ctx, tmp_path):
        (tmp_path / "gbk.txt").write_bytes("你好世界".encode("gbk"))
        r = ReadFileTool()
        result = await r.execute({"path": "gbk.txt"}, ctx)
        assert not result.is_error
        assert "你好世界" in result.content
        assert "decoded as" in result.content

    async def test_read_utf8_has_no_encoding_note(self, ctx, tmp_path):
        (tmp_path / "u.txt").write_text("你好", encoding="utf-8")
        r = ReadFileTool()
        result = await r.execute({"path": "u.txt"}, ctx)
        assert "你好" in result.content
        assert "decoded as" not in result.content

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

    async def test_edit_gbk_file_rewrites_utf8(self, ctx, tmp_path):
        path = tmp_path / "gbk.txt"
        path.write_bytes("你好世界".encode("gbk"))
        e = EditFileTool()
        result = await e.execute(
            {"path": "gbk.txt", "old_string": "世界", "new_string": "中国"},
            ctx,
        )
        assert not result.is_error
        assert "UTF-8" in result.content
        assert path.read_text(encoding="utf-8") == "你好中国"

    async def test_edit_refuses_undecodable(self, ctx, tmp_path, monkeypatch):
        from nono_agent.tools.builtin import _common as common
        from nono_agent.tools.builtin import edit as edit_mod

        monkeypatch.setattr(common, "_decode_candidates", lambda: ["utf-8"])
        monkeypatch.setattr(edit_mod, "decode_text", common.decode_text)
        (tmp_path / "bad.txt").write_bytes(b"\x80\x81")
        e = EditFileTool()
        result = await e.execute(
            {"path": "bad.txt", "old_string": "a", "new_string": "b"},
            ctx,
        )
        assert result.is_error
        assert "Could not decode" in result.content


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

    async def test_grep_gbk_file(self, ctx, tmp_path):
        (tmp_path / "gbk.txt").write_bytes("hello 你好\n".encode("gbk"))
        g = GrepTool()
        result = await g.execute({"pattern": "你好"}, ctx)
        assert "gbk.txt" in result.content
        assert "你好" in result.content


class TestBash:
    async def test_bash_echo(self, ctx):
        b = BashTool()
        result = await b.execute({"command": "echo hello"}, ctx)
        assert not result.is_error
        assert "hello" in result.content

    async def test_bash_timeout(self, ctx):
        b = BashTool()
        result = await b.execute(
            {
                "command": f'"{sys.executable}" -c "import time; time.sleep(5)"',
                "timeout": 1,
            },
            ctx,
        )
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


class TestTodoWrite:
    async def test_valid_todos(self, ctx):
        from nono_agent.tools.builtin import TodoWriteTool

        tool = TodoWriteTool()
        result = await tool.execute(
            {
                "todos": [
                    {"content": "do a", "status": "completed"},
                    {"content": "do b", "status": "in_progress"},
                ]
            },
            ctx,
        )
        assert not result.is_error
        assert "2 todo" in result.content

    async def test_invalid_status(self, ctx):
        from nono_agent.tools.builtin import TodoWriteTool

        tool = TodoWriteTool()
        result = await tool.execute(
            {"todos": [{"content": "x", "status": "bogus"}]}, ctx
        )
        assert result.is_error
        assert "invalid status" in result.content


class TestPathEscapeBlocked:
    async def test_read_escape_blocked(self, ctx):
        r = ReadFileTool()
        result = await r.execute({"path": "../secret.txt"}, ctx)
        assert result.is_error
        assert "escapes" in result.content
