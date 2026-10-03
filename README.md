# Nono Agent

Python AI Agent **运行时引擎**（`0.1.0`），灵感来自 Claude Code 的 QueryEngine 架构。

已对齐 Claude Code 的 Agent 运行时深度：Prompt 组装、权限规则、Agent 三件套、Rules/Skills/Plugins/MCP、子 Agent、会话治理等均基本落地，见 [docs/ROADMAP.md](docs/ROADMAP.md)。

## 现状一览

| 已有 | 尚未建成 |
|------|----------|
| `QueryEngine.submit_message()` 流式 API | WebSocket |
| Provider 三类协议（openai_chat_completions / openai_responses / anthropic_messages） | MCP OAuth 全量 / SSE transport |
| 23 个内置工具（Bash/JobOutput/JobKill/Read/Write/Edit/Glob/Grep/LS/LSP/WebFetch/ToolSearch/AskUser/TodoWrite/Plan/Worktree/Task）——只保留零配置原语，厂商能力走工具插件 | Transcript Postgres/Redis |
| 默认 4 级上下文压缩管线 + 可配置 ContextCollapse summarizer | 多模态附件产品化（P2） |
| Prompt Section 组装（静态/动态边界 + 优先级合并）+ 结构化输出闭环 | Agent Teams（P2 可选） |
| Rules + Skills（SkillTool）+ 子 Agent 三件套 + Slash MVP | HTTP Hooks 执行器（命令执行器已有） |
| 权限模式 + PermissionRule + 路径沙箱 + denial 追踪 | OS 级硬沙箱（容器/bubblewrap） |
| Shell 能力缝（方言/编码声明/进程组终止/超限落盘/超时转后台作业） | 持久化 Shell（PTY） |
| Settings 分层 + 用户态 Hooks（hooks.json）+ CLI 首次运行引导页 | — |
| MCP 客户端（stdio tools/resources 桥接）+ Plugins | — |
| Memory（memdir）+ Checkpoint + Task 工具族 | — |
| 工具协议、注册表、流式执行器 + CI（pytest+ruff+mypy） | — |
| 内置工具插件（`toolplugins/`）：声明式插件框架（引擎侧不含具体插件），产品侧注册 minimax-image / tavily | 第三方插件市场与签名 |
| 事件溯源会话日志（`TranscriptWriter` + JSONL/SQLite，落盘只有事件）+ 请求前一致性门禁 | — |
| 可观测性：TokenTracker + structlog + Metrics（事件流投影）+ 脱敏导出 + 不调模型回放 | — |
| CLI + Server（SSE，含鉴权/TTL/AskUser 回调 + 会话事件流通道） | — |

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
                u = msg.usage
                print(f"done: {msg.num_turns} turns, in={u.input_tokens} out={u.output_tokens} tokens")

asyncio.run(main())
```

自定义端点（OpenAI 兼容）可通过 `ProviderConfig`：

```python
from nono_agent import EngineConfig, ProviderConfig

config = EngineConfig(
    model="my-model",
    provider=ProviderConfig(
        model_id="my-model",
        adapter="openai_chat_completions",  # 或 "openai_responses" / "anthropic_messages"
        api_key="...",     # 建议从环境变量读取
        base_url="https://api.example.com/v1",
    ),
)
```

### CLI

产品 CLI 在 [`apps/cli`](apps/cli)（`uv sync --extra dev` 后可在仓库根直接跑）：

```bash
uv run nono chat
uv run nono chat -m "你好" --model MiniMax-M3 --provider a
uv run nono chat --log-api-requests   # 发 HTTP 前 dump 请求体
uv run nono sessions   # 列出历史会话
```

首次交互式运行会引导写入 `~/.nono-agent/config.yaml`（供应商 / 协议 / 模型目录）；API Key 保存在 `~/.nono-agent/credentials`，YAML 只保留自动生成的 `api_key_env` 指针。详见 [docs/USAGE.md](docs/USAGE.md)。

### Server（SSE）

```bash
uv run nono-app-server
# POST /session/new
# POST /chat                     （SSE 流）
# GET  /session/<id>/events      （会话事件流，SSE，脱敏后推送）
```

桌面产品层（React + Electron + sidecar）与终端 CLI 在 [`apps/`](apps/README.md)，引擎包 `nono_agent/` 不再包含 CLI/Server 适配层。Windows 桌面版打包见 [`apps/README.md`](apps/README.md#打包-windows-桌面版)（仓库根 `.\scripts\pack-desktop.cmd`）。

更多示例见 `examples/`（`engine_basic.py` 库用法、`custom_provider.py` 自定义 Provider）。产品 CLI / Server 入口是 `uv run nono` 与 `uv run nono-app-server`。

## 设计要点

- **单一公开 API：** `submit_message()` → `AsyncGenerator[SDKMessage]`
- **不可变配置 + 运行时快照：** 避免循环内读到变化的环境状态
- **强类型状态机：** 转移 / 终止原因穷举
- **Fail-closed 工具默认：** 默认非并发安全、默认假定有写副作用
- **事件溯源会话日志：** 引擎只向事件流追加 `SessionEvent`，落盘只有事件；消息数组由 `derive_messages()` 推导，resume / fork / 回放都走同一条日志
- **模型可见 ⟺ 日志化：** 每次 LLM 请求前校验「事件流重建 == 会话状态」，不一致即终止本轮 —— 绕过日志改上下文没有存活空间
- **协议可插拔：** Provider / Tool / Pipeline / Middleware / Transcript / Permission

## 文档

| 文档 | 说明 |
|------|------|
| [docs/USAGE.md](docs/USAGE.md) | 使用与测试说明（库 / CLI / Server / 配置 / 工具 / 测试） |
| [apps/README.md](apps/README.md) | 产品层开发与 Windows 桌面版打包 |
| [docs/Session-Observability-Design.md](docs/Session-Observability-Design.md) | 会话事件溯源设计（门禁、后端、可观测性） |
| [docs/Session-Event-Catalog.md](docs/Session-Event-Catalog.md) | 19 类会话事件的时机、顺序与载荷示例 |
| [docs/NonoAgent-Architecture.md](docs/NonoAgent-Architecture.md) | 设计理念与系统架构 |
| [docs/ROADMAP.md](docs/ROADMAP.md) | 能力现状、未实现项与打磨规划 |
| [docs/COMMIT.md](docs/COMMIT.md) | Git 提交规范（Conventional Commits） |

## 开发

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
```

## License

MIT
