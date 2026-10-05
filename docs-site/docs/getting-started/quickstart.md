---
title: 快速开始
---

# 快速开始

三种入口：作为库、终端 CLI、HTTP Server。选哪种取决于你要把它嵌进什么。

---

## 1. 作为库（集成用）

唯一公开 API 是 `QueryEngine.submit_message()`，返回 `AsyncGenerator[SDKMessage]`。

```python
import asyncio
from nono_agent import QueryEngine, EngineConfig, ProviderConfig

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

### 接自定义 / 第三方端点

```python
config = EngineConfig(
    model="my-model",
    provider=ProviderConfig(
        model_id="my-model",
        adapter="openai_chat_completions",   # 或 openai_responses / anthropic_messages
        api_key="...",                        # 建议从环境变量读
        base_url="https://api.example.com/v1",
    ),
)
```

`adapter` **必须显式指定**，它按 API 形态选协议，供应商由 `api_key` + `base_url` 决定。
同一端点用 `openai_chat_completions` 接和用 `openai_responses` 接是两个不同的适配器实例。

更多示例见 `examples/engine_basic.py`、`examples/custom_provider.py`。

---

## 2. 终端 CLI

产品入口在 `apps/cli`。仓库根执行过 `uv sync --extra dev` 后：

```bash
uv run nono chat                                  # 交互式对话
uv run nono chat -m "你好" --model MiniMax-M3      # 单条消息
uv run nono chat --cwd D:\Work\MyProject          # 指定工作目录
uv run nono chat --resume <session_id>            # 恢复历史会话
uv run nono sessions                              # 列出本地会话
```

首次交互式运行会进入引导：供应商 id、协议、Base URL、**真实 API Key**、模型信息。
目录写 `~/.nono-agent/config.yaml`（只含 `api_key_env` 指针），密钥写 `~/.nono-agent/credentials`，
并立刻注入当前进程环境变量 —— **不用重启**。

全部参数：`--model` / `--provider` / `--cwd` / `--resume` / `--message|-m` / `--config|-c` /
`--verbose|-v` / `--log-api-requests`。

`--resume` 以该会话的 `.header.json` 为约束：`cwd`、`permission_mode`、`agent_preset_id` 都被 header 覆盖，
不必再手动传 `--cwd`。

---

## 3. HTTP Server（SSE）

产品入口在 `apps/server`：

```bash
uv run nono-app-server
```

```bash
curl http://localhost:8000/health

curl -X POST http://localhost:8000/session/new

curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello!","session_id":"<上一步返回的 session_id>"}'

curl http://localhost:8000/sessions
curl -X DELETE http://localhost:8000/session/<session_id>
curl -X POST http://localhost:8000/abort -d '{"session_id":"..."}' -H "Content-Type: application/json"

# 实时看会话事件流（只读，脱敏后推送）
curl -N http://localhost:8000/session/<session_id>/events
```

服务端可选能力（`create_server_app(..., api_key=..., session_ttl=...)`）：

- **鉴权**：设了 `api_key` 后，除 `/health` 外需 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。
- **会话 TTL**：`session_ttl`（秒）空闲超时后台清理。
- **AskUser 回调**：流里收到 `ask_user` 事件后，用 `POST /session/<id>/answer` 回填 `{"answers": {...}}`。
- **事件流通道**：`GET /session/<id>/events` 支持 `after_seq` 断点续传、`follow=false` 只取快照。

---

## 4. 桌面版与 Web

```bash
# Web 前端（Vite dev server，默认 5173）
# pnpm workspace 的根在 apps/，不是仓库根 —— 两条命令都得在 apps/ 下跑
cd apps
pnpm install
pnpm dev:web            # 等价于 pnpm --filter @nono/web dev

# 桌面版打包（Windows，从仓库根执行）
.\scripts\pack-desktop.cmd
```

!!! warning "不要在仓库根跑 `pnpm install`"
    仓库根没有 `package.json` / `pnpm-workspace.yaml`，workspace 由 `apps/package.json`
    的 `workspaces: ["web", "desktop", "packages/*"]` 声明。根目录跑 pnpm 过滤不到任何项目。

前端通过 `NONO_SIDECAR_URL` 指向 Server。开发期可以用 mock sidecar 脱离真实模型：
`node scripts/dev/mock-sidecar.mjs`。

详见 [apps/README.md](https://gitee.com/dawn1996/nono-agent/blob/main/apps/README.md)。

---

## 下一步

- 配模型供应商与密钥 → [配置与密钥](configuration.md)
- 完整使用说明 → [使用手册](../guide/usage.md)
- 工具与权限怎么运作 → [工具与权限](../guide/tools-and-permissions.md)
- 要改代码 → [开发指南](../development/index.md)
