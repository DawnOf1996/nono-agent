"""请求体。"""

from __future__ import annotations

from pydantic import (
    BaseModel,
    Field,
)


class McpServerUpsert(BaseModel):
    id: str
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    env_file: str | None = None
    cwd: str | None = None
