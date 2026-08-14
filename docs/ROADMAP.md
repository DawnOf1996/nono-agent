# Nono Agent —— 后续规划 Roadmap

> 对照代码现状（`0.1.0`）。架构设计见 [NonoAgent-Architecture.md](./NonoAgent-Architecture.md)。  
> 对标参考：工作区 `ClaudeCode/` 源码与其 `docs/`（QueryEngine / Prompt 组装 / Subagent）。  
> 状态约定：`未开始` / `骨架` / `可用需打磨` / `已完成`

**项目定位：** 可扩展的 Agent **运行时骨架**（灵感来自 Claude Code QueryEngine）。循环主路径可跑通；生产级工具与扩展面尚未建成。  
**对标策略：** 对齐 CC 的 **Agent 运行时深度**（提示词组装、权限规则、Agent 三件套、MCP/Skills 等）；**不**复刻 Ink TUI、账号体系与全部产品 slash。

---

## 1. 能力全景（现状）


| 能力域                                             | 状态    | 备注                                                |
| ----------------------------------------------- | ----- | ------------------------------------------------- |
| QueryEngine + agentic loop                      | 可用需打磨 | 主路径可用；边界/预算/取消等需加固                                |
| Prompt Section 组装管线                             | 可用需打磨 | `prompt/` 有序 section + 静态/动态边界 + 优先级合并              |
| 状态机 / RecoveryChain                             | 可用需打磨 | 原因枚举齐全；策略与可观测性可加强                                 |
| 默认预处理管线（4 级）                                    | 可用需打磨 | ContextCollapse 可配置启用（默认 summarizer）               |
| Anthropic / OpenAI / OpenAI-Compatible Provider | 可用需打磨 | 原厂 + Chat Completions 兼容可用                        |
| Anthropic Messages 兼容（`messages_compatible`）    | 可用需打磨 | 非原厂 Messages 协议网关（httpx SSE）                         |
| OpenAI Responses Provider（原厂）                   | 可用需打磨 | 已实现流式事件映射（待真实 API 验证）                         |
| Responses 兼容（`responses_compatible`）            | 可用需打磨 | 非原厂 Responses 协议网关（httpx SSE）                       |
| 工具协议 / Registry / Executor                      | 可用需打磨 | 三态权限决策接入 Executor                                    |
| 内置工具                                            | 可用需打磨 | Bash/Read/Write/Edit/Glob/Grep/AskUser 可用；缺 LSP/Web/Worktree |
| AskUserQuestion 通道                              | 可用需打磨 | CLI 交互回调可用；Server 回调/SSE 待 Phase 3                |
| LSP 工具                                          | 未开始   | 对标 CC `LSPTool`                                   |
| ToolSearch / MCP Resources+Auth                 | 可用需打磨 | ToolSearch + Resources 可用；MCP Auth 待做               |
| Settings 分层（user/project/policy）                | 可用需打磨 | `settings/` 分层合并 + 来源追踪；待接入 EngineConfig        |
| Rules（项目/用户指令）                                  | 可用需打磨 | `rules/` 发现/合并/注入；含 `CLAUDE.md`/`AGENTS.md` 兼容        |
| Skills（含 SkillTool）                             | 可用需打磨 | `skills/` 发现/加载 + SkillTool + Section 注入                  |
| Plugins（扩展包）                                    | 可用需打磨 | plugin.json 组合注册 Rules/Skills/Agents/MCP/权限             |
| MCP 配置与桥接                                       | 可用需打磨 | stdio tools/resources 桥接；auth/SSE/ToolSearch 待做        |
| 用户态 Hooks 配置                                    | 可用需打磨 | `hooks.json` 命令执行器（before_tool 可否决）；HTTP 执行器待做        |
| 权限模式 + PermissionRule + denial 追踪               | 可用需打磨 | `permissions/` 策略/规则/沙箱/denial/Bash 分类；Server 默认 deny-write |
| Slash / 运行时命令（MVP 子集）                           | 可用需打磨 | `commands/` + help/model/cost/context/clear；不对齐 CC 100+ 全量 |
| Plan / Memory / Checkpoint / Task 生命周期          | 可用需打磨 | Plan/TodoWrite/Memory/Checkpoint/Task 均已落地；多模态附件待做     |
| 子 Agent 三件套                                     | 可用需打磨 | AgentTool + AgentDefinition + AgentRunner；worktree 隔离/后台待做   |
| Worktree 进出工具                                   | 可用需打磨 | enter/exit_worktree；cwd 自动切换待做                        |
| Agent Teams / Coordinator                       | 未开始   | **P2 / 可选**；见 §6                                  |
| Bridge / Remote / Cron                          | 未开始   | **默认非目标**；见 §6                                    |
| CLI 适配器                                         | 可用需打磨 | REPL / resume / 流式渲染可用                            |
| Server 适配器（SSE）                                 | 可用需打磨 | 多会话 + SSE + 鉴权 + TTL + AskUser 回调；无 WebSocket   |
| 会话持久化（Transcript）                               | 可用需打磨 | Protocol + NDJSON + SQLite；缺 Redis/Postgres         |
| CostTracker / 日志                                | 可用需打磨 | Metrics 最小可用（内存 counter/histogram/gauge）          |
| 文档 / README                                     | 进行中   | 本文档与 README 对齐中                                   |


---



## 1.5 Agent 运行时扩展分层（设计约束）

对标 Cursor / Claude Code 的 **Agent 运行时共性**（不含 IDE Tab / Inline UI）。扩展面按层叠加，**上层组合下层，禁止反向耦合**：

