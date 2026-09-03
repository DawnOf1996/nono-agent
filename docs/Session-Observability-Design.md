# Nono Agent —— 会话存储与可观测性功能设计与优化文档

> **文档角色：** 为 `nono_agent/session/` 与 `nono_agent/observability/` 定义**强观测、可回溯的会话存储**设计方案，并记录已落地范围。
> **配套文档：** 本文与《记忆系统设计》同组；记忆检索/反思的「使用追溯」以本文事件流为落点。事件的写出时机、顺序与 `data` 示例见 [Session-Event-Catalog.md](./Session-Event-Catalog.md)。
> **对标参考：** deepseek-harness 会话日志设计（`learn_docs/02-session-log.md` 及 `packages/core/session` 事件溯源实现）。
> **当前版本基准：** `0.1.0`；P0/P1/P2 已落地 —— `session/` 为事件溯源（落盘只有 `SessionEvent`，默认 LocalFile JSONL，另有 SQLite `events`/`session_header`），§4.1 事件目录全量实装，请求前一致性门禁默认 `strict`。剩余项见 §11。

---

## 1. 结论先行（TL;DR）

1. **把「会话日志」从「消息的持久化副本」升级为「模型上下文的唯一真源」**：采用**事件溯源（append-only 事件流）**，所有「模型看得见的东西」都先落日志，再从日志**确定性推导**出消息数组。
2. **核心信条（对标 dsh）**：`模型可见 ⟺ 日志化`。每次 LLM 请求前校验「事件流重建 == 运行时会话状态」，不一致就报 `log-reconstruction desync` 并终止本轮 —— 从机制上消灭「运行时偷偷改了一段上下文没记录」的隐性 bug。
3. **事件流一次落盘，白送五能力**：可恢复（resume）、可分叉（fork）、可传输（wire）、可调试（debug）、可回放（replay）。
4. **可观测性 = 事件流 + TokenTracker + Metrics 三者同源关联**，用 `session_id / step / seq` 统一主键，做到「任何决策都能回放到『模型当时看到了什么、花了多少 token、调了什么工具、命中了哪条记忆』」。
5. **保持 Protocol 可插拔**：`TranscriptWriter` 协议不动，演进为「事件写 + 消息推导」两层；NDJSON/SQLite 两后端都对齐同一事件信封。

---

## 2. 现状盘点（代码事实）

| 现有资产 | 位置 | 能力 | 剩余缺口 |
|---------|------|------|---------|
| `MessageStore` | `session/store.py` | 进程内当前上下文窗口；`snapshot_for_request()` 交给管线的是**深拷贝** | 按设计只存「当前窗口」；历史由事件流承载 |
| `TranscriptWriter` 协议 | `session/transcript.py` | `append_event/read_events/read_events_from/write_header/read_header/flush` | 多进程并发写同一事件流未定义 |
| `LocalFileTranscriptWriter` | 同上 | `<sid>.jsonl` 每行一个事件 + `<sid>.header.json`；0600 权限；重启后 seq 续接；残行跳过；旧格式拒读 | 无 zstd 压缩 |
| `SQLiteTranscriptWriter` | `session/db_transcript.py` | `events(seq, type, data, timestamp, ignorable)` + `type` 索引 + `session_header` 表 | 无按步骤/工具的聚合查询封装 |
| `derive_messages()` | `session/derive.py` | 事件流 → 消息数组纯函数；`compact/boundary` 整体重置为 `kept` 窗口；连续 `tool/result` 并入一条消息；未知必读事件拒重建 | — |
| 一致性门禁 | `session/derive.py` + `core/engine.py` | 每次 LLM 请求前 `verify_reconstruction`，失败定位到第一条差异；`session_invariant` 可选 strict/warn/off | — |
| `request/header` | `session/request_header.py` | `canonical_header` + `initial/resume/change` 判定，仅在真变化时写 | — |
| 版本门 | `session/header.py` | `SESSION_FORMAT_VERSION` 写入 SessionHeader，不符拒读 | 无跨版本迁移器 |
| `fork_transcript` | `session/fork.py` | 按 `seq` 剪一刀复制，`parent_session` 记谱系 | — |
| 脱敏投影 | `session/redact.py` | 读取/导出侧脱敏（密钥、token、JWT、私钥块、敏感键名） | 启发式匹配，非完备密钥检测 |
| 回放 | `session/replay.py` | 不调 LLM 重建 SDK 消息序列 / 纯文本记录 | 无独立 CLI 子命令 |
| `TokenTracker` | `observability/token_tracker.py` | 按模型累计 token/耗时；`usage` 事件已按步落日志 | — |
| `MetricsRecorder` | `observability/metrics.py` | counter/histogram/gauge + 标签约定 + `record_event_metric` 事件投影 | 内存实现无持久化（可换 OTel/Prometheus） |
| `structlog` | `observability/logging.py` | 结构化日志、JSON/彩色输出 | 与事件流**无关联**，无法从日志反查会话 |

