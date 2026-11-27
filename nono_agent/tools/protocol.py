"""工具协议定义。

所有工具必须实现 Tool Protocol。遵循"安全默认"的 Fail-closed 设计：
- is_concurrent_safe = False  — 默认不并发安全
- has_write_side_effects = True — 默认假设有写副作用

两层设计：
- Tool Protocol: 接口约束（使用 @runtime_checkable + Protocol）
- ToolBase: 便利基类，子类只需覆盖 _name/_description/_input_schema/execute

**权限侧也是协议的一部分。** 工具不只是"被调用"，它还要对"这次调用能不能
执行"表态 —— 拦截链（``permissions/gate.py``）按固定顺序问一圈，谁先命中谁
说了算。工具在链上的三个位置：

- ``evaluate_permission``  —— 对本次调用的自评（可看入参动态判断）
- ``always_requires_approval`` —— 声明本工具任何模式下都必须由人确认
- ``has_write_side_effects`` —— 静态声明有没有写副作用

没被声明的按更危险的情况处理：``evaluate_permission`` 拿不到就当有写副作用
（要问），``always_requires_approval`` 拿不到就当不需要（但写副作用已经让它
要问了）。漏写规则的后果应该是"多问一次"，而不是"漏放一次"。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from nono_agent.core.types import ToolResult
from nono_agent.permissions.decision import PermissionDecision


@runtime_checkable  # 支持 isinstance() 检查
class Tool(Protocol):
    """所有工具必须实现的协议接口。

    Fail-closed 设计：
    - 工具默认不并发安全，除非明确标记 is_concurrent_safe=True
    - 工具默认有写副作用，除非明确标记 has_write_side_effects=False
    """

    @property
    def name(self) -> str:
        """工具唯一标识（如 'read_file', 'bash'）。"""
        ...

    @property
    def description(self) -> str:
        """人类可读的描述，展示给 LLM 帮助其理解工具用途。"""
        ...

    @property
    def input_schema(self) -> dict[str, Any]:
        """工具输入参数的 JSON Schema，用于 LLM 生成正确的调用参数。"""
        ...

    @property
    def is_concurrent_safe(self) -> bool:
        """工具是否可以与其他工具安全并行执行。

        默认（Fail-closed）: False
        仅当工具真正无副作用或有内部锁机制时才覆盖为 True。
        """
        ...

    @property
    def has_write_side_effects(self) -> bool:
        """工具是否修改文件系统或外部状态。

        默认（Fail-closed）: True
        纯读取工具（如 read_file, glob）应覆盖为 False。
        """
        ...

    @property
    def is_read_only(self) -> bool:
        """has_write_side_effects 的反向便利属性。"""
        ...

    @property
    def always_requires_approval(self) -> bool:
        """本工具是否**必须**由人确认，任何权限模式都不能跳过。

        与 ``evaluate_permission`` 返回 ASK 的区别：ASK 是"这一次要问"，
        本属性是"这个工具永远要问"。后者对 bypass 模式免疫。

        默认 False —— 工具不声明就是不需要额外豁免。
        """
        ...

    async def execute(
        self,
        input_params: dict[str, Any],
        context: Any,  # ToolUseContext —— 前向引用避免循环导入
    ) -> ToolResult:
        """执行工具 —— 核心方法。

        Args:
            input_params: 经过校验的输入参数，符合 self.input_schema
            context: ToolUseContext，包含 cwd、abort signal、权限检查器等

        Returns:
            ToolResult，包含执行结果 content 和错误标志 is_error
        """
        ...

    def evaluate_permission(
        self,
        input_params: dict[str, Any],
        context: Any,  # ToolUseContext
    ) -> PermissionDecision:
        """工具对本次调用的自评 —— 可看入参动态判断。

        拦截链在收紧层第一个问它。返回 DENY 是终局（不可被任何模式放行）；
        返回 ASK / ALLOW 表示"工具不反对"，链继续往下走。

        默认实现：有写副作用 → ASK，否则 ALLOW。子类可按入参覆写
        （例如 bash 按命令风险分级）。
        """
        ...

    def should_prompt_for_permission(
        self,
        input_params: dict[str, Any],
        context: Any,  # ToolUseContext
    ) -> bool:
        """此次调用是否需要用户明确授权。

        兼容旧接口 —— 新链走 ``evaluate_permission``。本方法委托给它，
        只保留布尔语义。

        在 execute() 前调用。返回 True 时触发权限确认。
        默认: 委托给 has_write_side_effects（有写副作用就需要授权）。
        """
        ...

    def to_api_schema(self) -> dict[str, Any]:
        """转换为 API 工具 schema 格式（兼容 Anthropic/OpenAI）。"""
        ...


class ToolBase:
    """工具便利基类 —— 提供合理默认值。

    子类化此类而非直接实现 Protocol。子类只需覆盖：
    - _name: 工具名称
    - _description: 工具描述
    - _input_schema: 输入参数 JSON Schema
    - execute(): 工具执行逻辑

    安全默认值（Fail-closed）：
    - _is_concurrent_safe = False  （默认不并发安全）
    - _has_write_side_effects = True  （默认假设有写副作用）
    """

    # Fail-closed 安全默认值
    _name: str = ""
    _description: str = ""
    _input_schema: dict[str, Any] = {}
    _is_concurrent_safe: bool = False           # 默认不并发安全
    _has_write_side_effects: bool = True        # 默认假设有写副作用
    _always_requires_approval: bool = False     # 默认不要求每次人工确认

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def input_schema(self) -> dict[str, Any]:
        return self._input_schema

    @property
    def is_concurrent_safe(self) -> bool:
        return self._is_concurrent_safe

    @property
    def has_write_side_effects(self) -> bool:
        return self._has_write_side_effects

    @property
    def is_read_only(self) -> bool:
        return not self._has_write_side_effects

    @property
    def always_requires_approval(self) -> bool:
        return self._always_requires_approval

    def evaluate_permission(
        self,
        input_params: dict[str, Any],
        context: Any,
    ) -> PermissionDecision:
        """默认策略: 有写副作用就要问，否则放行。

        读的是**属性** ``has_write_side_effects`` 而不是私有字段 ——
        子类覆写属性（比如按实例配置决定读写）时，这里必须跟着变，
        否则属性说"只读"、自评说"有写副作用"，两边打架。
        """
        if self.has_write_side_effects:
            return PermissionDecision.ASK
        return PermissionDecision.ALLOW

    def should_prompt_for_permission(
        self,
        input_params: dict[str, Any],
        context: Any,
    ) -> bool:
        """兼容旧接口 —— 委托给 ``evaluate_permission``。"""
        return self.evaluate_permission(input_params, context) is PermissionDecision.ASK

    def to_api_schema(self) -> dict[str, Any]:
        """转换为 Anthropic Messages API 工具 schema 格式。"""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        """执行工具 —— 子类必须覆盖。"""
        raise NotImplementedError
