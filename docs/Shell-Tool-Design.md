# Shell 工具详细设计

> 配套文档：`docs/Shell-Tool-Refactor-Plan.md`（分期与动机）
> 本文给出**可直接实施**的粒度：模块划分、接口签名、逐文件改动、测试用例、提交拆分。
>
> 范围：**P0 + P1**。P2（OS 级沙箱）见 §9 待决策项。
> 状态：待评审　｜　基线：nono-agent 当前 master

---

## 1. 硬约束清单（已逐条核实）

这些不是建议，是**改动必须绕开**的既有事实。违反其中任何一条都会产生回归。

| # | 约束 | 位置 | 后果 |
|---|---|---|---|
| C1 | `ToolResult.ui` 在组装 tool_result 消息时被丢弃 | `core/loop.py:681-689`、`executor.py:311-330` | spill 路径放 `ui` → 模型永远看不到 |
| C2 | 存在第二层截断：20,000 token，保头 500 / 尾 200 token | `pipeline/tool_result_budget.py:17-24` | 截断提示必须落在正文**尾部** |
| C3 | 工具层无结构化输出，只有 `ToolResult.content` | `tools/protocol.py:94-96` | 退出码/spill 路径只能编码进文本 |
| C4 | `ToolUseContext` 是 frozen dataclass，`with_extra` 手工逐字段复制 | `tools/context.py:32-33,163-178` | 加字段必须同步补复制行 |
| C5 | executor 不做超时；abort 时对 pending task 调 `task.cancel()` | `executor.py:113-115`、`159-283` | 超时语义归工具自理；晋升后进程脱离 task |
| C6 | `CREATE_NO_WINDOW` 全仓唯一出处 | `utils/process.py:15-19`（bash.py:142 已用） | 新增 spawn 路径必须继续复用 `hidden_kwargs()` |
| C7 | `nono_agent/` 内 platform 分支只有 3 处，惯例是下沉到 `utils/` 单点函数 | `utils/process.py:17`、`mcp/runtimes.py:542,547,668` | 方言分流应做成 `utils/` 或 `shell/` 单点，**不要散进 bash.py** |
| C8 | 测试 71 个文件**扁平**放 `tests/`，无子包 | `tests/`、`pyproject.toml:45-48` | 新测试文件命名 `test_shell_*.py`，不建 `tests/test_shell/` |
| C9 | `conftest.py` 4 个 autouse 夹具已把 `DEFAULT_SESSION_DIR` 指向 tmp | `tests/conftest.py:36-43` | spill 放会话目录 → **自动获得测试隔离** |
| C10 | `create_builtin_tools()` 零参，3 个调用点 | `builtin/__init__.py:47`、`cli/repl.py:64-66`、`server/.../session_manager.py:37-42` | 加配置须保持零参可调用 |
| C11 | server 模式 `deny_all_permission_checker` 恒拒 | `apps/server/.../session_manager.py:28-34` | 新增写副作用工具会被直接拒（见 §5.5） |
| C12 | `active_session_dir()` 是进程级全局，由 `use_session_dir()` 切换 | `session/transcript.py:74-82` | spill 路径走 `active_session_dir()` 而非自建目录 |
| C13 | 测试风格是**真实子进程**，不 mock asyncio | `tests/test_builtin_tools.py:416-450` | 新测试用真实 `python -c`，不要 mock 事件循环 |
| C14 | `AgentTool.bind_base_config()` 是既有的 config→tool 下发先例 | `agents/agent_tool.py:62-65` ← `engine.py:1399` | 配置注入沿用同款 `bind_*` 钩子，不新造机制 |
| C15 | `EngineConfig` 是 frozen pydantic BaseModel | `core/config.py:72-183`（`frozen=True` at `:156`） | 新配置项必须是 pydantic 字段，随 `model_copy` 走 |

---

## 2. 关键设计决策

### D1 — 显式 argv，按平台分流（已确认）

废弃 `create_subprocess_shell(shell=True)`，改用 `create_subprocess_exec(*argv)`。

```
Windows : powershell.exe|pwsh.exe -NoLogo -NoProfile -NonInteractive -Command <preamble><command>
POSIX   : sh -c <preamble><command>
```

收益：
- 消除中间 shell 的引号转义层（`bash -c` 的字符串域在这里没有对应物）
- 编码声明可以**前置到同一条 argv**，真正做到生产端钉死
- 方言可配置、可测试、可注入 `platform` 参数

**⚠️ 这是破坏性变更。** 现状 Windows 上是 `cmd.exe /c`，改后是 PowerShell。模型写的 `dir /b` 这类 cmd 专有语法会失效。
缓解：方言可配（`ShellConfig.dialect`），出问题可回退到 `"cmd"`。

### D2 — PowerShell 退出码语义（**已 spike 实测，方案确定**）

`powershell -Command "<cmd>"` **不传播**原生命令的 `$LASTEXITCODE`。Windows PowerShell 5.1 实测：

| 命令 | 裸 `-Command` | 加归一化包装 |
|---|---|---|
| `exit 3` | 3 | 3 |
| `python -c "sys.exit(3)"` | **1** ❌ | **3** ✅ |
| `python -c "sys.exit(0)"` | 0 | 0 |
| `Get-Item <不存在>` | 1 | 1 |
| `cmd /c exit 4` | **1** ❌ | — |

DSH 也没解决（`dsh-pwsh-local/lib/index.js:197-206` 同样是裸 `-Command`），所以这是 PowerShell 本身的语义。

#### 归一化包装的两个候选与实测取舍

```powershell
# WRAP  简单版
if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE }

# WRAP2 带 $? 守卫 —— 采用
if (-not $?) { if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }
```

