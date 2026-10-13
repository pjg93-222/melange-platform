<#
.SYNOPSIS
Compatibility wrapper for the generic Mentat industry workbook generator.

.DESCRIPTION
The historical Crypto command keeps its complete CLI contract. All workbook
implementation now lives in build_industry_report.ps1 and is selected by the
data-driven -Industry value.
#>
[CmdletBinding()]
param(
  [string]$Industry = 'Crypto',
  [string]$DatabasePath = '',
  [string]$OutputDirectory = '',
  [string]$CommitteeMasterDirectory = '',
  [string]$BeneficiaryDirectory = '',
  [string]$RScriptPath = $(if ($env:MENTAT_RSCRIPT) { $env:MENTAT_RSCRIPT } else { 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe' }),
  [string]$NodePath = '',
  [string]$NodeModulesPath = '',
  [ValidateRange(0,32768)]
  [int]$NodeHeapMb = 0,
  [ValidateRange(1,45)]
  [double]$PerformanceBudgetMinutes = 15,
  [string]$WorkbookLockPath = '',
  [string]$ExcelMutexName = 'Global\MentatIndustryExcelFinalize',
  [ValidateSet('','2021-2022','2023-2024','2025-2026')]
  [string]$CycleLabel = '',
  [switch]$NoCache,
  [ValidateRange(0,10000)]
  [int]$CrossReferenceMaxRequests = 100,
  [switch]$CrossReferenceOffline,
  [switch]$RenderPreviews
)

$ErrorActionPreference = 'Stop'
$generator = Join-Path (
  Split-Path -Parent $MyInvocation.MyCommand.Path
) 'generate_industry_report.ps1'
$arguments = @{
  Industry = $Industry
  ExcelMutexName = $ExcelMutexName
  NodeHeapMb = $NodeHeapMb
  PerformanceBudgetMinutes = $PerformanceBudgetMinutes
  CrossReferenceMaxRequests = $CrossReferenceMaxRequests
}
if ($DatabasePath) { $arguments.DatabasePath = $DatabasePath }
if ($OutputDirectory) { $arguments.OutputDirectory = $OutputDirectory }
if ($CommitteeMasterDirectory) {
  $arguments.CommitteeMasterDirectory = $CommitteeMasterDirectory
}
if ($BeneficiaryDirectory) {
  $arguments.BeneficiaryDirectory = $BeneficiaryDirectory
}
if ($RScriptPath) { $arguments.RScriptPath = $RScriptPath }
if ($NodePath) { $arguments.NodePath = $NodePath }
if ($NodeModulesPath) { $arguments.NodeModulesPath = $NodeModulesPath }
if ($WorkbookLockPath) { $arguments.WorkbookLockPath = $WorkbookLockPath }
if ($CycleLabel) { $arguments.CycleLabel = $CycleLabel }
if ($NoCache) { $arguments.NoCache = $true }
if ($CrossReferenceOffline) { $arguments.CrossReferenceOffline = $true }
if ($RenderPreviews) { $arguments.RenderPreviews = $true }

& $generator @arguments
exit $LASTEXITCODE
