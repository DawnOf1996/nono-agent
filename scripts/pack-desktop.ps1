# 把 web + Python sidecar + Electron 打成 Windows 桌面版。
# 用法（仓库根）: .\scripts\pack-desktop.cmd
# 或: powershell -ExecutionPolicy Bypass -File .\scripts\pack-desktop.ps1
# 产物:
#   apps\desktop\release\win-unpacked\Nono.exe
#   apps\desktop\release\Nono-0.1.0-setup.exe

[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipSidecar,
    [switch]$SkipElectron
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$apps = Join-Path $root "apps"
$desktop = Join-Path $apps "desktop"
$webDist = Join-Path $apps "web\dist"
$resources = Join-Path $desktop "resources"
$sidecarDir = Join-Path $root "dist\desktop-sidecar\nono-app-server"
$sidecarExe = Join-Path $sidecarDir "nono-app-server.exe"

function Require-Cmd([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "未找到命令 '$Name'，请先安装后再打包。"
    }
}

function Copy-Tree([string]$From, [string]$To) {
    if (-not (Test-Path $From)) {
        throw "缺少打包输入: $From"
    }
    New-Item -ItemType Directory -Force -Path $To | Out-Null
    Copy-Item -Path (Join-Path $From "*") -Destination $To -Recurse -Force
}

function Assert-LastExit([string]$Label) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 失败，exit=$LASTEXITCODE"
    }
}

Require-Cmd uv
Require-Cmd pnpm
Require-Cmd node

Set-Location $root
Write-Host "==> 同步 Python 依赖"
uv sync --extra dev
Assert-LastExit "uv sync"

if (-not $SkipWeb) {
    Write-Host "==> 安装前端依赖并构建 web"
    Push-Location $apps
    try {
        pnpm install
        Assert-LastExit "pnpm install"
        pnpm build:web
        Assert-LastExit "pnpm build:web"
    } finally {
        Pop-Location
    }
    if (-not (Test-Path (Join-Path $webDist "index.html"))) {
        throw "web 构建失败，未找到 $webDist\index.html"
    }
}

if (-not $SkipSidecar) {
    Write-Host "==> 用 PyInstaller 冻结 sidecar（首次较慢）"
    uv run --with pyinstaller python (Join-Path $root "scripts\build_sidecar.py")
    Assert-LastExit "PyInstaller sidecar"
    if (-not (Test-Path $sidecarExe)) {
        throw "sidecar 构建失败，未找到 $sidecarExe"
    }
}

Write-Host "==> 组装 Electron extraResources"
if (Test-Path $resources) {
    Remove-Item $resources -Recurse -Force
}
Copy-Tree $sidecarDir (Join-Path $resources "sidecar")
Copy-Tree $webDist (Join-Path $resources "web")

if (-not $SkipElectron) {
    Write-Host "==> electron-builder 打 Windows 安装包"
    if (-not $env:ELECTRON_MIRROR) {
        $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    }
    if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) {
        $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
    }
    Push-Location $desktop
    try {
        pnpm exec electron-builder --win
        Assert-LastExit "electron-builder"
    } finally {
        Pop-Location
    }
}

$unpacked = Join-Path $desktop "release\win-unpacked\Nono.exe"
$setup = Get-ChildItem -Path (Join-Path $desktop "release") -Filter "Nono-*-setup.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

Write-Host ""
Write-Host "打包完成。"
if (Test-Path $unpacked) {
    Write-Host "可直接运行: $unpacked"
}
if ($setup) {
    Write-Host "安装包: $($setup.FullName)"
}
