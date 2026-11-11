#requires -Version 5.1
<#
.SYNOPSIS
    hsr-local 测试器（v2）：起服务端 → 起代理/游戏 → 事件驱动截图 → 收证据 → 出报告。

.DESCRIPTION
    两种场景（-Scenario）：

    challenge（默认，2026-11-11 起的重点）
        专门验证「混沌回忆 上半 → 换组 → 下半 → 结算 → 退出」这条链。
        脚本不点鼠标，只做环境和证据：
          1. 起服务端（默认 --profile 4.7.0）、等登录；
          2. 每秒扫服务端日志 + packets.log，把事件打进时间线：
             start challenge / challenge battle info / cast / battle result /
             challenge advance / challenge settle / leave challenge；
          3. 事件触发截图：开始挑战、每次战斗包、换组后 +0/+6/+14/+25 秒
             （下半黑屏就在这段）、结算、退出；
          4. 全程镜像客户端 Player.log（每次启动会被覆盖，所以边跑边存）；
          5. 按 Enter 结束 → 生成 report.md（结论摘要 + 时间线 + 挑战请求序列 +
             下半窗口客户端日志 + 异常汇总 + 截图清单）。

    core
        旧的通用回归（凝滞虚影三连战 / 换队 / 命途 / 换肤 / 指南），
        截图按 -ShotPlan 定时拍，报告按请求计数。

.EXAMPLE
    # 混沌回忆取证（默认 4.7.0）：脚本起服务端+代理+游戏，UAC 点“是”
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1

.EXAMPLE
    # 代理/游戏已经自己开好了，只让脚本起服务端并抓证据
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1 -NoLaunch

.EXAMPLE
    # 只看测试步骤，不启动任何东西
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1 -NoGame

.EXAMPLE
    # 旧的通用回归
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1 -Scenario core -Client 4.6.54
#>
param(
    [ValidateSet('challenge', 'core')]
    [string]$Scenario = 'challenge',

    # 客户端版本：决定 exe、Player.log 与服务端 --profile。
    [ValidateSet('4.6.54', '4.7.0')]
    [string]$Client = '4.7.0',

    [switch]$NoLaunch,      # 不启动代理/游戏，你自己启动
    [switch]$NoGame,        # 只打印测试步骤
    [switch]$KeepServer,    # 结束时保留服务端
    [switch]$KeepProxy,     # 结束时不动系统代理
    [switch]$PatchPrefs,    # 可选：预写客户端“已读”PlayerPrefs（客户端登录后会覆盖）

    [int]$LoginTimeoutSec = 240,
    [int]$MaxMinutes = 25,          # 兜底：这么久后自动收工
    [int[]]$ShotPlan = @(10, 40, 90),  # 只给 -Scenario core 用
    [switch]$StopAfterSettle,       # 结算后 45 s 没有新挑战包就自动收工

    # 只分析已经落盘的日志（不启动服务端/游戏）：
    #   直接传 <run>.server.log；同目录同名 .packets.log / .client.log 会自动带上。
    [string]$AnalyseOnly = '',

    [string]$GameDir = 'E:\SiFu\HSRSiFu',
    [string]$GameExe = '',          # 留空 = 按 -Client 选
    [string]$ProxyExe = 'E:\SiFu\HSRSiFu\firefly-go-proxy.exe',
    [string[]]$ServerArgs = @()     # 追加给服务端（--profile 已自动加）
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

# 4.7.0 是 CN 渠道：Player.log 在 miHoYo 目录下，与 4.6.54 的 Cognosphere 不同
# （docs/reference/05 §1；写错路径会收到旧日志、报告失真）。
$ClientProfiles = @{
    '4.6.54' = @{
        Exe = 'E:\SiFu\HSRSiFu\StarRail_4.6.54\StarRail.exe'
        Log = Join-Path $env:USERPROFILE 'AppData\LocalLow\Cognosphere\Star Rail\Player.log'
    }
    '4.7.0' = @{
        Exe = 'E:\SiFu\HSRSiFu\StarRail_CN_4.7.0\StarRail.exe'
        Log = Join-Path $env:USERPROFILE 'AppData\LocalLow\miHoYo\崩坏：星穹铁道\Player.log'
    }
}
if (-not $GameExe) { $GameExe = $ClientProfiles[$Client].Exe }

$Python = Join-Path $Root '.venv\Scripts\python.exe'
$OutDir = Join-Path $Root 'logs\test'
$PacketsLog = Join-Path $Root 'logs\packets.log'
$PlayerLog = $ClientProfiles[$Client].Log
$RunId = 'test-' + (Get-Date -Format 'yyyyMMdd-HHmmss')

# -AnalyseOnly：把已经落盘的一次运行重新分析成报告（不启动任何东西）。
if ($AnalyseOnly) {
    if (-not (Test-Path $AnalyseOnly)) { throw "找不到日志：$AnalyseOnly" }
    $serverOut = (Resolve-Path $AnalyseOnly).Path
    # xxx.server.log -> xxx（文件名本身可能带 .server / .client / .packets 后缀）
    $RunId = [IO.Path]::GetFileNameWithoutExtension($serverOut)
    if ($RunId -match '^(.*)\.(server|client|packets)$') { $RunId = $Matches[1] }
    $OutDir = Split-Path -Parent $serverOut
    $siblingPackets = Join-Path $OutDir "$RunId.packets.log"
    $siblingClient = Join-Path $OutDir "$RunId.client.log"
    if (Test-Path $siblingPackets) { $PacketsLog = $siblingPackets }
    if (Test-Path $siblingClient) { $PlayerLog = $siblingClient }
}

# --------------------------------------------------------------- small utils
function Write-Head($text) {
    Write-Host ''
    Write-Host ('=' * 68) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 68) -ForegroundColor Cyan
}

