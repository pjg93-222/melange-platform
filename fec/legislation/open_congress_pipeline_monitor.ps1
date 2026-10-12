<#
    Open the read-only pipeline watcher in its own visible console window.

    The watcher writes nothing; this only launches it. Close the window, or
    press Ctrl+C inside it, to stop watching.

    Windows PowerShell 5.1 compatible: no ternary, no ??, no &&.
#>
param(
    [string]$Root = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems',
    [int]$IntervalSeconds = 5
)

$ErrorActionPreference = 'Stop'

$watcher = Join-Path $PSScriptRoot 'watch_congress_pipeline.ps1'
if (-not (Test-Path -LiteralPath $watcher)) {
    throw "Watcher script not found: $watcher"
}

# The watcher sets its own window title, so -File is enough here.
$arguments = @(
    '-NoExit',
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    # W3: both paths contain the space in "Melange Systems"; unquoted, the new window died.
    '-File', ('"{0}"' -f $watcher),
    '-Root', ('"{0}"' -f $Root),
    '-IntervalSeconds', $IntervalSeconds
)

Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments | Out-Null
Write-Host 'Opened "CONGRESS PIPELINE - LIVE" in a new window.'
Write-Host ('  watcher : ' + $watcher)
Write-Host ('  root    : ' + $Root)
