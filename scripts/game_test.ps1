#requires -Version 5.1
<#
.SYNOPSIS
    hsr-local 手动测试器：起服务端 → 起代理/游戏 → 定时截图 → 收日志。

.DESCRIPTION
    这是给“真人操作游戏、脚本只负责环境和证据”的版本（旧的 exp_popup.ps1 是
    P0 实验专用的，可以不再用）。

    脚本会：
      1. 检查端口/旧游戏进程；
      2. （可选 -PatchPrefs）预写客户端“指南/教程已读”PlayerPrefs；
      3. 用默认 config.toml 启动服务端；
      4. 启动 firefly 代理并以管理员拉起游戏（UAC 点“是”）；
      5. 等登录包，在 +10 / +40 / +90 秒自动截图（可 -ShotPlan 改）；
      6. 暂停，等你手动测试（开战、按 G 关指南、看红点……）；
      7. 回车后收集 packets.log / Player.log，写报告；
      8. 停服务端/代理，还原系统代理。

    测试内容不需要脚本点鼠标；指南窗口需要鼠标关闭，由你来关。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1

.EXAMPLE
    # 已经自己启动了游戏/代理，只让脚本起服务端并抓证据
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1 -NoLaunch

.EXAMPLE
    # 只看测试步骤，不启动任何东西
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\game_test.ps1 -NoGame
#>
param(
    [int[]]$ShotPlan = @(10, 40, 90),
    [int]$LoginTimeoutSec = 240,

    [switch]$NoLaunch,      # 不启动代理/游戏，你自己启动
    [switch]$NoGame,        # 只打印测试步骤
    [switch]$KeepServer,    # 结束时保留服务端
    [switch]$KeepProxy,     # 结束时不动系统代理
    [switch]$PatchPrefs,    # 可选：预写客户端“已读”PlayerPrefs（客户端登录后会覆盖）

    # 客户端版本：决定 exe、Player.log 与服务端 --profile。
    [ValidateSet('4.6.54', '4.7.0')]
    [string]$Client = '4.6.54',

    [string]$GameDir = 'E:\SiFu\HSRSiFu',
    # 留空 = 按 -Client 选；显式传入时以传入值为准。
    [string]$GameExe = '',
    [string]$ProxyExe = 'E:\SiFu\HSRSiFu\firefly-go-proxy.exe',
    # 追加给服务端的参数（如 --strict）。--profile 已按 -Client 自动加。
    [string[]]$ServerArgs = @()
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

function Write-Head($text) {
    Write-Host ''
    Write-Host ('=' * 68) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 68) -ForegroundColor Cyan
}

function Write-Step($text) {
    Write-Host "[test] $text" -ForegroundColor Yellow
}

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

function Read-NewLog([string]$path, [long]$offset) {
    if (-not (Test-Path $path)) { return '' }
    $len = (Get-Item $path).Length
    if ($len -lt $offset) { $offset = 0 }
    $fs = [System.IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        $fs.Seek($offset, 'Begin') | Out-Null
        $reader = New-Object System.IO.StreamReader($fs)
        return $reader.ReadToEnd()
    } finally {
        $fs.Dispose()
    }
}

function Set-SystemProxy([int]$enable) {
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    Set-ItemProperty $key -Name ProxyEnable -Value $enable
}

# --------------------------------------------------------------- test plan
Write-Head "hsr-local · 手动游戏测试"

