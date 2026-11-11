#requires -Version 5.1
<#
.SYNOPSIS
    把客户端的“教程/指南已读”PlayerPrefs 写成全量（关游戏后运行）。

.DESCRIPTION
    客户端把指南/教程的本地已读状态存在注册表：
      HKCU\Software\Cognosphere\Star Rail
        User_<uid>_UnlockedTutorialGuides   (UTF-8 JSON 数组 + 结尾 NUL)
        User_<uid>_FinishedTutorials        (UTF-8 JSON 数组 + 结尾 NUL)

    ⚠ 实测：客户端在每次登录时会重写这两个值（FinishedTutorials 清空、
    UnlockedTutorialGuides 换成它自己的约 1800 条），所以本脚本只适合做对照
    实验，不能作为“去掉提示”的长期方案。真正修复是服务端
    GetTutorialGuideScRsp 里指南组的 type 必须为 0（TutorialGuideDefault），
    见 scripts/export_tutorials.py 与 data/tutorials.json。

    用法（游戏必须已关闭）：
      powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\fix_client_prefs.ps1
      powershell ... -File .\scripts\fix_client_prefs.ps1 -DryRun
#>
param(
    [int]$Uid = 0,
    [string]$TutorialsPath = '',
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
if (-not $TutorialsPath) { $TutorialsPath = Join-Path $Root 'data\tutorials.json' }
if (-not $Uid) {
    $cfgPath = Join-Path $Root 'config.toml'
    if (Test-Path $cfgPath) {
        $line = Get-Content $cfgPath | Where-Object { $_ -match '^\s*uid\s*=' } | Select-Object -First 1
        if ($line -and $line -match '=\s*(\d+)') { $Uid = [int]$Matches[1] }
    }
}
if (-not $Uid) { $Uid = 10001 }

if (-not (Test-Path $TutorialsPath)) { throw "missing $TutorialsPath" }
$data = Get-Content $TutorialsPath -Raw -Encoding UTF8 | ConvertFrom-Json
$tutorialIds = @($data.tutorials | ForEach-Object { [int]$_ })
$guideGroups = @($data.guides | ForEach-Object { [int]$_.id })
$guideItems = @($data.guide_ids | ForEach-Object { [int]$_ })
# The local UnlockedTutorialGuides list is consumed by both the group-level
# unlock checks and the per-item guide hints ("G 多层韧性").  Include both id
# spaces; unknown ids are ignored by the client.
$guideIds = @(@($guideGroups + $guideItems) | Sort-Object -Unique)
if (-not $tutorialIds -or -not $guideIds) { throw 'tutorials.json is empty' }

$key = 'HKCU:\Software\Cognosphere\Star Rail'
if (-not (Test-Path $key)) { throw "registry key not found: $key" }
$names = (Get-Item $key).GetValueNames()

function Set-PrefJson([string]$prefix, [int[]]$ids) {
    $matches = @($names | Where-Object { $_ -like "$prefix*" })
    if (-not $matches) {
        Write-Host "  ! no PlayerPrefs value starting with $prefix" -ForegroundColor DarkYellow
        return 0
    }
    foreach ($name in $matches) {
        # Merge with whatever the client already wrote: the client keeps its
        # own (slightly different) id set and we do not want to shrink it.
        $merged = [System.Collections.Generic.SortedSet[int]]::new()
        $existing = Get-ItemPropertyValue -Path $key -Name $name -ErrorAction SilentlyContinue
        if ($existing) {
            try {
                $text = [System.Text.Encoding]::UTF8.GetString($existing).TrimEnd([char]0)
                if ($text) {
                    foreach ($id in ($text | ConvertFrom-Json)) { [void]$merged.Add([int]$id) }
                }
            } catch {
                Write-Host "  ! $name is not valid JSON, overwriting" -ForegroundColor DarkYellow
            }
        }
        foreach ($id in $ids) { [void]$merged.Add([int]$id) }
        # Unity stores PlayerPrefs strings as UTF-8 bytes + a trailing NUL.
        # Without the NUL the game can fail to read the value (and then treats
        # the guide/tutorial as unseen), so keep the exact on-disk format.
        $json = ConvertTo-Json -InputObject @($merged) -Compress
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($json) + [byte]0
        if ($DryRun) {
            Write-Host "  would set $name = $($merged.Count) ids" -ForegroundColor DarkGray
        } else {
            New-ItemProperty -Path $key -Name $name -PropertyType Binary -Value $bytes -Force | Out-Null
            Write-Host "  set $name = $($merged.Count) ids" -ForegroundColor Green
        }
    }
    return $matches.Count
}

$changed = 0
$changed += Set-PrefJson "User_${Uid}_UnlockedTutorialGuides" $guideIds
$changed += Set-PrefJson "User_${Uid}_FinishedTutorials" $tutorialIds
Write-Host "client prefs: $changed value(s) patched (tutorials=$($tutorialIds.Count), guides=$($guideIds.Count))"