**结论：** 落盘内容只有事件；§4.1 的 17 个事件类型全部有真实发射路径；「可证明一致」的强门禁已成立 —— 每次 LLM 请求前校验事件流能重建出运行时状态，任何绕过事件流的状态变更都会被拦下（见 §6）。剩余缺口是存储效率（zstd）、多进程并发写、structlog 与事件流的关联键。

---

## 3. 设计哲学：日志先行（事件溯源）

```
       传统（现状）：运行时维护消息数组 → 跑完一个 step 把消息追加进 transcript
       事件溯源（目标）：任何会被模型看到的内容，先写成事件 → 再从事件确定性 derive 出消息
```

- **日志是事实真源**：LLM 流、工具执行都从日志派生；日志的完整性由不变式（invariant）强制。
- **append-only**：事件只追加不改写，历史不可变；「修正」用 `tombstone` 或新事件表达（与现有 `TombstoneMessage` 语义一致）。
- **确定性 derive**：`derive_messages()` 是纯函数，同样的日志在任何时间/进程/平台产出**字节级一致**的消息数组。

---

## 4. 事件模型（SessionEvent 信封）

每个事件同一信封：

```
SessionEvent {
  seq: int                 # 自增序号，定位用
  type: str                # 事件类型（见下表）
  data: dict               # 事件载荷
  timestamp: float         # 毫秒时间戳
  ignorable: bool          # 老版本不认识时是否允许跳过
}
```

### 4.1 事件类型目录（对齐 dsh，映射到 nono-agent Message 类型）

全部 17 个类型都已实装（`KNOWN_EVENT_TYPES` 是权威清单，`tests/test_event_coverage.py` 逐类型断言）。写出时机、顺序与载荷示例见 [Session-Event-Catalog.md](./Session-Event-Catalog.md)。「发射者」一栏说明谁有资格写这条事件 —— 引擎能从循环产出的消息流观察到的由引擎写，循环内部才知道的（step 边界、`store.replace()`、重试次数）由循环通过 `SessionEventSink` 上报，工具/记忆内部发生的由各自的观察者回调上报。

