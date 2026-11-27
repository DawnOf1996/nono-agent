---
title: Nono Agent
---

# Nono Agent

**Python AI Agent 运行时引擎**（`0.1.0`），架构灵感来自 Claude Code 的 QueryEngine。

如果你**想学 Agent 是怎么做的**——从"模型其实只会输出文字"开始，
到"一个能自己调工具、调完还能记住、能回滚、能拦危险操作"的完整引擎——
从这里开始。

<div class="grid cards" markdown>

- :material-school: **学习 Agent 开发**

    六篇主线，讲清架构、模块、选型与取舍

    [从第一篇开始 :octicons-arrow-right-24:](learn/01-what-is-an-agent.md)

- :material-rocket-launch: **快速开始**

    5 分钟跑起第一次对话，库 / CLI / Server 三种入口

    [快速开始 :octicons-arrow-right-24:](getting-started/quickstart.md)

- :material-book-open-variant: **使用手册**

    配置分层、密钥管理、工具插件、权限模式

    [使用手册 :octicons-arrow-right-24:](guide/usage.md)

- :material-code-braces: **开发指南**

    加一个内置工具、写一个工具插件、接一个新 Provider

    [开发指南 :octicons-arrow-right-24:](development/index.md)

</div>

---

## 学习路径

想搞懂"整体架构什么样、每个模块干什么、为什么这么设计"，按顺序读这六篇。
每篇都建立在前一篇之上，不需要有 Agent 开发背景。

| # | 读什么 | 你会搞明白 |
|---|--------|-----------|
| 01 | [Agent 到底是个什么程序](learn/01-what-is-an-agent.md) | 用二十行代码看清 Agent 的本质，然后列出会逼你写框架的 9 个痛点 |
| 02 | [一次对话的完整生命周期](learn/02-request-lifecycle.md) | 一次输入从进去到出来，经过哪 6+7 个阶段，每个阶段为什么必须存在 |
| 03 | [整体架构](learn/03-architecture-overview.md) | 四个层次、两条正交的轴、数据怎么流 |
| 04 | [模块地图](learn/04-module-map.md) | 23 个子系统各自的功能点、边界，以及三条贯穿全局的规矩 |
| 05 | [技术选型](learn/05-tech-choices.md) | 每项技术"为什么是它、不是别的"，以及代价 |
| 06 | [设计取舍](learn/06-design-decisions.md) | 11 个关键决策背后的权衡逻辑 |

> **只读一篇的话**，读 [03 · 整体架构](learn/03-architecture-overview.md)。
> **想动手改代码**，先读 [01](learn/01-what-is-an-agent.md) 和 [06](learn/06-design-decisions.md)，
> 否则你会在"为什么不能直接改消息数组"这种地方卡住。

---

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

---

## 核心设计要点

想直接看结论、不想读长文，这六条是整个项目的骨架：

- **模型可见 ⟺ 日志化。** 落盘只有事件，消息数组由 `derive_messages()` 推导；
  每次请求前校验"事件流重建 == 会话状态"，不一致即终止本轮。
- **状态从事件流推导。** 绕过日志改上下文这件事，在架构上就没有存活空间。
- **强类型状态机。** 7 种转移原因 + 11 种终止原因，每次循环必须二选一。
- **Fail-closed 安全默认。** 工具默认不并发、默认有写副作用；权限默认先问。
- **单一对话入口。** `submit_message()` → `AsyncGenerator[SDKMessage]`，CLI 与 Server 消费同一个流；
  会话治理另走 `resume_session()` / `fork_session()` / `compact()` / `clear_session()` / `abort()`。
- **协议可插拔。** Provider / Tool / Pipeline / Middleware / Transcript / Permission 六个接缝。

每条的完整解释见 [06 · 设计取舍](learn/06-design-decisions.md)。

---

## 环境要求

- Python **3.12+**
- 对应 Provider 的 API Key（如 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`）

```bash
uv sync --extra dev          # 推荐 uv
# 或 pip install -e ".[dev]"
```

---

## 仓库结构

```text
nono-agent/
├── nono_agent/        # 引擎：23 个子系统，零产品依赖
├── apps/              # 产品层：cli / server / web / desktop
├── tests/             # 77 个测试模块
├── examples/          # 库用法示例
├── scripts/           # 打包与运维脚本
└── docs-site/         # 本文档站
```

---

## License

MIT