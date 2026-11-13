#requires -Version 5.1
<#
.SYNOPSIS
    重启客户端（结束 -> 起代理 -> 等登录），把「重启—复验」压成一条命令。

.DESCRIPTION
    服务端不动（重启客户端后新握手会替换会话），脚本只负责客户端这一半：
      1. 结束 StarRail / firefly-go-proxy（提权进程可能杀不掉，脚本会明确告诉你）；
      2. 起代理（Start-Process -Verb RunAs，UAC 点“是”）；
      3. 等 logs\packets.log 出现新的 PlayerLoginFinishCsReq（默认 240 s）；
      4. 打印结果。

    为什么需要它：代理用 -Verb RunAs 起时游戏继承管理员权限，普通权限的 agent 既杀不掉
    也重启不了（guide/03 坑 9 / notes 2026-11-13 §5.4）。走一遍这个脚本就回到可自动化的状态。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restart_client.ps1

.EXAMPLE
    # 只等登录（代理/游戏你自己起）
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\restart_client.ps1 -WaitOnly
#>
param(
    [ValidateSet('4.6.54', '4.7.0', '4.7.51')]
    [string]$Client = '4.7.51',

    [string]$GameDir = 'E:\SiFu\HSRSiFu',
    [string]$ProxyExe = '',          # 留空 = $GameDir\firefly-go-proxy.exe
    [int]$ExitWaitSec = 20,          # 等进程退出的时间
    [int]$WaitSec = 240,             # 等进世界（PlayerLoginFinishCsReq）

    [switch]$NoProxy,                # 不重启代理（-WaitOnly 会隐含它）
    [switch]$WaitOnly,               # 只等登录，不杀进程/不起代理
    [switch]$KeepProxy               # 收尾不关代理（默认本来也不关，留参备用）
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ClientDirs = @{
    '4.6.54' = 'StarRail_4.6.54'
    '4.7.0'  = 'StarRail_CN_4.7.0'
    '4.7.51' = 'StarRail_4.7.51'
}
$GameExe = Join-Path (Join-Path $GameDir $ClientDirs[$Client]) 'StarRail.exe'
if (-not $ProxyExe) { $ProxyExe = Join-Path $GameDir 'firefly-go-proxy.exe' }
$PacketsLog = Join-Path $Root 'logs\packets.log'

function Test-Login([long]$offset) {
    if (-not (Test-Path $PacketsLog)) { return $false }
    $fs = $null
    try {
        $fs = [IO.File]::Open($PacketsLog, 'Open', 'Read', 'ReadWrite')
        if ($fs.Length -le $offset) { return $false }
        [void]$fs.Seek($offset, 'Begin')
        $reader = New-Object IO.StreamReader($fs, [Text.Encoding]::UTF8)
        $tail = $reader.ReadToEnd()
        $reader.Dispose()
        return ($tail -match 'PlayerLoginFinishCsReq')
    } catch {
        return $false
    } finally {
        if ($fs) { $fs.Dispose() }
    }
}

$offset = if (Test-Path $PacketsLog) { (Get-Item $PacketsLog).Length } else { 0 }

if ($WaitOnly) {
    Write-Host '只等登录（不重启任何进程）…' -ForegroundColor Cyan
} else {
    # --- 1) 结束旧进程 ---------------------------------------------------------
    $killed = 0
    foreach ($name in @('StarRail', 'firefly-go-proxy')) {
        $procs = @(Get-Process -Name $name -ErrorAction SilentlyContinue)
        if (-not $procs.Count) { continue }
        $ids = ($procs | ForEach-Object { $_.Id }) -join ', '
        Write-Host "结束 $name（PID $ids）…" -ForegroundColor Yellow
        $procs | Stop-Process -Force -ErrorAction SilentlyContinue
        $deadline = (Get-Date).AddSeconds($ExitWaitSec)
        while ((Get-Date) -lt $deadline -and @(Get-Process -Name $name -ErrorAction SilentlyContinue).Count) {
            Start-Sleep -Milliseconds 250
        }
        $left = @(Get-Process -Name $name -ErrorAction SilentlyContinue)
        if ($left.Count) {
            $leftIds = ($left | ForEach-Object { $_.Id }) -join ', '
            Write-Host "  $name 还活着（PID $leftIds）：多半是提权进程。" -ForegroundColor Red
            Write-Host '  请手动关掉它（或任务管理器结束），然后重跑本脚本；' -ForegroundColor Red
            Write-Host '  想以后都能自动重启：用普通权限起代理（不要 -Verb RunAs）。' -ForegroundColor Red
            exit 3
        }
        $killed++
    }
    if (-not $killed) { Write-Host '没有需要结束的进程' -ForegroundColor DarkGray }

    # --- 2) 起代理（UAC 点“是”） ----------------------------------------------
    if ($NoProxy) {
        Write-Host '-NoProxy：代理不动，自己起（起完加 -WaitOnly 等登录即可）' -ForegroundColor Yellow
    } else {
        if (-not (Test-Path $ProxyExe)) { throw "找不到代理：$ProxyExe（-ProxyExe 指定）" }
        if (-not (Test-Path $GameExe)) { throw "找不到客户端：$GameExe（-GameDir / -Client 指定）" }
        Write-Host "启动代理（UAC 点“是”）：$ProxyExe" -ForegroundColor Cyan
        $proxy = Start-Process -FilePath $ProxyExe -ArgumentList @('-e', $GameExe, '-p', '9978') -Verb RunAs -PassThru -ErrorAction Stop
        Start-Sleep -Seconds 3
        if ($proxy.HasExited) { throw "代理启动后立刻退出（UAC 被拒绝？）：$ProxyExe" }
        Write-Host "代理 PID $($proxy.Id)；游戏大约 30-45 秒进世界。" -ForegroundColor DarkGray
    }
}

# --- 3) 等登录 ---------------------------------------------------------------
$deadline = (Get-Date).AddSeconds($WaitSec)
while ((Get-Date) -lt $deadline) {
    if (Test-Login $offset) {
        Write-Host '登录包已出现（PlayerLoginFinishCsReq）——客户端回到可测试状态。' -ForegroundColor Green
        exit 0
    }
    Start-Sleep -Seconds 1
}
Write-Host "等了 $WaitSec 秒没有 PlayerLoginFinishCsReq：看代理是否拦截 HTTPS、系统代理是否指向 127.0.0.1:9978。" -ForegroundColor Red
exit 4
