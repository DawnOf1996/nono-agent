"""预处理管线 —— 可插拔的分阶段消息压缩机制。

每个 stage 实现 PreprocessStage Protocol，由 PreprocessPipeline 管理注册、排序和执行。

执行流程:
  messages → Stage1 → Stage2 → Stage3 → ... → 压缩后的 messages

默认 5 级成本递进:
  1. ToolResultBudget (priority=10): 截断超长工具结果
  2. SnipCompact (priority=20): 零成本滑动窗口丢弃旧消息
  3. MicroCompact (priority=110): 剥离旧 thinking + 截断长文本
  4. ContextCollapse (priority=150): LLM 摘要压缩旧消息
  5. AutoCompact (priority=200): 安全网（80%/95% 阈值）

新 stage 可以通过 register(before/after) 插入任意位置，无需修改核心循环。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from nono_agent.core.types import Message
from nono_agent.utils.tokens import DEFAULT_CONTEXT_WINDOW

logger = logging.getLogger(__name__)


@dataclass
class PipelineContext:
    """管线上下文 —— 在 stage 间传递的共享状态。"""

    # 当前模型 ID（用于日志与摘要器签名）
    model_id: str = ""

    # 生效的模型上下文窗口大小（token 数）—— 来自模型配置
    # （EngineConfig.effective_model_context），stage 据此计算触发阈值
    context_window: int = DEFAULT_CONTEXT_WINDOW

    # 系统提示词 + 工具 schema 的固定 token 开销。
    # 这部分不随消息压缩变化，计入 estimated_tokens 但 stage 重算消息部分时需加回。
    overhead_tokens: int = 0

    # API 调用的 token 预算
    max_tokens: int | None = None

    # 当前 token 估算值（含 overhead_tokens；stage 压缩后更新）
    estimated_tokens: int = 0

    # 是否是重试尝试（重试时 stage 可采取更激进的策略）
    is_retry: bool = False

    # stage 可读写的额外元数据
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """管线执行结果 —— 包含压缩后的消息和执行统计。"""

    messages: list[Message]                         # 处理后的消息列表
    context: PipelineContext                        # 更新后的上下文
    stages_applied: list[str] = field(default_factory=list)  # 实际执行的 stage 名称
    tokens_saved: int = 0                           # 节省的 token 数
    compact_boundary_created: bool = False          # 是否创建了压缩边界


@runtime_checkable
class PreprocessStage(Protocol):
    """预处理阶段协议 —— 所有管线 stage 必须实现的接口。"""

    @property
    def name(self) -> str:
        """Stage 唯一标识。"""
        ...

    @property
    def priority(self) -> int:
        """执行顺序 —— 数字越小越先执行。

        推荐范围:
        - 0-99: 轻量变换（工具结果截断、滑动窗口丢弃）
        - 100-199: 中等成本（微压缩、上下文折叠）
        - 200+: 重型变换（自动压缩安全网）
        """
        ...

    def should_run(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> bool:
        """快速检查是否需要运行此 stage —— 实现应该廉价（无 token 计数）。"""
        ...

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> tuple[list[Message], PipelineContext]:
        """执行预处理阶段 —— 可原地修改或替换消息列表。

        Returns:
            (messages, context) —— 压缩后的消息和更新后的上下文
        """
        ...


class PreprocessPipeline:
    """预处理管线 —— 管理并执行有序的处理阶段列表。"""

    def __init__(self) -> None:
        self._stages: list[PreprocessStage] = []

    def register(
        self,
        stage: PreprocessStage,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        """注册 stage —— 支持 before/after 指定插入位置，默认按 priority 排序。"""
        if before:
            for i, s in enumerate(self._stages):
                if s.name == before:
                    self._stages.insert(i, stage)
                    return
        elif after:
            for i, s in enumerate(self._stages):
                if s.name == after:
                    self._stages.insert(i + 1, stage)
                    return

        # 默认: 按 priority 插入到正确位置
        for i, s in enumerate(self._stages):
            if stage.priority < s.priority:
                self._stages.insert(i, stage)
                return
        self._stages.append(stage)

    def unregister(self, name: str) -> PreprocessStage | None:
        """按名称移除 stage。"""
        for i, s in enumerate(self._stages):
            if s.name == name:
                return self._stages.pop(i)
        return None

    async def execute(
        self,
        messages: list[Message],
        context: PipelineContext,
    ) -> PipelineResult:
        """按 priority 顺序执行所有已注册的 stage。

        should_run() 返回 False 的 stage 会被跳过。
        stage 异常不会中断管线（捕获后记录日志并继续）。
        """
        stages_applied: list[str] = []
        initial_tokens = context.estimated_tokens  # 记录初始 token 数，用于计算节省量
        compact_boundary_created = False

        # 按 priority 排序确保正确执行顺序
        sorted_stages = sorted(self._stages, key=lambda s: s.priority)

        for stage in sorted_stages:
            try:
                if not stage.should_run(messages, context):
                    logger.debug("Skipping stage '%s' (should_run=False)", stage.name)
                    continue

                logger.debug("Running stage '%s'", stage.name)
                messages, context = await stage.execute(messages, context)
                stages_applied.append(stage.name)

                # 检查 stage 是否创建了压缩边界
                if context.metadata.get("compact_boundary_created"):
                    compact_boundary_created = True
                    # 移除压缩边界标志，避免影响后续 stage 误读
                    context.metadata.pop("compact_boundary_created", None)

            except Exception:
                # stage 异常不中断管线，记录日志后继续
                logger.exception("Stage '%s' failed, continuing pipeline", stage.name)

        return PipelineResult(
            messages=messages,
            context=context,
            stages_applied=stages_applied,
            tokens_saved=max(0, initial_tokens - context.estimated_tokens),  # 计算节省的 token
            compact_boundary_created=compact_boundary_created,
        )

    @property
    def stages(self) -> list[PreprocessStage]:
        return list(self._stages)

    def __len__(self) -> int:
        return len(self._stages)
