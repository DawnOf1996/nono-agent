"""引擎与查询配置类型。

三层配置架构：
- EngineConfig: 不可变配置，构造时设定，跨轮次持久化
- QueryConfig: 运行时快照，在每次 submit_message() 入口冻结环境状态
- ProviderConfig: LLM Provider 初始化配置

设计意图：将“不变”和“可变”分离，避免循环体内读取到变化的环境状态。
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from nono_agent.paths import resolve_session_cwd
from nono_agent.utils.tokens import resolve_context_window


def _gen_session_id() -> str:
    """自动生成会话 ID，用于 transcript 持久化和多会话隔离。"""
    return str(uuid.uuid4())


class ProviderConfig(BaseModel):
    """LLM Provider 初始化配置 —— 用于创建 Provider 实例。

    配置驱动适配器选择（按 API 协议，不区分原厂 / 其他供应商）：
    - adapter: openai_chat_completions | openai_responses | anthropic_messages
    - extra_body: 每次请求时深度合并到请求体中的额外参数
    - 供应商由 api_key + base_url 决定；三类协议一律走对应 SDK client

    用法示例::

        # MiniMax 通过 OpenAI Chat Completions 协议
        ProviderConfig(
            model_id="MiniMax-M3",
            adapter="openai_chat_completions",
            api_key="your-key",
            base_url="https://api.minimax.chat/v1",
            extra_body={"reasoning_split": True},
        )
    """

    model_id: str                              # 模型 ID（如 "claude-sonnet-4-20250514"）
    # 模型上下文窗口大小（token 数）。压缩管线按此值计算触发阈值；
    # 未配置时回退 DEFAULT_CONTEXT_WINDOW（不做模型前缀猜测）。
    model_context: int | None = None
    # API 协议: openai_chat_completions | openai_responses | anthropic_messages
    adapter: str | None = None
    api_key: str = ""                          # API 密钥
    base_url: str | None = None                # 自定义 API 端点（代理/厂商地址）
    extra_headers: dict[str, str] = Field(default_factory=dict)  # 额外 HTTP 头
    extra_body: dict[str, Any] = Field(default_factory=dict)     # 额外请求体参数（深度合并）
    timeout: float = 600.0                     # 请求超时时间（秒）
    max_retries: int = 3                       # 最大重试次数
    # 单次补全 max tokens；未配置时由各协议实现自己的默认值
    max_tokens: int | None = None


class ThinkingConfig(BaseModel):
    """扩展思考配置 —— 控制 Claude 模型的 extended thinking 行为。"""

    type: str = "adaptive"  # "adaptive"(模型自决) | "enabled"(强制开启) | "disabled"(关闭)
    budget_tokens: int | None = None  # 思考 token 上限（None 表示不限制）


class EngineConfig(BaseModel):
    """QueryEngine 不可变配置 —— 构造时设定，跨轮次持久化。

    对应 TS 版的 QueryEngineConfig。引擎生命周期内不变，
    每次 submit_message() 会基于此创建 QueryConfig 快照。
    """

    # 工具执行的工作目录（构造时收成绝对路径，禁止 ``.`` 进入会话 header）
    cwd: str = "."

    @field_validator("cwd")
    @classmethod
    def _absolute_cwd(cls, value: str) -> str:
        return resolve_session_cwd(value or ".")

    # 主模型和备用模型（主模型不可用时切换）
    model: str = "claude-sonnet-4-20250514"
    fallback_model: str | None = None

    # 模型上下文窗口大小（token 数）—— provider.model_context 未配置时的兜底配置。
    # 未配置任何值时回退 DEFAULT_CONTEXT_WINDOW；不再基于模型 ID 前缀匹配。
    model_context: int | None = None

    # Provider 配置（库用法直接构造；CLI 从 YAML 目录 resolve）
    provider: ProviderConfig | None = None

    # 运行限制
    max_turns: int | None = None                  # 最大循环轮次（None 表示不限制）
    task_budget: dict[str, float] | None = None   # Token 预算: {"total": 100000}

    # 扩展思考配置
    thinking_config: ThinkingConfig | None = None

    # 自定义系统提示（替换默认提示）
    custom_system_prompt: str | None = None

    # 追加系统提示（拼接到默认/自定义提示后面）
    append_system_prompt: str | None = None

    # 输出风格（concise/detailed/explanatory）；None=不注入
    output_style: str | None = None

    # 结构化输出 JSON Schema（配置后 LLM 会返回符合 schema 的 JSON）
    json_schema: dict[str, Any] | None = None

    # ContextCollapse 默认 summarizer（§3.3）：启用后把 LLM 摘要折叠加入默认管线
    context_collapse_enabled: bool = False
    context_collapse_model: str | None = None  # 摘要用模型（None=主模型）

    # 详细日志
    verbose: bool = False

    # 是否在 SDK 输出中回放用户消息
    replay_user_messages: bool = False

    # 是否在 SDK 输出中包含流式增量事件（stream_event）
    include_partial_messages: bool = False

    # 会话 ID（不填则自动生成）
    session_id: str = Field(default_factory=_gen_session_id)

    # 记忆作用域：所属 agent（None=不按 agent 隔离；小镇场景每个 NPC 一个 id）
    agent_id: str | None = None

    # 记忆检索预算（对齐 MemoryManager.retrieve 的 top-k / token 上限）
    memory_retrieval_k: int = 10
    memory_retrieval_token_budget: int = 2000

    # 会话日志一致性门禁：每次 LLM 请求前校验「事件流重建 == 运行时消息状态」
    # - strict: 不一致则终止本轮（log_reconstruction_desync）
    # - warn:   只记日志与指标，不打断
    # - off:    不校验（长会话省一次全量事件读取）
    session_invariant: Literal["strict", "warn", "off"] = "strict"

    # 是否把流式增量（assistant/chunk）写入事件流。
    # 开启后可在不调 LLM 的情况下逐字回放 UI，代价是事件量随输出 token 线性增长；
    # 关闭为降级模式，只保留合并后的 assistant/message。
    log_assistant_chunks: bool = True

    model_config = ConfigDict(frozen=True)  # 不可变，防止运行期意外修改配置

    @property
    def effective_model(self) -> str:
        """实际使用的模型 ID —— provider.model_id 优先于 model 字段。"""
        if self.provider:
            return self.provider.model_id
        return self.model

    @property
    def effective_model_context(self) -> int:
        """生效的模型上下文窗口大小（token 数）。

        解析顺序：provider.model_context > model_context > DEFAULT_CONTEXT_WINDOW。
        压缩管线的全部触发阈值均基于此值计算。
        """
        if self.provider and self.provider.model_context:
            return resolve_context_window(self.provider.model_context)
        return resolve_context_window(self.model_context)


class RuntimeGates(BaseModel):
    """运行时特性门控 —— 在 query() 入口快照，循环体内不再重新读取。

    这些是环境变量/配置驱动的门控，而非编译期特性开关。
    """

    streaming_tool_execution: bool = True   # 是否启用流式工具执行（工具边流边执行）
    emit_tool_use_summaries: bool = False   # 是否发射工具调用摘要
    is_internal: bool = False               # 是否内部模式（启用额外调试功能）
    fast_mode_enabled: bool = True          # 是否启用快速模式（跳过某些校验）


class QueryConfig(BaseModel):
    """查询运行时快照 —— 在每次 submit_message() 入口捕获一次。

    对应 TS 版的 query/config.ts。冻结环境变量和门控状态，
    确保循环体不会读取到运行期变化的环境。
    """

    session_id: str                              # 所属会话 ID
    gates: RuntimeGates = Field(default_factory=RuntimeGates)  # 特性门控快照

    model_config = ConfigDict(frozen=True)  # 不可变，保证循环体内看到的状态一致
