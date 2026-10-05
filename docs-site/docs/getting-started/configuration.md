---
title: 配置与密钥
---

# 配置与密钥

Nono Agent 的配置分三块，各有各的文件：**模型供应商**（YAML 目录）、**密钥**（独立凭据文件）、**运行设置**（分层 settings.json）。三者刻意分开：YAML 可以进版本库，密钥不行。

---

## 1. 模型供应商：命名目录

CLI 的 LLM 配置是一份**命名供应商目录**，不是扁平的「单模型 JSON」。结构由 `nono_agent/providers/catalog.py` 的 pydantic 模型定义。

```yaml
agent_default_model:
  provider: a                 # 供应商 id，对应下面 providers 里的键
  model: MiniMax-M3

llm_provider:
  providers:
    a:                        # 供应商 id，稳定不变，api_key_env 由它派生
      display_name: My-Provider       # 必填，仅展示
      api_key_env: NONO_A_API_KEY     # 必填，密钥指针，不是密钥本身
      api: openai_responses           # 必填，三选一，见下表
      base_url: https://api.minimax.cn/v1
      models:
        - id: MiniMax-M3              # 发给 API 的 model
          name: MiniMax-M31           # 仅展示用
          context_window: 1M          # 支持 1000000 或 1M 或 128K
          max_tokens: 128K
          extra_body: {}              # 可选，合并进请求体
      extra_headers: {}               # 可选
      extra_body: {}                  # 可选
      timeout: 60.0                   # 可选，秒
```

`api` 的三个合法值（`nono_agent.providers.base.CANONICAL_ADAPTERS`）：

| `api` | 底层 SDK | 适用 |
|---|---|---|
| `openai_chat_completions` | `AsyncOpenAI.chat.completions` | 绝大多数 OpenAI 兼容端点 |
| `openai_responses` | `AsyncOpenAI.responses` | OpenAI Responses 协议 |
| `anthropic_messages` | `AsyncAnthropic.messages` | Anthropic Messages 协议 |

!!! note "供应商与协议是两件事"
    这里按 **API 形态**分类，不按厂商分类。同一个 MiniMax 端点，用 `openai_chat_completions` 接和用 `openai_responses` 接是两个不同的适配器实例，换协议只需要改 `api` 字段。

`context_window` / `max_tokens` 支持紧凑写法：纯整数，或 `128K` / `1M`（**十进制**，K=1000、M=1000000，不是 1024）。

### 分层与优先级

| 层 | 位置 | 说明 |
|---|---|---|
| 用户级 | `~/.nono-agent/config.yaml` | 全局生效 |
| 项目级 | `.nono-agent/config.yaml` | 从 cwd 向上逐级查找 |
| Overlay | CLI `--config <path>` | 测试 / 一次性覆盖 |

优先级：**CLI `--provider` / `--model` > 环境变量 `NONO_MODEL` > overlay > 项目 YAML > 用户 YAML**。

合并规则（`nono_agent/settings/merge.py` 的 `deep_merge`）：`agent_default_model` 整段覆盖；`llm_provider.providers` 按 provider id 深合并；`models` 按 model id 合并。

只给 `--model MiniMax-M3` 时的解析规则：先在默认 provider 里找；全局唯一则直接可用；多个 provider 都有同 id，则必须同时指定 `--provider`。

---

## 2. 密钥：指针与真值分离

**YAML 里永远不写密钥。** `api_key_env` 是指针，真值有两个来源，进程环境变量优先：

1. 进程环境变量
2. 用户级 `~/.nono-agent/credentials`（权限 0600）

```bash
# ~/.nono-agent/credentials —— do not commit
NONO_A_API_KEY=sk-...
```

`api_key_env` 由供应商 id 稳定生成（`a` → `NONO_A_API_KEY`），**不要手动改成随机名**，否则已存的凭据会对不上。旧的 `config.json` 不再读取，也不做迁移。

### 首次运行引导

交互式运行 `nono chat`（不带 `-m`）且目录为空时进入引导，逐项问：供应商 id、显示名、协议 `api`、Base URL、**真实 API Key**、模型 id / 展示名 / `context_window` / `max_tokens`。

