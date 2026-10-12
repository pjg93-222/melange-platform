[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$SourceDatabasePath,
  [Parameter(Mandatory = $true)][string]$ShardDatabasePath,
  [Parameter(Mandatory = $true)][string]$Industry,
  [Parameter(Mandatory = $true)][string]$Companies,
  [Parameter(Mandatory = $true)][string]$LogPath,
  [ValidatePattern(
    '^(?:2021-2022|2022|2023-2024|2024|2025-2026|2026)' +
    '(?:\|(?:2021-2022|2022|2023-2024|2024|2025-2026|2026))*$'
  )]
  [string]$Cycles = '2022|2024|2026',
  [string]$ResumeAfter = '1900-01-01T00:00:00',
  [string]$PythonPath = '',
  [string]$RScriptPath = 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe',
  [string]$RLibraryPath =
    'C:\Users\pgoff\AppData\Local\R\win-library\4.6'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $scriptRoot
$snapshotTool = Join-Path $toolsRoot 'sqlite_snapshot.py'
$shardRunner = Join-Path $scriptRoot 'Invoke-MentatCompanyShard.ps1'

if (-not $PythonPath -and $env:MENTAT_PYTHON_PATH) {
  $PythonPath = $env:MENTAT_PYTHON_PATH
}
# One interpreter order for every Mentat launcher (tools\mentat_python.psm1): the
# explicit path above, then Mentat's own venv, then the Codex app's runtime Python,
# then python on PATH. A fallback past the venv is reported on stderr, never stdout.
Import-Module (Join-Path $toolsRoot 'mentat_python.psm1') -Force
$pythonChoice = Resolve-MentatPython -ExplicitPath $PythonPath
if ($null -ne $pythonChoice) {
  $PythonPath = $pythonChoice.Path
  if ($pythonChoice.Warning) {
    [Console]::Error.WriteLine('WARNING: ' + $pythonChoice.Warning)
  }
}
if (-not $PythonPath) {
  throw (
    'Python runtime was not found. Pass -PythonPath, set ' +
    'MENTAT_PYTHON_PATH, or install Python 3 with python on PATH.'
  )
}
$PythonPath = [IO.Path]::GetFullPath($PythonPath)
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
  throw (
    "Python runtime is missing at '$PythonPath'. Pass -PythonPath, set " +
    'MENTAT_PYTHON_PATH, or install Python 3 with python on PATH.'
  )
}

foreach ($path in @(
  $SourceDatabasePath,
  $PythonPath,
  $RScriptPath,
  $RLibraryPath,
  $snapshotTool,
  $shardRunner
)) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Required copied-shard input is missing: $path"
  }
}

$SourceDatabasePath = [IO.Path]::GetFullPath($SourceDatabasePath)
$ShardDatabasePath = [IO.Path]::GetFullPath($ShardDatabasePath)
$LogPath = [IO.Path]::GetFullPath($LogPath)
if ([string]::Equals(
  $SourceDatabasePath,
  $ShardDatabasePath,
  [StringComparison]::OrdinalIgnoreCase
)) {
  throw 'SourceDatabasePath and ShardDatabasePath must be different files.'
}

$shardParent = Split-Path -Parent $ShardDatabasePath
$logParent = Split-Path -Parent $LogPath
[IO.Directory]::CreateDirectory($shardParent) | Out-Null
[IO.Directory]::CreateDirectory($logParent) | Out-Null
Write-Host "Python runtime: $PythonPath"

if (-not (Test-Path -LiteralPath $ShardDatabasePath)) {
  Write-Host "Creating consistent private SQLite shard: $ShardDatabasePath"
  & $PythonPath $snapshotTool $SourceDatabasePath $ShardDatabasePath
  if ($LASTEXITCODE -ne 0) {
    throw "SQLite snapshot creation exited with code $LASTEXITCODE."
  }
} else {
  Write-Host "Reusing checkpointed private SQLite shard: $ShardDatabasePath"
}

& $PythonPath $snapshotTool --check $ShardDatabasePath
if ($LASTEXITCODE -ne 0) {
  throw "Shard preflight quick_check exited with code $LASTEXITCODE."
}

& $shardRunner `
  -DatabasePath $ShardDatabasePath `
  -Industry $Industry `
  -ResumeAfter $ResumeAfter `
  -Companies $Companies `
  -Cycles $Cycles `
  -LogPath $LogPath `
  -RScriptPath $RScriptPath `
  -RLibraryPath $RLibraryPath
if ($LASTEXITCODE -ne 0) {
  throw "Copied company-shard runner exited with code $LASTEXITCODE."
}

& $PythonPath $snapshotTool --check $ShardDatabasePath
if ($LASTEXITCODE -ne 0) {
  throw "Shard post-run quick_check exited with code $LASTEXITCODE."
}
