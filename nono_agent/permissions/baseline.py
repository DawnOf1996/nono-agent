"""安全底线 —— 连「全部放行」模式都免疫的边界。

分两档：

- ``DENY`` —— **数据目录下的 ``credentials``**。读都不行，更别说写。
  这条是硬底线：不问，直接拒。它是密钥文件，模型读一次就等于把密钥带进
  上下文，之后每次对话都在日志里躺着。
- ``ASK`` —— 其它敏感路径（``.git/``、``.nono-agent/``、``~/.ssh/``、
  密钥文件……）。能访问，但必须让人知道。

与 ``PathSandbox`` 的分工：sandbox 管**能不能访问**（越界即拒），
baseline 管**能访问但要不要问**。两者都要，不能互相替代 —— 沙箱管不到
工作区内部的 ``.git/``，baseline 管不到工作区之外的路径。

底线与「用户显式配置」的分工：底线的 ASK 档不可被 ``auto_approve``
白名单覆盖（用户授权过某条命令，不等于授权过读密钥）；DENY 档则谁都
绕不过去。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from nono_agent.paths import CONFIG_DIR_NAME, is_data_dir, user_home
from nono_agent.permissions.rules import extract_command, extract_paths

logger = logging.getLogger(__name__)


class BaselineSeverity(StrEnum):
    """底线命中的严重度。"""

    #: 不可放行 —— 直接拒绝，不询问。
    DENY = "deny"
    #: 必须让人知道 —— 走一次询问。
    ASK = "ask"


@dataclass(frozen=True)
class BaselineHit:
    """一次底线命中。"""

    severity: BaselineSeverity
    reason: str
    rule: str


#: 受保护的目录名 —— 任意层级出现即命中（``.git`` 指目录，不是 ``.gitignore``）。
PROTECTED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        CONFIG_DIR_NAME,
        ".ssh",
        ".gnupg",
        ".aws",
        ".azure",
        ".kube",
        ".docker",
    }
)

#: 受保护的文件名 —— 精确匹配文件名（``path.name``）。
PROTECTED_FILES: frozenset[str] = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        ".netrc",
        ".gitconfig",
        ".git-credentials",
        "id_rsa",
        "id_ed25519",
        "id_ecdsa",
        "credentials",
        "credentials.json",
    }
)

#: 受保护的后缀 —— 密钥与证书。
PROTECTED_SUFFIXES: frozenset[str] = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".keystore", ".jks"}
)


class SafetyBaseline:
    """安全底线检查器。

    Args:
        enabled: 关掉后整个底线检查失效（留作应急开关，不建议用）。
        extra_dirs: 追加受保护目录名。
        extra_files: 追加受保护文件名。
        extra_suffixes: 追加受保护后缀。
    """

    def __init__(
        self,
        enabled: bool = True,
        *,
        extra_dirs: frozenset[str] | set[str] | None = None,
        extra_files: frozenset[str] | set[str] | None = None,
        extra_suffixes: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.enabled = enabled
        self._dirs = PROTECTED_DIRS | {d.lower() for d in (extra_dirs or ())}
        self._files = PROTECTED_FILES | {f.lower() for f in (extra_files or ())}
        self._suffixes = PROTECTED_SUFFIXES | {s.lower() for s in (extra_suffixes or ())}

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def check(
        self,
        tool_name: str,
        input_params: dict[str, Any],
        cwd: str,
    ) -> BaselineHit | None:
        """检查一次工具调用是否触碰底线。未命中返回 None。"""
        if not self.enabled:
            return None

        # 1. 路径参数 —— 路径级命中
        for raw in extract_paths(input_params):
            resolved = self._resolve(raw, cwd)
            if resolved is None:
                continue
            hit = self._check_path(resolved)
            if hit is not None:
                return hit

        # 2. 命令内容 —— 只查 DENY 档。
        #    ASK 档交给 BashClassifier（它按命令语义分级，比字符串匹配准）；
        #    命令里的敏感路径若不命中 DENY 档，最多算"可疑"，不值得拦。
        command = extract_command(input_params)
        if command and self._command_touches_credentials(command):
            return BaselineHit(
                severity=BaselineSeverity.DENY,
                reason=(
                    "拒绝访问数据目录下的 credentials —— "
                    "密钥文件不得进入模型上下文。"
                ),
                rule="baseline:data-dir-credentials",
            )

        return None

    # ------------------------------------------------------------------
    # 判定
    # ------------------------------------------------------------------

    def _check_path(self, resolved: Path) -> BaselineHit | None:
        """对已解析的绝对路径做底线判定。"""
        # DENY 档先判 —— 数据目录的 credentials 优先级最高。
        if self._is_data_dir_credentials(resolved):
            return BaselineHit(
                severity=BaselineSeverity.DENY,
                reason=(
                    f"拒绝访问 {resolved} —— 数据目录下的 credentials "
                    f"是密钥文件，不得进入模型上下文。"
                ),
                rule="baseline:data-dir-credentials",
            )

        name = resolved.name.lower()
        if name in self._files:
            return BaselineHit(
                severity=BaselineSeverity.ASK,
                reason=f"访问敏感文件 {resolved} 需要确认。",
                rule=f"baseline:file:{name}",
            )

        suffix = resolved.suffix.lower()
        if suffix in self._suffixes:
            return BaselineHit(
                severity=BaselineSeverity.ASK,
                reason=f"访问密钥/证书文件 {resolved} 需要确认。",
                rule=f"baseline:suffix:{suffix}",
            )

        for part in resolved.parts:
            low = part.lower()
            if low in self._dirs:
                return BaselineHit(
                    severity=BaselineSeverity.ASK,
                    reason=f"访问受保护目录 {resolved} 需要确认。",
                    rule=f"baseline:dir:{low}",
                )

        return None

    @staticmethod
    def _is_data_dir_credentials(resolved: Path) -> bool:
        """是不是数据目录下的 credentials 文件。

        判定要两件事同时成立：文件名是 ``credentials``，父目录是数据目录
        （``.nono-agent`` 且确实是数据目录，不是项目里同名的配置目录）。
        """
        if resolved.name.lower() != "credentials":
            return False
        parent = resolved.parent
        if parent.name != CONFIG_DIR_NAME:
            return False
        try:
            return is_data_dir(parent)
        except OSError:
            return False

    @staticmethod
    def _command_touches_credentials(command: str) -> bool:
        """命令文本里是否指向数据目录下的 credentials。

        只在同时出现 ``credentials`` 与数据目录标识时才判定 —— 单独出现
        ``credentials``（比如 ``grep credentials .``）不算。
        """
        low = command.replace("\\", "/").lower()
        if "credentials" not in low:
            return False
        if CONFIG_DIR_NAME in low:
            return True
        try:
            home = str(user_home()).replace("\\", "/").lower()
        except OSError:
            return False
        return bool(home) and home in low

    # ------------------------------------------------------------------
    # 路径解析
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve(raw: str, cwd: str) -> Path | None:
        """把入参路径解析成绝对路径。解析不了就当没路径，不猜。"""
        try:
            p = Path(os.path.expanduser(raw))
            if not p.is_absolute():
                p = Path(cwd) / p
            return p.resolve()
        except (OSError, ValueError, RuntimeError):
            return None
