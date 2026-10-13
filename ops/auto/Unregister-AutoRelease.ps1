<#
.SYNOPSIS
  Removes the "MelangeAutoRelease" scheduled task. Nothing else: the release state under %LOCALAPPDATA%\Melange\releases stays.
  To stop it temporarily instead, run `python ops\release.py auto --pause`.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param([string]$TaskName = 'MelangeAutoRelease')
$ErrorActionPreference = 'Stop'
if (-not (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)) { Write-Host "No task named $TaskName."; return }
if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister the scheduled task')) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "Unregistered $TaskName."
}