```text
┌─────────────────────────────────────────────────────────┐
│  Plugins（分发单元）  打包 Rules / Skills / Agents / Tools / Hooks / MCP │
├─────────────────────────────────────────────────────────┤
│  编排层     主循环 · 子 Agent · Plan · Slash · Task 生命周期          │
├─────────────────────────────────────────────────────────┤
│  外部工具   MCP（tools/resources/auth）· 内置 Tools · LSP · ToolSearch │
├─────────────────────────────────────────────────────────┤
│  按需能力   Skills（含 SkillTool）                                     │
├─────────────────────────────────────────────────────────┤
│  常驻约束   Rules · Settings · 权限模式/规则 · Ignore                  │
├─────────────────────────────────────────────────────────┤
│  提示组装   Prompt Section Registry（静态缓存前缀 + 动态段）           │
├─────────────────────────────────────────────────────────┤
│  引擎内核   QueryEngine · Loop · Pipeline · Provider · Session         │
└─────────────────────────────────────────────────────────┘
```

可扩展约定：


| 原则               | 含义                                                                   |
| ---------------- | -------------------------------------------------------------------- |
| Protocol 优先      | Rules / Skill / Plugin / MCP / Transcript / Memory 均以 Protocol 或清单接入 |
| 发现与注入分离          | Loader 只负责扫描校验；Injector / Section / Middleware 负责挂载                  |
| 配置可落盘            | 用户态能力必须能用项目文件表达，不只靠构造函数参数                                            |
| 组合优于硬编码          | Plugin = 对下层能力的清单组合；新增能力优先写配置或插件                                     |
| 安全默认 Fail-closed | 权限、网络、路径逃逸默认拒绝；显式 allow / 模式切换才放宽                                    |
| 深度对齐 CC 运行时      | 提示词组装、权限规则、Agent 三件套等按 CC 语义设计，API 可 Python 化                        |


关系澄清：

- **Rules ≠ Skills**：Rules 常驻；Skills 按需（且可有 SkillTool）  
- **Plugin ≠ MCP**：Plugin 本地扩展包；MCP 外部进程协议  
- **用户 Hooks ≠** `MiddlewareChain`：前者可落盘；后者引擎内部扩展点  
- **权限模式 ≠ PermissionRule**：模式是会话策略；规则是可落盘 allow/deny 细则  
- **AgentTool ≠ AgentDefinition ≠ 子实例**：入口工具 / 配置数据 / 运行中的子引擎



### 1.6 相对 Claude Code 源码：规划覆盖度


| 类别           | 内容                                                                                                                           |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| **已覆盖主干**    | loop/压缩/恢复、基础工具、Rules/Skills/Plugins/MCP、权限模式、Hooks、Slash、子 Agent、Plan/Memory、worktree 提及                                    |
| **曾偏薄、本文加细** | Prompt 组装、Agent 三件套、PermissionRule+denial、SkillTool、MCP Resources/Auth、ToolSearch、AskUser、LSP、Settings、Task 生命周期、Worktree 工具 |
| **明确降级/非目标** | Teams/Coordinator（P2）、Bridge/Remote/Cron（默认不做）、Ink TUI、OAuth 云账号、100+ slash 全量、ant-only 实验工具                                 |


参考文档（ClaudeCode 工作区）：`docs/QueryEngine-Design-Analysis.md`、`Prompt-Assembly-Pipeline.md`、`Subagent-Architecture.md`。

---



## 2. 未实现能力（能力层缺口）



### 2.0 Prompt Section 组装管线（优先级：P0）

对标 CC `Prompt-Assembly-Pipeline.md`：系统提示是**有序 section 列表**（静态缓存前缀 + 动态段），而非单次字符串拼接。


| 项                         | 说明                                                   | 状态  |
| ------------------------- | ---------------------------------------------------- | --- |
| `PromptSection` Protocol  | `name` + `render(ctx) -> str | None`                 | 已完成 |
| Section Registry          | 注册顺序、按名 memoize、可禁用                                  | 已完成 |
| 静态/动态边界                   | 利于 prompt cache（对标 `SYSTEM_PROMPT_DYNAMIC_BOUNDARY`） | 已完成 |
| 优先级合并                     | override > agent > custom > default；`append_*` 永远追加  | 已完成 |
| 内置 section                | identity、工具规范、权限/hooks 说明、env/git、Rules、Memory…      | 可用需打磨 |
| 替换 `_build_system_prompt` | Engine 只调组装器                                         | 已完成 |


建议模块：`nono_agent/prompt/`。

### 2.1 生产级内置工具（优先级：P0）

当前 `tools/builtin.py` 仅为 E2E 演示。


| 工具                   | 目标能力                               | 优先级   | 状态  |
| -------------------- | ---------------------------------- | ----- | --- |
| Bash / Shell         | 超时、cwd、截断、abort；危险命令分类             | P0    | 已完成 |
| Read                 | 大文件分段、编码/二进制、行号                    | P0    | 已完成 |
| Write / Edit         | 原子写、diff/patch、权限门控                | P0    | 可用需打磨 |
| Glob / Grep          | 工作区匹配与内容搜索                         | P0    | 已完成 |
| LS / Tree            | 目录列举 + Ignore                      | P1    | 已完成 |
| WebFetch / WebSearch | 网络 + SSRF/安全策略                     | P1    | 已完成 |
| AskUserQuestion      | 结构化提问；CLI 交互 / Server 回调或 SSE      | P0/P1 | 可用需打磨 |
| LSP                  | 定义跳转/引用/诊断（对标 `LSPTool`）           | P1    | 未开始 |
| NotebookEdit         | Jupyter（按需）                        | P2    | 未开始 |
| ToolSearch           | 工具过多时按需检索暴露                        | P1    | 已完成 |
| Enter/Exit Worktree  | git worktree 进出                    | P1    | 已完成 |
| TodoWrite            | 轻量任务列表                             | P1    | 已完成 |
| Task* 工具族            | Create/Get/Update/List/Stop/Output | P2    | 已完成 |
| Skill / Agent        | 见 §2.3 / §2.8                      | P1    | 已完成 |


