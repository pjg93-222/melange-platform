$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Import-Module (Join-Path $projectRoot 'tools\report_directory.psm1') -Force
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('mentat-report-directory-' + [Guid]::NewGuid().ToString('N'))
$prior = $env:MENTAT_REPORT_DIRECTORY
try {
  $configured = Join-Path $testRoot 'published'
  $isolated = Join-Path $testRoot 'isolated'
  $configRoot = Join-Path $testRoot 'project'
  New-Item -ItemType Directory -Path $configured,$isolated,(Join-Path $configRoot 'config') -Force | Out-Null
  @{schema_version=1;directory=$configured} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $configRoot 'config\report-directory.json') -Encoding UTF8
  Remove-Item Env:MENTAT_REPORT_DIRECTORY -ErrorAction SilentlyContinue
  if ((Resolve-MentatReportDirectory -ProjectRoot $configRoot) -ne $configured) { throw 'Config was not used.' }
  $env:MENTAT_REPORT_DIRECTORY = $isolated
  if ((Resolve-MentatReportDirectory -ProjectRoot $configRoot -OutputDirectory $isolated) -ne $isolated) { throw 'Explicit isolation was not used.' }
  $rejected = $false
  try { Resolve-MentatReportDirectory -ProjectRoot $configRoot -OutputDirectory $configured | Out-Null }
  catch { $rejected = $_.Exception.Message -like '*conflicts with the authoritative*' }
  if (-not $rejected) { throw 'A conflicting explicit output directory was allowed.' }
  $env:MENTAT_REPORT_DIRECTORY = Join-Path $testRoot 'unavailable'
  $rejected = $false
  try { Resolve-MentatReportDirectory -ProjectRoot $configRoot | Out-Null }
  catch { $rejected = $_.Exception.Message -like '*unavailable*' }
  if (-not $rejected) { throw 'Unavailable configuration used an alternate directory.' }
  foreach ($relative in @('tools\build_industry_report.ps1','tools\report_directory.psm1',
      'tools\parallel\Start-MentatIndustryReports.ps1','tools\parallel\Start-MentatResilientIndustryReports.ps1',
      'tools\parallel\Invoke-MentatIndustryWorker.ps1','tools\parallel\Invoke-MentatDirectReport.ps1')) {
    $tokens = $null; $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot $relative),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw "PowerShell parse failure in ${relative}: $errors" }
  }
  Write-Output 'Test-MentatReportDirectory: PASS'
} finally {
  if ($null -eq $prior) { Remove-Item Env:MENTAT_REPORT_DIRECTORY -ErrorAction SilentlyContinue }
  else { $env:MENTAT_REPORT_DIRECTORY = $prior }
  $resolved = [IO.Path]::GetFullPath($testRoot)
  if ($resolved.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()),[StringComparison]::OrdinalIgnoreCase)) {
    Remove-Item -LiteralPath $resolved -Recurse -Force -ErrorAction SilentlyContinue
  }
}
