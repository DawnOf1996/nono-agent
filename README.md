# Nono Agent

Python AI Agent **运行时引擎**（`0.1.0`），架构灵感来自 Claude Code 的 QueryEngine。

引擎（`nono_agent/`）只做一件事：把「一句话」变成「一串可审计的事件」。所有交互外壳——CLI、Sidecar、Web、桌面、会话查看器——都是同一份引擎的消费方：形态不同，引擎只有一份。

## 目录约定

README 里每段命令都标了**执行目录**。下表是这些目录的分工，新接手时先记住这张表：

| 目录 | 是什么 | 谁在里面跑 |
|------|--------|------------|
| 仓库根 | 含 `pyproject.toml` / `uv.lock` / `.venv` 的根目录 | `uv run …`、pytest / ruff / mypy、mkdocs |
| `apps/` | pnpm workspace 根（`pnpm-workspace.yaml`） | `pnpm dev:web`、`pnpm dev:desktop` |
| `apps/cli/` | 终端 CLI 的 Python 包 | 一般不在这里跑，根目录 `uv run nono` 即可 |
| `apps/server/` | Sidecar 的 Python 包 | `uv run nono-app-server`（工作目录决定它读哪份配置） |
| `apps/web/` | Vite + React 前端 | 由 `apps/` 下的 `pnpm dev:web` 拉起 |
| `apps/desktop/` | Electron 桌面壳 | 由 `apps/` 下的 `pnpm dev:desktop` 拉起 |
| `session-viewer/` | 会话轨迹查看器（自带 uv 环境） | `uv run python server.py` |
| `scripts/` | 打包与运维脚本 | `.\scripts\dev.ps1`、`.\scripts\pack-desktop.cmd` |
| `docs-site/` | MkDocs 文档站源码 | 命令在**仓库根**执行，路径写 `docs-site/mkdocs.yml` |

## 产品形态

| 形态 | 代码位置 | 执行目录 | 启动命令 | 访问入口 |
|------|----------|----------|----------|----------|
| **引擎库**（SDK） | `nono_agent/` | 任意 Python 项目 | `pip install -e .` 后 `import` | 进程内调用 |
| **终端 CLI** | `apps/cli/` | 仓库根 | `uv run nono chat` | 终端 |
| **本机 Sidecar**（SSE） | `apps/server/` | 仓库根 | `uv run nono-app-server` | http://127.0.0.1:8000 |
| **Web 工作台** | `apps/web/` | `apps/` | `pnpm dev:web` | http://127.0.0.1:5173 |
| **桌面应用** | `apps/desktop/` | `apps/` | `pnpm dev:desktop` | Electron 窗口（NoNo） |
| **会话查看器** | `session-viewer/` | 仓库根 | `uv run python session-viewer/server.py` | http://127.0.0.1:8765 |

> Sidecar / Web / 桌面是同一条链路的不同前端：Web 页面与 Electron 壳都通过 HTTP + SSE 消费 Sidecar，Sidecar 内部调用的才是引擎。

## 环境准备