配套：路径沙箱 + Ignore、输出预算、abort、Bash 与权限模式联动。

### 2.2 Rules —— 常驻指令层（优先级：P0）

对标 Cursor Rules 与 CC `CLAUDE.md` / memory files 发现。


| 项           | 说明                                               | 状态  |
| ----------- | ------------------------------------------------ | --- |
| 文件约定        | `.nono/rules/`、`AGENTS.md`、`CLAUDE.md`（兼容读）、用户全局 | 已完成 |
| 发现与加载       | CWD 向上 walk、校验、缓存                                | 可用需打磨 |
| 层级合并 / 条件启用 | 全局 < 项目 < 路径级；`globs`                            | 已完成 |
| 注入          | 作为 Prompt Section（§2.0），非一次性拼接                   | 已完成 |
| 调试          | `/rules` 列出生效来源                                  | 已完成 |


建议模块：`nono_agent/rules/`。

### 2.3 Skills —— 按需能力包（优先级：P0/P1）


| 项             | 说明                            | 状态  |
| ------------- | ----------------------------- | --- |
| 目录与 manifest  | `SKILL.md` / frontmatter      | 已完成 |
| 发现与加载         | 项目 / 用户 / plugin / bundled    | 可用需打磨 |
| 注入管线          | 提示片段 + 工具白名单                  | 可用需打磨 |
| **SkillTool** | 模型可调用的激活入口（对标 CC `SkillTool`） | 已完成 |
| CLI/Server    | list / enable / disable       | CLI 可用（/skills）；Server 待做 |




### 2.4 MCP 配置与使用（优先级：P0/P1）


| 项                     | 说明                          | 状态  |
| --------------------- | --------------------------- | --- |
| 配置 schema / 传输 / 生命周期 | `mcp.json`、stdio/SSE、启停健康检查 | 可用需打磨 |
| Tool 桥接               | MCP tools → `Tool` Protocol | 已完成 |
| **Resources**         | List/Read MCP resources     | 已完成 |
| **Auth**              | OAuth/令牌流（对标 `McpAuthTool`） | 静态 token 起步 |
| 权限隔离 + ToolSearch 协同  | 按 server 授权；工具过多时延迟暴露       | 未开始 |


建议模块：`nono_agent/mcp/`。

### 2.5 Plugins —— 扩展分发单元（优先级：P1）

可打包：Rules、Skills、**AgentDefinition**、Tools、Hooks、MCP 声明、权限需求清单。


| 项         | 说明                                                | 状态  |
| --------- | ------------------------------------------------- | --- |
| 清单 + 发现路径 | `plugin.json`；`.nono/plugins`、用户目录               | 已完成 |
| 加载器       | 校验、启停、冲突、失败隔离                                     | 已完成 |
| 注册对接      | Rules / Skills / Agents / MCP / 权限（Tools 经 MCP）   | 可用需打磨 |


建议模块：`nono_agent/plugins/`。原则：引擎只认 Protocol；插件无 loop 特权入口。

### 2.6 权限模式、PermissionRule 与 Ignore（优先级：P0）

对标 CC `PermissionMode` + `permissions/*`。


| 模式             | 行为         | 状态  |
| -------------- | ---------- | --- |
| `default`      | 写/执行需确认    | 已完成 |
| `accept_edits` | 工作区内编辑自动允许 | 已完成 |
| `plan`         | 禁止写副作用     | 已完成 |
| `bypass`       | 显式危险模式     | 已完成 |



| 项                  | 说明                                         | 状态  |
| ------------------ | ------------------------------------------ | --- |
| `PermissionPolicy` | 模式 + Checker                               | 已完成 |
| **PermissionRule** | 可落盘 allow/deny（工具名、路径、bash 模式）             | 已完成 |
| **denial 追踪**      | 进入会话态与 `SDKResult`（对标 `permissionDenials`） | 已完成 |
| Ignore + 路径沙箱      | `.gitignore` / `.nonoignore`；拒绝逃逸          | 已完成 |
| Bash 分类            | 危险命令与模式联动                                  | 可用需打磨 |




### 2.6.1 Settings 分层（优先级：P1）

对标 CC user / project / policy / flag。


| 来源         | 用途                      | 状态  |
| ---------- | ----------------------- | --- |
| 默认         | 引擎内置                    | 已完成 |
| user       | `~/.nono/settings.json` | 已完成 |
| project    | `.nono/settings.json`   | 已完成 |
| policy     | 托管/企业下发（Server）         | 已完成 |
| flag / env | CLI 临时覆盖，最高优先           | 已完成 |


各子系统禁止各自读文件；统一读合并后的 Settings。

### 2.7 用户态 Hooks 与 Slash 命令（优先级：P1）

**Hooks：** `session_start/end`、`before/after_tool`（可否决）、`before_submit`/`on_stop`；配置 `hooks.json` 或 Plugin；执行器为命令/HTTP（沙箱 + SSRF 防护）。

**Slash MVP（不对齐 CC 100+ 全量）：**


| 命令                                        | 作用           | 状态 |
| ----------------------------------------- | ------------ | --- |
| `/compact` `/clear`                       | 压缩 / 清空      | 已完成 |
| `/rules` `/skills` `/mcp` `/plugins`      | 扩展面调试        | 已完成 |
| `/permission` `/model` `/cost` `/context` | 模式、模型、成本、上下文 | 已完成 |
| `/memory` `/hooks`                        | P2 可后置       | 已完成 |
| Plugin 自定义 slash                          | 扩展机制         | 未开始 |


建议：`nono_agent/commands/`。

