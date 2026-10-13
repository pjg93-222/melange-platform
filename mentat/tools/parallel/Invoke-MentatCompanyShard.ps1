[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$DatabasePath,
  [Parameter(Mandatory = $true)][string]$Industry,
  [Parameter(Mandatory = $true)][string]$ResumeAfter,
  [Parameter(Mandatory = $true)][string]$Companies,
  [Parameter(Mandatory = $true)][string]$LogPath,
  [ValidatePattern('^(?:2021-2022|2022|2023-2024|2024|2025-2026|2026)(?:\|(?:2021-2022|2022|2023-2024|2024|2025-2026|2026))*$')]
  [string]$Cycles = '2022|2024|2026',
  [string]$RScriptPath = $(if ($env:MENTAT_RSCRIPT) { $env:MENTAT_RSCRIPT } else { 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe' }),
  [string]$RLibraryPath = $(if ($env:MENTAT_R_LIBRARY) { $env:MENTAT_R_LIBRARY } else { 'C:\Users\pgoff\AppData\Local\R\win-library\4.6' })
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $scriptRoot
$collector = Join-Path $toolsRoot 'collect_company_shard.R'
foreach ($path in @($DatabasePath, $RScriptPath, $RLibraryPath, $collector)) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Required company-shard input is missing: $path"
  }
}

try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat shard - $Companies"
  Clear-Host
} catch {
  # Host color/title support varies; collection must continue.
}

[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:R_LIBS_USER = [IO.Path]::GetFullPath($RLibraryPath)
$DatabasePath = [IO.Path]::GetFullPath($DatabasePath)
$LogPath = [IO.Path]::GetFullPath($LogPath)
[IO.Directory]::CreateDirectory((Split-Path -Parent $LogPath)) | Out-Null

Write-Host 'MENTAT COMPANY SHARD' -ForegroundColor Cyan
Write-Host "Industry:  $Industry"
Write-Host "Companies: $Companies"
Write-Host "Cycles:    $Cycles"
Write-Host "Database:  $DatabasePath"
Write-Host "Log:       $LogPath"

$priorErrorAction = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue'
  & $RScriptPath $collector $DatabasePath $Industry $ResumeAfter $Companies `
    $Cycles `
    2>&1 | Tee-Object -LiteralPath $LogPath
  $exitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $priorErrorAction
}
if ($exitCode -ne 0) {
  throw "Company shard exited with code $exitCode."
}
Write-Host 'COMPANY SHARD COMPLETE' -ForegroundColor Green
