<#
.SYNOPSIS
Run the cookie-cutter Mentat report pipeline for roster-discovered industries.

.DESCRIPTION
This is the failure-isolated production orchestrator. For each industry it:

  1. initializes a fresh run-local SQLite database;
  2. delegates collection to Start-MentatApiCompanyCycleFanout.ps1;
  3. shares safe worker capacity between collection and recovery;
  4. runs deterministic merge and company x cycle coverage gates;
  5. exports and verifies every raw contribution by industry/cycle/company;
  6. starts the workbook generator immediately after those gates.

Collection uses every safe API/RAM lane for one industry at a time. Workbook
generation from completed industries overlaps collection of the next industry,
so the Excel stage is pipelined rather than held until all industries finish.
No workbook generator can see or publish a partial collection database.

All state is persistent under one run directory: manifest.json, status.json,
events.jsonl, per-industry fanout/coverage/raw-data receipts, and process logs.
The content-addressed raw warehouse is durable under RawDataDirectory. API keys
are inherited from FEC_API_KEY/FEC_API_KEYS or read by the fanout from KeysFile;
they are never written into a spec, command line, log, or receipt here.

.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\Start-MentatResilientIndustryReports.ps1

.EXAMPLE
powershell.exe -NoProfile -ExecutionPolicy Bypass -File `
  .\Start-MentatResilientIndustryReports.ps1 `
  -Industries Fintech,Crypto -KeysFile C:\secure\fec-keys.txt
#>
[CmdletBinding()]
param(
  [string[]]$Industries = @(),
  [ValidatePattern(
    '^(?:2021-2022|2022|2023-2024|2024|2025-2026|2026)' +
    '(?:\|(?:2021-2022|2022|2023-2024|2024|2025-2026|2026))*$'
  )]
  [string]$Cycles = '2022|2024|2026',
  [ValidateRange(0, 64)]
  [int]$MaxParallel = 0,
  [ValidateRange(0, 64)]
  [int]$RecoveryWorkers = 0,
  [ValidateRange(1, 8)]
  [int]$MaxGeneratorWorkers = 3,
  [ValidateRange(50, 95)]
  [int]$RamBudgetPercent = 92,
  [ValidateRange(0.5, 8.0)]
  [double]$RamPerApiLaneGB = 0.625,
  [ValidateRange(0.5, 8.0)]
  [double]$RamPerGeneratorGB = 1.5,
  [ValidateRange(0, 4096)]
  [int]$FanoutMemoryPerLaneMB = 0,
  [ValidateRange(0, 32768)]
  [int]$NodeHeapMb = 0,
  [ValidateRange(1, 45)]
  [double]$GeneratorPerformanceBudgetMinutes = 15,
  [ValidateRange(2, 64)]
  [int]$MergeBatchLimit = 10,
  [string]$KeysFile = '',
  [string]$OutputDirectory = '',
  [string]$RawDataDirectory = '',
  [string]$RunRoot = '',
  # Continue the run folder named here instead of creating a new one: its
  # logs, events, industry databases and fanout manifests are all reused.
  [ValidatePattern('^$|^\d{8}T\d{6}-\d+$')]
  [string]$ResumeRunId = '',
  [string]$RosterPath = '',
  [string]$GeneratorPath = '',
  [string]$RScriptPath =
    'C:\Program Files\R\R-4.6.0\bin\Rscript.exe',
  [string]$RLibraryPath =
    'C:\Users\pgoff\AppData\Local\R\win-library\4.6',
  [string]$PythonPath = '',
  [string]$ExcelMutexName = 'Global\MentatIndustryExcelFinalize',
  [switch]$NoCache,
  [ValidateRange(0,10000)]
  [int]$CrossReferenceMaxRequests = 100,
  [switch]$CrossReferenceOffline,
  [switch]$PlanOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$managedProcessNullExitCode = -9999

$parallelRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsRoot = Split-Path -Parent $parallelRoot
$projectRoot = Split-Path -Parent $toolsRoot
$workspaceRoot = Split-Path -Parent $projectRoot
$fanoutPath = Join-Path $parallelRoot 'Start-MentatApiCompanyCycleFanout.ps1'
$mentatEntry = Join-Path $projectRoot 'mentat.R'
$coverageTool = Join-Path $toolsRoot 'validate_industry_coverage.py'
$rawExportTool = Join-Path $toolsRoot 'export_raw_contribution_warehouse.py'
$powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source

function Resolve-FullPath {
  param([string]$Path, [string]$BasePath)
  if ([string]::IsNullOrWhiteSpace($Path)) { return '' }
  if ([IO.Path]::IsPathRooted($Path)) {
    return [IO.Path]::GetFullPath($Path)
  }
  return [IO.Path]::GetFullPath((Join-Path $BasePath $Path))
}

function ConvertTo-Slug {
  param([Parameter(Mandatory = $true)][string]$Value)
  $slug = [Text.RegularExpressions.Regex]::Replace(
    $Value.ToLowerInvariant(),
    '[^a-z0-9]+',
    '-'
  ).Trim('-')
  if ([string]::IsNullOrWhiteSpace($slug)) {
    throw "Industry name cannot be converted to a safe slug: $Value"
  }
  return $slug
}

function ConvertTo-PowerShellLiteral {
  param([AllowEmptyString()][string]$Value)
  return "'$($Value.Replace("'", "''"))'"
}

function New-ScriptInvocation {
  param(
    [Parameter(Mandatory = $true)][string]$ScriptPath,
    [Parameter(Mandatory = $true)][Collections.IDictionary]$Parameters
  )
  $parts = New-Object Collections.Generic.List[string]
  $parts.Add('& ' + (ConvertTo-PowerShellLiteral $ScriptPath))
  foreach ($key in $Parameters.Keys) {
    $value = $Parameters[$key]
    if ($value -is [bool]) {
      if ($value) { $parts.Add("-$key") }
      continue
    }
    if ($null -eq $value) { continue }
    if ([string]::IsNullOrWhiteSpace([string]$value)) { continue }
    $parts.Add("-$key " + (ConvertTo-PowerShellLiteral ([string]$value)))
  }
  $invoke = $parts -join ' '
  return (
    '$ErrorActionPreference=''Stop''; try { ' +
    $invoke +
    '; if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }; exit 0 } ' +
    'catch { [Console]::Error.WriteLine($_.Exception.ToString()); exit 1 }'
  )
}

function Write-AtomicJson {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value,
    [switch]$Telemetry
  )
  $directory = Split-Path -Parent $Path
  [IO.Directory]::CreateDirectory($directory) | Out-Null
  $pending = Join-Path $directory (
    '.{0}.{1}.{2}.pending' -f
      [IO.Path]::GetFileName($Path),
      $PID,
      [Guid]::NewGuid().ToString('N')
  )
  $encoding = New-Object Text.UTF8Encoding($false)
  [IO.File]::WriteAllText(
    $pending,
    ($Value | ConvertTo-Json -Depth 20),
    $encoding
  )
  try {
    for ($attempt = 1; $attempt -le 10; $attempt++) {
      $backup = $null
      try {
        if (Test-Path -LiteralPath $Path) {
          $backup = "$Path.backup-$PID-$([Guid]::NewGuid().ToString('N'))"
          [IO.File]::Replace($pending, $Path, $backup, $true)
        } else {
          [IO.File]::Move($pending, $Path)
        }
        if ($Telemetry) { return $true }
        return
      } catch {
        $exception = $_.Exception
        $isIoException = (
          $exception -is [IO.IOException] -or
          $exception.InnerException -is [IO.IOException]
        )
        if (-not $isIoException) { throw }
        if ($attempt -eq 10) {
          if ($Telemetry) { return $false }
          throw
        }
        $delayMilliseconds = [int][math]::Min(
          200,
          5 * [math]::Pow(2, $attempt - 1)
        )
        Start-Sleep -Milliseconds $delayMilliseconds
      } finally {
        if ($backup -and (Test-Path -LiteralPath $backup)) {
          try { [IO.File]::Delete($backup) } catch {}
        }
      }
    }
  } finally {
    if (Test-Path -LiteralPath $pending) {
      try { [IO.File]::Delete($pending) } catch {}
    }
  }
}

function Get-LastNonemptyLogLine {
  param([string[]]$Paths)
  $existing = @(
    $Paths |
      Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } |
      Sort-Object { (Get-Item -LiteralPath $_).LastWriteTimeUtc } -Descending
  )
  foreach ($path in $existing) {
    $lines = @(
      Get-Content -LiteralPath $path -Tail 64 |
        Where-Object {
          -not [string]::IsNullOrWhiteSpace([string]$_)
        }
    )
    if ($lines.Count) {
      return ([string]$lines[$lines.Count - 1]).Trim()
    }
  }
  return ''
}

function Format-ManagedExitCode {
  param([Parameter(Mandatory = $true)][int]$ExitCode)
  if ($ExitCode -eq $managedProcessNullExitCode) {
    return (
      'null (PowerShell 5.1 process ExitCode unavailable; ' +
      "sentinel=$managedProcessNullExitCode)"
    )
  }
  return [string]$ExitCode
}