### 2.8 子 Agent 三件套（优先级：P1）

对标 CC `Subagent-Architecture.md`：


| 概念                  | 职责                                                              | 状态  |
| ------------------- | --------------------------------------------------------------- | --- |
| **AgentTool**       | 模型入口（`subagent_type`/`prompt`/model/background/cwd/`isolation`） | 可用需打磨 |
| **AgentDefinition** | tools 白名单、model、system prompt；多 source                          | 已完成 |
| **run 实例**          | 独立 Engine/Store，结果回写父会话                                         | 已完成 |


Definition 来源：`built-in` / `user`（`~/.nono/agents`）/ `project`（`.nono/agents`）/ `plugin` / `flag`。

运行时：权限子集化、预算封顶、abort 联动、结果协议、串行默认、`isolation=worktree`、可选后台 + Task 输出。

建议模块：`nono_agent/agents/`。

### 2.8.1 Agent Teams / Coordinator（优先级：P2，可选）

对标 CC Team/SendMessage/coordinator。**不阻塞** Phase 1–3；见 §6。

### 2.9 会话消息持久化扩展（优先级：P1）

`TranscriptWriter` Protocol；已有 LocalFile NDJSON + SQLite + 工厂切换。规划：Postgres/MySQL、Redis、可选 S3。配套：schema 版本、`list_sessions`/`delete`/`read_range`。

### 2.10 Provider 协议矩阵扩展（优先级：P1）


| `adapter`              | 协议               | 面向        | 现状  |
| ---------------------- | ---------------- | --------- | --- |
| `anthropic`            | Messages         | 原厂        | ✅   |
| `messages_compatible`  | Messages         | 非原厂       | ✅   |
| `openai`               | Chat Completions | 兼容        | ✅   |
| （前缀 gpt-/o*）           | Chat Completions | 原厂 OpenAI | ✅   |
| `responses`            | Responses        | 原厂        | ✅   |
| `responses_compatible` | Responses        | 非原厂       | ✅   |


配套：`_messages_utils` / `_responses_utils`；`extra_body`；与 §2.0 静态段协同 prompt cache。

### 2.11 Plan / Memory / Checkpoint / Task（优先级：P1–P2）


| 能力                  | 说明                                 | 优先级 | 状态  |
| ------------------- | ---------------------------------- | --- | --- |
| Plan 模式工具           | Enter/Exit Plan；联动 `plan` 权限       | P1  | 已完成 |
| TodoWrite           | 轻量进度                               | P1  | 已完成 |
| Task 工具族            | Create/Get/Update/List/Stop/Output | P2  | 可用需打磨 |
| Memory（memdir 思路）   | 记忆目录 + 类型约束 + Prompt Section       | P2  | 可用需打磨 |
| Checkpoint / Rewind | 编辑快照/补丁栈                           | P2  | 已完成 |
| 多模态附件               | `attachments` 产品化                  | P2  | 未开始 |


Protocol：`MemoryStore` / `CheckpointStore`。

### 2.12 其它未实现 / 占位


| 项                        | 现状    | 计划                 |
| ------------------------ | ----- | ------------------ |
| Responses 原厂 Provider    | 已实现   | 待真实 API 验证后供兼容层复用    |
| Server WebSocket         | 仅 SSE | 按需                 |
| Metrics                  | 最小可用 | 内存 counter/histogram/gauge |
| ContextCollapse 默认启用     | 可配置   | `context_collapse_enabled` 开关 |
| `json_schema` 闭环         | 可用需打磨 | response_format + structured_output 解析；深度 schema 校验待做 |
| 预算硬门控                    | 已实现   | loop 强制终止          |
| Output Styles            | 已实现   | `output_style` 配置 + OutputStyle section |
| SDK `permission_denials` | 已实现   | SDKResult 回灌 DenialTracker |


---



## 3. 已实现需打磨（运行时加固）



### 3.1 核心引擎

- [ ] 预算门控：`max_budget_usd` / `task_budget` 在 loop 内真正生效并产出明确 `TerminalReason`
- [x] `fallback_model` 流式失败切换路径验证与测试
- [ ] abort：流式、工具执行、权限等待、AskUser 等待各阶段一致性（含竞态）
- [ ] `MessageStore` compact boundary / tombstone / resume 边界用例补齐
- [ ] Recovery 策略可观测：压缩节省 token、重试原因对 SDK 消费者透明
- [ ] 接入 §2.0 Prompt Section 组装器，替换临时 `_build_system_prompt`
- [ ] 默认系统提示与工具描述质量（面向真实任务，而非演示）



### 3.2 Provider

- [ ] Anthropic / OpenAI / Compatible 的 tool_use、thinking、usage 字段对齐
- [ ] 错误分类统一（prompt_too_long / rate_limit / overload → RecoveryChain）
- [ ] token 估算与真实 usage 偏差校准
- [ ] 实现原厂 `ResponsesProvider`，并抽出可复用的 Responses 事件映射
- [ ] 新增 `messages_compatible` / `responses_compatible` 并注册到 `ProviderRegistry`
- [ ] adapter 命名与文档一致，避免「openai = 原厂还是兼容」歧义
- [ ] 实现或移除已过时占位，避免“路由存在但必崩”



### 3.3 管线

- [x] 默认是否注册 ContextCollapse 的配置项（`context_collapse_enabled` + 默认 summarizer）
- [ ] AutoCompact 80%/95% 阈值与模型窗口表维护



### 3.4 工具与权限

- [ ] 落地 `PermissionPolicy` + `PermissionRule` + denial 追踪（§2.6）
- [ ] Server 默认 deny-write 与可配置策略文档化
- [ ] 工具输入 JSON Schema 校验在 execute 前强制执行
- [ ] 并发安全标记审计；危险工具强制串行
- [ ] 内置工具统一遵守 Ignore + 路径沙箱
- [ ] Bash 危险命令分类与 bypass killswitch
- [ ] AskUserQuestion 在 CLI/Server 的统一事件协议



