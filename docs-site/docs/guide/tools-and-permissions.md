---
title: 工具与权限
---

# 工具与权限

## 1. 23 个内置工具

`create_builtin_tools()` 返回：

| 分类 | 工具 |
|---|---|
| **Shell** | `bash` |
| **后台作业** | `job_output`、`job_kill` |
| **文件** | `read_file`、`write_file`、`edit_file` |
| **检索** | `glob`、`grep`、`list_directory` |
| **开发** | `lsp`、`web_fetch` |
| **交互** | `ask_user_question`、`todo_write`、`tool_search` |
| **模式** | `enter_plan_mode`、`exit_plan_mode`、`enter_worktree`、`exit_worktree` |
| **子任务** | `task_create`、`task_list`、`task_get`、`task_update`、`task_stop` |

!!! note "只有零配置原语在内置里"
    搜索、图像生成这类**要密钥、要开关**的厂商能力一律走工具插件（`tavily` / `minimax-image`），
    默认关闭。内置工具表里不该塞一堆不配置就报错的东西。

    `web_fetch` 是例外说明：它是**原语**不是厂商能力 —— 给个 URL 就能读，零配置、本机直连。
    它与 `tavily_extract` 不等价：后者要密钥、会重整正文，而且 **URL 会发给第三方**。

### 工具默认值是 fail-closed

| 属性 | 默认 | 含义 |
|---|---|---|
| `is_concurrent_safe` | `False` | 默认不并发安全，必须显式声明才能进并行组 |
| `has_write_side_effects` | `True` | 默认假定有写副作用，因此默认要权限确认 |

只读工具（`read_file` / `glob` / `grep`）要显式标 `_has_write_side_effects = False`，
真正无副作用的才标 `_is_concurrent_safe = True`。

### 共同约定

- **路径沙箱**：文件工具的路径都过 `PathSandbox`（注入时生效）。
- **Ignore**：列举/搜索类工具跳过 `.gitignore` / `.nonoignore` 命中的路径。
- **编码**：读文本 UTF-8 优先，失败回退本机默认编码与 gb18030；`edit_file` 写回 UTF-8。
- **出网**：`web_fetch` 与工具插件的 HTTP 执行器共用 SSRF 闸门 `nono_agent/utils/net_guard.py`
  （拒绝私网 / 回环 / 链路本地 / 云元数据地址）。
- **MCP 工具**：命名为 `mcp__<server>__<tool>`，沿用全局权限模式。

### bash 的执行语义

`bash` 走能力缝 `nono_agent/shell/`，工具只描述执行与渲染结果，进程机制由可替换的执行器承担：

| 行为 | 说明 |
|---|---|
| 方言 | 显式 argv，不经中间 shell。Windows 走 PowerShell（`auto` 探测 pwsh/powershell），POSIX 走 `sh -c` |
| 编码 | 生产端前置声明（前置到同一条 argv），消费端保留回退兜底 |
| 流分离 | stdout / stderr 分开收集，渲染时 stderr 单独一段 |
| 超限 | 超过 `max_output_bytes`（默认 30 KB）时溢出部分**落盘**，不丢弃 |
| 超时 | 默认 `promote_on_timeout`：超时**不杀进程**，转成后台作业，模型可用 `job_output` 读、`job_kill` 停 |
| 进程组 | Windows 上整组终止，不留孤儿进程 |

配置项在 `EngineConfig.shell`（`ShellConfig`）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `dialect` | `auto` | `auto` / `powershell` / `pwsh` / `cmd` / `bash` / `sh` |
| `timeout_s` | 120.0 | 默认超时（秒） |
| `max_timeout_s` | 600.0 | `--timeout` 的上限 |
| `max_output_bytes` | 30000 | 内存里保留的输出上限，超出落盘 |
| `max_spill_bytes` | 67108864 | 落盘上限 |
| `merge_streams` | False | 是否把 stderr 并回 stdout |
| `promote_on_timeout` | True | 超时转后台（关掉则直接 kill） |
| `env_overrides` | `{}` | 追加到子进程的环境变量 |

详见 [Shell 工具设计](../architecture/shell-tool.md)。

---

## 2. 权限

### 四种权限模式

`PermissionMode`（`nono_agent/permissions/policy.py`）：

| 模式 | 行为 |
|---|---|
| `default` | 写/执行需确认（`ASK`），读自动允许 |
| `accept_edits` | **与 `default` 同**：读允许，写仍需确认 |
| `plan` | 禁止任何写副作用（只读） |
| `bypass` | 显式危险模式，全部允许（**但 `deny` 规则、安全底线、工具的绝对否决仍生效**） |

