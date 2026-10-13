<#
.SYNOPSIS
  Registers the "MelangeAutoRelease" scheduled task: `python ops\release.py auto` every 15 minutes and at logon.

.DESCRIPTION
  Run it yourself, once. `-WhatIf` prints what would be registered and changes nothing.
  The task runs as you, interactive, run level Limited (never elevated), one instance at a time, and appends everything to
  %LOCALAPPDATA%\Melange\releases\auto.log. It needs, on the machine and in your session:
    * the Windows "OpenSSH Authentication Agent" service running with the release key loaded (ssh-add, once per boot),
    * `gh` signed in, the Credential Manager entries and `restic`/`cloudflared` (ops\RELEASE.md, "Paul's one-time setup").
  Without the key in the agent the task still runs: it checks first, alerts, releases nothing and tries again next time.
  `-ConfirmOutward` is the same list `release.py auto --confirm-outward` takes; drop a step from it to keep that step from
  ever happening unattended (the release then stops there, pending). Pause without unregistering: `release.py auto --pause`.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
  [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path,
  [string]$PythonExe = '',
  [ValidateRange(5, 1440)][int]$EveryMinutes = 15,
  [string]$ConfirmOutward = 'git,geo,data,manifest,netlify,undraft',
  [string]$TaskName = 'MelangeAutoRelease'
)
$ErrorActionPreference = 'Stop'
if ($ConfirmOutward -notmatch '^(git|geo|data|manifest|netlify|undraft)(,(git|geo|data|manifest|netlify|undraft))*$') {
  throw "ConfirmOutward must be a comma list of git,geo,data,manifest,netlify,undraft (got '$ConfirmOutward')."
}
$script = Join-Path $RepoRoot 'ops\release.py'
if (-not (Test-Path -LiteralPath $script -PathType Leaf)) { throw "Not found: $script" }
if (-not $PythonExe) { $PythonExe = (Get-Command python -ErrorAction Stop).Source }
if ($PythonExe -like '*\WindowsApps\*') { throw "python resolves to a Microsoft Store alias ($PythonExe). Pass -PythonExe with the real interpreter." }
$releases = Join-Path $env:LOCALAPPDATA 'Melange\releases'
$log = Join-Path $releases 'auto.log'
# cmd /c "<python> <script> auto ... >> <log> 2>&1": the outer quotes are stripped by cmd, the inner ones stay.
$arguments = '/c ""{0}" "{1}" auto --confirm-outward {2} >> "{3}" 2>&1"' -f $PythonExe, $script, $ConfirmOutward, $log
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\cmd.exe" -Argument $arguments -WorkingDirectory $RepoRoot
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) -RepetitionDuration (New-TimeSpan -Days 3650)
$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Write-Host "Task      : $TaskName (every $EveryMinutes min and at logon, run level Limited, one instance at a time)"
Write-Host "Command   : cmd.exe $arguments"
Write-Host "Directory : $RepoRoot"
Write-Host "Log       : $log"
if ($PSCmdlet.ShouldProcess($TaskName, 'Register the scheduled task')) {
  New-Item -ItemType Directory -Force -Path $releases | Out-Null
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($repeat, $logon) -Principal $principal -Settings $settings `
    -Description 'Melange automatic propagation: python ops\release.py auto (ops\RELEASE.md, Automatic propagation)' | Out-Null
  Write-Host "Registered. Dry look first: python ops\release.py auto --plan"
}