if ($NoGame) {
    Write-Host @'
测试步骤（脚本不启动任何东西）：

1. 启动服务端（--profile 选客户端版本）：
     cd E:\LCXS\run\hsr-local
     .\.venv\Scripts\python.exe -m hsr --profile 4.6.54
     .\.venv\Scripts\python.exe -m hsr --profile 4.7.0   # CN 4.7.0，需要 features.local_cdn

2. 启动代理 + 游戏（UAC 点“是”）：
     cd E:\SiFu\HSRSiFu
     .\firefly-go-proxy.exe -e "E:\SiFu\HSRSiFu\StarRail_4.6.54\StarRail.exe" -p 9978
     .\firefly-go-proxy.exe -e "E:\SiFu\HSRSiFu\StarRail_CN_4.7.0\StarRail.exe" -p 9978

3. 登录后检查：
   a) 出生点是否和凝滞虚影重合（data/world.json: 31440/192820/433790；persist_position=false）
   b) 走到 calyx 按 F → 选关卡 → 战斗 → 结算 → 再打一次
   c) 进世界后应没有「G 多层韧性」提示 /「欢愉命途」弹窗；
      报告里 UnlockTutorialGuideCsReq 应为 0
   d) 看指南/手机图标红点是否还在（历史红点可忽略）

4. 结束：关游戏 → Ctrl+C 服务端 → 关代理 → 还原系统代理：
     Set-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings' -Name ProxyEnable -Value 0
'@
    Write-Host ''
    Write-Host "本次 -Client=$Client：exe=$GameExe" -ForegroundColor Cyan
    Write-Host "Player.log=$PlayerLog" -ForegroundColor Cyan
    return
}

# ------------------------------------------------------------------ preflight
if (-not (Test-Path $Python)) { throw "python venv not found: $Python" }
if (-not (Test-Path (Join-Path $Root 'config.toml'))) { throw 'config.toml not found' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

if (-not (Test-PortFree 21000 'tcp')) {
    throw 'HTTP 21000 已被占用：先关掉旧服务端（或旧测试器）再跑。'
}
if (-not (Test-PortFree 23301 'udp')) {
    throw 'UDP 23301 已被占用：先关掉旧服务端再跑。'
}
$runningGame = Get-Process -Name StarRail -ErrorAction SilentlyContinue
if ($runningGame) {
    throw "检测到 StarRail.exe 还在运行 (PID $($runningGame.Id -join ','))：请先完全退出游戏。"
}

# ------------------------------------------------------------ client prefs
# 客户端在登录时会重写这两个 PlayerPrefs（FinishedTutorials 清空、
# UnlockedTutorialGuides 换成它自己的 1804 条），所以默认不写；保留开关只用于
# 对照实验。真正消除「G 多层韧性」提示的是 GetTutorialGuideScRsp 里指南组的
# type 必须为 0（见 data/tutorials.json 与 scripts/export_tutorials.py）。
if ($PatchPrefs) {
    Write-Step '预写客户端“指南/教程已读”PlayerPrefs（可选）…'
    & (Join-Path $PSScriptRoot 'fix_client_prefs.ps1')
}

# --------------------------------------------------------------- start server
$serverOut = Join-Path $OutDir "$RunId.server.log"
$serverErr = Join-Path $OutDir "$RunId.server.err.log"
$packetsOffset = if (Test-Path $PacketsLog) { (Get-Item $PacketsLog).Length } else { 0 }

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
Write-Step "服务端已就绪 (PID $($server.Id))：udp://127.0.0.1:23301  http://127.0.0.1:21000"

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
    Start-Sleep -Seconds 2
    $new = Read-NewLog $PacketsLog $packetsOffset
    if ($new -match 'PlayerLoginFinishCsReq|GetCurSceneInfoCsReq') {
        $loginSeen = $true
        break
    }
    if ($proxy -and $proxy.HasExited) {
        Write-Host '    代理已退出：可能 UAC 被拒绝，或代理启动失败。' -ForegroundColor Red
    }
}
if (-not $loginSeen) {
    Write-Host '没有检测到登录包。检查代理/系统代理/游戏是否真的启动。' -ForegroundColor Red
}

