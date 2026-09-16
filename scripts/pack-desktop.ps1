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

function Prepare-AppIcon {
    $logo = Join-Path $apps "logo.png"
    if (-not (Test-Path $logo)) {
        throw "缺少应用图标: $logo"
    }
    $buildDir = Join-Path $desktop "build"
    $webLogo = Join-Path $apps "web\public\logo.png"
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
    New-Item -ItemType Directory -Force -Path (Split-Path $webLogo) | Out-Null
    Copy-Item $logo (Join-Path $buildDir "icon.png") -Force

    $logoPy = $logo.Replace("\", "/")
    $buildPy = $buildDir.Replace("\", "/")
    $webPy = $webLogo.Replace("\", "/")
    $scriptPath = Join-Path $buildDir "_make_icon.py"
    @"
from pathlib import Path
from PIL import Image

src = Path("$logoPy")
build = Path("$buildPy")
im = Image.open(src).convert("RGBA")
sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
ico = build / "icon.ico"
im.save(ico, format="ICO", sizes=sizes)

web = im.copy()
web.thumbnail((256, 256), Image.Resampling.LANCZOS)
web_path = Path("$webPy")
web.save(web_path, format="PNG", optimize=True)
print(f"wrote {ico} ({ico.stat().st_size} bytes)")
print(f"wrote {web_path} ({web_path.stat().st_size} bytes)")
"@ | Set-Content -Path $scriptPath -Encoding utf8
    try {
        uv run --with pillow python $scriptPath
        Assert-LastExit "generate app icon"
    } finally {
        Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path (Join-Path $buildDir "icon.ico"))) {
        throw "生成 icon.ico 失败"
    }
}

Require-Cmd uv
Require-Cmd pnpm
Require-Cmd node

Set-Location $root
Write-Host "==> 同步 Python 依赖"
uv sync --extra dev
Assert-LastExit "uv sync"

Write-Host "==> 从 apps/logo.png 生成应用图标"
Prepare-AppIcon

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
