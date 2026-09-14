# 同时拉起 sidecar 与 Vite（Windows）
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

Start-Process -FilePath "uv" -ArgumentList @("run", "nono-app-server") -WorkingDirectory (Join-Path $root "apps\server")
Start-Process -FilePath "pnpm" -ArgumentList @("dev:web") -WorkingDirectory $root

Write-Host "已启动 sidecar (127.0.0.1:8000) 与 Vite (127.0.0.1:5173)"
Write-Host "桌面壳另开终端: pnpm dev:desktop"
