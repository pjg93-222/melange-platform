[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$Industry,
  [Parameter(Mandatory = $true)][string]$DatabasePath,
  [string]$OutputDirectory = '',
  [Parameter(Mandatory = $true)][string]$LogPath,
  [ValidateSet('','2021-2022','2023-2024','2025-2026')]
  [string]$CycleLabel = '',
  [ValidateRange(0,10000)]
  [int]$CrossReferenceMaxRequests = 100,
  [switch]$CrossReferenceOffline,
  [string]$RLibraryPath = 'C:\Users\pgoff\AppData\Local\R\win-library\4.6'
)

$ErrorActionPreference = 'Stop'
$parallelRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $parallelRoot
Import-Module (Join-Path $toolsRoot 'report_directory.psm1') -Force
$OutputDirectory = Resolve-MentatReportDirectory -ProjectRoot (Split-Path -Parent $toolsRoot) -OutputDirectory $OutputDirectory
$env:MENTAT_REPORT_DIRECTORY = $OutputDirectory
$generator = Join-Path $toolsRoot 'generate_industry_report.ps1'
foreach ($path in @($DatabasePath, $RLibraryPath, $generator)) {
  if (-not (Test-Path -LiteralPath $path)) {
    throw "Required direct-report input is missing: $path"
  }
}

try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat direct report - $Industry"
  Clear-Host
} catch {
  # Host color/title support varies; generation must continue.
}

[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:R_LIBS_USER = [IO.Path]::GetFullPath($RLibraryPath)
$DatabasePath = [IO.Path]::GetFullPath($DatabasePath)
$OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
$LogPath = [IO.Path]::GetFullPath($LogPath)
[IO.Directory]::CreateDirectory((Split-Path -Parent $LogPath)) | Out-Null

Write-Host 'MENTAT DIRECT INDUSTRY REPORT' -ForegroundColor Cyan
Write-Host "Industry: $Industry"
Write-Host "Database: $DatabasePath"
Write-Host "Output:   $OutputDirectory"
Write-Host "Log:      $LogPath"
if ($CycleLabel) { Write-Host "Cycle:    $CycleLabel" }

$generatorArguments = @{
  Industry = $Industry
  DatabasePath = $DatabasePath
  OutputDirectory = $OutputDirectory
  CrossReferenceMaxRequests = $CrossReferenceMaxRequests
}
if ($CycleLabel) { $generatorArguments.CycleLabel = $CycleLabel }
if ($CrossReferenceOffline) { $generatorArguments.CrossReferenceOffline = $true }
& $generator @generatorArguments 2>&1 | Tee-Object -LiteralPath $LogPath
if ($LASTEXITCODE -ne 0) {
  throw "Direct report generator exited with code $LASTEXITCODE."
}
Write-Host 'DIRECT REPORT COMPLETE' -ForegroundColor Green
