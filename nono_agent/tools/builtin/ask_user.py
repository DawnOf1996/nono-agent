"""AskUserQuestion 工具 —— 人在环结构化提问。

模型可向用户提出一个或多个结构化问题（单选/多选），
答案通过 context.ask_user 回调回灌对话。

选项只是建议：两端（CLI 渲染与 Web 卡片）都同时提供自由输入，
用户可以完全绕开选项自己写一句，答案原样回给模型。

``multi_select`` 在进通道前统一归一化成布尔，避免两端各自 ``bool()`` 出分歧。

CLI 注入交互式回调（rich 渲染 + stdin 输入）；
Server 在 Phase 3 通过回调/SSE 提供。回调缺失时工具返回明确错误。
"""

from __future__ import annotations

import json
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.protocol import ToolBase

_TRUE_TOKENS = frozenset({"true", "1", "yes", "on"})
_FALSE_TOKENS = frozenset({"false", "0", "no", "off", "n", ""})


def as_bool(value: Any) -> bool:
    """把模型可能写出的各种 ``multi_select`` 收敛成真正的布尔。

    schema 声明的是 boolean，但模型的输出不总是守规矩（"true"/"1"/"是" 都见过）。
    两端各自 ``bool(...)`` 过一次，同一个 ``"false"`` 字符串在 CLI 会得到 False、
    在 Web 的 ``Boolean("false")`` 却得到 True —— 多选被静默打开。
    统一在进通道前做一次，两端拿到的就都是布尔了。
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
        # 认不出的写法按"没写这个字段"处理：单选更保守，
        # 真要多选，用户仍可以用自由输入把多条答案写进去。
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return bool(value)


def _normalize_question(question: Any) -> Any:
    """补齐/归一化一道题；非 dict 原样放过，交给下游自己报错。"""
    if not isinstance(question, dict):
        return question
    return {**question, "multi_select": as_bool(question.get("multi_select"))}


class AskUserQuestionTool(ToolBase):
    """向用户提出结构化问题并获取答案。"""

    _name = "ask_user_question"
    _description = (
        "Ask the user one or more structured questions to clarify intent before "
        "proceeding. Use this when you need a decision, preference, or confirmation. "
        "Each question can have a list of options (single or multi-select). "
        "The user can also type their own answer instead of picking an option, so an "
        "answer that matches none of your labels is deliberate free-form input - "
        "read it as the user's actual intent rather than a mistake. "
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

        # 归一化后再进通道：CLI 与 Web 都不该再各自猜一遍布尔。
        normalized = [_normalize_question(item) for item in questions]
        answers = await context.ask_user_question(normalized)
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
