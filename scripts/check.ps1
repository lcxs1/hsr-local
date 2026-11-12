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

Write-Host '== ps1 编码 ==' -ForegroundColor Cyan
$noBom = @()
Get-ChildItem -Path $Root -Filter *.ps1 -Recurse -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.venv\\' } |
    ForEach-Object {
        $bytes = [IO.File]::ReadAllBytes($_.FullName)
        $hasBom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
        $nonAscii = $false
        foreach ($b in $bytes) { if ($b -gt 0x7F) { $nonAscii = $true; break } }
        if ($nonAscii -and -not $hasBom) { $noBom += $_.FullName }
    }
if ($noBom.Count) {
    Write-Host '这些 .ps1 含非 ASCII 但没有 UTF-8 BOM（Windows PowerShell 5.1 会乱码/语法错误）：' -ForegroundColor Red
    $noBom | ForEach-Object { Write-Host "  $_" -ForegroundColor Red }
    exit 1
}

if (-not $SkipRuff) {
    $Ruff = $null
    $cmd = Get-Command ruff -ErrorAction SilentlyContinue
    if ($cmd) { $Ruff = $cmd.Source }
    if (-not $Ruff) {
        $candidate = Join-Path $Root '.venv\Scripts\ruff.exe'
        if (Test-Path $candidate) { $Ruff = $candidate }
    }
    if ($Ruff) {
        Write-Host '== ruff ==' -ForegroundColor Cyan
        & $Ruff check .
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } else {
        Write-Host 'ruff not found, skipped (pip install -r requirements-dev.txt)' -ForegroundColor Yellow
    }
}

Write-Host 'OK' -ForegroundColor Green
