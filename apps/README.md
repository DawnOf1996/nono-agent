# apps/

产品层：桌面壳 + React 工作台 + 封装 nono-agent 的本机 sidecar。引擎代码仍在仓库根的 `nono_agent/`，这里只消费 `create_server_app()`。

```text
apps/
  server/     Python sidecar（FastAPI + SSE）
  web/        Vite + React + shadcn 风格组件
  desktop/    Electron：加载 web，必要时 spawn server
  packages/
    protocol/ 前后端共享 TypeScript 类型
  package.json / pnpm-workspace.yaml / pnpm-lock.yaml
```

## 开发

需要：Python 3.12+、`uv`、Node 20+（pnpm 或 npm）。先有一份 `~/.nono-agent/config.yaml`（跑过一次 `python -m nono_agent.adapters.cli chat` 即可）。

三个终端：

```bash
# 1. sidecar
cd apps/server
uv sync
uv run nono-app-server

# 2. web（apps/ 是 pnpm workspace 根）
cd apps
pnpm install
pnpm dev:web

# 3. 桌面壳（可选；会复用已启动的 sidecar）
pnpm dev:desktop
```

Windows 也可在仓库根执行 `.\scripts\dev.ps1`，会拉起 sidecar 与 Vite。

浏览器打开 http://127.0.0.1:5173 。Electron 加载同一地址。点「新任务」可选本地目录作为工作空间；「设置」里配置 / 切换模型。
