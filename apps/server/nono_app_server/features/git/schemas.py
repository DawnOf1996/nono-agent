"""请求体。"""

from __future__ import annotations

from pydantic import BaseModel


class RepoBody(BaseModel):
    root: str
    repo: str | None = None


class PathsBody(RepoBody):
    paths: list[str] | None = None


class DiscardBody(RepoBody):
    paths: list[str] = []
    untracked: list[str] = []


class CommitBody(RepoBody):
    message: str
    stage_all: bool = False