function Write-Step($text) { Write-Host "[test] $text" -ForegroundColor Yellow }
function Write-Evt($text) { Write-Host "  [evt] $text" -ForegroundColor Green }
function Write-Warn2($text) { Write-Host "  [warn] $text" -ForegroundColor Red }

function Test-PortFree([int]$port, [string]$kind) {
    if ($kind -eq 'tcp') {
        $client = New-Object System.Net.Sockets.TcpClient
        try {
            $client.Connect('127.0.0.1', $port)
            $client.Close()
            return $false
        } catch {
            return $true
        } finally {
            $client.Dispose()
        }
    }
    try {
        $udp = New-Object System.Net.Sockets.UdpClient -ArgumentList $port
        $udp.Close()
        return $true
    } catch {
        return $false
    }
}

function Save-Screenshot([string]$Path) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    if (-not ('GameTestDpi' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class GameTestDpi {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
    }
    [GameTestDpi]::SetProcessDPIAware() | Out-Null
    $vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bmp = New-Object System.Drawing.Bitmap($vs.Width, $vs.Height)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    try {
        $g.CopyFromScreen($vs.Left, $vs.Top, 0, 0, $bmp.Size)
        $bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally {
        $g.Dispose()
        $bmp.Dispose()
    }
}

# 增量读文件（服务端日志/packets.log/Player.log 都是边写边读）。
# 每个文件记住自己的游标；返回“完整行”，残行留到下一次。
$script:Cursors = @{}
$script:Carry = @{}

function Read-Chunk([string]$path) {
    if (-not (Test-Path $path)) { return '' }
    if (-not $script:Cursors.ContainsKey($path)) { $script:Cursors[$path] = [long]0 }
    $off = [long]$script:Cursors[$path]
    try {
        $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
    } catch {
        return ''
    }
    try {
        $len = $fs.Length
        if ($len -lt $off) { $off = 0 }
        if ($len -eq $off) { return '' }
        $fs.Seek($off, [System.IO.SeekOrigin]::Begin) | Out-Null
        $count = [int]($len - $off)
        $buf = New-Object byte[] $count
        $read = $fs.Read($buf, 0, $count)
        $script:Cursors[$path] = $off + $read
        return [System.Text.Encoding]::UTF8.GetString($buf, 0, $read)
    } finally {
        $fs.Dispose()
    }
}

function Read-NewLines([string]$path) {
    $chunk = Read-Chunk $path
    if (-not $chunk) { return @() }
    if (-not $script:Carry.ContainsKey($path)) { $script:Carry[$path] = '' }
    $text = $script:Carry[$path] + $chunk
    $parts = $text -split "`n"
    if ($parts.Count -le 1) {
        $script:Carry[$path] = $text
        return @()
    }
    $script:Carry[$path] = $parts[$parts.Count - 1]
    $lines = @()
    for ($i = 0; $i -lt $parts.Count - 1; $i++) { $lines += $parts[$i].TrimEnd("`r") }
    return $lines
}

function Set-SystemProxy([int]$enable) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    Set-ItemProperty $key -Name ProxyEnable -Value $enable
}

# 服务端日志 / packets.log 行首都是 HH:mm:ss.fff，用来切片窗口。
function Get-Stamp([string]$line) {
    if ($line -match '^(\d{2}:\d{2}:\d{2}\.\d{3})') { return $Matches[1] }
    return ''
}

function Select-Window([object[]]$lines, [string]$from, [string]$to) {
    if (-not $from) { return @() }
    $out = @()
    foreach ($line in $lines) {
        $stamp = Get-Stamp $line
        if (-not $stamp) { continue }
        if ($stamp -lt $from) { continue }
        if ($to -and $stamp -gt $to) { continue }
        $out += $line
    }
    return $out
}

# ------------------------------------------------------------- scenario plan
$ChallengeSteps = @(
    '1) 登录后：星际和平指南（或右上角菜单）→ 忘却之庭 → 混沌回忆',
    '   如果「指导 → 传送」进忘却之庭卡在加载条：别等，直接退出游戏重开，改走指南面板。',
    '2) 选【第 1 层】（不要选第 12 层：那是 4.7 的星启 3 节点模式，另算）',
    '3) 编队界面：上半一队、下半另一队（两队都放满，方便看换队）→ 开始挑战',
    '4) 上半：走到怪旁边按 F 开打 → 打完。**打完不要动**，等 20 秒看会不会自动进下半',
    '5) 下半：能打就打（看队伍是不是换成了第二队）；黑屏/卡住就等 30 秒，别退',
    '6) 下半打完看结算页（星数/剩余轮次）；然后退出挑战回世界',
    '7) 再进一次混沌回忆、再打第 1 层（验证退出后重新进不污染）',
    '测试完按 Enter 收工；中途想停也可以直接按 Enter。'
)

$CoreSteps = @(
    '1) 出生点：是不是和凝滞虚影（calyx）重叠、每次登录同一个位置；',
    '2) 战斗：走到 calyx 按 F → 选关卡 → 打进结算 → 再打一次；',
    '3) 「G 多层韧性」提示：按 G → 鼠标关闭指南窗口 → 重复，记录第几轮消失；',
    '4) 看指南/手机图标的小红点是否还在。'
)

# 服务端日志 → 事件。Name 会出现在时间线与截图文件名里。
# Shots 是“事件后第几秒各拍一张”。
$ChallengeRules = @(
    @{ Name = 'enter-scene';    Pattern = 'enter scene: entry=(\d+)';                        Shots = @() },
    @{ Name = 'tierce-start';   Pattern = 'start challenge tierce: id=(\d+)';                Shots = @(0) },
    @{ Name = 'start';          Pattern = 'start challenge: id=(\d+)';                       Shots = @(0) },
    @{ Name = 'battle-info';    Pattern = 'challenge battle info: id=(\d+) .*phase=(\d+)';   Shots = @(0, 4) },
    @{ Name = 'cast';           Pattern = 'scene cast skill -> challenge battle';            Shots = @() },
    @{ Name = 'result';         Pattern = 'challenge battle result: .*phase=(\d+)/(\d+) .*win=(\w+)'; Shots = @(0) },
    @{ Name = 'advance';        Pattern = 'challenge advance \((inplace|scene)\)';           Shots = @(0, 6, 14, 25) },
    @{ Name = 'advance-wait';   Pattern = 'challenge advance: waiting for client';           Shots = @(0) },
    @{ Name = 'settle';         Pattern = 'challenge settle:';                               Shots = @(0, 5) },
    @{ Name = 'leave';          Pattern = 'leave challenge:';                                Shots = @(0) },
    @{ Name = 'direct';         Pattern = 'challenge direct start';                          Shots = @(0) }
)

$CoreRules = @(
    @{ Name = 'cocoon';         Pattern = 'start cocoon: cocoon_id=(\d+)';                   Shots = @() },
    @{ Name = 'quick-cocoon';   Pattern = 'quick start cocoon: cocoon_id=(\d+)';             Shots = @() },
    @{ Name = 'battle-result';  Pattern = 'battle result: battle_id=(\d+)';                  Shots = @() },
    @{ Name = 'lineup';         Pattern = 'lineup replaced';                                 Shots = @() },
    @{ Name = 'avatar-path';    Pattern = 'avatar path changed';                             Shots = @() },
    @{ Name = 'skin';           Pattern = 'avatar skin changed';                             Shots = @() }
)

$Rules = if ($Scenario -eq 'challenge') { $ChallengeRules } else { $CoreRules }

# --------------------------------------------------------------- run state
# 采集用的共享状态。放在 preflight 之前，因为 -AnalyseOnly 也要用同一套分析。
$shots = New-Object System.Collections.ArrayList
$events = New-Object System.Collections.ArrayList
$pendingShots = New-Object System.Collections.ArrayList
$clientLines = New-Object System.Collections.Generic.List[string]
$clientExceptions = New-Object System.Collections.ArrayList
$packetLines = New-Object System.Collections.ArrayList
$shotIndex = 0
$settleAt = $null
$loginSeen = $false
$serverPid = 'n/a'
$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$ExceptionPattern = 'Exception|NullReference|_RefreshGroup|_OnGetCurBattleInfo|AdventureModule|battleArea|LuaException|Assertion'
$clientMirror = Join-Path $OutDir "$RunId.client.log"
$packetsMirror = Join-Path $OutDir "$RunId.packets.log"

# 当前阶段切换模式与代码版本（报告里要写清楚测的是哪一版）。
$advanceMode = 'unknown'
$configPath = Join-Path $Root 'config.toml'
if (Test-Path $configPath) {
    $configText = Get-Content $configPath -Raw -Encoding UTF8
    if ($configText -match '(?m)^\s*advance\s*=\s*"([^"]+)"') { $advanceMode = $Matches[1] }
}
$gitHead = 'unknown'
try { $gitHead = (git -C $Root rev-parse --short HEAD 2>$null) } catch { }

if ($AnalyseOnly) {
    # 不做任何启动/截图：把现成的三份日志整份灌进同一套分析流程。
    Write-Step "只分析已有日志：$(Split-Path -Leaf $serverOut)（scenario $Scenario）"
    $loginSeen = $true
    foreach ($line in (Get-Content $serverOut -Encoding UTF8)) {
        if (-not $line.Trim()) { continue }
        foreach ($rule in $Rules) {
            if ($line -match $rule.Pattern) {
                [void]$events.Add([pscustomobject]@{
                    Time = Get-Stamp $line; Name = $rule.Name; Line = $line.Trim(); Client = 0
                })
                break
            }
        }
    }
    if (Test-Path $PacketsLog) {
        foreach ($line in (Get-Content $PacketsLog -Encoding UTF8)) { [void]$packetLines.Add($line) }
    }
    if (Test-Path $PlayerLog) {
        foreach ($line in (Get-Content $PlayerLog -Encoding UTF8)) {
            $clientLines.Add($line)
            if ($line -match $ExceptionPattern) {
                $trim = $line.Trim()
                if ($trim -and -not $clientExceptions.Contains($trim)) { [void]$clientExceptions.Add($trim) }
            }
        }
    }
    Write-Step "事件 $($events.Count) 条 / 请求 $($packetLines.Count) 行 / 客户端 $($clientLines.Count) 行"
}

# ------------------------------------------------------------------- preflight
if (-not $AnalyseOnly) {
Write-Head "hsr-local · $Scenario 测试（client $Client）"

if ($NoGame) {
    Write-Host ''
    Write-Host "场景：$Scenario（客户端 $Client）" -ForegroundColor Cyan
    Write-Host ''
    foreach ($line in $(if ($Scenario -eq 'challenge') { $ChallengeSteps } else { $CoreSteps })) {
        Write-Host "  $line"
    }
    Write-Host ''
    Write-Host '启动命令（脚本负责服务端，代理/游戏可以自己起）：' -ForegroundColor Cyan
    Write-Host "  cd $Root"
    Write-Host "  .\.venv\Scripts\python.exe -m hsr --profile $Client"
    Write-Host "  cd $GameDir"
    Write-Host "  .\firefly-go-proxy.exe -e `"$GameExe`" -p 9978"
    Write-Host ''
    Write-Host "Player.log=$PlayerLog" -ForegroundColor DarkGray
    return
}

if (-not (Test-Path $Python)) { throw "python venv not found: $Python" }
if (-not (Test-Path (Join-Path $Root 'config.toml'))) { throw 'config.toml not found' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Test-PortFree 21000 'tcp')) { throw 'HTTP 21000 已被占用：先关掉旧服务端（或旧测试器）再跑。' }
if (-not (Test-PortFree 23301 'udp')) { throw 'UDP 23301 已被占用：先关掉旧服务端再跑。' }
$runningGame = Get-Process -Name StarRail -ErrorAction SilentlyContinue
if ($runningGame) {
    throw "检测到 StarRail.exe 还在运行 (PID $($runningGame.Id -join ','))：请先完全退出游戏。"
}

Write-Step "阶段切换模式 [endgame] advance = $advanceMode；代码 $gitHead"

if ($PatchPrefs) {
    Write-Step '预写客户端“指南/教程已读”PlayerPrefs（可选）…'
    & (Join-Path $PSScriptRoot 'fix_client_prefs.ps1')
}

# --------------------------------------------------------------- start server
$serverOut = Join-Path $OutDir "$RunId.server.log"
$serverErr = Join-Path $OutDir "$RunId.server.err.log"

Write-Step "启动服务端（client $Client，profile $Client）…"
$serverArgList = @('-m', 'hsr', '--profile', $Client) + $ServerArgs
$server = Start-Process -FilePath $Python -ArgumentList $serverArgList `
    -WorkingDirectory $Root -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput $serverOut -RedirectStandardError $serverErr

$serverReady = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    if ($server.HasExited) { break }
    if ((Test-Path $serverOut) -and (Select-String -Path $serverOut -Pattern 'dispatch listening' -Quiet)) {
        $serverReady = $true
        break
    }
}
if (-not $serverReady) {
    Write-Host (Get-Content $serverOut -Tail 30 -Encoding UTF8 -ErrorAction SilentlyContinue)
    Write-Host (Get-Content $serverErr -Tail 30 -Encoding UTF8 -ErrorAction SilentlyContinue)
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    throw '服务端没有起来，见上面日志。'
}
$serverPid = $server.Id
Write-Step "服务端已就绪 (PID $serverPid)：udp://127.0.0.1:23301  http://127.0.0.1:21000"

# 从服务端启动这一刻开始收集，别把上一次运行的内容算进来。
[void](Read-Chunk $serverOut)
if (Test-Path $PacketsLog) { $script:Cursors[$PacketsLog] = [long](Get-Item $PacketsLog).Length }
if (Test-Path $PlayerLog) { $script:Cursors[$PlayerLog] = [long](Get-Item $PlayerLog).Length }

# ------------------------------------------------------------------ launch
$proxy = $null
if ($NoLaunch) {
    Write-Step '按 -NoLaunch：请自行启动代理与游戏。参考命令：'
    Write-Host "    cd `"$GameDir`""
    Write-Host "    .\firefly-go-proxy.exe -e `"$GameExe`" -p 9978"
} else {
    if (-not (Test-Path $ProxyExe)) { throw "代理不存在：$ProxyExe（或用 -NoLaunch 手动启动）" }
    if (-not (Test-Path $GameExe)) { throw "游戏不存在：$GameExe" }
    Write-Step '启动 firefly 代理 + 游戏（UAC 弹窗请点“是”）。'
    $proxy = Start-Process -FilePath $ProxyExe `
        -ArgumentList @('-e', $GameExe, '-p', '9978') `
        -WorkingDirectory $GameDir -PassThru -WindowStyle Minimized
    Write-Step "代理 PID $($proxy.Id)；游戏启动后大约 30-45 秒进世界。"
}

# ---------------------------------------------------------------- wait login
Write-Step "等待客户端登录（最多 $LoginTimeoutSec 秒）…"
$loginSeen = $false
$deadline = (Get-Date).AddSeconds($LoginTimeoutSec)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 1
    $lines = Read-NewLines $PacketsLog
    foreach ($line in $lines) {
        if ($line -match 'PlayerLoginFinishCsReq|GetCurSceneInfoCsReq') { $loginSeen = $true }
    }
    if ($loginSeen) { break }
    if ($proxy -and $proxy.HasExited) { Write-Warn2 '代理已退出：可能 UAC 被拒绝，或代理启动失败。' }
}
if (-not $loginSeen) { Write-Warn2 '没有检测到登录包。检查代理/系统代理/游戏是否真的启动。' }

# ------------------------------------------------------------------ evidence
# （$clientMirror / $packetsMirror / 采集变量都在 preflight 之前就建好了）
if (-not $AnalyseOnly) {
    Set-Content -Path $clientMirror -Value '' -Encoding UTF8
    Set-Content -Path $packetsMirror -Value '' -Encoding UTF8
}

function Take-Shot([string]$name) {
    $script:shotIndex = $script:shotIndex + 1
    $path = Join-Path $OutDir ("$RunId-{0:d2}-{1}.png" -f $script:shotIndex, $name)
    try {
        Save-Screenshot $path
        [void]$shots.Add($path)
        Write-Host "    shot -> $(Split-Path -Leaf $path)" -ForegroundColor DarkGray
    } catch {
        Write-Warn2 "截图失败：$_"
    }
    return $path
}

function Queue-Shots([string]$name, [int[]]$offsets) {
    foreach ($offset in $offsets) {
        $shotName = if ($offset -gt 0) { "$name+$offset" } else { $name }
        [void]$pendingShots.Add([pscustomobject]@{
            Due  = (Get-Date).AddSeconds($offset)
            Name = $shotName
        })
    }
}

$ExceptionPattern = 'Exception|NullReference|_RefreshGroup|_OnGetCurBattleInfo|AdventureModule|battleArea|LuaException|Assertion'

# 主循环：1 秒一轮，扫三个文件 + 拍到期截图 + 看键盘。
Write-Head "开始收集（场景 $Scenario）"
Write-Host '现在轮到你在游戏里操作；脚本只记录和截图。' -ForegroundColor Cyan
Write-Host ''
if ($Scenario -eq 'challenge') {
    foreach ($line in $ChallengeSteps) { Write-Host "  $line" }
} else {
    foreach ($line in $CoreSteps) { Write-Host "  $line" }
}
Write-Host ''
Write-Host '随时按 Enter 结束并生成报告（-MaxMinutes 到点也会自动结束）。' -ForegroundColor Yellow
Write-Host ''

$stopwatch = [System.Diagnostics.Stopwatch]::StartNew()
$lastStatus = 0
$userDone = $false
$lastChallengeAt = Get-Date

while (-not $userDone) {
    # 1) 服务端日志 → 事件
    foreach ($line in (Read-NewLines $serverOut)) {
        if (-not $line.Trim()) { continue }
        foreach ($rule in $Rules) {
            if ($line -match $rule.Pattern) {
                $clientMark = $clientLines.Count
                [void]$events.Add([pscustomobject]@{
                    Time   = Get-Stamp $line
                    Name   = $rule.Name
                    Line   = $line.Trim()
                    Client = $clientMark
                })
                Write-Evt "$(Get-Stamp $line)  $($rule.Name)  $($line.Trim())"
                if ($rule.Shots.Count -gt 0) { Queue-Shots $rule.Name $rule.Shots }
                if ($rule.Name -eq 'settle') { $settleAt = Get-Date }
                if ($rule.Name -in @('start', 'tierce-start', 'battle-info', 'result', 'advance', 'leave', 'direct')) {
                    $lastChallengeAt = Get-Date
                }
                break
            }
        }
    }

    # 2) 客户端日志 → 镜像 + 异常实时提示
    $newClient = Read-NewLines $PlayerLog
    if ($newClient.Count -gt 0) {
        foreach ($line in $newClient) {
            $clientLines.Add($line)
            if ($line -match $ExceptionPattern) {
                $trim = $line.Trim()
                if ($trim -and -not $clientExceptions.Contains($trim)) {
                    [void]$clientExceptions.Add($trim)
                    Write-Warn2 "client: $trim"
                }
            }
        }
        Add-Content -Path $clientMirror -Value ($newClient -join "`r`n") -Encoding UTF8
    }

    # 3) packets.log → 本次运行的请求序列
    $newPackets = Read-NewLines $PacketsLog
    if ($newPackets.Count -gt 0) {
        foreach ($line in $newPackets) { [void]$packetLines.Add($line) }
        Add-Content -Path $packetsMirror -Value ($newPackets -join "`r`n") -Encoding UTF8
    }

    # 4) 到期截图
    if ($pendingShots.Count -gt 0) {
        $now = Get-Date
        $due = @($pendingShots | Where-Object { $_.Due -le $now })
        foreach ($item in $due) {
            [void](Take-Shot $item.Name)
            [void]$pendingShots.Remove($item)
        }
    }

    # 5) 键盘 / 超时 / 结算后自动收工
    try {
        if ([Console]::KeyAvailable) {
            $key = [Console]::ReadKey($true)
            if ($key.Key -eq 'Enter' -or $key.Key -eq 'Q' -or $key.Key -eq 'Escape') { $userDone = $true }
        }
    } catch { }
    if ($stopwatch.Elapsed.TotalMinutes -ge $MaxMinutes) {
        Write-Warn2 "到 -MaxMinutes=$MaxMinutes，自动收工。"
        $userDone = $true
    }
    if ($StopAfterSettle -and $settleAt) {
        if (((Get-Date) - $lastChallengeAt).TotalSeconds -ge 45) {
            Write-Step '结算后 45 秒没有新挑战包，自动收工。'
            $userDone = $true
        }
    }

    # 6) 存活提示（每 30 秒一次）
    if (($stopwatch.Elapsed.TotalSeconds - $lastStatus) -ge 30) {
        $lastStatus = $stopwatch.Elapsed.TotalSeconds
        Write-Host ("    …运行中 {0:N0}s，事件 {1} 条，截图 {2} 张（按 Enter 结束）" -f $stopwatch.Elapsed.TotalSeconds, $events.Count, $shots.Count) -ForegroundColor DarkGray
    }

    Start-Sleep -Milliseconds 500
}

} # <- end: -not $AnalyseOnly

