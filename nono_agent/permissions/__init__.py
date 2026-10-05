"""权限体系 —— 会话权限模式、可落盘规则、路径沙箱、拒绝追踪与 Bash 分类。

对标 Claude Code 的 PermissionMode + permissions/* 设计（docs-site/docs/roadmap/index.md §2.6）：

- :mod:`nono_agent.permissions.policy` —— PermissionMode / PermissionPolicy
- :mod:`nono_agent.permissions.rules` —— 可落盘 allow/deny/ask 规则
- :mod:`nono_agent.permissions.sandbox` —— 路径沙箱 + Ignore
- :mod:`nono_agent.permissions.denial` —— 拒绝追踪（回灌 SDKResult）
- :mod:`nono_agent.permissions.bash_classify` —— 危险命令分类

设计原则：fail-closed —— 默认拒绝写/执行，显式 allow 或切换模式才放宽。
"""

from nono_agent.permissions.bash_classify import BashClassifier, BashRisk
from nono_agent.permissions.denial import Denial, DenialTracker
from nono_agent.permissions.policy import (
    PermissionDecision,
    PermissionMode,
    PermissionPolicy,
)
from nono_agent.permissions.rules import PermissionRule, RuleAction
from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox

__all__ = [
    "BashClassifier",
    "BashRisk",
    "Denial",
    "DenialTracker",
    "IgnoreMatcher",
    "PathSandbox",
    "PermissionDecision",
    "PermissionMode",
    "PermissionPolicy",
    "PermissionRule",
    "RuleAction",
]