!!! warning "`accept_edits` 目前不自动允许任何编辑"
    `policy.py` 里 `ACCEPT_EDITS` 与 `DEFAULT` 两个分支返回值逐字相同，
    行内注释写明「编辑类工具在工作区内的自动允许由 rules 层表达」。
    也就是说这个模式名现在**没有比 `default` 更宽松的行为**。
    想要「工作区内编辑免确认」，用可落盘规则表达：

    ```json
    { "action": "allow", "tool_name": "edit_file", "path": "**" }
    ```

切换方式：会话内 `/permission`，或用 `enter_plan_mode` / `exit_plan_mode` 工具，
或设环境变量 `NONO_PERMISSION_MODE`（`default` / `accept_edits` / `plan` / `bypass`）。

模式切换是**两段式**的：请求时立刻记录 `permission/onchange` 事件，运行时仍用旧模式，
到下一个 turn / step 边界才 `commit_pending` 真正生效并写 `permission/changed`。这样
「请求切换」和「切换已生效」在事件流里是两个可区分的事实。

`exit_plan_mode` 恢复的是**进入 plan 之前的模式**，不是拍脑袋回 `default`。
它声明了 `always_requires_approval` —— 模型不能自己解开用户设下的只读限制，
退出计划模式要有人点头。

### 权限拦截链

模型说「我要执行这个命令」，从这句话到命令真的跑起来，中间隔着一道检查。
这道检查就是**权限拦截链**（`nono_agent/permissions/gate.py`）——
顺序写死在代码里，而不是散在各层或写在文档里。顺序只有一处定义，
才谈得上"不可能被绕过"。

```text
绝对收紧层    任何自动放行都越不过
  0   hook_pre          PreToolUse hook 否决        → DENY
  1a  rule_deny         显式禁止规则                 → DENY
  1c  tool_verdict      工具的绝对否决               → DENY
  1e  tool_always_ask   工具声明必须人工             → ASK
  1g  safety_baseline   安全底线                     → DENY / ASK
  1h  plan_mode         plan 模式只读                → DENY

用户标记层    可被「不再询问」白名单部分覆盖
  1b  rule_ask          「这个工具每次都问」         → ASK
  2a  auto_approve      「这些命令不再问」           → ALLOW
  1f  rule_ask_content  「这类操作要问」             → ASK

放宽层
  2b  mode_bypass       全部放行模式                 → ALLOW
  2c  rule_allow        总是允许规则                 → ALLOW

兜底层
  3   default           没被声明就当要问             → ASK / ALLOW
```

从顺序里直接读出三条性质：

1. **禁止永远赢** —— `deny` 在最前，`bypass` 也绕不过。
2. **人的显式配置高于省事模式** —— 用户明写的 `ask` 规则排在 `bypass` 之前。
3. **有些边界连用户自己都不能放开** —— 安全底线与工具的绝对否决排在一切自动放行之前。

「软 ASK / 硬 ASK」不靠标志位区分，**靠位置**：收紧层的 ASK 谁都动不了，
兜底层的 ASK 可被 `bypass` 放宽。

!!! note "hook 与检查链串联，不并联"
    `before_tool` hook 说拒绝 → 拒绝；说允许 → **继续走完整条链**，
    不能跳过安全检查。业务方能加规则，但不能拆规则。
    hook 可以用退出码（非零 = 拒绝）或 stdout 的 JSON 表达三态判定：

    ```json
    {"decision": "ask", "reason": "这条命令需要人工确认", "rule": "my-rule"}
    ```

### 规则格式

```json
{
  "mode": "default",
  "rules": [
    { "action": "allow", "tool_name": "read_file", "path": "src/**" },
    { "action": "deny",  "tool_name": "bash", "bash_pattern": "rm -rf" },
    { "action": "ask",   "tool_name": "bash", "bash_pattern": "npm publish" }
  ],
  "auto_approve": [
    { "action": "allow", "tool_name": "bash", "bash_pattern": "^git status" }
  ]
}
```

两套清单，语义不同，不要混：

| 清单 | 作用 | 优先级 |
|---|---|---|
| `rules` | 「这个操作本身有多危险」 | `deny` > `ask` > `allow` |
| `auto_approve` | 「不再询问」白名单 —— 用户显式授权过的操作 | 命中即放行，**可覆盖 `ask` 规则** |

`auto_approve` 覆盖得了 `ask` 规则，但**覆盖不了** `deny`、工具的绝对否决、
安全底线与 plan 模式 —— 用户授权过某条命令，不等于授权过读密钥。

