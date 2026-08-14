# Nono Agent

Python AI Agent **运行时引擎**（`0.1.0`），灵感来自 Claude Code 的 QueryEngine 架构。

当前定位是可扩展的 **Agent 骨架**：核心循环、Provider 适配、工具协议与 CLI/SSE 双侧适配已可跑通；相对 Claude Code 运行时深度（Prompt 组装、权限规则、Agent 三件套、Rules/Skills/Plugins/MCP 等）仍在规划中，见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 现状一览

| 已有 | 尚未建成 |
|------|----------|
| `QueryEngine.submit_message()` 流式 API | LSP / WebFetch / Worktree / TodoWrite 工具 |
| Anthropic / OpenAI / OpenAI-Compatible Provider | Skills（含 SkillTool）、Plugins、MCP（含 Resources/Auth） |
| 生产级内置工具（Bash/Read/Write/Edit/Glob/Grep）+ AskUserQuestion（CLI） | Settings 分层、Slash MVP、子 Agent 三件套 |
| 默认 4 级上下文压缩管线 | DB/Redis、Plan/Memory/Checkpoint |
| Prompt Section 组装（静态/动态边界 + 优先级合并） | Provider 兼容矩阵补齐、WebSocket、真实 Metrics |
| Rules 发现注入（`CLAUDE.md`/`AGENTS.md`/`.nono/rules`） | — |
| 权限模式 + PermissionRule + 路径沙箱 + denial 追踪 | — |
| 工具协议、注册表、流式执行器 | — |
| `TranscriptWriter` + 本地 NDJSON | — |
| CLI + Server（SSE） | — |

详细架构见 [docs/NonoAgent-Architecture.md](docs/NonoAgent-Architecture.md)，规划见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 要求

- Python **3.12+**
- 对应 Provider 的 API Key（如 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）

## 安装

```bash
# 推荐使用 uv
uv sync

# 或 pip（可编辑安装）
pip install -e ".[dev]"
```

## 快速开始

### 作为库使用

```python
import asyncio
from nono_agent import QueryEngine, EngineConfig

async def main():
    config = EngineConfig(
        model="claude-sonnet-4-20250514",
        cwd=".",
    )
    async with QueryEngine(config) as engine:
        async for msg in engine.submit_message("Hello!"):
            if msg.type == "assistant":
                print(msg.message)
            elif msg.type == "result":
                print(f"done: {msg.num_turns} turns, ${msg.total_cost_usd:.4f}")

asyncio.run(main())
```

自定义端点（OpenAI 兼容）可通过 `ProviderConfig`：

```python
from nono_agent import EngineConfig, ProviderConfig

config = EngineConfig(
    model="my-model",
    provider=ProviderConfig(
        model_id="my-model",
        adapter="openai",  # 或 "anthropic"
        api_key="...",     # 建议从环境变量读取
        base_url="https://api.example.com/v1",
    ),
)
```

### CLI

```bash
python -m nono_agent.adapters.cli chat --model claude-sonnet-4-20250514
python -m nono_agent.adapters.cli chat -m "你好" --model claude-sonnet-4-20250514
```

### Server（SSE）

```bash
python examples/server_basic.py
# POST /session/new
# POST /chat  （SSE 流）
```

更多示例见 `examples/`。

## 设计要点

- **单一公开 API：** `submit_message()` → `AsyncGenerator[SDKMessage]`
- **不可变配置 + 运行时快照：** 避免循环内读到变化的环境状态
- **强类型状态机：** 转移 / 终止原因穷举
- **Fail-closed 工具默认：** 默认非并发安全、默认假定有写副作用
- **协议可插拔：** Provider / Tool / Pipeline / Middleware / Transcript / Permission

## 文档

| 文档 | 说明 |
|------|------|
| [docs/NonoAgent-Architecture.md](docs/NonoAgent-Architecture.md) | 设计理念与系统架构 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 未实现能力与打磨规划 |
| [docs/COMMIT.md](docs/COMMIT.md) | Git 提交规范（Conventional Commits） |

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## License

MIT
