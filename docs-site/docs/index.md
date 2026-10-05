---
title: Nono Agent
---

# Nono Agent

**Python AI Agent 运行时引擎**（`0.1.0`），架构灵感来自 Claude Code 的 QueryEngine。

Nono Agent 把「模型调用」和「模型能看到什么」拆成两条正交的轴：一条是 Provider / Tool / Pipeline / Middleware / Transcript / Permission 六个可插拔协议，一条是只追加的事件流。所有状态从事件流推导，绕过日志改上下文没有存活空间。

<div class="grid cards" markdown>

- :material-rocket-launch: **快速开始**

    5 分钟跑起第一次对话，含库、CLI、Server 三种入口

    [快速开始 :octicons-arrow-right-24:](getting-started/quickstart.md)

- :material-book-open-variant: **使用手册**

    配置分层、密钥管理、工具插件、内置工具、权限模式

    [使用手册 :octicons-arrow-right-24:](guide/usage.md)

- :material-sitemap: **架构设计**

    事件溯源、记忆系统、上下文压缩、Shell 工具的设计与实现

    [架构总览 :octicons-arrow-right-24:](architecture/nono-agent-architecture.md)

- :material-code-braces: **开发指南**

    加一个内置工具、写一个工具插件、接一个新 Provider

    [开发指南 :octicons-arrow-right-24:](development/index.md)

</div>

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

## 60 秒上手

```python
import asyncio
from nono_agent import QueryEngine, EngineConfig

async def main():
    config = EngineConfig(model="claude-sonnet-4-20250514", cwd=".")
    async with QueryEngine(config) as engine:
        async for msg in engine.submit_message("Hello!"):
            if msg.type == "assistant":
                print(msg.message)
            elif msg.type == "result":
                u = msg.usage
                print(f"done: {msg.num_turns} turns, in={u.input_tokens} out={u.output_tokens} tokens")

asyncio.run(main())
```

## 能力现状

| 已有 | 尚未建成 |
|------|----------|
| `QueryEngine.submit_message()` 流式 API | WebSocket |
| Provider 三类协议（openai_chat_completions / openai_responses / anthropic_messages） | MCP OAuth 全量 / SSE transport |
| 23 个内置工具，只保留零配置原语，厂商能力走工具插件 | Transcript Postgres/Redis |
| 默认 4 级上下文压缩管线 + 可配置 ContextCollapse summarizer | 多模态附件产品化（P2） |
| Prompt Section 组装（静态/动态边界 + 优先级合并）+ 结构化输出闭环 | Agent Teams（P2 可选） |
| Rules + Skills（SkillTool）+ 子 Agent 三件套 + Slash MVP | HTTP Hooks 执行器（命令执行器已有） |
| 权限模式 + PermissionRule + 路径沙箱 + denial 追踪 | OS 级硬沙箱（容器/bubblewrap） |
| Shell 能力缝（方言/编码声明/进程组终止/超限落盘/超时转后台作业） | 持久化 Shell（PTY） |
| Settings 分层 + 用户态 Hooks（`hooks.json`）+ CLI 首次运行引导页 | — |
| MCP 客户端（stdio tools/resources 桥接）+ Plugins | — |
| Memory（memdir）+ Checkpoint + Task 工具族 | — |
| 工具协议、注册表、流式执行器 + CI（pytest+ruff+mypy） | — |
| 内置工具插件框架（`toolplugins/`）：引擎侧不含具体插件，产品侧注册 minimax-image / tavily | 第三方插件市场与签名 |
| 事件溯源会话日志（`TranscriptWriter` + JSONL/SQLite，落盘只有事件）+ 请求前一致性门禁 | — |
| 可观测性：TokenTracker + structlog + Metrics（事件流投影）+ 脱敏导出 + 不调模型回放 | — |
| CLI + Server（SSE，含鉴权/TTL/AskUser 回调 + 会话事件流通道） | — |

完整规划见 [路线图](roadmap/index.md)。

## 设计要点

- **单一公开 API：** `submit_message()` → `AsyncGenerator[SDKMessage]`
- **不可变配置 + 运行时快照：** 避免循环内读到变化的环境状态
- **强类型状态机：** 转移 / 终止原因穷举
- **Fail-closed 工具默认：** 默认非并发安全、默认假定有写副作用
- **事件溯源会话日志：** 引擎只向事件流追加 `SessionEvent`，落盘只有事件；消息数组由 `derive_messages()` 推导，resume / fork / 回放都走同一条日志
- **模型可见 ⟺ 日志化：** 每次 LLM 请求前校验「事件流重建 == 会话状态」，不一致即终止本轮
- **协议可插拔：** Provider / Tool / Pipeline / Middleware / Transcript / Permission

## 仓库结构

```text
nono-agent/
├── nono_agent/        # 引擎：23 个子系统，零产品依赖
├── apps/              # 产品层：cli / server / web / desktop
├── tests/             # 75 个测试模块（1016 条用例）
├── examples/          # 库用法示例
├── scripts/           # 打包与运维脚本
└── docs-site/         # 本文档站
```

## License

MIT
