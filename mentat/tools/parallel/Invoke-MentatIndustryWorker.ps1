<#
.SYNOPSIS
Execute one Mentat industry report inside a visible worker console.

.DESCRIPTION
This script is launched by Start-MentatIndustryReports.ps1. It reads a
run-specific JSON specification, waits for a shared build slot, streams every
child-process line to the console and a UTF-8 process log, records a PowerShell
transcript, and atomically publishes state and final result JSON.

When the generator supports -ExcelMutexName, only its Excel COM phase should
take the named mutex. For backward compatibility, a generator without that
parameter is protected by holding the Excel mutex for its entire invocation.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$SpecPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$lastGoodModulePath = Join-Path $PSScriptRoot 'MentatLastGoodGuard.psm1'
if (-not (Test-Path -LiteralPath $lastGoodModulePath -PathType Leaf)) {
  throw "Last-good guard module is missing: $lastGoodModulePath"
}
Import-Module -Name $lastGoodModulePath -Force -ErrorAction Stop

function Write-AtomicJson {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)]$Value
  )
  $directory = Split-Path -Parent $Path
  [IO.Directory]::CreateDirectory($directory) | Out-Null
  $temporary = Join-Path $directory (
    '.{0}.{1}.{2}.tmp' -f
      [IO.Path]::GetFileName($Path),
      $PID,
      [Guid]::NewGuid().ToString('N')
  )
  $encoding = New-Object Text.UTF8Encoding($false)
  [IO.File]::WriteAllText(
    $temporary,
    ($Value | ConvertTo-Json -Depth 12),
    $encoding
  )
  try {
    for ($attempt = 1; $attempt -le 10; $attempt++) {
      $backup = $null
      try {
        if (Test-Path -LiteralPath $Path) {
          $backup = "$Path.backup-$PID-$([Guid]::NewGuid().ToString('N'))"
          [IO.File]::Replace($temporary, $Path, $backup, $true)
        } else {
          [IO.File]::Move($temporary, $Path)
        }
        return
      } catch {
        $exception = $_.Exception
        $isIoException = (
          $exception -is [IO.IOException] -or
          $exception.InnerException -is [IO.IOException]
        )
        if (-not $isIoException -or $attempt -eq 10) { throw }
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
    if (Test-Path -LiteralPath $temporary) {
      try { [IO.File]::Delete($temporary) } catch {}
    }
  }
}

function Format-CommandArgument {
  param([string]$Value)
  if ($Value -notmatch '[\s'']') { return $Value }
  return "'" + $Value.Replace("'", "''") + "'"
}

function Write-WorkerState {
  param(
    [Parameter(Mandatory = $true)][string]$Stage,
    [string]$Detail = ''
  )
  Write-AtomicJson $spec.state_path ([ordered]@{
    run_id = [string]$spec.run_id
    industry = [string]$spec.industry
    slug = [string]$spec.slug
    worker_pid = $PID
    stage = $Stage
    detail = $Detail
    updated_at = [DateTimeOffset]::Now.ToString('o')
    started_at = $workerStartedAt.ToString('o')
  })
}

$SpecPath = [IO.Path]::GetFullPath($SpecPath)
if (-not (Test-Path -LiteralPath $SpecPath -PathType Leaf)) {
  throw "Worker specification is missing: $SpecPath"
}
$spec = Get-Content -LiteralPath $SpecPath -Raw | ConvertFrom-Json
foreach ($field in @(
  'run_id', 'industry', 'slug', 'generator_path', 'mentat_path',
  'rscript_path', 'r_library_path', 'data_root', 'database_path',
  'output_directory',
  'expected_workbook_path', 'state_path', 'result_path', 'process_log_path',
  'transcript_path', 'slot_semaphore_name', 'excel_mutex_name'
)) {
  if (
    $null -eq $spec.PSObject.Properties[$field] -or
    [string]::IsNullOrWhiteSpace([string]$spec.$field)
  ) {
    throw "Worker specification is missing required field: $field"
  }
}

$workerToolsRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Import-Module (Join-Path $workerToolsRoot 'report_directory.psm1') -Force
$spec.output_directory = Resolve-MentatReportDirectory -ProjectRoot (Split-Path -Parent $workerToolsRoot) -OutputDirectory ([string]$spec.output_directory)
$env:MENTAT_REPORT_DIRECTORY = [string]$spec.output_directory
if (-not [string]::Equals(
  [IO.Path]::GetFullPath((Split-Path -Parent ([string]$spec.expected_workbook_path))).TrimEnd('\','/'),
  ([string]$spec.output_directory).TrimEnd('\','/'), [StringComparison]::OrdinalIgnoreCase)) {
  throw 'Worker workbook path is outside the authoritative report directory.'
}

$lastGoodRoot = if (
  $null -ne $spec.PSObject.Properties['last_good_root'] -and
  -not [string]::IsNullOrWhiteSpace([string]$spec.last_good_root)
) {
  [IO.Path]::GetFullPath([string]$spec.last_good_root)
} else {
  [IO.Path]::GetFullPath(
    (Join-Path (Split-Path -Parent ([string]$spec.run_directory)) 'last-good')
  )
}
$availabilityStatePath = if (
  $null -ne $spec.PSObject.Properties['availability_state_path'] -and
  -not [string]::IsNullOrWhiteSpace([string]$spec.availability_state_path)
) {
  [IO.Path]::GetFullPath([string]$spec.availability_state_path)
} else {
  [IO.Path]::GetFullPath(
    (Join-Path ([string]$spec.run_directory) (
      'availability\{0}.availability.json' -f [string]$spec.slug
    ))
  )
}
$initialAvailability = if (
  $null -ne $spec.PSObject.Properties['initial_availability']
) {
  $spec.initial_availability
} else {
  $null
}

try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat Report - $($spec.industry)"
  Clear-Host
} catch {
  # Host color/title support varies; reporting must continue.
}
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = New-Object Text.UTF8Encoding($false)

$workerStartedAt = [DateTimeOffset]::Now
$totalWatch = [Diagnostics.Stopwatch]::StartNew()
$slotWatch = [Diagnostics.Stopwatch]::StartNew()
$collectionWatch = New-Object Diagnostics.Stopwatch
$generatorWatch = New-Object Diagnostics.Stopwatch
$slotSemaphore = $null
$excelMutex = $null
$slotAcquired = $false
$excelAcquired = $false
$transcriptStarted = $false
$processWriter = $null
$exitCode = 1
$status = 'failed'
$errorText = ''
$outputWorkbook = [string]$spec.expected_workbook_path
if ($null -eq $initialAvailability) {
  $initialAvailability = Initialize-MentatReportAvailability `
    -Industry ([string]$spec.industry) `
    -TargetPath $outputWorkbook `
    -LastGoodRoot $lastGoodRoot `
    -StatePath $availabilityStatePath
}
$workbookHash = ''
$lastGoodState = $null
$lastGoodError = ''
$finalAvailability = $null
$generatorSupportsMutex = $false
$generatorStartedAt = $null
$collectionExitCode = $null
$resumeAfter = ''
if ($null -ne $spec.PSObject.Properties['resume_after']) {
  $resumeAfter = ([string]$spec.resume_after).Trim()
}
$resumedCollection = -not [string]::IsNullOrWhiteSpace($resumeAfter)
$slotCapacity = 1
if ($null -ne $spec.PSObject.Properties['slot_capacity']) {
  $parsedSlotCapacity = 0
  if (
    [int]::TryParse(
      [string]$spec.slot_capacity,
      [ref]$parsedSlotCapacity
    ) -and
    $parsedSlotCapacity -ge 1 -and
    $parsedSlotCapacity -le 64
  ) {
    $slotCapacity = $parsedSlotCapacity
  } else {
    throw "Worker specification has an invalid slot_capacity: $($spec.slot_capacity)"
  }
}

try {
  [IO.Directory]::CreateDirectory([string]$spec.output_directory) | Out-Null
  [IO.Directory]::CreateDirectory(
    (Split-Path -Parent ([string]$spec.process_log_path))
  ) | Out-Null

  Start-Transcript -LiteralPath ([string]$spec.transcript_path) -Force | Out-Null
  $transcriptStarted = $true

  Write-Host ''
  Write-Host 'MENTAT INDUSTRY REPORT WORKER' -ForegroundColor Cyan
  Write-Host "Industry: $($spec.industry)"
  Write-Host "Run ID:   $($spec.run_id)"
  Write-Host "PID:      $PID"
  Write-Host "Started:  $($workerStartedAt.ToString('o'))"
  Write-Host "Log:      $($spec.process_log_path)"
  Write-Host ''

  Write-WorkerState -Stage 'waiting_for_build_slot' `
    -Detail "Waiting on $($spec.slot_semaphore_name)"
  try {
    $slotSemaphore = [Threading.Semaphore]::OpenExisting(
      [string]$spec.slot_semaphore_name
    )
  } catch [Threading.WaitHandleCannotBeOpenedException] {
    if (-not $resumedCollection) { throw }
    $createdResumeSemaphore = $false
    $slotSemaphore = [Threading.Semaphore]::new(
      $slotCapacity,
      $slotCapacity,
      [string]$spec.slot_semaphore_name,
      [ref]$createdResumeSemaphore
    )
    Write-Warning (
      'The original run semaphore was already closed. Created a private ' +
      "$slotCapacity-worker semaphore for this checkpoint resume."
    )
  }
  [void]$slotSemaphore.WaitOne()
  $slotAcquired = $true
  $slotWatch.Stop()
  Write-Host (
    'Build slot acquired after {0:n3} seconds.' -f $slotWatch.Elapsed.TotalSeconds
  ) -ForegroundColor Green

  $processEncoding = New-Object Text.UTF8Encoding($false)
  $processWriter = New-Object IO.StreamWriter(
    [string]$spec.process_log_path,
    $true,
    $processEncoding
  )
  $processWriter.AutoFlush = $true

  if ([bool]$spec.collect_from_scratch) {
    $runDirectory = [IO.Path]::GetFullPath([string]$spec.run_directory)
    $dataRoot = [IO.Path]::GetFullPath([string]$spec.data_root)
    $allowedDataRoot = [IO.Path]::GetFullPath(
      (Join-Path $runDirectory 'data')
    ).TrimEnd('\') + '\'
    if (-not $dataRoot.StartsWith(
      $allowedDataRoot,
      [StringComparison]::OrdinalIgnoreCase
    )) {
      throw "Refusing collection outside the run data directory: $dataRoot"
    }
    if (Test-Path -LiteralPath $dataRoot) {
      $existing = @(Get-ChildItem -LiteralPath $dataRoot -Force)
      if ($existing.Count -and -not $resumedCollection) {
        throw "From-scratch data root is not empty: $dataRoot"
      }
    } else {
      [IO.Directory]::CreateDirectory($dataRoot) | Out-Null
    }
    if (
      $resumedCollection -and
      -not (Test-Path -LiteralPath ([string]$spec.database_path) -PathType Leaf)
    ) {
      throw "Resume requested without an existing database: $($spec.database_path)"
    }

    $rscriptPath = [IO.Path]::GetFullPath([string]$spec.rscript_path)
    $mentatPath = [IO.Path]::GetFullPath([string]$spec.mentat_path)
    $env:R_LIBS_USER = [string]$spec.r_library_path
    $collectionArguments = @(
      $mentatPath,
      "--industry=$($spec.industry)",
      "--data-root=$dataRoot",
      '--engine=chrome',
      '--skip-phase2-names',
      '--defer-report-files'
    )
    if ($resumedCollection) {
      $collectionArguments += "--resume-after=$resumeAfter"
    }
    Write-Host ''
    if ($resumedCollection) {
      Write-Host 'CHECKPOINT-RESUME COLLECTION COMMAND:' -ForegroundColor Cyan
    } else {
      Write-Host 'FROM-SCRATCH COLLECTION COMMAND:' -ForegroundColor Cyan
    }
    Write-Host (
      '  ' + ((@($rscriptPath) + $collectionArguments | ForEach-Object {
        Format-CommandArgument ([string]$_)
      }) -join ' ')
    )
    $collectionDetail = if ($resumedCollection) {
      "Checkpoint resume after $resumeAfter using $($spec.database_path)"
    } else {
      "Fresh database: $($spec.database_path)"
    }
    Write-WorkerState -Stage 'running_collection' -Detail $collectionDetail
    $collectionWatch.Start()
    $priorErrorAction = $ErrorActionPreference
    try {
      $ErrorActionPreference = 'Continue'
      & $rscriptPath @collectionArguments 2>&1 | ForEach-Object {
        $line = [string]$_
        $processWriter.WriteLine($line)
        Write-Host $line
      }
      $collectionExitCode = $LASTEXITCODE
    } finally {
      $ErrorActionPreference = $priorErrorAction
      $collectionWatch.Stop()
    }
    if ($collectionExitCode -ne 0) {
      $exitCode = [int]$collectionExitCode
      throw "From-scratch collection exited with code $collectionExitCode."
    }
    if (-not (Test-Path -LiteralPath ([string]$spec.database_path) -PathType Leaf)) {
      $exitCode = 4
      throw "Collection completed without creating the expected database: $($spec.database_path)"
    }
    Write-Host (
      'Collection completed in {0:n3} seconds.' -f
        $collectionWatch.Elapsed.TotalSeconds
    ) -ForegroundColor Green
  }

  $generatorPath = [IO.Path]::GetFullPath([string]$spec.generator_path)
  if (-not (Test-Path -LiteralPath $generatorPath -PathType Leaf)) {
    throw "Report generator is missing: $generatorPath"
  }
  $generatorCommand = Get-Command -Name $generatorPath -ErrorAction Stop
  if (-not $generatorCommand.Parameters.ContainsKey('Industry')) {
    throw "Report generator must declare an -Industry parameter: $generatorPath"
  }

  $powerShellPath = (Get-Command powershell.exe -ErrorAction Stop).Source
  $generatorArguments = New-Object Collections.Generic.List[string]
  foreach ($argument in @(
    '-NoLogo',
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', $generatorPath,
    '-Industry', [string]$spec.industry
  )) {
    $generatorArguments.Add([string]$argument)
  }
  if ($generatorCommand.Parameters.ContainsKey('DatabasePath') -and
      -not [string]::IsNullOrWhiteSpace([string]$spec.database_path)) {
    $generatorArguments.Add('-DatabasePath')
    $generatorArguments.Add([string]$spec.database_path)
  }
  if ($generatorCommand.Parameters.ContainsKey('RScriptPath')) {
    $generatorArguments.Add('-RScriptPath')
    $generatorArguments.Add([string]$spec.rscript_path)
  }
  if ($generatorCommand.Parameters.ContainsKey('OutputDirectory')) {
    $generatorArguments.Add('-OutputDirectory')
    $generatorArguments.Add([string]$spec.output_directory)
  }
  if ($generatorCommand.Parameters.ContainsKey('RunDirectory')) {
    $generatorArguments.Add('-RunDirectory')
    $generatorArguments.Add([string]$spec.run_directory)
  }
  if (
    [bool]$spec.no_cache -and
    $generatorCommand.Parameters.ContainsKey('NoCache')
  ) {
    $generatorArguments.Add('-NoCache')
  }
  # Carried from the launcher spec exactly as database_path is, because the
  # builder is where the FEC requests are made. An older spec omits both
  # fields and the builder keeps its own defaults.
  if (
    $null -ne $spec.PSObject.Properties['cross_reference_max_requests'] -and
    $generatorCommand.Parameters.ContainsKey('CrossReferenceMaxRequests')
  ) {
    $crossReferenceMaxRequests = 0
    if (
      -not [int]::TryParse(
        [string]$spec.cross_reference_max_requests,
        [ref]$crossReferenceMaxRequests
      ) -or
      $crossReferenceMaxRequests -lt 0 -or
      $crossReferenceMaxRequests -gt 10000
    ) {
      throw (
        'Worker specification has an invalid cross_reference_max_requests: ' +
        [string]$spec.cross_reference_max_requests
      )
    }
    $generatorArguments.Add('-CrossReferenceMaxRequests')
    $generatorArguments.Add([string]$crossReferenceMaxRequests)
  }
  if (
    $null -ne $spec.PSObject.Properties['cross_reference_offline'] -and
    [bool]$spec.cross_reference_offline -and
    $generatorCommand.Parameters.ContainsKey('CrossReferenceOffline')
  ) {
    $generatorArguments.Add('-CrossReferenceOffline')
  }

  $generatorSupportsMutex = $generatorCommand.Parameters.ContainsKey(
    'ExcelMutexName'
  )
  # Keep a worker-owned handle open for the lifetime of the generator so the
  # named mutex survives even if the launcher console is closed.
  try {
    $excelMutex = [Threading.Mutex]::OpenExisting(
      [string]$spec.excel_mutex_name
    )
  } catch [Threading.WaitHandleCannotBeOpenedException] {
    if (-not $resumedCollection) { throw }
    $createdExcelMutex = $false
    $excelMutex = [Threading.Mutex]::new(
      $false,
      [string]$spec.excel_mutex_name,
      [ref]$createdExcelMutex
    )
    Write-Warning (
      'The shared Excel mutex was already closed. Recreated it for this ' +
      'checkpoint resume.'
    )
  }
  if ($generatorSupportsMutex) {
    $generatorArguments.Add('-ExcelMutexName')
    $generatorArguments.Add([string]$spec.excel_mutex_name)
  }
  $env:MENTAT_EXCEL_MUTEX_NAME = [string]$spec.excel_mutex_name

  $displayCommand = @($powerShellPath) + @($generatorArguments)
  Write-Host 'Command:' -ForegroundColor Cyan
  Write-Host (
    '  ' + (($displayCommand | ForEach-Object {
      Format-CommandArgument ([string]$_)
    }) -join ' ')
  )
  if ($generatorSupportsMutex) {
    Write-Host (
      "Excel COM mutex delegated to generator: $($spec.excel_mutex_name)"
    )
  } else {
    Write-Warning (
      'Generator does not expose -ExcelMutexName. For safety, this worker ' +
      'will serialize the entire generator behind the shared Excel mutex.'
    )
    Write-WorkerState -Stage 'waiting_for_excel_mutex' `
      -Detail 'Backward-compatible whole-generator serialization'
    try {
      [void]$excelMutex.WaitOne()
    } catch [Threading.AbandonedMutexException] {
      Write-Warning (
        'Recovered an abandoned Excel mutex from an interrupted earlier ' +
        'worker; this worker now owns the mutex.'
      )
    }
    $excelAcquired = $true
  }

  Write-WorkerState -Stage 'running_workbook_generator' `
    -Detail "Child output is streaming to $($spec.process_log_path)"
  $generatorStartedAt = [DateTimeOffset]::Now
  $generatorWatch.Start()
  $priorErrorAction = $ErrorActionPreference
  try {
    # Native stderr is intentionally merged into stdout. Continue prevents a
    # native stderr line from becoming a terminating PowerShell ErrorRecord.
    $ErrorActionPreference = 'Continue'
    & $powerShellPath @generatorArguments 2>&1 | ForEach-Object {
      $line = [string]$_
      $processWriter.WriteLine($line)
      Write-Host $line
    }
    $exitCode = $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $priorErrorAction
    $generatorWatch.Stop()
  }

  if ($exitCode -ne 0) {
    throw "Report generator exited with code $exitCode."
  }
  if (-not (Test-Path -LiteralPath $outputWorkbook -PathType Leaf)) {
    $exitCode = 2
    throw "Expected workbook was not created: $outputWorkbook"
  }

  $workbookItem = Get-Item -LiteralPath $outputWorkbook
  if (
    $null -ne $generatorStartedAt -and
    $workbookItem.LastWriteTimeUtc -lt
      $generatorStartedAt.UtcDateTime.AddSeconds(-2)
  ) {
    $exitCode = 3
    throw (
      'Expected workbook exists but was not refreshed by this run: ' +
      $outputWorkbook
    )
  }
  $workbookHash = (
    Get-FileHash -LiteralPath $outputWorkbook -Algorithm SHA256
  ).Hash
  $freshCompletedAt = [DateTimeOffset]::Now
  try {
    $lastGoodState = Save-MentatAuditedLastGood `
      -Industry ([string]$spec.industry) `
      -SourcePath $outputWorkbook `
      -ExpectedOutputName ([IO.Path]::GetFileName($outputWorkbook)) `
      -LastGoodRoot $lastGoodRoot `
      -GeneratorCompletedAt $freshCompletedAt.ToString('o')
  } catch {
    # The freshly audited workbook is already atomically published. A
    # last-good archive failure must be loud and recorded, but must not relabel
    # the successfully generated report as failed.
    $lastGoodError = $_.Exception.Message
    Write-Warning "Last-good archive failed: $lastGoodError"
  }
  $finalAvailability = [ordered]@{
    schema_version = 1
    industry = [string]$spec.industry
    expected_output_name = [IO.Path]::GetFileName($outputWorkbook)
    target_path = $outputWorkbook
    checked_at = $freshCompletedAt.ToString('o')
    availability = 'present'
    action = 'fresh_audited_report_published'
    provenance = [ordered]@{
      kind = 'fresh_generator_output'
      generator_started_at = $generatorStartedAt.ToString('o')
      generator_completed_at = $freshCompletedAt.ToString('o')
      evidence = [ordered]@{
        path = $workbookItem.FullName
        bytes = [int64]$workbookItem.Length
        sha256 = $workbookHash.ToLowerInvariant()
        last_write_utc = $workbookItem.LastWriteTimeUtc.ToString('o')
      }
      last_good = $lastGoodState
    }
    error = $lastGoodError
  }
  Write-AtomicJson $availabilityStatePath $finalAvailability
  $status = 'succeeded'
  Write-WorkerState -Stage 'completed' -Detail $outputWorkbook
} catch {
  $errorText = $_.Exception.Message
  if ($exitCode -eq 0) { $exitCode = 1 }
  try {
    Write-WorkerState -Stage 'failed' -Detail $errorText
  } catch {
    # The final result write below is the authoritative failure receipt.
  }
  Write-Host ''
  Write-Host "FAILED: $errorText" -ForegroundColor Yellow
} finally {
  if ($generatorWatch.IsRunning) { $generatorWatch.Stop() }
  if ($collectionWatch.IsRunning) { $collectionWatch.Stop() }
  if ($null -ne $processWriter) { $processWriter.Dispose() }
  if ($excelAcquired -and $null -ne $excelMutex) {
    try { $excelMutex.ReleaseMutex() } catch {}
  }
  if ($null -ne $excelMutex) { $excelMutex.Dispose() }
  if ($slotAcquired -and $null -ne $slotSemaphore) {
    try { [void]$slotSemaphore.Release() } catch {}
  }
  if ($null -ne $slotSemaphore) { $slotSemaphore.Dispose() }
  $totalWatch.Stop()
}

$finishedAt = [DateTimeOffset]::Now
if ($status -eq 'succeeded') {
  Write-Host ''
  Write-Host 'SUCCEEDED' -ForegroundColor Green
  Write-Host "Workbook: $outputWorkbook"
  Write-Host ("Generator: {0:n3} seconds" -f $generatorWatch.Elapsed.TotalSeconds)
  Write-Host ("Collection:{0,10:n3} seconds" -f $collectionWatch.Elapsed.TotalSeconds)
  Write-Host ("Total:     {0:n3} seconds" -f $totalWatch.Elapsed.TotalSeconds)
}

$result = [ordered]@{
  run_id = [string]$spec.run_id
  industry = [string]$spec.industry
  slug = [string]$spec.slug
  status = $status
  exit_code = [int]$exitCode
  worker_pid = $PID
  started_at = $workerStartedAt.ToString('o')
  finished_at = $finishedAt.ToString('o')
  slot_wait_seconds = [math]::Round($slotWatch.Elapsed.TotalSeconds, 3)
  collection_elapsed_seconds = [math]::Round(
    $collectionWatch.Elapsed.TotalSeconds,
    3
  )
  collection_exit_code = $collectionExitCode
  resumed_collection = $resumedCollection
  resume_after = $resumeAfter
  generator_elapsed_seconds = [math]::Round(
    $generatorWatch.Elapsed.TotalSeconds,
    3
  )
  total_elapsed_seconds = [math]::Round($totalWatch.Elapsed.TotalSeconds, 3)
  over_45_minutes = ($totalWatch.Elapsed.TotalMinutes -ge 45)
  excel_mutex_delegated = $generatorSupportsMutex
  output_workbook = if (
    Test-Path -LiteralPath $outputWorkbook -PathType Leaf
  ) { $outputWorkbook } else { '' }
  workbook_sha256 = $workbookHash
  initial_availability = $initialAvailability
  final_availability = $finalAvailability
  last_good_state = $lastGoodState
  last_good_error = $lastGoodError
  last_good_root = $lastGoodRoot
  availability_state_path = $availabilityStatePath
  process_log = [string]$spec.process_log_path
  transcript = [string]$spec.transcript_path
  error = $errorText
}
try {
  Write-AtomicJson ([string]$spec.result_path) $result

  if ($result.over_45_minutes) {
    Write-Warning (
      'This industry exceeded the 45-minute performance target. The completed ' +
      'output, logs, and timing receipt were preserved.'
    )
  }
  Write-Host ''
  Write-Host 'The worker is finished. This console will remain open.' `
    -ForegroundColor Cyan
} finally {
  if ($transcriptStarted) {
    try { Stop-Transcript | Out-Null } catch {}
  }
}
