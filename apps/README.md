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

开发有两条路，按要不要 Electron 来选。

#### A. 桌面联调（一条命令）

```bash
cd apps
pnpm dev:desktop
```

Windows 上也可以在仓库根执行 `.\scripts\dev.ps1`，就是上面那条命令的包装。

桌面壳会把整条链自己拉起来，逻辑和打包态一致：

| 进程     | 端口  | 谁负责拉起                                         |
| -------- | ----- | -------------------------------------------------- |
| sidecar  | 8000  | 桌面壳 spawn `uv run nono-app-server`，已在跑就复用 |
| Vite     | 5173  | 桌面壳 spawn `pnpm dev:web`，已在跑就复用           |
| Electron | —     | 你执行的那条命令                                   |

**不需要**再手动启动 sidecar 或 Vite。反过来同时手动起也没坏处 —— 桌面壳会
探测到端口上已有健康服务就直接复用，但那种情况下 sidecar 的日志不在你眼前。

两个「已在跑就复用」的例外：

- 端口上有个服务在监听、但 `/health` 不通（还在冷启动，或压根不是 nono sidecar），
  桌面壳会先等它 30s 就绪，仍然不通就**放弃 spawn 并打印原因**——那种情况下
  再 spawn 必然撞 `winerror 10048`，只会多一条看不懂的报错；
- 想完全接管某个进程，设 `NONO_SIDECAR=0`（不碰后端）或 `NONO_WEB=0`（不碰 Vite）
  再启动桌面壳。

从托盘图标或窗口关闭按钮退出时，桌面壳会回收**自己拉起**的那些进程；你手起的
不会被误杀。注意窗口的关闭按钮只是隐藏到托盘，进程还在 —— 要真正退出请用托盘菜单。

#### B. 纯 Web 联调（三个终端）

只改前端、不用 Electron 时走这条。sidecar 的日志直接落在终端里，比 A 更方便看：

```bash
# 1. sidecar
cd apps/server
uv run nono-app-server

# 2. web（apps/ 是 pnpm workspace 根）
cd apps
pnpm dev:web

# 3. 浏览器
#    打开 http://127.0.0.1:5173
```

sidecar 记得在 `apps/server` 目录下跑（`cwd` 决定它读哪份配置）。这条路上
不要再开 `pnpm dev:desktop` —— 不然 Electron 会去复用你手起的 sidecar，而且
关窗口只是缩到托盘，进程还在。

#### 换端口

两条路都用 `NONO_PORT`，桌面壳会把它同时传给 sidecar 和 Vite：

```powershell
cd apps
$env:NONO_PORT = "8010"
pnpm dev:desktop
```

走 B 的话，手起 sidecar 和手起 Vite 要在**各自那个终端里**都设上同一个
`NONO_PORT`，否则 Vite 的代理会指向 8000。

> 注意：Vite 的代理目标在**启动时**固化（`__NONO_SIDECAR_HTTP__`）。改了
> `NONO_PORT` 必须把 Vite 重启才生效。

浏览器打开 [http://127.0.0.1:5173](http://127.0.0.1:5173) 。Electron 加载同一地址。点「新任务」可选本地目录作为工作空间；「设置」里配置 / 切换模型。

### 不接模型调流式渲染

`scripts/dev/mock-sidecar.mjs` 是一个假 sidecar：只实现前端启动与单轮对话需要的接口，按 token 逐片吐 SSE，用来稳定复现「单轮输出很多」时的界面表现，不烧任何 API 额度。

```bash
node scripts/dev/mock-sidecar.mjs --port 8010 --tokens 8000 --think 500 --interval 4
cd apps && $env:NONO_SIDECAR_URL="http://127.0.0.1:8010"; pnpm dev:web
```


| 参数                 | 说明                                             |
| ------------------ | ---------------------------------------------- |
| `--tokens`         | 正文分片数，越大越接近长输出场景                               |
| `--think`          | 思考分片数                                          |
| `--interval`       | 每片之间的毫秒间隔                                      |
| `--tools`          | 中间插入几个「思考 → 正文 → 工具调用」步骤                       |
| `--sample torture` | 换成一份专门压 markdown 结构的样本（松散列表、嵌套引用、围栏/缩进代码、表格对齐） |




## 打包 Windows 桌面版

在仓库根执行（需要 Python 3.12+、`uv`、Node 20+、`pnpm`）：

```powershell
.\scripts\pack-desktop.cmd
```

默认 PowerShell 会禁止直接跑 `.ps1`。上面这个 `.cmd` 会用 `-ExecutionPolicy Bypass` 启动；也可以手写：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\pack-desktop.ps1
```

版本号是 `x.y.z`。`x` 和 `y` 改 `apps/desktop/package.json` 里 `version` 的前两段（当前 `0.2`）；第三段只是占位，打包不会用。`z` 由脚本按本机时间写成 `yyyyMMddHH`，例如 2026-09-28 13 时得到 `0.2.2026092813`。同一小时内再打包，版本号相同。单独跑 `pnpm dist:desktop` 不会生成这个 `z`。

脚本会依次：

1. `uv sync --extra dev` 同步 Python 依赖
2. 在 `apps/` 里 `pnpm install` 并 `pnpm build:web`，产出 Vite 静态资源
3. 用 PyInstaller 把 sidecar 冻成 `nono-app-server.exe`（首次较慢）
4. 把 web 产物与 sidecar 拷进 Electron `extraResources`
5. `electron-builder --win` 打安装包

产物：


| 文件                                                     | 说明                                          |
| ------------------------------------------------------ | ------------------------------------------- |
| `apps/desktop/release/win-unpacked/NoNo.exe`           | 免安装，解压目录里直接双击                               |
| `apps/desktop/release/NoNo-<x.y.yyyyMMddHH>-setup.exe` | NSIS 安装包，例如 `NoNo-0.2.2026092813-setup.exe` |


运行时 Electron 会拉起本机 sidecar，sidecar 托管前端并提供 `/health`、`/chat` 等 API。模型配置仍读 `~/.nono-agent/`（与开发态相同）。

只重打其中一步时可用：

```powershell
.\scripts\pack-desktop.cmd -SkipWeb        # 前端没改
.\scripts\pack-desktop.cmd -SkipSidecar    # Python 没改
.\scripts\pack-desktop.cmd -SkipElectron   # 只准备 resources，不跑 electron-builder
```

换过 sidecar / 前端后，已安装的旧版不会自动更新，需要再跑一遍脚本并用新的 `NoNo.exe` 或 setup 覆盖安装。

## 自动打包

```bash
# 提交到github
git push github dev-ai
# 构建 tag 推送 自动打包
git tag v0.3.x
git push github v0.3.x
```

