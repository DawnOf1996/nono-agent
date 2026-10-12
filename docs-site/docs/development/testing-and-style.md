---
title: 测试与代码规范
---

# 测试与代码规范

## 1. 代码规范

由 `ruff` 与 `mypy` 强制，配置在根 `pyproject.toml`。

### ruff

```toml
target-version = "py311"
line-length = 100
select = ["E", "F", "I", "N", "W", "UP"]
known-first-party = ["nono_agent", "nono_app_cli", "nono_app_server", "tests"]
```

| 项 | 要求 |
|---|---|
| 行宽 | 100 |
| 导入顺序 | `isort` 规则，一级 import 块内自动排序 |
| 命名 | `N` 规则：`snake_case` 函数/变量、`PascalCase` 类、`UPPER` 常量 |
| 现代化 | `UP` 规则：能用 `X \| None` 就不要 `Optional[X]`（本项目 `from __future__ import annotations` 是标配） |

```bash
uv run ruff check .            # 只报
uv run ruff check --fix .      # 自动修能修的
uv run ruff format .           # 格式化（本项目未强制，谨慎使用）
```

### mypy

```toml
python_version = "3.11"
strict = true
```

`strict = true` 意味着**所有**函数都要标注类型，没有 `Any` 逃逸是做不到的：

```python
def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult: ...
#                    ↑ 输入可能不可信，Any 是合理的      ↑ ToolUseContext 循环导入，用 Any + 注释
```

CI 只查 `nono_agent/`，产品层不查。

!!! warning "`python_version = 3.11` 与运行时不一致"
    运行时要求 Python 3.12+，CI 也用 3.12，但 mypy 按 3.11 语义检查。改这个值会一次性冒出一批
    错误，要改就单独开一个 PR，别混在功能改动里。

---

## 2. CI

`.github/workflows/ci.yml` 在 `main` 分支 push 与所有 PR 上跑，**两个 job**：

```yaml
# job: test
- run: uv sync --extra dev
- run: uv run ruff check .
- run: uv run mypy nono_agent/
- run: uv run pytest

# job: docs
- run: uv sync --group docs
- run: uv run mkdocs build -f docs-site/mkdocs.yml --strict
```

**本地跑通再推。** `test` 三条 + `docs` 那条全绿是合并的最低门槛。`--strict` 会把断链、
没进 `nav` 的页面直接判失败 —— 改 `docs-site/` 下的文件时它就是那道闸门。

---

## 3. 测试

当前 1055 条、77 个 `test_*.py` 模块（另有 `conftest.py` / `helpers.py` / `__init__.py`），扁平放在 `tests/`（不建子包）。

### 3.1 命名与位置

| 内容 | 文件 |
|---|---|
| 工具 | `tests/test_builtin_tools.py` / `test_tools.py` |
| 引擎主循环 | `tests/test_engine.py` / `test_engine_events.py` |
| 会话事件 | `test_session_events.py` / `test_transcript_events.py` / `test_event_coverage.py` / `test_event_ordering.py` / `test_session_invariant.py` |
| 压缩管线 | `test_pipeline.py` / `test_summarizer.py` / `test_context_usage.py` |
| Shell | `test_shell_bash.py` / `test_shell_dialect.py` / `test_shell_spill.py` / `test_shell_executor.py` / `test_shell_jobs.py` / `test_shell_wiring.py` |
| 权限 | `test_permissions.py` / `test_session_permissions.py` |
| 工具插件 | `test_tool_plugins.py` / `test_tool_plugin_auth.py` |
| 产品层 | `test_server.py` / `test_web_ui.py` / `test_cli_config.py` / `test_onboarding.py` |

新增测试沿用 `test_<主题>.py`，主题与被测模块对应。

### 3.2 夹具

`tests/helpers.py` 提供（**不进引擎包**）：

- `MockProvider(responses, default_response)` —— 脚本化响应，按调用顺序返回，耗尽后回退默认。
- `FixtureReplayProvider` —— 回放录制的 API 响应，做确定性集成测试。
- `MockTool(name, response, concurrent_safe, write_side_effects)`。
- `create_test_engine(responses, *, tools, model, transcript_writer, provider, config_overrides, **engine_kwargs)`。

`tests/conftest.py` 里有 4 个 autouse 夹具，其中 `DEFAULT_SESSION_DIR` 指向 tmp —— 涉及落盘的工具测试**自动获得隔离**，不用自己清目录。

### 3.3 风格约定

- **子进程测试用真实进程**，不 mock asyncio 事件循环（Shell 工具的既有约定，设计文档 C13）。
- `asyncio_mode = "auto"`：写 `async def test_xxx()` 就行，不用手写 `@pytest.mark.asyncio`。
- 不 mock `ToolUseContext` 以外的引擎内部；`ToolUseContext(cwd=tmp_path)` 是最省事的构造。

### 3.4 事件改动要连带看的测试

动了 `session/events.py` 或任何事件的发射点，这几个文件是守门人：

| 文件 | 守什么 |
|---|---|
| `test_event_coverage.py` | 每个 `KNOWN_EVENT_TYPES` 成员都有真实发射路径；数量断言 |
| `test_event_ordering.py` | 事件相对顺序 |
| `test_session_invariant.py` | 请求前一致性门禁（`derive_messages` 重建 == 运行时状态） |
| `test_transcript_events.py` | 落盘与读取 |

新增事件类型时，`KNOWN_EVENT_TYPES` 与 `test_event_coverage.py` 的数量断言会一起失败 —— 这是**有意**的，别去改断言绕过去。

### 3.5 常用命令

```bash
uv run pytest                          # 全量
uv run pytest tests/test_pipeline.py   # 单文件
uv run pytest -k "compact and not shell"  # 按表达式
uv run pytest -x --lf                  # 失败即停 + 只跑上次失败的
uv run pytest --cov=nono_agent --cov-report=term-missing   # 覆盖率
```

---

## 4. 提交规范

见 [提交规范](commit-convention.md)（Conventional Commits）。

---

## 相关页面

- [开发环境与目录结构](index.md)
- [提交规范](commit-convention.md)
- [事件目录](../architecture/session-event-catalog.md)