# ------------------------------------------------------------------ analyse
Write-Step '汇总证据、生成报告…'

# 等最后一批日志落盘（只有实际跑过才有新内容；-AnalyseOnly 直接跳过）
if (-not $AnalyseOnly) {
    Start-Sleep -Seconds 1
    foreach ($line in (Read-NewLines $PlayerLog)) { $clientLines.Add($line) }
    foreach ($line in (Read-NewLines $PacketsLog)) { [void]$packetLines.Add($line) }
    foreach ($line in (Read-NewLines $serverOut)) {
        foreach ($rule in $Rules) {
            if ($line -match $rule.Pattern) {
                [void]$events.Add([pscustomobject]@{ Time = Get-Stamp $line; Name = $rule.Name; Line = $line.Trim(); Client = $clientLines.Count })
                break
            }
        }
    }
}

# --- 结论判定（挑战场景） -------------------------------------------------
function Find-Events([string]$name) {
    return @($events | Where-Object { $_.Name -eq $name })
}
function Get-Phase([string]$line) {
    # 两种日志格式都认：battle info 是 `phase=1`，result 是 `phase=1/2`。
    if ($line -match 'phase=(\d+)(/\d+)?') { return [int]$Matches[1] }
    return 0
}
function Get-Win([string]$line) {
    if ($line -match 'win=(\w+)') { return $Matches[1] }
    return ''
}

