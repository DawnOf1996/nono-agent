"""Slash 命令注册表 —— 运行时命令（MVP 子集）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class CommandContext:
    """命令执行上下文 —— 提供引擎与工作目录访问。"""

    engine: Any | None = None      # QueryEngine（供 /tokens /model /clear 等使用）
    cwd: str = "."


@runtime_checkable
class SlashCommand(Protocol):
    """Slash 命令协议。"""

    @property
    def name(self) -> str:
        """命令名（不含前导 ``/``）。"""
        ...

    @property
    def description(self) -> str:
        """一句话说明（/help 展示）。"""
        ...

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        """执行命令并返回展示文本。"""
        ...


class CommandRegistry:
    """Slash 命令注册表。"""

    def __init__(self) -> None:
        self._commands: dict[str, SlashCommand] = {}

    def register(self, command: SlashCommand) -> None:
        if command.name in self._commands:
            raise ValueError(f"Command '/{command.name}' is already registered.")
        self._commands[command.name] = command

    def find(self, name: str) -> SlashCommand | None:
        return self._commands.get(name)

    def names(self) -> list[str]:
        return sorted(self._commands.keys())

    def execute(self, name: str, args: list[str], ctx: CommandContext) -> str:
        command = self._commands.get(name)
        if command is None:
            return f"Unknown command '/{name}'. Try /help."
        return command.execute(args, ctx)

    def __len__(self) -> int:
        return len(self._commands)

    def __contains__(self, name: str) -> bool:
        return name in self._commands
