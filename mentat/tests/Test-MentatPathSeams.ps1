$ErrorActionPreference = 'Stop'
# The PowerShell launchers' Rscript and R-library defaults: today's Windows value when MENTAT_RSCRIPT / MENTAT_R_LIBRARY are
# unset, the variable when set (containers). Each default is read from the script's own param block and evaluated, never run.
$projectRoot = Split-Path -Parent $PSScriptRoot
$windows = @{
  RScriptPath  = @{ Name = 'MENTAT_RSCRIPT';   Default = 'C:\Program Files\R\R-4.6.0\bin\Rscript.exe' }
  RLibraryPath = @{ Name = 'MENTAT_R_LIBRARY'; Default = 'C:\Users\pgoff\AppData\Local\R\win-library\4.6' }
}
$scripts = @(
  'tools\build_industry_report.ps1', 'tools\generate_crypto_report.ps1',
  'tools\parallel\Start-MentatIndustryReports.ps1', 'tools\parallel\Start-MentatResilientIndustryReports.ps1',
  'tools\parallel\Start-MentatApiCompanyCycleFanout.ps1', 'tools\parallel\Invoke-MentatEmployerPartition.ps1',
  'tools\parallel\Invoke-MentatDirectReport.ps1', 'tools\parallel\Invoke-MentatCompanyShardFromCopy.ps1',
  'tools\parallel\Invoke-MentatCompanyShard.ps1')
$prior = @{}
foreach ($entry in $windows.Values) { $prior[$entry.Name] = [Environment]::GetEnvironmentVariable($entry.Name) }
$checked = 0
try {
  foreach ($relative in $scripts) {
    $tokens = $null; $errors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot $relative), [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw "PowerShell parse failure in ${relative}: $errors" }
    foreach ($parameter in $ast.ParamBlock.Parameters) {
      $parameterName = $parameter.Name.VariablePath.UserPath
      if (-not $windows.ContainsKey($parameterName)) { continue }
      $entry = $windows[$parameterName]
      $expression = $parameter.DefaultValue.Extent.Text
      Remove-Item "Env:$($entry.Name)" -ErrorAction SilentlyContinue
      $unset = Invoke-Expression $expression
      if ($unset -ne $entry.Default) { throw "$relative -${parameterName}: default changed to '$unset' (expected '$($entry.Default)')." }
      [Environment]::SetEnvironmentVariable($entry.Name, '/container/value')
      $set = Invoke-Expression $expression
      if ($set -ne '/container/value') { throw "$relative -${parameterName}: $($entry.Name) did not move the default (got '$set')." }
      [Environment]::SetEnvironmentVariable($entry.Name, '')
      $empty = Invoke-Expression $expression
      if ($empty -ne $entry.Default) { throw "$relative -${parameterName}: an empty $($entry.Name) must fall back to the Windows default (got '$empty')." }
      $checked++
    }
  }
  if ($checked -ne 15) { throw "Expected 15 launcher defaults, checked $checked." }
}
finally {
  foreach ($name in $prior.Keys) { [Environment]::SetEnvironmentVariable($name, $prior[$name]) }
}
'PASS: {0} launcher defaults unchanged on Windows and moved by their variables' -f $checked