$verdicts = @()
$battleInfos = @(Find-Events 'battle-info')
$results = @(Find-Events 'result')
$advances = @(Find-Events 'advance')
$waits = @(Find-Events 'advance-wait')
$settles = @(Find-Events 'settle')
$leaves = @(Find-Events 'leave')
$starts = @(@(Find-Events 'start') + @(Find-Events 'tierce-start'))

$upperInfo = @($battleInfos | Where-Object { (Get-Phase $_.Line) -eq 1 })
$lowerInfo = @($battleInfos | Where-Object { (Get-Phase $_.Line) -ge 2 })
$upperWin = @($results | Where-Object { (Get-Phase $_.Line) -eq 1 -and (Get-Win $_.Line) -eq 'True' })
$lowerWin = @($results | Where-Object { (Get-Phase $_.Line) -ge 2 -and (Get-Win $_.Line) -eq 'True' })

$verdicts += [pscustomobject]@{
    Item  = '开始挑战'
    Value = $(if ($starts.Count) { "$($starts.Count) 次" } else { '未发生' })
    Ok    = ($starts.Count -gt 0)
}
$verdicts += [pscustomobject]@{ Item = '上半开打（battle info phase=1）'; Value = "$($upperInfo.Count) 次"; Ok = ($upperInfo.Count -gt 0) }
$verdicts += [pscustomobject]@{ Item = '上半胜利'; Value = "$($upperWin.Count) 次"; Ok = ($upperWin.Count -gt 0) }
$advanceText = if ($advances.Count) {
    "$($advances.Count) 次：" + (($advances | ForEach-Object { $_.Name + '@' + $_.Time }) -join ' , ')
} elseif ($waits.Count) {
    '服务端在等客户端（advance=client）'
} else {
    '未发生'
}
$verdicts += [pscustomobject]@{ Item = '阶段切换'; Value = $advanceText; Ok = ($advances.Count -gt 0) }
$verdicts += [pscustomobject]@{ Item = '下半开打（battle info phase>=2）'; Value = "$($lowerInfo.Count) 次"; Ok = ($lowerInfo.Count -gt 0) }
$verdicts += [pscustomobject]@{ Item = '下半胜利'; Value = "$($lowerWin.Count) 次"; Ok = ($lowerWin.Count -gt 0) }
$verdicts += [pscustomobject]@{ Item = '结算 notify'; Value = "$($settles.Count) 次"; Ok = ($settles.Count -gt 0) }
$verdicts += [pscustomobject]@{ Item = '退出挑战'; Value = "$($leaves.Count) 次"; Ok = ($leaves.Count -gt 0) }

