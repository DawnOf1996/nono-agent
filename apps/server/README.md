# apps/server

桌面端 Python sidecar。引擎 HTTP/SSE API 在本包 `nono_app_server.engine`，产品层再加上本机绑定、权限确认与 `/app` 路由。

```text
nono_app_server/
  main.py           sidecar 入口（本机绑定）
  config.py         环境变量 + YAML → EngineConfig
  extra_routes.py   /app 产品路由
  catalog_api.py    模型目录读写
  permissions.py    桌面权限确认
  engine/
    app.py              create_server_app / run_server
    session_manager.py  QueryEngine 会话生命周期
    routes.py           HTTP / SSE 路由
    models.py           请求响应模型
    views.py            SessionInfo 投影
    sse.py              流式队列辅助
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
| GET | `/app/permissions` | 待确认的写工具请求 |
| POST | `/app/permissions/{id}/answer` | `{ "allow": true \| false }` |

环境变量：`NONO_HOST`、`NONO_PORT`、`NONO_CWD`、`NONO_MODEL`、`NONO_PROVIDER`、`NONO_WEB_ORIGIN`。
