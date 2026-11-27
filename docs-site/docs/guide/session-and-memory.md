---
title: 会话与记忆
---

# 会话与记忆

## 1. 会话日志是唯一真源

Nono Agent 的会话存储是**事件溯源**：落盘的只有 `SessionEvent`，消息数组由
`derive_messages()` 从事件流**确定性推导**。

```text
传统：    运行时维护消息数组 → 跑完一步把消息追加进 transcript
事件溯源：任何会被模型看到的内容先写成事件 → 再从事件推导消息
```

核心信条：**模型可见 ⟺ 日志化**。每次 LLM 请求前校验「事件流重建 == 运行时状态」，
不一致就报 `log_reconstruction_desync` 并终止本轮。绕过事件流改上下文没有存活空间。

### 20 类事件

| 分类 | 事件 |
|---|---|
| 回合 | `turn/start`、`turn/end` |
| 步骤 | `step/start`、`step/end` |
| 请求 | `request/header` |
| 输入 | `user/input`、`user/message` |
| 输出 | `assistant/chunk`、`assistant/message` |
| 工具 | `tool/call`、`tool/result`、`ui/tool-result` |
| 压缩 | `compact/boundary` |
| 异常 | `api/retry` |
| 权限 | `permission/denial`、`permission/onchange`、`permission/changed` |
| 记忆 | `memory/write`、`memory/retrieve` |
| 计量 | `usage` |

完整时机、顺序与载荷示例见 [会话事件目录](../architecture/session-event-catalog.md)。

### 落盘布局

| 后端 | 布局 | 说明 |
|---|---|---|
| LocalFile（默认） | `~/.nono-agent/sessions/<sid>.jsonl.zst` + `<sid>.header.json` | zstd 独立帧可追加；既有明文 `.jsonl` 在首次追加时自动迁到 zst |
| SQLite | `events(seq, type, data, timestamp, ignorable)` + `session_header` | 有 `type` 索引 |

文件按 `0600`、目录按 `0700`。`session_invariant` 门禁默认 `strict`，可设 `warn` / `off`。

### 查会话

```bash
# 列出本地会话（session id + 事件数 + 摘要 + cwd）
uv run nono sessions

# 恢复某个会话（以该会话的 .header.json 为约束，不用再传 --cwd）
uv run nono chat --resume <session_id>
```

!!! note "zstdcat / jq 是外部工具"
    下面这类命令需要自备 `zstd` 与 `jq`，仓库不提供。Windows 上更省事的是用仓库自带的
    `session-viewer`，或直接用 Python 读：

```python
from pathlib import Path
from nono_agent.session import LocalFileTranscriptWriter, redact_events, replay_sdk_messages

events = await LocalFileTranscriptWriter(
    Path.home() / ".nono-agent" / "sessions" / "s1.jsonl"
).read_events()
```

!!! warning "`~` 不会被展开"
    `LocalFileTranscriptWriter` 的路径参数直接 `Path(session_file)`，**没有** `expanduser()`。
    传字符串 `"~/..."` 会创建一个字面量 `~` 目录。用 `Path.home()` 自己拼。

### 脱敏与回放

```python
safe = redact_events(events)                      # 外发前脱敏，信封 seq 不变
for msg in replay_sdk_messages(events):          # 不调模型重建 SDK 消息序列
    print(msg.type)
```

**脱敏在读取/导出侧，不在落盘侧。** 落盘就脱敏会让事件流不再等于「模型真的看到了什么」，
门禁与回放随之失真，而且脱敏是启发式、不可逆的。磁盘上逐字保留，只对外发副本做投影。

### 分叉

```python
from nono_agent.session import fork_transcript

# 底层入口：要自己提供 source / target 两个 writer，且是 async
await fork_transcript(source_writer, target_writer, boundary_seq=120, new_session_id="sid-2")
```