| 事件类型 | 载荷 | 发射者 | 来源（nono-agent 现有概念） |
|---------|------|--------|---------------------------|
| `turn/start` / `turn/end` | `{turn}` / `{turn, reason}` | 引擎 | 一次 `submit_message` 的边界；`reason` 对齐 `SDKResultMessage.subtype` |
| `step/start` / `step/end` | `{turn, step, model}` / `{turn, step, reason}` | 循环 | 一次 LLM 请求 + 其引起的工具执行；成对出现，`reason` 为 `completed`/`next_turn`/`aborted_tools`/`api_error:*` 等 |
| `request/header` | `{header, reason: initial\|resume\|change}` | 引擎 | 请求配置快照（见 §5）；`config` 含 provider/protocol/model/context_size |
| `user/input` | `{content, attachments?}` | 引擎 | 用户原始输入（原话）；`turn/start` 之后、`step/start` 之前；不参与 derive |
| `user/message` | `{content}` | 引擎 | `UserMessage`（组装好的最终用户信息 / stop hook 校验错误 / 截断恢复提示）；`step/start` 之后、`request/header` 之前 |
| `assistant/chunk` | `{chunk}` | 引擎 | `StreamEvent` 增量；`log_assistant_chunks=False` 为降级模式 |
| `assistant/message` | `{content}` | 引擎 | `AssistantMessage`（已含 tool_use blocks） |
| `tool/call` | `{call_id, name, args}` | 引擎 | `ToolUseBlock` |
| `tool/result` | `{call_id, result, is_error}` | 引擎 | `ToolResult`；连续多条属于同一条 user 消息（并行工具调用） |
| `compact/boundary` | `{content, metadata: {strategy, ...}, kept}` | 引擎 + 循环 | 任何 `store.replace()`：手动 `/compact`、`/clear`、管线自动压缩、`prompt_too_long` 恢复 |
| `api/retry` | `{attempt, max_retries, delay, reason, ...}` | 循环 | fallback 切换与 `RecoveryChain` 重试 |
| `permission/denial` | `{tool, rule, mode, path}` | `DenialTracker` 观察者 | `DenialTracker` → `SDKResult.permission_denials` |
| `memory/write` / `memory/retrieve` | `{memory_id, kind, adopted, importance}` / `{query, memory_ids, kinds, adopted}` | `MemoryManager` 观察者 | 记忆系统（见《记忆系统设计》§5/§12.5） |
| `usage` | `{usage, model}` | 引擎 | 每步 `TokenTracker.record_usage`（见 §7） |

> **`kept` 的语义**：`compact/boundary` 携带**压缩后的完整窗口**（含边界标记本身），`derive_messages()` 遇到它就把消息数组整体替换为 `kept`。这样「压缩」不再是需要 derive 侧猜测的特例，任何策略（手动 / 管线 / 恢复 / 清空）都用同一条事件精确表达。

> **`ignorable`**：新增事件类型一律标 `ignorable=true`，保证旧版本读新日志不崩（前向兼容）。加载时遇到不可识别的非 ignorable 事件则报错（版本门）。

---

## 5. `request/header` 快照（「模型当时被告诉了什么」）

这是「可回溯性」最关键的增量。每个 step 发给 LLM 的**请求配置**要能被重放：

```
header = {
  config: {
    provider,        # 模型提供商信息（解析出的 Provider 协议标识，如 anthropic_messages / openai_chat_completions）
    protocol,        # 模型协议（显式 adapter 优先，否则回退到 provider 标识）
    model,           # 模型 ID
    context_size,    # 模型上下文窗口大小（token 数）
    agent_id,        # 记忆作用域（可空）
    ...
  },
  system: str,          # 组装好的 system prompt 全文
  tools: list[dict],    # 有序工具 schema 列表（字节级比对）
}
```

**写入时机（只在真变化时写，不是每 step 一条）**：

| reason | 触发条件 |
|--------|---------|
| `initial` | 本引擎实例第一次请求 + 日志中无任何 header |
| `resume` | 本引擎实例第一次请求 + 日志中已有 header（进程重启 / fork 后接续 / 换 LLM 后继续） |
| `change` | 非首次请求 + 新 header 与最新一条在 `config/system/tools` 任一维度字节级不等 |

> 落地为 `canonical_header()` + `header_equals()`（比对 `config`、`system`、`tools[]` 有序），由 `build_request()` 单一入口写。与 `EngineConfig` 的 `frozen` 语义协同：配置不变则 header 不变、只写一条。

---

## 6. 一致性校验（invariant，回溯性之魂）

