# apps/server

桌面端 Python sidecar。不复制引擎，只组合 `nono_agent.adapters.server.create_server_app`，并加上本机绑定与权限确认。

## 运行

在仓库根目录已有 `~/.nono-agent/config.yaml` 时：

```bash
cd apps/server
uv sync
uv run nono-app-server
```

若不想再装一份引擎依赖，也可在仓库根用现有 `.venv`：

```powershell
$env:PYTHONPATH="apps/server"
uv run python -m nono_app_server
```

默认 `http://127.0.0.1:8000`。引擎 SSE 路由保持不变：`POST /chat`、`POST /session/new`、`GET /sessions`。产品层额外提供：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/app/info` | sidecar 元信息、cwd、模型、目录是否就绪 |
| GET | `/app/permissions` | 待确认的写工具请求 |
| POST | `/app/permissions/{id}/answer` | `{ "allow": true \| false }` |

环境变量：`NONO_HOST`、`NONO_PORT`、`NONO_CWD`、`NONO_MODEL`、`NONO_PROVIDER`、`NONO_WEB_ORIGIN`。
