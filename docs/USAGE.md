# Nono Agent —— 使用与测试说明

本文档面向「想跑起来 / 想写测试 / 想接入」的开发者，覆盖安装、三种使用方式（库 / CLI / Server）、配置、内置工具、扩展面与测试方法。所有命令均已在本仓库全部测试中验证；若某处与代码不符，以 `nono_agent/` 源码为准。

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
from nono_agent import QueryEngine, EngineConfig, ProviderConfig

async def main():
    config = EngineConfig(
        model="MiniMax-M3",
        cwd=".",
        provider=ProviderConfig(
            model_id="MiniMax-M3",
            adapter="openai_responses",  # openai_chat_completions | anthropic_messages
            api_key="...",  # 建议从环境变量读取
            base_url="https://api.minimax.cn/v1",
        ),
    )
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
        adapter="openai_chat_completions",          # openai_responses | anthropic_messages
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

CLI 参数：`--model` / `--provider` / `--cwd` / `--resume` / `--message|-m` / `--config|-c` / `--verbose|-v`。

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

# 观测会话事件流（只读，脱敏后推送；详见 §7.5）
curl -N http://localhost:8000/session/<session_id>/events
```

服务端可选能力（`create_server_app(..., api_key=..., session_ttl=...)`）：
- **鉴权**：设置 `api_key` 后，除 `/health` 外需 `Authorization: Bearer <key>` 或 `X-API-Key: <key>`。
- **会话 TTL**：`session_ttl`（秒）空闲超时后台清理。
- **AskUser SSE**：流中收到 `ask_user` 事件后，用 `POST /session/<id>/answer` 回填 `{"answers": {...}}`。
- **事件流通道**：`GET /session/<id>/events` 跟随会话事件（`after_seq` 断点续传、`follow=false` 只取快照）。

---

## 3. 配置（外部化，禁止硬编码密钥）

CLI 的 LLM 配置是**命名供应商目录**，不是扁平的单模型 JSON。YAML 里只写 `api_key_env` 指针，真实密钥保存在用户级 `~/.nono-agent/credentials`（权限 0600）。查找顺序：**进程环境变量 > credentials 文件**。不读取、不迁移旧的 `config.json`。

库用法不变：直接构造 `EngineConfig(provider=ProviderConfig(...))` 不必读 YAML。

### 3.1 分层 YAML

- 用户级：`~/.nono-agent/config.yaml`
- 项目级：`.nono-agent/config.yaml`（从 cwd 向上查找）
- Overlay：CLI `--config` 指向的一层 YAML（测试 / 一次性覆盖）

优先级：**CLI `--provider` / `--model` > `--config` overlay > 项目 YAML > 用户 YAML**。

合并规则：`agent_default_model` 整段覆盖；`llm_provider.providers` 按 provider id 深合并；`models` 按 model id 合并。

```yaml
agent_default_model:
  provider: a
  model: MiniMax-M3

llm_provider:
  providers:
    a:
      display_name: My-Provider
      api_key_env: NONO_A_API_KEY
      api: openai_responses          # openai_chat_completions | anthropic_messages
      base_url: https://api.minimax.cn/v1
      models:
        - id: MiniMax-M3            # 发给 API 的 model
          name: MiniMax-M31         # 仅展示
          context_window: 1000000
          max_tokens: 128000
