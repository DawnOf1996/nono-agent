---
title: 开发环境与目录结构
---

# 开发环境与目录结构

本页面向要往 Nono Agent 里加东西的人。装完环境先看这里，再看
[新增内置工具](add-builtin-tool.md) / [新增工具插件](add-tool-plugin.md) / [新增 Provider 适配器](add-provider.md)。

---

## 1. 环境

```bash
git clone https://gitee.com/dawn1996/nono-agent.git
cd nono-agent
uv sync --extra dev
```

| 命令 | 作用 |
|---|---|
| `uv run pytest` | 跑全量测试（当前 1016 条） |
| `uv run pytest tests/test_tools.py -v` | 跑单文件 |
| `uv run pytest -k "compact and not shell"` | 按表达式筛 |
| `uv run ruff check .` | lint |
| `uv run ruff check --fix .` | lint + 自动修 |
| `uv run mypy nono_agent/` | 类型检查（**只查引擎包**，产品层不查） |

!!! warning "`uv sync` 是精确同步，不是增量安装"
    不带 `--extra dev` 会把 pytest / ruff / mypy 和 `apps/*` 一起卸掉。补装东西时永远写全：
    `uv sync --extra dev --group docs`。

!!! warning "mypy 的 python_version 与运行时不一致"
    `pyproject.toml` 里 `[tool.mypy] python_version = "3.11"`，而运行时要求 3.12+、CI 也用 3.12。
    也就是说类型检查按 3.11 语义跑。改这个值可能一次性冒出一批错误，单独开一个 PR 处理。

---

## 2. 仓库结构

```text
nono-agent/
├── nono_agent/          # 引擎：23 个子系统，零产品依赖
├── apps/                # 产品层：cli / server / web / desktop / packages
├── tests/               # 1016 条测试，75 个 test_*.py 模块，扁平放
├── examples/            # 库用法示例
├── scripts/             # 打包与运维脚本（pack-desktop 等）
├── session-viewer/      # 会话日志查看器（独立包）
├── runtime-test/        # 运行时冒烟工作区
└── docs-site/           # 本文档站（MkDocs Material）
```

**产品层不许反向依赖引擎的内部实现。** `nono_agent/` 里不该出现 `import nono_app_*`；反之 `apps/` 可以随意读引擎公开 API。引擎与产品之间只靠「注册表 + 协议 + 回调」接缝，例如工具插件目录靠 `register_plugin_dir()` 挂进来，而不是引擎 import 产品。

---

## 3. 引擎子系统地图

```text
nono_agent/
│
│ ─── 引擎内核 ─────────────────────────────────────────────
├── core/                 QueryEngine / agentic_loop / 配置 / 状态机 / 类型
├── pipeline/             调用前压缩管线（4 个默认 stage + 可选 ContextCollapse）
├── recovery/             RecoveryChain：API/循环错误分类与恢复动作
├── hooks/                MiddlewareChain / StopHook / PostSampling / user_hooks
├── observability/        TokenTracker / structlog / Metrics
├── utils/                abort / tokens / net_guard / process
│
│ ─── LLM 与会话 ───────────────────────────────────────────
├── providers/            三类协议适配器 + 分层 YAML 模型目录
├── session/              事件溯源：events / derive / transcript / redact / replay / stats
│
│ ─── 工具 ─────────────────────────────────────────────────
├── tools/                Tool 协议 / ToolRegistry / Executor / ToolUseContext
│   └── builtin/          23 个内置工具
├── shell/                本地 Shell 执行器 + JobRegistry
├── jobs/                 作业板（按会话分片）
│
│ ─── 扩展面 ───────────────────────────────────────────────
├── prompt/               PromptSection 组装
├── rules/ skills/ plugins/ toolplugins/ commands/
├── settings/ permissions/ mcp/ agents/ memory/ checkpoint/
```

每个包的职责与边界见 [架构总览 §3.1](../architecture/nono-agent-architecture.md) 和
[路线图 §5.2](../roadmap/index.md)。

---

## 4. 三个核心协议

改动之前先认准这三条接缝——绝大多数扩展都挂在这上面。

### 4.1 `Tool` 协议（`tools/protocol.py`）