对标 dsh `invariant.ts`，每次 LLM 请求前（引擎收到 `RequestStartEvent` 时）强制：

1. **状态一致**：`derive_messages(events)` == `messages_to_derived(MessageStore)`（canonical JSON 相等）。
2. **配置一致**：请求的 `model/system/tools` == 由事件流 `fold_request_header()` 推出的 header（`next_reason()` 只在真变化时补写 header，等价于持续比对）。

不一致 → `log-reconstruction desync` → 本轮以 `error_log_reconstruction_desync` 终止，报错定位到第一条不同的消息。**没有「偷偷改上下文」的存活空间。**

**为什么比的是会话状态，而不是发给模型的数组。** 预处理管线会为单次请求截断工具结果、剥离 thinking，请求数组本就不等于会话状态。关键在于管线拿到的是 `snapshot_for_request()` 的**深拷贝**：它无法改写已记录的历史，于是

```
请求数组 = 管线(会话状态)，管线是确定性纯函数
会话状态 = derive_messages(事件流)   ← 门禁校验这一条
```

两式相乘即得「整次请求可从事件流复现」。反过来若管线能就地改写 store（改造前的行为），截断就会永久且无声地重写历史，任何门禁都无从谈起。

`derive_messages()` 确定性规则（`session/derive.py`）：
1. 按 `seq` 升序逐事件折叠：`user/message` → user，`assistant/message` → assistant，`tool/result` → user `tool_result` 消息（**连续多条并入同一条**，对齐 API 对并行工具结果的要求）；
2. `compact/boundary` 丢弃此前全部消息，整体重置为事件携带的 `kept` 窗口；
3. 未识别事件：`ignorable=true` 跳过，否则抛 `ReconstructionError`（宁可拒重建，也不悄悄丢上下文）；
4. `turn/step/user-input/request-header/chunk/tool-call/usage/api-retry/permission/memory` 等事件不参与折叠 —— `user/input` 只保留原始输入供审计，模型上下文的重建只看 `user/message`。

### 6.1 门禁成立所依赖的改造

| 依赖 | 说明 |
|------|------|
| 管线深拷贝 | `snapshot_for_request()` 返回深拷贝，stage 无法就地改写已记录历史 |
| 状态变更全事件化 | 循环内每次 `store.replace()`（管线压缩、`reactive_compact`、`collapse_drain`）都通过 `SessionEventSink` 写 `compact/boundary`；`/clear` 写 `kept=[]` |
| 合成消息全部 yield | 循环注入的 stop-hook 校验消息、截断恢复提示、中止占位工具结果都产出给引擎，从而进事件流 |
| 指向已有事件流即接续 | `initialize()` 在 store 为空且事件流非空时用 derive 重建状态 —— 否则会一边追加事件、一边用空上下文请求模型 |

### 6.2 `session_invariant` 配置

| 取值 | 行为 | 适用 |
|------|------|------|
| `strict`（默认） | 不一致则终止本轮 | 开发与 CI；把偏差在第一次出现时暴露 |
| `warn` | 记日志 + `session.desync` 指标，继续执行 | 生产灰度期，先观测再收紧 |
| `off` | 不校验 | 超长会话；省掉每次请求的一次全量事件读取 |

---

## 7. 可观测性三件套同源关联

以 `session_id + turn + step + seq` 为统一关联键，把三个现有组件从「孤立」变成「同源」：

| 组件 | 现状 | 演进 |
|------|------|------|
| **TokenTracker** | 会话累计，终点汇总 | ✅ 每步 `usage` 事件；`SDKResultMessage.usage` 仍为汇总，但可下钻到 step |
| **MetricsRecorder** | 全局内存 counter/histogram/gauge | ✅ 标签常量 `TAG_SESSION_ID / TAG_AGENT_ID / TAG_MODEL / TAG_TOOL / TAG_KIND / TAG_REASON / TAG_EVENT`；`record_event_metric()` 把事件流投影为指标 |
| **structlog** | 独立结构化日志 | 待做：统一 `bind(session_id=..., step=..., seq=...)`，使日志、事件流、指标三者可互查 |

