"""Bash 命令分类 —— 危险命令识别（对标 Claude Code Bash 安全策略）。

将 shell 命令分为三类风险级别：

- ``SAFE``：纯读、无副作用
- ``WRITE``：有写副作用（创建/修改文件、安装包等）
- ``DANGEROUS``：破坏性/不可逆（删除、格式化、提权、关机等）

危险命令默认在 ``default`` 模式下触发确认或拒绝；``bypass`` 模式才放行。
"""

from __future__ import annotations

import re
from enum import StrEnum


class BashRisk(StrEnum):
    """命令风险级别。"""

    SAFE = "safe"
    WRITE = "write"
    DANGEROUS = "dangerous"


# 危险命令模式 —— 命中即 DANGEROUS（按顺序匹配，先匹配先返回）
_DANGEROUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*(r[f]+|-r[f]|-f[r])\b"),       # rm -rf
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+)*[^;|&]*\*/"),                   # rm */ 通配删除
    re.compile(r"\bmkfs\b"),                                            # 格式化
    re.compile(r"\bdd\s+if="),                                          # dd 裸写
    re.compile(r"\bshutdown\b|\breboot\b|\bpoweroff\b|\bhalt\b"),       # 关机/重启
    re.compile(r"\bchmod\s+(-[a-zA-Z]*\s+)*777\b"),                     # chmod 777
    re.compile(r"\bchown\s+.*-R\b"),                                    # 递归改属主
    re.compile(r"\bsudo\b"),                                            # 提权
    re.compile(r"\b:\(\)\s*\{\s*:\|:&\s*\};:") ,                        # fork bomb
    re.compile(r"\bcurl\b.*\|\s*(ba)?sh\b|\bwget\b.*\|\s*(ba)?sh\b"),   # 下载即执行
    re.compile(r"\bmv\s+[^;|&]*\s+/etc/|\bmv\s+[^;|&]*\s+/(bin|sbin|usr)/"),  # 移动系统目录
    re.compile(r"\b>/\s*dev/sd[a-z]\b"),                                # 覆写块设备
    re.compile(r"\bkill\s+-9\s+-1\b|\bkillall\b"),                      # 杀进程
    re.compile(r"\bgit\s+push\s+--force\b|\bgit\s+reset\s+--hard\b"),   # 破坏性 git
]

# 写操作命令前缀 —— 命中即至少 WRITE
_WRITE_COMMANDS = {
    "touch", "mkdir", "mv", "cp", "rm", "rmdir", "chmod", "chown",
    "ln", "ln-s", "sed", "awk", "tee", "cat", "echo", "printf",
    "pip", "pip3", "npm", "yarn", "pnpm", "cargo", "go", "apt", "apt-get",
    "yum", "dnf", "brew", "snap", "make", "cmake", "install", "wget", "curl",
    "git", "tar", "unzip", "zip", "rsync", "scp", "ssh", "mount", "umount",
    "systemctl", "service", "python", "python3", "node", "cargo", "docker",
    "kubectl", "helm", "terraform", "gradle", "mvn",
}


class BashClassifier:
    """Bash 命令风险分类器。"""

    def classify(self, command: str) -> BashRisk:
        """分类命令。

        先匹配危险模式，再匹配写操作命令；否则视为 SAFE。
        """
        stripped = (command or "").strip()
        if not stripped:
            return BashRisk.SAFE

        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(stripped):
                return BashRisk.DANGEROUS

        if self._has_write_command(stripped):
            return BashRisk.WRITE

        return BashRisk.SAFE

    def is_dangerous(self, command: str) -> bool:
        return self.classify(command) is BashRisk.DANGEROUS

    def has_write_side_effects(self, command: str) -> bool:
        return self.classify(command) in (BashRisk.WRITE, BashRisk.DANGEROUS)

    @staticmethod
    def _has_write_command(command: str) -> bool:
        # 提取首个词元（跳过前导赋值/变量等），与写命令集合比对
        tokens = command.split()
        if not tokens:
            return False
        head = tokens[0]
        # 处理 sudo/command 前缀链
        while head in ("sudo", "env", "command", "nohup", "time", "xargs") and len(tokens) > 1:
            tokens = tokens[1:]
            head = tokens[0]
        # 去掉 basename（/usr/bin/echo → echo）
        head = head.split("/")[-1]
        return head in _WRITE_COMMANDS