# 上半是否被重打：同一 stage 出现两次 battle info
$upperStages = @($battleInfos | Where-Object { (Get-Phase $_.Line) -eq 1 } | ForEach-Object {
    if ($_.Line -match 'stage=(\d+)') { $Matches[1] }
})
$replayedUpper = ($upperStages.Count -gt 1 -and (@($upperStages | Select-Object -Unique)).Count -lt $upperStages.Count)

# 客户端在“下半战斗包之后”的反应：advance 时间点 → 结束
$advanceStamp = if ($advances.Count) { $advances[0].Time } else { '' }
$secondHalfPackets = @()
if ($advanceStamp) { $secondHalfPackets = @(Select-Window $packetLines $advanceStamp '') }
$quitAfterBattle = @($secondHalfPackets | Where-Object { $_ -match 'QuitBattleCsReq' })
$castAfterAdvance = @($secondHalfPackets | Where-Object { $_ -match 'SceneCastSkillCsReq' })

$reactionText = "cast $($castAfterAdvance.Count) 次 / QuitBattle $($quitAfterBattle.Count) 次"
if ($quitAfterBattle.Count) {
    $reactionText += "（首次 QuitBattle $(Get-Stamp $quitAfterBattle[0])）"
}
$verdicts += [pscustomobject]@{
    Item  = '下半的客户端反应'
    Value = $reactionText
    Ok    = ($quitAfterBattle.Count -eq 0)
}