- Python **3.12+** 与 [uv](https://docs.astral.sh/uv/)
- Node **20+** 与 pnpm（仅 Web / 桌面形态需要）
- Provider API Key（`ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / …）

```bash
# 目录：仓库根 —— 安装引擎 + CLI + Sidecar
uv sync --extra dev
# 或
pip install -e ".[dev]"
```

前端与桌面依赖单独装。pnpm workspace 根在 `apps/`，**不要**在仓库根跑 `pnpm install`：

```bash
# 目录：apps/server —— Sidecar 依赖
cd apps/server && uv sync
```

```bash
# 目录：apps —— Web / Electron 依赖
cd apps && pnpm install
```

首次交互式运行会引导写入 `~/.nono-agent/config.yaml`（供应商 / 协议 / 模型目录）；API Key 存在 `~/.nono-agent/credentials`，YAML 只保留自动生成的 `api_key_env` 指针。

## 启动方式

### 1. 引擎库

```python
# 目录：任意 Python 项目（仓库内现成示例见 examples/，需已安装 nono-agent）
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

自定义端点（OpenAI 兼容）走 `ProviderConfig`：

```python
# 目录：任意 Python 项目
from nono_agent import EngineConfig, ProviderConfig

config = EngineConfig(
    model="my-model",
    provider=ProviderConfig(
        model_id="my-model",
        adapter="openai_chat_completions",  # 或 "openai_responses" / "anthropic_messages"
        api_key="...",                      # 建议从环境变量读取
        base_url="https://api.example.com/v1",
    ),
)
```

完整示例见 [`examples/`](examples/)（`engine_basic.py` 库用法、`custom_provider.py` 自定义 Provider）。

### 2. 终端 CLI

```bash
# 目录：仓库根
uv run nono chat                                  # 交互式
uv run nono chat -m "你好" --model MiniMax-M3 --provider a
uv run nono chat --log-api-requests              # 发 HTTP 前 dump 请求体
uv run nono sessions                              # 列出历史会话
```

入口是 `uv run nono`（等价 `nono-app-cli`）。详见 [`apps/cli/README.md`](apps/cli/README.md)。

### 3. 本机 Sidecar（SSE）

```bash
# 目录：仓库根
uv run nono-app-server
```

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 健康检查 |
| `POST` | `/session/new` | 新建会话 |
| `POST` | `/chat` | 发消息（SSE 流） |
| `GET` | `/session/<id>/events` | 会话事件流（SSE，脱敏后推送） |
| `GET` | `/app/...` | 产品能力（目录 / MCP / 工具插件 / 权限 / 文件 / Git / 作业 / 终端 / 工作区） |

端口用 `NONO_PORT` 覆盖（默认 8000），`NONO_CWD` 决定它读哪份配置与工作目录。

### 4. Web 工作台

三个终端，各起一个：

```bash
# 终端 1 —— 目录：apps/server（cwd 决定它读哪份配置）
cd apps/server && uv run nono-app-server
```

```bash
# 终端 2 —— 目录：apps（pnpm workspace 根）
cd apps && pnpm dev:web
```

```bash
# 终端 3 —— 浏览器打开 http://127.0.0.1:5173
```

Vite 会把 `/app` 代理到 Sidecar，端口在**启动时**固化——改了 `NONO_PORT` 必须重启 Vite。

不接模型调试时用假 Sidecar（逐片吐 SSE，不烧额度）：

```bash
# 终端 1 —— 目录：仓库根
node scripts/dev/mock-sidecar.mjs --port 8010 --tokens 8000
```

```powershell
# 终端 2 —— 目录：apps
cd apps
$env:NONO_SIDECAR_URL="http://127.0.0.1:8010"; pnpm dev:web
```

```bash
# 终端 2 —— 目录：apps（bash 写法）
cd apps && NONO_SIDECAR_URL="http://127.0.0.1:8010" pnpm dev:web
```

### 5. 桌面应用

```bash
# 目录：apps（pnpm workspace 根）
cd apps && pnpm dev:desktop
```

Windows 上也可以在仓库根执行包装脚本：

```powershell
# 目录：仓库根
.\scripts\dev.ps1
```

桌面壳自己拉起整条链路（sidecar 8000 + Vite 5173 + Electron），逻辑与打包态一致。端口已被占用时**不接管**，会弹窗说明是谁占着。

打包 Windows 版：

```powershell
# 目录：仓库根
.\scripts\pack-desktop.cmd
```

产物：`apps/desktop/release/win-unpacked/NoNo.exe`（免安装）与 `apps/desktop/release/NoNo-<x.y.yyyyMMddHH>-setup.exe`（NSIS 安装包）。详见 [`apps/README.md`](apps/README.md)。

### 6. 会话查看器

把 `~/.nono-agent/sessions/` 里的会话事件流渲染成轨迹时间线与对话视图。

```bash
# 目录：仓库根（复用根 .venv）；也可进 session-viewer/ 用它自己的 uv 环境
uv run python session-viewer/server.py   # → http://127.0.0.1:8765
```

```bash
# 目录：session-viewer —— 独立虚拟环境
cd session-viewer && uv sync && uv run python server.py
```

详见 [`session-viewer/README.md`](session-viewer/README.md)。

## 设计要点

- **单一公开 API：** `submit_message()` → `AsyncGenerator[SDKMessage]`，CLI 与 Sidecar 消费同一个流
- **模型可见 ⟺ 日志化：** 落盘只有事件，消息数组由 `derive_messages()` 推导；每次请求前校验「事件流重建 == 会话状态」，不一致即终止本轮
- **强类型状态机：** 转移 / 终止原因穷举
- **Fail-closed 工具默认：** 默认非并发安全、默认假定有写副作用
- **协议可插拔：** Provider / Tool / Pipeline / Middleware / Transcript / Permission

## 开发

```bash
# 目录：仓库根
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy nono_agent/
```

文档站本地预览：

```bash
# 目录：仓库根 —— mkdocs.yml 的路径相对当前目录，所以写 docs-site/mkdocs.yml
uv sync --extra dev --group docs
uv run mkdocs serve -f docs-site/mkdocs.yml
# → http://127.0.0.1:8001（端口 8001，避开 sidecar 的 8000）
```

## 文档索引

| 分类 | 文档 | 说明 |
|------|------|------|
| 上手 | [快速开始](docs-site/docs/getting-started/quickstart.md) | 三种入口的上手路径 |
| 上手 | [安装](docs-site/docs/getting-started/installation.md) | 环境与依赖 |
| 上手 | [配置与密钥](docs-site/docs/getting-started/configuration.md) | 供应商目录、密钥分层、环境变量 |
| 使用 | [使用手册](docs-site/docs/guide/usage.md) | 库 / CLI / Server / 配置 / 工具 / 测试 |
| 使用 | [工具与权限](docs-site/docs/guide/tools-and-permissions.md) | 内置工具、工具插件、权限模式 |
| 使用 | [会话与记忆](docs-site/docs/guide/session-and-memory.md) | 会话治理、事件溯源、记忆系统 |
| 架构 | [架构总览](docs-site/docs/architecture/nono-agent-architecture.md) | 设计理念与系统架构 |
| 架构 | [会话事件溯源](docs-site/docs/architecture/session-observability.md) | 门禁、后端、可观测性 |
| 架构 | [会话事件目录](docs-site/docs/architecture/session-event-catalog.md) | 事件类型、时机、顺序与载荷示例 |
| 架构 | [记忆系统](docs-site/docs/architecture/memory-system.md) | 跨会话记忆的准入、检索、反思 |
| 架构 | [上下文压缩](docs-site/docs/architecture/context-compression.md) | 预处理管线与触发阈值 |
| 架构 | [图片资产管线](docs-site/docs/architecture/asset-pipeline.md) | 图片附件的处理链路 |
| 架构 | [Shell 工具](docs-site/docs/architecture/shell-tool.md) | 方言、编码、流分离、超限落盘、超时晋升 |
| 开发 | [开发环境与目录结构](docs-site/docs/development/index.md) | 目录结构与工程约定 |
| 开发 | [新增内置工具](docs-site/docs/development/add-builtin-tool.md) | 加一个内置工具的完整流程 |
| 开发 | [新增工具插件](docs-site/docs/development/add-tool-plugin.md) | 声明式插件框架 |
| 开发 | [新增 Provider 适配器](docs-site/docs/development/add-provider.md) | 接一个新协议 |
| 开发 | [测试与代码规范](docs-site/docs/development/testing-and-style.md) | pytest / ruff / mypy 规范 |
| 开发 | [提交规范](docs-site/docs/development/commit-convention.md) | Conventional Commits |
| 学习 | [学习路径总览](docs-site/docs/learn/index.md) | 六篇主线：Agent 是什么 → 怎么做的 |
| 规划 | [路线图](docs-site/docs/roadmap/index.md) | 能力规划与未实现项 |
| 产品层 | [apps/README.md](apps/README.md) | Web / 桌面开发、端口、打包 |
| 工具 | [session-viewer/README.md](session-viewer/README.md) | 会话轨迹 / 对话可视化 |
| 文档站 | [docs-site/README.md](docs-site/README.md) | MkDocs 站点结构与预览说明 |

## License

MIT