### 7.0 事件流 → 指标的投影

事件流是唯一事实来源，指标是它的聚合投影：同一次运行既能按 `seq` 逐条回看，也能按标签聚合成曲线。`_stage_event()` 是单一入口，因此所有事件（含循环与观察者上报的）都会自动计入指标。

| 事件类型 | 指标 | 额外标签 |
|---------|------|---------|
| 全部 | `session.events` | `event`（事件类型） |
| `turn/end` | `session.turns` | `reason` |
| `step/end` | `session.steps` | `reason` |
| `tool/call` | `tool.executions` | `tool` |
| `api/retry` | `api.errors` | `reason` |
| `compact/boundary` | `compact.operations` | — |
| `permission/denial` | `permission.denials` | `tool` |
| `memory/write` | `memory.write` | `kind` |
| `memory/retrieve` | `memory.retrieve` | — |
| 门禁失败 | `session.desync` | — |

所有指标都带 `session_id`；载荷里缺失的标签取 `unknown`，保证标签集稳定，聚合后端不会因维度漂移而分裂时间序列。

### 7.1 强观测/可回溯的最小验收（KPI）

| 可回答的问题 | 依赖 |
|-------------|------|
| 「5 秒前这个 NPC 在干嘛」 | 打印事件流末尾 seq |
| 「它为什么这样回答」 | `request/header.system` + 输入 + 完整 assistant/message |
| 「这一步花了多少 token、哪个模型」 | 每步 `usage` 事件 |
| 「它命中了哪条记忆、是否采用」 | `memory/retrieve` 事件（`adopted` 字段） |
| 「把故障重现包发给同事」 | `redact_events()` 投影后导出事件流 |
| 「不调 LLM 重放 UI」 | `replay_sdk_messages(events, include_chunks=True)` |
| 「实时看它现在看到了什么」 | `GET /session/{id}/events` SSE 通道 |

---

## 8. 持久化与版本门（对齐 dsh 的 JSONL + SQLite）

`TranscriptWriter` 仍是 Protocol，语义为「**只 append 事件**」；消息由 `derive_messages()` 推导，不落盘：

| 后端 | 布局 | 说明 |
|------|------|------|
| `LocalFileTranscriptWriter`（默认） | `$SESSIONS/<sid>.jsonl`，每行一个 `SessionEvent` | append-only；每次追加开闭文件，进程被杀不丢已写事件；重启后从既有最大 `seq` 续接 |
| `SQLiteTranscriptWriter` | `events(seq, type, data, timestamp, ignorable)` + `type` 索引 + `session_header` | 结构化列可索引查询，支撑回溯面板 |
| `SessionHeader` | LocalFile 存 `<sid>.header.json`；SQLite 存 `session_header` 表 | `{session_id, format_version, created_at, cwd, parent_session, agent_preset_id}` |

- **协议没有全量覆写接口**：覆写会与追加交错并破坏事件流，「修正」用新事件表达（`compact/boundary`、tombstone）。
- **版本门**：`SESSION_FORMAT_VERSION` 写入 SessionHeader；版本不符**直接拒读**。事件格式之前的消息副本 transcript（JSONL 行缺事件信封字段 / SQLite 存在 `messages` 表）同样拒读，不静默当作空会话。
- **残行自愈**：JSONL 中非法 JSON 行（写入中断残留）跳过；合法 JSON 但不是事件信封则拒读。
- **写入策略**：事件先同步入队（使 `compact()`、`clear_session()` 这类同步 API 与循环的同步 sink 都能记事件），在最近的 await 点按序落盘。顺序为 `turn/start` → `user/input` →（组 prompt 时的 `memory/retrieve`）→（管线压缩时的 `compact/boundary`）→ `step/start` → `user/message` → `request/header`；`user/message` 与 `request/header` 在首个 `RequestStartEvent` 时（即 LLM 调用前）`await` 落盘以保证 resume。`strict` 门禁在每次请求前也会先排空队列。zstd 压缩与 write-behind 批量见 §11。
- **文件权限**：会话文件 `0600`、会话目录 `0700`。事件流逐字保留提示词、工具输出与请求配置，落盘时就要挡住同机其它用户；不支持 POSIX 权限位的平台上 `chmod` 是无操作。

