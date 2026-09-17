# apps/server

桌面端 Python sidecar。引擎 HTTP/SSE API 在 `nono_app_server.engine`，产品层 `/app` 在 `nono_app_server.product`。

```text
nono_app_server/
  app.py                 FastAPI 组装（引擎 + /app）
  main.py                进程入口（本机绑定 + uvicorn）
  config.py              环境变量 + YAML → EngineConfig
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
  product/               桌面 sidecar 产品 API（/app）
    router.py            挂载 /app
    info.py              GET /app/info
    catalog.py           模型目录读写
    permissions.py       桌面权限确认
    filesystem.py        本机目录浏览
    store.py             SQLite 工作区 / 会话列表
    workspaces.py        /app/workspaces
    sessions.py          /app/sessions（映射引擎 session_id）
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
| GET | `/app/workspaces` | 工作区列表（SQLite） |
| POST | `/app/workspaces` | 按路径创建或返回已有工作区 |
| DELETE | `/app/workspaces/{id}` | 删除工作区及其会话列表行 |
| GET | `/app/sessions` | 会话列表（SQLite 映射，不含详情） |
| POST | `/app/sessions` | 创建列表行并打开对应引擎会话 |
| PATCH | `/app/sessions/{id}` | 更新标题 / 轮次等列表字段 |
| DELETE | `/app/sessions/{id}` | 从列表移除并关闭引擎实例 |

工作区与侧栏会话列表以 `~/.nono-agent/app.db` 为准（可用 `NONO_APP_DB` 覆盖）。会话详情（事件流、对话 SSE）仍走引擎 `GET /session/{id}/events` 与 `POST /chat`，用返回体里的 `engine_session_id`。首次启动若库是空的，会把引擎磁盘上的会话索引导入列表。

环境变量：`NONO_HOST`、`NONO_PORT`、`NONO_CWD`、`NONO_MODEL`、`NONO_PROVIDER`、`NONO_WEB_ORIGIN`、`NONO_APP_DB`。