### 3.5 扩展面（Rules / Plugins / Hooks / Settings）

- [ ] Rules / Prompt Section 合并结果可调试（哪些文件、为何生效）
- [ ] Plugin 失败隔离与权限声明校验；可注册 AgentDefinition
- [ ] Settings 分层合并可观测（最终生效值来源）
- [ ] 用户 Hooks 超时、否决语义与引擎 `MiddlewareChain` 对齐文档
- [ ] Slash 与 `submit_message` 的分流协议



### 3.6 会话与持久化

- [ ] `LocalFileTranscriptWriter`：损坏行恢复、并发多进程写入、eager_flush 行为验证
- [ ] `record` 全量覆写 vs `record_append` 在 compact / resume 路径上的一致性
- [ ] 协议是否需扩展（如 `list_sessions` / `delete` / `read_range`）以支撑 DB/Redis 后端
- [ ] 压缩后 transcript / resume 一致性（与管线协同）



### 3.7 适配器

- [x] CLI：去掉硬编码密钥/调试配置，改为环境变量与配置文件
- [x] CLI：会话列表、成本展示（工具调用可视化待做）
- [x] Server：会话 TTL、并发限制、鉴权、优雅关闭压测
- [x] Server：可注入非文件 Transcript 后端（为 DB 多会话做准备）
- [x] SDKMessage 对外契约稳定化（版本字段、`permission_denials`、AskUser 事件）



### 3.8 可观测、测试与工程化

- [x] Metrics 真实实现（至少 counter/histogram）
- [x] 扩展单测与集成测（真实 Provider 可选、MCP mock、Transcript 后端 mock）
- [x] CI：pytest + ruff + mypy
- [x] README / 示例与真实默认行为一致
- [x] 清理仓库内敏感信息风险（密钥不得入库）

---



## 4. 分阶段规划



### Phase 0 —— 文档与基线对齐（已完成）

- [x] 架构文档标注实现状态
- [x] 编写本 Roadmap（含相对 CC 缺口）
- [x] 更新 README
- [x] 清理 CLI 中硬编码密钥与调试 Provider 配置



### Phase 1 —— 可干活的本地 Agent（P0）

1. [x] 生产级工具：Bash、Read、Edit/Write、Glob、Grep；Bash 安全分类起步
2. [x] **PermissionPolicy + PermissionRule + Ignore + 路径沙箱**（§2.6）+ denial 追踪
3. [x] **Prompt Section 组装**（§2.0）+ **Rules 发现注入**（§2.2，含 CLAUDE.md 兼容）
4. [x] AskUserQuestion 最小通道（CLI）
5. [x] 引擎预算门控与关键边界测试
6. [x] CLI 配置外部化（env / 配置文件）

**完成定义：** 本地「读改跑」闭环；Rules/权限可落盘；拒绝可回灌；可取消。

### Phase 2 —— 可扩展生态（P0/P1）

1. [x] MCP：tools + **resources**（auth/ToolSearch 待做）
2. [x] Skills + **SkillTool**
3. [x] Plugins（含 Agents/Hooks 声明）
4. [x] Settings 分层（user/project/flag）
5. [x] 用户 Hooks + Slash MVP
6. [x] Plan 模式工具 + TodoWrite
7. [ ] LSP 工具（可选但建议）
8. [x] ContextCollapse 默认 summarizer；Metrics 最小可用
9. [x] Transcript：SQLite 起步
10. [x] Provider：`messages_compatible`；原厂 `responses`

**完成定义：** 配置/插件扩展行为，无需改引擎；MCP/Skills/Rules/Settings 可组合。

### Phase 3 —— 多 Agent 与服务化（P1）

1. [x] **子 Agent 三件套**（AgentTool + Definition + run）+ Worktree 工具
2. [x] Server：鉴权、会话治理（TTL）、AskUser 回调/SSE（WebSocket 按需待做）
3. [ ] Transcript 生产后端；Plugin/Slash 管理 API
4. [x] `responses_compatible`（结构化输出闭环待做）
5. [x] SDK 契约含 `permission_denials`

**完成定义：** 可落盘 Agent 定义并可派发子任务；CLI/Server 扩展面对称。

### Phase 4 —— 打磨与生产就绪（持续）

- Memory（memdir）/ Checkpoint / Task 工具族 / Output Styles（§2.11）  
- 可选：Agent Teams / Coordinator（§2.8.1）  
- Provider 对齐、压缩与成本、CI 门禁

---



## 5. 完整包树与职责说明

> **原则：** 规划目录是「引擎内核 + 扩展面」并存，**不是**推倒重来。  
> 现有 `core/` / `pipeline/` / `recovery/` / `hooks/` / `adapters/` / `observability/` / `utils/` **原位保留**；扩展包以新建为主；`tools/builtin.py` 拆为包，权限/prompt 从现有文件**抽出**。

状态标记：`✅` 已有主路径 · `🔧` 已有需扩展/拆分 · `📋` 规划新建 · `P2` 后期

### 5.1 完整目标包树

