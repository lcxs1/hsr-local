#requires -Version 5.1
<#
.SYNOPSIS
    P0「位面规则」弹窗 真机 A/B 测试器（一条命令跑完一整轮）。

.DESCRIPTION
    流程：
      1. 用 scripts/make_exp_config.py 生成一份只启用指定实验的临时 config
      2. 启动服务端（后台，日志写 logs\exp\）
      3. 启动 firefly 代理 + 游戏（或提示你手动启动）
      4. 等客户端发 PlayerLoginFinishCsReq（= 登录完成）
      5. 延迟若干秒后自动截屏（弹窗出现后无需人工操作）
      6. 汇总 packets.log / Player.log，写一份 markdown 报告
      7. 回车结束：停服务端、还原系统代理

    需要人工做的只有两件事：
      * UAC 弹窗时点“是”（firefly 需要管理员权限启动游戏）
      * 测试结束后回窗口按 Enter

.EXAMPLE
    # 推荐第一轮：登录态全关，只验证 SDK 公告开关修复
    .\scripts\exp_popup.ps1 -Experiment baseline

.EXAMPLE
    # 仍然弹窗时：让 SDK 的 getConfig 直接 404（pearl-sr 的做法）再试
    .\scripts\exp_popup.ps1 -Experiment sdk404

.EXAMPLE
    # 已经自己启动了游戏/代理，只想让脚本起服务端并抓证据
    .\scripts\exp_popup.ps1 -Experiment activity -NoLaunch

.EXAMPLE
    # 只生成配置、不启动任何东西（给手动测试用）
    .\scripts\exp_popup.ps1 -Experiment baseline -NoGame