---

## 9. 白送的五大能力（并落到 nono-agent 现有 API）

| 能力 | 语义 | 落点 |
|------|------|------|
| **可恢复 Resume** | 重放日志重建 `MessageStore` → `derive_messages()` 接续 | `resume_session(session_id)` 已走「读事件流 + derive」 |
| **可分叉 Fork** | 在某个 `seq` 剪一刀复制成新会话，自动继承历史 | `engine.fork_session(boundary_seq, new_session_id)` + `session/fork.py`；`SessionHeader.parent_session` 记录谱系 |
| **可传输 Wire** | JSONL 序列化即传 | 事件流本身就是 JSONL；`ignorable` + 版本门保证跨版本；`redact_events()` 做外发投影 |
| **可回放 Replay** | 不调 LLM 重建输出流 | `replay_sdk_messages(events)` → `SDKMessage` 序列（`include_chunks` 控制是否逐 token 重放）；`replay_transcript_text()` 出纯文本 |
| **可调试 Debug** | UI/遥测实时看「模型看到了什么」 | `GET /session/{id}/events`（SSE，脱敏后推送，`after_seq` 断点续传，`follow` 持续跟随） |

---

## 10. 与 Smallville 的映射（验收口径）

| 报告要求 | 本设计落点 |
|---------|-----------|
| 决策过程可回放 | 事件流 = 结构化动作序列；`derive_messages()` + `request/header` 可精确重放任意 step |
| 成本可归因 | 每步 `usage` 事件 + `model/agent_id` 维度 → 按 NPC/模型/步骤归因 |
| 工具调用成功率/越界率 | `tool/call`/`tool/result` 事件 + `permission/denial` 事件 |
| 记忆检索命中率/角色一致性 | `memory/retrieve` 事件 + `adopted` 字段（与《记忆系统设计》闭环） |
| 点击居民看「它大脑在干嘛」 | 复用 `assistant/chunk` 事件流（SSE），UI 实时渲染 |
| 世界状态变更可回放 | 动作是 `json_schema` 结构化输出，作为 `tool/call`（小镇工具）进事件流 |

---

## 11. 分阶段落地

| 阶段 | 内容 | 状态 |
|------|------|------|
| **P0** | 事件信封 + `SessionEvent` 类型目录 + `turn/request-header/user/assistant/tool/usage` 事件；`derive_messages()`；两个后端都改事件流（落盘只有事件，默认 LocalFile）；`resume` 走事件流 | ✅ 已落地 |
| **P1** | `request/header` 的 initial/resume/change + `header_equals`；`SessionHeader` + 版本门 + 旧格式拒读；每步 `usage` 事件；`fork_session` | ✅ 已落地 |
| **P2** | 状态变更全事件化（管线压缩 / 恢复 / `/clear`）+ 管线深拷贝 → 请求前 invariant 强门禁；`step/*`、`assistant/chunk`、`api/retry`、`permission/denial`、`memory/*` 事件；redact 层 + 会话文件权限；Metrics 标签约定 + 事件投影；Server `session/event` SSE 通道；replay | ✅ 已落地 |
| **P3** | zstd 压缩 + write-behind 批量；增量 derive（缓存上次结果，只折叠新事件）；structlog 绑定 `session_id/step/seq`；SQLite 按步骤/工具的聚合查询；回溯面板 UI；replay CLI 子命令；多进程并发写语义；跨版本迁移器 | 待做 |

