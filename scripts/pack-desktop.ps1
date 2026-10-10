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
#
# -OutputDir:
#   换打包输出目录。相对路径按 apps\desktop 解析，绝对路径直接用。
#   编辑器（Cursor/VS Code 等）可能长期占着 release 里的旧文件，
#   导致 electron-builder 删不掉而报 EBUSY；这时换一个输出目录即可继续打包:
#     .\scripts\pack-desktop.cmd -OutputDir release-run
#
# -Version:
#   显式指定产品版本，形如 0.3.0（v 前缀会自动剥掉）。不给就退回上面的时间戳方案。
#   CI 上必须给：GitHub Release 的 tag 才是权威版本号，而时间戳跟着 runner 的时区
#   走，两边对不上，用户在「关于」里看到的版本就会和 Release 名对不起来。
#
# -Publish:
#   打包完把产物发成 GitHub Release（安装包 / latest.yml / blockmap 一起）。
#   需要环境变量 GH_TOKEN。本地手工打包别开 —— 免得误传。
#

[CmdletBinding()]
param(
    [switch]$SkipWeb,
    [switch]$SkipSidecar,
    [switch]$SkipElectron,
    [string]$OutputDir = "release",
    [string]$Version,
    [switch]$Publish,
    [switch]$PublishOnly
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$apps = Join-Path $root "apps"
$desktop = Join-Path $apps "desktop"
$webDist = Join-Path $apps "web\dist"
$resources = Join-Path $desktop "resources"
$sidecarDir = Join-Path $root "dist\desktop-sidecar\nono-app-server"
$sidecarExe = Join-Path $sidecarDir "nono-app-server.exe"
$outDir = if ([System.IO.Path]::IsPathRooted($OutputDir)) { $OutputDir } else { Join-Path $desktop $OutputDir }

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

# Windows Restart Manager: 列出还持有某个文件句柄的进程，EBUSY 才能说清是谁占的。
$script:fileLockFinderSource = @'
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;

public static class FileLockFinder {
    const int ERROR_MORE_DATA = 234;

    [StructLayout(LayoutKind.Sequential)]
    struct RM_UNIQUE_PROCESS {
        public int ProcessId;
        public System.Runtime.InteropServices.ComTypes.FILETIME ProcessStartTime;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct RM_PROCESS_INFO {
        public RM_UNIQUE_PROCESS Process;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 256)] public string AppName;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 64)] public string ServiceShortName;
        public uint ApplicationType;
        public uint AppStatus;
        public uint TSSessionId;
        [MarshalAs(UnmanagedType.Bool)] public bool Restartable;
    }

    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmStartSession(out uint handle, int flags, string key);
    [DllImport("rstrtmgr.dll")]
    static extern int RmEndSession(uint handle);
    [DllImport("rstrtmgr.dll", CharSet = CharSet.Unicode)]
    static extern int RmRegisterResources(uint handle, uint fileCount, string[] files, uint appCount, IntPtr apps, uint serviceCount, string[] services);
    [DllImport("rstrtmgr.dll")]
    static extern int RmGetList(uint handle, out uint needed, ref uint count, [In, Out] RM_PROCESS_INFO[] infos, ref uint reasons);

    public static string[] Describe(string path) {
        uint handle;
        string key = Guid.NewGuid().ToString("N").Substring(0, 32);
        if (RmStartSession(out handle, 0, key) != 0) return new string[0];
        try {
            RmRegisterResources(handle, 1, new string[] { path }, 0, IntPtr.Zero, 0, null);
            uint needed = 0, count = 0, reasons = 0;
            if (RmGetList(handle, out needed, ref count, null, ref reasons) != ERROR_MORE_DATA || needed == 0) return new string[0];
            var infos = new RM_PROCESS_INFO[needed];
            count = needed;
            if (RmGetList(handle, out needed, ref count, infos, ref reasons) != 0) return new string[0];
            var result = new List<string>();
            for (int i = 0; i < count; i++) {
                int pid = infos[i].Process.ProcessId;
                string label;
                try { label = Process.GetProcessById(pid).ProcessName + " (PID " + pid + ")"; }
                catch { label = (infos[i].AppName ?? "?") + " (PID " + pid + ")"; }
                result.Add(label);
            }
            return result.ToArray();
        } finally {
            RmEndSession(handle);
        }
    }
}
'@