#>
param(
    [ValidateSet('baseline', 'sdk404', 'mission', 'quest', 'activity', 'activity_active',
        'basicinfo_omit', 'basicinfo_nested', 'core', 'all')]
    [string]$Experiment = 'baseline',

    # 可选覆盖（留空则用 preset 自带的）
    [ValidateSet('', 'off', 'ended', 'active')]
    [string]$Activity = '',

    [ValidateSet('', 'keep', 'omit', 'nested')]
    [string]$BasicInfo = '',

    [string]$GameDir = 'E:\SiFu\HSRSiFu',
    [string]$GameExe = 'E:\SiFu\HSRSiFu\StarRail_4.6.54\StarRail.exe',
    [string]$ProxyExe = 'E:\SiFu\HSRSiFu\firefly-go-proxy.exe',

    [switch]$NoLaunch,      # 不自动启动代理/游戏（你自己启动）
    [switch]$NoGame,        # 只生成配置并退出
    [switch]$KeepProxy,     # 结束时不要动系统代理
    [switch]$KeepSdkCache,  # 不清理 SDK 缓存（默认会清，见下）
    [switch]$NoCalyx,       # 测试用：把凝滞虚影 group/prop 从场景里去掉
    [switch]$SkipClientPrefs, # 不写客户端教程/指南已读状态

    [int]$LoginTimeoutSec = 240,
    # 登录完成后第几秒截图（可传 -ShotPlan 10,45,90,150 做长时间观察）
    [int[]]$ShotPlan = @(10, 25, 40)
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
$ExpDir = Join-Path $Root 'logs\exp'
$PacketsLog = Join-Path $Root 'logs\packets.log'
$PlayerLog = Join-Path $env:USERPROFILE 'AppData\LocalLow\Cognosphere\Star Rail\Player.log'
$RunId = '{0}-{1}' -f $Experiment, (Get-Date -Format 'yyyyMMdd-HHmmss')

function Write-Head($text) {
    Write-Host ''
    Write-Host ('=' * 68) -ForegroundColor Cyan
    Write-Host "  $text" -ForegroundColor Cyan
    Write-Host ('=' * 68) -ForegroundColor Cyan
}

function Write-Step($text) {
    Write-Host "[exp] $text" -ForegroundColor Yellow
}

function Clear-SdkAnnounceCache {
    # The SDK caches its config/announcement URL in HKCU and otherwise keeps
    # using the official beta announcement page even when getConfig changes.
    $key = 'HKCU:\Software\Cognosphere\Star Rail'
    if (-not (Test-Path $key)) { return }
    $item = Get-Item $key
    foreach ($name in $item.GetValueNames()) {
        if ($name -match '^(MIHOYOSDK_CONFIG_MODEL_NAME|MIHOYOSDK_ANNOUNCE_URL)') {
            Remove-ItemProperty -Path $key -Name $name -ErrorAction SilentlyContinue
            Write-Host "    cleared cached $name" -ForegroundColor DarkGray
        }
    }
}

function Get-SdkAnnounceUrl {
    $key = 'HKCU:\Software\Cognosphere\Star Rail'
    $item = Get-Item $key -ErrorAction SilentlyContinue
    if (-not $item) { return '(no key)' }
    foreach ($name in $item.GetValueNames()) {
        if ($name -match '^MIHOYOSDK_ANNOUNCE_URL') {
            $value = $item.GetValue($name)
            if ($value -is [byte[]]) {
                return [System.Text.Encoding]::UTF8.GetString($value).Trim([char]0)
            }
            return [string]$value
        }
    }
    return '(none)'
}

function Get-GamePrefs {
    # Snapshot the game's Unity PlayerPrefs (HKCU registry) as name -> string.
    $key = 'HKCU:\Software\Cognosphere\Star Rail'
    $map = @{}
    $item = Get-Item $key -ErrorAction SilentlyContinue
    if ($item) {
        foreach ($name in $item.GetValueNames()) {
            $value = $item.GetValue($name)
            if ($value -is [byte[]]) {
                $map[$name] = [Convert]::ToBase64String($value)
            } else {
                $map[$name] = [string]$value
            }
        }
    }
    return $map
}

function Compare-GamePrefs($before, $after) {
    $names = @(@($before.Keys) + @($after.Keys) | Sort-Object -Unique)
    $changed = @()
    foreach ($name in $names) {
        $old = $before[$name]
        $new = $after[$name]
        if ("$old" -ne "$new") {
            $changed += [pscustomobject]@{ Name = $name; Before = $old; After = $new }
        }
    }
    return $changed
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
    if (-not ('ExpDpi' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class ExpDpi {
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
'@
    }
    [ExpDpi]::SetProcessDPIAware() | Out-Null
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
    if ($len -lt $offset) { $offset = 0 }   # log rotated / truncated
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

# ------------------------------------------------------------------ pre-flight
if (-not (Test-Path $Python)) { throw "python venv not found: $Python" }
if (-not (Test-Path (Join-Path $Root 'config.toml'))) { throw 'config.toml not found' }
New-Item -ItemType Directory -Force -Path $ExpDir | Out-Null

Write-Head "hsr-local · P0 位面规则弹窗 · experiment=$Experiment"

if (-not $NoGame) {
    if (-not (Test-PortFree 21000 'tcp')) {
        throw 'HTTP 21000 已被占用：先关掉上一轮的服务端（或这个测试器），再重跑。'
    }
    if (-not (Test-PortFree 23301 'udp')) {
        throw 'UDP 23301 已被占用：先关掉上一轮的服务端，再重跑。'
    }
    $runningGame = Get-Process -Name StarRail -ErrorAction SilentlyContinue
    if ($runningGame) {
        throw "检测到 StarRail.exe 还在运行 (PID $($runningGame.Id -join ','))：请先完全退出游戏，否则会复用旧会话。"
    }
}

# --------------------------------------------------------------- config + server
$cfgPath = Join-Path $ExpDir "$RunId.toml"
$mkArgs = @('scripts\make_exp_config.py', '--preset', $Experiment, '--out', $cfgPath)
if ($Activity) { $mkArgs += @('--activity', $Activity) }
if ($BasicInfo) { $mkArgs += @('--basic-info', $BasicInfo) }
if ($NoCalyx) { $mkArgs += '--no-calyx' }
Write-Step "生成实验配置：$cfgPath"
& $Python @mkArgs | ForEach-Object { Write-Host "    $_" }

if ($NoGame) {
    Write-Host ''
    Write-Host "配置已生成。手动启动服务端：" -ForegroundColor Green
    Write-Host "  .\.venv\Scripts\python.exe -m hsr --config `"$cfgPath`""
    Write-Host ''
    Write-Host '弹出位面规则面板时截图，并把 logs\packets.log 尾部发回来。'
    return
}

$serverOut = Join-Path $ExpDir "$RunId.server.log"
$serverErr = Join-Path $ExpDir "$RunId.server.err.log"
$packetsOffset = if (Test-Path $PacketsLog) { (Get-Item $PacketsLog).Length } else { 0 }

Write-Step '启动服务端…'
$server = Start-Process -FilePath $Python `
    -ArgumentList @('-m', 'hsr', '--config', $cfgPath) `
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
    Write-Host (Get-Content $serverOut -Tail 30 -ErrorAction SilentlyContinue)
    Write-Host (Get-Content $serverErr -Tail 30 -ErrorAction SilentlyContinue)
    if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force }
    throw '服务端没有起来，见上面日志。'
}
Write-Step "服务端已就绪 (PID $($server.Id))：udp://127.0.0.1:23301  http://127.0.0.1:21000"

# ------------------------------------------------------------------- launch game
$proxy = $null
if (-not $KeepSdkCache) {
    Write-Step '清理 SDK 公告缓存（MIHOYOSDK_ANNOUNCE_URL / CONFIG_MODEL_NAME）…'
    Clear-SdkAnnounceCache
}
if (-not $SkipClientPrefs) {
    Write-Step '写入客户端教程/指南已读状态（消除指南红点/提示）…'
    try {
        & (Join-Path $Root 'scripts\fix_client_prefs.ps1')
        $clientPrefsOk = $true
    } catch {
        Write-Host "    客户端 PlayerPrefs 写入失败：$_" -ForegroundColor Red
        Write-Host '    游戏没关时可能写不进去；可先关游戏再手动跑 scripts\fix_client_prefs.ps1。' -ForegroundColor DarkYellow
        $clientPrefsOk = $false
    }
} else {
    $clientPrefsOk = $false
}
$prefsBefore = Get-GamePrefs
if ($NoLaunch) {
    Write-Step '按 -NoLaunch：请自行启动代理与游戏。参考命令：'
    Write-Host "    cd `"$GameDir`""
    Write-Host "    .\firefly-go-proxy.exe -e `"$GameExe`" -p 9978"
} else {
    if (-not (Test-Path $ProxyExe)) { throw "代理不存在：$ProxyExe（或用 -NoLaunch 手动启动）" }
    if (-not (Test-Path $GameExe)) { throw "游戏不存在：$GameExe" }
    Write-Step '启动 firefly 代理 + 游戏（UAC 弹窗请点“是”）。代理窗口会最小化，不要关它。'
    $proxy = Start-Process -FilePath $ProxyExe `
        -ArgumentList @('-e', $GameExe, '-p', '9978') `
        -WorkingDirectory $GameDir -PassThru -WindowStyle Minimized
    Write-Step "代理 PID $($proxy.Id)；游戏启动后大约 30-45 秒进世界。"
}

# ---------------------------------------------------------------- wait for login
Write-Step "等待客户端登录（最多 $LoginTimeoutSec 秒）…"
$loginSeen = $false
$deadline = (Get-Date).AddSeconds($LoginTimeoutSec)
$lastTick = Get-Date
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    $new = Read-NewLog $PacketsLog $packetsOffset
    if ($new -match 'PlayerLoginFinishCsReq|GetCurSceneInfoCsReq') {
        $loginSeen = $true
        break
    }
    if (((Get-Date) - $lastTick).TotalSeconds -ge 15) {
        $lastTick = Get-Date
        $elapsed = [int]((Get-Date) - $deadline.AddSeconds(-$LoginTimeoutSec)).TotalSeconds
        Write-Host "    …已等 $elapsed 秒（还没有登录包）" -ForegroundColor DarkGray
    }
    if ($proxy -and $proxy.HasExited) {
        Write-Host '    代理已退出：可能 UAC 被拒绝，或代理启动失败。' -ForegroundColor Red
    }
}
if (-not $loginSeen) {
    Write-Host '没有检测到登录包。检查：代理是否把域名指到 127.0.0.1:21000、游戏是否真的启动。' -ForegroundColor Red
} else {
    $plan = @($ShotPlan | Where-Object { $_ -gt 0 } | Sort-Object)
    if (-not $plan) { $plan = @(10, 25, 40) }
    Write-Step "检测到登录完成。截图计划：+$($plan -join ' / +') 秒。期间请不要动鼠标键盘、不要点“关闭”。"
    $shots = @()
    $previous = 0
    $index = 0
    foreach ($second in $plan) {
        $wait = $second - $previous
        if ($wait -gt 0) { Start-Sleep -Seconds $wait }
        $previous = $second
        $index++
        $shot = Join-Path $ExpDir "$RunId-$index.png"
        Save-Screenshot $shot
        $shots += $shot
        Write-Host "    第 $index 张（+$second 秒）：$shot" -ForegroundColor DarkGray
    }
    Write-Step "截图：$($shots -join ' , ')"

    $finalShot = Join-Path $ExpDir "$RunId-paused.png"
    try { Save-Screenshot $finalShot } catch { Write-Host "暂停前截图失败：$_" -ForegroundColor DarkYellow }

    Write-Host ''
    Write-Host '截图完成。' -ForegroundColor Green
    Write-Host '如果屏幕中间还有「G 多层韧性」提示：请按 G → 关闭指南窗口 → 等 1 秒。' -ForegroundColor Cyan
    Write-Host '  （这一轮会记录 finish guide 的 group/type 和 ServerPrefs 写入，用来定位已读状态。）'
    Write-Host '如果要顺手验证 Phase 2 战斗，现在可以走到凝滞虚影，按 F 开始战斗。' -ForegroundColor Cyan
    Write-Host '完成后回车。' -ForegroundColor DarkGray
}

if ($loginSeen) {
    Read-Host '完成游戏内操作后按 Enter：收集日志、生成报告并停止服务端' | Out-Null
}

# ---------------------------------------------------------------------- analyse
Write-Step '汇总日志…'
$newPackets = Read-NewLog $PacketsLog $packetsOffset
$packetLines = @($newPackets -split "`r?`n" | Where-Object { $_ -match '->|unhandled|connected|listening|prefs loaded|progress book|battle info:|battle result:|start cocoon:|quick start cocoon:|GET /|POST /|HEAD /' })

$suspects = @(
    'GetMissionDataCsReq', 'GetMissionStatusCsReq', 'GetMainMissionCustomValueCsReq',
    'GetQuestDataCsReq', 'GetActivityScheduleConfigCsReq',
    'RogueMagicQueryCsReq', 'RogueTournQueryCsReq', 'RogueTournGetCurRogueCocoonInfoCsReq',
    'CommonRogueQueryCsReq', 'GetRogueInfoCsReq', 'ChessRogueQueryCsReq',
    'GetBasicInfoCsReq', 'GetAllServerPrefsDataCsReq', 'PlayerLoginCsReq',
    'GetCurSceneInfoCsReq', 'GetSceneMapInfoCsReq', 'EnterSectionCsReq',
    'PlayerLoginFinishCsReq',
    'GetTutorialGuideCsReq', 'UnlockTutorialGuideCsReq', 'FinishTutorialGuideCsReq',
    'InteractPropCsReq', 'StartCocoonStageCsReq', 'QuickStartCocoonStageCsReq',
    'PVEBattleResultCsReq', 'GetCurBattleInfoCsReq', 'SceneCastSkillCsReq'
)
$table = foreach ($name in $suspects) {
    $handle = @($packetLines | Where-Object { $_ -match [regex]::Escape($name) })
    $markedUnhandled = @($handle | Where-Object { $_ -match 'unhandled' }).Count -gt 0
    [pscustomobject]@{
        Command  = $name
        Seen     = if ($handle.Count -gt 0) { 'yes' } else { '-' }
        Handled  = if ($handle.Count -eq 0) { '-' } elseif ($markedUnhandled) { 'NO(unhandled)' } else { 'yes' }
        Times    = $handle.Count
    }
}

$unhandled = @($packetLines | Where-Object { $_ -match 'unhandled' } |
    ForEach-Object { if ($_ -match 'unhandled (\S+)') { $Matches[1] } } | Sort-Object -Unique)

$playerTail = if (Test-Path $PlayerLog) {
    @(Get-Content $PlayerLog -Tail 400)
} else { @() }
$playerLogTime = if (Test-Path $PlayerLog) {
    (Get-Item $PlayerLog).LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
} else { '不存在' }
$exceptions = @($playerTail | Select-String -Pattern 'NullReferenceException|Exception:|Exception ' |
    ForEach-Object { $_.Line.Trim() } | Select-Object -Unique -First 20)
$sdkAnnounceAfter = Get-SdkAnnounceUrl
$prefsAfter = Get-GamePrefs
$prefsChanged = Compare-GamePrefs $prefsBefore $prefsAfter

$shotList = @($shots | ForEach-Object { Split-Path -Leaf $_ })
if ($finalShot -and (Test-Path $finalShot)) { $shotList += Split-Path -Leaf $finalShot }

$report = Join-Path $ExpDir "$RunId.report.md"
$sb = New-Object System.Text.StringBuilder
[void]$sb.AppendLine("# P0 弹窗实验报告：$Experiment")
[void]$sb.AppendLine('')
[void]$sb.AppendLine("- 时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')")
[void]$sb.AppendLine("- 配置：``$cfgPath``")
[void]$sb.AppendLine("- 服务端 PID：$($server.Id)（日志 ``$([IO.Path]::GetFileName($serverOut))``）")
[void]$sb.AppendLine("- 登录包：$(if ($loginSeen) { '已看到' } else { '未看到' })")
[void]$sb.AppendLine("- 截图：$(if ($shotList.Count) { ($shotList -join ', ') } else { '无' })")
[void]$sb.AppendLine("- Player.log 最后写入：$playerLogTime")
[void]$sb.AppendLine("- SDK announce URL（测试后）：$sdkAnnounceAfter")
[void]$sb.AppendLine("- PlayerPrefs 变化条数：$($prefsChanged.Count)")
[void]$sb.AppendLine("- 客户端教程/指南已读状态已写入：$clientPrefsOk")
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 实验开关')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```')
foreach ($line in @(Get-Content $cfgPath | Select-String -Pattern '^popup_')) { [void]$sb.AppendLine($line.Line) }
[void]$sb.AppendLine('```')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 登录阶段的关键请求')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('| 命令 | 客户端发了 | 服务端处理 | 次数 |')
[void]$sb.AppendLine('|---|---|---|---|')
foreach ($row in $table) {
    [void]$sb.AppendLine("| $($row.Command) | $($row.Seen) | $($row.Handled) | $($row.Times) |")
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 仍未处理的命令（登录阶段）')
[void]$sb.AppendLine('')
if ($unhandled.Count) { foreach ($u in $unhandled) { [void]$sb.AppendLine("- ``$u``") } }
else { [void]$sb.AppendLine('（无）') }
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## PlayerPrefs 变化（测试前 → 后）')
[void]$sb.AppendLine('')
if ($prefsChanged.Count) {
    [void]$sb.AppendLine('| Key | 测试前 | 测试后 |')
    [void]$sb.AppendLine('|---|---|---|')
    foreach ($row in $prefsChanged) {
        $beforeText = [string]$row.Before
        $afterText = [string]$row.After
        if ($beforeText.Length -gt 120) { $beforeText = $beforeText.Substring(0, 120) + '…' }
        if ($afterText.Length -gt 120) { $afterText = $afterText.Substring(0, 120) + '…' }
        [void]$sb.AppendLine("| ``$($row.Name)`` | $beforeText | $afterText |")
    }
} else {
    [void]$sb.AppendLine('（没有变化：说明弹窗的“已读”状态不在 PlayerPrefs 里）')
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## SDK / HTTP 请求')
[void]$sb.AppendLine('')
$httpLines = @($packetLines | Where-Object { $_ -match 'GET /|POST /|HEAD /' })
if ($httpLines.Count) {
    [void]$sb.AppendLine('```')
    foreach ($line in $httpLines) { [void]$sb.AppendLine($line) }
    [void]$sb.AppendLine('```')
} else {
    [void]$sb.AppendLine('（本次没有 HTTP 请求）')
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 战斗（Phase 2）')
[void]$sb.AppendLine('')
$battleLines = @($packetLines | Where-Object { $_ -match 'cocoon|Battle|battle' })
if ($battleLines.Count) {
    [void]$sb.AppendLine('```')
    foreach ($line in $battleLines) { [void]$sb.AppendLine($line) }
    [void]$sb.AppendLine('```')
} else {
    [void]$sb.AppendLine('（本次没有战斗相关请求）')
}
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## Player.log 异常')
[void]$sb.AppendLine('')
if ($exceptions.Count) { foreach ($e in $exceptions) { [void]$sb.AppendLine("- ``$e``") } }
else { [void]$sb.AppendLine('（末 400 行里没有异常）') }
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## packets.log 片段（本次运行）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```')
$tailLines = @($packetLines | Select-Object -Last 80)
foreach ($line in $tailLines) { [void]$sb.AppendLine($line) }
[void]$sb.AppendLine('```')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 服务端日志（stdout 尾部）')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('```')
foreach ($line in @(Get-Content $serverOut -Tail 20 -ErrorAction SilentlyContinue)) {
    [void]$sb.AppendLine($line)
}
[void]$sb.AppendLine('```')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('## 怎么判定')
[void]$sb.AppendLine('')
[void]$sb.AppendLine('- 第 2/3 张截图里**没有**「位面规则」面板 → 这组开关有效，进入二分。')
[void]$sb.AppendLine('- 还在弹 → 这组开关不是原因，换下一组。')
[void]$sb.AppendLine('- 如果 Player.log 的写入时间早于本次测试，说明客户端本次没有写日志，异常栏不可信。')
$sb.ToString() | Set-Content -Path $report -Encoding UTF8

Write-Head "结果"
Write-Host "报告：$report" -ForegroundColor Green
Write-Host "截图：$($shots -join ' , ')" -ForegroundColor Green
if ($finalShot -and (Test-Path $finalShot)) { Write-Host "暂停前截图：$finalShot" -ForegroundColor Green }
Write-Host ''
Write-Host '判定：' -ForegroundColor Cyan
Write-Host '  * 第 2/3 张截图里没有「位面规则」面板 → 这组开关有效（下一轮再二分）'
Write-Host '  * 还在弹 → 这组开关不是原因，把它关掉试下一组'
Write-Host ''
Write-Host '把 report.md 的内容或截图发回来即可继续分析。' -ForegroundColor Yellow

if (-not $KeepProxy) {
    try { Set-SystemProxy 0; Write-Step '系统代理已关闭（ProxyEnable=0）' } catch { Write-Host "还原系统代理失败：$_" -ForegroundColor Red }
}
if ($server -and -not $server.HasExited) {
    Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    Write-Step '服务端已停止'
}
if ($proxy -and -not $proxy.HasExited) {
    Stop-Process -Id $proxy.Id -Force -ErrorAction SilentlyContinue
    Write-Step '代理已停止'
}
Write-Head '完成'