function Write-RInvocationReceipt {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Industry,
    [Parameter(Mandatory = $true)][string]$Unit,
    [Parameter(Mandatory = $true)][int]$ExitCode,
    [Parameter(Mandatory = $true)][double]$ElapsedSeconds,
    [Parameter(Mandatory = $true)][string[]]$LogPaths
  )
  $lastLine = Get-LastNonemptyLogLine $LogPaths
  $exitCodeDescription = Format-ManagedExitCode $ExitCode
  Write-AtomicJson $Path ([ordered]@{
    industry = $Industry
    unit = $Unit
    exit_code = $ExitCode
    exit_code_description = $exitCodeDescription
    elapsed_seconds = [math]::Round($ElapsedSeconds, 3)
    last_log_line = $lastLine
    log_paths = $LogPaths
    rscript_path = $RScriptPath
    recorded_at = [DateTimeOffset]::Now.ToString('o')
  })
  Write-RunEvent 'rscript_invocation_completed' $Industry (
    "unit=$Unit; exit=$exitCodeDescription; elapsed=" +
    "$([math]::Round($ElapsedSeconds,3)); last_log_line=$lastLine"
  )
}

function Get-KeyCount {
  param([string]$Path)
  $values = @()
  if (-not [string]::IsNullOrWhiteSpace($Path)) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
      throw "FEC key file is missing: $Path"
    }
    $values = @(
      Get-Content -LiteralPath $Path |
        ForEach-Object { $_.Trim() } |
        Where-Object {
          -not [string]::IsNullOrWhiteSpace($_) -and
          -not $_.StartsWith('#')
        }
    )
  }
  if (-not [string]::IsNullOrWhiteSpace($env:FEC_API_KEYS)) {
    $values = @($values) + @(
      [Text.RegularExpressions.Regex]::Split(
        $env:FEC_API_KEYS,
        '[,;|\r\n]+'
      ) |
        ForEach-Object { $_.Trim() } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
  }
  if (-not [string]::IsNullOrWhiteSpace($env:FEC_API_KEY)) {
    $values = @($values) + @($env:FEC_API_KEY.Trim())
  }
  $environmentValues = [Environment]::GetEnvironmentVariables()
  $numberedPairs = @(
    foreach ($name in $environmentValues.Keys) {
      $textName = [string]$name
      if ($textName -match '^FEC_API_KEY_(\d+)$') {
        [pscustomobject]@{
          ordinal = [int]$Matches[1]
          value = ([string]$environmentValues[$name]).Trim()
        }
      }
    }
  )
  $numbered = @(
    $numberedPairs |
      Sort-Object ordinal |
      ForEach-Object { $_.value } |
      Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
  )
  if ($numbered.Count) {
    $values = @($values) + $numbered
  }
  $count = @($values | Select-Object -Unique).Count
  if ($count -eq 0) {
    $count = & $PythonPath (Join-Path $projectRoot 'tools\fec_key_store.py') --count
    if ($LASTEXITCODE -ne 0) { throw 'Unable to read the encrypted FEC key store.' }
  }
  return [int]$count
}

function Get-ResourcePlan {
  param(
    [int]$KeyCount,
    [int]$RequestedParallel,
    [int]$BudgetPercent,
    [double]$LaneGB,
    [int]$MergeBatchLimit
  )
  $totalBytes = 0.0
  $freeBytes = 0.0
  try {
    $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
    $totalBytes = [double]$os.TotalVisibleMemorySize * 1KB
    $freeBytes = [double]$os.FreePhysicalMemory * 1KB
  } catch {
    try {
      Add-Type -AssemblyName Microsoft.VisualBasic -ErrorAction Stop
      $computerInfo = [Microsoft.VisualBasic.Devices.ComputerInfo]::new()
      $totalBytes = [double]$computerInfo.TotalPhysicalMemory
      $freeBytes = [double]$computerInfo.AvailablePhysicalMemory
    } catch {
      # If both APIs are unavailable, the key count remains the hard cap.
    }
  }
  $usedBytes = [math]::Max(0.0, $totalBytes - $freeBytes)
  $targetBytes = $totalBytes * ($BudgetPercent / 100.0)
  # Recompute this plan immediately before every fanout so live available
  # memory reflects only generators that are actually running. Reserving every
  # configured generator here would strand RAM before those processes exist
  # and can collapse a multi-key fanout to one lane.
  $laneHeadroom = $targetBytes - $usedBytes
  $ramCap = if ($totalBytes -gt 0) {
    [math]::Max(1, [math]::Floor($laneHeadroom / ($LaneGB * 1GB)))
  } else {
    $KeyCount
  }
  $requested = if ($RequestedParallel -gt 0) {
    $RequestedParallel
  } else {
    $KeyCount
  }
  $logicalCpu = [math]::Max(1, [Environment]::ProcessorCount)
  # API lanes are remote-I/O bound. Report logical CPUs as telemetry, but let
  # the key, RAM, and request limits govern concurrency. The shard merge now
  # batches instead of attaching every shard at once, so it no longer caps
  # lane count here; it is still reported below for telemetry.
  $effective = [math]::Max(
    1,
    [math]::Min(
      $KeyCount,
      [math]::Min(
        $requested,
        [int]$ramCap
      )
    )
  )
  return [ordered]@{
    key_count = $KeyCount
    requested_api_lanes = $RequestedParallel
    effective_api_lanes = [int]$effective
    fanout_max_parallel = [int]$effective
    allocation_mode = 'live_recomputed_enforced'
    ram_budget_percent = $BudgetPercent
    ram_per_api_lane_gb = $LaneGB
    total_ram_gb = if ($totalBytes -gt 0) {
      [math]::Round($totalBytes / 1GB, 3)
    } else { $null }
    free_ram_gb = if ($freeBytes -gt 0) {
      [math]::Round($freeBytes / 1GB, 3)
    } else { $null }
    ram_lane_cap = [int]$ramCap
    logical_cpu_telemetry = [int]$logicalCpu
    cpu_bound_lane_cap_applied = $false
    sqlite_merge_cap = $MergeBatchLimit
  }
}

function Resolve-ManagedLogBase {
  param(
    [Parameter(Mandatory = $true)][string]$Directory,
    [Parameter(Mandatory = $true)][string]$Stage
  )
  # Start-ManagedPowerShell refuses to reuse a log path, and a resumed run
  # keeps the run folder the stopped attempt logged into. Hand this attempt
  # its own pair beside those logs rather than overwriting the evidence of
  # the attempt being resumed: <stage>.stdout.log, then
  # <stage>.resume-1.stdout.log, <stage>.resume-2.stdout.log, and so on.
  $base = Join-Path $Directory $Stage
  if (-not (Test-Path -LiteralPath "$base.stdout.log") -and
      -not (Test-Path -LiteralPath "$base.stderr.log")) {
    return $base
  }
  $attempt = 1
  while (
    (Test-Path -LiteralPath "$base.resume-$attempt.stdout.log") -or
    (Test-Path -LiteralPath "$base.resume-$attempt.stderr.log")
  ) {
    $attempt++
  }
  return "$base.resume-$attempt"
}

function Start-ManagedPowerShell {
  param(
    [Parameter(Mandatory = $true)][string]$Command,
    [Parameter(Mandatory = $true)][string]$StdoutPath,
    [Parameter(Mandatory = $true)][string]$StderrPath,
    [switch]$InheritFecKeys
  )
  foreach ($path in @($StdoutPath, $StderrPath)) {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
    if (Test-Path -LiteralPath $path) {
      throw "Managed process log already exists: $path"
    }
  }
  $encoded = [Convert]::ToBase64String(
    [Text.Encoding]::Unicode.GetBytes($Command)
  )
  $savedFecEnvironment = [ordered]@{}
  if (-not $InheritFecKeys) {
    foreach ($item in Get-ChildItem Env:) {
      if ($item.Name -notmatch '^FEC_API_KEYS?$|^FEC_API_KEY_\d+$') {
        continue
      }
      $savedFecEnvironment[$item.Name] = $item.Value
      Remove-Item -LiteralPath "Env:$($item.Name)"
    }
  }
  try {
    $process = Start-Process `
      -FilePath $powerShellPath `
      -ArgumentList @(
        '-NoLogo',
        '-NoProfile',
        '-NonInteractive',
        '-ExecutionPolicy', 'Bypass',
        '-EncodedCommand', $encoded
      ) `
      -WindowStyle Hidden `
      -RedirectStandardOutput $StdoutPath `
      -RedirectStandardError $StderrPath `
      -PassThru
  } finally {
    foreach ($name in $savedFecEnvironment.Keys) {
      Set-Item -LiteralPath "Env:$name" -Value $savedFecEnvironment[$name]
    }
  }
  # Windows PowerShell 5.1 can return $null from ExitCode for a child that
  # exits before its Process handle is first accessed. Cache the handle now;
  # removing this line makes fast managed steps appear to fail with exit=.
  $null = $process.Handle
  return $process
}

