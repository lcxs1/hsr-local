#requires -Version 5.1
<#
.SYNOPSIS
    最小可玩回归：单测 + 离线 5 链路回放 + ruff + proto/handler 检查。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\check.ps1
#>
param(
    [switch]$SkipRuff
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw "python venv not found: $Python" }

Write-Host '== unittest ==' -ForegroundColor Cyan
& $Python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '== check_proto ==' -ForegroundColor Cyan
& $Python scripts\check_proto.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '== replay ==' -ForegroundColor Cyan
& $Python scripts\replay_flows.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host '== check_docs ==' -ForegroundColor Cyan
& $Python scripts\check_docs.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

if (-not $SkipRuff) {
    $Ruff = $null
    $cmd = Get-Command ruff -ErrorAction SilentlyContinue
    if ($cmd) { $Ruff = $cmd.Source }
    if (-not $Ruff) {
        $candidate = 'E:\LCXS\run\echium-server-main\.venv\Scripts\ruff.exe'
        if (Test-Path $candidate) { $Ruff = $candidate }
    }
    if ($Ruff) {
        Write-Host '== ruff ==' -ForegroundColor Cyan
        & $Ruff check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host 'ruff not found, skipped (use -SkipRuff to silence)' -ForegroundColor Yellow
    }
}

Write-Host 'OK' -ForegroundColor Green