| 场景 | WRAP | WRAP2 |
|---|---|---|
| `python exit(3)` | 3 ✅ | 3 ✅ |
| `Get-Date; python exit(3)` | 3 ✅ | 3 ✅ |
| `Get-Date` | 0 ✅ | 0 ✅ |
| `Get-Item <不存在>` | 1 ✅ | 1 ✅ |
| **`python exit(3); Get-Date`** | **3** ❌ **误报** | **0** ✅ |

WRAP 在「失败原生命令后跟成功 cmdlet」时会把陈旧的 `$LASTEXITCODE` 当成终态 —— 模型
看到 `[exit code: 3]` 但命令其实成功了，这会直接误导后续决策。WRAP2 用 `$?` 守卫消除该误报。

**结论：采用 WRAP2。** 代价是所有命令多 6 个 token 的尾巴，无可观测副作用。

#### 附带验证：显式 argv 不会破坏管道与重定向

```
Get-Process | Select-Object -First 2 Id   → exit=0, out='Id | -- | 33968 | 5148'
Set-Content -Path ... ; (Get-Item ...).Length → exit=0, out='3'
```

即 §D1「去掉中间 shell」在 PowerShell 方言下**无功能损失**（PowerShell 自带管道与重定向）。
POSIX 侧仍用 `sh -c`，管道/重定向由 sh 提供，行为不变。


### D3 — spill 落盘位置

```
<active_session_dir()>/spill/<session_id>/<job_id>/{stdout,stderr}.log
```

- 复用唯一既有产物约定（`session/transcript.py:74-76`），不新造 artifacts 目录
- 自动继承 C9 的测试隔离
- 权限对齐会话文件：`0o600` / 目录 `0o700`（`transcript.py:64,67`）
- 生命周期：随会话删除/迁移（`relocate_session_files`，`transcript.py:98`）一并处理

### D4 — 配置下发方式

沿用 C14 的 `bind_*` 钩子，不引入新容器。

```python
# core/config.py
class ShellConfig(BaseModel):
    dialect: Literal["auto","powershell","pwsh","cmd","sh","bash"] = "auto"
    timeout_s: float = 120.0
    max_timeout_s: float = 600.0
    max_output_chars: int = 30_000
    max_spill_bytes: int = 64 * 1024 * 1024
    merge_streams: bool = False
    promote_on_timeout: bool = True
    env_overrides: dict[str, str] = Field(default_factory=dict)

# EngineConfig
shell: ShellConfig = Field(default_factory=ShellConfig)
```

---

## 3. 模块结构

```
nono_agent/
  shell/
    __init__.py        导出 ShellDialect / SpillWriter / ShellConfig 读取
    dialect.py         方言解析 + argv 构造        [P0]
    preamble.py        编码声明                    [P0]
    spill.py           字节 ring + 溢出落盘         [P0]
    streams.py         stdout/stderr 分离收集        [P0]
    types.py           ShellSpec / ShellProcess     [P1]
    local.py           LocalShellExecutor           [P1]
    jobs.py            JobRegistry 实例              [P1]
  tools/builtin/
    bash.py            薄编排层
    job_output.py      模型读后台作业                [P1]
    job_kill.py        模型停后台作业                [P1]
```

> 放 `nono_agent/shell/` 而非 `nono_agent/utils/shell.py`：`utils/` 现有 3 个模块全是
> 小工具函数（C7），shell 是有状态的子系统，混进去会破坏该目录的定位。

---

## 4. P0 详细设计

### 4.1 `nono_agent/shell/preamble.py`

```python
"""编码声明与退出码归一化 —— 在命令执行前钉死两件 PowerShell 会弄错的事。

命令文本的编码在消费端猜是消费端的事（``tools/builtin/_common.py:147-204`` 的
utf-8 → locale → gb18030 回退链）；但猜错的代价是整段输出乱码。把声明前置到
生产端，这条回退链就退化成兜底而非常规路径。

⚠️ 声明的作用范围仅限 **PowerShell 自身产生的输出**（Write-Output、错误消息、
格式化表格）。它 **不改变子进程的 stdout 编码** —— 子进程自行决定。
实测见 §D2 附带说明。
"""

from __future__ import annotations

# PowerShell：分号分隔而非换行，保证错误行号不错位。
PS_PREAMBLE = (
    "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
    "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); "
)

# 退出码归一化。裸 -Command 会把失败原生命令压成 1（实测），且简单的
# $LASTEXITCODE 包装会在「失败后跟成功 cmdlet」时误报。用 $? 守卫消除误报。
# 详见 §D2 的实测表。放在命令之后，不是之前。
PS_EXIT_WRAP = (
    "if (-not $?) { if ($null -ne $LASTEXITCODE) { exit $LASTEXITCODE } else { exit 1 } }"
)

# cmd：`chcp 65001` 切代码页。用 & 而非 ; 分隔（cmd 不认 ;）。
CMD_PREAMBLE = "chcp 65001>nul & "

# POSIX：只设 PYTHONIOENCODING。不设 LC_ALL=C.UTF-8 —— macOS 没有该 locale，
# 设了会得到 "cannot change locale" 警告，反而污染输出。
POSIX_PREAMBLE = "export PYTHONIOENCODING=utf-8; "
```

#### 编码前置的实测收益范围（重要，避免高估）

Windows PowerShell 5.1 实测，不设 `PYTHONIOENCODING`：

| 场景 | 前置 | 不前置 |
|---|---|---|
| PowerShell 自身 `Write-Output "中文测试"` | `中文测试` ✅ | `���Ĳ���` ❌ |
| PowerShell **错误消息** `Get-Item <不存在>` | `找不到路径...` ✅ | 乱码 ❌ |
| 子进程 `python -c "print('中文')"` | `����` ❌ | `����` ❌ |