# 客户端异常
$nreCount = @($clientExceptions | Where-Object { $_ -match 'NullReference' }).Count
$verdicts += [pscustomobject]@{ Item = '客户端异常行'; Value = "$($clientExceptions.Count) 种（其中 NRE $nreCount）"; Ok = ($clientExceptions.Count -eq 0) }

# --- 报告 -----------------------------------------------------------------
$report = Join-Path $OutDir "$RunId.report.md"
$sb = New-Object System.Text.StringBuilder
function Add-Line([string]$text = '') { [void]$sb.AppendLine($text) }

Add-Line "# hsr-local 测试报告：$RunId"
Add-Line ''
Add-Line "- 场景：**$Scenario**"
Add-Line "- 时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Add-Line "- 客户端：$Client（服务端 --profile $Client）"
Add-Line "- 阶段切换模式：``[endgame] advance = $advanceMode``；代码：``$gitHead``"
Add-Line "- 服务端 PID：$serverPid（``$([IO.Path]::GetFileName($serverOut))``）"
Add-Line "- 登录包：$(if ($loginSeen) { '已看到' } else { '未看到' })"
Add-Line "- 运行时长：$([int]$stopwatch.Elapsed.TotalSeconds) 秒"
Add-Line "- 截图：$($shots.Count) 张"
Add-Line "- 客户端日志镜像：``$([IO.Path]::GetFileName($clientMirror))``（$(if (Test-Path $clientMirror) { (Get-Item $clientMirror).Length } else { 0 }) 字节）"
Add-Line "- 请求日志镜像：``$([IO.Path]::GetFileName($packetsMirror))``"
Add-Line ''

