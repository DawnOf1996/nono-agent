# Nono Agent —— 核心设计理念与系统架构

> **文档角色：** 描述目标架构与已落地设计。实现进度、缺口与打磨项见 [ROADMAP.md](./ROADMAP.md)。  
> **当前版本：** `0.1.0` —— 可扩展的 Agent **运行时骨架**，不是能力齐备的 Agent 产品。

## 1. 项目概述

Nono Agent 是一个 Python AI Agent 引擎，灵感来源于 Claude Code 的 TypeScript QueryEngine 架构。它将经过验证的 Agent 设计理念（扣留-恢复机制、多层压缩管线、状态转移逻辑）重构为 Python 实现，并在架构上支持 **CLI 终端** 与 **线上服务端 SDK** 双场景。

**技术栈：** Python 3.12+ / Pydantic v2 / httpx / Anthropic SDK / OpenAI SDK / FastAPI / Rich / Typer

### 1.1 实现状态总览

| 层级 | 状态 | 说明 |
|------|------|------|
| 核心引擎（`QueryEngine` / `agentic_loop` / 状态机 / 恢复链） | 骨架可用 | 主路径可跑通；边界行为与打磨见 Roadmap |
| 预处理管线 | 部分可用 | 默认 4 级；`ContextCollapse` 有实现但未进默认管线 |
| LLM Provider | 部分可用 | 原厂 Anthropic/OpenAI + Chat Completions 兼容可用；Messages/Responses 兼容层与原厂 Responses 见 Roadmap |
| 工具协议与执行器 | 骨架可用 | 协议/注册表/执行器在；内置工具仅为演示 |
| Skills | **未实现** | 含 SkillTool；见 Roadmap §2.3 |
| Rules / Plugins / Settings | **未实现** | 常驻指令、扩展包、分层配置；§1.5 / §2.2 / §2.5 / §2.6.1 |
| Prompt Section 组装 | **未实现** | 对标 CC Prompt-Assembly；§2.0 |
| MCP | **未实现** | tools + resources + auth；§2.4 |
| 子 Agent 三件套 | **未实现** | AgentTool + Definition + run；§2.8 |
| 权限模式 / Rule / denial / Hooks / Slash | **未实现** | §2.6–§2.7 |
| 会话持久化（`TranscriptWriter`） | 骨架 | 默认本地 NDJSON；DB/Redis 等后端见 Roadmap |
| CLI / Server 适配器 | 基本可用 | Server 目前仅 SSE（文档曾提及的 WebSocket 未实现） |
| 可观测性 | 部分可用 | CostTracker + 日志可用；Metrics 为 No-Op 占位 |

---

## 2. 核心设计理念

### 2.1 单一引擎内核，双侧适配

引擎只暴露一个公开 API —— `submit_message()`，返回 `AsyncGenerator[SDKMessage]`。CLI 适配器和服务端适配器消费同一个生成器，区别仅在于交付方式：

- **CLI**：本地 `async for` 消费，Rich 直接渲染到终端
- **Server**：通过 SSE 推送给远程 Web 客户端（WebSocket 为规划能力，见 Roadmap）

```
                    ┌──────────────┐
                    │ QueryEngine  │
                    │              │
                    │ submit_msg() │──→ AsyncGenerator[SDKMessage]
                    └──────────────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
        ┌──────────┐         ┌──────────┐
        │   CLI    │         │  Server  │
        │ (Rich)   │         │  (SSE)   │
        └──────────┘         └──────────┘
```

### 2.2 不可变配置 + 运行时快照分离

配置架构将"不变"与"可变"分离：

| 类型 | 生命周期 | 作用 |
|------|---------|------|
| `EngineConfig` | 构造时设定，`frozen=True` | 模型、预算、系统提示等 |
| `QueryConfig` | 每次 `submit_message()` 入口冻结 | 运行时门控快照 |
| `LoopState` | 每次迭代构造新实例，`frozen=True` | 循环状态（轮次、恢复计数等） |

这解决了 TS 版的常见 bug：循环体内读取到运行期变化的环境状态。

### 2.3 强类型状态机

使用 `Enum` + `dataclass(frozen=True)` + `match/case` 穷举所有状态转移和终止原因：

- **7 种转移原因** (`TransitionReason`)：NEXT_TURN / MAX_OUTPUT_TOKENS_ESCALATE / MAX_OUTPUT_TOKENS_RECOVERY / REACTIVE_COMPACT_RETRY / COLLAPSE_DRAIN_RETRY / STOP_HOOK_BLOCKING / TOKEN_BUDGET_CONTINUATION
- **10 种终止原因** (`TerminalReason`)：COMPLETED / ABORTED_STREAMING / ABORTED_TOOLS / PROMPT_TOO_LONG / IMAGE_ERROR / MODEL_ERROR / BLOCKING_LIMIT / STOP_HOOK_PREVENTED / HOOK_STOPPED / MAX_TURNS

