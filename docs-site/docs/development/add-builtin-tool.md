---
title: 新增内置工具
---

# 新增内置工具

以一个具体工具为例，从零走完：写实现 → 注册 → 测试 → 文档。

!!! tip "先问一句：它该是内置工具还是工具插件？"
    **内置工具 = 零配置即用的原语**，不带密钥、不需要开关、不依赖具体厂商
    （`read_file` / `glob` / `bash` 这类）。
    **要密钥、要开关、要 HTTP 端点 → 走工具插件**（`http_json` / `mcp` 两种声明），
    见 [新增工具插件](add-tool-plugin.md)。
    内置工具表里不该塞一堆不配置就报错的东西。

---

## 1. 实现

在 `nono_agent/tools/builtin/` 新建一个文件，继承 `ToolBase`：

```python
# nono_agent/tools/builtin/word_count.py
"""WordCount 工具 —— 统计文本的词数与行数。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nono_agent.core.types import ToolResult
from nono_agent.tools.builtin._common import error_result, ok_result, resolve_path
from nono_agent.tools.protocol import ToolBase


class WordCountTool(ToolBase):
    """统计文本的行数、词数与字符数。"""

    _name = "word_count"
    _description = (
        "Count lines, words and characters in a text file. "
        "Returns a short one-line summary."
    )
    _input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File to read (relative to cwd or absolute).",
            },
        },
        "required": ["path"],
    }
    # 纯读、无副作用 —— 这两个必须显式声明，不能吃默认值
    _is_concurrent_safe = True
    _has_write_side_effects = False

    async def execute(self, input_params: dict[str, Any], context: Any) -> ToolResult:
        raw = input_params.get("path")
        if not raw:
            return error_result(self._name, "No path provided.")

        try:
            target = Path(resolve_path(context, str(raw)))
        except ValueError as exc:          # 沙箱拒绝
            return error_result(self._name, str(exc))

        if not target.is_file():
            return error_result(self._name, f"Not a file: {target}")

        text = target.read_text(encoding="utf-8", errors="replace")
        lines = len(text.splitlines())
        words = len(text.split())
        return ok_result(self._name, f"{lines} lines, {words} words, {len(text)} chars")
```

### 必须遵守的约定

| 项 | 规则 |
|---|---|
| **返回值** | 只用 `_common` 里的 `ok_result()` / `error_result()` 构造 `ToolResult`。别自己拼 `ToolResult(content=...)` —— 错误路径要带 `is_error=True`，否则模型会把失败当成功继续往下走。 |
| **路径解析** | 一律走 `_common.resolve_path(context, path)`，它会过 `PathSandbox`。直接 `Path(path) / os.path.join` 就能绕掉沙箱。 |
| **ignore 匹配** | 列举/搜索类工具要读 `context.ignore`（`IgnoreMatcher`）并跳过 `.gitignore` / `.nonoignore` 命中的路径。 |
| **取消** | 长循环里定期查 `context.signal.aborted`。 |
| **并发安全** | 只有真正无副作用、无内部状态竞争的工具才标 `_is_concurrent_safe = True`。基类默认是 `False`，**不要**为了性能顺手改成 True。 |
| **写副作用** | 会改文件或外部状态的保持默认 `True`（或显式 `True`）。它决定权限决策和 `/permission` 提示。 |
| **描述文本** | 写给模型看，要说清「什么时候用我」「参数什么意思」，不是写给人的使用手册。 |

---

## 2. 注册

在 `nono_agent/tools/builtin/__init__.py` 加导入、加入 `__all__`、加进工厂：

```python
from nono_agent.tools.builtin.word_count import WordCountTool

# ... __all__ 里加 "WordCountTool",

def create_builtin_tools(config: EngineConfig | None = None) -> list[ToolBase]:
    tools: list[ToolBase] = [
        # ... 原有 18 个 ...
        WordCountTool(),
    ]
    tools.extend(create_task_tools(TaskStore()))
    return tools
```

工厂现在返回 **23 个**工具。`config` 形参保持可省略 —— CLI 和 Server 两个生产调用点都传它，但测试和第三方可以直接零参调用。

### 需要工具之间共享状态时

