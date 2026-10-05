---
title: Shell 工具重构方案
---

# Shell 工具重构方案

> 目标：把 `nono_agent/tools/builtin/bash.py` 从「单文件 + 正则黑名单 + 丢弃式截断 + 超时即杀」
> 演进为分层能力架构，吸收 DSH（`@deepseek-ai/dsh-tool-pwsh` / `dsh-pwsh-local` / `dsh-sandbox-windows-acl`）
> 的四项关键设计：**编码生产端钉死、流分离、溢出落盘、超时晋升后台**。
>
> 状态：**P0 / P1 已全部实施完毕**　｜　基线 commit：见 `git log -1`
>
> ⚠️ **本文是历史方案，不是当前实现的说明。** P0（编码钉死 / 流分离 / 溢出落盘）与
> P1（能力缝 / 后台作业 / Job 工具）均已落地，具体实现与最终形态见
> [Shell 工具设计](../architecture/shell-tool.md)（尤其是 §11 实施结果）。
> 下面 §0 的「现状盘点」描述的是**重构前**的代码，行号与结论基本都已失效；
> 唯一仍然有效的待办是 P2（模式分级替代分类器、Windows ConstrainedLanguage）。

---

## 0. 现状盘点（重构前基线，**已全部失效**）

> 下表是动手前的代码事实快照，仅作决策留痕。当前实现的模块结构、签名与行号请以
> `docs-site/docs/architecture/shell-tool.md` 为准。

| 事实 | 位置 |
|---|---|
| 单文件 204 行，类继承 `ToolBase` | `nono_agent/tools/builtin/bash.py:64` |
| `create_subprocess_shell(shell=True)`，过中间 shell | `bash.py:137` |
| **stdout/stderr 合并**成一路（`stderr=STDOUT`） | `bash.py:140` |
| `asyncio.wait_for(timeout=120)`，**超时即 kill** | `bash.py:33`、`:172` |
| `truncate_text(30_000)` 头尾各半，**中间内容永久丢失** | `bash.py:31`、`:188` |
| 编码在**消费端猜测**：utf-8 → locale → gb18030 → replace | `tools/builtin/_common.py:147-204` |
| `open_job` 在 spawn **之后**调用 | `bash.py:137` → `:153` |
| jobs board 是**模块级全局** `_jobs: dict` + `threading.Lock` | `jobs/board.py:16-20` |
| **无任何模型侧 job 查询工具**（17 个 builtin 中不存在） | `tools/builtin/__init__.py:47-70` |
| `PathSandbox` 只做**词法路径校验**，对命令内容零约束 | `permissions/sandbox.py:25-86` |
| 安全全靠 `BashClassifier` 正则黑名单（POSIX 习惯命令） | `permissions/bash_classify.py:27-42` |
| 常量硬编码，无法配置 | `bash.py:31`、`:33` |
| `create_builtin_tools()` 零参，3 个调用点 | `builtin/__init__.py:47`、`cli/repl.py:65`、`server/session_manager.py:40` |
| **server 模式无交互确认，所有写操作直接 DENY** | `apps/server/.../session_manager.py:28-34` |

### 0.2 已核实的框架约束（影响设计，必须遵守）

| 约束 | 位置 | 对方案的影响 |
|---|---|---|
| 工具层**无结构化输出**，只有 `ToolResult.content` | `tools/protocol.py:94-96` | 退出码、spill 路径只能编码进文本 |
| **`ui` 在入模型上下文时被丢弃** | `core/loop.py:680-700`、`executor.py:311-330` | **spill 路径必须放 `content`，放 `ui` 等于没放** |
| 上下文类名是 **`ToolUseContext`**（非 ToolContext），frozen dataclass | `tools/context.py:32-33` | 加字段要走 `with_extra`（`:163-178`，手工逐字段复制） |
| **executor 不做超时**，只做 abort 取消 | `tools/executor.py:113-115` | 超时语义完全归工具自理 |
| abort 时 executor 对 pending task 调 `task.cancel()` | `executor.py:113-115` | 晋升后进程已脱离该 task，需自行维持 kill 通路（P1-2 核心难点） |
| **存在第二层截断**：`ToolResultBudgetStage`，20,000 token，保头 500 / 尾 200 token | `pipeline/tool_result_budget.py:18-24` | spill 路径必须落在**尾部**才不被二次截断吃掉 |
| `QueryEngine` 有约 25 个 service 字段，构造期可注入 | `core/engine.py:164-248` | **P1 执行器的注入点**（不需要新容器） |
| **无通用 service registry / capability 容器** | — | 确认不引入 Cordis；用 `QueryEngine` 字段即可 |
| `ToolUseContext.extra` / `with_extra` 是唯一通用附加通道（engine 未使用） | `context.py:67,163` | P1 的**轻量替代方案**：不注入 executor，只挂 registry |

