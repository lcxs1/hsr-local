#requires -Version 5.1
<#
.SYNOPSIS
    把本地 freesr-data.json 推给运行中的 hsr-local，走和网页「连接 PS」同一个入口。

.DESCRIPTION
    POST /srtools，body = { data: <freesr json>, username, password }
    服务端会落盘、替换内存 roster，并把新状态推给正在游戏的客户端（不用重启）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push_freesr.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push_freesr.ps1 `
        -Path D:\new-freesr.json -Url http://127.0.0.1:21000/srtools

.EXAMPLE
    # 允许把遗器/光锥清空（默认会被服务端拒绝，防止网页空状态误清空）
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\push_freesr.ps1 -Force
#>
param(
    [string]$Path = '',
    [string]$Url = 'http://127.0.0.1:21000/srtools',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Path) { $Path = Join-Path $Root 'data\freesr-data.json' }
if (-not (Test-Path $Path)) { throw "freesr file not found: $Path" }

if ($Force) { $Url = $Url + '?force=1' }

Write-Host "pushing $Path" -ForegroundColor Cyan
Write-Host "     to $Url" -ForegroundColor Cyan

$doc = Get-Content -Raw -Encoding UTF8 $Path | ConvertFrom-Json
$body = @{
    data     = $doc
    username = ''
    password = ''
} | ConvertTo-Json -Depth 64 -Compress

try {
    $rsp = Invoke-RestMethod -Uri $Url -Method Post -ContentType 'application/json' -Body $body
} catch {
    $detail = $_.ErrorDetails.Message
    if ($detail) { throw "server rejected the push: $detail" }
    throw
}

Write-Host ("OK: avatars={0} relics={1} lightcones={2} written={3} sessions={4}" -f `
    $rsp.avatars, $rsp.relics, $rsp.lightcones, $rsp.written, $rsp.sessions) -ForegroundColor Green
Write-Host '服务端日志应出现一行: freesr hot reload: ... from srtools, written, pushed to N session(s)'