```
nono_agent/
│
│ ─── 引擎内核（保留）──────────────────────────────────────────
├── core/                          # ✅ 编排与类型
│   ├── engine.py                  # QueryEngine · submit_message
│   ├── loop.py                    # agentic_loop
│   ├── config.py                  # EngineConfig / QueryConfig / ProviderConfig
│   ├── state.py                   # LoopState / Transition / Terminal
│   └── types.py                   # InternalMessage · SDKMessage
│
├── pipeline/                      # 🔧 预处理压缩管线
│   ├── base.py
│   ├── tool_result_budget.py
│   ├── snip_compact.py
│   ├── microcompact.py
│   ├── context_collapse.py        # 默认未注册；需 summarizer
│   └── auto_compact.py
│
├── recovery/                      # ✅ 错误恢复决策
│   ├── chain.py
│   ├── prompt_too_long.py
│   ├── max_output_tokens.py
│   └── media_size.py
│
├── hooks/                         # 🔧 引擎内生命周期（≠ 用户 hooks.json）
│   ├── middleware.py              # MiddlewareChain
│   ├── stop_hooks.py
│   └── post_sampling.py
│
├── adapters/                      # 🔧 交付层
│   ├── cli.py
│   ├── server.py
│   └── testing.py
│
├── observability/                 # 🔧 成本 / 日志 / 指标
│   ├── cost_tracker.py
│   ├── logging.py
│   └── metrics.py                 # No-Op → 真实实现
│
├── utils/                         # 🔧 通用原语
│   ├── abort.py
│   └── tokens.py                  # 可增 path/ignore 辅助，或放到 permissions
│
│ ─── LLM 与会话（保留 + 扩展）────────────────────────────────
├── providers/                     # 🔧 协议矩阵
│   ├── base.py                    # LLMProvider · ProviderRegistry
│   ├── anthropic_provider.py      # ✅ Messages 原厂
│   ├── messages_compatible.py     # 📋 Messages 非原厂
│   ├── openai_provider.py         # ✅ Chat 原厂
│   ├── openai_compatible.py       # ✅ Chat 兼容
│   ├── responses_provider.py      # 📋/占位 Responses 原厂
│   ├── responses_compatible.py    # 📋 Responses 非原厂
│   ├── _openai_utils.py
│   ├── _messages_utils.py         # 📋
│   └── _responses_utils.py        # 📋
│
├── session/                       # 🔧 内存消息 + 持久化
│   ├── store.py                   # MessageStore ✅
│   ├── transcript.py              # Protocol + LocalFile ✅
│   ├── db_transcript.py           # 📋 SQL
│   ├── redis_transcript.py        # 📋 Redis
│   └── factory.py                 # 📋 按配置创建 Writer
│
│ ─── 工具协议 + 生产工具（拆分迁移）──────────────────────────
├── tools/                         # 🔧
│   ├── protocol.py                # Tool · ToolBase ✅
│   ├── registry.py                # ToolRegistry ✅
│   ├── executor.py                # Streaming/Batch ✅
│   ├── context.py                 # ToolUseContext（瘦身；权限委托 permissions）
│   └── builtin/                   # 📋 替代 builtin.py
│       ├── __init__.py            # create_builtin_tools()
│       ├── bash.py / read.py / write.py / edit.py
│       ├── glob.py / grep.py
│       ├── ask_user.py
│       ├── lsp.py / worktree.py   # P1
│       ├── web_fetch.py / …       # P1
│       └── …                      # Todo / Plan 进出等
│
│ ─── 扩展面（规划新建）──────────────────────────────────────
├── prompt/                        # 📋 Prompt Section 组装
│   ├── section.py                 # PromptSection Protocol
│   ├── registry.py                # 有序注册 · memoize · 静/动态边界
│   ├── assembler.py               # 优先级：override > agent > custom > default
│   └── sections/                  # 内置 section 实现
│       ├── identity.py
│       ├── tools_guidance.py
│       ├── env_info.py
│       ├── rules_section.py       # 消费 rules/
│       └── memory_section.py      # 消费 memory/（P2）
│
├── rules/                         # 📋 常驻指令发现/合并
│   ├── loader.py                  # RuleLoader Protocol + 文件系统实现
│   ├── merge.py                   # 层级与 globs
│   └── models.py                  # Rule 清单类型
│
├── skills/                        # 📋 按需能力包
│   ├── loader.py
│   ├── models.py
│   └── skill_tool.py              # 注册进 ToolRegistry 的 SkillTool
│
├── plugins/                       # 📋 扩展分发单元
│   ├── loader.py                  # 清单校验 · 启停 · 失败隔离
│   ├── manifest.py                # plugin.toml/json schema
│   └── activate.py                # 向各 Registry 注册
│
├── commands/                      # 📋 Slash MVP（无 UI）
│   ├── registry.py                # CommandRegistry
│   ├── parser.py                  # 与自然语言分流
│   └── builtin/                   # compact/clear/rules/mcp/…
│
├── settings/                      # 📋 分层配置合并
│   ├── sources.py                 # default/user/project/policy/flag
│   ├── merge.py
│   └── models.py
│
├── permissions/                   # 📋 从 context/adapters 抽出
│   ├── policy.py                  # PermissionPolicy · 模式
│   ├── rules.py                   # PermissionRule 解析/匹配
│   ├── denial.py                  # denial 追踪 → SDKResult
│   ├── sandbox.py                 # 路径沙箱 · Ignore
│   └── bash_classify.py           # 危险命令分类（可后置）
│
├── mcp/                           # 📋 MCP 客户端
│   ├── config.py
│   ├── client.py                  # 传输 · 生命周期
│   ├── bridge.py                  # tools → Tool Protocol
│   ├── resources.py               # List/Read resources
│   └── auth.py                    # MCP auth 流
│
├── agents/                        # 📋 子 Agent 三件套
│   ├── definition.py              # AgentDefinition · 多 source 加载
│   ├── agent_tool.py              # 模型可调用入口
│   ├── runner.py                  # 独立 Engine/Store · 结果回写
│   └── builtin/                   # general-purpose / explore 等
│
├── memory/                        # P2 跨会话记忆
│   ├── store.py                   # MemoryStore Protocol
│   └── memdir.py                  # 目录布局 · 类型约束
│
└── checkpoint/                    # P2 编辑回滚
    ├── store.py                   # CheckpointStore Protocol
    └── file_history.py            # 快照/补丁栈
```

