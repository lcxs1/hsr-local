#requires -Version 5.1
<#
.SYNOPSIS
    重新生成 StarRail_pb2.py / .pyi / hsr.desc（换客户端版本时跑）。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\gen_proto.ps1

.EXAMPLE
    .\scripts\gen_proto.ps1 -Protoc "C:\protoc\bin\protoc.exe"
#>
param(
    [string]$Protoc = 'protoc',
    [string]$ProtoDir = 'proto',
    [string]$OutDir = 'hsr\protocol\gen',
    [string]$Desc = 'proto\hsr.desc'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$protoFile = Join-Path $ProtoDir 'StarRail.proto'
if (-not (Test-Path $protoFile)) { throw "missing $protoFile" }
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Force -Path $OutDir | Out-Null }

$cmd = Get-Command $Protoc -ErrorAction SilentlyContinue
if (-not $cmd) {
    throw "protoc not found: $Protoc（安装 protoc 或传 -Protoc 完整路径）"
}

Write-Host "protoc: $($cmd.Source)"
& $cmd.Source `
    "--proto_path=$ProtoDir" `
    "--python_out=$OutDir" `
    "--pyi_out=$OutDir" `
    "--descriptor_set_out=$Desc" `
    "--include_imports" `
    $protoFile
if ($LASTEXITCODE -ne 0) { throw "protoc failed with exit code $LASTEXITCODE" }
Write-Host "generated $OutDir\StarRail_pb2.py, $OutDir\StarRail_pb2.pyi, $Desc" -ForegroundColor Green
Write-Host "next: run .\scripts\check.ps1" -ForegroundColor Yellow