每次 `continue` 必须构造新的 `LoopState` 实例，编译器保证所有字段都被显式处理。

### 2.4 Fail-Closed 安全设计

工具协议采用安全默认值：
- `is_concurrent_safe = False` —— 默认不并发安全
- `has_write_side_effects = True` —— 默认假设有写副作用
- 权限检查器通过依赖注入，CLI 交互式确认，Server 默认拒绝所有写操作

### 2.5 协议驱动的可插拔架构

所有扩展点均基于 Python `Protocol`（结构化子类型），无需继承即可实现：

| 协议 | 职责 | 扩展方式 |
|------|------|---------|
| `LLMProvider` | LLM API 适配 | `ProviderRegistry` 注册 |
| `Tool` | 工具执行 | `ToolRegistry` 注册 |
| `PreprocessStage` | 预处理管线阶段 | `pipeline.register()` |
| `EngineMiddleware` | 生命周期钩子 | `MiddlewareChain.add()` |
| `TranscriptWriter` | 会话持久化 | 构造函数注入 |
| `PermissionChecker` | 权限策略 | 构造函数注入 |

### 2.6 高度透明的设计理念

引擎内部流转的消息（`InternalMessage`）与对外协议格式（`SDKMessage`）分离。`QueryEngine._to_sdk_messages()` 负责转换，外部消费者只看到标准化的 SDK 消息流。所有中间状态（流式事件、工具进度、压缩通知、Tombstone 清理）都通过事件透传给消费者。

---

## 3. 系统架构

### 3.1 完整包树与职责

> 目标结构 = **引擎内核（保留）+ LLM/会话（扩展）+ 工具拆分 + 扩展面（新建）**。  
> 详细规划、迁移对照与依赖禁忌见 [ROADMAP.md §5](./ROADMAP.md)。  
> 标记：`✅` 已有 · `🔧` 已有需扩展 · `📋` 规划 · `P2` 后期

```
nono_agent/
│
│ ─── 引擎内核 ─────────────────────────────────────────────
├── core/                 # ✅ QueryEngine / loop / config / state / types
├── pipeline/             # 🔧 预处理压缩（默认 4 级；ContextCollapse 可选）
├── recovery/             # ✅ RecoveryChain 与策略
├── hooks/                # 🔧 引擎内 Middleware/Stop/PostSampling
├── adapters/             # 🔧 CLI / Server(SSE) / Testing
├── observability/        # 🔧 CostTracker / logging / metrics(占位)
├── utils/                # 🔧 abort / tokens
│
│ ─── LLM 与会话 ───────────────────────────────────────────
├── providers/            # 🔧 原厂 + compatible 协议矩阵
├── session/              # 🔧 MessageStore + TranscriptWriter(+DB/Redis 规划)
│
│ ─── 工具 ─────────────────────────────────────────────────
├── tools/                # 🔧 protocol / registry / executor / context
│   └── builtin/          # 📋 生产级工具包（替代演示 builtin.py）
│
│ ─── 扩展面 ───────────────────────────────────────────────
├── prompt/               # 📋 PromptSection + Registry + Assembler
├── rules/                # 📋 常驻指令发现/合并（CLAUDE.md/AGENTS.md 兼容）
├── skills/               # 📋 Skill 加载 + SkillTool
├── plugins/              # 📋 扩展包加载（组合下层能力）
├── commands/             # 📋 Slash MVP（无 UI）
├── settings/             # 📋 user/project/policy/flag 合并
├── permissions/          # 📋 Policy / Rule / denial / sandbox
├── mcp/                  # 📋 client + tools/resources/auth 桥接
├── agents/               # 📋 AgentTool + Definition + runner
├── memory/               # P2 MemoryStore / memdir
└── checkpoint/           # P2 编辑快照回滚
```

#### 3.1.1 各包职责一览

