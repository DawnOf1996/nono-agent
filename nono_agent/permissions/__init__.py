"""权限体系 —— 拦截链、会话权限模式、可落盘规则、路径沙箱、拒绝追踪与 Bash 分类。

对标 Claude Code 的 PermissionMode + permissions/* 设计（docs-site/docs/roadmap/index.md §2.6）：

- :mod:`nono_agent.permissions.gate` —— **权限拦截链**（决策的唯一实现，顺序即优先级）
- :mod:`nono_agent.permissions.decision` —— ALLOW/ASK/DENY 三态词汇表（工具层与策略层共用）
- :mod:`nono_agent.permissions.policy` —— PermissionMode / PermissionPolicy / 规则分层查询
- :mod:`nono_agent.permissions.rules` —— 可落盘 allow/deny/ask 规则
- :mod:`nono_agent.permissions.baseline` —— 安全底线（连 bypass 都免疫）
- :mod:`nono_agent.permissions.sandbox` —— 路径沙箱 + Ignore
- :mod:`nono_agent.permissions.denial` —— 拒绝追踪（回灌 SDKResult）
- :mod:`nono_agent.permissions.bash_classify` —— 危险命令分类

设计原则：fail-closed —— 默认拒绝写/执行，显式 allow 或切换模式才放宽。
没被声明的，按更危险的情况处理：漏写规则的后果是"多问一次"，不是"漏放一次"。
"""

from nono_agent.permissions.baseline import (
    BaselineHit,
    BaselineSeverity,
    SafetyBaseline,
)
from nono_agent.permissions.bash_classify import BashClassifier, BashRisk
from nono_agent.permissions.decision import PermissionDecision
from nono_agent.permissions.denial import Denial, DenialTracker
from nono_agent.permissions.gate import (
    GateOutcome,
    GateStep,
    PermissionCheck,
    PermissionGate,
    PermissionRequest,
)
from nono_agent.permissions.policy import (
    PermissionMode,
    PermissionPolicy,
)
from nono_agent.permissions.rules import PermissionRule, RuleAction
from nono_agent.permissions.sandbox import IgnoreMatcher, PathSandbox

__all__ = [
    "BaselineHit",
    "BaselineSeverity",
    "BashClassifier",
    "BashRisk",
    "Denial",
    "DenialTracker",
    "GateOutcome",
    "GateStep",
    "IgnoreMatcher",
    "PathSandbox",
    "PermissionCheck",
    "PermissionDecision",
    "PermissionGate",
    "PermissionMode",
    "PermissionPolicy",
    "PermissionRequest",
    "PermissionRule",
    "RuleAction",
    "SafetyBaseline",
]
