"""权限决策三态 —— 工具侧与策略层共用的词汇表。

单独放在叶子模块里，是为了让 ``tools/protocol.py`` 能引用它而不产生循环依赖：
权限层从不反过来 import 工具层（``tools`` → ``permissions`` 是单向的）。

三个值同时承担两个角色：

- **策略层**：``PermissionPolicy`` 与拦截链的最终结论
- **工具层**：``Tool.evaluate_permission`` 对本次调用的自评

两层用同一套词汇，链上就不需要「把工具的结论翻译成策略的结论」这一步 ——
翻译本身就是容易出错的地方。
"""

from __future__ import annotations

from enum import StrEnum


class PermissionDecision(StrEnum):
    """权限决策三态。

    - ``ALLOW`` —— 直接放行
    - ``ASK``   —— 需要人确认
    - ``DENY``  —— 拒绝，终局

    ASK 另有软硬之分，但**不靠额外的标志位区分** —— 软硬由它在拦截链里的
    位置决定：收紧层里的 ASK 不可被任何自动放行越过，放宽层兜底的 ASK 可被
    bypass 放宽。这样「哪种 ASK 免疫哪种模式」是代码结构读出来的，不是配置出来的。
    """

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

    @property
    def is_allowed(self) -> bool:
        return self is PermissionDecision.ALLOW

    @property
    def is_denied(self) -> bool:
        return self is PermissionDecision.DENY