结果分两处落盘：目录写 `~/.nono-agent/config.yaml`（只含自动生成的 `api_key_env`），密钥写 `~/.nono-agent/credentials`，并立刻注入当前进程环境变量——**不需要重启**。

YAML 已存在但缺对应密钥时**不会**覆盖目录，只补问 API Key。

---

## 3. 运行设置：分层 settings.json

与模型目录是两套东西。层级（后者覆盖前者）：

```text
default  <  user  <  project  <  policy  <  flag（CLI/env 显式指定）
```

- 用户级：`~/.nono-agent/settings.json`
- 项目级：`.nono-agent/settings.json`

项目 `settings.json` 的主要命名空间（`mcp` / `tools` / `permissions`）：

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "calculator": { "enabled": true, "disabledTools": ["divide"] },
      "username": { "enabled": false }
    }
  },
  "tools": {
    "tavily": {
      "enabled": true,
      "api_key_env": "NONO_TAVILY_API_KEY",
      "options": { "server": { "transport": "http" } }
    }
  }
}
```

**未声明即不启用**：工作区默认不启用任何 MCP server、任何工具插件。启停必须显式写在这里，或者在会话内开关。

!!! note "工具插件的开关以用户级为主"
    设置面板把 `tools.<id>` 写在**用户级** `~/.nono-agent/settings.json`（见 [使用手册](../guide/usage.md) §3.5「内置工具插件」）。
    引擎读的是合并后的 Settings，所以工作区里写同名块也会生效并覆盖用户级值 ——
    面板会把这种插件标成 `project_owned`。

自定义 MCP 连接只写用户级 `~/.nono-agent/mcp.json`（Cursor 同款连接字段）：

```json
{
  "mcpServers": {
    "my-server": {
      "type": "http",
      "url": "https://example.com/mcp",
      "headers": { "Authorization": "Bearer ..." }
    }
  }
}
```

---

## 4. 用户运行时目录

```text
~/.nono-agent/
├── config.yaml        # LLM 供应商目录（不含密钥）
├── credentials        # API Key（0600，勿提交）
├── settings.json      # 用户级分层设置（工具插件开关写这里）
├── mcp.json           # 自定义 MCP 连接清单（不含工作区启停）
├── mcp/               # MCP 进程日志（launcher 写）
├── runtimes/          # MCP 运行时缓存
├── sessions/          # 会话事件流
├── memory/            # 跨会话记忆（每条一个 JSON）
├── rules/             # 用户全局规则
├── skills/            # 用户全局技能
├── plugins/           # 用户全局插件
└── agents/            # 用户全局子 Agent
```

桌面版 sidecar 另有两个：应用状态库 `app.db`，以及工作区变更快照 `workspace-changes/<session_id>/`。

## 5. 项目侧约定目录

```text
.nono-agent/
├── config.yaml        # 项目级 LLM 目录（与用户级合并）
├── settings.json      # 分层设置
├── rules/*.md         # 常驻指令（也可用 AGENTS.md / CLAUDE.md）
├── skills/<name>/SKILL.md
├── agents/*.md        # 子 Agent 定义
├── plugins/<name>/plugin.json
├── hooks.json         # 用户态 Hooks
├── images/            # 图像类工具插件的产物落盘目录
└── sessions/          # 会话事件流
```

---

## 6. 环境变量

| 变量 | 用途 |
|---|---|
| `NONO_MODEL` | 覆盖默认模型 id |
| `NONO_CWD` | 覆盖工作目录（优先级低于 CLI `--cwd`） |
| `NONO_LOG_API_REQUESTS` | `1`/`true`/`yes`/`on`：SDK 发 HTTP 前 dump 请求体，等价 `--log-api-requests` |
| `NONO_LSP_COMMAND` / `NONO_LSP_ARGS` | `lsp` 工具的语言服务器与参数（逗号分隔） |
| `NONO_PERMISSION_MODE` | 权限模式覆盖：`default` / `accept_edits` / `plan` / `bypass` |
| `<供应商>_API_KEY` | YAML 里 `api_key_env` 指向的供应商密钥 |

---

## 相关页面

- [快速开始](quickstart.md)
- [工具与权限](../guide/tools-and-permissions.md)
- [新增工具插件](../development/add-tool-plugin.md)