项目侧约定目录（与包分离，属用户/仓库配置）：

```
.nono/
├── settings.json
├── rules/
├── agents/                 # AgentDefinition markdown
├── skills/
├── plugins/
├── hooks.json              # 用户态 Hooks（由 hooks 适配层加载）
├── mcp.json
└── memory/
```

### 5.2 各包职责说明

#### 引擎内核

| 包 | 职责 | 不负责 | 现状 |
|----|------|--------|------|
| **`core/`** | 顶层编排（`QueryEngine`）、主循环、不可变配置与运行时快照、状态机、消息/SDK 类型 | 具体 LLM HTTP、具体工具实现、UI | ✅ |
| **`pipeline/`** | API 调用前消息压缩（预算截断、滑动窗口、microcompact、autocompact、可选 collapse） | 持久化、权限决策 | 🔧 ContextCollapse 未默认启用 |
| **`recovery/`** | API/循环错误分类与恢复动作（压缩重试、升 max_tokens、终止） | 主动压缩策略实现细节可委托 pipeline | ✅ |
| **`hooks/`** | **引擎内** Middleware / Stop / PostSampling 链（代码注入） | 用户 `hooks.json` 的加载与沙箱执行（由扩展面适配后挂入本链） | 🔧 |
| **`adapters/`** | CLI/Server/Testing：消费 `submit_message` 流、权限交互、AskUser 通道、Slash 入口调用 | 业务工具逻辑、Prompt 内容拼装 | 🔧 |
| **`observability/`** | 成本、结构化日志、指标 | 业务决策 | 🔧 Metrics 占位 |
| **`utils/`** | Abort、token 估算等无业务依赖的原语 | 领域策略 | 🔧 |

#### LLM 与会话

| 包 | 职责 | 不负责 | 现状 |
|----|------|--------|------|
| **`providers/`** | LLMProvider Protocol、注册路由、各协议 stream/normalize；原厂与 `*_compatible` | 工具执行、会话存储 | 🔧 兼容层与 Responses 待补 |
| **`session/`** | 运行时 `MessageStore`；`TranscriptWriter` 持久化抽象与多后端 | 跨会话 Memory（见 `memory/`） | 🔧 仅 LocalFile |

#### 工具

| 包 | 职责 | 不负责 | 现状 |
|----|------|--------|------|
| **`tools/`**（协议层） | Tool Protocol、Registry、Executor、ToolUseContext | 权限策略实现（委托 `permissions/`） | ✅ 协议；context 待瘦身 |
| **`tools/builtin/`** | 生产级内置工具实现（Bash/Read/Edit/…/AskUser/LSP/Worktree） | MCP 桥接工具（`mcp/`）、Skill/Agent 入口（可放本包或 skills/agents 再注册） | 📋 现为单文件演示 `builtin.py` |

#### 扩展面

| 包 | 职责 | 不负责 | 现状 |
|----|------|--------|------|
| **`prompt/`** | Section Protocol、Registry、组装优先级与静/动态边界；替换 `_build_system_prompt` | 发现 Rules/Memory 文件（消费 `rules/`/`memory/` 的已合并结果） | 📋 |
| **`rules/`** | 常驻指令发现（含 CLAUDE.md/AGENTS.md 兼容）、层级合并、条件 globs | 写入最终 prompt 字符串（交给 `prompt/` section） | 📋 |
| **`skills/`** | Skill 包发现/加载；**SkillTool** 供模型按需激活 | Plugin 分发格式（`plugins/` 可内嵌 skills） | 📋 |
| **`plugins/`** | 扩展包清单、启停、失败隔离；**组合**注册 Rules/Skills/Agents/Tools/Hooks/MCP/Commands | 实现具体 Tool/Agent 逻辑 | 📋 |
| **`commands/`** | Slash 解析与执行注册表；与自然语言分流 | Rich/HTTP 渲染（`adapters/`） | 📋 |
| **`settings/`** | default/user/project/policy/flag 合并；单一读入口 | 替代 `EngineConfig`（构造期仍用 EngineConfig；settings 可生成/覆盖其输入） | 📋 |
| **`permissions/`** | 会话模式、PermissionRule、denial 追踪、路径沙箱、Ignore、Bash 分类 | 具体 tool.execute 业务 | 📋 现散落在 context/cli/server |
| **`mcp/`** | MCP 配置、进程生命周期、tools/resources/auth 桥接到 ToolRegistry | 通用 Tool 协议定义 | 📋 |
| **`agents/`** | AgentDefinition 多 source、AgentTool、runner（子 Engine） | Teams/Coordinator（P2 可选，可另模块） | 📋 |
| **`memory/`** | 跨会话记忆存储与 memdir 约定；供 prompt section 注入 | 单次 transcript（`session/`） | P2 |
| **`checkpoint/`** | 文件编辑快照/回滚 | Git 本身 | P2 |

### 5.3 包依赖方向（禁止反向）

```text
adapters ──► core/engine ──► prompt, pipeline, recovery, providers,
                             tools, agents, session, permissions, …

plugins  ──► rules, skills, mcp, agents, commands, permissions, hooks(适配)
             （只组合注册，不反向被内核 import 具体插件）

tools/builtin ──► permissions, utils
permissions   ──► settings（可读合并结果）
prompt        ──► rules / memory 的「已加载视图」（只读）
core          ↛  adapters          ✗
prompt        ↛  tools 执行实现    ✗（可引用 tool schema 列表，不执行）
```

### 5.4 从现有代码迁移对照