后台作业那套是范例：`bash` 把它登记的作业交给注入的 `JobRegistry`，`job_output` / `job_kill` 从同一个注册表读。三者**必须同源**，否则模型读不到自己起的进程。共享容器经 `ToolUseContext` 注入（`context.shell_jobs`），不要用模块级全局。

---

## 3. 权限

工具默认走 `ToolUseContext.check_permission_decision()`：注入 `PermissionPolicy` 时按三态规则（`ALLOW` / `ASK` / `DENY`，规则优先级 `deny > allow > ask > 模式默认`）判定；没注入就回落到 `permission_checker` 布尔回调。

如果你的工具需要**特殊权限语义**（而不是「有写副作用就问」），覆写：

```python
def should_prompt_for_permission(self, input_params, context) -> bool:
    # 例：读操作里只有越过沙箱才问
    return not str(input_params.get("path", "")).startswith("safe-prefix/")
```

Server / 桌面的真实链路是 `make_permission_checker(session_id)` **按会话挂起询问**（`GET /app/permissions` 拉取、`POST /app/permissions/{request_id}/answer` 回填），不是恒拒。只有直接调 `create_server_app()` 且不传任何 checker 时，才落到默认的 `deny_all_permission_checker`（恒拒写副作用 + 对 `_SESSION_SCOPED_TOOLS` 里的 `job_kill` 放行）。所以新增写工具在桌面端**默认是可用的**（会弹窗）；若你判断某类工具不该让用户逐次确认，那是产品决策，去改 `apps/server/.../features/permissions/` 的策略，先问。

---

## 4. 测试

```python
# tests/test_builtin_tools.py
import pytest

from nono_agent.tools.builtin.word_count import WordCountTool
from nono_agent.tools.context import ToolUseContext
from nono_agent.core.types import ToolResult


def _ctx(tmp_path) -> ToolUseContext:
    return ToolUseContext(cwd=str(tmp_path))


async def test_counts_lines_and_words(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hello world\nsecond line\n", encoding="utf-8")

    result = await WordCountTool().execute({"path": "a.txt"}, _ctx(tmp_path))

    assert result.is_error is False
    assert "2 lines" in result.content
    assert "4 words" in result.content


async def test_missing_file_is_error(tmp_path):
    result = await WordCountTool().execute({"path": "nope.txt"}, _ctx(tmp_path))
    assert result.is_error is True
```

再在 `tests/test_builtin_tools.py` 里加一条工厂断言，确认新工具进了 `create_builtin_tools()`：

```python
def test_factory_includes_word_count():
    names = {t.name for t in create_builtin_tools()}
    assert "word_count" in names
```

跑：

```bash
uv run pytest tests/test_builtin_tools.py -v
uv run ruff check nono_agent/tools/builtin/word_count.py
uv run mypy nono_agent/
```

---

## 5. 顺带要更新的地方

| 位置 | 改什么 |
|---|---|
| `README.md` 现状表 | 「23 个内置工具」→ 新的数量与名字 |
| `docs-site/docs/guide/usage.md` §4 | `create_builtin_tools()` 返回值清单 |
| `docs-site/docs/architecture/nono-agent-architecture.md` | 「23 个内置工具」→ 新数量 |
| `docs-site/docs/roadmap/index.md` | 同上（多处） |
| `docs-site/docs/development/commit-convention.md` | 若有 commit 格式约定要补充 |

---

## 6. 检查清单

- [ ] 继承 `ToolBase`，只覆写 `_name` / `_description` / `_input_schema` / `execute`
- [ ] 显式声明 `_is_concurrent_safe` 与 `_has_write_side_effects`
- [ ] 路径过 `resolve_path()`，列举类过 `context.ignore`
- [ ] 错误路径走 `error_result()`（`is_error=True`）
- [ ] 在 `create_builtin_tools()` 与 `__all__` 注册
- [ ] 工具数量在 4 处文档里同步
- [ ] `pytest` / `ruff` / `mypy` 全绿

---

## 相关页面

- [新增工具插件](add-tool-plugin.md)（要密钥、要开关的能力走这条路）
- [工具与权限](../guide/tools-and-permissions.md)
- [开发环境与目录结构](index.md)
