"""引擎 HTTP API 的请求 / 响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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
