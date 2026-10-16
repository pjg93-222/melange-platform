[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$RepoRoot,
  [Parameter(Mandatory = $true)][string]$PythonExe,
  [switch]$Push
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$scriptPath = Join-Path $repo 'ops\sync_sources.py'
if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) { throw "Missing source synchronizer: $scriptPath" }
$stateRoot = Join-Path $env:LOCALAPPDATA 'Melange\source-sync'
New-Item -ItemType Directory -Force -Path $stateRoot | Out-Null
$logPath = Join-Path $stateRoot 'scheduled.log'
$invokeArgs = @($scriptPath, '--apply')
if ($Push) { $invokeArgs += '--push' }
Push-Location -LiteralPath $repo
try {
  "`n[$([DateTime]::UtcNow.ToString('o'))] Source synchronization" | Out-File -LiteralPath $logPath -Append -Encoding utf8
  & $PythonExe @invokeArgs 2>&1 | Out-File -LiteralPath $logPath -Append -Encoding utf8
  exit $LASTEXITCODE
} finally { Pop-Location }
