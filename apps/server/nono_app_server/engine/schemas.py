"""引擎 HTTP API 的请求 / 响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """聊天请求 —— 客户端发送消息的入口。"""

    message: str
    session_id: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    attachments: list[dict[str, Any]] | None = None


class SessionInfo(BaseModel):
    """会话信息 —— 用于 API 响应的会话元数据。"""

    session_id: str
    model: str
    cwd: str = ""
    turn_count: int
    total_tokens: int
    permission_mode: str = "default"
    summary: str = ""
    updated_at: float = 0


class NewSessionRequest(BaseModel):
    """创建会话 —— JSON 体可指定 model / cwd / permission_mode。"""

    model: str | None = None
    cwd: str | None = None
    permission_mode: str | None = None


class PermissionModeBody(BaseModel):
    """切换会话权限模式。"""

    mode: str


class CommandRequest(BaseModel):
    """执行一条斜杠指令。"""

    line: str
    session_id: str | None = None
    cwd: str | None = None


class AbortRequest(BaseModel):
    """中止请求 —— 客户端请求取消正在进行的查询。"""

    session_id: str
    reason: str | None = None


class ResumeRequest(BaseModel):
    """恢复会话请求 —— 从 transcript 存储加载历史会话。"""

    session_id: str
    model: str | None = None
    skip_system: bool = False


class AnswerRequest(BaseModel):
    """AskUser 回答请求 —— 客户端对 ask_user 事件的回应。"""

    answers: dict[str, Any] = {}


class SessionUsage(BaseModel):
    """会话累计 token 用量（四个互不重叠的桶）。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    total_tokens: int = 0
    cache_hit_ratio: float | None = None


class SessionContext(BaseModel):
    """上下文占用快照 —— 总量、占比与三分项。

    三分项来自引擎当次请求的 ``analyze_context_usage``；旧版本日志没有，
    此时三个分项为 None，只有总量可从最后一次请求的输入量兜底得出。
    """

    context_window: int = 0
    total_tokens: int = 0
    usage_ratio: float = 0.0
    system_prompt_tokens: int | None = None
    tools_tokens: int | None = None
    messages_tokens: int | None = None
    message_count: int | None = None


class SessionStats(BaseModel):
    """会话级统计 —— 由事件流推导，见 nono_agent/session/stats.py。"""

    turns: int = 0
    steps: int = 0
    model_duration_ms: float = 0.0
    tool_duration_ms: float = 0.0
    ttft_ms: float | None = None
    tps: float | None = None
    model: str | None = None
    usage: SessionUsage = Field(default_factory=SessionUsage)
    context: SessionContext | None = None