**结论**：前置声明的价值集中在 **PowerShell 自身输出与错误消息** —— 而这恰恰是模型
最需要读的内容（错误信息全中文）。子进程编码仍由子进程决定，`_common.decode_text`
的 gb18030 回退链必须保留，两者互补而非替代。


### 4.2 `nono_agent/shell/dialect.py`

```python
from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from typing import Final

from nono_agent.shell.preamble import (
    CMD_PREAMBLE, POSIX_PREAMBLE, PS_EXIT_WRAP, PS_PREAMBLE,
)

_PS_ARGS: Final = ("-NoLogo", "-NoProfile", "-NonInteractive", "-Command")


@dataclass(frozen=True)
class ShellDialect:
    """一个 shell 方言：argv 前缀 + 前置声明 + 后置归一化。"""
    name: str
    argv_prefix: tuple[str, ...]
    preamble: str
    suffix: str          # 追加在命令之后；PS_EXIT_WRAP 为退出码归一化（§D2）
    exe: str


_DIALECTS: Final[dict[str, ShellDialect]] = {
    "powershell": ShellDialect("powershell", ("powershell.exe", *_PS_ARGS), PS_PREAMBLE, PS_EXIT_WRAP, "powershell.exe"),
    "pwsh":       ShellDialect("pwsh",       ("pwsh.exe", *_PS_ARGS),       PS_PREAMBLE, PS_EXIT_WRAP, "pwsh.exe"),
    "cmd":        ShellDialect("cmd",        ("cmd.exe", "/D", "/S", "/C"), CMD_PREAMBLE,  "", "cmd.exe"),
    "bash":       ShellDialect("bash",       ("bash", "-c"),                POSIX_PREAMBLE, "", "bash"),
    "sh":         ShellDialect("sh",         ("sh", "-c"),                  POSIX_PREAMBLE, "", "sh"),
}


def resolve_dialect(
    name: str = "auto", *, platform: str | None = None
) -> ShellDialect:
    """解析方言。``auto``：Windows 优先 pwsh 7，退回 powershell 5.1；POSIX 用 sh。

    ``platform`` 显式注入而非直读 ``sys.platform``，让平台分支可测（C7）。
    """
    if name != "auto":
        try:
            return _DIALECTS[name]
        except KeyError:
            raise ValueError(
                f"unknown shell dialect {name!r}; expected one of "
                f"{sorted(_DIALECTS) + ['auto']}"
            ) from None
    plat = platform if platform is not None else sys.platform
    if plat == "win32":
        for candidate in ("pwsh", "powershell"):
            dialect = _DIALECTS[candidate]
            if shutil.which(dialect.exe):
                return dialect
        return _DIALECTS["powershell"]   # 探测失败时给最后的兜底
    return _DIALECTS["sh"]


def build_argv(dialect: ShellDialect, command: str) -> list[str]:
    """命令作为一个 argv 元素整体传入 —— 不存在中间 shell 的引号转义层。

    形态：``[*prefix, preamble + command + suffix]``。前缀/声明/后缀全是分号分隔，
    不引入换行，PowerShell 的错误行号因此保持准确（命令始终在第 1 行）。
    """
    tail = f"; {dialect.suffix}" if dialect.suffix else ""
    return [*dialect.argv_prefix, f"{dialect.preamble}{command}{tail}"]


def env_overrides(dialect: ShellDialect, extra: dict[str, str] | None = None) -> dict[str, str]:
    """模型友好的环境覆盖：禁颜色、禁分页器，否则工具输出被 ANSI 码污染。"""
    base = {"NO_COLOR": "1", "PAGER": "cat", "GIT_PAGER": "cat"}
    if extra:
        base.update(extra)
    return base
```

### 4.3 `nono_agent/shell/spill.py`

```python
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OutputDelta:
    """一次增量读的结果。

    ``lossy`` 表示 ``from_offset`` 之前的字节已被环形缓冲淘汰，
    完整内容在 ``spill_path``。
    """
    text: str
    next_offset: int
    lossy: bool = False
    spill_path: Path | None = None
    truncated: bool = False


class SpillWriter:
    """字节环形缓冲 + 溢出落盘。

    内存保留 ``max_bytes`` 尾部；超出部分追加写入 ``spill_path``（上限
    ``max_spill_bytes``，超出后丢弃并标记 truncated）。落盘发生在**写入时**，
    不是截断时 —— 这样模型拿到路径的瞬间文件已经完整。
    """

    def __init__(
        self,
        max_bytes: int,
        spill_path: Path | None,
        *,
        max_spill_bytes: int = 64 * 1024 * 1024,
        encoding: str = "utf-8",
    ) -> None:
        if max_bytes <= 0:
            raise ValueError(f"max_bytes must be positive, got {max_bytes}")
        self._max_bytes = max_bytes
        self._max_spill_bytes = max_spill_bytes
        self._spill_path = spill_path
        self._encoding = encoding
        self._ring = bytearray()
        self._spilled = 0
        self._closed = False
        self._lock = threading.Lock()

    @property
    def spill_path(self) -> Path | None:
        return self._spill_path

    def write(self, data: bytes) -> None:
        """接收一块原始字节。线程安全。"""
        if not data or self._closed:
            return
        with self._lock:
            self._ring.extend(data)
            if len(self._ring) <= self._max_bytes:
                return
            # 溢出部分落盘
            overflow = bytes(self._ring[: len(self._ring) - self._max_bytes])
            del self._ring[: len(overflow)]
            if self._spill_path is None:
                return          # 无落盘路径 → 纯丢弃（调用方据此标记 truncated）
            if self._spilled + len(overflow) > self._max_spill_bytes:
                return          # 落盘也超限 → 停止写，保持已有内容
            self._spill_path.parent.mkdir(parents=True, exist_ok=True)
            with self._spill_path.open("ab") as handle:
                handle.write(overflow)
            try:
                os.chmod(self._spill_path, 0o600)   # 对齐 transcript.py:64
            except OSError:
                pass
            self._spilled += len(overflow)

    def read_from(self, offset: int = 0) -> OutputDelta:
        """从 ``offset`` 读。``offset`` 小于环形起点时 lossy=True。"""
        with self._lock:
            base = self._spilled
            if offset < base:
                return OutputDelta("", base, lossy=True, spill_path=self._spill_path)
            start = offset - base
            raw = bytes(self._ring[start:])
            return OutputDelta(
                raw.decode(self._encoding, errors="replace"),
                base + len(raw),
                spill_path=self._spill_path if base else None,
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
```

