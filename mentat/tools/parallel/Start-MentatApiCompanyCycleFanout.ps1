[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$SourceDatabasePath,
  [Parameter(Mandatory = $true)][string]$Industry,
  [ValidatePattern('^(?:2021-2022|2022|2023-2024|2024|2025-2026|2026)(?:[|,](?:2021-2022|2022|2023-2024|2024|2025-2026|2026))*$')]
  [string]$Cycles = '2022|2024|2026',
  [string]$RunDirectory = '',
  [string]$OutputReceiptPath = '',
  [ValidateRange(0, 64)][int]$MaxParallel = 0,
  [ValidateRange(0, 64)][int]$InitialWorkers = 0,
  [ValidateRange(0, 64)][int]$RecoveryWorkers = 0,
  [ValidateRange(1, 64)][int]$ConcurrencyPerKey = 8,
  [string]$KeysFile = '',
  [string]$BudgetDatabase = '',
  [ValidateRange(128, 8192)][int]$MemoryPerLaneMB = 640,
  [ValidateRange(256, 16384)][int]$MemoryReserveMB = 1024,
  [ValidateRange(32, 2048)][int]$SQLiteCacheMB = 256,
  [ValidateRange(2, 64)][int]$MergeBatchLimit = 10,
  [switch]$Resume,
  [string]$PythonPath = '',
  [string]$RScriptPath = 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe',
  [string]$RLibraryPath =
    'C:\Users\pgoff\AppData\Local\R\win-library\4.6'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $scriptRoot
$projectRoot = Split-Path -Parent $toolsRoot
$fanout = Join-Path $toolsRoot 'api_company_cycle_fanout.py'

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
  $fanout
)) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Required API-fanout input is missing: $path"
  }
}
if ($KeysFile -and -not (Test-Path -LiteralPath $KeysFile -PathType Leaf)) {
  throw "FEC API keys file is missing: $KeysFile"
}

$SourceDatabasePath = [IO.Path]::GetFullPath($SourceDatabasePath)
if (-not $RunDirectory) {
  $RunDirectory = Join-Path $projectRoot (
    '.cache\api-fanout\{0}-{1}' -f
      (Get-Date -Format 'yyyyMMddTHHmmss'),
      $PID
  )
}
$RunDirectory = [IO.Path]::GetFullPath($RunDirectory)
[IO.Directory]::CreateDirectory($RunDirectory) | Out-Null
if (-not $OutputReceiptPath) {
  $OutputReceiptPath = Join-Path $RunDirectory 'receipt.json'
}
$OutputReceiptPath = [IO.Path]::GetFullPath($OutputReceiptPath)

try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat API fanout - $Industry"
} catch {
  # Console color/title support is optional.
}
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)

$arguments = @(
  $fanout,
  '--source-database', $SourceDatabasePath,
  '--industry', $Industry,
  '--cycles', $Cycles,
  '--run-directory', $RunDirectory,
  '--output-receipt', $OutputReceiptPath,
  '--max-parallel', [string]$MaxParallel,
  '--initial-workers', [string]$InitialWorkers,
  '--recovery-workers', [string]$RecoveryWorkers,
  '--concurrency-per-key', [string]$ConcurrencyPerKey,
  '--rscript-path', ([IO.Path]::GetFullPath($RScriptPath)),
  '--r-library-path', ([IO.Path]::GetFullPath($RLibraryPath)),
  '--memory-per-lane-mb', [string]$MemoryPerLaneMB,
  '--memory-reserve-mb', [string]$MemoryReserveMB,
  '--sqlite-cache-mb', [string]$SQLiteCacheMB,
  '--merge-batch-limit', [string]$MergeBatchLimit
)
if ($KeysFile) {
  $arguments += @('--keys-file', ([IO.Path]::GetFullPath($KeysFile)))
}
if ($BudgetDatabase) {
  $arguments += @(
    '--budget-database',
    ([IO.Path]::GetFullPath($BudgetDatabase))
  )
}
if ($Resume) {
  $arguments += '--resume'
}

Write-Host 'MENTAT API COMPANY-CYCLE FANOUT' -ForegroundColor Cyan
Write-Host "Industry:         $Industry"
Write-Host "Cycles:           $Cycles"
Write-Host "Source database:  $SourceDatabasePath"
Write-Host "Run directory:    $RunDirectory"
Write-Host "Python runtime:   $PythonPath"
Write-Host "Max parallel:     $(if ($MaxParallel) { $MaxParallel } else { 'auto' })"
Write-Host "Recovery workers: $RecoveryWorkers"
Write-Host "Resume:            $([bool]$Resume)"
Write-Host "Status:            $(Join-Path $RunDirectory 'status.json')"
Write-Host 'API key values are redacted and never passed on the command line.'
Write-Host (
  'Source database is unchanged during collection and is updated in place ' +
  'only after every company-cycle task passes.'
)

& $PythonPath @arguments
$exitCode = $LASTEXITCODE
if ($exitCode -ne 0) {
  throw (
    "API company-cycle fanout exited with code $exitCode. " +
    "Receipt: $OutputReceiptPath"
  )
}
if (-not (Test-Path -LiteralPath $OutputReceiptPath -PathType Leaf)) {
  throw "API company-cycle fanout succeeded without a receipt."
}
$receipt = Get-Content -LiteralPath $OutputReceiptPath -Raw | ConvertFrom-Json
if ($receipt.status -ne 'succeeded' -or -not [bool]$receipt.merged) {
  throw "API fanout receipt did not confirm a complete merged run."
}
Write-Host "API FANOUT COMPLETE: $OutputReceiptPath" -ForegroundColor Green
Write-Output $OutputReceiptPath
