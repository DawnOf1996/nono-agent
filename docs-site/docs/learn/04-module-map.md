---
title: 04 · 模块地图
---

# 模块地图

> 这一页回答"**每个模块的功能点是什么、边界在哪**"。按依赖从内到外排列，
> 每行都能对应到 `nono_agent/` 下一个真实的包。所有数量都是代码实测值。

---

## 1. 一张表看懂全部子系统

| 包 | LOC | 干什么 | 明确**不**干什么 |
|----|-----|--------|-----------------|
| `core/` | 3161 | 编排、主循环、配置快照、状态机、消息类型 | HTTP、具体工具、UI |
| `tools/` | 2811 | 工具协议 / 注册表 / 执行器 + `builtin/` 23 个工具 | 权限策略的实现 |
| `session/` | 2104 | 内存消息 + 事件流多后端 + 推导 + 一致性门禁 + 脱敏/回放 | 跨会话记忆 |
| `providers/` | 1751 | 三类协议适配 + 分层模型目录 | 工具执行、会话存储 |
| `mcp/` | 1453 | MCP 客户端生命周期 + tools/resources 桥接 | Tool 协议定义 |
| `toolplugins/` | 1236 | 声明式工具插件框架 | **不含任何具体厂商插件** |
| `shell/` | 1171 | 本地 Shell 执行器 + JobRegistry | 直接面向模型的那层工具语义 |
| `pipeline/` | 808 | 调用前分级压缩 | 持久化、权限 |
| `permissions/` | 662 | 模式 / 规则 / 拒绝 / 沙箱 / ignore | `tool.execute` 业务 |
| `memory/` | 641 | 跨会话记忆（可治理记录 + 准入/检索/反思） | 单次 transcript |
| `hooks/` | 643 | 引擎内中间件链 + 用户态 `hooks.json` | 用户 hook 的安全策略 |
| `observability/` | 453 | Token / 日志 / 指标 | 业务决策、脱敏 |
| `utils/` | 428 | Abort / token 估算 / SSRF 闸门 / 进程 | 领域策略 |
| `recovery/` | 390 | 错误分类与恢复动作 | UI、传输细节 |
| `agents/` | 370 | 子 Agent 三件套 | Teams / Coordinator |
| `commands/` | 356 | Slash 注册与解析 | 终端/HTTP 渲染 |
| `plugins/` | 309 | 扩展包加载（只组合） | 具体 Tool/Agent 业务代码 |
| `prompt/` | 434 | Section 组装、静态/动态边界、优先级 | 文件发现 |
| `jobs/` | 176 | 作业板（按会话分片） | 进程管理（在 `shell/`） |
| `rules/` | 211 | 常驻指令发现与合并 | 最终 prompt 拼接 |
| `settings/` | 224 | 五层配置合并的单一入口 | 取代 `EngineConfig` 本身 |
| `skills/` | 182 | Skill 加载 + SkillTool | Plugin 清单格式 |
| `checkpoint/` | 70 | 编辑快照回滚 | Git 本身 |

---

## 2. 内核：core/

引擎只有两个主角，其余是支撑。

| 文件 | 内容 |
|------|------|
| `engine.py` | `QueryEngine` —— 对外唯一入口，管 turn 事务 |
| `loop.py` | `agentic_loop` —— while 循环本体 |
| `state.py` | `TransitionReason`(7) / `TerminalReason`(**11**) / `LoopState`(frozen) |
| `config.py` | `EngineConfig` / `QueryConfig` / `ShellConfig`（Pydantic） |
| `types.py` | `InternalMessage` vs `SDKMessage` 两套消息 |
| `input_types.py` | 输入类型定义 |

### 2.1 枚举比实现更宽

`TerminalReason` 有 **11 个**成员，`TransitionReason` 有 **7 个**——
但**枚举定义的是设计词汇表，不是当前每条都走得到**。