Add-Line '## A. 结论摘要'
Add-Line ''
Add-Line '| 检查项 | 结果 | 判定 |'
Add-Line '|---|---|---|'
foreach ($row in $verdicts) {
    Add-Line "| $($row.Item) | $($row.Value) | $(if ($row.Ok) { '✅' } else { '❌' }) |"
}
Add-Line ''
if ($replayedUpper) { Add-Line '> ⚠️ 上半被打了不止一次（同一 stage 出现多条 battle info）：服务端可能没推进阶段，或客户端重打了上半。' ; Add-Line '' }

Add-Line '## B. 事件时间线（服务端日志匹配行）'
Add-Line ''
Add-Line '```'
foreach ($event in $events) {
    $text = $event.Line
    if ($event.Time -and $text.StartsWith($event.Time)) { $text = $text.Substring($event.Time.Length).Trim() }
    Add-Line ("{0}  {1,-14} {2}" -f $event.Time, $event.Name, $text)
}
Add-Line '```'
Add-Line ''

Add-Line '## C. 挑战请求序列（packets.log）'
Add-Line ''
Add-Line '### C1. 从「开始挑战」到「退出挑战」'
Add-Line ''
$startStamp = if ($starts.Count) { $starts[0].Time } else { '' }
$endStamp = if ($leaves.Count) { $leaves[$leaves.Count - 1].Time } else { '' }
$challengePackets = Select-Window $packetLines $startStamp $endStamp
Add-Line '```'
foreach ($line in ($challengePackets | Select-Object -First 200)) { Add-Line $line.Trim() }
if ($challengePackets.Count -gt 200) { Add-Line "…（共 $($challengePackets.Count) 行，完整见 packets 镜像）" }
Add-Line '```'
Add-Line ''
Add-Line '### C2. 下半窗口（advance 之后）：客户端对第二次战斗包的反应'
Add-Line ''
Add-Line '```'
foreach ($line in ($secondHalfPackets | Select-Object -First 160)) { Add-Line $line.Trim() }
Add-Line '```'
Add-Line ''