Import-Module (Join-Path $toolsRoot 'report_directory.psm1') -Force
$OutputDirectory = Resolve-MentatReportDirectory -ProjectRoot $projectRoot -OutputDirectory $OutputDirectory
$env:MENTAT_REPORT_DIRECTORY = $OutputDirectory
if (-not $RawDataDirectory) {
  $RawDataDirectory = Join-Path $workspaceRoot 'outputs\raw-contributions'
}
if (-not $RunRoot) {
  $RunRoot = Join-Path $projectRoot '.cache\resilient-industry-runs'
}
if (-not $GeneratorPath) {
  $GeneratorPath = Join-Path $toolsRoot 'generate_industry_report.ps1'
}
if (-not $RosterPath) {
  $RosterPath = Join-Path $projectRoot 'inputs\Company Name.xlsx'
}
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
$OutputDirectory = Resolve-FullPath $OutputDirectory $workspaceRoot
$RawDataDirectory = Resolve-FullPath $RawDataDirectory $workspaceRoot
$RunRoot = Resolve-FullPath $RunRoot $projectRoot
$RosterPath = Resolve-FullPath $RosterPath $projectRoot
$GeneratorPath = Resolve-FullPath $GeneratorPath $toolsRoot
$RScriptPath = Resolve-FullPath $RScriptPath $workspaceRoot
$RLibraryPath = Resolve-FullPath $RLibraryPath $workspaceRoot
$PythonPath = Resolve-FullPath $PythonPath $workspaceRoot
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
  throw (
    "Python runtime is missing at '$PythonPath'. Pass -PythonPath, set " +
    'MENTAT_PYTHON_PATH, or install Python 3 with python on PATH.'
  )
}
if ($KeysFile) {
  $KeysFile = Resolve-FullPath $KeysFile $workspaceRoot
}

foreach ($required in @(
  $fanoutPath,
  $mentatEntry,
  $coverageTool,
  $rawExportTool,
  $RosterPath,
  $GeneratorPath,
  $RScriptPath,
  $RLibraryPath,
  $PythonPath
)) {
  if (-not (Test-Path -LiteralPath $required)) {
    throw "Required resilient-report input is missing: $required"
  }
}

$rosterOutput = & $PythonPath $coverageTool --roster $RosterPath
if ($LASTEXITCODE -ne 0) {
  throw "Roster readiness validation failed for $RosterPath"
}
try {
  $rosterValidation = ($rosterOutput -join "`n") |
    ConvertFrom-Json -ErrorAction Stop
} catch {
  throw "Roster readiness validation returned invalid JSON: $RosterPath"
}
if ([string]$rosterValidation.status -ne 'succeeded') {
  throw "Roster readiness validation did not pass: $RosterPath"
}
$discoveredIndustries = @(
  $rosterValidation.industries |
    ForEach-Object { ([string]$_).Trim() } |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
)
if (-not $Industries.Count) {
  $Industries = $discoveredIndustries
}

$cleanIndustries = @(
  $Industries |
    ForEach-Object {
      if ($null -ne $_) {
        $_ -split ',' | ForEach-Object { $_.Trim() }
      }
    } |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    Select-Object -Unique
)
if (-not $cleanIndustries.Count) {
  throw 'At least one industry is required.'
}
$unknownIndustries = @(
  $cleanIndustries | Where-Object { $_ -notin $discoveredIndustries }
)
if ($unknownIndustries.Count) {
  throw (
    'Requested industries are absent from the roster workbook: ' +
    ($unknownIndustries -join ', ')
  )
}
$slugs = @($cleanIndustries | ForEach-Object { ConvertTo-Slug $_ })
if (@($slugs | Select-Object -Unique).Count -ne $slugs.Count) {
  throw 'Industry names collide after safe-slug conversion.'
}