> **为什么 offset 空间是「已落盘字节数 + 环内字节数」**：
> 这样 `read_from` 的游标语义与 P1 的 `job_output(from_offset)` 完全一致，
> 同一个游标在内存环和落盘文件之间无缝衔接，不需要在晋升时重置。

### 4.4 `nono_agent/shell/streams.py`

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from nono_agent.shell.spill import OutputDelta, SpillWriter


@dataclass
class StreamPair:
    """stdout / stderr 的对称收集器。"""
    stdout: SpillWriter
    stderr: SpillWriter

    @property
    def spill_paths(self) -> list[Path]:
        return [p for p in (self.stdout.spill_path, self.stderr.spill_path) if p is not None]


class StreamCollector:
    """持续把进程的两条管道读进 SpillWriter，直到 EOF。

    对应 DSH 的 readOutput()：非消费式，registry 通过 lazy 绑定拿句柄。
    """

    def __init__(self, streams: StreamPair) -> None:
        self._streams = streams

    async def pump(
        self,
        proc: asyncio.subprocess.Process,
        *,
        on_delta: callable | None = None,
    ) -> None:
        await asyncio.gather(
            self._pump_one(proc.stdout, self._streams.stdout, "stdout", on_delta),
            self._pump_one(proc.stderr, self._streams.stderr, "stderr", on_delta),
        )

    async def _pump_one(self, reader, writer, channel, on_delta) -> None:
        if reader is None:
            return
        while True:
            block = await reader.read(8192)
            if not block:
                return
            writer.write(block)
            if on_delta is not None:
                on_delta(channel)
```

### 4.5 `core/config.py` 新增 `ShellConfig`

见 D4。追加到 `EngineConfig`（`:72-183`）末尾：

```python
    # Shell（bash 工具）执行参数
    shell: ShellConfig = Field(default_factory=ShellConfig)
```

`ShellConfig` 定义在同文件 `EngineConfig` 之前（`ProviderConfig:27` / `ThinkingConfig:65` 的既有排布）。

### 4.6 `tools/builtin/bash.py` 改造

**保留**：`ToolBase` 继承、`_name`/`_description`/`_input_schema`、参数校验、审批调用、风险前缀。
**替换**：`execute` 主体。

```python
class BashTool(ToolBase):
    _name = "bash"
    _is_concurrent_safe = False
    _has_write_side_effects = True

    def __init__(
        self,
        classifier: BashClassifier | None = None,
        *,
        config: ShellConfig | None = None,
    ) -> None:
        self._classifier = classifier or BashClassifier()
        self._config = config or ShellConfig()

    def bind_shell_config(self, config: ShellConfig) -> None:
        """运行期下发配置。对齐 AgentTool.bind_base_config（agents/agent_tool.py:62）。"""
        self._config = config

    async def execute(self, input_params, context) -> ToolResult:
        command = (input_params.get("command") or "").strip()
        if not command:
            return _error("No command provided.")

        timeout = self._resolve_timeout(input_params)   # clamp 到 [1, max_timeout_s]
        workdir = resolve_path(context, input_params.get("workdir") or context.cwd)
        dialect = resolve_dialect(self._config.dialect)
        argv = build_argv(dialect, command)
        risk = self._classifier.classify(command)

        job_id = self._open_job(context, command)        # 移到 spawn 之前（见 §4.7）
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,           # ← 不再是 STDOUT
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                cwd=workdir,
                env={**os.environ, **env_overrides(dialect, self._config.env_overrides)},
                **hidden_kwargs(),                        # C6
            )
        except (OSError, ValueError) as exc:
            self._finish_job(job_id, "failed", None)
            return _error(f"Failed to start command: {exc}")

        # ... abort 联动（§5.2）...
        # ... 收集 + 等待（§4.7）...

        return self._render(result, risk)
```

**渲染**（对应 DSH `renderPwshResult`，`dsh-tool-pwsh/lib/index.js:155-176`）：

```
<stdout 正文>
[stderr]
<stderr 正文>
[output truncated; full output: <path>]      ← 必须最后一行（约束 C2）
[exit code: N]                                ← 零退出且无信号时不输出
```

改动要点：
- 风险前缀 `[dangerous] ` 保留（`bash.py:196` 现有行为）
- **非零退出改为标记而非 `is_error=True`** —— 对齐 DSH「非零退出是报告，不是错误，由模型判断」。
  这是行为变更，需在 commit message 标注。

### 4.7 P0 实施顺序（含一处顺序修正）

现状 `bash.py:137` 先 spawn、`:153` 才 `open_job`。改用 `create_subprocess_exec` 后
若仍先 spawn，极短命的命令可能在 `open_job` 前就结束 → UI 看不到。
**新顺序：先 `open_job`（拿 id）→ 再 spawn → 失败时 `finish_job(status="failed")`。**

### 4.8 `create_builtin_tools()` 改造（C10）

```python
def create_builtin_tools(config: EngineConfig | None = None) -> list[ToolBase]:
    shell_config = config.shell if config is not None else ShellConfig()
    tools: list[ToolBase] = [
        BashTool(config=shell_config),
        ...
    ]
