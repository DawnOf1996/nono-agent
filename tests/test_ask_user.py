"""Tests for the AskUserQuestion tool and channel."""

import pytest

from nono_agent.tools.builtin import AskUserQuestionTool
from nono_agent.tools.builtin.ask_user import as_bool
from nono_agent.tools.context import ToolUseContext
from nono_app_cli.interactive import parse_choice

OPTIONS = [{"label": "yes"}, {"label": "no", "description": "换个方向"}]


class TestAskUserQuestionTool:
    @pytest.mark.asyncio
    async def test_returns_answers(self):
        async def fake_ask(questions):
            return {"answers": {questions[0]["question"]: "yes"}}

        ctx = ToolUseContext(cwd="/tmp", ask_user=fake_ask)
        tool = AskUserQuestionTool()
        result = await tool.execute(
            {
                "questions": [
                    {
                        "question": "Proceed?",
                        "header": "Confirm",
                        "options": [{"label": "yes"}, {"label": "no"}],
                    }
                ]
            },
            ctx,
        )
        assert not result.is_error
        assert "yes" in result.content

    @pytest.mark.asyncio
    async def test_no_channel_returns_error(self):
        ctx = ToolUseContext(cwd="/tmp")  # no ask_user callback
        tool = AskUserQuestionTool()
        result = await tool.execute(
            {"questions": [{"question": "Proceed?"}]}, ctx
        )
        assert result.is_error
        assert "not available" in result.content

    @pytest.mark.asyncio
    async def test_invalid_questions(self):
        ctx = ToolUseContext(cwd="/tmp")
        tool = AskUserQuestionTool()
        result = await tool.execute({"questions": []}, ctx)
        assert result.is_error
        assert "non-empty" in result.content

    @pytest.mark.asyncio
    async def test_free_text_answer_reaches_the_model(self):
        """用户自己写的答案要原样回灌 —— 不能被"不是合法选项"过滤掉。"""

        async def fake_ask(questions):
            return {"answers": {questions[0]["question"]: "先只改 README，别动代码"}}

        ctx = ToolUseContext(cwd="/tmp", ask_user=fake_ask)
        result = await AskUserQuestionTool().execute(
            {
                "questions": [
                    {
                        "question": "改哪些文件？",
                        "options": [{"label": "全部"}, {"label": "只改文档"}],
                    }
                ]
            },
            ctx,
        )
        assert not result.is_error
        assert "先只改 README，别动代码" in result.content

    @pytest.mark.asyncio
    async def test_multi_select_normalized_before_the_callback(self):
        """通道里拿到的必须是真布尔 —— 两端各自 bool() 会把 "false" 读反。"""

        seen: list = []

        async def fake_ask(questions):
            seen.append(questions)
            return {"answers": {}}

        ctx = ToolUseContext(cwd="/tmp", ask_user=fake_ask)
        await AskUserQuestionTool().execute(
            {
                "questions": [
                    {"question": "A", "multi_select": "true"},
                    {"question": "B", "multi_select": "false"},
                    {"question": "C"},
                    {"question": "D", "multi_select": 1},
                    {"question": "E", "multi_select": None},
                ]
            },
            ctx,
        )
        assert [item["multi_select"] for item in seen[0]] == [
            True,
            False,
            False,
            True,
            False,
        ]


class TestAsBool:
    """模型不守 schema 时的兜底：认得出的按语义，认不出的按没写。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("True", True),
            ("  YES ", True),
            ("1", True),
            ("on", True),
            ("false", False),
            ("no", False),
            ("off", False),
            ("", False),
            ("大概吧", False),  # 认不出的写法不猜，按单选处理
            (1, True),
            (0, False),
            (None, False),
        ],
    )
    def test_converges_to_bool(self, raw, expected):
        result = as_bool(raw)
        assert result is expected
        assert isinstance(result, bool)

    @pytest.mark.parametrize("raw", ["false", "0", None, "no"])
    def test_string_false_is_not_truthy(self, raw):
        """这条是 Web 端 Boolean("false") === true 的那个坑。"""
        assert as_bool(raw) is False


class TestParseChoice:
    """CLI 的一行输入 → 答案。选项之外的话必须留住。"""

    def test_number_picks_the_label(self):
        assert parse_choice("2", OPTIONS, multi=False) == "no"

    def test_out_of_range_number_is_kept_as_text(self):
        assert parse_choice("9", OPTIONS, multi=False) == "9"

    def test_single_free_text_survives(self):
        assert parse_choice("先看看 README", OPTIONS, multi=False) == "先看看 README"

    def test_multi_keeps_numbers_and_free_text(self):
        assert parse_choice("1, 顺便加个测试", OPTIONS, multi=True) == [
            "yes",
            "顺便加个测试",
        ]

    def test_multi_dedupes_and_drops_blanks(self):
        assert parse_choice("1, ,yes", OPTIONS, multi=True) == ["yes"]

    def test_multi_empty_is_empty(self):
        assert parse_choice("  ", OPTIONS, multi=True) == []

    def test_no_options_returns_raw_text(self):
        assert parse_choice("都行", [], multi=False) == "都行"