按 `seq` 剪一刀复制，`parent_session` 记谱系。不想手搓 writer 就用引擎方法
`await engine.fork_session(boundary_seq, new_session_id)`（`core/engine.py:1207`）。

产品层的会话标题另有一层编号：叉分会话写成「序号 - 家族名」（`1 - 原会话`、`2 - 原会话`），
原会话永远不带序号，靠它一眼分出原文和叉分。家族归属记在 `sessions.fork_root`（指向最初的会话），
所以套娃叉分接着排号而不是叠前缀；侧边对话不进列表、不占号，被提升成顶层会话时才拿到家族里的号。

---

## 2. 记忆系统

跨会话记忆落在 `~/.nono-agent/memory/`，每条记录一个 JSON（或 SQLite 单表 JSON blob）。

### 记录结构

`MemoryRecord` 带身份（`ensure_identity()`）、类型（`kind`，默认 `semantic`）与生命周期
（`is_active()`）。`kind` 的合法取值是 `working` / `episodic` / `semantic` / `procedural` /
`meta` / `persona`；`reflect()` 产物的 `kind` 是 `meta`，`reflection` 只是它的 `source` 标记，
**不是**一个 kind。

### 编排

`MemoryManager` 负责四件事：

| 环节 | 作用 |
|---|---|
| `admit()` | 准入。带门槛，且**不经过候选池**直接判定 |
| `retrieve()` | 检索。组 system prompt 时每轮调一次 |
| `reflect()` | 反思。生成高重要性（`importance=8.0`）、`keys=["reflection"]`、`source="reflection"` 的记录 |
| 观察者 | `add_write_observer()` / `add_retrieve_observer()` → 事件流投影出 `memory/write` / `memory/retrieve` |

### 排序

检索排序综合三个因子：时间衰减（λ=86400 秒）、重要度、匹配度，权重 α/β/γ = 0.3/0.4/0.3。
token 预算由 `memory_retrieval_k`（默认 10）与 `memory_retrieval_token_budget`（默认 2000）控制
（`MemoryManager.retrieve(k=..., token_budget=...)` 是同一组参数的运行时入口），
但**首条记忆不受预算卡死** —— 检索不到东西等于没检索。

### 观测

记忆系统的使用可追溯：`memory/retrieve` 事件带 `adopted` 字段，能回答「它命中了哪条记忆、
是否采用」，是 [会话可观测性](../architecture/session-observability.md) 的落点之一。

---

## 3. 观测与统计

| 组件 | 位置 | 能力 |
|---|---|---|
| `TokenTracker` | `observability/token_tracker.py` | 按模型累计 token 与耗时；`get_step_usages()` 按步查 |
| `MetricsRecorder` | `observability/metrics.py` | counter / histogram / gauge + 标签约定 + 事件流投影 |
| `StatsAccumulator` | `session/stats.py` | 由事件流 O(1) 推导 TTFT / TPS / token / 消息数 |
| `structlog` | `observability/logging.py` | 结构化日志（**与事件流无关联键**，无法从日志反查会话 —— 已知缺口） |

引擎侧读快照用 `engine.session_stats()`，订阅每步推送用 `engine.add_stats_listener()`。

指标名统一带 `nono_agent.` 前缀（`nono_agent.session.events`、`nono_agent.tool.executions` …），
所有指标都带 `session_id`；载荷里缺失的标签取 `unknown`，保证标签集稳定。

Server 侧可以实时订阅会话事件流：

```bash
curl -N http://localhost:8000/session/<session_id>/events
```

支持 `after_seq` 断点续传、`follow=false` 只取快照。推送内容已脱敏。

---

## 相关页面

- [会话事件目录](../architecture/session-event-catalog.md)
- [会话事件溯源设计](../architecture/session-observability.md)
- [记忆系统设计](../architecture/memory-system.md)
- [上下文压缩](../architecture/context-compression.md)
- [工具与权限](tools-and-permissions.md)