注意 `ask` 规则分两级，位置不同：

- **工具级**（只声明 `tool_name`）= 「这个工具每次都问」→ `auto_approve` 覆盖不了
- **内容级**（声明了 `path` 或 `bash_pattern`）= 「这类操作要问」→ `auto_approve` 能覆盖

所以「bash 每次都问，但 `git status` 不要再问」要写成内容级的 `ask` 规则，
而不是工具级的。

### 安全底线

安全底线是**连 `bypass` 模式都免疫**的边界，分两档：

| 档 | 对象 | 行为 |
|---|---|---|
| `DENY` | 数据目录下的 `credentials` | 直接拒绝，不询问 |
| `ASK` | `.git/`、`.nono-agent/`、`~/.ssh/`、`.env`、`*.pem` 等 | 走一次询问 |

数据目录的 `credentials` 是密钥文件 —— 模型读一次就等于把密钥带进上下文，
之后每次对话都在日志里躺着，所以这一档是硬拒。

底线与沙箱的分工：`PathSandbox` 管**能不能访问**（越界即拒），
底线管**能访问但要不要问**。两者都要 —— 沙箱管不到工作区内部的 `.git/`，
底线管不到工作区之外的路径。

清单在 `nono_agent/permissions/baseline.py`，后续按需扩充。

### 工具侧协议

工具不只是"被调用"，它还要对"这次调用能不能执行"表态：

```python
def evaluate_permission(self, input_params, context) -> PermissionDecision:
    """本次调用的自评 —— 可看入参动态判断。"""

@property
def always_requires_approval(self) -> bool:
    """本工具任何模式下都必须由人确认。"""
```

`BashTool` 按命令风险分级自评：`SAFE` → `ALLOW`（`ls` 不必打扰用户）、
`WRITE` → `ASK`、`DANGEROUS` → `ASK`（**不直接拒** —— 危险命令的具体后果由用户判断，
模型没资格替用户决定 `rm -rf` 就不该跑）。

没被声明的按更危险的情况处理：`evaluate_permission` 拿不到就当有写副作用（要问），
`always_requires_approval` 拿不到就当不需要。漏写规则的后果应该是"多问一次"，
而不是"漏放一次"。

### 无人值守（弹不出审批窗）

链判出 `ASK` 之后，需要有人回答。没有交互通道时（后台 agent、无人值守服务），
**没有人能批准 → 按拒绝处理**（fail-closed）。

放行的口子只有一个：**用户 hook 明确放行**。`before_tool` hook 返回
`{"decision": "allow"}` 时，这次调用不必再问 —— 但链上更靠前的判定
（`deny` 规则、工具的绝对否决、安全底线、plan 模式）照常生效，hook 不能拆规则。

另一种放行方式是落盘的 `allow` / `auto_approve` 规则 —— 那是显式配置，
不是绕过。

### 拒绝追踪

权限拒绝由 `DenialTracker` 记录，回灌到 `SDKResultMessage.permission_denials`，
同时在事件流里留一条 `permission/denial` 审计事件。
事件的 `rule` 字段记的是**真正命中的规则标识**，`step` 字段记的是
拦截链上命中的是哪一步 —— 光有一句"权限被拒绝"没法复盘。
**`permission/denial` 本身是审计资产，完整保留不脱敏。**

### 产品层的差异

| 入口 | 权限行为 |
|---|---|
| CLI | 交互式询问（`permission_checker` 回调） |
| Server / 桌面 | `make_permission_checker(session_id)` **按会话挂起询问**：`GET /app/permissions` 拉取待答项、`POST /app/permissions/{request_id}/answer` 回填（可选 `NONO_PERMISSION_TIMEOUT`，超时按拒绝处理） |

注意 `SessionManager.checker_for()`（`engine/session_manager.py:142-146`）**优先**用
`permission_checker_factory`，只有调用方直接调 `create_server_app()` 且不传任何 checker 时，
才落到默认的 `deny_all_permission_checker`（恒拒写副作用 + 对 `_SESSION_SCOPED_TOOLS`（`job_kill`）放行）。
真实产品链路走的是挂起询问，桌面端对写工具是弹窗而不是硬拒。

---

## 相关页面

- [配置与密钥](../getting-started/configuration.md)
- [新增内置工具](../development/add-builtin-tool.md)
- [新增工具插件](../development/add-tool-plugin.md)
- [Shell 工具设计](../architecture/shell-tool.md)
- [会话与记忆](session-and-memory.md)
