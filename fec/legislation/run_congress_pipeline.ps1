# Hidden Windows Task Scheduler entry point. The Python wrapper owns the lease,
# detailed logs, timeouts and source-to-dashboard terminal receipt.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$pythonPath = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
$refreshScript = Join-Path $PSScriptRoot 'refresh_congress_pipeline.py'
$engineDirectory = Join-Path $projectRoot 'data\engine'
$env:MONEYBALL_ROOT = $projectRoot
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Congress refresh Python runtime is missing: $pythonPath"
}
$job = Start-Process -FilePath $pythonPath -ArgumentList ('"{0}"' -f $refreshScript) `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -Wait `
    -RedirectStandardOutput (Join-Path $engineDirectory 'congress_scheduled.stdout.log') `
    -RedirectStandardError (Join-Path $engineDirectory 'congress_scheduled.stderr.log')
exit $job.ExitCode