$keyCount = Get-KeyCount $KeysFile
if ($keyCount -lt 1) {
  throw (
    'No FEC API keys were found. Set FEC_API_KEY/FEC_API_KEYS or supply ' +
    '-KeysFile with one key per line.'
  )
}
$canonicalFanoutMemoryPerLaneMB = if ($FanoutMemoryPerLaneMB -gt 0) {
  $FanoutMemoryPerLaneMB
} else {
  [int][math]::Ceiling($RamPerApiLaneGB * 1024)
}
if ($canonicalFanoutMemoryPerLaneMB -lt 128) {
  throw 'FanoutMemoryPerLaneMB must be zero (derived) or at least 128.'
}
$canonicalRamPerApiLaneGB = $canonicalFanoutMemoryPerLaneMB / 1024.0
$resourcePlan = Get-ResourcePlan `
  -KeyCount $keyCount `
  -RequestedParallel $MaxParallel `
  -BudgetPercent $RamBudgetPercent `
  -LaneGB $canonicalRamPerApiLaneGB `
  -MergeBatchLimit $MergeBatchLimit
$resourcePlan.fanout_memory_per_lane_mb = $canonicalFanoutMemoryPerLaneMB
$resourcePlan.ram_per_generator_gb = $RamPerGeneratorGB
$resourcePlan.generator_workers = $MaxGeneratorWorkers

$plan = [ordered]@{
  industries = $cleanIndustries
  cycles = $Cycles
  resume_run_id = $ResumeRunId
  strategy = (
    'all safe API lanes collect one industry; completed-industry workbook ' +
    'generators overlap the next collection'
  )
  recovery_workers = $RecoveryWorkers
  resources = $resourcePlan
  runtimes = [ordered]@{
    powershell_path = $powerShellPath
    python_path = $PythonPath
    rscript_path = $RScriptPath
    r_library_path = $RLibraryPath
  }
  generator_path = $GeneratorPath
  node_heap_mb = $NodeHeapMb
  generator_performance_budget_minutes = $GeneratorPerformanceBudgetMinutes
  cross_reference_max_requests = [int]$CrossReferenceMaxRequests
  cross_reference_offline = [bool]$CrossReferenceOffline
  fanout_path = $fanoutPath
  coverage_gate = $coverageTool
  raw_data_export = $rawExportTool
  roster_path = $RosterPath
  discovered_industries = $discoveredIndustries
  output_directory = $OutputDirectory
  raw_data_directory = $RawDataDirectory
}
if ($PlanOnly) {
  $plan | ConvertTo-Json -Depth 10
  exit 0
}

[IO.Directory]::CreateDirectory($OutputDirectory) | Out-Null
[IO.Directory]::CreateDirectory($RawDataDirectory) | Out-Null
[IO.Directory]::CreateDirectory($RunRoot) | Out-Null
$resumingRun = -not [string]::IsNullOrWhiteSpace($ResumeRunId)
if ($resumingRun) {
  # The run id is kept: the saved industry databases hold the verified work of
  # the attempt that stopped, and the fanout manifests beside them are what a
  # fanout resume reloads its lane snapshots from.
  $runId = $ResumeRunId
  $runDirectory = Join-Path $RunRoot $runId
  if (-not (Test-Path -LiteralPath $runDirectory -PathType Container)) {
    throw "ResumeRunId names no run folder under RunRoot: $runDirectory"
  }
} else {
  $runId = '{0}-{1}' -f (Get-Date -Format 'yyyyMMddTHHmmss'), $PID
  $runDirectory = Join-Path $RunRoot $runId
}
$logDirectory = Join-Path $runDirectory 'logs'
$industryRoot = Join-Path $runDirectory 'industries'
foreach ($directory in @($runDirectory, $logDirectory, $industryRoot)) {
  [IO.Directory]::CreateDirectory($directory) | Out-Null
}
$manifestPath = Join-Path $runDirectory 'manifest.json'
$statusPath = Join-Path $runDirectory 'status.json'
$eventsPath = Join-Path $runDirectory 'events.jsonl'
$summaryPath = Join-Path $runDirectory 'summary.json'
$phaseTimingsPath = Join-Path $runDirectory 'phase-timings.json'
$orchestratorLog = Join-Path $logDirectory 'orchestrator.log'
$startedAt = [DateTimeOffset]::Now
if ($resumingRun -and (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
  try {
    $priorManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($null -ne $priorManifest.PSObject.Properties['started_at']) {
      $startedAt = [DateTimeOffset]::Parse([string]$priorManifest.started_at)
    }
  } catch {
    # A manifest this process cannot read is no reason to refuse the resume.
    # The run keeps its own start time and says so in the rewritten manifest.
  }
}
$wallWatch = [Diagnostics.Stopwatch]::StartNew()
$env:R_LIBS_USER = $RLibraryPath
try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat resilient run - $runId"
  Clear-Host
} catch {
  # Host color/title support is optional; logs/state remain authoritative.
}

$industryStates = New-Object Collections.Generic.List[object]
for ($index = 0; $index -lt $cleanIndustries.Count; $index++) {
  $industry = $cleanIndustries[$index]
  $slug = $slugs[$index]
  $root = Join-Path $industryRoot $slug
  $dataRoot = Join-Path $root 'data'
  $fanoutRoot = Join-Path $root 'fanout'
  $database = Join-Path $dataRoot 'mentat.db'
  $outputWorkbook = Join-Path $OutputDirectory (
    "$industry Industry Spending Overview.xlsx"
  )
  foreach ($directory in @($root, $dataRoot, $fanoutRoot)) {
    [IO.Directory]::CreateDirectory($directory) | Out-Null
  }
  $industryStates.Add([pscustomobject][ordered]@{
    industry = $industry
    slug = $slug
    ordinal = $index + 1
    stage = 'pending'
    status = 'pending'
    detail = ''
    started_at = $null
    finished_at = $null
    collection_seconds = $null
    fanout_seconds = $null
    merge_seconds = $null
    coverage_seconds = $null
    raw_data_seconds = $null
    warehouse_seconds = $null
    export_seconds = $null
    workbook_build_seconds = $null
    formula_seconds = $null
    finalize_seconds = $null
    generator_seconds = $null
    total_seconds = $null
    under_15_minutes = $null
    database = $database
    data_root = $dataRoot
    fanout_root = $fanoutRoot
    fanout_receipt = Join-Path $root 'fanout-receipt.json'
    coverage_receipt = Join-Path $root 'coverage-receipt.json'
    raw_data_receipt = Join-Path $root 'raw-data-receipt.json'
    generator_receipt = Join-Path $root 'generator-receipt.json'
    raw_data_root = $RawDataDirectory
    raw_snapshot_id = ''
    raw_current_manifest = ''
    raw_snapshot_manifest = ''
    output_workbook = $outputWorkbook
    workbook_sha256 = ''
  })
}

Write-AtomicJson $manifestPath ([ordered]@{
  run_id = $runId
  started_at = $startedAt.ToString('o')
  resumed_run = $resumingRun
  resumed_at = $(if ($resumingRun) { [DateTimeOffset]::Now.ToString('o') } else { $null })
  launcher_pid = $PID
  plan = $plan
  run_directory = $runDirectory
  status_path = $statusPath
  events_path = $eventsPath
  summary_path = $summaryPath
  phase_timings_path = $phaseTimingsPath
})

$collectionDurations = New-Object Collections.Generic.List[double]
$generatorDurations = New-Object Collections.Generic.List[double]
$pendingGenerators = New-Object Collections.ArrayList
$runningGenerators = New-Object Collections.ArrayList
$recoveryQueue = New-Object Collections.ArrayList
$completedGenerators = New-Object Collections.ArrayList
$activeRecovery = $null
$statusWriteFailures = 0
$eventWriteFailures = 0
$logWriteFailures = 0

function Write-TelemetryAppend {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Text
  )
  $encoding = New-Object Text.UTF8Encoding($false)
  for ($attempt = 1; $attempt -le 10; $attempt++) {
    try {
      [IO.File]::AppendAllText($Path, $Text, $encoding)
      return $true
    } catch {
      $exception = $_.Exception
      $isIoException = (
        $exception -is [IO.IOException] -or
        $exception.InnerException -is [IO.IOException]
      )
      if (-not $isIoException) { throw }
      if ($attempt -eq 10) { return $false }
      $delayMilliseconds = [int][math]::Min(
        200,
        5 * [math]::Pow(2, $attempt - 1)
      )
      Start-Sleep -Milliseconds $delayMilliseconds
    }
  }
  return $false
}

function Write-RunLog {
  param([Parameter(Mandatory = $true)][string]$Message)
  $line = '[{0}] {1}' -f (
    [DateTimeOffset]::Now.ToString('yyyy-MM-ddTHH:mm:ss.fffzzz')
  ), $Message
  $written = Write-TelemetryAppend $orchestratorLog (
    $line + [Environment]::NewLine
  )
  if (-not $written) {
    $script:logWriteFailures++
    Write-Warning (
      "Orchestrator log append remained locked after 10 attempts; " +
      "continuing run. path=$orchestratorLog"
    )
  }
  Write-Host $line
}

function Write-RunEvent {
  param(
    [Parameter(Mandatory = $true)][string]$Event,
    [string]$Industry = '',
    [string]$Detail = ''
  )
  $value = [ordered]@{
    at = [DateTimeOffset]::Now.ToString('o')
    event = $Event
    industry = $Industry
    detail = $Detail
  }
  $written = Write-TelemetryAppend $eventsPath (
    ($value | ConvertTo-Json -Compress) + [Environment]::NewLine
  )
  if (-not $written) {
    $script:eventWriteFailures++
    Write-Warning (
      "Run event append remained locked after 10 attempts; " +
      "continuing run. path=$eventsPath event=$Event"
    )
  }
}

function Read-LiveTextFile {
  param([Parameter(Mandatory = $true)][string]$Path)
  $stream = $null
  $reader = $null
  try {
    $share = [IO.FileShare](
      [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
    )
    $stream = [IO.File]::Open(
      $Path,
      [IO.FileMode]::Open,
      [IO.FileAccess]::Read,
      $share
    )
    $reader = [IO.StreamReader]::new(
      $stream,
      [Text.Encoding]::UTF8,
      $true
    )
    return $reader.ReadToEnd()
  } catch [IO.IOException] {
    return $null
  } catch [UnauthorizedAccessException] {
    return $null
  } finally {
    if ($null -ne $reader) {
      $reader.Dispose()
    } elseif ($null -ne $stream) {
      $stream.Dispose()
    }
  }
}

function Get-FanoutLiveState {
  param([string]$FanoutRoot)
  foreach ($candidate in @(
    (Join-Path $FanoutRoot 'status.json'),
    (Join-Path $FanoutRoot 'state.json')
  )) {
    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
      $content = Read-LiveTextFile $candidate
      if ($null -eq $content) { return $null }
      try {
        return $content | ConvertFrom-Json -ErrorAction Stop
      } catch {
        # Atomic replacement or a short read can make one poll unavailable.
        return $null
      }
    }
  }
  return $null
}

function Get-GeneratorView {
  $values = New-Object Collections.Generic.List[object]
  foreach ($job in @(
    @($pendingGenerators) +
    @($runningGenerators) +
    @($recoveryQueue) +
    @($completedGenerators)
  )) {
    $values.Add([ordered]@{
      industry = $job.industry
      attempt = $job.attempt
      lane = $job.lane
      status = $job.status
      started_at = $job.started_at
      finished_at = $job.finished_at
      output_workbook = $job.output_workbook
    })
  }
  if ($null -ne $activeRecovery) {
    $job = $activeRecovery
    $values.Add([ordered]@{
      industry = $job.industry
      attempt = $job.attempt
      lane = $job.lane
      status = $job.status
      started_at = $job.started_at
      finished_at = $job.finished_at
      output_workbook = $job.output_workbook
    })
  }
  return $values
}

function Write-RunStatus {
  param(
    [string]$Stage,
    [string]$CurrentIndustry = '',
    $FanoutState = $null
  )
  $remainingCollections = @(
    $industryStates | Where-Object {
      $_.stage -in @('pending', 'initializing', 'collecting')
    }
  ).Count
  $averageCollection = if ($collectionDurations.Count) {
    ($collectionDurations | Measure-Object -Average).Average
  } else { $null }
  $collectionEta = $null
  if ($null -ne $FanoutState -and
      $null -ne $FanoutState.PSObject.Properties['estimated_remaining_seconds'] -and
      $null -ne $FanoutState.estimated_remaining_seconds) {
    $collectionEta = [double]$FanoutState.estimated_remaining_seconds
  }
  if ($null -ne $averageCollection) {
    $afterCurrent = [math]::Max(
      0,
      $remainingCollections - $(if ($CurrentIndustry) { 1 } else { 0 })
    )
    $collectionEta = (
      $(if ($null -ne $collectionEta) { $collectionEta } else { 0 }) +
      $averageCollection * $afterCurrent
    )
  }
  $generatorPending = @(
    @($pendingGenerators) +
    @($runningGenerators) +
    @($recoveryQueue) +
    $(if ($null -ne $activeRecovery) { @($activeRecovery) } else { @() })
  ).Count
  $averageGenerator = if ($generatorDurations.Count) {
    ($generatorDurations | Measure-Object -Average).Average
  } else { $null }
  $generatorEta = if (($generatorPending -or $remainingCollections) -and $null -ne $averageGenerator) {
    $averageGenerator * [math]::Ceiling(
      ($generatorPending + $remainingCollections) / [double]$MaxGeneratorWorkers
    )
  } elseif ($generatorPending -or $remainingCollections) { $null } else { 0.0 }
  $overallEta = if ($null -eq $generatorEta -or ($remainingCollections -and $null -eq $collectionEta)) {
    $null
  } elseif ($null -ne $collectionEta) {
    [math]::Max($collectionEta, $generatorEta)
  } elseif ($generatorPending) {
    $generatorEta
  } else {
    $null
  }
  $written = Write-AtomicJson $statusPath ([ordered]@{
    run_id = $runId
    updated_at = [DateTimeOffset]::Now.ToString('o')
    stage = $Stage
    current_industry = $CurrentIndustry
    elapsed_seconds = [math]::Round($wallWatch.Elapsed.TotalSeconds, 3)
    estimated_remaining_seconds = if ($null -ne $overallEta) {
      [math]::Round($overallEta, 3)
    } else { $null }
    collection_eta_seconds = if ($null -ne $collectionEta) {
      [math]::Round($collectionEta, 3)
    } else { $null }
    generator_eta_seconds = if ($null -ne $generatorEta) { [math]::Round($generatorEta, 3) } else { $null }
    resources = $resourcePlan
    fanout = $FanoutState
    industries = $industryStates
    generators = Get-GeneratorView
    telemetry = [ordered]@{
      status_write_failures = $statusWriteFailures
      event_write_failures = $eventWriteFailures
      log_write_failures = $logWriteFailures
    }
  }) -Telemetry
  if (-not $written) {
    $script:statusWriteFailures++
    Write-Warning (
      "Run status remained locked after 10 atomic-write attempts; " +
      "continuing run. path=$statusPath failures=$statusWriteFailures"
    )
  }
}

function New-GeneratorJob {
  param(
    [Parameter(Mandatory = $true)]$IndustryState,
    [int]$Attempt = 1,
    [string]$Lane = 'normal'
  )
  return [pscustomobject][ordered]@{
    industry = $IndustryState.industry
    slug = $IndustryState.slug
    database = $IndustryState.database
    output_workbook = $IndustryState.output_workbook
    receipt_path = $IndustryState.generator_receipt
    receipt = $null
    attempt = $Attempt
    lane = $Lane
    status = 'queued'
    process = $null
    stopwatch = $null
    started_at = $null
    finished_at = $null
    stdout = ''
    stderr = ''
    error = ''
  }
}

function Start-GeneratorJob {
  param([Parameter(Mandatory = $true)]$Job)
  $jobRoot = Join-Path $industryRoot $Job.slug
  $prefix = 'generator-{0}-attempt-{1}' -f $Job.lane, $Job.attempt
  $jobLogBase = Resolve-ManagedLogBase $jobRoot $prefix
  $Job.stdout = "$jobLogBase.stdout.log"
  $Job.stderr = "$jobLogBase.stderr.log"
  $parameters = [ordered]@{
    Industry = $Job.industry
    DatabasePath = $Job.database
    OutputDirectory = $OutputDirectory
    RScriptPath = $RScriptPath
    ExcelMutexName = $ExcelMutexName
    NodeHeapMb = $NodeHeapMb
    PerformanceBudgetMinutes = $GeneratorPerformanceBudgetMinutes
    NoCache = [bool]$NoCache
    CrossReferenceMaxRequests = $CrossReferenceMaxRequests
    CrossReferenceOffline = [bool]$CrossReferenceOffline
  }
  $command = New-ScriptInvocation $GeneratorPath $parameters
  $Job.process = Start-ManagedPowerShell `
    -Command $command `
    -StdoutPath $Job.stdout `
    -StderrPath $Job.stderr
  $Job.stopwatch = [Diagnostics.Stopwatch]::StartNew()
  $Job.started_at = [DateTimeOffset]::Now.ToString('o')
  $Job.status = 'running'
  Write-RunEvent 'generator_started' $Job.industry (
    "attempt=$($Job.attempt); lane=$($Job.lane); pid=$($Job.process.Id)"
  )
  Write-RunLog (
    "Generator started: $($Job.industry) attempt=$($Job.attempt) " +
    "lane=$($Job.lane) pid=$($Job.process.Id)"
  )
}