| 包 | 职责摘要 | 边界（不负责） |
|----|----------|----------------|
| `core/` | 编排、主循环、配置快照、状态机、消息/SDK 类型 | HTTP、具体工具、UI |
| `pipeline/` | 调用前消息压缩各 Stage | 持久化、权限 |
| `recovery/` | 错误分类与恢复动作 | UI、Provider 传输细节 |
| `hooks/` | 代码级生命周期链 | 用户 `hooks.json` 加载（适配后挂入） |
| `adapters/` | 流式交付、交互确认、AskUser/Slash 入口 | 业务工具与 Prompt 内容 |
| `observability/` | 成本、日志、指标 | 业务决策 |
| `utils/` | Abort、token 等无领域原语 | 领域策略 |
| `providers/` | LLM 协议适配与路由 | 工具执行、会话存储 |
| `session/` | 内存消息 + Transcript 多后端 | 跨会话 Memory |
| `tools/` | Tool 协议、注册、执行、上下文 | 权限策略实现 |
| `tools/builtin/` | Bash/Read/Edit/AskUser/LSP/Worktree 等 | MCP/Skill/Agent 桥接实现可外置再注册 |
| `prompt/` | Section 组装、静/动态边界、优先级 | 文件发现（消费 rules/memory 视图） |
| `rules/` | 常驻指令发现与合并 | 最终 prompt 拼接 |
| `skills/` | Skill 包 + SkillTool | Plugin 清单格式 |
| `plugins/` | 扩展包启停与组合注册 | 具体 Tool/Agent 业务代码 |
| `commands/` | Slash 注册与解析 | 终端/HTTP 渲染 |
| `settings/` | 分层配置合并单一入口 | 取代 EngineConfig 类型本身 |
| `permissions/` | 模式、规则、denial、沙箱、Ignore | tool.execute 业务 |
| `mcp/` | MCP 生命周期与桥接 | Tool Protocol 定义 |
| `agents/` | 子 Agent 三件套 | Teams/Coordinator（可选 P2） |
| `memory/` | 跨会话记忆 | 单次 transcript |
| `checkpoint/` | 编辑回滚快照 | Git 本身 |

#### 3.1.2 依赖方向

```text
adapters → core → prompt / pipeline / recovery / providers / tools / agents / session / permissions / …
plugins  → rules / skills / mcp / agents / commands / permissions   （只组合）
core ↛ adapters · prompt ↛ tool 执行实现
```

#### 3.1.3 迁移要点

- **保留：** 内核七件套 + `providers`/`session`/`tools` 协议层  
- **拆分：** `tools/builtin.py` → `tools/builtin/`  
- **抽出：** `_build_system_prompt` → `prompt/`；权限闭包 → `permissions/`  
- **新建：** 扩展面各包按 Phase 落地，旧路径可短期 re-export  

项目配置目录（非 Python 包）：`.nono/{settings,rules,agents,skills,plugins,hooks.json,mcp.json,memory}/`。

### 3.2 架构总览图

```mermaid
graph TB
    subgraph Adapters
        CLI[CLI Adapter<br/>Typer + Rich]
        Server[Server Adapter<br/>FastAPI + SSE]
    end

    subgraph Engine
        QE[QueryEngine<br/>顶层编排]
        AL[Agentic Loop<br/>while True 主循环]
    end

    subgraph Subsystems
        PP[PreprocessPipeline<br/>默认4级压缩管线]
        MW[MiddlewareChain<br/>生命周期钩子]
        PR[ProviderRegistry<br/>LLM路由]
        TR[ToolRegistry<br/>工具注册]
        TE[StreamingToolExecutor<br/>工具执行]
        RC[RecoveryChain<br/>错误恢复]
        SH[StopHookExecutor<br/>响应校验]
        PS[PostSamplingChain<br/>采样后钩子]
    end

    subgraph Session
        MS[MessageStore<br/>运行时内存]
        TW[TranscriptWriter<br/>可插拔持久化]
        CT[CostTracker<br/>成本跟踪]
    end

    CLI --> QE
    Server --> QE
    QE --> AL
    AL --> PP
    AL --> MW
    AL --> PR
    AL --> TE
    AL --> RC
    AL --> SH
    AL --> PS
    TE --> TR
    QE --> MS
    QE --> TW
    QE --> CT
```

---

## 4. Engine → Loop 流程示意图

### 4.1 submit_message() 七阶段流程

