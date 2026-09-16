# apps/

产品层：桌面壳 + React 工作台 + 本机 sidecar + 终端 CLI。引擎代码仍在仓库根的 `nono_agent/`，这里消费 `QueryEngine`。

```text
apps/
  cli/        终端 CLI（Typer + Rich）
  server/     Python sidecar（FastAPI + SSE）
  web/        Vite + React + shadcn 风格组件
  desktop/    Electron：加载 web，必要时 spawn server
  packages/
    protocol/ 前后端共享 TypeScript 类型
  package.json / pnpm-workspace.yaml / pnpm-lock.yaml
```

## 开发

需要：Python 3.12+、`uv`、Node 20+（pnpm 或 npm）。先有一份 `~/.nono-agent/config.yaml`（跑过一次 `uv run nono chat` 即可）。

### 环境下载

pnpm workspace 根在 `apps/`，不要在仓库根跑 `pnpm install`。

```bash
# sidecar 依赖（apps/server）
cd apps/server
uv sync

# 前端 / 桌面依赖（apps/）
cd apps
pnpm install
```

`pnpm install` 只保证 npm 包装齐。Electron 本体（约 100MB 的 `electron.exe`）由包内 `postinstall` 另下，镜像见同目录 `.npmrc` 的 `electron_mirror`。

若提示 `Already up to date`，但 `pnpm dev:desktop` 报 `Electron failed to install correctly`，说明 JS 包装了、二进制没下完。在 `apps/` 补一次：

```bash
pnpm rebuild electron
# 或
node node_modules/.pnpm/electron@37.10.3/node_modules/electron/install.js
```

成功后 `pnpm --filter @nono/desktop exec electron --version` 应能打出版本号。

### 启动

三个终端：

```bash
# 1. sidecar
cd apps/server
uv run nono-app-server

# 2. web（apps/ 是 pnpm workspace 根）
cd apps
pnpm dev:web

# 3. 桌面壳（可选；会复用已启动的 sidecar）
pnpm dev:desktop
```

Windows 也可在仓库根执行 `.\scripts\dev.ps1`，会拉起 sidecar 与 Vite。

浏览器打开 http://127.0.0.1:5173 。Electron 加载同一地址。点「新任务」可选本地目录作为工作空间；「设置」里配置 / 切换模型。

## 打包 Windows 桌面版

在仓库根执行（需要 Python 3.12+、`uv`、Node 20+、`pnpm`）：

```powershell
.\scripts\pack-desktop.cmd
```

默认 PowerShell 会禁止直接跑 `.ps1`。上面这个 `.cmd` 会用 `-ExecutionPolicy Bypass` 启动；也可以手写：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\pack-desktop.ps1
```

脚本会依次：

1. `uv sync --extra dev` 同步 Python 依赖
2. 在 `apps/` 里 `pnpm install` 并 `pnpm build:web`，产出 Vite 静态资源
3. 用 PyInstaller 把 sidecar 冻成 `nono-app-server.exe`（首次较慢）
4. 把 web 产物与 sidecar 拷进 Electron `extraResources`
5. `electron-builder --win` 打安装包

产物：

| 文件 | 说明 |
|------|------|
| `apps/desktop/release/win-unpacked/Nono.exe` | 免安装，解压目录里直接双击 |
| `apps/desktop/release/Nono-0.1.0-setup.exe` | NSIS 安装包 |

运行时 Electron 会拉起本机 sidecar，sidecar 托管前端并提供 `/health`、`/chat` 等 API。模型配置仍读 `~/.nono-agent/`（与开发态相同）。

只重打其中一步时可用：

```powershell
.\scripts\pack-desktop.cmd -SkipWeb        # 前端没改
.\scripts\pack-desktop.cmd -SkipSidecar    # Python 没改
.\scripts\pack-desktop.cmd -SkipElectron   # 只准备 resources，不跑 electron-builder
```

换过 sidecar / 前端后，已安装的旧版不会自动更新，需要再跑一遍脚本并用新的 `Nono.exe` 或 setup 覆盖安装。
