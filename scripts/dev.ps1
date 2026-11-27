# 开发态一键启动。
#
# 现在桌面壳自己会把 sidecar 和 Vite 都拉起来（见 apps/desktop/src/main.js 的
# ensureSidecar / ensureWebServer），所以这个脚本只是 `pnpm dev:desktop` 的一层
# 包装，方便在仓库根直接敲。真正的启动逻辑只有那一处，别在这里重复实现。
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Set-Location (Join-Path $root "apps")
pnpm dev:desktop