实测：11 个终止原因里 **8 个由循环真实产出**。`PROMPT_TOO_LONG` 与 `BLOCKING_LIMIT`
在循环里没有构造点；`IMAGE_ERROR` 只在 `recovery/chain.py` 里被赋值，
而 `RecoveryDecision.terminal_reason` 目前写了没被读。
`TransitionReason.TOKEN_BUDGET_CONTINUATION` 全仓无构造点——任务预算超限走的是
**终止** `TASK_BUDGET_EXHAUSTED`，不是继续。

> 读代码时找不到某个分支不用怀疑自己：它就是还没接上的那条路。

---

## 3. 工具：tools/ + shell/ + jobs/

### 3.1 23 个内置工具（实测）

`create_builtin_tools()` 实跑返回 **23 个**：

| 类别 | 工具 |
|------|------|
| 文件 | `read_file` `write_file` `edit_file` |
| 搜索 | `glob` `grep` `list_directory` `tool_search` |
| 执行 | `bash` `job_output` `job_kill` |
| 交互 | `ask_user_question` |
| 规划 | `todo_write`、`enter_plan_mode`、`exit_plan_mode`、`enter_worktree`、`exit_worktree` |
| 代码 | `lsp` |
| 网络 | `web_fetch` |
| 任务 | `task_create` `task_list` `task_get` `task_update` `task_stop` |

**注意**：`tools/builtin/demo.py` 里还有 `calculator` / `get_time`，但它们**不在默认集合里**。
另外整个 `tools/` 下 grep `_name = ` 会得到 25 个——多出来的两个正是 demo 工具。
文档里说"23 个内置工具"指的是默认启用的那一组。

### 3.2 工具协议的两个默认值

```python
class ToolBase:                       # 注意：基类不继承 Tool Protocol，只提供便利实现
    _is_concurrent_safe: bool = False       # 默认不并发安全 → 循环里串行跑
    _has_write_side_effects: bool = True    # 默认有写副作用 → 权限默认要确认
```

（这两个下划线类属性正是喂给协议里那圈同名 `@property` 的值——
直接实现 `Tool` Protocol 的工具则要自己写 `property`。）

**默认值选保守是有意的**：工具忘了声明自己并发安全，后果只是慢（串行）；
如果默认乐观，后果是数据竞争。这两个布尔直接驱动了
[02 生命周期](02-request-lifecycle.md) §3.1 里的并行/串行决策。

### 3.3 shell/ 的"能力缝"

`shell/` 最值得学的一点：**工具不直接 spawn 进程**。
工具只描述一次执行（构造 `ShellSpec`），由可替换的执行器完成。

```
BashTool  ──构造──→  ShellSpec  ──交给──→  ShellExecutor  ──→  ShellOutcome
```

好处是将来要接沙箱执行器，只需覆写构造 argv 的部分，超时分类、进程组终止、
输出收集、游标语义全部复用。

---

## 4. 状态：session/

| 文件 | 干什么 |
|------|--------|
| `store.py` | `MessageStore`——进程内的**当前上下文窗口**（运行时状态，事件流才是真源） |
| `events.py` | `SessionEvent` 信封 + `KNOWN_EVENT_TYPES`（**20 个事件类型**） |
| `derive.py` | `derive_messages()`：事件 → 消息的**纯函数** |
| `transcript.py` | `TranscriptWriter` 协议 + `LocalFileTranscriptWriter`（JSONL，zstd） |
| `db_transcript.py` | `SQLiteTranscriptWriter`（events + session_header 表） |
| `request_header.py` | `request/header` 快照（模型当时被告知了什么） |
| `header.py` | `SessionHeader` + 格式版本门 |
| `fork.py` | 按 `seq` 分叉出新会话 |
| `redact.py` | 读取侧脱敏投影 |
| `replay.py` | 不调模型重建 SDK 输出流 |
| `stats.py` | 由事件流 O(1) 推导 TTFT / TPS / token |
| `factory.py` | `create_transcript_writer()`——按配置挑选落盘后端 |

详见 [会话事件溯源](../architecture/session-observability.md)。

---

## 5. 权限：permissions/