function Start-QueuedGenerators {
  while (
    $pendingGenerators.Count -gt 0 -and
    $runningGenerators.Count -lt $MaxGeneratorWorkers
  ) {
    $job = $pendingGenerators[0]
    $pendingGenerators.RemoveAt(0)
    Start-GeneratorJob $job
    [void]$runningGenerators.Add($job)
  }
  if ($null -eq $activeRecovery -and $recoveryQueue.Count -gt 0) {
    $script:activeRecovery = $recoveryQueue[0]
    $recoveryQueue.RemoveAt(0)
    Start-GeneratorJob $script:activeRecovery
  }
}

function Read-GeneratorReceipt {
  param([Parameter(Mandatory = $true)][string]$Path)
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
  $lines = @(Get-Content -LiteralPath $Path)
  for ($index = $lines.Count - 1; $index -ge 0; $index--) {
    if (-not $lines[$index].TrimStart().StartsWith('{')) { continue }
    $candidate = ($lines[$index..($lines.Count - 1)] -join "`n")
    try {
      $value = $candidate | ConvertFrom-Json -ErrorAction Stop
      if ($null -ne $value.PSObject.Properties['industry'] -and
          $null -ne $value.PSObject.Properties['workbook'] -and
          $null -ne $value.PSObject.Properties['total_seconds']) {
        return $value
      }
    } catch {
      # Keep scanning backward for the final complete JSON object.
    }
  }
  return $null
}

