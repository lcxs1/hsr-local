#requires -Version 5.1
<#
.SYNOPSIS
    异相仲裁（Peak）真机测试（一键）：起服务端 + 代理 + 游戏，收证据，结束自动判定。

.DESCRIPTION
    1. 调 scripts/game_test.ps1 -Scenario peak 起服务端 / firefly / 游戏，按事件截图、收日志；
    2. 你按 Enter 收工后，跑 scripts/analyze_peak.py：进场 / 战斗包 / 结算 / Confirm / 离开逐项判定；
    3. 打印要发回来的文件。

    脚本不点鼠标；Peak 面板要在游戏里自己点开。入口都没看到也能验服务端：
    -DebugPeak 101 会在登录后自动调 /debug/peak?id=101，把角色直接送进 arena（有场景、无 HUD）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\peak_test.ps1

.EXAMPLE
    # 代理/游戏自己开，只让脚本起服务端并在登录后自动进 Peak 101
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\peak_test.ps1 -NoLaunch -DebugPeak 101

.EXAMPLE
    # 只分析已经跑完的一轮
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\peak_test.ps1 -AnalyseOnly -Run logs\test\test-20261113-210000.server.log
#>
param(
    [ValidateSet('4.6.54', '4.7.0', '4.7.51')]
    [string]$Client = '4.7.51',

    [string]$GameDir = 'E:\SiFu\HSRSiFu',

    [switch]$NoLaunch,      # 代理/游戏自己启动，只让脚本起服务端
    [switch]$AnalyseOnly,   # 不启动任何东西，只分析 -Run / 最新的一轮
    [string]$Run = '',      # *.server.log、运行前缀或 logs\test 目录

    # 登录后自动调 /debug/peak?id=N 直接进 arena（0 = 不自动进，自己点界面）。
    [int]$DebugPeak = 0
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "python venv not found: $Python" }
$OutDir = Join-Path $Root 'logs\test'
$Analyzer = Join-Path $PSScriptRoot 'analyze_peak.py'

if ($AnalyseOnly) {
    $target = if ($Run) { $Run } else { $OutDir }
    & $Python $Analyzer $target
    exit $LASTEXITCODE
}

# --- 1) 说明 -----------------------------------------------------------------
Write-Host ''
Write-Host '游戏里按这个顺序操作（脚本不会替你点鼠标）：' -ForegroundColor Cyan
Write-Host '  1) 登录进世界 -> 星际和平指南 -> 忘却之庭 -> 找【异相仲裁】入口（第三张卡）'
Write-Host '  2) 先只看面板：11 个组？组内 3 小关 + BOSS 都有？困难模式开关在不在？'
Write-Host '  3) 选第 1 组第 1 关（peak 101）-> 编队 -> 开始；看能不能进场景、走位、按 F 开打'
Write-Host '  4) 打完看结算页（轮次/星数）-> 确认结算 -> 回面板；再试 BOSS（104）和困难模式'
Write-Host '  5) 没有入口也能验服务端：加 -DebugPeak 101，登录后自动进 arena（只有场景，没 HUD）'
Write-Host '  6) 回到这个窗口按 Enter 收工，脚本自动判定'
Write-Host ''

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$before = @(Get-ChildItem $OutDir -Filter '*.server.log' -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })

# --- 2) 起服务端 / 代理 / 游戏（复用 game_test.ps1） ---------------------------
$gameTest = Join-Path $PSScriptRoot 'game_test.ps1'
# 同一个 PowerShell 进程里直接跑（另起进程时无 BOM 的 .ps1 会被 PS5.1 按 ANSI 读，见 guide/03 坑 24）。
& $gameTest -Client $Client -Scenario peak -GameDir $GameDir -DebugPeak $DebugPeak -NoLaunch:$NoLaunch

# --- 3) 判定 -----------------------------------------------------------------
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
    Write-Host 'Peak 链跑通了 —— 进场 / 战斗包 / 结算都在。' -ForegroundColor Green
} else {
    Write-Host 'Peak 没跑通。把 report.md、同目录 .server.log / .packets.log、' -ForegroundColor Yellow
    Write-Host '以及 peak 相关截图（*-peak-start*.png / *-battle-info*.png）发回来。' -ForegroundColor Yellow
}
if (-not $ranNew) { exit 3 }
exit $verdict
