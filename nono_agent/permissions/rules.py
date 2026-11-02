"""可落盘权限规则 —— 对标 Claude Code permissions/* 的 allow/deny 细则。

规则按「工具名 / 路径 glob / bash 正则」三轴匹配：
- ``tool_name``：精确工具名或 ``*`` 通配
- ``path``：glob 模式（对输入参数中的 path 字段）
- ``bash_pattern``：正则（对输入参数中的 command 字段）

动作三态：``allow`` / ``deny`` / ``ask``。
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class RuleAction(StrEnum):
    """规则动作。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


def _extract_path(params: dict[str, Any]) -> str | None:
    """从工具输入参数中提取文件路径（可能位于嵌套字段）。"""
    for key in ("path", "file_path", "target", "target_directory"):
        val = params.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def _extract_command(params: dict[str, Any]) -> str | None:
    """从工具输入参数中提取命令字符串（bash 类工具）。"""
    for key in ("command", "cmd"):
        val = params.get(key)
        if isinstance(val, str) and val:
            return val
    return None


def extract_paths(params: dict[str, Any]) -> list[str]:
    """提取入参里所有可能的路径 —— 供安全底线逐个检查。

    比 ``_extract_path`` 多取 ``workdir``：bash 的 workdir 也是它会碰的路径。
    """
    found: list[str] = []
    for key in ("path", "file_path", "target", "target_directory", "workdir"):
        val = params.get(key)
        if isinstance(val, str) and val and val not in found:
            found.append(val)
    return found


def extract_command(params: dict[str, Any]) -> str | None:
    """公开版命令提取 —— 供安全底线扫描命令内容。"""
    return _extract_command(params)


@dataclass
class PermissionRule:
    """单条权限规则。

    三个匹配轴至少提供一个；提供多个时按 AND 语义匹配
    （即必须全部命中才生效）。
    """

    action: RuleAction
    tool_name: str | None = None
    path: str | None = None
    bash_pattern: str | None = None
    description: str = ""
    #: 规则标识 —— 写进审计事件的 ``rule`` 字段。留空则回落到 description。
    id: str = ""
    # 编译缓存（不参与序列化）
    _bash_re: re.Pattern[str] | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self.bash_pattern is not None:
            try:
                self._bash_re = re.compile(self.bash_pattern)
            except re.error:
                # 正则写错会让这条规则**静默失效** —— 轴永远不命中，
                # 用户以为规则生效了，其实没有。宁可响一声。
                self._bash_re = None
                logger.warning(
                    "Permission rule has invalid bash_pattern %r — axis disabled",
                    self.bash_pattern,
                )

    @property
    def label(self) -> str:
        """审计用的规则标识。"""
        return self.id or self.description or self.action.value

    @property
    def is_tool_level(self) -> bool:
        """是否「整个工具」级别的规则 —— 只声明了 tool_name 轴。

        工具级与内容级在拦截链里的位置不同：工具级的 ask 无法被
        「不再询问」白名单覆盖，内容级的可以。
        """
        return (
            self.tool_name is not None
            and self.path is None
            and self.bash_pattern is None
        )

    @property
    def is_content_level(self) -> bool:
        """是否内容级规则 —— 声明了 path 或 bash_pattern 轴。"""
        return self.path is not None or self.bash_pattern is not None

    # ------------------------------------------------------------------
    # 匹配
    # ------------------------------------------------------------------

    def matches(self, tool_name: str, params: dict[str, Any]) -> bool:
        """判断规则是否命中本次工具调用。

        未声明的轴视为「不限」；声明的轴全部命中才算匹配。
        """
        axes: list[bool] = []
        has_axis = False

        if self.tool_name is not None:
            has_axis = True
            axes.append(self._match_tool(tool_name))

        if self.path is not None:
            has_axis = True
            path = _extract_path(params)
            axes.append(path is not None and self._match_path(path))

        if self.bash_pattern is not None:
            has_axis = True
            command = _extract_command(params)
            axes.append(
                command is not None
                and self._bash_re is not None
                and bool(self._bash_re.search(command))
            )

        # 完全没有声明任何轴 → 匹配所有调用（危险，但允许显式表达）
        if not has_axis:
            return True

        return all(axes)

    def _match_tool(self, tool_name: str) -> bool:
        if self.tool_name == "*":
            return True
        return fnmatch.fnmatch(tool_name, self.tool_name or "")

    def _match_path(self, path: str) -> bool:
        # 归一化反斜杠，便于跨平台 glob
        normalized = path.replace("\\", "/")
        pattern = (self.path or "").replace("\\", "/")
        return fnmatch.fnmatch(normalized, pattern)

    # ------------------------------------------------------------------
    # 序列化
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "tool_name": self.tool_name,
            "path": self.path,
            "bash_pattern": self.bash_pattern,
            "description": self.description,
            "id": self.id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PermissionRule:
        action_raw = data.get("action")
        if not isinstance(action_raw, str):
            raise ValueError(f"Unknown rule action: {action_raw!r}")
        try:
            action = RuleAction(action_raw)
        except ValueError:
            raise ValueError(f"Unknown rule action: {action_raw!r}") from None
        return cls(
            action=action,
            tool_name=data.get("tool_name"),
            path=data.get("path"),
            bash_pattern=data.get("bash_pattern"),
            description=data.get("description", ""),
            id=str(data.get("id", "") or ""),
        )
