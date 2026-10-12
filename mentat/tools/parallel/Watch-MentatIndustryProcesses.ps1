<#
.SYNOPSIS
Display live Mentat worker state and every recursive child process.

.DESCRIPTION
The monitor is read-only. It refreshes a dark-red console with worker stages,
PIDs, elapsed times, process names, and full command lines. When summary.json is
published, it displays the final timing and exit-code ledger and returns; the
launcher opens the host with -NoExit so the final screen remains visible.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [string]$RunDirectory,
  [ValidateRange(1, 10)]
  [int]$RefreshSeconds = 2
)

$ErrorActionPreference = 'Continue'
$RunDirectory = [IO.Path]::GetFullPath($RunDirectory)
$manifestPath = Join-Path $RunDirectory 'manifest.json'
$summaryPath = Join-Path $RunDirectory 'summary.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
  throw "Run manifest is missing: $manifestPath"
}
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json

try {
  $Host.UI.RawUI.BackgroundColor = 'DarkRed'
  $Host.UI.RawUI.ForegroundColor = 'White'
  $Host.UI.RawUI.WindowTitle = "Mentat All Processes - $($manifest.run_id)"
  Clear-Host
} catch {
  # Host color/title support varies; monitoring must continue.
}
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = New-Object Text.UTF8Encoding($false)
$monitorTranscript = Join-Path $RunDirectory 'logs\monitor.transcript.txt'
$monitorTranscriptStarted = $false
try {
  [IO.Directory]::CreateDirectory((Split-Path -Parent $monitorTranscript)) |
    Out-Null
  Start-Transcript -LiteralPath $monitorTranscript -Force | Out-Null
  $monitorTranscriptStarted = $true
} catch {
  Write-Host "Monitor transcript could not start: $($_.Exception.Message)" `
    -ForegroundColor Yellow
}

function Read-JsonFiles {
  param([string]$Pattern)
  $values = New-Object Collections.Generic.List[object]
  foreach ($file in Get-ChildItem -Path $Pattern -File -ErrorAction SilentlyContinue) {
    try {
      $values.Add(
        (Get-Content -LiteralPath $file.FullName -Raw | ConvertFrom-Json)
      )
    } catch {
      # Atomic writers make this rare; a later refresh will retry.
    }
  }
  return @($values)
}

function Get-RecursiveWorkerProcesses {
  param([int[]]$WorkerPids)
  if (-not $WorkerPids.Count) { return @() }
  $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
  $childrenByParent = @{}
  foreach ($process in $all) {
    $parent = [int]$process.ParentProcessId
    if (-not $childrenByParent.ContainsKey($parent)) {
      $childrenByParent[$parent] = New-Object Collections.Generic.List[object]
    }
    $childrenByParent[$parent].Add($process)
  }

  $seen = New-Object 'Collections.Generic.HashSet[int]'
  $queue = New-Object Collections.Generic.Queue[int]
  foreach ($workerPid in $WorkerPids) {
    if ($seen.Add([int]$workerPid)) { $queue.Enqueue([int]$workerPid) }
  }
  while ($queue.Count) {
    $parent = $queue.Dequeue()
    if (-not $childrenByParent.ContainsKey($parent)) { continue }
    foreach ($child in $childrenByParent[$parent]) {
      $childPid = [int]$child.ProcessId
      if ($seen.Add($childPid)) { $queue.Enqueue($childPid) }
    }
  }
  return @($all | Where-Object { $seen.Contains([int]$_.ProcessId) })
}

while ($true) {
  $states = @(Read-JsonFiles (Join-Path $RunDirectory 'state\*.state.json'))
  $results = @(Read-JsonFiles (Join-Path $RunDirectory 'results\*.result.json'))
  $workerPids = @(
    $states |
      Where-Object { $null -ne $_.worker_pid } |
      ForEach-Object { [int]$_.worker_pid } |
      Select-Object -Unique
  )

  $processes = @()
  $processError = ''
  try {
    $processes = @(Get-RecursiveWorkerProcesses $workerPids)
  } catch {
    $processError = $_.Exception.Message
  }

  try {
    Clear-Host
    $Host.UI.RawUI.BackgroundColor = 'DarkRed'
    $Host.UI.RawUI.ForegroundColor = 'White'
  } catch {}
  Write-Host 'MENTAT INDUSTRY REPORTS - ALL PROCESSES' -ForegroundColor Cyan
  Write-Host "Run ID: $($manifest.run_id)"
  Write-Host (
    'Updated: {0} | Completed: {1}/{2} | Max parallel: {3}' -f
      (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'),
      $results.Count,
      @($manifest.industries).Count,
      $manifest.max_parallel
  )
  Write-Host ''
  Write-Host 'WORKERS' -ForegroundColor Cyan
  if (-not $states.Count) {
    Write-Host '  Waiting for worker consoles to initialize...'
  } else {
    foreach ($state in $states | Sort-Object industry) {
      $stateTime = [DateTimeOffset]::Parse([string]$state.started_at)
      $elapsed = ([DateTimeOffset]::Now - $stateTime).TotalSeconds
      Write-Host (
        '  {0,-34} PID {1,6}  {2,-28}  {3,8:n1}s' -f
          [string]$state.industry,
          [int]$state.worker_pid,
          [string]$state.stage,
          $elapsed
      )
      if (-not [string]::IsNullOrWhiteSpace([string]$state.detail)) {
        Write-Host "      $($state.detail)"
      }
    }
  }

  Write-Host ''
  Write-Host 'RECURSIVE PROCESS TREE' -ForegroundColor Cyan
  if ($processError) {
    Write-Host "  Process inspection unavailable: $processError" `
      -ForegroundColor Yellow
  } elseif (-not $processes.Count) {
    Write-Host '  No active worker/child processes found.'
  } else {
    foreach ($process in $processes | Sort-Object ParentProcessId, ProcessId) {
      $workingSetMb = [math]::Round(
        [double]$process.WorkingSetSize / 1MB,
        1
      )
      Write-Host (
        '  PID {0,6}  PPID {1,6}  {2,-22}  {3,8:n1} MB' -f
          [int]$process.ProcessId,
          [int]$process.ParentProcessId,
          [string]$process.Name,
          $workingSetMb
      )
      Write-Host "      $([string]$process.CommandLine)"
    }
  }

  Write-Host ''
  Write-Host "Run folder: $RunDirectory"
  if (Test-Path -LiteralPath $summaryPath -PathType Leaf) {
    try {
      $summary = Get-Content -LiteralPath $summaryPath -Raw | ConvertFrom-Json
      Write-Host ''
      Write-Host 'FINAL SUMMARY' -ForegroundColor Cyan
      Write-Host (
        'Wall clock: {0:n2} minutes | Succeeded: {1}/{2} | Under 45: {3}' -f
          [double]$summary.wall_clock_minutes,
          [int]$summary.succeeded,
          [int]$summary.industry_count,
          [bool]$summary.under_45_minutes
      )
      foreach ($result in $summary.results | Sort-Object industry) {
        Write-Host (
          '  {0,-34} {1,-10} exit={2,3} collect={3,8:n1}s build={4,8:n1}s total={5,8:n1}s' -f
            [string]$result.industry,
            [string]$result.status,
            [int]$result.exit_code,
            [double]$result.collection_elapsed_seconds,
            [double]$result.generator_elapsed_seconds,
            [double]$result.total_elapsed_seconds
        )
      }
      Write-Host ''
      Write-Host "Summary JSON: $summaryPath"
      Write-Host "Monitor log:  $monitorTranscript"
      Write-Host 'Monitoring complete. This console will remain open.' `
        -ForegroundColor Cyan
      if ($monitorTranscriptStarted) {
        try { Stop-Transcript | Out-Null } catch {}
      }
      break
    } catch {
      Write-Host "Summary is being finalized: $($_.Exception.Message)"
    }
  }
  Start-Sleep -Seconds $RefreshSeconds
}
