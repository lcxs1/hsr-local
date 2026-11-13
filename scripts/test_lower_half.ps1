#requires -Version 5.1
<#
.SYNOPSIS
    混沌回忆上下半真机测试（一键）：起服务端 + 代理 + 游戏，结束后自动判定下半有没有真打。

.DESCRIPTION
    1. 检查 config.toml 的 [endgame] advance 是不是 official（这一轮验的就是官方包序）；
    2. 调 scripts/game_test.ps1 起服务端 / firefly / 游戏，按事件截图、收日志；
    3. 你按 Enter 收工后，跑 scripts/analyze_run.py：
       打印每半场的 turns / cost、换半场之后客户端 cast 了几次、PASS 还是 FAIL。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_lower_half.ps1

.EXAMPLE
    # 只分析已经跑完的一轮，不启动任何东西
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\test_lower_half.ps1 -AnalyseOnly -Run logs\test\test-20261112-185701.server.log
#>
param(
    [ValidateSet('4.6.54', '4.7.0', '4.7.51')]
    [string]$Client = '4.7.51',

    [switch]$NoLaunch,      # 代理/游戏自己启动，只让脚本起服务端
    [switch]$AnalyseOnly,   # 不启动任何东西，只分析 -Run / 最新的一轮
    [string]$Run = ''       # *.server.log、运行前缀或 logs\test 目录
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "python venv not found: $Python" }
$OutDir = Join-Path $Root 'logs\test'
$Analyzer = Join-Path $PSScriptRoot 'analyze_run.py'

if ($AnalyseOnly) {
    $target = if ($Run) { $Run } else { $OutDir }
    & $Python $Analyzer $target
    exit $LASTEXITCODE
}

# --- 1) 模式检查 -------------------------------------------------------------
$line = $null
$configPath = Join-Path $Root 'config.toml'
if (Test-Path $configPath) {
    $hit = Select-String -Path $configPath -Pattern '^advance\s*=' | Select-Object -First 1
    if ($hit) { $line = $hit.Line.Trim() }
}
if (-not $line -or $line -notmatch 'official') {
    Write-Host "config.toml 里的阶段切换不是 official：$line" -ForegroundColor Red
    Write-Host '先把 [endgame] advance 改成 "official" 再跑（这一轮验的就是官方包序）。'
    exit 2
}
Write-Host "阶段切换：$line" -ForegroundColor DarkGray

# --- 2) 说明 -----------------------------------------------------------------
Write-Host ''
Write-Host '游戏里按这个顺序打（脚本不会替你点鼠标）：' -ForegroundColor Cyan
Write-Host '  1) 大厅点混沌回忆水晶 -> 第 1 层（5501）-> 配两队';
Write-Host '  2) 上半正常打完（别秒退，判据要看回合数）';
Write-Host '  3) 关键观察：下半能不能走位、按 F 进战斗、真的出招';
Write-Host '  4) 打完退出挑战；有条件的话再整局打一次第 2 层（用来区分「第二场战斗」和「某一层的数据」）';
Write-Host '  5) 回到这个窗口按 Enter 收工，脚本自动判定并告诉你该发哪些文件';
Write-Host ''

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$before = @(Get-ChildItem $OutDir -Filter '*.server.log' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })

# --- 3) 起服务端 / 代理 / 游戏（复用 game_test.ps1） ---------------------------
$gameTest = Join-Path $PSScriptRoot 'game_test.ps1'
# 同一个 PowerShell 进程里直接跑，不另起 powershell.exe：另起进程时如果脚本没有 UTF-8 BOM，
# Windows PowerShell 5.1 会按 ANSI 读中文 → 乱码 + 语法错误（2026-11-12 踩过）。
& $gameTest -Client $Client -NoLaunch:$NoLaunch

# --- 4) 判定 -----------------------------------------------------------------
$after = @(Get-ChildItem $OutDir -Filter '*.server.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime)
$new = @($after | Where-Object { $before -notcontains $_.FullName })
$ranNew = $new.Count -gt 0
if (-not $ranNew) {
    Write-Host ''
    Write-Host '这一轮没有产生新的服务端日志（game_test.ps1 没跑起来？），下面分析的只是旧日志。' -ForegroundColor Yellow
}
$target = if ($new.Count) { $new[-1].FullName } elseif ($Run) { $Run } else { $OutDir }

Write-Host ''
Write-Host '== 判定 ==' -ForegroundColor Cyan
& $Python $Analyzer $target
$verdict = $LASTEXITCODE

$report = Get-ChildItem $OutDir -Filter '*.report.md' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime | Select-Object -Last 1
Write-Host ''
if ($report) { Write-Host ("报告：" + $report.FullName) -ForegroundColor Green }
if ($verdict -eq 0) {
    Write-Host '下半真打了 —— 换半场 + 战斗都通了。' -ForegroundColor Green
} else {
    Write-Host '下半还是没真打。把 report.md、同目录 .server.log / .packets.log、' -ForegroundColor Yellow
    Write-Host '以及 *-battle-info+4.png 那张截图发回来（全黑就说明战斗场景没加载完）。' -ForegroundColor Yellow
}
if (-not $ranNew) { exit 3 }
exit $verdict
