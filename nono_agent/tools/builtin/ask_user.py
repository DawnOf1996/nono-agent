"""AskUserQuestion 工具 —— 人在环结构化提问。

模型可向用户提出一个或多个结构化问题（单选/多选），
答案通过 context.ask_user 回调回灌对话。

CLI 注入交互式回调（rich 渲染 + stdin 输入）；
Server 在 Phase 3 通过回调/SSE 提供。回调缺失时工具返回明确错误。
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.protocol import ToolBase


class AskUserQuestionTool(ToolBase):
    """向用户提出结构化问题并获取答案。"""

    _name = "ask_user_question"
    _description = (
        "Ask the user one or more structured questions to clarify intent before "
        "proceeding. Use this when you need a decision, preference, or confirmation. "
        "Each question can have a list of options (single or multi-select). "
        "The user's answers are returned for you to act on."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "questions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question": {"type": "string", "description": "The question text."},
                        "header": {"type": "string", "description": "Short heading."},
                        "options": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "label": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                                "required": ["label"],
                            },
                        },
                        "multi_select": {"type": "boolean"},
                    },
                    "required": ["question"],
                },
            },
        },
        "required": ["questions"],
    }
    _is_concurrent_safe = False
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        questions = input_params.get("questions")
        if not isinstance(questions, list) or not questions:
            return ToolResult(
                tool_use_id="",
                content="Error: 'questions' must be a non-empty list.",
                is_error=True,
                tool_name=self._name,
            )

        answers = await context.ask_user_question(questions)
        if answers is None:
            return ToolResult(
                tool_use_id="",
                content=(
                    "Error: The AskUserQuestion channel is not available in this "
                    "environment (no interactive callback). Re-prompt the user in "
                    "plain text instead."
                ),
                is_error=True,
                tool_name=self._name,
            )

        payload = json.dumps(answers, ensure_ascii=False)
        return ToolResult(
            tool_use_id="",
            content=f"User answers: {payload}",
            tool_name=self._name,
        )
