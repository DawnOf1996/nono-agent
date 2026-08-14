"""Tests for the AskUserQuestion tool and channel."""

import pytest

from nono_agent.tools.builtin import AskUserQuestionTool
from nono_agent.tools.context import ToolUseContext


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
