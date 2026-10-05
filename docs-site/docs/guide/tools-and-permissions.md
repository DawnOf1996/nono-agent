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
| `bypass` | 显式危险模式，全部允许（**但显式 `deny` 规则仍生效**） |

!!! warning "`accept_edits` 目前不自动允许任何编辑"
    `policy.py:174-187` 里 `ACCEPT_EDITS` 与 `DEFAULT` 两个分支返回值逐字相同，
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

### 决策链路

```text
PermissionPolicy.check(tool_name, params, has_write_side_effects)
    ↓
  deny 规则命中？          → DENY
  allow 规则命中？         → ALLOW
  ask 规则命中？           → ASK
  未命中 → 模式默认         → 按 default/accept_edits/plan/bypass
    ↓
ASK 或未注入 policy
    ↓
permission_checker 交互回调（CLI 询问 / Server 拒绝）→ ALLOW / DENY
```

规则优先级固定为 **`deny > allow > ask > 模式默认`**。即使在 `bypass` 模式下，显式 `deny` 规则依然被尊重。

权限规则格式：

```json
{
  "mode": "default",
  "rules": [
    { "action": "allow", "tool_name": "read_file", "path": "src/**" },
    { "action": "deny",  "tool_name": "bash", "bash_pattern": "rm -rf" }
  ]
}
```

### 路径沙箱

`PathSandbox` 做词法路径校验，防止工具读写逃出工作区。注意它**不约束命令内容** ——
`bash` 里跑什么命令由 `permissions/bash_classify.py` 的分类器把关（SAFE / WRITE / DANGEROUS 三档，
按 POSIX 习惯命令匹配）。从正则黑名单升级到权限模式驱动是 P2 计划项，尚未实施。

### 拒绝追踪

权限拒绝由 `DenialTracker` 记录，回灌到 `SDKResultMessage.permission_denials`，
同时在事件流里留一条 `permission/denial` 审计事件。**`permission/denial` 本身是审计资产，完整保留不脱敏。**

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
