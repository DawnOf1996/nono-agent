<#
.SYNOPSIS
    取 agent-browser 的 Windows 可执行文件到 apps/resources/agent-browser/。

.DESCRIPTION
    browser-agent 工具插件的**交互那一半**靠这个二进制（读页面、点元素、输文字）。

    **为什么是 apps/resources/，不是 apps/desktop/resources/**：后者是
    extraResources 的组装区，pack-desktop.ps1 每次打包都会先整个删掉再从构建
    产物重建（sidecar 由 PyInstaller 冻出来、web 由 vite 出）。放在那儿的东西
    活不过一次打包 —— 而 agent-browser 是**输入**，不是构建产物。详见
    apps/resources/README.md。

    本仓库选择**把二进制提交进 git**（而不是每次 CI 现下）：换来的是任何人
    clone 完就能直接打包，不需要网络，也不会因为 registry 抖一下就打不出包。
    代价是仓库里多 17 MB，而且进了历史就一直在 —— 想换掉用 git rm + filter-repo。

    脚本仍然留着：它是「这份文件哪来的、怎么复现」的说明，也是版本升级时的
    唯一入口。已经有文件就直接跳过，所以正常打包不会触发下载。

.PARAMETER Version
    agent-browser 版本号。**钉死** —— 这是实测过能驱动 Electron <webview> 的
    版本，跟着 latest 漂等于每次打包都在换一条没测过的路。

.PARAMETER DestDir
    落地目录，默认 <仓库根>/apps/resources/agent-browser。

.PARAMETER Force
    已有文件也重新下载。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts\fetch_agent_browser.ps1 -Force
#>
[CmdletBinding()]
param(
    [string]$Version = "0.39.1",
    [string]$DestDir,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
if (-not $DestDir) { $DestDir = Join-Path $root "apps\resources\agent-browser" }

# 必须定义在使用之前：PowerShell 在**运行到调用处**才去查函数，不是解析期。
function Require-Cmd([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "未找到命令 '$Name'。agent-browser 取件需要它来下载和解包 tarball。"
    }
}

function Assert-LastExit([string]$Label) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 失败，exit=$LASTEXITCODE"
    }
}

# 只冻了 Windows x64。其余平台的 npm 包结构没验证过，与其打包时才发现，
# 不如在这里就把话说清楚。
$exeName = "agent-browser-win32-x64.exe"
if ($env:OS -ne "Windows_NT") {
    throw "fetch_agent_browser 目前只支持 Windows（冻结的是 $exeName）。其他平台需要在 pack-desktop.ps1 里补对应的产物。"
}

$destExe = Join-Path $DestDir $exeName
if ((Test-Path $destExe) -and (-not $Force)) {
    Write-Host "agent-browser 已在缓存里：$destExe"
    exit 0
}

# 先确认 arch。x64 之外的架构（arm64）不在 npm 包里，早失败早看到。
if ($env:PROCESSOR_ARCHITECTURE -notin @("AMD64", "x64")) {
    throw "只冻结了 x64 的 agent-browser，当前架构是 '$env:PROCESSOR_ARCHITECTURE'。"
}

$url = "https://registry.npmjs.org/agent-browser/-/agent-browser-$Version.tgz"
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("agent-browser-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$tgz = Join-Path $tmp "agent-browser.tgz"

try {
    Write-Host "==> 下载 agent-browser $Version（约 51 MB，CI 上 1~2 分钟）"
    Write-Host "    $url"
    # -L 跟随重定向：registry.npmjs.org 会把包 tarball 甩到 CDN。
    # 不加 --fail 的话，404 会存成一个几百字节的 HTML，之后解包才炸，
    # 报错还长得像「tar 文件损坏」，极具误导性。
    curl.exe -sSfL --retry 3 --retry-delay 2 --max-time 900 -o $tgz $url
    Assert-LastExit "下载 agent-browser"

    if (-not (Test-Path $tgz)) { throw "下载完成但文件不存在：$tgz" }
    $size = (Get-Item $tgz).Length
    # 一个真的 tarball 不可能只有几 KB。太小 = 下到了错误页而不是包。
    if ($size -lt 1MB) {
        throw "下载到的 agent-browser 包只有 $size 字节，明显不是 tarball。八成是网络中间层返回了错误页。"
    }
    Write-Host "    已下载 $([math]::Round($size / 1MB, 1)) MB"

    Write-Host "==> 解包"
    Push-Location $tmp
    try {
        tar -xzf $tgz
        Assert-LastExit "解包 agent-browser"
    } finally {
        Pop-Location
    }

    # npm 包的路径固定在 package/bin/ 下。写死是刻意的：这里不做 glob 搜索，
    # 万一上游改了目录结构，应该在这里响亮地失败，而不是悄悄挑一个别的 exe。
    $src = Join-Path $tmp "package\bin\$exeName"
    if (-not (Test-Path $src)) {
        throw "包里没有 package\bin\$exeName。上游改了包结构？需要同步更新 scripts\fetch_agent_browser.ps1 和 apps\desktop\src\main.js 里的 BROWSER_AGENT_BINARY。"
    }

    New-Item -ItemType Directory -Force -Path $DestDir | Out-Null
    Copy-Item -Path $src -Destination $destExe -Force

    # 拷完再确认一次存在。少这一步的话，文件缺失会一路飘到 electron-builder
    # 才炸，而那时候信息已经跟原因脱节了。
    if (-not (Test-Path $destExe)) {
        throw "拷贝后仍找不到 $destExe"
    }
    Write-Host "==> agent-browser 已就位：$destExe（$([math]::Round((Get-Item $destExe).Length / 1MB, 1)) MB）"
}
finally {
    if (Test-Path $tmp) { Remove-Item $tmp -Recurse -Force }
}

function Assert-LastExit([string]$Label) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Label 失败，exit=$LASTEXITCODE"
    }
}
