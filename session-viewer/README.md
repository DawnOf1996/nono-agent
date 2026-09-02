# Nono Agent · Session Viewer

一个把 nono-agent 的**会话事件流**可视化成「轨迹 / 对话」的前端服务。参考
deepseek-harness 的 Trajectory（轨迹检查台账）思路：不重新发明消息模型，而是把
append-only 的 `SessionEvent` 事件流按 `turn` → `step` → 消息/工具/用量 折叠成
可读、可点击下钻的时间线。

## 能力

- **给定 `session_id` 加载**：从 `~/.nono-agent/sessions/<id>.jsonl` 读取（后端提供 API）。
- **离线会话文件**：直接把 `<sid>.jsonl` 拖进页面，或点「打开离线文件」选择；解析全在浏览器内完成。
- **轨迹 Timeline 视图**：`turn/start → user/input → memory/retrieve → step/start →
  user/message → request/header → assistant/* → tool/call → tool/result → usage →
  step/end → turn/end` 的分层结构，含相对时间、结束原因、聚合后的流式 chunk。
- **对话 Chat 视图**：用户消息 / 助手回复（reasoning 可折叠、文本、工具调用）/ 工具结果 /
  每步 token 用量。
- **详情面板**：点击任意事件查看完整 `data` JSON（含 `request/header` 的 system prompt
  与工具 schema）。

## 运行（用 uv 管理虚拟环境）

本项目是自包含的：自带 `pyproject.toml`，用 `uv` 管理虚拟环境，只依赖 `fastapi` +
`uvicorn`，**不依赖** nono-agent 根仓库的包（读取会话目录的逻辑已在 `server.py`
里内联，无需安装 anthropic/openai/rich 等重依赖）。

两种 uv 用法，任选其一：

**A. 复用仓库已有的 uv 环境（最快，零下载）**

```bash
# 在 nono-agent 仓库根目录（其 pyproject 已声明 fastapi/uvicorn）
uv run python session-viewer/server.py
```

**B. 给 session-viewer 一个专属虚拟环境（独立、可复制）**

```bash
cd session-viewer
uv sync          # 首次会按 pyproject.toml 下载并安装 fastapi/uvicorn
uv run python server.py
```

两种方式都打开 <http://127.0.0.1:8765>。

> `[tool.uv] package = false` 让 uv 只负责管理依赖虚拟环境，不把本工程 build 成包。

会话目录默认是 `~/.nono-agent/sessions/`，可用环境变量 `NONO_SESSION_DIR` 覆盖：

```bash
NONO_SESSION_DIR=/path/to/sessions uv run python server.py
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/sessions` | 列出本地会话（id / 事件数 / 元信息） |
| `GET` | `/api/session/{id}` | 返回 `{session_id, header, events}` |
| `GET` | `/api/session/{id}/events` | 仅返回事件流 |
| `GET` | `/api/health` | 健康检查 + 会话目录可达性 |
| `GET` | `/` | 前端页面 |

## 目录结构

```
session-viewer/
├── pyproject.toml     # uv 项目定义（fastapi/uvicorn；package=false）
├── server.py          # FastAPI 后端（读取会话目录 + 静态服务，自包含）
├── static/
│   ├── index.html     # 单页应用骨架
│   ├── style.css      # 样式
│   └── app.js         # JSONL 解析 + 轨迹/对话渲染（无构建、无第三方依赖）
└── README.md
```