---

## 12. 多维合理性校验

### 12.1 技术可行性 —— ✅ 高
- 现有 `Message`/`SDKMessage` 已带 `uuid/timestamp`，`TombstoneMessage`、`compact_boundary`、`DenialTracker` 概念齐备，事件映射是「加信封 + 加类型」，不推翻引擎。
- `resume_session` 已有「读 writer → validate → replace store」路径，改为「读事件流 → derive」是替换而非重写。

### 12.2 性能 —— ✅ 可控
- append-only 写入与「追加一条消息」同复杂度；每轮多写 `turn/*`、`request/header`、`usage` 等少量元事件。
- 事件量增长由 zstd + write-behind + 可选归档控制；SQLite 结构化列索引支撑回溯查询。
- invariant 的 `derive_messages` 是纯函数，可在「请求前」增量校验（缓存上次 derive 结果，仅对新事件做增量折叠），避免每 step 全量重算。

### 12.3 正确性 —— ✅ 强（本设计的核心价值）
- `模型可见 ⟺ 日志化` + invariant 从机制上杜绝「上下文与日志不一致」；`request/header` 快照保证「配置可重放」。

### 12.4 兼容/演进 —— ✅ 强
- `ignorable` 前向兼容；版本门做「地基优先」硬闸；Protocol 不变，后端（JSONL/SQLite/未来 Postgres/Redis）可插拔。
- 与现有 `TranscriptWriter` 调用方（`engine`、`resume_session`、`/sessions`）保持接口形状，仅内部语义升级。

### 12.5 成本/收益权衡 —— ✅ 正收益
- 代价：每步多写 `turn/step/request-header` 等少量元事件 + invariant 校验开销。
- 收益：resume/fork/replay/debug 从「要专门写」变为「白送」，且消除最贵的隐性 bug（上下文漂移、不可复现）。

### 12.6 安全/隐私 —— ✅ 已处理（读取侧脱敏 + 权限隔离）
- **脱敏在读取/导出侧，不在落盘侧**：落盘就脱敏会让事件流不再等于「模型真的看到了什么」，门禁与回放随之失真，且脱敏是启发式的、不可逆 —— 一旦误伤就永久丢证据。因此磁盘上逐字保留，`redact_events()` 只作用于外发副本（SSE 通道、导出）。
- **落盘侧靠权限隔离**：会话文件 `0600` / 目录 `0700`（§8）。
- `redact_event()` 保留信封（`seq/type/timestamp`）只改 `data`，因此脱敏后的流仍可按 `seq` 对齐与断点续传。
- 覆盖 `sk-`/`ghp_` 类前缀密钥、AWS access key、JWT、`Bearer` 头、PEM 私钥块、`password=`/`token:` 赋值式，以及 `api_key`/`secret`/`credential` 等敏感键名（整值替换）。启发式匹配，不承诺完备。
- `permission/denial` 事件本身是审计资产，完整保留。

### 12.7 与 deepseek-harness 对照自检
| dsh 要素 | 本项目落地 |
|----------|-----------|
| 追加-only 事件流 + 信封 | §4（`seq/type/data/timestamp/ignorable`） |
| `deriveMessages()` 确定性 | §6 `derive_messages()` |
| invariant `log-reconstruction desync` | §6 |
| `request/header` initial/resume/change + 5 维比对 | §5（config/system/tools 三维精简，适配 Python 配置） |
| `SessionHeader` + `SESSION_FORMAT_VERSION` | §8 |
| JSONL(zstd) + SQLite 双后端 | §8 |
| 白送五能力 | §9 |

> 差异说明：dsh 是 TS 微内核 + declaration merging 扩事件；nono-agent 用 Python `Literal` 判别 + Protocol 注入（与 `core/types.py` 现有 `Literal` 风格一致）。「比对五维」在 Python 侧简化为「config/system/tools」三维（`adapterDefaults` 并入 `config`），语义等价。