### 0.1 隐含的设计冲突

`session_manager.py:28` 的 `deny_all_permission_checker` 在 server 模式下返回 `False`（拒绝）。
`BashTool.should_prompt_for_permission` 对 `WRITE`/`DANGEROUS` 返回 `True`（`bash.py:112-119`），
经 `executor.py:202-217` 的链路后 → **server 模式下所有写类 bash 命令一律被拒**。

这不是 bug，是「无沙箱 ⇒ 不能放行」的必然结果。它正是 P2 的动机：
**要放开 server 模式的命令能力，前提是先有强制边界**，而不是继续加固正则。

---

## 1. 目标架构

对齐 DSH 的能力缝（capability seam），但适配 nono-agent 的 `ToolBase` 体系，不引入 Cordis 式 IoC。

```
┌─ tools/builtin/ ─────────────────────────────────┐
│  bash.py        薄编排：schema / 审批 / 渲染        │
│  job_output.py  模型读后台作业          [P1]       │
│  job_kill.py    模型停后台作业          [P1]       │
└──────────────────────┬───────────────────────────┘
                       │ context: ToolUseContext (frozen, context.py:32)
                       │  ├─ .shell  : ShellExecutor   [P1 新增字段]
                       │  └─ .shell_jobs: JobRegistry [P1 新增字段]
┌─ shell/ ─────────────▼───────────────────────────┐
│  types.py     ShellSpec / ShellProcess / Outcome   │
│  executor.py  ShellExecutor 协议 + 语义契约        │
│  local.py     LocalShellExecutor（asyncio）        │
│  spill.py     字节 ring + 溢出落盘        [P0]     │
│  jobs.py      JobRegistry 实例            [P1]     │
│  sandbox.py   沙箱包装器                  [P2]     │
└───────────────────────────────────────────────────┘
```

**注入路径**（不动 `ToolBase` 契约）：
`QueryEngine.__init__`（`core/engine.py:164-248`）持有 `_shell_executor` / `_shell_jobs` →
`ToolUseContext` 新增两个字段（`context.py:32-67`）→
`with_extra` 已是手工逐字段复制（`context.py:163-178`），**新增字段必须同步补一行** →
engine 构造 context 处（`engine.py:451-462`）注入。

> **轻量备选**：若不想给 `ToolUseContext` 加字段，可只用现有的 `extra: dict`（`context.py:67`）
> 通道（engine 目前未使用它）。P0 完全不需要碰 context。

**核心契约**（对应 DSH `dsh-shell/lib/index.js:68-82`）：

- `ShellProcess.result()` **只对基础设施故障 reject**；非零退出、超时、abort 都 resolve
- `read_output(from_offset)` **增量**：连续读不重复；有损时报告 `lossy` + `spill_paths`
- `on_expiry` 语义二分：`"kill"` 到期杀 / `"none"` 不设 deadline（晋升的前提）

---

## 2. 分阶段实施

### P0 — 编码钉死 + 流分离 + 落盘（约 1 天）

**动机**：收益/风险比最高，不改架构、不改工具签名，纯粹修三个已知缺陷。

#### P0-1 编码生产端钉死

新建 `shell/preamble.py`：

```python
_WINDOWS = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)
_POSIX = "export PYTHONIOENCODING=utf-8; "   # 实际落地值（preamble.py:37）
# 注：不设 LC_ALL=C.UTF-8 / LANG —— macOS 上会告警

def wrap_command(command: str, *, platform: str | None = None) -> str:
    """前置编码声明。用 `;` 分隔而非换行，保证 PowerShell 错误行号不错位。"""
```

