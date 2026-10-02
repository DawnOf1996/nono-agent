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

    async def test_write_new_file_is_marked(self, ctx, tmp_path):
        """新建：整篇都是新增，结果标 created，UI 自己就能算出来，不必再附差异。"""
        w = WriteFileTool()
        result = await w.execute({"path": "new.py", "content": "a\nb\n"}, ctx)
        assert not result.is_error
        assert result.ui["created"] is True
        assert result.ui["added"] == 2
        assert "@@" in result.ui["diff"]

    async def test_write_overwrite_carries_unified_diff(self, ctx, tmp_path):
        """覆盖写：ui 附件里带 @@ 段和真实的 +N/-M，卡片才能渲染真差异。"""
        (tmp_path / "c.py").write_text("a = 1\nb = 2\nc = 3\n", encoding="utf-8")
        w = WriteFileTool()
        result = await w.execute(
            {"path": "c.py", "content": "a = 1\nb = 20\nc = 3\n"}, ctx
        )
        assert not result.is_error
        assert result.ui["added"] == 1 and result.ui["removed"] == 1
        assert "-b = 2" in result.ui["diff"]
        assert "+b = 20" in result.ui["diff"]

    async def test_write_result_text_has_no_diff(self, ctx, tmp_path):
        """差异只走 ui 附件：结果文本回到一句话，不占模型 token。"""
        (tmp_path / "c.py").write_text("a = 1\n", encoding="utf-8")
        w = WriteFileTool()
        result = await w.execute({"path": "c.py", "content": "a = 2\n"}, ctx)
        assert result.content.count("\n") == 0
        assert "@@" not in result.content

    async def test_write_overwrite_big_diff_keeps_stats_only(self, ctx, tmp_path):
        """差异过大时只留统计（diff=None + truncated），模型不必为一张卡片重读整份文件。"""
        before = "\n".join(f"line {i}" for i in range(400))
        after = "\n".join(f"LINE {i}" for i in range(400))
        (tmp_path / "big.txt").write_text(before, encoding="utf-8")
        w = WriteFileTool()
        result = await w.execute({"path": "big.txt", "content": after}, ctx)
        assert not result.is_error
        assert result.ui["added"] == 400 and result.ui["removed"] == 400
        assert result.ui["diff"] is None
        assert result.ui["truncated"] is True

    async def test_write_binary_baseline_unavailable(self, ctx, tmp_path):
        """基线是二进制：读不出来就没有差异，不硬凑一个假的。"""
        (tmp_path / "b.bin").write_bytes(b"\x00\x01old")
        w = WriteFileTool()
        result = await w.execute({"path": "b.bin", "content": "new"}, ctx)
        assert not result.is_error
        assert result.ui is None

    async def test_write_rejects_non_string_content(self, ctx, tmp_path):
        w = WriteFileTool()
        result = await w.execute({"path": "x.txt", "content": 42}, ctx)
        assert result.is_error
        assert "must be a string" in result.content


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

    async def test_edit_reports_first_match_line(self, ctx, tmp_path):
        """ui 附件里的差异带真实行号。"""
        (tmp_path / "f.txt").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "three", "new_string": "THREE"}, ctx
        )
        assert not result.is_error
        assert result.ui["added"] == 1 and result.ui["removed"] == 1
        assert "-three" in result.ui["diff"] and "+THREE" in result.ui["diff"]

    async def test_edit_ui_diff_is_whole_file(self, ctx, tmp_path):
        """行号必须是**整文件**的：改第 20 行，hunk 就得以 17 打头（前面带 3 行上下文），
        而不是从 1 重新数 —— 这正是拿 old_string/new_string 自己算做不到的地方。"""
        body = "\n".join(f"line{i}" for i in range(1, 41))
        (tmp_path / "f.txt").write_text(body, encoding="utf-8")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "line20", "new_string": "LINE20"}, ctx
        )
        assert not result.is_error
        header = next(
            line for line in result.ui["diff"].splitlines() if line.startswith("@@")
        )
        assert header.startswith("@@ -17,7 +17,7 @@"), header

    async def test_edit_result_text_has_no_diff(self, ctx, tmp_path):
        (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")
        e = EditFileTool()
        result = await e.execute({"path": "f.txt", "old_string": "a", "new_string": "A"}, ctx)
        assert result.content.count("\n") == 0
        assert "@@" not in result.content

    async def test_edit_error_has_no_ui_diff(self, ctx, tmp_path):
        """失败就没有差异：文件没动过，不能画一条"打算怎么改"。"""
        (tmp_path / "f.txt").write_text("alpha\n", encoding="utf-8")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "gamma", "new_string": "GAMMA"}, ctx
        )
        assert result.is_error
        assert result.ui is None

    async def test_edit_multi_line_match_reports_start_line(self, ctx, tmp_path):
        (tmp_path / "f.txt").write_bytes(b"a\nb\nc\nd\ne\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "f.txt", "old_string": "c\nd", "new_string": "C\nD"}, ctx
        )
        assert not result.is_error
        assert result.ui["added"] == 2 and result.ui["removed"] == 2
        assert "-c" in result.ui["diff"] and "+C" in result.ui["diff"]

    async def test_edit_matches_across_crlf(self, ctx, tmp_path):
        """CRLF 文件 + 模型给的 LF old_string：必须能匹配上，且写回仍是 CRLF。

        模型看到的正文来自 read_file 的 splitlines()（不带 \\r），直接贴回来就是 LF；
        不做归一的话，Windows 上每一次跨行编辑都会报 old_string not found。
        """
        path = tmp_path / "crlf.txt"
        path.write_bytes(b"one\r\ntwo\r\nthree\r\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "crlf.txt", "old_string": "two\nthree", "new_string": "TWO\nTHREE"}, ctx
        )
        assert not result.is_error
        assert path.read_bytes() == b"one\r\nTWO\r\nTHREE\r\n"
        # 差异在 LF 空间里算，行号不受 CRLF 影响：改的是第 2 行，hunk 从 1 起（3 行上下文）。
        header = next(
            line for line in result.ui["diff"].splitlines() if line.startswith("@@")
        )
        assert header.startswith("@@ -1,3 +1,3 @@"), header

    async def test_edit_keeps_lf_file_lf(self, ctx, tmp_path):
        path = tmp_path / "lf.txt"
        path.write_bytes(b"a\nb\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "lf.txt", "old_string": "a\nb", "new_string": "A\nB"}, ctx
        )
        assert not result.is_error
        assert path.read_bytes() == b"A\nB\n"

    async def test_edit_accepts_crlf_old_string_on_lf_file(self, ctx, tmp_path):
        """反过来也要认：模型偶尔自己带上 \\r\\n。"""
        path = tmp_path / "lf2.txt"
        path.write_bytes(b"a\nb\n")
        e = EditFileTool()
        result = await e.execute(
            {"path": "lf2.txt", "old_string": "a\r\nb", "new_string": "A\nB"}, ctx
        )
        assert not result.is_error
        assert path.read_bytes() == b"A\nB\n"

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

    def test_bash_requires_description(self):
        """命令功能描述是必填 —— 对话界面靠它把「运行命令」这行说成人话。"""
        schema = BashTool().input_schema
        assert "description" in schema["properties"]
        assert "description" in schema["required"]


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
