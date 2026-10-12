[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$DatabasePath,
  [Parameter(Mandatory = $true)][string]$Industry,
  [Parameter(Mandatory = $true)][string]$Company,
  [Parameter(Mandatory = $true)][int]$Cycle,
  [Parameter(Mandatory = $true)][ValidateSet('P1_employer', 'P2_variant')][string]$Phase,
  [Parameter(Mandatory = $true)][string]$Variants,
  [Parameter(Mandatory = $true)][string]$LogPath,
  [string]$RScriptPath = 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe',
  [string]$RLibraryPath = 'C:\Users\pgoff\AppData\Local\R\win-library\4.6'
)

$ErrorActionPreference = 'Stop'
$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $scriptRoot
$collector = Join-Path $toolsRoot 'collect_employer_date_partition.R'
foreach ($path in @($DatabasePath, $RScriptPath, $RLibraryPath, $collector)) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Required employer-partition input is missing: $path"
  }
}

try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat partition - $Company $Cycle"
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

Write-Host 'MENTAT EMPLOYER DATE PARTITION' -ForegroundColor Cyan
Write-Host "Industry: $Industry"
Write-Host "Company:  $Company"
Write-Host "Cycle:    $Cycle"
Write-Host "Phase:    $Phase"
Write-Host "Variants: $Variants"
Write-Host "Database: $DatabasePath"
Write-Host "Log:      $LogPath"

$priorErrorAction = $ErrorActionPreference
try {
  $ErrorActionPreference = 'Continue'
  & $RScriptPath $collector $DatabasePath $Industry $Company $Cycle $Phase $Variants `
    2>&1 | Tee-Object -LiteralPath $LogPath
  $exitCode = $LASTEXITCODE
} finally {
  $ErrorActionPreference = $priorErrorAction
}
if ($exitCode -ne 0) {
  throw "Employer date-partition recovery exited with code $exitCode."
}
Write-Host 'EMPLOYER DATE PARTITION COMPLETE' -ForegroundColor Green