```mermaid
sequenceDiagram
    participant User
    participant Adapter as CLI / Server
    participant QE as QueryEngine
    participant ALoop as AgenticLoop
    participant SDK as SDKMessage流

    User->>Adapter: 用户输入
    Adapter->>QE: submit_message(input)

    Note over QE: Phase 1 快照配置与校验
    QE->>QE: 创建 QueryConfig frozen

    Note over QE: Phase 2: 添加用户消息
    QE->>QE: store.append UserMessage
    QE->>QE: transcript.record_append
    opt replay_user_messages
        QE-->>SDK: SDKUserMessageReplay
    end

    Note over QE: Phase 3 解析 Provider 并构建上下文
    QE->>QE: provider_registry.resolve
    QE->>QE: _build_system_prompt
    QE->>QE: 构建 LoopState

    Note over QE,ALoop: Phase 4 运行 agentic_loop
    QE->>ALoop: agentic_loop 所有子系统
    ALoop-->>QE: yield InternalMessage
    QE-->>SDK: yield SDKMessage 转换后

    loop for each InternalMessage from ALoop
        ALoop-->>QE: InternalMessage
        QE->>QE: _to_sdk_messages
        QE->>QE: 记录 cost 和 transcript
        QE-->>SDK: yield SDKMessage
    end

    Note over QE: Phase 6 持久化完整 transcript
    QE->>QE: transcript.record all_messages

    Note over QE: Phase 7 发出最终结果
    QE-->>SDK: yield SDKResultMessage
    SDK-->>Adapter: 流式渲染
    Adapter-->>User: 展示结果
```

### 4.2 Agentic Loop 内部七阶段循环

```mermaid
sequenceDiagram
    participant PP as PreprocessPipeline
    participant MW as MiddlewareChain
    participant LLM as LLMProvider
    participant PSC as PostSamplingChain
    participant SH as StopHookExecutor
    participant TE as StreamingToolExecutor
    participant MS as MessageStore

    Note over PP,MS: while True - 每次迭代是一次完整 LLM 调用加工具执行

    rect rgb(230, 245, 255)
        Note over PP: Phase 1 预处理消息
        PP->>PP: ToolResultBudget then SnipCompact then MicroCompact then AutoCompact
        PP-->>MS: 压缩后的 messages
    end

    rect rgb(240, 255, 240)
        Note over MW: Phase 2 中间件预处理
        MW->>MW: run_before_api_call
        MW-->>MS: 可修改 messages 和 prompt 和 tools
    end

    rect rgb(255, 245, 230)
        Note over LLM: Phase 3 LLM API 调用 流式
        LLM-->>MS: yield StreamEvent 透传
        LLM-->>MS: yield AssistantMessage
    end

    rect rgb(245, 240, 255)
        Note over MW: Phase 4 组装 AssistantMessage
        MW->>MW: run_after_api_call
        MS->>MS: store.append assistant_msg
    end

    rect rgb(255, 255, 230)
        Note over PSC: Phase 5 Post-sampling 钩子
        PSC->>PSC: run assistant_msg and tool_blocks
    end

    rect rgb(255, 235, 235)
        Note over SH: Phase 6 Stop hooks
        SH->>SH: execute assistant_msg
        alt preventing
            SH-->>MS: 终止循环
        else blocking
            SH-->>MS: 注入错误信息, continue
        end
    end

    rect rgb(235, 255, 245)
        Note over TE: Phase 7 工具执行与转移决策
        alt no tool_use blocks
            TE-->>MS: COMPLETED
        else has tool_use blocks
            TE->>TE: 并发安全工具并行, 顺序工具串行
            TE-->>MS: tool_result as UserMessage
            MS->>MS: store.append tool_result_msg
            Note over TE: Transition NEXT_TURN, 构造新 LoopState 后 continue
        end
    end
```

### 4.3 错误恢复链决策流程

```mermaid
graph TB
    Error[API Error] --> Classify{错误分类}

    Classify -->|prompt_too_long| PTL{已尝试压缩?}
    PTL -->|No| RC[反应式压缩: 剥离thinking并截断tool_result]
    PTL -->|Yes| CD[上下文折叠排水: 移除最旧N对消息]

    Classify -->|max_output_tokens| MOT{已升级tokens?}
    MOT -->|No| ESC[升级max_tokens: 8k到64k]
    MOT -->|Yes| REC{恢复次数小于3?}
    REC -->|Yes| RPR[注入恢复提示: 请继续输出]
    REC -->|No| TERM1[终止]

    Classify -->|media_size| TERM2[终止]
    Classify -->|model_error| TERM3[终止]
    Classify -->|api_error| TERM4[终止]

    RC --> Retry[continue 重试]
    CD --> Retry
    ESC --> Retry
    RPR --> Retry
```

### 4.4 预处理管线执行顺序

**默认管线（`QueryEngine._create_default_pipeline`）为 4 级：**  
`ToolResultBudget` → `SnipCompact` → `MicroCompact` → `AutoCompact`。

目标完整顺序（含可选的 LLM 摘要折叠）如下；`ContextCollapse` 需注入 `summarizer` 后手动注册：