- 保留 `_common.decode_command_output` 作为**兜底**（应对子进程忽略声明的情况）
- Windows 上 `create_subprocess_shell` 走 `cmd.exe`，无法直接前置 PS 声明
  → 需改为**显式 argv**：`["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", wrapped]`（`dialect.py:25` `_PS_ARGS`）
  （这一步顺带消除了 `shell=True` 的引号转义层）

**改动文件**：`shell/preamble.py`（新）、`bash.py`

#### P0-2 stdout/stderr 分离

- `bash.py:139-140` 改为 `stderr=PIPE`
- 新增 `shell/streams.py`：`SeparatedStreams`（stdout/stderr 各自 ring + spill）
- 渲染成 DSH 同款：
  ```
  <stdout 正文>
  [stderr]
  <stderr 正文>
  ```
- 保留 `merge_streams: bool = False` 配置项，向后兼容旧行为

#### P0-3 溢出落盘

`shell/spill.py`：

```python
class SpillWriter:
    """字节 ring + 溢出落盘。对应 DSH 的 maxOutputBytes / maxSpillBytes 双预算。"""
    def __init__(self, max_bytes: int, spill_path: Path | None, max_spill_bytes: int = 64 << 20): ...
    def write(self, data: bytes) -> None: ...
    def read_from(self, offset: int) -> OutputDelta: ...  # {text, next_offset, lossy, spill_path}
    def close(self) -> Path | None: ...
```

- 内存 `max_output_bytes`（默认 30k）+ 落盘 `max_spill_bytes`（默认 64MB）
- 落盘位置：`<session_dir>/spill/<session_id>/<job_id>/{stdout,stderr}.log`
- **回报路径给模型**：`[output truncated; full output: <path>]`

**两条容易踩的坑**（已核实）：

1. **路径必须放 `ToolResult.content`，不能放 `ui`** —— `ui` 在 `core/loop.py:680-700`
   组装 tool_result 消息时被丢弃，模型永远看不到。
2. **截断标记必须落在输出尾部** —— `ToolResultBudgetStage`
   （`pipeline/tool_result_budget.py:18-24`）会对超过 20,000 token 的 tool_result
   再截一次，只保留**头 500 / 尾 200 token**。所以 `[output truncated; ...]`
   这行必须拼在正文**之后**（DSH 的 `streamText` 正是这么做的）。

> 反过来说：spill 机制的价值被这层二次截断**放大**了 ——
> 模型即使只看到头尾两段，也知道完整内容在哪个文件，可以自己去读。

#### P0-4 配置化

`core/config.py` 新增：

```python
class ShellConfig(BaseModel):
    timeout_s: float = 120.0
    max_timeout_s: float = 600.0
    max_output_bytes: int = 30_000
    max_spill_bytes: int = 64 * 1024 * 1024
    merge_streams: bool = False
    env_overrides: dict[str, str] = Field(default_factory=dict)  # NO_COLOR / PAGER 等
```

`EngineConfig` 加 `shell: ShellConfig = Field(default_factory=ShellConfig)`。

`create_builtin_tools(config: EngineConfig | None = None)` —— 保持零参可调用（向后兼容），
3 个调用点（`cli/repl.py:65`、`server/session_manager.py:40`、测试）改为传 config。

**P0 验收**
- `bash "python -c \"print('中文')\""` 在 Windows 上无乱码
- 产生 >30KB 输出的命令，结果里含可读 spill 路径，且该文件内容完整
- stderr 出现在独立的 `[stderr]` 段
- `pytest tests/ -q` 全绿

---

### P1 — 能力缝 + 超时晋升 + 模型侧作业控制（约 3-4 天）

#### P1-1 抽出 `ShellExecutor` 能力层

`shell/types.py` 定义 `ShellSpec` / `ShellProcess` / `ShellOutcome`；
`shell/local.py` 实现 `LocalShellExecutor`。
`BashTool` 改为只做：参数校验 → 审批 → 调 `ctx.shell` → 渲染。

**为什么现在做**：P2 的沙箱包装器需要「继承 local 执行器、只覆写 argv 构造」这个扩展点。
不做 P1，P2 就只能在 `bash.py` 里塞 `if sandbox_mode:` 分支。

#### P1-2 超时晋升后台（**本次重构风险最高的一步**）

**为什么 executor 帮不上忙**：`StreamingToolExecutor` 在 abort 时对 pending task 调
`task.cancel()`（`executor.py:113-115`），且**不做任何超时**（`executor.py:159-283` 的
`_execute_one` 里没有 timeout）。晋升后的进程已经脱离该 task，必须自建 kill 通路。

