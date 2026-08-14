# Nono Agent —— 使用与测试说明

本文档面向「想跑起来 / 想写测试 / 想接入」的开发者，覆盖安装、三种使用方式（库 / CLI / Server）、配置、内置工具、扩展面与测试方法。所有命令均已在本仓库 `239` 个测试中验证；若某处与代码不符，以 `nono_agent/` 源码为准。

---

## 1. 环境要求与安装

- Python **3.12+**
- 推荐 `uv`（也可用 `pip`）

```bash
cd /data/workspace/projects/nono-agent
uv sync --extra dev     # 安装运行时依赖 + pytest/ruff/mypy
```

> 安装完成后用 `uv run ...` 前缀执行命令，或激活 `.venv`。

---

## 2. 三种使用方式

### 2.1 作为库（推荐用于集成）

唯一公开 API 是 `QueryEngine.submit_message()`，返回 `AsyncGenerator[SDKMessage]`：

```python
import asyncio
from nono_agent import QueryEngine, EngineConfig

async def main():
    config = EngineConfig(model="claude-sonnet-4-20250514", cwd=".")
    async with QueryEngine(config) as engine:
        async for msg in engine.submit_message("你好"):
            if msg.type == "assistant":
                print(msg.message)
            elif msg.type == "result":
                u = msg.usage
                print(f"done: {msg.num_turns} turns, in={u.input_tokens} out={u.output_tokens} tokens")

asyncio.run(main())
```

自定义端点（OpenAI 兼容 / 第三方）通过 `ProviderConfig`：

```python
from nono_agent import EngineConfig, ProviderConfig

config = EngineConfig(
    model="MiniMax-M3",
    provider=ProviderConfig(
        model_id="MiniMax-M3",
        adapter="openai",          # anthropic | messages_compatible | openai | responses | responses_compatible
        api_key="...",             # 建议从环境变量读取
        base_url="https://api.minimaxi.com/v1",
    ),
)
```

更多示例见 `examples/cli_basic.py`、`examples/custom_provider.py`。

### 2.2 CLI

```bash
# 交互式对话
uv run python -m nono_agent.adapters.cli chat

# 单条消息（非交互）
uv run python -m nono_agent.adapters.cli chat -m "你好" --model MiniMax-M3

# 恢复历史会话
uv run python -m nono_agent.adapters.cli chat --resume <session_id>

# 列出本地已保存的会话
uv run python -m nono_agent.adapters.cli sessions
```

CLI 参数：`--model` / `--cwd` / `--resume` / `--message|-m` / `--config|-c` / `--verbose|-v`。

### 2.3 Server（HTTP + SSE）

```bash
uv run python examples/server_basic.py
```

```bash
# 健康检查
curl http://localhost:8000/health

# 创建会话
curl -X POST http://localhost:8000/session/new

# 发消息（SSE 流）
curl -N -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message":"Hello!","session_id":"<上一步返回的 session_id>"}'

# 会话列表 / 关闭 / 中止
curl http://localhost:8000/sessions
curl -X DELETE http://localhost:8000/session/<session_id>
curl -X POST http://localhost:8000/abort -d '{"session_id":"..."}' -H "Content-Type: application/json"
```

服务端可选能力（`create_server_app(..., api_key=..., session_ttl=...)`）：
- **鉴权**：设置 `api_key` 后，除 `/health` 外需 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。
- **会话 TTL**：`session_ttl`（秒）空闲超时后台清理。
- **AskUser SSE**：流中收到 `ask_user` 事件后，用 `POST /session/<id>/answer` 回填 `{"answers": {...}}`。

---

## 3. 配置（外部化，禁止硬编码密钥）

### 3.1 环境变量

| 变量 | 用途 |
|------|------|
| `NONO_MODEL` / `NONO_ADAPTER` / `NONO_API_KEY` / `NONO_BASE_URL` / `NONO_CWD` | Provider 与模型路由 |
| `NONO_SEARCH_URL` / `NONO_SEARCH_API_KEY` | `web_search` 工具端点 |
| `NONO_LSP_COMMAND` / `NONO_LSP_ARGS`（逗号分隔） | `lsp` 工具的语言服务器 |
| `NONO_PERMISSION_MODE` | 权限模式 flag 覆盖（`default/accept_edits/plan/bypass`） |

### 3.2 配置文件（`--config` / 项目目录）

```json
{
  "model": "MiniMax-M3",
  "cwd": ".",
  "provider": {
    "adapter": "openai",
    "api_key": "sk-...",
    "base_url": "https://api.minimaxi.com/v1",
    "extra_body": {"reasoning_split": true}
  }
}
```

优先级：**命令行参数 > 环境变量 > `--config` 文件 > 用户基础配置 > 默认值**。

### 3.3 首次运行引导页

交互式运行 `chat`（不带 `-m`）时，若检测不到「必备配置」（协议 `adapter` + 密钥 `api_key`，来自环境变量 / `--config` / 用户基础配置），会进入**交互式引导**，按提示依次输入：① 协议、② 模型、③ API Key、④ Base URL（可选），完成后保存到用户基础配置：

```
~/.nono-agent/config.json
```

该文件格式与 `--config` 一致（`model` + `provider{adapter, api_key, base_url}`），之后每次启动自动读取，无需重复输入。

### 3.4 项目侧约定目录 `.nono/`

```
.nono/
├── settings.json      # 分层设置（default < user < project < policy < flag）
├── rules/*.md         # 常驻指令（也可用 AGENTS.md / CLAUDE.md）
├── skills/<name>/SKILL.md
├── agents/*.md        # 子 Agent 定义
├── plugins/<name>/plugin.json
├── hooks.json         # 用户态 Hooks
├── mcp.json           # MCP 服务器声明
└── memory/*.md        # 跨会话记忆
```

