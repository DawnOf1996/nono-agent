"""Rules —— 常驻指令层（对标 Cursor Rules / CC CLAUDE.md）。

发现与加载分离：Loader 只负责扫描校验，Injector/Section 负责挂载。
"""

from nono_agent.rules.loader import ROOT_RULE_FILES, FileSystemRuleLoader, RuleLoader
from nono_agent.rules.merge import list_effective_rules, merge_rules
from nono_agent.rules.models import Rule

__all__ = [
    "FileSystemRuleLoader",
    "Rule",
    "RuleLoader",
    "ROOT_RULE_FILES",
    "list_effective_rules",
    "merge_rules",
]
