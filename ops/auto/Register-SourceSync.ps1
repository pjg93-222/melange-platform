<#
.SYNOPSIS
  Opt-in, limited-user source-only synchronization every 15 minutes and at logon.
.DESCRIPTION
  Requires one successful manual source synchronization before registration.
  -Push opts in to ordinary fast-forward pushes to the pinned platform repository.
  No tags, installer publication, data upload, signing keys, or production deployment.
  -WhatIf displays the action and changes no tasks. Existing tasks are never replaced.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
  [string]$PythonExe = '',
  [ValidateRange(5, 1440)][int]$EveryMinutes = 15,
  [string]$TaskName = 'MelangeSourceSync',
  [switch]$Push
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$runner = Join-Path $repo 'ops\auto\source_sync_task.py'
if (-not (Test-Path -LiteralPath $runner -PathType Leaf)) { throw "Missing runner: $runner" }
if (-not $PythonExe) { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
if ($PythonExe -like '*\WindowsApps\*') { throw 'Pass a real Python interpreter, not a Store alias.' }
$PythonWindowless = Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $PythonWindowless -PathType Leaf)) { throw 'A matching pythonw.exe is required for a hidden task.' }
# All values are arguments to a fixed Python script, never shell fragments.
foreach ($value in @($repo, $runner, $PythonExe)) {
  if ($value.Contains('"') -or $value.Contains("`r") -or $value.Contains("`n")) { throw 'Unsafe task argument.' }
}
$receiptPath = Join-Path $env:LOCALAPPDATA 'Melange\source-sync\latest.json'
if (-not (Test-Path -LiteralPath $receiptPath -PathType Leaf)) {
  throw 'Run python ops/sync_sources.py --apply (and --push if desired) successfully before installing polling.'
}
$receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
if ($receipt.platformRemote -ne 'https://github.com/pjg93-222/melange-platform.git' -or
    -not (Test-Path -LiteralPath $receipt.securityReport -PathType Leaf) -or
    ($Push -and -not $receipt.pushed)) { throw 'A successful matching source-only sync receipt is required.' }
$config = Get-Content -LiteralPath (Join-Path $repo 'ops\release_config.json') -Raw | ConvertFrom-Json
$current = & git --git-dir $config.platform_bare rev-parse refs/heads/main
if ($LASTEXITCODE -ne 0 -or $current -ne $receipt.candidate) { throw 'Platform main differs from the verified receipt; run a manual sync first.' }
$arguments = '"{0}"' -f $runner
if ($Push) { $arguments += ' --push' }
Write-Host "Task: $TaskName; every $EveryMinutes minutes plus logon; limited user; Push=$([bool]$Push)"
Write-Host "$PythonWindowless $arguments"
if ($PSCmdlet.ShouldProcess($TaskName, 'Register source-only scheduled task')) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { throw 'A task with this name already exists; it will not be replaced.' }
  $identity = "$env:USERDOMAIN\$env:USERNAME"
  $action = New-ScheduledTaskAction -Execute $PythonWindowless -Argument $arguments -WorkingDirectory $repo
  $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
  $logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
  $principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
  $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($repeat, $logon) -Principal $principal -Settings $settings -Description 'Verified committed sources to platform main only; no tags, data, installers, or deployment.' | Out-Null
}