```

三个调用点：
- `builtin/__init__.py:47`（自身导出）—— 无需改
- `cli/repl.py:64-66` —— 改为 `create_builtin_tools(config)`（该处已有 config）
- `server/session_manager.py:37-42` —— 改为 `create_builtin_tools(config)`，
  签名加 `config: EngineConfig | None = None` 并从 `default_config` 取

零参可调用性保留 → 现有测试 `test_builtin_tools.py:31` 不受影响。

---

## 5. P1 详细设计

### 5.1 `nono_agent/shell/types.py` —— 能力协议

对齐 DSH `dsh-shell/lib/index.js:68-82` 的语义契约。

```python
"""Shell 执行能力协议。

``ShellExecutor`` 是可替换的能力缝：LocalShellExecutor 直接 spawn，
未来的沙箱执行器继承它、只覆写 argv 构造。不引入 IoC 容器 ——
执行器由 QueryEngine 持有、经 ToolUseContext 下发。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

from nono_agent.shell.dialect import ShellDialect


@dataclass(frozen=True)
class ShellSpec:
    """一次执行的完整规格。调用方所有，不透明传给执行器。"""
    command: str
    dialect: ShellDialect
    workdir: Path | None = None
    timeout_s: float = 120.0
    on_expiry: str = "kill"       # "kill" 到期杀 | "none" 不设 deadline
    env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ShellOutcome:
    """前台执行的终态。**只有基础设施故障才 raise**；非零退出/超时/abort 都在这里 resolve。"""
    exit_code: int | None
    signal: str | None
    timed_out: bool
    aborted: bool
    timeout_s: float
    stdout: OutputDelta
    stderr: OutputDelta
    sandbox: dict | None = None      # P2 预留


@runtime_checkable
class ShellProcess(Protocol):
    """活着的进程句柄。``read_output`` 必须增量：连续读不重复。"""
    async def wait(self) -> ShellOutcome: ...
    def read_output(self, offset: int) -> tuple[str, OutputDelta]: ...
    def kill(self) -> bool: ...
    @property
    def status(self) -> str: ...      # "running" | "completed" | "killed"


@runtime_checkable
class ShellExecutor(Protocol):
    async def execute(self, spec: ShellSpec, signal: AbortSignal | None = None) -> ShellProcess: ...
```

### 5.2 `nono_agent/shell/local.py`

```python
class LocalShellExecutor:
    """在本地 spawn 进程。P2 的沙箱执行器继承本类，只覆写 _prepare_argv。"""

    def __init__(self, *, spill_root: Path | None = None) -> None:
        self._spill_root = spill_root

    def _argv(self, spec: ShellSpec) -> list[str]:
        return build_argv(spec.dialect, spec.command)

    async def execute(self, spec, signal=None) -> ShellProcess:
        proc = await asyncio.create_subprocess_exec(
            *self._argv(spec),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
            cwd=spec.workdir,
            env={**os.environ, **spec.env},
            **hidden_kwargs(),                      # C6
        )
        return _LocalProcess(proc, spec, self._spill_root)
```

**`on_expiry` 的实现差异**（这是 P1 最重要的一处）：

```python
# _LocalProcess 内部
async def wait(self) -> ShellOutcome:
    try:
        if self._spec.on_expiry == "kill":
            await asyncio.wait_for(self._await_exit(), timeout=self._spec.timeout_s)
        else:
            await self._await_exit()        # 不设 deadline
    except TimeoutError:
        self.kill()
        ...timed_out=True
    ...
```

即 **`on_expiry="none"` 时执行器完全不设 deadline**，超时判断上移到工具层
（`asyncio.wait_for(proc.wait(), timeout)`）。工具层超时时**不调 `kill()`**，只把句柄交给 registry。
这样「超时」和「abort」在物理上就是两条独立的通路，不再纠缠。

### 5.3 abort 监听的所有权移交（C5 核心）

```python
# bash.py
if self._promote and registry is not None:
    job = registry.start(spec, kill=proc.kill)
    try:
        outcome = await asyncio.wait_for(proc.wait(), timeout)
    except TimeoutError:
        # 移交：registry 继续持有 abort 监听，execute 立即返回
        registry.attach_abort_watch(job, context.signal)
        return self._render_promoted(job)
    registry.detach_abort_watch(job)
    return self._render_foreground(outcome)
```

对照现状 `bash.py:183-185` 的 `finally: abort_task.cancel()` —— 晋升路径**不能**执行它。
`JobRegistry.attach_abort_watch` 在 signal 触发时调 `kill()`，并在 engine teardown 时统一
`kill_all()` 兜底（防孤儿进程）。

### 5.4 `nono_agent/shell/jobs.py` —— registry 实例化

从 `jobs/board.py`（模块级全局 + `threading.Timer`）迁移为实例。

```python
class JobRegistry:
    """会话级作业注册表。

    替换 board.py 的模块级全局：消除跨会话串扰与测试污染
    （board.py:160-171 的 reset_jobs 正是为此存在）。
    保留 subscribe 接口 —— server 的 SSE 消费路径不能断
    （apps/server/.../features/jobs/api.py:59）。
    """

    def __init__(self, session_id: str, *, max_output_chars: int = 32_000) -> None: ...
    def start(self, spec: ShellSpec, *, kill: Callable[[], None]) -> JobHandle: ...
    def attach_abort_watch(self, job: JobHandle, signal: AbortSignal | None) -> None: ...
    def detach_abort_watch(self, job: JobHandle) -> None: ...
    def read(self, job_id: str, from_offset: int = 0) -> OutputDelta: ...
    def list(self) -> list[dict]: ...                # 供 SSE，字段与 Job.public() 对齐
    def subscribe(self, listener) -> Callable[[], None]: ...
    def kill(self, job_id: str, reason: str) -> bool: ...
    def kill_all(self) -> None: ...
```

- 实时输出改用 P0 的 `SpillWriter`（增量 + 落盘），替代 `board.py:84` 的
  `job.output = text[-_MAX_OUTPUT:]` 全量替换
- `threading.Timer(_FLUSH_S=0.08)`（`board.py:190`）→ asyncio task
- `board.py` 保留为薄 re-export，或在 P1 完成后删除（取决于 `apps/server` 的引用面）

### 5.5 新增 `job_output` / `job_kill`

```python
class JobOutputTool(ToolBase):
    _name = "job_output"
    _is_concurrent_safe = True
    _has_write_side_effects = False        # 只读 → 不会被 C11 拦
    _input_schema = {
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "..."},
            "from_offset": {"type": "integer", "description": "增量游标；省略则从头读"},
        },
        "required": ["job_id"],
    }

class JobKillTool(ToolBase):
    _name = "job_kill"
    _is_concurrent_safe = True
    _has_write_side_effects = True         # ⚠️ 会被 C11 的 deny_all 拦住
    _input_schema = {...}
```

**C11 冲突必须处理**：`apps/server/.../session_manager.py:28-34` 的
`deny_all_permission_checker` 恒返回 `False`，`job_kill` 在 server 模式下必定被拒
（经 `executor.py:202-217`）。三选一：
1. 在 `deny_all_permission_checker` 里对 `{"job_kill"}` 开白名单（**推荐**：停自己起的进程不放大攻击面）
2. 把 `JobKillTool._has_write_side_effects` 设为 `False`（语义撒谎，不推荐）
3. 推迟到 P2 沙箱落地后再加

三个工具必须**共享同一 registry 实例**（`create_builtin_tools` 内构造一次传三个）。

### 5.6 `ToolUseContext` 扩展（C4）

```python
@dataclass(frozen=True)
class ToolUseContext:
    ...
    shell: Any | None = None        # ShellExecutor
    shell_jobs: Any | None = None   # JobRegistry
```

⚠️ `with_extra`（`context.py:163-178`）是**手工逐字段复制**，必须同步加两行，
否则 P1 的一切在子代理里失效（`agents/runner.py:86-118` 会走 with_extra 路径）。

engine 构造处 `core/engine.py:451-462` 注入。

---

## 6. 测试计划

遵循既有风格：**扁平命名**（C8）、**真实子进程不 mock 事件循环**（C13）、
`asyncio_mode="auto"` 无需 `@pytest.mark.asyncio`（`pyproject.toml:46`）。

### 6.1 `tests/test_shell_dialect.py`（P0）

| 用例 | 断言 |
|---|---|
| `resolve_dialect("auto", platform="win32")` 在装有 pwsh 时返回 pwsh | `dialect.name == "pwsh"` |
| `resolve_dialect("auto", platform="linux")` | `dialect.name == "sh"` |
| `resolve_dialect("cmd", platform="win32")` | 显式优先于 auto |
| `resolve_dialect("nope")` | `pytest.raises(ValueError)` |
| `build_argv(pwsh, "Get-Process")` | `argv[-1].endswith(PS_EXIT_WRAP)`；`argv[0] == "pwsh.exe"`；以 PS_PREAMBLE 开头 |
| `build_argv` 长度 | 恒为 `len(prefix) + 1`（命令不被拆词） |
| 注入空格/引号的命令 | `argv` 长度不变（无引号转义层） |
| `build_argv(cmd 方言)` | `suffix == ""`，不追加 PS 包装（否则 cmd 语法错误） |
| **命令内不含换行** | `argv[-1].count("\n") == 0`（换行会破坏 PowerShell 错误行号） |

### 6.2 `tests/test_shell_spill.py`（P0）

| 用例 | 断言 |
|---|---|
| 未超限时 `read_from(0)` | 全文，`lossy=False`，`spill_path is None` |
| 超 `max_bytes` 后 `read_from(0)` | `lossy=True`，返回**尾部** `max_bytes` 字节 |
| 超限后 `read_from(base)` | 起点正确 |
| 落盘文件内容 | 溢出部分完整写入 |
| 超过 `max_spill_bytes` | 停止增长，`read_from` 标记 `truncated` |
| 游标连续性 | 多次 `read_from` 拼接 == 完整字节流 |
| `spill_path` 权限 | Windows 上跳过（非 POSIX 权限位），POSIX 断言 `0o600` |

### 6.3 `tests/test_shell_bash.py`（P0，替换 `TestBash` 部分用例）

| 用例 | 断言 |
|---|---|
| 中文输出（PowerShell 自身） | `Write-Output "中文"` → content 含正确中文，无 `\ufffd` |
| 中文错误消息 | `Get-Item <不存在>` → stderr 段含可读的 `找不到路径` |
| 退出码：原生命令失败 | `python -c "sys.exit(3)"` → `[exit code: 3]`（**不是 1**） |
| 退出码：cmdlet 失败 | `Get-Item <不存在>` → `[exit code: 1]` |
| 退出码：成功 | `Get-Date` → 无 `[exit code]` 标记 |
| **退出码不误报** | `python exit(3); Get-Date` → `[exit code: 0]`（无标记） |
| 管道仍可用 | `Get-Process \| Select-Object -First 2 Id` → 正常输出 |
| stderr 分离 | Python 写 stderr → content 含 `[stderr]` 段 |
| 大输出 | content 含 `full output:` 且该文件存在、可读、内容完整 |
| 截断提示在尾部 | 用正则确认提示行是最后一行（约束 C2） |
| workdir | 相对路径基于 workspace 解析 |
| 非法 dialect | `pytest.raises(ValueError)` |
| 中文路径下的 workdir | 不抛 `OSError` |
| **非零退出不是错误** | `is_error is False`（新语义，见 §4.6） |

### 6.4 `tests/test_shell_jobs.py`（P1）

| 用例 | 断言 |
|---|---|
| 晋升 | 短命令 + 小 timeout → 返回 `moved to background job`，**进程仍存活** |
| `job_output` 增量 | 两次读不重复（约束 SpillWriter 游标语义） |
| `job_kill` | 进程终止，job 状态转 `killed`，重复调用幂等 |
| abort 移交 | 晋升后触发 `context.signal` → 进程被杀 |
| 跨会话隔离 | 两个 registry 互不可见（这正是 `JobRegistry` 实例化的目的） |
| SSE 兼容 | `subscribe` 回调仍被调用，字段与 `Job.public()`（`board.py:36-47`）一致 |
| teardown | `kill_all()` 后无残留子进程（防泄漏） |

### 6.5 平台跳过模式

参照 `tests/test_product_terminal.py:85-86` 的既有写法：

```python
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="PowerShell dialect is Windows-only")
```

---

## 7. 提交拆分

每个提交独立可测、可回滚。

| # | 提交 | 触及文件 | 可回滚性 |
|---|---|---|---|
| 1 | `feat(shell): 方言解析与编码声明` | `shell/preamble.py`、`shell/dialect.py`、`test_shell_dialect.py` | 纯新增，删除即回滚 |
| 2 | `feat(shell): 字节 ring 与溢出落盘` | `shell/spill.py`、`test_shell_spill.py` | 纯新增 |
| 3 | `feat(config): ShellConfig 并接入 EngineConfig` | `core/config.py`、`builtin/__init__.py`、两个调用点 | `create_builtin_tools` 保持零参兼容 |
| 4 | `refactor(bash)!: 显式 argv + 流分离 + 落盘` | `bash.py`、`shell/streams.py`、`test_shell_bash.py` | ⚠️ **破坏性**：Windows 方言变更 + 退出码语义变更 |
| 5 | `feat(shell): ShellExecutor 能力协议` | `shell/types.py`、`shell/local.py` | 纯新增，bash 未切过去 |
| 6 | `refactor(bash)!: 切到能力缝` | `bash.py`、`context.py`、`core/engine.py` | 需与 7 一起回滚 |
| 7 | `feat(shell): JobRegistry 与模型侧作业工具` | `shell/jobs.py`、`job_output.py`、`job_kill.py`、`session_manager.py` | 新工具可从 registry 摘除 |

**提交 4 是唯一的破坏性提交。** 建议：
- 单独成 PR，changelog 明确写「Windows 默认方言由 cmd 改为 PowerShell」
- 提供 `shell.dialect: "cmd"` 回退开关
- 先落 D2 的 spike 结论

---

## 8. 文档与提示词更新

| 位置 | 改动 |
|---|---|
| `README.md:13` | 工具数 22 → 24（新增 `job_output` / `job_kill`） |
| `README.md:17` | 对照表「OS 级硬沙箱 ❌」保持不变 —— P2 未做，**不得虚报** |
| `docs/USAGE.md:300` | 补默认 timeout / 输出上限 / spill 行为 / 方言与退出码语义 |
| `docs/USAGE.md:298` | 编码说明改为「生产端前置声明 + 消费端兜底回退」 |
| `bash.py:68-79` `_description` | 告知模型：非零退出以 `[exit code: N]` 报告；大输出会给出文件路径；默认方言 |
| `prompt/sections/tools_guidance.py:14-23` | 同步（该节是给模型的通用工具约束） |

---

## 9. 待决策项

| # | 问题 | 状态 | 建议 |
|---|---|---|---|
| Q1 | PowerShell 退出码语义（§D2） | ✅ **已 spike 实测，方案确定** | 采用 `PS_EXIT_WRAP`（`$?` 守卫版） |
| Q2 | Windows 默认方言是否从 cmd 切到 PowerShell | 待定 | 切，但保留 `dialect: "cmd"` 回退 |
| Q3 | `job_kill` 如何绕开 `deny_all_permission_checker`（C11） | 待定 | 白名单 |
| Q4 | `jobs/board.py` 的去留 | 待定 | 先保留 re-export，`apps/server` 引用面确认后再删 |
| Q5 | P2 OS 级沙箱与 `README.md:17` 的「❌」声明冲突 | **需产品决策** | 见下 |
| Q6 | spill 文件的保留期与清理时机 | 待定 | 随会话删除（对齐 `relocate_session_files`） |

> **Q5 值得单独强调**：`README.md:17` 把「OS 级硬沙箱（容器/bubblewrap）❌」列为**有意的能力缺口**。
> 这不是遗漏，是产品定位。因此 P2 不是我该单方面推进的技术债，而是需要你确认是否改变产品承诺的决策。

### 9.1 环境事实（影响 Q2）

目标机 **未安装 pwsh 7**，仅有 Windows PowerShell 5.1。影响：
- `resolve_dialect("auto")` 在本机会走 `shutil.which("pwsh.exe")` 失败 → 降级 `powershell.exe`，
  降级路径已被 spike 覆盖（全部数据来自 5.1）
- 若目标用户群普遍只有 5.1，`pwsh` 优先的逻辑仍应保留（PowerShell 7 行为更好），
  但**测试矩阵必须以 5.1 为准**，不能只在 7 上验证

---

## 10. 执行前置

1. ✅ **D2 spike 已完成** —— 结论见 §D2，采用 `$?` 守卫版包装
   （可复现：`python scripts/spike_pwsh_dialect.py`）
2. 确认 Q2 / Q3 / Q5 三个决策
3. **补测环境**：至少一台装有 pwsh 7 的机器复跑 §D2 的表，确认 7 的行为与 5.1 一致
   （目标机当前只有 Windows PowerShell 5.1）

### 10.1 基线测试状态（2026-10-03 实测）

```
775 passed, 1 failed  (50.51s)
FAILED tests/test_user_hooks.py::TestUserHookExecutor::test_run_passes_context
```

该失败是**既有的、与本设计无关的 Windows 平台缺陷**：测试用例硬编码执行 `cat`
（`tests/test_user_hooks.py:45` 附近），`cat` 在 Windows 上不存在，断言 `exit_code == 0`
必然失败；测试缺少 `sys.platform == "win32"` 跳过守卫（参照
`tests/test_product_terminal.py:85-86` 的既有写法）。

**处理建议**：实施前先给该测试加跳过守卫，让基线转绿，否则重构过程中无法区分
「新引入的回归」与「既有噪声」。

> 顺带一提：这个失败本身就是 §C7 描述的问题的一个实例 —— shell 执行路径上的平台分支
> 缺乏系统处理。它在 `user_hooks.py` 而非 `bash.py`，但同属「跨平台 shell 调用」这一类。

---

## 11. 实施结果（2026-10-03）

P0 + P1 已实施完成。验收：

```
pytest : 897 passed, 1 skipped        （基线 775 passed, 1 failed）
ruff   : All checks passed            （我触及的文件）
mypy   : Success, no issues in 14 source files
```

新增测试 122 条（`test_shell_dialect/spill/bash/executor/jobs/wiring.py`），
外加修复基线里那个 `cat` 测试（改用 `more`，跨平台等价的 stdin 透传）。

### 11.1 实施中发现并修正的设计缺口

以下是设计文档没写、但实现时暴露出来的问题。设计文档本身保持原样以便对照。

| # | 问题 | 处理 |
|---|---|---|
| 1 | **超时后被孙进程拖住**（最严重）。只杀 shell 时，shell 启动的孙进程继续持有 stdout 管道，`proc.wait()` 一直阻塞到它自然结束 —— 跑 30 分钟的命令「超时」后还要再等 30 分钟 | 新增 `shell/proc.py`：独立进程组 + 杀整棵树（Windows `taskkill /T/F`、POSIX `killpg`），且等待有上界。回归测试 `test_bash_timeout_returns_promptly` |
| 2 | **`SpillWriter` 无处落盘时游标错位**。被丢弃的字节没有推进绝对偏移空间，`read_from` 返回的文本实际起点与 `start_offset` 不符，后续增量读静默出错 | `_base_offset = 已落盘 + 已丢弃`，无处落盘也照常推进。测试 `test_no_spill_path_means_pure_drop` |
| 3 | **`ShellOutcome` 无法表达「显式 kill」**。被杀进程带着退出码 1 被误报成 `failed` | 新增 `killed` 字段；`status` 里三类非正常结束都压过 exit_code 推断 |
| 4 | **协议签名与实现不符**。`ShellProcess.kill` 协议声明同步、实现是异步 | 协议改为 async |
| 5 | **`merge_streams` 会输出重复内容**。两条流共用 writer 后 `deltas()` 把同一份读两遍 | `StreamPair` 加 `merged` 标记，`deltas()` 在合并模式只取一次；执行器侧真正共用 writer |
| 6 | **超时语义与旧的「报错」冲突**。晋升是 P1 的新默认，旧测试期望 `is_error` | 默认改为晋升（`promote_on_timeout=True`），`False` 时退回「超时即终止且报错」。两条路径都有测试 |
| 7 | **PowerShell 错误消息随 `$PSUICulture` 变语言** | 测试断言「无 U+FFFD」而非具体语言 —— 断言性质，不断言文案 |
| 8 | **`registry._mark()` 在无事件循环时崩**。旧 `threading.Timer` 能在同步环境工作，asyncio 定时器不能 | 无 loop 时退化为立即同步推送，保持旧的同步调用点可用 |

### 11.2 晋升消息的游标语义（已定，实现与 DSH 一致）

晋升时返回的文本就是「到目前为止的输出」，且**推进游标**；后续 `job_output` 只给更新的
部分。这与 DSH 一致（`dsh-tool-pwsh/lib/index.js:186-188` 的 `renderPwshPromoted`
嵌入一次消费式读）。第一次写测试时我以为是 bug，实为契约，已由
`test_incremental_reads_do_not_repeat` 固化。

### 11.3 待决策项的落地情况

| # | 状态 |
|---|---|
| Q1 退出码语义 | ✅ 已 spike 并实施（`PS_EXIT_WRAP`，`$?` 守卫版） |
| Q2 默认方言 | ✅ 切到 PowerShell，保留 `ShellConfig(dialect="cmd")` 回退 |
| Q3 `job_kill` 权限 | ✅ `deny_all_permission_checker` 白名单放行（`_SESSION_SCOPED_TOOLS`） |
| Q4 `board.py` 去留 | ✅ 保留为按会话分片的门面，Web 端 SSE 调用点零改动 |
| **Q5 OS 级沙箱** | ⏸ **未做** —— `README.md:17` 的「OS 级硬沙箱 ❌」声明保持不变，需产品决策 |
| Q6 spill 清理 | ⏸ 落盘在会话目录 `spill/`，随会话目录一起清理；独立保留期策略未做 |

### 11.4 未纳入本轮

- **P2 OS 级沙箱**（Q5 未决）
- **持久化 Shell / PTY**（设计文档 §5 明确列为「不做」）
- **spill 文件的定时清理**（Q6）
- **`bypass` 模式下的策略分级替代命令分类器**（P2-a，依赖沙箱先落地）