### 12.8 风险与预案
| 风险 | 影响 | 预案 |
|------|------|------|
| 全量覆写与 append 交错 | 事件流损坏/丢事件 | 已消除：协议只有 `append_event`，无覆写接口；「修正」用 `compact/boundary`、tombstone 等新事件表达 |
| invariant 误报阻断 | 正常流程 fail | 校验只读不写；`session_invariant=warn/off` 可降级（§6.2）；`ignorable` 事件不参与严格比对 |
| 门禁的读取开销 | 每次请求全量读事件流 | 长会话用 `warn`/`off`；增量 derive 见 §11 P3 |
| 事件量/存储膨胀 | 磁盘/查询压力 | `log_assistant_chunks=False` 降级为「仅保留合并 message」；zstd + write-behind + 归档见 §11 P3 |
| 敏感数据落盘 | 泄漏 | 文件权限 `0600` + 外发侧 redact（§12.6）；Server 默认 deny-write |
| 跨版本日志不兼容 | 拒读 | 版本门 + `ignorable`；升级时提供一次性迁移器（对标 dsh migrate scripts） |

---

## 附：关键改动点速查

| 文件 | 改动方向 | 状态 |
|------|---------|------|
| `nono_agent/session/events.py` | `SessionEvent` 信封 + 类型目录 + `KNOWN_EVENT_TYPES` + `SessionEventSink` 协议 | ✅ |
| `nono_agent/session/header.py` | `SessionHeader` + `make_header` + 版本门 | ✅ |
| `nono_agent/session/derive.py` | `derive_messages()` + `fold_request_header()` + `verify_reconstruction()` + 消息互转 | ✅ |
| `nono_agent/session/transcript.py` | `TranscriptWriter` 改事件协议；`LocalFile` 事件 JSONL + header 侧车 + 旧格式拒读 + `0600` 权限 | ✅ |
| `nono_agent/session/store.py` | `snapshot_for_request()` 返回深拷贝，管线无法改写已记录历史 | ✅ |
| `nono_agent/session/redact.py` | `redact_text/redact_value/redact_event/redact_events` 读取侧脱敏 | ✅ |
| `nono_agent/session/replay.py` | `replay_sdk_messages()` + `replay_transcript_text()` | ✅ |
| `nono_agent/session/db_transcript.py` | `messages` 表 → `events` + `session_header` 表 | ✅ |
| `nono_agent/session/request_header.py` | `canonical_header()` + `header_equals()` + `next_reason()` | ✅ |
| `nono_agent/session/fork.py` | `fork_transcript()` 按 seq 分叉 | ✅ |
| `nono_agent/core/engine.py` | 只 append 事件；`turn/start` → `user/input` →（`memory/retrieve`）→ `step/start` → `user/message` → `request/header` 的顺序写入；请求前跑门禁；观察者接线；空 store 时从事件流重建；`fork_session` | ✅ |
| `nono_agent/core/loop.py` | 通过 `SessionEventSink` 上报 `step/*`、`api/retry`、`store.replace()`；合成消息全部 yield；管线压缩后、中间件前追加本轮用户消息 | ✅ |
| `nono_agent/permissions/denial.py` | `DenialTracker.add_observer()` → `permission/denial` | ✅ |
| `nono_agent/memory/manager.py` | 写入/检索观察者 → `memory/write`、`memory/retrieve` | ✅ |
| `nono_agent/observability/metrics.py` | 标签常量 + `record_event_metric()` 事件投影 | ✅ |
| `nono_agent/adapters/server.py` | `GET /session/{id}/events` SSE 通道（脱敏 + `after_seq` + `follow`） | ✅ |
| `nono_agent/core/config.py` | `session_invariant`、`log_assistant_chunks` | ✅ |