```python
@runtime_checkable
class Tool(Protocol):
    # 这几个都是 @property，直接实现 Protocol 的工具必须自己写 property
    @property
    def name(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def input_schema(self) -> dict[str, Any]: ...
    @property
    def is_concurrent_safe(self) -> bool: ...      # ToolBase 默认 False
    @property
    def has_write_side_effects(self) -> bool: ...  # ToolBase 默认 True
    @property
    def is_read_only(self) -> bool: ...            # = not has_write_side_effects
    async def execute(self, input_params, context) -> ToolResult: ...
    def should_prompt_for_permission(self, input_params, context) -> bool: ...
    def to_api_schema(self) -> dict[str, Any]: ...
```

绝大多数工具继承 `ToolBase` 而不是直接实现 Protocol —— 基类已经给了 fail-closed 默认值和 `to_api_schema`。
注意基类用 `_is_concurrent_safe` / `_has_write_side_effects` 这两个**下划线类属性**喂给上面那圈 property。

### 4.2 `LLMProvider` 协议（`providers/base.py`）

```python
@runtime_checkable
class LLMProvider(Protocol):
    name: str
    supports_thinking: bool
    supports_tool_choice: bool
    async def stream(self, *, messages, system_prompt, tools,
                     thinking_config=None, model="", max_tokens=None,
                     signal=None, fallback_model=None,
                     on_streaming_fallback=None, **kwargs
                    ) -> AsyncGenerator[StreamEvent | Message, None]: ...
    def count_tokens(self, messages, system_prompt, tools) -> int: ...
    def normalize_messages(self, messages) -> list[dict[str, Any]]: ...
```

### 4.3 `TranscriptWriter` 协议（`session/transcript.py`）

会话日志只写事件。关键方法是 `append_events`（一批事件一次落盘），其余是 `append_event` / `read_events` / `read_events_from` / `write_header` / `read_header` / `flush`。

---

## 5. 写测试

测试全部放在 `tests/` 下的扁平文件里，命名 `test_<主题>.py`，**不建子包**。

### 5.1 无需真实 API

`tests/helpers.py` 提供全套夹具（不进引擎包）：

| 夹具 | 用途 |
|---|---|
| `MockProvider(responses=[...], default_response=...)` | 脚本化响应，按调用顺序返回，支持多轮 |
| `FixtureReplayProvider` | 回放录制的 API 响应，做确定性集成测试 |
| `MockTool(name, response, concurrent_safe=True, write_side_effects=False)` | 模拟工具 |
| `create_test_engine(responses, *, tools, model, transcript_writer, provider, config_overrides, **engine_kwargs)` | 造一个预配置好的 `QueryEngine` |

典型骨架：

```python
from tests.helpers import MockTool, create_test_engine


async def test_my_tool_runs():
    engine = create_test_engine(
        responses=[
            {"text": "", "tool_calls": [{"name": "my_tool", "args": {"x": 1}}]},
            {"text": "done", "tool_calls": []},
        ],
        tools=[MyTool()],
    )
    async with engine:
        results = [m async for m in engine.submit_message("go")]
    assert any("ok" in str(m) for m in results)
```

### 5.2 测试风格约定

- **子进程相关的测试用真实进程**，不 mock asyncio 事件循环（这是 Shell 工具测试的既有约定，见 Shell 设计文档 C13）。
- 会话目录由 `tests/conftest.py` 的 autouse 夹具指向 tmp，所以涉及落盘的工具测试自动获得隔离。
- 事件相关改动要同时看 `tests/test_event_coverage.py`（逐类型断言有真实发射路径）和 `tests/test_event_ordering.py`。
- 改了事件类型，`KNOWN_EVENT_TYPES` 与 `test_event_coverage.py` 的数量断言会一起失败——这是有意的。

### 5.3 提交前

```bash
uv run ruff check . && uv run mypy nono_agent/ && uv run pytest
```

提交信息规范见 [提交规范](commit-convention.md)。

---

## 6. 相关页面

- [新增内置工具](add-builtin-tool.md)
- [新增工具插件](add-tool-plugin.md)
- [新增 Provider 适配器](add-provider.md)
- [测试与代码规范](testing-and-style.md)
- [架构总览](../architecture/nono-agent-architecture.md)
- [路线图](../roadmap/index.md)