# ----------------------------------------------------------------- shots
$shots = @()
if ($loginSeen) {
    $plan = @($ShotPlan | Where-Object { $_ -gt 0 } | Sort-Object)
    if (-not $plan) { $plan = @(10, 40, 90) }
    Write-Step "登录完成。截图计划：+$($plan -join ' / +') 秒（期间你可以正常操作游戏）。"
    $previous = 0
    $index = 0
    foreach ($second in $plan) {
        $wait = $second - $previous
        if ($wait -gt 0) { Start-Sleep -Seconds $wait }
        $previous = $second
        $index++
        $shot = Join-Path $OutDir "$RunId-$index.png"
        Save-Screenshot $shot
        $shots += $shot
        Write-Host "    第 $index 张（+$second 秒）：$shot" -ForegroundColor DarkGray
    }
}

# --------------------------------------------------------------- manual test
Write-Head "轮到你操作"
Write-Host '请按下面顺序测（截图已经拍完，脚本不会碰鼠标/键盘）：' -ForegroundColor Cyan
Write-Host '  1) 出生点：是不是和凝滞虚影（calyx）重叠、每次登录同一个位置；'
Write-Host '  2) 战斗：走到 calyx 按 F → 选关卡 → 打进结算 → 再打一次；'
Write-Host '  3) 「G 多层韧性」提示：按 G → 用鼠标关闭指南窗口 → 重复，记录第几轮消失；'
Write-Host '  4) 看指南/手机图标的小红点是否还在。'
Write-Host ''
Read-Host '测试完成后按 Enter：收集日志、生成报告并结束' | Out-Null

# ------------------------------------------------------------------ analyse
Write-Step '汇总日志…'
$newPackets = Read-NewLog $PacketsLog $packetsOffset
$packetLines = @($newPackets -split "`r?`n" | Where-Object { $_ -match '->|unhandled|connected|listening|GET /|POST /' })

$keys = @(
    @{ Name = '登录完成'; Pattern = 'PlayerLoginFinishCsReq' },
    @{ Name = '场景信息'; Pattern = 'GetCurSceneInfoCsReq' },
    @{ Name = '开始凝滞虚影'; Pattern = 'StartCocoonStageCsReq' },
    @{ Name = '战斗结算'; Pattern = 'PVEBattleResultCsReq' },
    @{ Name = '快速开始'; Pattern = 'QuickStartCocoonStageCsReq' },
    @{ Name = '阵容替换'; Pattern = 'ReplaceLineupCsReq' },
    @{ Name = '命途切换'; Pattern = 'SetAvatarPathCsReq' },
    @{ Name = '批量命途切换'; Pattern = 'SetMultipleAvatarPathsCsReq' },
    @{ Name = '换肤'; Pattern = 'DressAvatarSkinCsReq' },
    @{ Name = '脱皮肤'; Pattern = 'TakeOffAvatarSkinCsReq' },
    @{ Name = '解锁指南'; Pattern = 'UnlockTutorialGuideCsReq' },
    @{ Name = '完成指南'; Pattern = 'FinishTutorialGuideCsReq' },
    @{ Name = '解锁教程'; Pattern = 'UnlockTutorialCsReq' },
    @{ Name = '完成教程'; Pattern = 'FinishTutorialCsReq' },
    @{ Name = '未知命令'; Pattern = 'unhandled command:' },
    @{ Name = 'ServerPrefs 写入'; Pattern = 'UpdateServerPrefsCsReq' }
)
$counts = foreach ($key in $keys) {
    [pscustomobject]@{
        Item  = $key.Name
        Count = @($packetLines | Where-Object { $_ -match [regex]::Escape($key.Pattern) }).Count
    }
}
$byItem = @{}
foreach ($row in $counts) { $byItem[$row.Item] = $row.Count }
$retryOk = ($byItem['开始凝滞虚影'] -ge 2 -and $byItem['战斗结算'] -ge 2)
$pathOk = ($byItem['命途切换'] -ge 1 -or $byItem['批量命途切换'] -ge 1)
$lineupOk = ($byItem['阵容替换'] -ge 1)

