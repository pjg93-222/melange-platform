<#
.SYNOPSIS
  Registers the "MelangeRefreshWatchdog" scheduled task: `python fec\refresh_watchdog.py --once` every 30 minutes and at logon.

.DESCRIPTION
  Run it yourself, once. `-WhatIf` prints what would be registered (or removed) and changes nothing. Idempotent: running it again
  replaces the task in place. `-Unregister` removes the task (nothing else: the watchdog's state and alert log stay in data\engine).
  The watchdog (fec\refresh_watchdog.py, stdlib only, no AI) reads the refresh pipeline's own files and Task Scheduler, and raises a
  Windows toast plus a line in data\engine\watchdog_alerts.log only when a problem has persisted for 3 consecutive runs. It exits 0
  whatever it finds. The task runs as you, interactive, run level Limited, one instance at a time, 10 minute limit, and uses
  pythonw.exe (no console window every 30 minutes) when it sits beside python.exe; a crash is written to the alert log by the script.
  `fec\refresh_watchdog.py --once --dry-run` shows what it sees right now without writing or toasting anything.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$RepoRoot = '',
  [string]$PythonExe = '',
  [ValidateRange(5, 1440)][int]$EveryMinutes = 30,
  [string]$TaskName = 'MelangeRefreshWatchdog',
  [switch]$Unregister
)
$ErrorActionPreference = 'Stop'
# $PSScriptRoot is empty inside a param() default under Windows PowerShell 5.1, so the default is resolved here.
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path }

if ($Unregister) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister the scheduled task')) {
      Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
      Write-Host "Unregistered $TaskName."
    }
  } else {
    Write-Host "$TaskName is not registered; nothing to do."
  }
  return
}

$script = Join-Path $RepoRoot 'fec\refresh_watchdog.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "Not found: $script" }
if (-not $PythonExe) { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
if ($PythonExe -like '*\WindowsApps\*') { throw "python resolves to a Microsoft Store alias ($PythonExe). Pass -PythonExe with the real interpreter." }
$windowless = Join-Path (Split-Path -Parent $PythonExe) 'pythonw.exe'
if ((Split-Path -Leaf $PythonExe) -ieq 'python.exe' -and (Test-Path -LiteralPath $windowless -PathType Leaf)) { $PythonExe = $windowless }
$arguments = '"{0}" --once' -f $script
$action = New-ScheduledTaskAction -Execute $PythonExe -Argument $arguments -WorkingDirectory $RepoRoot
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Write-Host "Task      : $TaskName (every $EveryMinutes min and at logon, run level Limited, one instance at a time, 10 min limit)"
Write-Host "Command   : $PythonExe $arguments"
Write-Host "Directory : $RepoRoot"
Write-Host "Alert log : $(Join-Path $RepoRoot 'data\engine\watchdog_alerts.log')"
if ($PSCmdlet.ShouldProcess($TaskName, 'Register the scheduled task (replaces an existing one)')) {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($repeat, $logon) -Principal $principal -Settings $settings -Force `
    -Description 'Melange refresh watchdog: python fec\refresh_watchdog.py --once (toast + alert log after 3 consecutive breached runs; stdlib, no AI)' | Out-Null
  Write-Host "Registered. Dry look first: python fec\refresh_watchdog.py --once --dry-run"
}