function Get-FileLockingProcess([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) {
        return @()
    }
    try {
        if (-not ("FileLockFinder" -as [type])) {
            Add-Type -TypeDefinition $script:fileLockFinderSource -Language CSharp
        }
        return @([FileLockFinder]::Describe($Path))
    } catch {
        return @()
    }
}

function Format-FileLockHint([string]$Path) {
    $lockers = Get-FileLockingProcess $Path
    if (@($lockers).Count -eq 0) {
        return "未能定位占用进程（占用可能刚被释放，或占用者没有登记到 Restart Manager）。"
    }
    return "占用进程: " + (@($lockers) -join "、")
}

# 上一轮打包出来的 NoNo.exe 还在跑时，输出目录删不掉，先结束它。
function Stop-StaleDesktopApp([string]$OutPath) {
    $prefix = $OutPath.TrimEnd("\") + "\"
    Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
        $proc = $_
        $path = $null
        try { $path = $proc.Path } catch { return }
        if (-not $path) { return }
        if ($path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -and $proc.ProcessName -match "^(NoNo|electron)$") {
            Write-Host "==> 结束残留进程 $($proc.ProcessName) (PID $($proc.Id))：$path"
            Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

# electron-builder 不会先删上一次的 appOutDir，残留文件被占就直接 EBUSY。
function Clear-StaleAppOutDir([string]$OutPath) {
    $stale = Get-ChildItem -Path $OutPath -Directory -Filter "win-unpacked*" -ErrorAction SilentlyContinue
    foreach ($dir in $stale) {
        $errs = $null
        Remove-Item -LiteralPath $dir.FullName -Recurse -Force -ErrorAction SilentlyContinue -ErrorVariable errs
        if (Test-Path -LiteralPath $dir.FullName) {
            # 删不掉的文件就是被占的那些，直接问 Restart Manager 是谁占的。
            $locked = $null
            $left = Get-ChildItem -LiteralPath $dir.FullName -Recurse -File -ErrorAction SilentlyContinue | Select-Object -First 50
            foreach ($file in $left) {
                if (@(Get-FileLockingProcess $file.FullName).Count -gt 0) {
                    $locked = $file.FullName
                    break
                }
            }
            if (-not $locked) {
                $locked = $dir.FullName
            }
            throw ("清不掉上一次的打包产物: $locked`n" + (Format-FileLockHint $locked) + "`n先关掉占用它的程序（编辑器里打开该文件也算），再重新打包。")
        }
        Write-Host "==> 已清理旧产物 $($dir.Name)"
    }
}

# 不传 $Explicit：x.y 来自 desktop/package.json，z 是当前时间 yyyyMMddHH（本地手工打包）。
# 传了 $Explicit：以它为准（CI 从 Release tag 来）。但 Windows 四段版本仍然跟构建
#   时间走 —— 那四个数字只喂给 Windows 资源，管的是「资源有没有变过」，
#   不参与用户看到的版本号语义，所以不必跟着 tag 变。
function Get-DesktopPackVersion([string]$PackageJson, [string]$Explicit) {
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
    $monthDay = [int]$now.ToString("MMdd")
    $hourMinute = [int]$now.ToString("HHmm")

    $version = "$major.$minor.$revision"
    $stampText = "前两段 $($major).$($minor) 来自 package.json，末段为打包时间 " +
        $now.ToString("yyyy-MM-dd HH") + " 时"

    if ($Explicit) {
        $candidate = ($Explicit.Trim() -replace '^v', '')
        if ($candidate -notmatch '^(\d+)\.(\d+)\.(\d+)$') {
            throw "-Version 须为 x.y.z（可带 v 前缀），当前为 '$Explicit'。"
        }
        $cMajor = [int64]$Matches[1]
        $cMinor = [int64]$Matches[2]
        $cPatch = [int64]$Matches[3]
        if ($cMajor -gt 65535 -or $cMinor -gt 65535 -or $cPatch -gt 65535) {
            throw "-Version 的每一段都必须 ≤ 65535，当前为 '$candidate'。"
        }
        $major = $cMajor
        $minor = $cMinor
        $version = $candidate
        $stampText = "版本由 -Version 指定为 $candidate"
    }
    elseif ([int64]$revision -gt 2147483647) {
        throw "打包时间版本 $revision 超过 NSIS 产品版本上限 2147483647。"
    }

    [pscustomobject]@{
        Major      = $major
        Minor      = $minor
        Revision   = $revision
        Version    = $version
        WinVersion = "$major.$minor.$monthDay.$hourMinute"
        StampText  = $stampText
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

# 用 gh CLI 发 Release，而不是 electron-builder 的 --publish。
# 换掉的理由（踩过的坑）：
#   1. electron-builder 一次构建会建出两条 Release —— exe 和 latest.yml 走的是两个
#      publisher 实例（getOrCreatePublisher 用整个 publishConfig 当缓存键，而写
#      update info 时会多带一个 updaterCacheDirName 字段），两个实例各自
#      getOrCreateRelease，于是同一个 tag 出现两条 Release。
#   2. .exe（几十 MB）的上传失败会被静默吞掉：构建步骤 exit 0、CI 全绿，
#      Release 上却只剩 .blockmap，而打包那一步的日志早已被 PyInstaller 的输出
#      撑爆被 GitHub 截断，事后根本查不出原因。
# gh 的好处是：一条 Release、失败会明确报错、日志只有几行，出问题一眼看得见。
function Publish-GitHubRelease([string]$OutDir, [string]$Version) {
    Require-Cmd gh
    $raw = gh release view $Version --json tagName --jq '.tagName' 2>$null
    if ($LASTEXITCODE -eq 0 -and $raw) {
        Write-Host "==> Release $raw 已存在，补传产物"
    } else {
        # --generate-notes 而不是 --notes-from-tag：后者要求 annotated tag，
        # 而我们打的是 lightweight tag，那样会直接失败。
        gh release create $Version --title $Version --generate-notes
        Assert-LastExit "gh release create"
        Write-Host "==> 已创建 Release $Version"
    }
    # 只取输出目录顶层的文件。递归会把 win-unpacked 里的一堆 .dll 也拖上去。
    $assets = Get-ChildItem -Path $OutDir -File |
        Where-Object { $_.Name -like "*.exe" -or $_.Name -like "*.exe.blockmap" -or $_.Name -eq "latest.yml" }
    if (-not $assets) {
        throw "在 $OutDir 没找到可上传的产物（*.exe / *.exe.blockmap / latest.yml）。"
    }
    $paths = @($assets | ForEach-Object { $_.FullName })
    Write-Host "==> 上传 $($paths.Count) 个资产"
    gh release upload $Version @paths --clobber
    Assert-LastExit "gh release upload"
}

# tag 名要带 v 前缀，和打 tag 的写法对齐。CI 上 GITHUB_REF_NAME 就是它；
# 本地则从当前 commit 反查。都拿不到时才退回 "v$Version"。
function Resolve-ReleaseTag([string]$Version) {
    $tag = $env:GITHUB_REF_NAME
    if (-not $tag) {
        $tag = (& git describe --tags --exact-match 2>$null)
    }
    if (-not $tag) {
        $tag = "v$Version"
        Write-Host "警告: 取不到当前 git tag，按 $tag 处理。"
    }
    return $tag
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

if ($PublishOnly) {
    # 只发版、不重建。CI 上把「打包」和「发布」拆成两个 step 就是为了这个：
    # 打包那一步十几分钟、日志被 PyInstaller 撑爆会被 GitHub 截断，出事时
    # 根本看不到 electron-builder 说了什么。发布单独一步，日志只有几行，
    # 而且失败了可以原样重跑，不用再等一遍完整构建。
    if (-not $env:GH_TOKEN) {
        throw "-PublishOnly 需要环境变量 GH_TOKEN。"
    }
    $tag = Resolve-ReleaseTag ""
    Write-Host "==> 发布到 GitHub Release: $tag"
    Publish-GitHubRelease $outDir $tag
    return
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
    $spec = Get-DesktopPackVersion $pkgPath $Version
    $desktopVersion = $spec.Version
    Write-Host "==> 桌面版本 $desktopVersion（$($spec.StampText)）"
    Write-Host "==> electron-builder 打 Windows 安装包"
    # 本地（国内网络）默认走 npmmirror 加速。GitHub runner 在美国机房，走镜像反而
    # 慢一大截，而且镜像偶尔会缺刚发布的版本 —— 所以 CI 上不设。
    if (-not $env:GITHUB_ACTIONS) {
        if (-not $env:ELECTRON_MIRROR) {
            $env:ELECTRON_MIRROR = "https://npmmirror.com/mirrors/electron/"
        }
        if (-not $env:ELECTRON_BUILDER_BINARIES_MIRROR) {
            $env:ELECTRON_BUILDER_BINARIES_MIRROR = "https://npmmirror.com/mirrors/electron-builder-binaries/"
        }
    }
    if ($Publish -and -not $env:GH_TOKEN) {
        throw "-Publish 需要环境变量 GH_TOKEN（GitHub Actions 里用 secrets.GITHUB_TOKEN 注入）。"
    }
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    $originalPkg = [System.IO.File]::ReadAllText($pkgPath)
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $defaultOut = [System.IO.Path]::GetFullPath((Join-Path $desktop "release"))
    if ([System.IO.Path]::GetFullPath($outDir) -ne $defaultOut) {
        Write-Host "==> 输出目录改用 $outDir"
    }
    Stop-StaleDesktopApp $outDir
    Clear-StaleAppOutDir $outDir
    Push-Location $desktop
    try {
        Write-DesktopPackVersion $pkgPath $spec.Version $spec.WinVersion
        $builderArgs = @("--win", "--config.directories.output=$OutputDir")
        # 必须显式 --publish never，而不是「什么都不传」：
        # electron-builder 的默认策略是 onTagOrDraft —— 在 CI 的 tag 构建里它
        # 照样会去建 GitHubPublisher、照样要 GH_TOKEN，找不到就直接抛错退出。
        # never 才是「构建归构建、发版归发版」。latest.yml 照常在本地生成
        # （更新清单是「regardless of publish state」写的），只是不再由它上传。
        $builderArgs += "--publish"
        $builderArgs += "never"
        pnpm exec electron-builder @builderArgs
        if ($LASTEXITCODE -ne 0) {
            Write-Host "electron-builder 失败。常见原因: 输出目录里的旧产物被占用（编辑器打开过里面的文件也算），或上一次打包的 NoNo.exe 仍在运行。"
            throw "electron-builder 失败，exit=$LASTEXITCODE"
        }
    } finally {
        [System.IO.File]::WriteAllText($pkgPath, $originalPkg, $utf8NoBom)
        Pop-Location
    }
}

$unpacked = Join-Path $outDir "win-unpacked\NoNo.exe"
$setup = Get-ChildItem -Path $outDir -Filter "NoNo-*-setup.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

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
# 更新检测只认 latest.yml。它不在，说明 package.json 的 publish 段没配好或者
# 打包器没生成 —— 这时候客户端永远查不到新版，而且没人会报错，很容易漏掉。
$latestYml = Join-Path $outDir "latest.yml"
if (Test-Path $latestYml) {
    Write-Host "更新清单: $latestYml"
} elseif ($desktopVersion) {
    Write-Host "警告: 没找到 latest.yml，这次打出来的包没法给已安装的用户推送更新。"
}

# 发版放在最后：产物校验过了才值得传上去，而且失败要真的让 CI 红掉。
if ($Publish -and $desktopVersion) {
    $tag = Resolve-ReleaseTag $desktopVersion
    Write-Host ""
    Write-Host "==> 发布到 GitHub Release: $tag"
    Publish-GitHubRelease $outDir $tag
}