```mermaid
graph LR
    Input[messages] --> TRB[ToolResultBudget: 截断超长工具结果]
    TRB --> SC[SnipCompact: 滑动窗口丢弃]
    SC --> MC[MicroCompact: 剥离thinking并截断]
    MC --> CC[ContextCollapse: LLM摘要压缩 可选]
    CC --> AC[AutoCompact: 80/95%安全网]
    AC --> Output[压缩后 messages]
```

### 4.5 Provider 路由机制

适配器按 **协议 × 原厂/兼容** 选择（兼容层用于非原厂网关，详见 [ROADMAP.md §2.6](./ROADMAP.md)）：

```mermaid
graph TB
    Request["resolve(model_id, config)"] --> Check{config.adapter 已指定?}

    Check -->|Yes| AdapterMap[适配器映射]
    AdapterMap -->|anthropic| AP[AnthropicProvider 原厂 Messages]
    AdapterMap -->|messages_compatible| MC[MessagesCompatible 规划]
    AdapterMap -->|openai| OCP[OpenAICompatibleProvider Chat兼容]
    AdapterMap -->|responses| RP[ResponsesProvider 原厂占位]
    AdapterMap -->|responses_compatible| RC[ResponsesCompatible 规划]

    Check -->|No| PrefixMatch{model_id 前缀匹配}
    PrefixMatch -->|claude-*| AP2[AnthropicProvider]
    PrefixMatch -->|gpt-* / o1 / o3 / o4| OP[OpenAIProvider]
    PrefixMatch -->|No match| Error[ValueError: 提示可用适配器]
```

> 当前已注册：`anthropic` / `openai` / `responses`（后者调用即失败）。  
> 规划注册：`messages_compatible` / `responses_compatible`。
---

## 5. 关键设计决策

### 5.1 消息存储：单一数据源 vs 三数组

TS 版使用三个独立数组（`mutableMessages`, `messages`, `messagesForQuery`）需要复杂同步。Python 版拆成两层：

| 层 | 组件 | 职责 |
|----|------|------|
| 运行时 | `MessageStore` | 进程内单一数据源；`snapshot_after_boundary()` / `gc_before_boundary()` |
| 持久化 | `TranscriptWriter` | 跨进程可恢复；默认 `LocalFileTranscriptWriter`（NDJSON） |

持久化通过 Protocol 注入，后续可替换为 **SQLite / PostgreSQL / Redis / 对象存储** 等实现，引擎与适配器无需改循环逻辑。扩展规划见 [ROADMAP.md §2.5](./ROADMAP.md)。

### 5.2 中间件链 vs 分散钩子

TS 版在多个位置分散调用 hook。Python 版用 `MiddlewareChain` 统一 6 个生命周期点，支持否决（`on_transition` 返回 `None` 终止循环），异常不中断链。

### 5.3 恢复链 vs 内联错误处理

TS 版的错误恢复逻辑散落在循环体内。Python 版 `RecoveryChain` 集中决策，使用 `match/case` 穷举匹配，恢复策略按优先级递进。

### 5.4 AsyncGenerator vs 回调

引擎使用 `AsyncGenerator` 而非回调或事件总线，使数据流方向明确、生命周期可控、取消机制自然（Generator 关闭即取消）。

---

## 6. 依赖注入全景

```
QueryEngine.__init__(
    config: EngineConfig,                    # 必填：不可变配置
    provider_registry: ProviderRegistry,     # 可选：LLM Provider 注册表
    tool_registry: ToolRegistry,             # 可选：工具注册表
    pipeline: PreprocessPipeline,            # 可选：预处理管线
    middleware_chain: MiddlewareChain,        # 可选：中间件链
    stop_hook_executor: StopHookExecutor,    # 可选：Stop hook 执行器
    post_sampling_chain: PostSamplingChain,  # 可选：采样后钩子链
    recovery_chain: RecoveryChain,           # 可选：恢复链
    transcript_writer: TranscriptWriter,     # 可选：会话持久化
    permission_checker: PermissionChecker,   # 可选：权限检查器（CLI/Server注入）
)
```

所有子系统"可注入或自动创建"——不传则创建默认实例。这使得测试时可以 mock 任意子系统，生产环境中 CLI 和 Server 只需注入各自的 `PermissionChecker`。

---

## 7. 相关文档

| 文档 | 内容 |
|------|------|
| [ROADMAP.md](./ROADMAP.md) | 未实现能力、打磨项、分阶段规划 |
| [ROADMAP.md §5](./ROADMAP.md) | **完整包树、各包职责、依赖方向、迁移对照** |
| [COMMIT.md](./COMMIT.md) | Git 提交规范（Conventional Commits） |
| [README.md](../README.md) | 安装、快速开始、项目定位 |