Add-Line '## D. 客户端日志：下半窗口'
Add-Line ''
# 正常跑：事件发生时记下了客户端日志的行号，直接切窗口。
# -AnalyseOnly：客户端日志没有时间戳、也无法还原行号，退化成「含关键字的末段」。
$hasMarks = ($advances.Count -gt 0 -and [int]$advances[0].Client -gt 0)
if ($hasMarks) {
    $markFrom = [int]$advances[0].Client
    $markTo = if ($settles.Count) { [int]$settles[0].Client } elseif ($leaves.Count) { [int]$leaves[0].Client } else { $clientLines.Count }
    if ($markTo -le $markFrom) { $markTo = [Math]::Min($clientLines.Count, $markFrom + 400) }
    $from = [Math]::Max(0, $markFrom - 10)
    $to = [Math]::Min($clientLines.Count - 1, $markTo + 20)
    Add-Line "行范围：$from..$to（共 $($clientLines.Count) 行；advance $markFrom / settle $markTo）"
    Add-Line ''
    Add-Line '```'
    for ($i = $from; $i -le $to; $i++) {
        $line = $clientLines[$i]
        if ($line -match 'Exception|NullReference|Error|error|FAILED|Warning|warning|Battle|battle|Challenge|challenge|Scene|scene|Quit|quit|Enter|enter|Avatar|avatar') {
            Add-Line $line.Trim()
        }
    }
    Add-Line '```'
} else {
    Add-Line '（没有行号标记：取客户端日志里含关键字/报错的末段 200 行）'
    Add-Line ''
    Add-Line '```'
    $picked = @($clientLines | Where-Object {
        $_ -match 'Exception|NullReference|Error|error|FAILED|Warning|warning|Battle|battle|Challenge|challenge|Quit|quit|Scene|scene'
    })
    foreach ($line in ($picked | Select-Object -Last 200)) { Add-Line $line.Trim() }
    Add-Line '```'
}
Add-Line ''
Add-Line '（只保留含关键字/报错的行；完整原文在客户端日志镜像里。）'
Add-Line ''

Add-Line '## E. 客户端异常汇总（本次运行，去重）'
Add-Line ''
if ($clientExceptions.Count) {
    foreach ($line in ($clientExceptions | Select-Object -First 40)) { Add-Line "- ``$line``" }
} else {
    Add-Line '（无）'
}
Add-Line ''

Add-Line '## F. 截图'
Add-Line ''
if ($shots.Count) {
    foreach ($shot in $shots) { Add-Line "- ``$(Split-Path -Leaf $shot)``" }
} else {
    Add-Line '（无）'
}
Add-Line ''

Add-Line '## G. 服务端日志尾部（40 行，查意外错误用）'
Add-Line ''
Add-Line '```'
foreach ($line in @(Get-Content $serverOut -Tail 40 -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    Add-Line $line
}
Add-Line '```'
Add-Line ''

if ($Scenario -eq 'core') {
    Add-Line '## G. 关键请求计数（core）'
    Add-Line ''
    $keys = @(
        @{ Name = '登录完成'; Pattern = 'PlayerLoginFinishCsReq' },
        @{ Name = '场景信息'; Pattern = 'GetCurSceneInfoCsReq' },
        @{ Name = '开始凝滞虚影'; Pattern = 'StartCocoonStageCsReq' },
        @{ Name = '战斗结算'; Pattern = 'PVEBattleResultCsReq' },
        @{ Name = '阵容替换'; Pattern = 'ReplaceLineupCsReq' },
        @{ Name = '命途切换'; Pattern = 'SetAvatarPathCsReq' },
        @{ Name = '换肤'; Pattern = 'DressAvatarSkinCsReq' },
        @{ Name = '未知命令'; Pattern = 'unhandled command:' }
    )
    Add-Line '| 项目 | 次数 |'
    Add-Line '|---|---|'
    foreach ($key in $keys) {
        Add-Line "| $($key.Name) | $(@($packetLines | Where-Object { $_ -match [regex]::Escape($key.Pattern) }).Count) |"
    }
    Add-Line ''
}

Add-Line '## H. 手动确认（你来填）'
Add-Line ''
Add-Line '- 上半战斗画面：正常 / 黑屏 / 卡住'
Add-Line '- 上半打完有没有自动进下半：是 / 否（黑屏 / 停在结算）'
Add-Line '- 下半队伍：第二队 / 还是上半那队'
Add-Line '- 下半战斗画面：正常 / 黑屏 / 卡住 / 没进去'
Add-Line '- 下半结算页：出现了 / 没出现'
Add-Line '- 退出后：回到世界 / 卡在车厢或加载条'
Add-Line '- 备注：'

$sb.ToString() | Set-Content -Path $report -Encoding UTF8

Write-Head '完成'
Write-Host "报告：$report" -ForegroundColor Green
Write-Host "客户端日志：$clientMirror" -ForegroundColor Green
Write-Host "请求日志：$packetsMirror" -ForegroundColor Green
Write-Host "截图：$($shots.Count) 张（$OutDir）" -ForegroundColor Green
Write-Host ''
Write-Host '把这三样发回来就够：report.md + *.client.log + *.packets.log（截图按需）。' -ForegroundColor Yellow
Write-Host "只想重新分析已落盘的日志：-AnalyseOnly <本次>.server.log" -ForegroundColor DarkGray
Write-Host ''

# ------------------------------------------------------------------ cleanup
if (-not $AnalyseOnly) {
    if (-not $KeepProxy) {
        try { Set-SystemProxy 0; Write-Step '系统代理已关闭（ProxyEnable=0）' } catch { Write-Warn2 "还原系统代理失败：$_" }
    }
    if ($proxy -and -not $proxy.HasExited) {
        Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
        Write-Step '代理已停止'
    }
    if (-not $KeepServer -and $server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        Write-Step '服务端已停止'
    }
}
