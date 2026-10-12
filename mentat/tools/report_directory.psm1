function Resolve-MentatReportDirectory {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [string]$OutputDirectory = ''
  )
  $configured = [string]$env:MENTAT_REPORT_DIRECTORY
  if ([string]::IsNullOrWhiteSpace($configured)) {
    $configPath = Join-Path $ProjectRoot 'config\report-directory.json'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) {
      throw 'The authoritative report directory is not configured.'
    }
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    if ($config.schema_version -ne 1) { throw 'Unsupported report-directory configuration.' }
    $configured = [string]$config.directory
  }
  if ([string]::IsNullOrWhiteSpace($configured) -or -not [IO.Path]::IsPathRooted($configured)) {
    throw 'The report directory must be an explicit absolute path.'
  }
  if (-not (Test-Path -LiteralPath $configured -PathType Container)) {
    throw 'The configured report directory is unavailable; no alternate directory will be used.'
  }
  $resolved = (Resolve-Path -LiteralPath $configured).ProviderPath.TrimEnd('\','/')
  if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $requested = [IO.Path]::GetFullPath($OutputDirectory).TrimEnd('\','/')
    if (-not [string]::Equals($requested, $resolved, [StringComparison]::OrdinalIgnoreCase)) {
      throw "OutputDirectory conflicts with the authoritative report directory: $resolved. Remove the old override, or set MENTAT_REPORT_DIRECTORY explicitly for an isolated run."
    }
  }
  return $resolved
}

Export-ModuleMember -Function Resolve-MentatReportDirectory