function Complete-GeneratorJob {
  param([Parameter(Mandatory = $true)]$Job)
  $Job.process.Refresh()
  if (-not $Job.process.HasExited) { return $false }
  if ($Job.stopwatch.IsRunning) { $Job.stopwatch.Stop() }
  $Job.finished_at = [DateTimeOffset]::Now.ToString('o')
  $elapsed = [double]$Job.stopwatch.Elapsed.TotalSeconds
  $generatorDurations.Add($elapsed)
  $Job.process.WaitForExit()
  $reportedExitCode = $Job.process.ExitCode
  $exitCode = if ($null -eq $reportedExitCode) {
    [int]$managedProcessNullExitCode
  } else {
    [int]$reportedExitCode
  }
  $exitCodeDescription = Format-ManagedExitCode $exitCode
  $workbookExists = Test-Path -LiteralPath $Job.output_workbook -PathType Leaf
  $generatorReceipt = Read-GeneratorReceipt $Job.stdout
  $receiptValid = (
    $null -ne $generatorReceipt -and
    [string]::Equals(
      [IO.Path]::GetFullPath([string]$generatorReceipt.workbook),
      [IO.Path]::GetFullPath([string]$Job.output_workbook),
      [StringComparison]::OrdinalIgnoreCase
    )
  )
  if ($exitCode -eq 0 -and $workbookExists -and $receiptValid) {
    $Job.receipt = $generatorReceipt
    Write-AtomicJson $Job.receipt_path $generatorReceipt
    $Job.status = 'succeeded'
    [void]$completedGenerators.Add($Job)
    $state = $industryStates | Where-Object {
      $_.industry -eq $Job.industry
    } | Select-Object -First 1
    $state.stage = 'completed'
    $state.status = 'succeeded'
    $state.finished_at = $Job.finished_at
    $priorGeneratorSeconds = if ($null -eq $state.generator_seconds) {
      0.0
    } else { [double]$state.generator_seconds }
    $state.generator_seconds = [math]::Round(
      $priorGeneratorSeconds + $elapsed,
      3
    )
    if ($null -ne $generatorReceipt.PSObject.Properties['export_seconds']) {
      $state.export_seconds = [double]$generatorReceipt.export_seconds
    }
    if ($null -ne $generatorReceipt.PSObject.Properties['build_seconds']) {
      $state.workbook_build_seconds = [double]$generatorReceipt.build_seconds
    }
    if ($null -ne $generatorReceipt.PSObject.Properties['formula_seconds']) {
      $state.formula_seconds = [double]$generatorReceipt.formula_seconds
    }
    if ($null -ne $generatorReceipt.PSObject.Properties['finalize_seconds']) {
      $state.finalize_seconds = [double]$generatorReceipt.finalize_seconds
    }
    $state.detail = (
      'Generator receipt PASS: budget_pass={0}; receipt={1}' -f
        [bool]$generatorReceipt.performance_budget_pass,
        $Job.receipt_path
    )
    $state.workbook_sha256 = (
      Get-FileHash -LiteralPath $Job.output_workbook -Algorithm SHA256
    ).Hash
    Write-RunEvent 'generator_succeeded' $Job.industry (
      "attempt=$($Job.attempt); seconds=$([math]::Round($elapsed,3))"
    )
    Write-RunLog (
      "Generator succeeded: $($Job.industry) " +
      "seconds=$([math]::Round($elapsed,3))"
    )
  } elseif ($Job.attempt -lt 2) {
    $Job.status = 'queued_recovery'
    $state = $industryStates | Where-Object {
      $_.industry -eq $Job.industry
    } | Select-Object -First 1
    $priorGeneratorSeconds = if ($null -eq $state.generator_seconds) {
      0.0
    } else { [double]$state.generator_seconds }
    $state.generator_seconds = [math]::Round(
      $priorGeneratorSeconds + $elapsed,
      3
    )
    $retry = New-GeneratorJob `
      -IndustryState $state `
      -Attempt ($Job.attempt + 1) `
      -Lane 'recovery'
    [void]$recoveryQueue.Add($retry)
    Write-RunEvent 'generator_recovery_queued' $Job.industry (
      "exit=$exitCodeDescription; workbook_exists=$workbookExists; " +
      "receipt_valid=$receiptValid"
    )
    Write-RunLog (
      "Generator queued for the single recovery lane: $($Job.industry) " +
      "exit=$exitCodeDescription workbook_exists=$workbookExists " +
      "receipt_valid=$receiptValid"
    )
  } else {
    $Job.status = 'failed'
    $Job.error = (
      "generator exit=$exitCodeDescription workbook_exists=$workbookExists " +
      "receipt_valid=$receiptValid; " +
      "stderr=$($Job.stderr)"
    )
    [void]$completedGenerators.Add($Job)
    $state = $industryStates | Where-Object {
      $_.industry -eq $Job.industry
    } | Select-Object -First 1
    $state.stage = 'failed'
    $state.status = 'failed'
    $state.finished_at = $Job.finished_at
    $priorGeneratorSeconds = if ($null -eq $state.generator_seconds) {
      0.0
    } else { [double]$state.generator_seconds }
    $state.generator_seconds = [math]::Round(
      $priorGeneratorSeconds + $elapsed,
      3
    )
    $state.detail = $Job.error
    Write-RunEvent 'generator_failed' $Job.industry $Job.error
    Write-RunLog "Generator failed after recovery: $($Job.industry)"
  }
  return $true
}

function Update-GeneratorJobs {
  foreach ($job in @($runningGenerators)) {
    if (Complete-GeneratorJob $job) {
      [void]$runningGenerators.Remove($job)
    }
  }
  if ($null -ne $activeRecovery -and
      (Complete-GeneratorJob $activeRecovery)) {
    $script:activeRecovery = $null
  }
  Start-QueuedGenerators
}

function Wait-ManagedProcess {
  param(
    [Parameter(Mandatory = $true)]$Process,
    [Parameter(Mandatory = $true)][string]$Stage,
    [Parameter(Mandatory = $true)]$IndustryState,
    [string]$FanoutRoot = ''
  )
  $lastHeartbeat = [DateTimeOffset]::MinValue
  while ($true) {
    $Process.Refresh()
    Update-GeneratorJobs
    $live = if ($FanoutRoot) {
      Get-FanoutLiveState $FanoutRoot
    } else { $null }
    Write-RunStatus $Stage $IndustryState.industry $live
    if ($Process.HasExited) {
      $Process.WaitForExit()
      $reportedExitCode = $Process.ExitCode
      if ($null -eq $reportedExitCode) {
        return [int]$managedProcessNullExitCode
      }
      return [int]$reportedExitCode
    }
    if (([DateTimeOffset]::Now - $lastHeartbeat).TotalSeconds -ge 15) {
      $etaText = ''
      if ($null -ne $live -and
          $null -ne $live.PSObject.Properties['estimated_remaining_seconds']) {
        $etaText = '; fanout ETA={0:n1} min' -f (
          [double]$live.estimated_remaining_seconds / 60.0
        )
      }
      Write-RunLog (
        "${Stage}: $($IndustryState.industry); elapsed=" +
        "$([math]::Round($wallWatch.Elapsed.TotalMinutes,2)) min$etaText"
      )
      $lastHeartbeat = [DateTimeOffset]::Now
    }
    # Keep the status/heartbeat cadence, but release the next stage as soon as
    # this child exits instead of leaving completed work asleep for two seconds.
    [void]$Process.WaitForExit(2000)
  }
}

if ($resumingRun) {
  Write-RunEvent 'run_resumed' '' "run_id=$runId"
} else {
  Write-RunEvent 'run_started' '' "run_id=$runId"
}
Write-RunLog 'MENTAT RESILIENT INDUSTRY RUN'
Write-RunLog "Run ID: $runId"
Write-RunLog "Industries: $($cleanIndustries -join ', ')"
Write-RunLog (
  "API lanes: $($resourcePlan.effective_api_lanes)/$keyCount; " +
  "RAM budget: $RamBudgetPercent%; recovery lanes: $RecoveryWorkers"
)
Write-RunLog "Run folder: $runDirectory"
if ($resumingRun) {
  Write-RunLog (
    "Resuming run ${runId}: saved industry databases and lane state are " +
    'reused; logs and events are appended.'
  )
}
Write-RunStatus 'starting'

foreach ($state in $industryStates) {
  $state.started_at = [DateTimeOffset]::Now.ToString('o')
  $state.stage = 'initializing'
  $state.status = 'running'
  $industryRootPath = Join-Path $industryRoot $state.slug
  $cycleCsv = ($Cycles -split '\|') |
    ForEach-Object {
      switch ($_) {
        '2021-2022' { '2022' }
        '2023-2024' { '2024' }
        '2025-2026' { '2026' }
        default { $_ }
      }
    }
  # A fanout resume reloads lane databases whose fingerprint must still
  # match the source database its manifest recorded. Re-initializing would
  # write a new database and make every saved lane snapshot unusable.
  $resumeIndustry = (
    $resumingRun -and
    (Test-Path -LiteralPath $state.database -PathType Leaf) -and
    (Test-Path -LiteralPath (Join-Path $state.fanout_root 'manifest.json') -PathType Leaf)
  )
  if ($resumeIndustry) {
    Write-RunEvent 'industry_resuming' $state.industry
    Write-RunLog "Resuming $($state.industry): saved lane state found"
    Write-RunStatus 'initializing' $state.industry
  } else {
    Write-RunEvent 'industry_initializing' $state.industry
    Write-RunLog "Initializing fresh database: $($state.industry)"
    Write-RunStatus 'initializing' $state.industry
    $initLogBase = Resolve-ManagedLogBase $industryRootPath 'initialize'
    $initStdout = "$initLogBase.stdout.log"
    $initStderr = "$initLogBase.stderr.log"
    $initCommand = (
      '& ' + (ConvertTo-PowerShellLiteral $RScriptPath) + ' ' +
      (ConvertTo-PowerShellLiteral $mentatEntry) + ' ' +
      (ConvertTo-PowerShellLiteral "--industry=$($state.industry)") + ' ' +
      (ConvertTo-PowerShellLiteral "--cycles=$($cycleCsv -join ',')") + ' ' +
      (ConvertTo-PowerShellLiteral "--data-root=$($state.data_root)") + ' ' +
      (ConvertTo-PowerShellLiteral '--skip-scrape') + ' ' +
      (ConvertTo-PowerShellLiteral '--defer-report-files') +
      '; exit $LASTEXITCODE'
    )
    $initWatch = [Diagnostics.Stopwatch]::StartNew()
    $process = Start-ManagedPowerShell `
      -Command $initCommand `
      -StdoutPath $initStdout `
      -StderrPath $initStderr
    $exitCode = Wait-ManagedProcess $process 'initializing' $state
    $exitCodeDescription = Format-ManagedExitCode $exitCode
    $initWatch.Stop()
    Write-RInvocationReceipt `
      -Path (Join-Path $industryRootPath 'initialize.invocation.json') `
      -Industry $state.industry `
      -Unit 'initialize' `
      -ExitCode $exitCode `
      -ElapsedSeconds $initWatch.Elapsed.TotalSeconds `
      -LogPaths @($initStdout, $initStderr)
    if ($exitCode -ne 0 -or
        -not (Test-Path -LiteralPath $state.database -PathType Leaf)) {
      $state.stage = 'failed'
      $state.status = 'failed'
      $state.finished_at = [DateTimeOffset]::Now.ToString('o')
      $state.detail = (
        "Initialization failed: exit=$exitCodeDescription; stderr=$initStderr"
      )
      Write-RunEvent 'industry_failed' $state.industry $state.detail
      Write-RunLog $state.detail
      continue
    }
  }

  $state.stage = 'collecting'
  $collectionWatch = [Diagnostics.Stopwatch]::StartNew()
  Write-RunEvent 'fanout_started' $state.industry
  Write-RunLog "API company-cycle fanout started: $($state.industry)"
  $fanoutLogBase = Resolve-ManagedLogBase $industryRootPath 'fanout'
  $fanoutStdout = "$fanoutLogBase.stdout.log"
  $fanoutStderr = "$fanoutLogBase.stderr.log"
  $liveFanoutPlan = Get-ResourcePlan `
    -KeyCount $keyCount `
    -RequestedParallel $MaxParallel `
    -BudgetPercent $RamBudgetPercent `
    -LaneGB $canonicalRamPerApiLaneGB `
    -MergeBatchLimit $MergeBatchLimit
  Write-RunLog (
    "Live fanout starting workers: " +
    "$($liveFanoutPlan.effective_api_lanes)/$keyCount; " +
    "lane ceiling: $(if ($MaxParallel) { $MaxParallel } else { 'auto' }); " +
    "memory_per_lane_mb=$canonicalFanoutMemoryPerLaneMB"
  )
  $fanoutParameters = [ordered]@{
    SourceDatabasePath = $state.database
    Industry = $state.industry
    Cycles = $Cycles
    RunDirectory = $state.fanout_root
    OutputReceiptPath = $state.fanout_receipt
    # Lane ceiling stays at the operator's request (0 = structural auto max);
    # the live-recomputed RAM/key cap now only sets the starting worker count.
    MaxParallel = $MaxParallel
    InitialWorkers = $liveFanoutPlan.effective_api_lanes
    RecoveryWorkers = $RecoveryWorkers
    MemoryPerLaneMB = $canonicalFanoutMemoryPerLaneMB
    MergeBatchLimit = $MergeBatchLimit
    Resume = $resumeIndustry
    KeysFile = $KeysFile
    PythonPath = $PythonPath
    RScriptPath = $RScriptPath
    RLibraryPath = $RLibraryPath
  }
  $fanoutCommand = New-ScriptInvocation $fanoutPath $fanoutParameters
  $process = Start-ManagedPowerShell `
    -Command $fanoutCommand `
    -StdoutPath $fanoutStdout `
    -StderrPath $fanoutStderr `
    -InheritFecKeys
  $exitCode = Wait-ManagedProcess `
    -Process $process `
    -Stage 'collecting' `
    -IndustryState $state `
    -FanoutRoot $state.fanout_root
  $exitCodeDescription = Format-ManagedExitCode $exitCode
  $collectionWatch.Stop()
  $fanoutReceiptObject = $null
  if (Test-Path -LiteralPath $state.fanout_receipt -PathType Leaf) {
    try {
      $fanoutReceiptObject = Get-Content `
        -LiteralPath $state.fanout_receipt `
        -Raw |
        ConvertFrom-Json -ErrorAction Stop
    } catch {
      $fanoutReceiptObject = $null
    }
  }
  $fanoutSeconds = $collectionWatch.Elapsed.TotalSeconds
  $mergeSeconds = 0.0
  if ($null -ne $fanoutReceiptObject) {
    if ($null -ne $fanoutReceiptObject.PSObject.Properties['wall_clock_seconds']) {
      $fanoutSeconds = [double]$fanoutReceiptObject.wall_clock_seconds
    }
    if (
      $null -ne $fanoutReceiptObject.PSObject.Properties['merge'] -and
      $null -ne $fanoutReceiptObject.merge -and
      $null -ne $fanoutReceiptObject.merge.PSObject.Properties[
        'wall_clock_seconds'
      ]
    ) {
      $mergeSeconds = [double]$fanoutReceiptObject.merge.wall_clock_seconds
    }
  }
  $state.fanout_seconds = [math]::Round($fanoutSeconds, 3)
  $state.merge_seconds = [math]::Round($mergeSeconds, 3)
  $state.collection_seconds = [math]::Round(
    [math]::Max(0.0, $fanoutSeconds - $mergeSeconds),
    3
  )
  $collectionDurations.Add($collectionWatch.Elapsed.TotalSeconds)
  if ($exitCode -ne 0 -or
      -not (Test-Path -LiteralPath $state.fanout_receipt -PathType Leaf)) {
    $state.stage = 'failed'
    $state.status = 'failed'
    $state.finished_at = [DateTimeOffset]::Now.ToString('o')
    $state.detail = (
      "Fanout failed: exit=$exitCodeDescription; stderr=$fanoutStderr"
    )
    Write-RunEvent 'industry_failed' $state.industry $state.detail
    Write-RunLog $state.detail
    continue
  }

  $state.stage = 'finalizing_database'
  Write-RunEvent 'database_finalizing' $state.industry
  Write-RunLog "Retagging merged database: $($state.industry)"
  $retagLogBase = Resolve-ManagedLogBase $industryRootPath 'retag'
  $retagStdout = "$retagLogBase.stdout.log"
  $retagStderr = "$retagLogBase.stderr.log"
  $retagCommand = (
    '& ' + (ConvertTo-PowerShellLiteral $RScriptPath) + ' ' +
    (ConvertTo-PowerShellLiteral $mentatEntry) + ' ' +
    (ConvertTo-PowerShellLiteral "--industry=$($state.industry)") + ' ' +
    (ConvertTo-PowerShellLiteral "--cycles=$($cycleCsv -join ',')") + ' ' +
    (ConvertTo-PowerShellLiteral "--data-root=$($state.data_root)") + ' ' +
    (ConvertTo-PowerShellLiteral '--retag-only') + ' ' +
    (ConvertTo-PowerShellLiteral '--defer-report-files') +
    '; exit $LASTEXITCODE'
  )
  $retagWatch = [Diagnostics.Stopwatch]::StartNew()
  $process = Start-ManagedPowerShell `
    -Command $retagCommand `
    -StdoutPath $retagStdout `
    -StderrPath $retagStderr
  $exitCode = Wait-ManagedProcess $process 'finalizing_database' $state
  $exitCodeDescription = Format-ManagedExitCode $exitCode
  $retagWatch.Stop()
  Write-RInvocationReceipt `
    -Path (Join-Path $industryRootPath 'retag.invocation.json') `
    -Industry $state.industry `
    -Unit 'retag' `
    -ExitCode $exitCode `
    -ElapsedSeconds $retagWatch.Elapsed.TotalSeconds `
    -LogPaths @($retagStdout, $retagStderr)
  if ($exitCode -ne 0) {
    $state.stage = 'failed'
    $state.status = 'failed'
    $state.finished_at = [DateTimeOffset]::Now.ToString('o')
    $state.detail = (
      "Database finalization failed: exit=$exitCodeDescription"
    )
    Write-RunEvent 'industry_failed' $state.industry $state.detail
    Write-RunLog $state.detail
    continue
  }

  $state.stage = 'coverage_gate'
  $coverageWatch = [Diagnostics.Stopwatch]::StartNew()
  Write-RunEvent 'coverage_gate_started' $state.industry
  Write-RunLog "Coverage gate started: $($state.industry)"
  $coverageLogBase = Resolve-ManagedLogBase $industryRootPath 'coverage'
  $coverageStdout = "$coverageLogBase.stdout.log"
  $coverageStderr = "$coverageLogBase.stderr.log"
  $coverageCommand = (
    '& ' + (ConvertTo-PowerShellLiteral $PythonPath) + ' ' +
    (ConvertTo-PowerShellLiteral $coverageTool) + ' ' +
    (ConvertTo-PowerShellLiteral $state.database) + ' ' +
    (ConvertTo-PowerShellLiteral $state.industry) + ' ' +
    (ConvertTo-PowerShellLiteral $Cycles) + ' ' +
    (ConvertTo-PowerShellLiteral $state.fanout_receipt) + ' ' +
    (ConvertTo-PowerShellLiteral '--output') + ' ' +
    (ConvertTo-PowerShellLiteral $state.coverage_receipt) +
    '; exit $LASTEXITCODE'
  )
  $process = Start-ManagedPowerShell `
    -Command $coverageCommand `
    -StdoutPath $coverageStdout `
    -StderrPath $coverageStderr
  $exitCode = Wait-ManagedProcess $process 'coverage_gate' $state
  $exitCodeDescription = Format-ManagedExitCode $exitCode
  $coverageWatch.Stop()
  $state.coverage_seconds = [math]::Round(
    $coverageWatch.Elapsed.TotalSeconds,
    3
  )
  if ($exitCode -ne 0) {
    $state.stage = 'failed'
    $state.status = 'failed'
    $state.finished_at = [DateTimeOffset]::Now.ToString('o')
    $state.detail = (
      "Coverage gate failed: exit=$exitCodeDescription; " +
      "receipt=$($state.coverage_receipt)"
    )
    Write-RunEvent 'industry_failed' $state.industry $state.detail
    Write-RunLog $state.detail
    continue
  }

  $state.stage = 'raw_data_gate'
  $rawDataWatch = [Diagnostics.Stopwatch]::StartNew()
  Write-RunEvent 'raw_data_export_started' $state.industry
  Write-RunLog "Raw-data warehouse export started: $($state.industry)"
  $rawDataLogBase = Resolve-ManagedLogBase $industryRootPath 'raw-data'
  $rawDataStdout = "$rawDataLogBase.stdout.log"
  $rawDataStderr = "$rawDataLogBase.stderr.log"
  $rawDataCommand = (
    '& ' + (ConvertTo-PowerShellLiteral $PythonPath) + ' ' +
    (ConvertTo-PowerShellLiteral $rawExportTool) + ' ' +
    (ConvertTo-PowerShellLiteral $state.database) + ' ' +
    (ConvertTo-PowerShellLiteral $state.industry) + ' ' +
    (ConvertTo-PowerShellLiteral $Cycles) + ' ' +
    (ConvertTo-PowerShellLiteral '--output-root') + ' ' +
    (ConvertTo-PowerShellLiteral $RawDataDirectory) + ' ' +
    (ConvertTo-PowerShellLiteral '--receipt') + ' ' +
    (ConvertTo-PowerShellLiteral $state.raw_data_receipt) +
    '; exit $LASTEXITCODE'
  )
  $process = Start-ManagedPowerShell `
    -Command $rawDataCommand `
    -StdoutPath $rawDataStdout `
    -StderrPath $rawDataStderr
  $exitCode = Wait-ManagedProcess $process 'raw_data_gate' $state
  $exitCodeDescription = Format-ManagedExitCode $exitCode
  $rawDataWatch.Stop()
  $state.raw_data_seconds = [math]::Round(
    $rawDataWatch.Elapsed.TotalSeconds,
    3
  )
  $state.warehouse_seconds = $state.raw_data_seconds
  $rawReceipt = $null
  $coverageReceipt = $null
  try {
    if (Test-Path -LiteralPath $state.raw_data_receipt -PathType Leaf) {
      $rawReceipt = Get-Content `
        -LiteralPath $state.raw_data_receipt `
        -Raw |
        ConvertFrom-Json -ErrorAction Stop
    }
    $coverageReceipt = Get-Content `
      -LiteralPath $state.coverage_receipt `
      -Raw |
      ConvertFrom-Json -ErrorAction Stop
  } catch {
    $rawReceipt = $null
  }
  $rawReceiptValid = $false
  $rawContributionRows = [int64]0
  $rawCorporateRows = [int64]0
  try {
    $rawReceiptValid = (
      $exitCode -eq 0 -and
      $null -ne $rawReceipt -and
      [string]$rawReceipt.status -eq 'succeeded' -and
      -not [string]::IsNullOrWhiteSpace([string]$rawReceipt.snapshot_id) -and
      (Test-Path -LiteralPath ([string]$rawReceipt.current_manifest) -PathType Leaf) -and
      (Test-Path -LiteralPath ([string]$rawReceipt.snapshot_manifest) -PathType Leaf)
    )
    if ($rawReceiptValid) {
      $rawContributionRows = [int64]$rawReceipt.rows.contributions
      $rawCorporateRows = [int64]$rawReceipt.rows.corporate_giving
      $coverageContributionRows = [int64]$coverageReceipt.contributions.in_scope
      $coverageCorporateRows = [int64]$coverageReceipt.corporate_giving.in_scope
      $rawReceiptValid = (
        $rawContributionRows -eq $coverageContributionRows -and
        $rawCorporateRows -eq $coverageCorporateRows
      )
    }
  } catch {
    $rawReceiptValid = $false
  }
  if (-not $rawReceiptValid) {
    $state.stage = 'failed'
    $state.status = 'failed'
    $state.finished_at = [DateTimeOffset]::Now.ToString('o')
    $state.detail = (
      "Raw-data gate failed: exit=$exitCodeDescription; " +
      "receipt=$($state.raw_data_receipt)"
    )
    Write-RunEvent 'industry_failed' $state.industry $state.detail
    Write-RunLog $state.detail
    continue
  }
  $state.raw_snapshot_id = [string]$rawReceipt.snapshot_id
  $state.raw_current_manifest = [string]$rawReceipt.current_manifest
  $state.raw_snapshot_manifest = [string]$rawReceipt.snapshot_manifest
  Write-RunEvent 'raw_data_export_passed' $state.industry (
    "snapshot=$($state.raw_snapshot_id); " +
    "contributions=$rawContributionRows; corporate=$rawCorporateRows"
  )
  Write-RunLog (
    "Raw-data PASS: $($state.industry); " +
    "snapshot=$($state.raw_snapshot_id); " +
    "rows=$($rawContributionRows + $rawCorporateRows); " +
    "seconds=$($state.raw_data_seconds)"
  )

  $state.stage = 'generator_queued'
  $state.detail = (
    'Merge, coverage, and raw-data gates passed; workbook generation queued.'
  )
  $job = New-GeneratorJob -IndustryState $state
  [void]$pendingGenerators.Add($job)
  Start-QueuedGenerators
  Write-RunEvent 'industry_gated' $state.industry
  Write-RunLog (
    "Merge/coverage/raw-data PASS; generator released: $($state.industry)"
  )
  Write-RunStatus 'pipelining' $state.industry
}

