<#
Task Scheduler launcher for the local Python daily refresh. The scheduler hides
this process with -WindowStyle Hidden. Python owns day gating, leases and logs.
No agent, Codex automation, generated shell command, or detached process is used.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('first-use', 'retry')]
    [string]$Trigger,
    [string]$Root,
    [string]$PythonPath = (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
)

$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$refreshRoot = [IO.Path]::GetFullPath($Root)
$refreshEntry = Join-Path $refreshRoot 'fec\daily_refresh.py'
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Daily refresh Python runtime is missing: $PythonPath"
}
if (-not (Test-Path -LiteralPath $refreshEntry -PathType Leaf)) {
    throw "Daily refresh entry point is missing: $refreshEntry"
}
Set-Location -LiteralPath $refreshRoot
$env:MONEYBALL_ROOT = $refreshRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
& $PythonPath -u $refreshEntry --trigger $Trigger
exit $LASTEXITCODE
