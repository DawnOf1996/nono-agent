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

| 进程     | 端口  | 谁负责拉起                            |
| -------- | ----- | ------------------------------------- |
| sidecar  | 8000  | 桌面壳 spawn `uv run nono-app-server` |
| Vite     | 5173  | 桌面壳 spawn `pnpm dev:web`           |
| Electron | —     | 你执行的那条命令                      |

**不要**手动起 sidecar 或 Vite：端口上已经有服务时，桌面壳**不接管**，而是报端口
被占用并弹窗说明是谁占着（多半是上一次没退干净的实例）。

不复用是刻意的。复用来的 sidecar 带着**上一个 Electron 实例**注入的环境变量 ——
浏览器控制的 CDP 端口就是这么变成过期值的：引擎去戳上一个实例的调试端口，而当前
实例的端口其实好好的，最后报一句「端口没绑上」。这种状态没法自愈（进程活着、
`/health` 照常回 ok），只能从根上避免：端口空就自己起，退出时只回收自己起的。

三种「不由桌面壳拉起」的方式，都是显式的：

- `NONO_SIDECAR=0`（不碰后端）/ `NONO_WEB=0`（不碰 Vite）；
- `NONO_SIDECAR_URL` 指向别处的 sidecar（`scripts/dev/mock-sidecar.mjs` 走的就是
  这个），配 `NONO_SIDECAR=0` 用；
- `NONO_WEB_URL` 指向别处的网页，配 `NONO_WEB=0` 用。

退出有三条路，回收的责任人不一样：

| 你怎么退                       | 谁来回收                                          |
| ------------------------------ | ------------------------------------------------- |
| 托盘菜单「退出」               | 桌面壳自己：杀进程树，并**等端口真的空出来**再退  |
| 终端里 `Ctrl+C`                | 启动器：直接 `taskkill /T` 收掉整棵 Electron 进程树 |
| 关掉跑它的那个终端窗口         | 同上，走 `SIGHUP`（Windows 会留约 5 秒给收尾）    |

你手起的 sidecar / Vite 不会被误杀。注意窗口的关闭按钮只是隐藏到托盘，进程还在 ——
要真正退出请用托盘菜单，或者在终端 Ctrl+C。

`Ctrl+C` 这条路绕了一圈是有原因的：Electron 主进程在 Windows 上**收不到**终端
Ctrl+C 的 JS 信号（`process.on("SIGINT")` 不触发，主进程被系统直接终止），回收没法
交给它。所以 `pnpm dev:desktop` 经过一层纯 node 的启动器
（`apps/desktop/scripts/dev-desktop.mjs`）：它 `detached` 地拉起 Electron，让 Electron
自成一个进程组、终端的 Ctrl+C 传不进去，再由启动器整树回收 —— 动手时根进程还活着，
不会出现「根先死、taskkill 扑空、子树留下继续占着端口」。

> 直接 `electron .` 启动会绕过启动器，Ctrl+C 退回旧行为（留下孤儿）。开发请统一走
> `pnpm dev:desktop`。三条退出路径里只有 Ctrl+C 这条是端到端实测过的。

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
不要再开 `pnpm dev:desktop` —— 它会撞上你手起的 8000/5173，报端口被占用并拒绝
接管（这是刻意的，见 A 那一节）；关窗口也只是缩到托盘，进程还在。确实要一起开，
就给桌面壳换一套端口（`NONO_PORT` + `NONO_WEB_URL`）。

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