$playerTail = if (Test-Path $PlayerLog) {
    @(Get-Content $PlayerLog -Tail 500)
} else { @() }
$playerLogTime = if (Test-Path $PlayerLog) {
    (Get-Item $PlayerLog).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
} else { '不存在' }
$exceptions = @($playerTail | Select-String -Pattern 'NullReferenceException|Exception:|Exception ' |
    ForEach-Object { $_.Line.Trim() } | Select-Object -Unique -First 20)

$worldLines = @(Get-Content (Join-Path $Root 'data\world.json') -Encoding UTF8)
$shotList = @($shots | ForEach-Object { Split-Path -Leaf $_ })

$report = Join-Path $OutDir "$RunId.report.md"
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("# hsr-local 手动测试报告：$RunId")
[void]$sb.AppendLine('')
[void]$sb.AppendLine("- 时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$sb.AppendLine("- 客户端：$Client（服务端 --profile $Client）")
[void]$sb.AppendLine("- 服务端 PID：$($server.Id)（日志 ``$([IO.Path]::GetFileName($serverOut))``）")
[void]$sb.AppendLine("- 登录包：$(if ($loginSeen) { '已看到' } else { '未看到' })")
[void]$sb.AppendLine("- 截图：$(if ($shotList.Count) { ($shotList -join ', ') } else { '无' })")
[void]$sb.AppendLine("- Player.log 最后写入：$playerLogTime")
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 出生点 / calyx（data/world.json）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```')
foreach ($line in $worldLines) { [void]$sb.AppendLine($line) }
[void]$sb.AppendLine('```')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 关键请求计数')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('| 项目 | 次数 |')
[void]$sb.AppendLine('|---|---|')
foreach ($row in $counts) { [void]$sb.AppendLine("| $($row.Item) | $($row.Count) |") }
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 闭环检查')
[void]$sb.AppendLine('')
[void]$sb.AppendLine("- 再来一次（至少 2 次 Start/Result）：$(if ($retryOk) { '通过' } else { '未验证' })")
[void]$sb.AppendLine("- 换队：$(if ($lineupOk) { '已发生' } else { '未发生' })")
[void]$sb.AppendLine("- 命途切换：$(if ($pathOk) { '已发生' } else { '未发生' })")
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## Player.log 异常（末 500 行）')
[void]$sb.AppendLine('')
if ($exceptions.Count) { foreach ($e in $exceptions) { [void]$sb.AppendLine("- ``$e``") } }
else { [void]$sb.AppendLine('（无）') }
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 服务端日志（stdout 尾部）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```')
foreach ($line in @(Get-Content $serverOut -Tail 30 -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    [void]$sb.AppendLine($line)
}
[void]$sb.AppendLine('```')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## packets.log 片段（本次运行）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```')
foreach ($line in @($packetLines | Select-Object -Last 80)) { [void]$sb.AppendLine($line) }
[void]$sb.AppendLine('```')
$sb.ToString() | Set-Content -Path $report -Encoding UTF8

Write-Head "完成"
Write-Host "报告：$report" -ForegroundColor Green
Write-Host "截图：$(if ($shots.Count) { $shots -join ' , ' } else { '无' })" -ForegroundColor Green
Write-Host ''
Write-Host '把 report.md 和三张截图发回来即可。' -ForegroundColor Yellow
Write-Host ''
Write-Host '（脚本会停服务端/代理；如果游戏还开着，直接关掉即可。）' -ForegroundColor DarkGray

# ------------------------------------------------------------------ cleanup
if (-not $KeepProxy) {
    try { Set-SystemProxy 0; Write-Step '系统代理已关闭（ProxyEnable=0）' } catch { Write-Host "还原系统代理失败：$_" -ForegroundColor Red }
}
if ($proxy -and -not $proxy.HasExited) {
    Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
    Write-Step '代理已停止'
}
if (-not $KeepServer -and $server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    Write-Step '服务端已停止'
}
