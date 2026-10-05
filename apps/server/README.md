# apps/server

桌面端 Python sidecar。引擎 HTTP/SSE API 在 `nono_app_server.engine`，产品功能在 `nono_app_server.features`（挂到 `/app`），跨功能工具在 `nono_app_server.common`。

```text
nono_app_server/
  app.py                 FastAPI 组装（引擎 + /app）
  main.py                进程入口（本机绑定 + uvicorn）
  config.py              环境变量 + YAML → EngineConfig
  common/                跨功能共用
    deps.py              sidecar / 引擎配置 / SQLite 依赖
    store.py             工作区与会话列表
    paths.py             工作区路径解析与越界检查
    textdiff.py          unified diff 解析与文本对比
    gitcmd.py            调用系统 git
  features/              按功能划分的 /app API
    router.py            挂载 /app
    files/               文件服务：api 路由、service 读写、util 文件名与哈希
    terminal/            持久终端：api + service
    git/                 仓库状态、diff、暂存、提交、历史
    changes/             每轮文件变更（git 私有快照 + 文件工具前像）
    sessions/            会话列表（映射引擎 session_id）
    workspaces/          工作区列表
    permissions/         桌面权限确认
    catalog/             模型目录
    mcp/                 MCP 清单
    toolplugins/         工具插件：清单、配置面板 schema、reload
    control/             运行时控制（启停等）
    jobs/                任务板
    info/                GET /app/info
  engine/                Agent 引擎 HTTP / SSE
    app.py               create_server_app / run_server
    auth.py              API Key 中间件
    session_manager.py   QueryEngine 会话生命周期
    schemas.py           请求响应模型
    views.py             SessionInfo 投影
    sse.py               流式队列辅助
    routers/
      health.py          GET /health
      chat.py            POST /chat、/abort
      sessions.py        会话 CRUD / 事件流 / AskUser
      commands.py        斜杠指令
```

## 运行

仓库根已 `uv sync --extra dev` 时：

```bash
uv run nono-app-server
```

或只装本包：

```bash
cd apps/server
uv sync
uv run nono-app-server
```

默认 `http://127.0.0.1:8000`。引擎 SSE 路由保持不变：`POST /chat`、`POST /session/new`、`GET /sessions`。产品层额外提供：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/app/info` | sidecar 元信息、cwd、模型、目录是否就绪 |
| GET | `/app/catalog` | 模型目录快照（不含密钥） |
| POST | `/app/catalog/providers` | 新增/更新供应商 |
| POST | `/app/catalog/default` | 设置默认模型 |
| GET | `/app/permissions` | 待确认的写工具请求 |
| POST | `/app/permissions/{id}/answer` | `{ "allow": true \| false }` |
| GET | `/app/fs` | 浏览本机目录 |
| POST | `/app/fs/mkdir` | 创建子目录 |
| POST | `/app/fs/open` | 用系统文件管理器打开目录 |
| GET | `/app/fs/file` | 读取工作区文件供预览；文本带内容哈希 `hash` |
| PUT | `/app/fs/file` | 保存文本文件；沿用原编码 / BOM / 换行，`base_hash` 不符时 409 |
| GET | `/app/git/status` | 工作树状态（暂存 / 未暂存 / 未跟踪 + 行数），`repo` 选仓库或 worktree |
| GET | `/app/git/worktrees` | 工作区内所有仓库的 checkout 及改动数 |
| GET | `/app/git/diff` | 单个文件的对比，`staged` 选暂存区，`context` 控制上下文行数 |
| POST | `/app/git/stage`、`/unstage`、`/discard`、`/commit` | 暂存、取消暂存、丢弃、提交 |
| GET | `/app/git/log`、`/app/git/commit` | 提交历史、单个提交的逐文件对比 |
| GET | `/app/changes/{session}` | 该会话每轮的文件变更摘要 |
| GET | `/app/changes/{session}/{turn}/diff` | 某轮某个文件的轮前 / 轮后对比 |
| POST | `/app/changes/{session}/{turn}/revert` | 把文件恢复成该轮开始前的内容（之后又被改过时 409） |
| GET | `/app/workspaces` | 工作区列表（SQLite） |
| POST | `/app/workspaces` | 按路径创建或返回已有工作区 |
| DELETE | `/app/workspaces/{id}` | 删除工作区及其会话列表行 |
| GET | `/app/sessions` | 会话列表（SQLite 映射，不含详情） |
| POST | `/app/sessions` | 创建列表行并打开对应引擎会话 |
| PATCH | `/app/sessions/{id}` | 更新标题 / 轮次等列表字段 |
| DELETE | `/app/sessions/{id}` | 从列表移除并关闭引擎实例 |

工作区与侧栏会话列表以 `~/.nono-agent/app.db` 为准（可用 `NONO_APP_DB` 覆盖）。会话详情（事件流、对话 SSE）仍走引擎 `GET /session/{id}/events` 与 `POST /chat`，用返回体里的 `engine_session_id`。首次启动若库是空的，会把引擎磁盘上的会话索引导入列表。

每轮对话结束时，`POST /chat` 在 `done` 之前多推一条 `workspace_changes` 事件，内容是这一轮的文件变更摘要（没有改动文件时不推）。记录方式：工作区在 git 仓库里时，轮开始和轮结束各把工作树写进私有索引与临时对象库，两棵树相减（仓库自身的索引、对象、引用都不会被写入，轮前已有的未提交改动也不计入）；`edit_file` / `write_file` 执行前另拷一份文件原内容，覆盖非仓库工作区和被忽略的文件。每个改动文件的轮前、轮后内容按 SHA-1 存在 `~/.nono-agent/workspace-changes/<session>/`，删除会话时一并清理。

环境变量：`NONO_HOST`、`NONO_PORT`、`NONO_CWD`、`NONO_MODEL`、`NONO_PROVIDER`、`NONO_WEB_ORIGIN`、`NONO_APP_DB`。