---

## 4. 内置工具（22 个）

`create_builtin_tools()` 返回：`bash`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`、`list_directory`、`lsp`、`web_fetch`、`web_search`、`tool_search`、`ask_user_question`、`todo_write`、`enter_plan_mode`、`exit_plan_mode`、`enter_worktree`、`exit_worktree`、`task_create`、`task_list`、`task_get`、`task_update`、`task_stop`。

要点：
- 文件工具遵守路径沙箱（`PathSandbox`）与 `.gitignore`/`.nonoignore`（`IgnoreMatcher`）。
- `web_fetch`/`web_search` 有 SSRF 防护（拒绝私网/回环/链路本地/云元数据）。
- `bash` 有危险命令分类（`rm -rf`/`mkfs`/`sudo`/fork bomb 等）与超时/截断/abort 联动。
- MCP 工具以 `mcp__<server>__<tool>` 命名，fail-closed（默认需授权）。

---

## 5. Slash 命令（REPL 内以 `/` 开头）

`/help` `/model` `/tokens` `/context` `/clear` `/compact` `/rules` `/skills` `/permission` `/mcp` `/plugins` `/memory` `/hooks`。

示例：`/permission plan`（进入计划模式）、`/skills disable <name>`、`/compact`（手动压缩上下文）。

---

## 6. 扩展面速查

- **Rules**：`.nono/rules/*.md`、`AGENTS.md`/`CLAUDE.md` 自动发现并注入 Prompt；frontmatter 支持 `globs`/`priority`。
- **Skills**：`SKILL.md`（frontmatter：`name`/`description`/`tools`），模型用 `skill` 工具激活。
- **子 Agent**：`.nono/agents/*.md`；内置 `general-purpose`/`explore`；模型用 `agent` 工具派发。
- **Plugins**：`plugin.json` 组合注册 rules/skills/agents/mcp/权限/自定义 slash。
- **MCP**：`.nono/mcp.json` 声明 stdio 服务器，桥接 tools + resources。
- **权限**：`PermissionMode`（`default/accept_edits/plan/bypass`）+ 可落盘 `PermissionRule`（工具名/path glob/bash 正则）。

---

## 7. 测试

### 7.1 一键跑全套

```bash
uv run pytest -q            # 239 个测试
uv run ruff check .         # lint
uv run mypy nono_agent/     # 类型检查（strict）
```

三者均为 CI 门禁（见 `.github/workflows/ci.yml`）。

### 7.2 测试基础设施（无需真实 API）

测试用 `MockProvider` / `MockTool` / `create_test_engine`，位于 `nono_agent/adapters/testing.py`：

```python
from nono_agent.adapters.testing import create_test_engine, MockTool

engine = create_test_engine(
    responses=[{"text": "回答", "tool_calls": []}],   # 脚本化模型响应
    tools=[MockTool(name="calculator", response="42", concurrent_safe=True, write_side_effects=False)],
)
async with engine:
    async for msg in engine.submit_message("hi"):
        ...  # 断言 SDKMessage
```

要点：
- `MockProvider(responses=[...])` 按序返回脚本化响应，`calls` 记录每次调用参数（可断言 system_prompt/tools/model）。
- 需要回退测试时，继承 `MockProvider` 抛 `FallbackTriggeredError`（见 `tests/test_fallback.py`）。
- 结构化输出测试用 `json_schema` 配置 + 返回 JSON 文本（见 `tests/test_structured_output.py`）。

### 7.3 各模块测试入口

| 测试文件 | 覆盖 |
|----------|------|
| `test_permissions.py` | PermissionMode/PermissionRule/沙箱/Ignore/Bash 分类/denial |
| `test_builtin_tools.py` | 22 个内置工具 + 工厂 |
| `test_mcp.py` | MCP stdio 客户端（mock 服务器） |
| `test_lsp.py` | LSP 客户端（mock 语言服务器） |
| `test_agents.py` / `test_skills.py` / `test_plugins.py` / `test_rules.py` | 扩展面 |
| `test_server.py` | Server 鉴权/AskUser 端点 |
| `test_budget.py` / `test_fallback.py` / `test_pipeline.py` | 引擎边界 |
| `test_db_transcript.py` / `test_checkpoint.py` / `test_memory.py` | 持久化 |

### 7.4 无外部依赖的 mock 技巧

- MCP / LSP 用 `tmp_path` 写一个临时 Python 脚本当「服务器」，`sys.executable` 启动（见 `tests/test_mcp.py`、`tests/test_lsp.py`）。
- 需要 git 仓库的测试（worktree）用 `pytest.mark.skipif(shutil.which("git") is None)` 保护。
- 网络工具只测纯函数（`is_url_blocked`），不发起真实请求。

---

## 8. 包结构速览

```
nono_agent/
├── core/          引擎内核（QueryEngine/loop/config/state/types）
├── pipeline/      预处理压缩管线（含 ContextCollapse）
├── recovery/      错误恢复决策链
├── providers/     协议矩阵（anthropic/openai/…/messages_compatible/responses(+compatible)）
├── session/       MessageStore + Transcript（NDJSON/SQLite + factory）
├── tools/         工具协议/注册表/执行器 + builtin/（22 个生产工具）
├── prompt/        Prompt Section 组装（静态/动态边界 + 优先级合并）
├── rules/ skills/ plugins/ commands/ settings/ permissions/ mcp/ agents/ memory/ checkpoint/
├── hooks/         引擎内 Middleware + 用户态 hooks.json
├── observability/ Token 跟踪/日志/指标
└── adapters/      CLI / Server / testing
```

详细设计见 [NonoAgent-Architecture.md](./NonoAgent-Architecture.md)，规划与状态见 [ROADMAP.md](./ROADMAP.md)。