| 现有 | 目标动作 |
|------|----------|
| `core/` `pipeline/` `recovery/` `hooks/` `adapters/` `observability/` `utils/` | **原位保留** |
| `providers/` `session/` | **原位扩展**（新文件，不改包名） |
| `tools/protocol|registry|executor` | **原位保留** |
| `tools/context.py` | **瘦身**：Checker 类型可 re-export；策略实现迁 `permissions/` |
| `tools/builtin.py` | **拆为** `tools/builtin/`；薄 re-export 过渡后删除 |
| `core/engine._build_system_prompt` | **抽到** `prompt/`，Engine 只调用组装器 |
| CLI/Server 内联 permission 闭包 | **迁到** `permissions/` + adapters 注入 Policy |
| （无） | **新建** prompt/rules/skills/plugins/commands/settings/permissions/mcp/agents/memory/checkpoint |

推荐节奏：Phase 1 做 `tools/builtin/` + `permissions/` + `prompt/` + `rules/`；Phase 2 起其余新建包；避免一次性大搬家。

### 5.5 与 §1.5 分层的对应

| 扩展分层 | 主要落点包 |
|----------|------------|
| Plugins 分发 | `plugins/` |
| 编排（子 Agent / Plan / Slash / Task） | `agents/` `commands/` `tools/builtin` |
| 外部工具 | `mcp/` `tools/builtin` |
| 按需能力 | `skills/` |
| 常驻约束 | `rules/` `permissions/` `settings/` |
| 提示组装 | `prompt/` |
| 引擎内核 | `core/` `pipeline/` `recovery/` `hooks/` `providers/` `session/` |

---



## 6. 非目标（短期内不做）

- 完整复刻 Claude Code / Cursor 的产品 UI（Ink TUI、Tab、Inline Diff 等）  
- 复刻 CC **100+ slash** 全量（只做 MVP 子集）  
- Anthropic 账号 OAuth、settings 云同步、Claude.ai 限额体系  
- Bridge / IDE 远程桥接、Remote Agent 云会话（默认不做；若做单独立项）  
- Cron / 定时触发、Slack/GitHub App 安装器等产品集成  
- `USER_TYPE=ant` / feature-flag 实验工具  
- Agent Teams / Coordinator（**默认为 P2 可选**，非 Phase 1–3 阻塞项）  
- 多租户 SaaS 计费与企业控制台  
- 自研模型训练或本地推理运行时  
- 中央插件市场（先本地/私有目录）

---



## 7. 变更记录


| 日期         | 说明                                                                                                                         |
| ---------- | -------------------------------------------------------------------------------------------------------------------------- |
| 2026-08-07 | 初版：对齐 `0.1.0` 现状                                                                                                           |
| 2026-08-07 | 会话持久化扩展；Provider 兼容矩阵                                                                                                      |
| 2026-08-10 | Rules / Plugins / 权限 / Hooks / Slash / Plan·Memory                                                                         |
| 2026-08-10 | 对照 ClaudeCode 源码加细：Prompt 组装、Agent 三件套、PermissionRule、AskUser、LSP、MCP Resources/Auth、Settings、Task、明确 Teams/Bridge/Cron 边界 |
| 2026-08-10 | §5 完整包树（内核+扩展面）与各包职责、依赖方向、迁移对照 |
| 2026-08-10 | 新增 [COMMIT.md](./COMMIT.md) Git 提交规范 |
| 2026-08-14 | Phase 0/1 落地：`permissions`/`prompt`/`rules` 包、生产级 `tools/builtin`、AskUserQuestion(CLI)、预算门控、CLI 配置外部化与密钥清理 |
| 2026-08-14 | Phase 2 推进：Settings 分层、messages_compatible、Metrics 最小可用、Skills+SkillTool、Plan 模式工具、SQLite Transcript、Slash MVP、TodoWrite |
| 2026-08-14 | Phase 2/3 推进：子 Agent 三件套、用户态 hooks.json、原厂 responses Provider、ContextCollapse 默认 summarizer |
| 2026-08-14 | Phase 2/3 推进：MCP stdio 客户端（tools/resources 桥接）、Worktree 进出工具、responses_compatible |
| 2026-08-14 | Phase 2/4 推进：Plugins 扩展包、json_schema 结构化输出闭环、Memory（memdir） |
| 2026-08-14 | Phase 4 推进：mypy 全量清零、Checkpoint、Task 工具族、CI（pytest+ruff+mypy） |
| 2026-08-14 | Phase 3/4 推进：Server 治理（鉴权/TTL/AskUser SSE）、Output Styles section |
| 2026-08-14 | Phase 1/2 补齐：LS/WebFetch(SSRF)/ToolSearch 工具、Slash /rules /skills /permission /mcp /plugins |
| 2026-08-14 | 补齐：WebSearch 工具、MCP 静态 token/headers、Skills enable/disable |
| 2026-08-14 | 补齐：fallback_model 切换、/memory /hooks slash、CLI sessions 会话列表 |


## 附录
可用的测试模型（密钥一律经环境变量注入，**不得硬编码入库**）：
```bash
model MiniMax-M3
base_url https://api.minimaxi.com 或 https://api.minimaxi.com/anthropic(messages协议)
# 通过环境变量提供密钥（CLI 或配置文件的读取约定见 adapters/cli.py）
# export NONO_API_KEY=... NONO_BASE_URL=https://api.minimaxi.com/v1 NONO_ADAPTER=openai NONO_MODEL=MiniMax-M3
```

测试配置示例：

```python
import os
provider_config = ProviderConfig(
        model_id="MiniMax-M3",
        adapter="openai",
        api_key=os.environ["NONO_API_KEY"],
        base_url="https://api.minimaxi.com/v1",
        extra_body={
            "reasoning_split": True,
            "thinking": {
                "type": "adaptive"
            }
        },
    )
```