Write-RunLog 'All industry collections have reached a terminal state.'
Write-RunEvent 'collections_terminal'
while (
  $pendingGenerators.Count -gt 0 -or
  $runningGenerators.Count -gt 0 -or
  $recoveryQueue.Count -gt 0 -or
  $null -ne $activeRecovery
) {
  Update-GeneratorJobs
  Write-RunStatus 'waiting_for_generators'
  Start-Sleep -Seconds 2
}

$wallWatch.Stop()
$finishedAt = [DateTimeOffset]::Now
foreach ($state in $industryStates) {
  if ($null -ne $state.started_at -and $null -ne $state.finished_at) {
    $duration = (
      [DateTimeOffset]::Parse([string]$state.finished_at) -
      [DateTimeOffset]::Parse([string]$state.started_at)
    ).TotalSeconds
    $state.total_seconds = [math]::Round($duration, 3)
    $state.under_15_minutes = (
      $state.status -eq 'succeeded' -and $duration -lt (15 * 60)
    )
  } else {
    $state.under_15_minutes = $false
  }
}
$failures = @($industryStates | Where-Object { $_.status -ne 'succeeded' })
$under15 = @(
  $industryStates | Where-Object { $_.under_15_minutes -eq $true }
).Count
$phaseTimingIndustries = @(
  foreach ($state in $industryStates) {
    $collection = [double]$state.collection_seconds
    $merge = [double]$state.merge_seconds
    $warehouse = [double]$state.warehouse_seconds
    $export = [double]$state.export_seconds
    $build = [double]$state.workbook_build_seconds
    $formula = [double]$state.formula_seconds
    $finalize = [double]$state.finalize_seconds
    $total = [double]$state.total_seconds
    $accounted = (
      $collection + $merge + $warehouse + $export + $build + $formula +
      $finalize
    )
    [ordered]@{
      industry = $state.industry
      slug = $state.slug
      status = if ($state.status -eq 'succeeded') { 'ok' } else { 'degraded' }
      total_seconds = [math]::Round($total, 3)
      collection_seconds = [math]::Round($collection, 3)
      merge_seconds = [math]::Round($merge, 3)
      warehouse_seconds = [math]::Round($warehouse, 3)
      export_seconds = [math]::Round($export, 3)
      workbook_build_seconds = [math]::Round($build, 3)
      formula_seconds = [math]::Round($formula, 3)
      finalize_seconds = [math]::Round($finalize, 3)
      other_seconds = [math]::Round(
        [math]::Max(0.0, $total - $accounted),
        3
      )
    }
  }
)
Write-AtomicJson $phaseTimingsPath ([ordered]@{
  run_id = $runId
  status = if ($failures.Count) { 'degraded' } else { 'ok' }
  generated_at = [DateTimeOffset]::Now.ToString('o')
  industries = $phaseTimingIndustries
})
foreach ($timing in $phaseTimingIndustries) {
  Write-RunLog (
    (
      'industry={0} total={1:n3}s collection={2:n3}s merge={3:n3}s ' +
      'warehouse={4:n3}s export={5:n3}s build={6:n3}s formula={7:n3}s ' +
      'finalize={8:n3}s status={9}'
    ) -f
      $timing.slug,
      $timing.total_seconds,
      $timing.collection_seconds,
      $timing.merge_seconds,
      $timing.warehouse_seconds,
      $timing.export_seconds,
      $timing.workbook_build_seconds,
      $timing.formula_seconds,
      $timing.finalize_seconds,
      $timing.status
  )
}
$finalStatus = if ($failures.Count) { 'failed' } else { 'succeeded' }
Write-RunStatus $(if ($failures.Count) { 'failed' } else { 'completed' })
Write-RunEvent 'run_completed' '' (
  "status=$finalStatus; " +
  "seconds=$([math]::Round($wallWatch.Elapsed.TotalSeconds, 3))"
)
$summary = [ordered]@{
  run_id = $runId
  status = $finalStatus
  started_at = $startedAt.ToString('o')
  finished_at = $finishedAt.ToString('o')
  wall_clock_seconds = [math]::Round($wallWatch.Elapsed.TotalSeconds, 3)
  wall_clock_minutes = [math]::Round($wallWatch.Elapsed.TotalMinutes, 3)
  under_45_minutes = $wallWatch.Elapsed.TotalMinutes -lt 45
  industry_count = $industryStates.Count
  succeeded = $industryStates.Count - $failures.Count
  failed = $failures.Count
  performance = [ordered]@{
    per_industry_target_minutes = 15
    industries_under_15_minutes = $under15
    industries_over_or_failed_15_minutes = $industryStates.Count - $under15
    all_industries_target_minutes = 45
    all_industries_under_45_minutes = (
      $wallWatch.Elapsed.TotalMinutes -lt 45 -and $failures.Count -eq 0
    )
  }
  resources = $resourcePlan
  industries = $industryStates
  telemetry = [ordered]@{
    status_write_failures = $statusWriteFailures
    event_write_failures = $eventWriteFailures
    log_write_failures = $logWriteFailures
  }
  phase_timings_path = $phaseTimingsPath
  run_directory = $runDirectory
}
Write-AtomicJson $summaryPath $summary
Write-RunLog (
  "RUN $($summary.status.ToUpperInvariant()): " +
  "$($summary.succeeded)/$($summary.industry_count) industries; " +
  "$($summary.wall_clock_minutes) minutes; under45=$($summary.under_45_minutes)"
)
Write-RunLog "Summary: $summaryPath"
if ($failures.Count) { exit 1 }
exit 0
