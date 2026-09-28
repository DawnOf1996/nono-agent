# 把 web + Python sidecar + Electron 打成 Windows 桌面版。
# 用法（仓库根）: .\scripts\pack-desktop.cmd
# 或: powershell -ExecutionPolicy Bypass -File .\scripts\pack-desktop.ps1
#
# 版本号 x.y.z
#   x、y：改 apps\desktop\package.json 的 version 前两段，第三段忽略。
#   z：本次打包的本地时间 yyyyMMddHH。例如 2026-09-28 13 时 → 0.2.2026092813。
#   安装包文件名使用这个版本。同一小时内再次打包，版本号相同。
#   NSIS 用 32 位整数解析产品版本，到分钟的 yyyyMMddHHmm 会溢出，所以 z 精确到小时。
#   exe 属性里的四段数字版本是 x.y.MMdd.HHmm（每段 ≤ 65535），供 Windows 资源使用。
#
# 产物:
#   apps\desktop\release\win-unpacked\NoNo.exe
#   apps\desktop\release\NoNo-<x.y.yyyyMMddHH>-setup.exe

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

# x.y 来自 desktop/package.json；z 与 Windows 四段版本由当前本地时间生成。
function Get-DesktopPackVersion([string]$PackageJson) {
    $pkg = Get-Content -Raw -Encoding UTF8 $PackageJson | ConvertFrom-Json
    $raw = [string]$pkg.version
    if ($raw -notmatch '^(\d+)\.(\d+)(?:\.\d+)?$') {
        throw "apps/desktop/package.json 的 version 须为 x.y 或 x.y.z（只读取 x.y），当前为 '$raw'。"
    }
    $major = [int64]$Matches[1]
    $minor = [int64]$Matches[2]
    if ($major -gt 65535 -or $minor -gt 65535) {
        throw "version 的 x、y 必须在 0 到 65535 之间，当前为 $major.$minor。"
    }

    $now = Get-Date
    $revision = $now.ToString("yyyyMMddHH")
    if ([int64]$revision -gt 2147483647) {
        throw "打包时间版本 $revision 超过 NSIS 产品版本上限 2147483647。"
    }
    $monthDay = [int]$now.ToString("MMdd")
    $hourMinute = [int]$now.ToString("HHmm")

    [pscustomobject]@{
        Major      = $major
        Minor      = $minor
        Revision   = $revision
        Version    = "$major.$minor.$revision"
        WinVersion = "$major.$minor.$monthDay.$hourMinute"
        StampText  = $now.ToString("yyyy-MM-dd HH") + " 时"
    }
}

# 只在 electron-builder 运行期间改 package.json，调用方负责恢复原文。
function Write-DesktopPackVersion([string]$PackageJson, [string]$Version, [string]$WinVersion) {
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $scriptPath = Join-Path $env:TEMP ("nono-stamp-desktop-version-{0}.js" -f [guid]::NewGuid().ToString("n"))
    $js = @'
const fs = require("fs");
const file = process.argv[2];
const pkg = JSON.parse(fs.readFileSync(file, "utf8"));
pkg.version = process.argv[3];
pkg.shortVersionWindows = process.argv[4];
pkg.build = pkg.build || {};
pkg.build.buildVersion = process.argv[4];
fs.writeFileSync(file, JSON.stringify(pkg, null, 2) + "\n");
'@
    try {
        [System.IO.File]::WriteAllText($scriptPath, $js, $utf8NoBom)
        & node $scriptPath $PackageJson $Version $WinVersion
        Assert-LastExit "写入桌面版本号"
    } finally {
        Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
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

$desktopVersion = $null
if (-not $SkipElectron) {
    $pkgPath = Join-Path $desktop "package.json"
    $spec = Get-DesktopPackVersion $pkgPath
    $desktopVersion = $spec.Version
    Write-Host "==> 桌面版本 $desktopVersion（前两段 $($spec.Major).$($spec.Minor) 来自 package.json，末段为打包时间 $($spec.StampText)）"
    Write-Host "==> electron-builder 打 Windows 安装包"
    if (-not $env:ELECTRON_MIRROR) {
        $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
    }
    if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) {
        $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $originalPkg = [System.IO.File]::ReadAllText($pkgPath)
    Push-Location $desktop
    try {
        Write-DesktopPackVersion $pkgPath $spec.Version $spec.WinVersion
        pnpm exec electron-builder --win
        Assert-LastExit "electron-builder"
    } finally {
        [System.IO.File]::WriteAllText($pkgPath, $originalPkg, $utf8NoBom)
        Pop-Location
    }
}

$unpacked = Join-Path $desktop "release\win-unpacked\NoNo.exe"
$setup = Get-ChildItem -Path (Join-Path $desktop "release") -Filter "NoNo-*-setup.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

Write-Host ""
Write-Host "打包完成。"
if ($desktopVersion) {
    Write-Host "版本号: $desktopVersion"
}
if (Test-Path $unpacked) {
    Write-Host "可直接运行: $unpacked"
}
if ($setup) {
    Write-Host "安装包: $($setup.FullName)"
}