| 概念 | 内容 |
|------|------|
| 四种模式 | `default` / `accept_edits` / `plan` / `bypass` |
| 三种裁决 | `allow` / `ask` / `deny` |
| 判定顺序 | DENY 规则 → ALLOW 规则 → ASK 规则 → 模式默认 |
| 规则 | 按 `tool_name` / `path` / `bash_pattern`(正则) 三轴匹配 |
| 沙箱 | `PathSandbox.validate()` 拦住越界路径；`IgnoreMatcher` 认 `.gitignore`/`.nonoignore` |
| 拒绝追踪 | `DenialTracker`——工具内部的拒绝只能由它上报 |

两个容易踩的点：

- **`bypass` 也不是万能放行**——显式 `DENY` 规则依然生效。
- **`default` 与 `accept_edits` 在代码里行为相同**（都：有写副作用就 `ask`）。
  "工作区内自动允许编辑"是通过 **rules 层**表达的，不是靠 `accept_edits` 这个模式。

详见 [工具与权限](../guide/tools-and-permissions.md)。

---

## 6. 扩展面速查

| 包 | 协议 / 基类 | 扩展方式 |
|----|------------|---------|
| `prompt/` | `PromptSection`(Protocol) / `PromptSectionBase` | 实现 `render()`，注册到 `SectionRegistry` |
| `rules/` | `RuleLoader`(Protocol) / `FileSystemRuleLoader` | `rules/*.md`、`AGENTS.md`/`CLAUDE.md` |
| `skills/` | `SkillLoader`(Protocol) | `skills/<name>/SKILL.md`，模型用 `skill` 工具激活 |
| `plugins/` | `PluginLoader`(Protocol) | `plugin.json` 组合下层能力 |
| `toolplugins/` | — | `plugin.json`（`http_json` / `mcp` 两种 kind） |
| `commands/` | `SlashCommand`(Protocol) | 实现 `execute()` |
| `agents/` | `AgentTool` / `AgentDefinition` / `AgentRunner` | `agents/*.md`，模型用 `agent` 工具派发 |
| `memory/` | `MemoryStore`(Protocol) | `~/.nono-agent/memory/<id>.json` |
| `checkpoint/` | `CheckpointStore`(Protocol) | 实现 `snapshot`/`rewind` |
| `observability/` | `MetricsRecorder`(Protocol) | `set_metrics_recorder()` 换 OTel/Prometheus |
| `mcp/` | — | `~/.nono-agent/mcp.json` |

---

## 7. 贯穿全局的三条规矩

读完上面的表，你会发现三个模式在每一行重复出现。它们不是巧合：

**① 协议（Protocol）定契约，基类给便利默认。**
`Tool`/`ToolBase`、`PromptSection`/`PromptSectionBase`、`EngineMiddleware`/`MiddlewareBase`……
每个可插拔点都是这个两层结构。新人先看协议理解约束，动手时继承基类。

**② 默认偏保守（但要知道例外）。**
工具默认不并发、默认有副作用；`PermissionPolicy` 模式默认为 `default`（写操作要问）；
非法配置往安全方向降级（比如非法 mode 字符串退回 `default`，而不是放行）；
`bypass` 模式下显式 `DENY` 规则依然生效。

!!! warning "唯一 fail-open 的地方"
    **没注入 `permission_checker` 时，工具调用默认全部放行**
    （`tools/context.py:119` 写明"无检查器时默认允许"）。
    也就是说库用法若不自己注入检查器，工具是直接执行的。
    "拒绝一切写操作、仅放行 `job_kill`"是 Server 模式主动注入 deny-all checker 的结果，
    属于产品层选择，不是引擎默认。

**③ 没有 entry point。**
全项目 grep `entry_points` / `importlib.metadata` / `pkg_resources` **零命中**。
所有扩展的发现都靠**文件系统 + 清单**。所以在项目里丢个目录就能生效，
不用装包、不用注册。

---

## 8. 继续

- 这些技术为什么这么选 → [05 · 技术选型](05-tech-choices.md)
- 每个设计背后的取舍故事 → [06 · 设计取舍](06-design-decisions.md)