**三个必须先解决的语义冲突**：

1. **abort ≠ timeout**
   - `context.signal` 触发（用户主动中断）→ **必须 kill**
   - `timeout` 到期（默认 120s）→ **不能 kill**，要晋升
   - 现状 `bash.py:165-169` 的 `_watch_abort` 与 `bash.py:173-175` 的超时分支都 kill，
     晋升路径需要把这两者彻底分开

2. **abort 监听的生命周期必须移交**
   现状在 `finally: abort_task.cancel()`（`bash.py:183-185`）。晋升后 `execute` 立即返回，
   abort 监听必须**移交 job registry 持有**，否则用户中断时后台进程收不到 kill。

3. **engine teardown 要能收干净**
   没有移交的 registry 会在会话销毁时留下孤儿进程。engine 关闭路径需要 `registry.kill_all()`。

改造后的 `execute` 骨架：

```python
if self._promote_on_timeout and registry is not None:
    job = registry.start(spec, kill=lambda: proc.kill())   # 先注册，spawn 在 run() 内
    try:
        outcome = await asyncio.wait_for(proc.wait(), timeout)
    except TimeoutError:
        registry.attach_abort_watch(job.id, context.signal)    # 移交，不 cancel
        return self._render_promoted(job)                   # 进程继续跑
    registry.detach_abort_watch(job, None)
    return self._render_foreground(outcome)
else:
    # 旧行为：on_expiry="kill"
```

返回文本对齐 DSH：
```
[still running after 120000ms; moved to background job <id>]
The command keeps running in the background. You will be notified when it finishes;
read newer output with job_output, stop it with job_kill.
```

> 注意：晋升结果本身会被 `ToolResultBudgetStage` 处理，这段提示文本很短，不会被截断。

#### P1-3 `JobRegistry` 实例化

`jobs/board.py` 当前是模块级全局（`_jobs: dict` / `_lock` / `threading.Timer`），
存在多会话串扰与测试靠 `reset_jobs()` 清理的问题。

改为 `shell/jobs.py` 的 `JobRegistry` 实例：
- 挂在 `QueryEngine`（`core/engine.py:164-248`）→ 经 `ToolUseContext` 传给三个工具共享同一实例
- `threading.Timer` → asyncio task（当前同步定时器混在事件循环里，本身就是隐患）
- `set_output` 全量替换 → 改为增量 `write(offset, data)`，与 P0-3 的 spill ring 共用同一数据结构
- **保留 `subscribe` 订阅接口**（`board.py:138-157`），server 侧 SSE 消费路径不能断
- `kill_job`（`board.py:114-128`）保留并加幂等保证

#### P1-4 新增 `job_output` / `job_kill` 工具

```python
class JobOutputTool(ToolBase):
    _name = "job_output"
    _has_write_side_effects = False      # 只读
    # 参数：job_id (required), from_offset (optional)

class JobKillTool(ToolBase):
    _name = "job_kill"
    _has_write_side_effects = True       # 停进程 = 状态变更
```

- 三者必须在 `create_builtin_tools()` 里**共享同一个 registry 实例**才能工作
- 输出同样只能走 `content`（`ui` 会被丢弃）
- 注意 `JobKillTool` 有写副作用 → 在 server 模式下会命中 `deny_all_permission_checker`
  被拒，需要显式白名单或在 `session_manager.py:28` 调整

**P1 验收**
- `sleep 300` 触发晋升，进程未被杀；`job_output` 能读到增量
- `job_kill` 能终止后台进程
- 中途用户 abort 能杀掉后台进程
- 跨会话不串扰（两个 session 各起一个 job，`job_output` 只看得到自己的）

---

### P2 — 沙箱分级（约 1-2 周）

> **状态：未实施。** P2 是本文**唯一仍然有效**的待办项。`permissions/bash_classify.py`
> 仍是唯一的命令分类器，`bash.py` 仍在调用它；代码里也没有 ConstrainedLanguage 相关实现。

**前置**：P1 必须先落地。没有能力缝，沙箱只能写成 `bash.py` 里的分支。✅ 已满足。

#### P2-1 建议先做的（低成本）