```

可选字段（同级、按需）：`extra_headers` / `extra_body` / `timeout`。`id` → `ProviderConfig.model_id`（`name` 不进 API）；`context_window` → `model_context`。

只给 `--model MiniMax-M3` 时：先在默认 provider 里找；全局唯一则可用；多个 provider 都有同 id 则必须同时指定 `--provider`。

### 3.2 密钥与环境变量

引导时输入的 API Key 写入 `~/.nono-agent/credentials`：

```
# nono-agent credentials — do not commit
NONO_A_API_KEY=sk-...
```

YAML 中的 `api_key_env` 由供应商 id 稳定生成（`a` → `NONO_A_API_KEY`），不要改成随机名，否则旧凭据会对不上。进程环境变量若已设置则优先于该文件（便于 CI 覆盖）。

| 变量 | 用途 |
|------|------|
| `NONO_A_API_KEY` 等（YAML 里的 `api_key_env`） | 对应供应商的 API Key（也可改写 credentials） |
| `NONO_MODEL` / `NONO_CWD` | 覆盖默认模型 id / 工作目录 |
| `NONO_SEARCH_URL` / `NONO_SEARCH_API_KEY` | `web_search` 工具端点 |
| `NONO_LSP_COMMAND` / `NONO_LSP_ARGS`（逗号分隔） | `lsp` 工具的语言服务器 |
| `NONO_PERMISSION_MODE` | 权限模式 flag 覆盖（`default/accept_edits/plan/bypass`） |

### 3.3 首次运行引导页

交互式运行 `chat`（不带 `-m`）且目录为空时，进入引导：供应商 id、显示名、协议 `api`、Base URL、**真实 API Key**、模型 id/展示名/`context_window`/`max_tokens`。目录保存到 `~/.nono-agent/config.yaml`（只含自动生成的 `api_key_env`），密钥保存到 `~/.nono-agent/credentials`。当前进程会立刻注入该环境变量，无需重启。

YAML 已存在但缺少对应密钥时**不会**覆盖目录，只补问 API Key 并写入 credentials。

用户运行时数据都在 `~/.nono-agent/`：

```
~/.nono-agent/
├── config.yaml        # LLM 供应商目录（不含密钥）
├── credentials        # API Key（0600，勿提交）
├── settings.json      # 用户级分层设置
├── sessions/          # 会话事件流
├── memory/            # 跨会话记忆（每条一个 JSON）
├── rules/             # 用户全局规则
├── skills/            # 用户全局技能
├── plugins/           # 用户全局插件
└── agents/            # 用户全局子 Agent
```

### 3.4 项目侧约定目录 `.nono-agent/`

```
.nono-agent/
├── config.yaml        # 项目级 LLM 目录（与用户级合并）
├── settings.json      # 分层设置（default < user < project < policy < flag）
├── rules/*.md         # 常驻指令（也可用 AGENTS.md / CLAUDE.md）
├── skills/<name>/SKILL.md
├── agents/*.md        # 子 Agent 定义
├── plugins/<name>/plugin.json
├── hooks.json         # 用户态 Hooks
└── mcp.json           # MCP 服务器声明
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

`/model` 打印当前 `provider / display_name + model id (name)` 以及目录列表；本轮不支持热切换，换模型请重启并加 `--provider` / `--model`。

示例：`/permission plan`（进入计划模式）、`/skills disable <name>`、`/compact`（手动压缩上下文）。

---

## 6. 扩展面速查

- **Rules**：`.nono-agent/rules/*.md`、`AGENTS.md`/`CLAUDE.md` 自动发现并注入 Prompt；frontmatter 支持 `globs`/`priority`。
- **Skills**：`SKILL.md`（frontmatter：`name`/`description`/`tools`），模型用 `skill` 工具激活。
- **子 Agent**：`.nono-agent/agents/*.md`；内置 `general-purpose`/`explore`；模型用 `agent` 工具派发。
- **Plugins**：`plugin.json` 组合注册 rules/skills/agents/mcp/权限/自定义 slash。
- **MCP**：`.nono-agent/mcp.json` 声明 stdio 服务器，桥接 tools + resources。
- **权限**：`PermissionMode`（`default/accept_edits/plan/bypass`）+ 可落盘 `PermissionRule`（工具名/path glob/bash 正则）。

---

## 7. 会话日志与可观测性

会话日志是**事件溯源**的：引擎只向事件流追加 `SessionEvent`，落盘内容只有事件，消息数组由 `derive_messages()` 推导。默认后端是本地 JSONL，无需任何配置即启用。

### 7.1 事件流落在哪里

```
~/.nono-agent/sessions/<session_id>.jsonl         # 每行一个 SessionEvent（append-only）
~/.nono-agent/sessions/<session_id>.header.json   # SessionHeader（含 format_version）
~/.nono-agent/memory/<memory_id>.json             # 跨会话记忆记录
```

事件流逐字保留提示词、工具输出与请求配置，因此文件按 `0600`、目录按 `0700` 落盘。外发副本（导出、SSE 推送）走 `redact_events()` 脱敏，见 §7.4。

```bash
# 列出本地会话（session id + 事件数）
uv run python -m nono_agent.adapters.cli sessions

# 直接看某一轮到底发生了什么
cat ~/.nono-agent/sessions/<session_id>.jsonl | jq -c '{seq, type}'

# 「模型当时被告诉了什么」——请求配置快照
cat ~/.nono-agent/sessions/<session_id>.jsonl | jq 'select(.type=="request/header") | .data.header'
```

共 17 个事件类型，权威清单是 `nono_agent/session/events.py` 的 `KNOWN_EVENT_TYPES`。写出时机、顺序与每条 `data` 的示例见 [Session-Event-Catalog.md](./Session-Event-Catalog.md)。

| 类型 | 回答的问题 |
|------|-----------|
| `turn/start`、`turn/end` | 一次 `submit_message` 的边界与结束原因 |
| `step/start`、`step/end` | 一轮里做了几次 LLM 请求、每次为什么结束 |
| `request/header` | 模型当时被告诉了什么（提供商/协议/上下文大小 + system prompt + 工具） |
| `user/input` | 用户这一句的原始输入（原话，供审计，不参与重建） |
| `user/message`、`assistant/message` | 对话本身（组装好的用户信息 + 含 tool_use blocks 的回复） |
| `assistant/chunk` | 逐 token 增量，用于不调模型重放 UI |
| `tool/call`、`tool/result` | 调了什么工具、拿到什么结果 |
| `compact/boundary` | 上下文在哪里被压缩/清空，压缩后保留了什么 |
| `api/retry` | 重试与 fallback 切换 |
| `permission/denial` | 被权限规则拦下的操作（审计资产） |
| `memory/write`、`memory/retrieve` | 写了哪条记忆、检索命中了什么、是否采用 |
| `usage` | 每步的 token 与模型 |

```bash
# 一轮里做了几次请求、每次为什么结束
cat ~/.nono-agent/sessions/<sid>.jsonl | jq -c 'select(.type=="step/end") | .data'

# 上下文被压缩过几次、每次剩多少条消息
cat ~/.nono-agent/sessions/<sid>.jsonl \
  | jq -c 'select(.type=="compact/boundary") | {strategy: .data.metadata.strategy, kept: (.data.kept|length)}'
```

`assistant/chunk` 是事件量最大的一类。只关心合并后的回复时用 `EngineConfig(log_assistant_chunks=False)` 降级，代价是无法逐 token 重放。

### 7.2 换后端 / 读事件 / 分叉

```python
from nono_agent import QueryEngine, EngineConfig
from nono_agent.session import SQLiteTranscriptWriter, derive_messages

writer = SQLiteTranscriptWriter("sessions/s1.db")   # events 表 + session_header 表
async with QueryEngine(EngineConfig(model="..."), transcript_writer=writer) as engine:
    async for msg in engine.submit_message("hi"):
        ...

events = await writer.read_events()            # 全量事件
messages = derive_messages(events)             # 确定性重建模型上下文
await engine.fork_session(boundary_seq=12, new_session_id="s1-fork")  # 在某步剪一刀
```

`--resume <session_id>` 走的就是「读事件流 → derive」。把引擎指向一个已有事件流（如上面的 `SQLiteTranscriptWriter`）也会自动接续该会话的上下文。事件格式之前的消息副本 transcript 会被**拒读**（`VersionMismatchError`），不会被当成空会话。

### 7.3 一致性门禁

事件流是模型上下文的唯一真源，所以每次 LLM 请求前会校验「事件流重建出的消息 == 引擎当前的会话状态」。不一致说明有人绕过事件流改了上下文，本轮以 `error_log_reconstruction_desync` 终止，错误信息指向第一条不同的消息。

```python
EngineConfig(session_invariant="strict")  # 默认：不一致即终止本轮
EngineConfig(session_invariant="warn")    # 记日志 + session.desync 指标，继续跑
EngineConfig(session_invariant="off")     # 不校验，省掉每次请求的一次全量事件读取
```

自定义组件（自研 stage、直接操作 `engine.message_store`）触发它，通常是真漏了事件而非误报。

### 7.4 脱敏导出与回放

```python
from nono_agent.session import LocalFileTranscriptWriter, redact_events, replay_sdk_messages

events = await LocalFileTranscriptWriter("~/.nono-agent/sessions/s1.jsonl").read_events()

# 外发前脱敏（密钥/token/JWT/私钥块/敏感键名）；信封 seq 不变，仍可按序对齐
safe = redact_events(events)

# 不调模型，重建当时的 SDK 输出序列（include_chunks=True 则逐 token 重放）
for msg in replay_sdk_messages(events, session_id="s1"):
    print(msg.type)
```

脱敏只作用于读取/导出侧：磁盘上保持逐字真实，否则事件流就不再等于「模型真的看到了什么」，门禁与回放随之失真。

### 7.5 实时事件通道（Server）

```bash
# 跟随一个会话的事件流（SSE，推送前已脱敏）
curl -N http://localhost:8000/session/<sid>/events

# 断线重连：从上次收到的 seq 之后继续
curl -N "http://localhost:8000/session/<sid>/events?after_seq=42"

# 只取历史快照，读完即结束
curl "http://localhost:8000/session/<sid>/events?follow=false"
```

每条消息为 `event: session_event`，`id` 是事件 `seq`，`data` 是 `SessionEvent` JSON。

### 7.6 Token / 指标 / 日志

- **每轮汇总**：`SDKResultMessage.usage` / `model_usage`；CLI 每轮结束打印，REPL 里 `/tokens` 查累计、`/context` 查当前消息数。
- **下钻到步**：`engine.token_tracker.get_step_usages()`，或直接读事件流里的 `usage` 事件（带 `model`）。
- **指标**：`nono_agent/observability/metrics.py`，默认内存实现，`get_metrics_recorder().snapshot()` 取快照；可 `set_metrics_recorder()` 换 OTel/Prometheus。事件流会自动投影为指标 —— 每个事件计入 `session.events`（带 `event` 标签），关键事件另计入 `session.turns`/`session.steps`/`tool.executions`/`api.errors`/`compact.operations`/`permission.denials`/`memory.*`，全部带 `session_id`。
- **结构化日志**：`observability/logging.py`（structlog），输出到 stderr，Server 模式可 JSON。

---

## 8. 测试

### 8.1 一键跑全套

```bash
uv run pytest -q            # 运行全部测试
uv run ruff check .         # lint
uv run mypy nono_agent/     # 类型检查（strict）
```

三者均为 CI 门禁（见 `.github/workflows/ci.yml`）。

### 8.2 测试基础设施（无需真实 API）

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

### 8.3 各模块测试入口

| 测试文件 | 覆盖 |
|----------|------|
| `test_permissions.py` | PermissionMode/PermissionRule/沙箱/Ignore/Bash 分类/denial |
| `test_builtin_tools.py` | 22 个内置工具 + 工厂 |
| `test_mcp.py` | MCP stdio 客户端（mock 服务器） |
| `test_lsp.py` | LSP 客户端（mock 语言服务器） |
| `test_agents.py` / `test_skills.py` / `test_plugins.py` / `test_rules.py` | 扩展面 |
| `test_server.py` | Server 鉴权/AskUser 端点/事件流 SSE 通道 |
| `test_budget.py` / `test_fallback.py` / `test_pipeline.py` | 引擎边界 |
| `test_transcript_events.py` / `test_session_events.py` / `test_engine_events.py` | 事件流后端 / derive 折叠 / 引擎事件发射 |
| `test_event_coverage.py` | 17 个事件类型逐个有真实发射路径 + 管线 compact/boundary + 指标投影 |
| `test_session_invariant.py` | 一致性门禁（strict/warn/off）+ 管线深拷贝 + 接续已有事件流 |
| `test_session_redact.py` / `test_session_replay.py` | 脱敏投影 / 不调模型回放 |
| `test_checkpoint.py` / `test_memory.py` | 持久化 |

### 8.4 无外部依赖的 mock 技巧

- MCP / LSP 用 `tmp_path` 写一个临时 Python 脚本当「服务器」，`sys.executable` 启动（见 `tests/test_mcp.py`、`tests/test_lsp.py`）。
- 需要 git 仓库的测试（worktree）用 `pytest.mark.skipif(shutil.which("git") is None)` 保护。
- 网络工具只测纯函数（`is_url_blocked`），不发起真实请求。

---

## 9. 包结构速览

```
nono_agent/
├── core/          引擎内核（QueryEngine/loop/config/state/types）
├── pipeline/      预处理压缩管线（含 ContextCollapse）
├── recovery/      错误恢复决策链
├── providers/     协议矩阵（openai_chat_completions / openai_responses / anthropic_messages）
├── session/       MessageStore + 事件流 Transcript（JSONL/SQLite + derive + fork）
├── tools/         工具协议/注册表/执行器 + builtin/（22 个生产工具）
├── prompt/        Prompt Section 组装（静态/动态边界 + 优先级合并）
├── rules/ skills/ plugins/ commands/ settings/ permissions/ mcp/ agents/ memory/ checkpoint/
├── hooks/         引擎内 Middleware + 用户态 hooks.json
├── observability/ Token 跟踪/日志/指标
└── adapters/      CLI / Server / testing
```

详细设计见 [NonoAgent-Architecture.md](./NonoAgent-Architecture.md)，规划与状态见 [ROADMAP.md](./ROADMAP.md)。