**(a) 模式分级替代单一分类器**
现有 `BashClassifier` 只有 SAFE/WRITE/DANGEROUS 三档，POSIX 习惯命令，Windows 完全失准
（`Remove-Item -Recurse -Force`、`forfiles`、`python -c "import shutil"` 全部漏网）。
改为**权限模式驱动**：模式（`default` / `accept_edits` / `plan` / `bypass`，
`permissions/policy.py:23-29`）× 路径边界，而不是命令字符串匹配。
命令文本分类器**降级为提示信息**，不再是安全边界。

**(b) Windows ConstrainedLanguage**
`read-only` 模式下前置：
```powershell
$ExecutionContext.SessionState.LanguageMode = 'ConstrainedLanguage'
```
阻断 `.NET` 静态调用 / `Add-Type` / COM / 反射。零 Win32 API，成本极低。
并把这些边界**写进工具描述**（对齐 DSH `dsh-tool-pwsh/lib/index.js:257` 的做法），
让模型知情调整策略。

#### P2-2 需要单独立项的（高成本）

Windows 受限令牌 + capability SID + Low integrity 标签
（对标 `dsh-sandbox-windows-acl`）。需要 ctypes 调 Win32（`CreateRestrictedToken`、
`SetSecurityInfo`、Job Object），且要处理硬链接别名、其他 AppContainer 工具 ACL 不可读等残留缺口。

**建议：先在 P0/P1 上线并收集真实反馈，再决定是否投入 P2-2。**

---

## 3. 测试策略

| 层级 | 内容 |
|---|---|
| 单元 | `spill.py` 环形/落盘边界；`preamble.py` 平台分流；`streams.py` 渲染 |
| 工具契约 | `job_output` 增量不重复；`job_kill` 幂等；跨会话隔离 |
| 集成 | 真实子进程：退出码、超时晋升、abort、中文输出、stderr 分离 |
| 回归 | `tests/test_builtin_tools.py:31` 现有断言不得破坏 |
| 平台 | Windows / POSIX 参数化（`platform` 显式注入，不依赖 `sys.platform` 直读） |

新增 `tests/test_shell_*.py` (flat) 包。子进程统一用短小的跨平台命令（`python -c`），
不 mock `asyncio` 内部 —— 真实进程才能暴露管道/编码/终止的真实行为。

---

## 4. 风险与回滚

| 风险 | 缓解 |
|---|---|
| P1-2 晋升逻辑改错导致进程泄漏 | 引擎 teardown 时 `registry.kill_all()`；加泄漏检测测试 |
| 去掉 `shell=True` 改变命令语义（管道/重定向/通配） | 显式选 shell：Windows 用 `cmd.exe /c` 或 PowerShell，POSIX 用 `/bin/sh -c`；保留回归测试 |
| 配置化改动波及 3 个调用点 | 参数可选、默认不变，旧调用点不传也能跑 |
| spill 文件堆积 | 会话结束清理 + 保留上限；路径放会话目录而非全局 tmp |
| 行为变更影响既有 prompt | 渲染格式变更需同步更新 `prompt/sections/tools_guidance.py` |

**回滚**：每阶段独立可发布。P0 只改内部实现、工具签名不变 → 可单独回滚。
P1 引入新工具（`job_output` / `job_kill`）→ 可通过配置关闭晋升（`promote_on_timeout=False`）退回旧行为。

---

## 5. 明确不做的事

- **不移植 `BashClassifier` 正则的「加固版」** —— 绕过成本近零，负收益
- **不引入 Cordis / IoC 容器** —— nono-agent 已有 `ToolContext` 作为能力载体，再加一层是过度设计
- **P0/P1 不碰权限决策链路**（`executor.py:202-217`）—— 避免与 P2 沙箱改动混在一起
- **不移植 PTY / 持久化 shell** —— 依赖 `pywinpty`/`pty` 跨平台，收益依赖具体使用场景，另行评估

---

## 6. 建议的交付顺序

```
P0（1天）    ──► 立即可上线，独立收益
P1（3-4天）  ──► 需要 P0 的 spill ring 与流分离作为地基
P2-a（2-3天）──► 解锁 server 模式的命令能力
P2-b（1-2周）──► 暂缓，收集 P0-P2a 反馈后决策
```

**先做 P0**。它零架构风险、零签名变更，却解决了「乱码」和「输出丢失」这两个每天都在损耗的问题。
