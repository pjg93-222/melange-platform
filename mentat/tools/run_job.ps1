<#
.SYNOPSIS
  Runs one Mentat compute job in its container (D-173..D-182) and succeeds only when the job's own completion receipt verifies.

.DESCRIPTION
  The Windows side of mentat\compute.compose.yaml. In order: take a per-job lock; start Docker Desktop if the engine is down;
  check the image lock (a sha256 digest, never a tag); check the VM clock against this host; stage the FEC keys (collecting jobs
  only) as one owner-only file for a Docker secret; `docker compose run --rm` the job; verify <LOCALAPPDATA>\Mentat\compute\receipts\
  <run id>.json from here (tools\compute_job.py verify), never the exit code alone (DE-053); wipe the key file in a finally block.
  Wrapper start and finish are Glass Box events through Mentat's glassbox_emit (compute_job.py event), as run_logged.ps1 is.

  Exit codes: 0 completed and verified; 1 the job ran and failed, or its receipt did not verify; 2 refused before anything ran
  (unknown job, no image digest, bad run id); 3 Docker or its clock; 75 another run of this job holds the lock; 124 timed out.

  Replaces run_logged.ps1 for MentatNightlyAnchors (-Job anchors) and MentatRefitWatcher (-Job refit-detect) only after three
  parity runs and Paul's go; the two Windows tasks are not touched by this script (mentat\COMPUTE.md).

  Optional caller environment (forward or back slashes): REPORTS_HOST, LOO_HOST, CHE_HOST, ZIPC_HOST, COLLECT_HOST, EVIDENCE_HOST,
  POCKET_IN_HOST, POCKET_OUT_HOST, ANCHORS_OUT_HOST (the host folders the job that names them mounts), GLASSBOX_EVENTS_DIR, MENTAT_REPORT_DIRECTORY.

.PARAMETER JobArgs
  Arguments after the `--` of compute_job.py run. An array: from a console `-JobArgs '--industry','Banking'`. Under `powershell -File` a
  comma list arrives as one string, so a scheduled task runs only jobs that take no arguments (anchors, refit-detect).

.PARAMETER PlanOnly
  Print the plan as JSON (compose command, host variables, verification maps) and do nothing else: no Docker, no keys, no events.

.PARAMETER Batch, Workers, MaxRequests, Seeds, EnableEndpointContract, PriorityCandidates
  Only for -Job collect-downstream (D075 phase 1, host-native: tools\collect_committee_activity.py runs directly on Windows against the shared ledger under
  state\committee-activity, no container). -Batch is the batch number (names the run), -Workers 1..32, -MaxRequests the physical request cap, -Seeds the seed file,
  -EnableEndpointContract the repeatable pinned contract ids, -PriorityCandidates the optional priority list. `--live` is never added here: pass it in -JobArgs.
  -JobArgs also accepts --max-failed-attempts N, --include-later-cycle-context, --optional-receipts, --ccl CYCLE=PATH and --ccl-vintage CYCLE=TEXT. The job refuses to create
  a ledger, to run beside another collector process, and treats exit 0 as complete only when the collector's own receipt verifies (DE-053).

.PARAMETER Runtime, InitVolume, JobArgsJson
  D075 phase 2 (opt-in). -Runtime host (the default) is today's behaviour. -Runtime container runs the downstream collector as a compose job: collect-downstream (the
  collector in a hardened container with NO FEC key; a host broker, api_host_pool.py --scope spending-evidence-v2, holds the keys, the shared quota ledger and the archive),
  collect-downstream-recover (crash recovery in the volume, no network) and collect-downstream-import (seed an empty volume from a host snapshot). Their arguments all come
  in -JobArgs / -JobArgsJson (--batch, --collector-run-id, --expect-base-sha256, --seeds /inputs/..., --max-requests, --max-failed-attempts, --workers, --live, ...); -Batch
  and the other host-native parameters are refused there. The live ledger is in the Docker volume DOWNSTREAM_VOLUME (default mentat-downstream-frontier, created once
  with -InitVolume, never pruned); every run ends with a hash-checked export under <state>\exports\<run id>, verified from here. Host variables: DOWNSTREAM_STATE_HOST,
  DOWNSTREAM_INPUTS_HOST (the folder holding the seed file) or DOWNSTREAM_IMPORT_HOST (import), DOWNSTREAM_VOLUME, DOWNSTREAM_MIN_FREE_GB (default 30).
  -JobArgsJson names a JSON array of the job arguments, for callers that cannot pass a PowerShell array under -File.
#>
[CmdletBinding(PositionalBinding = $false)]
param(
  [Parameter(Mandatory)][string]$Job,
  [string]$RunId = '',
  [string]$ImageLock = '',
  [string]$PythonPath = '',
  [string]$DockerPath = 'docker',
  [string]$WslPath = 'wsl.exe',
  [int]$DockerStartSeconds = 240,
  [double]$MaxClockSkewSeconds = 2,
  [switch]$PlanOnly,
  [int]$Batch = 0,
  [int]$Workers = 1,
  [int]$MaxRequests = 0,
  [string]$Seeds = '',
  [string[]]$EnableEndpointContract = @(),
  [string]$PriorityCandidates = '',
  [string]$Runtime = 'host',
  [switch]$InitVolume,
  [string]$JobArgsJson = '',
  [string[]]$JobArgs = @()
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $projectRoot
$composeFile = Join-Path $projectRoot 'compute.compose.yaml'
Import-Module (Join-Path $PSScriptRoot 'mentat_python.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'report_directory.psm1') -Force

function ConvertTo-Slash([string]$Path) { return $Path.TrimEnd('\', '/').Replace('\', '/') }

function Format-Argument([string]$Text) {
  if ($Text -match '[\s"]') { return '"' + $Text.Replace('"', '\"') + '"' }
  return $Text
}

# A native command with stderr kept out of PowerShell's error stream (Windows PowerShell 5.1 turns stderr lines into errors).
function Invoke-Native([string]$Exe, [string[]]$Arguments, [switch]$StdoutOnly) {
  $prior = $ErrorActionPreference
  $ErrorActionPreference = 'Continue'
  try {
    if ($StdoutOnly) { $lines = @(& $Exe @Arguments 2>$null | ForEach-Object { [string]$_ }) }
    else {
      $lines = @(& $Exe @Arguments 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { [string]$_ } })
    }
    return [pscustomobject]@{ Code = $LASTEXITCODE; Output = ($lines -join "`n") }
  }
  finally { $ErrorActionPreference = $prior }
}

function Stop-Refused([string]$Message, [int]$Code) {
  [Console]::Error.WriteLine($Message)
  exit $Code
}

function Write-Summary([string]$Text) {
  Add-Content -Encoding utf8 -Path $script:summaryLog -Value ('{0} {1}' -f (Get-Date -Format o), $Text)
}

function Send-GlassBox([string]$Kind, [string]$Status, [string]$Summary, [string]$Detail = '') {
  # Emission never breaks the run (glassbox_emit swallows its own failures).
  $null = Invoke-Native $script:python.Path @($script:jobTool, 'event', '--kind', $Kind, '--status', $Status, '--summary', $Summary,
    '--run-id', $script:RunId, '--stage', 'wrapper', '--detail', $Detail)
}

function Test-DockerEngine { return ((Invoke-Native $DockerPath @('info')).Code -eq 0) }

function Start-DockerEngine {
  if (Test-DockerEngine) { return $true }
  $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
  if (Test-Path -LiteralPath $desktop -PathType Leaf) { Start-Process -FilePath $desktop -WindowStyle Hidden }
  $deadline = (Get-Date).AddSeconds($DockerStartSeconds)
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 5
    if (Test-DockerEngine) { return $true }
  }
  return $false
}

# Seconds the Docker VM clock is ahead of this host (negative: behind), measured with the job's own image and no network.
function Get-ClockSkew([string]$Image) {
  $before = [DateTimeOffset]::UtcNow
  $reply = Invoke-Native $DockerPath @('run', '--rm', '--network', 'none', '--entrypoint', 'date', $Image, '-u', '+%s.%N') -StdoutOnly
  $after = [DateTimeOffset]::UtcNow
  $seconds = 0.0
  if ($reply.Code -ne 0 -or -not [double]::TryParse(($reply.Output.Trim() -replace ',', '.'), [Globalization.NumberStyles]::Float,
      [Globalization.CultureInfo]::InvariantCulture, [ref]$seconds)) { return $null }
  $middle = ($before.ToUnixTimeMilliseconds() + $after.ToUnixTimeMilliseconds()) / 2000.0
  return ($seconds - $middle)
}

# --- the request ------------------------------------------------------------------------------------------------------------
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) { throw 'LOCALAPPDATA is empty.' }
$python = Resolve-MentatPython -ExplicitPath $PythonPath
if ($null -eq $python) { throw 'No Python interpreter was found for the compute wrapper.' }
$jobTool = Join-Path $PSScriptRoot 'compute_job.py'
$secretTool = Join-Path $PSScriptRoot 'compute_secrets.py'
$hostPoolTool = Join-Path $PSScriptRoot 'api_host_pool.py'
$volumeTool = Join-Path $PSScriptRoot 'downstream_volume.py'

# --- D075 phase 2: -Runtime and -JobArgsJson ------------------------------------------------------------------------------------------------------
if ($JobArgsJson) {
  if ($JobArgs.Count) { Stop-Refused 'REFUSED: give -JobArgs or -JobArgsJson, not both.' 2 }
  if (-not (Test-Path -LiteralPath $JobArgsJson -PathType Leaf)) { Stop-Refused "REFUSED: no -JobArgsJson file at $JobArgsJson." 2 }
  try {
    $parsedArgs = Get-Content -LiteralPath $JobArgsJson -Raw -Encoding UTF8 | ConvertFrom-Json      # PS 5.1 hands a JSON array back as ONE object: keep it in a variable, then enumerate it
    if ($null -eq $parsedArgs -or $parsedArgs -isnot [object[]]) { throw 'not an array' }
    $JobArgs = @($parsedArgs | ForEach-Object { [string]$_ })
  }
  catch { Stop-Refused 'REFUSED: -JobArgsJson is not a JSON array of strings.' 2 }
}
if ($Runtime -cnotin 'host', 'container') { Stop-Refused "REFUSED: -Runtime must be 'host' (the default) or 'container'." 2 }
$downstream = ($Runtime -eq 'container' -and $Job -in 'collect-downstream', 'collect-downstream-recover', 'collect-downstream-import')
if (-not $downstream -and ($InitVolume -or $Job -in 'collect-downstream-recover', 'collect-downstream-import')) {
  Stop-Refused 'REFUSED: collect-downstream-recover, collect-downstream-import and -InitVolume belong to -Runtime container.' 2
}

# --- collect-downstream: the downstream spending collector run HOST-NATIVE (D075 phase 1; no container, no Docker, no image lock) ----------------
# It runs tools\collect_committee_activity.py directly on Windows against the shared ledger under state\committee-activity (override: $env:DOWNSTREAM_STATE_HOST).
# `--live` is never added here: the caller passes it in -JobArgs, so the authorization stays visible in -PlanOnly and in the log. Everything below the
# refusals is the same DE-053 rule as the container jobs: exit 0 of the child is one input, the collector's own receipt must verify from this side.
if ($Job -eq 'collect-downstream' -and $Runtime -ne 'container') {
  $collector = Join-Path $PSScriptRoot 'collect_committee_activity.py'
  $stateHost = if ($env:DOWNSTREAM_STATE_HOST) { $env:DOWNSTREAM_STATE_HOST } else { Join-Path $projectRoot 'state\committee-activity' }
  $ledgerHost = Join-Path $stateHost 'frontier.sqlite'
  $allowedFlags = '--live', '--max-failed-attempts', '--include-later-cycle-context', '--optional-receipts', '--ccl', '--ccl-vintage', '--bulk-activity-index', '--expect-bulk-index-sha256'
  $refusals = @()
  if ($Batch -lt 1) { $refusals += '-Batch (the batch number, 1 or more) is required.' }
  if ($Workers -lt 1 -or $Workers -gt 32) { $refusals += '-Workers must be 1 to 32.' }
  if ($MaxRequests -lt 1) { $refusals += '-MaxRequests (the physical request cap, 1 or more) is required.' }
  if (-not $Seeds -or -not (Test-Path -LiteralPath $Seeds -PathType Leaf)) { $refusals += '-Seeds must name an existing seed file.' }
  if ($PriorityCandidates -and -not (Test-Path -LiteralPath $PriorityCandidates -PathType Leaf)) { $refusals += '-PriorityCandidates must name an existing JSON file.' }
  foreach ($contract in $EnableEndpointContract) { if ($contract -cnotmatch '^openfec-[A-Za-z0-9._-]+$') { $refusals += "-EnableEndpointContract '$contract' is not a contract id." } }
  foreach ($argument in $JobArgs) {
    if ($argument -like '--*' -and $argument -cnotin $allowedFlags) { $refusals += "'$argument' is not accepted in -JobArgs for collect-downstream (the job owns --state/--seeds/--max-requests/--workers/--enable-endpoint-contract/--priority-candidates; recovery, close-out, re-bind, migration and --init-new-ledger are separate deliberate commands)." }
  }
  if (-not (Test-Path -LiteralPath $collector -PathType Leaf)) { $refusals += "No collector at $collector." }
  if (-not (Test-Path -LiteralPath $ledgerHost -PathType Leaf)) { $refusals += "No frontier.sqlite at $stateHost; this job never creates a ledger (a new ledger forks the frontier)." }
  if ([string]::IsNullOrWhiteSpace($RunId)) { $RunId = 'collect-downstream-b{0:D2}-{1}' -f $Batch, (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') }
  if ($RunId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$') { $refusals += 'The run id may hold only letters, digits, dots, dashes and underscores.' }
  if ($refusals.Count) { Stop-Refused (($refusals | ForEach-Object { "REFUSED: $_" }) -join "`n") 2 }

  $collectorArguments = @('-B', $collector, '--state', $stateHost, '--seeds', $Seeds, '--max-requests', [string]$MaxRequests, '--workers', [string]$Workers)
  foreach ($contract in $EnableEndpointContract) { $collectorArguments += '--enable-endpoint-contract'; $collectorArguments += $contract }
  if ($PriorityCandidates) { $collectorArguments += '--priority-candidates'; $collectorArguments += $PriorityCandidates }
  $collectorArguments += $JobArgs
  $timeoutMinutes = 725            # the collector has no wall-clock cap of its own; this is the backstop (12 h plus 5 min)
  if ($PlanOnly) {
    [ordered]@{ job = $Job; run_id = $RunId; batch = $Batch; runtime = 'host-native (no container)'; python = $python.Path; collector = $collector
                state = $stateHost; ledger = $ledgerHost; arguments = $collectorArguments; live_requested = ($JobArgs -contains '--live')
                workers = $Workers; max_requests = $MaxRequests; fec_keys = 'loaded by the collector from the DPAPI store only when --live is passed; never staged or mounted here'
                timeout_minutes = $timeoutMinutes } | ConvertTo-Json -Depth 6
    return
  }

  $logDirectory = Join-Path $env:LOCALAPPDATA 'Mentat\logs\compute'
  $lockDirectory = Join-Path $env:LOCALAPPDATA 'Mentat\compute\locks'
  foreach ($folder in $logDirectory, $lockDirectory) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
  $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
  $outLog = Join-Path $logDirectory "$Job-$stamp.out.log"
  $errLog = Join-Path $logDirectory "$Job-$stamp.err.log"
  $summaryLog = Join-Path $logDirectory "$Job.log"
  try { $lockStream = [IO.File]::Open((Join-Path $lockDirectory "$Job.lock"), 'OpenOrCreate', 'ReadWrite', 'None') }
  catch [IO.IOException] { Stop-Refused "Another run of $Job holds its lock." 75 }
  $code = 1
  $problems = @()
  try {
    do {
      # Any collector already alive (including an old suspended batch) is a second writer the run lease cannot see.
      $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*collect_committee_activity.py*' })
      if ($running.Count) { $code = 75; $problems += ('A collector process is already running (pid ' + (($running | ForEach-Object { $_.ProcessId }) -join ', ') + ').'); break }
      Write-Summary "START $Job run=$RunId batch=$Batch workers=$Workers cap=$MaxRequests live=$($JobArgs -contains '--live')"
      Send-GlassBox 'run.start' 'running' "Compute job ${Job}: host-native wrapper start" "batch $Batch"
      $started = [DateTime]::UtcNow
      $child = Start-Process -FilePath $python.Path -ArgumentList (($collectorArguments | ForEach-Object { Format-Argument ([string]$_) }) -join ' ') -NoNewWindow -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
      $null = $child.Handle        # PS 5.1: ExitCode stays empty unless the handle is opened before exit
      if ($child.WaitForExit($timeoutMinutes * 60000)) { $childCode = $child.ExitCode }
      else {
        $null = Invoke-Native 'taskkill.exe' @('/T', '/F', '/PID', [string]$child.Id)
        $childCode = 124
        $problems += "Killed after $timeoutMinutes minutes; the next collector start refuses until --recover-crashed-runs resolves it."
      }
      # The exit code is one input. The job is complete only when the collector's own receipt verifies from this side (DE-053).
      $verified = $false
      if ($childCode -eq 0) {
        try {
          $printed = (Get-Content -LiteralPath $outLog -Encoding UTF8 | Where-Object { $_ -match '^\{.*"receipt"' } | Select-Object -Last 1) | ConvertFrom-Json
          $receiptFile = Get-Item -LiteralPath ([string]$printed.receipt)
          $data = Get-Content -LiteralPath $receiptFile.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
          $stopType = if ($null -ne $data.stop) { [string]$data.stop.error_type } else { '' }
          $checks = [ordered]@{
            'receipt is newer than the start'      = ($receiptFile.LastWriteTimeUtc -ge $started.AddSeconds(-2))
            'receipt is inside the state folder'   = ([IO.Path]::GetFullPath($receiptFile.DirectoryName) -eq [IO.Path]::GetFullPath($stateHost).TrimEnd('\'))
            'receipt names this ledger'            = ([IO.Path]::GetFullPath([string]$data.ledger) -eq [IO.Path]::GetFullPath($ledgerHost))
            'cap and workers are the requested'    = (([int]$data.physical_request_cap -eq $MaxRequests) -and ([int]$data.workers -eq $Workers))
            'attempts within the cap'              = ([int]$data.http_attempts -le $MaxRequests)
            'every attempt is accounted'           = (([int]$data.endpoint_attempts_recorded + [int]$data.candidate_committee_lookup_http_attempts) -eq [int]$data.http_attempts)
            'stop is not an integrity failure'     = ($stopType -notin 'ArchiveBindingFailed', 'FECArchiveFailed', 'EndpointContractRefused', 'CrashRecovered', 'IntegrityError', 'OperationalError', 'DatabaseError')
          }
          foreach ($name in $checks.Keys) { if (-not $checks[$name]) { $problems += "receipt check failed: $name" } }
          $verified = -not ($checks.Values -contains $false)
          Write-Summary ("RECEIPT {0} http_attempts={1} stop={2}" -f $receiptFile.FullName, $data.http_attempts, $(if ($stopType) { $stopType } else { 'none' }))
        }
        catch { $problems += "The collector's receipt could not be verified: $_" }
      }
      if ($childCode -eq 124) { $code = 124 }
      elseif ($childCode -eq 0 -and $verified) { $code = 0 }
      else { $code = 1; if ($childCode -ne 0) { $problems += "collector exited $childCode" } }
    } while ($false)
  }
  catch { $code = 1; $problems += "wrapper error: $_" }
  finally {
    $detail = ($problems | Where-Object { $_ }) -join '; '
    if ($detail) { Add-Content -Encoding utf8 -Path $errLog -Value $detail }
    Write-Summary "END exit=$code out=$outLog err=$errLog $detail"
    Send-GlassBox 'run.finish' $(if ($code -eq 0) { 'completed' } else { 'failed' }) "Compute job ${Job}: host-native wrapper $(if ($code -eq 0) { 'verified' } else { 'failed' })" $(if ($detail) { $detail } else { "exit $code" })
    $lockStream.Dispose()
  }
  exit $code
}

$listing = Invoke-Native $python.Path @($jobTool, 'jobs') -StdoutOnly
if ($listing.Code -ne 0) { Stop-Refused 'compute_job.py jobs failed.' 2 }
$known = $listing.Output | ConvertFrom-Json
$entry = $known.PSObject.Properties[$Job]
if ($null -eq $entry) { Stop-Refused ("Unknown job '$Job'. Jobs: " + (($known.PSObject.Properties | ForEach-Object { $_.Name }) -join ', ')) 2 }
$spec = $entry.Value

if ([string]::IsNullOrWhiteSpace($RunId)) { $RunId = $Job + '-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') }
if ($RunId -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$') { Stop-Refused 'The run id may hold only letters, digits, dots, dashes and underscores.' 2 }

# Images run by digest only (D-175, F10): a tag, a missing digest or an unpublished image is refused here, not at pull time.
if (-not $ImageLock) { $ImageLock = Join-Path $projectRoot 'compute.images.lock' }
if (-not (Test-Path -LiteralPath $ImageLock -PathType Leaf)) { Stop-Refused "No image lock at $ImageLock." 2 }
$lock = Get-Content -LiteralPath $ImageLock -Raw | ConvertFrom-Json
$digest = [string]$lock.digest
if ($digest -cnotmatch '^sha256:[0-9a-f]{64}$') {
  Stop-Refused "compute.images.lock has no sha256 digest (got '$digest'). CI publishes compute-mentat by digest (compute-images.yml, digests.json); record that digest, never a tag." 2
}
$image = ([string]$lock.image) + '@' + $digest

$hosts = [ordered]@{
  DIGEST            = $digest
  MENTAT_HOST       = ConvertTo-Slash $repoRoot
  LOCALAPPDATA_HOST = ConvertTo-Slash $env:LOCALAPPDATA
  GLASSBOX_HOST     = ConvertTo-Slash $(if ($env:GLASSBOX_EVENTS_DIR) { $env:GLASSBOX_EVENTS_DIR } else { Join-Path $env:USERPROFILE '.claude\glassbox\events' })
}
if ($Job -in 'anchors', 'refit-detect', 'refit-parity', 'refit' -and -not $env:REPORTS_HOST) {
  $hosts['REPORTS_HOST'] = ConvertTo-Slash (Resolve-MentatReportDirectory -ProjectRoot $projectRoot)
}
$containerOf = [ordered]@{ REPORTS_HOST = '/reports'; COLLECT_HOST = '/collect'; EVIDENCE_HOST = '/evidence'; POCKET_IN_HOST = '/work/in'; POCKET_OUT_HOST = '/work/out'
                           ANCHORS_OUT_HOST = '/mentat/mentat/state/giving-anchors' }
foreach ($name in @($containerOf.Keys) + 'LOO_HOST', 'CHE_HOST', 'ZIPC_HOST') {
  $given = [Environment]::GetEnvironmentVariable($name)
  if ($given) { $hosts[$name] = ConvertTo-Slash $given }
}
$maps = @(('/mentat=' + $hosts.MENTAT_HOST), ('/localappdata=' + $hosts.LOCALAPPDATA_HOST), ('/glassbox/events=' + $hosts.GLASSBOX_HOST))
foreach ($name in $containerOf.Keys) { if ($hosts.Contains($name)) { $maps += ($containerOf[$name] + '=' + $hosts[$name]) } }

# --- D075 phase 2: the downstream collector in the container runtime (opt-in) -----------------------------------------------------------------------
# The live ledger is in a Docker named volume; the FEC keys are staged for the host broker ONLY (never mounted, never a container variable); the container gets a key-free
# descriptor. Everything below is refused (exit 2) before Docker is touched, except the checks that need the engine.
$downstreamInfo = $null; $stateHost = $null; $storedMap = $null; $volume = $null; $sharedLock = $null; $inputsHost = $null; $importHost = $null
if ($downstream) {
  if ($Batch -or $MaxRequests -or $Seeds -or $Workers -ne 1 -or $EnableEndpointContract.Count -or $PriorityCandidates) {
    Stop-Refused 'REFUSED: -Batch, -Workers, -MaxRequests, -Seeds, -EnableEndpointContract and -PriorityCandidates belong to -Runtime host; with -Runtime container give --batch, --workers, --max-requests, --seeds /inputs/..., --enable-endpoint-contract and --priority-candidates in -JobArgs or -JobArgsJson.' 2
  }
  $stateHost = if ($env:DOWNSTREAM_STATE_HOST) { $env:DOWNSTREAM_STATE_HOST } else { Join-Path $projectRoot 'state\committee-activity' }
  $planned = Invoke-Native $python.Path (@($jobTool, 'downstream-plan', '--job', $Job, '--') + $JobArgs) -StdoutOnly
  try { $downstreamInfo = $planned.Output | ConvertFrom-Json } catch { $downstreamInfo = $null }
  if ($null -eq $downstreamInfo -or -not $downstreamInfo.ok) { Stop-Refused ('REFUSED: ' + $(if ($downstreamInfo) { $downstreamInfo.error } else { 'compute_job.py downstream-plan gave no answer.' })) 2 }
  $volume = if ($env:DOWNSTREAM_VOLUME) { $env:DOWNSTREAM_VOLUME } else { 'mentat-downstream-frontier' }
  if ($volume -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,60}$') { Stop-Refused 'REFUSED: DOWNSTREAM_VOLUME is not a volume name.' 2 }
  $inputsHost = $env:DOWNSTREAM_INPUTS_HOST; $importHost = $env:DOWNSTREAM_IMPORT_HOST
  $stateBack = $stateHost.TrimEnd('\', '/').Replace('/', '\')
  $hosts['DOWNSTREAM_STATE_HOST'] = ConvertTo-Slash $stateHost
  $hosts['DOWNSTREAM_EXPORT_HOST'] = ConvertTo-Slash (Join-Path $stateHost 'exports')
  $hosts['DOWNSTREAM_VOLUME'] = $volume
  # How the container's paths are RECORDED: the Windows spelling of batches 01 to 03, so the receipt chain, the validator and the reader keep working. List /hoststate first:
  # a stored path under the state folder is READ through the read-only host view.
  $storedMap = '/hoststate=' + $stateBack + ';/frontier=' + $stateBack + ';/export=' + $stateBack + '\exports'
  if ($Job -eq 'collect-downstream-import') { $hosts['DOWNSTREAM_IMPORT_HOST'] = ConvertTo-Slash $(if ($importHost) { $importHost } else { Join-Path $stateHost 'import' }) }
  else {
    $hosts['DOWNSTREAM_INPUTS_HOST'] = ConvertTo-Slash $(if ($inputsHost) { $inputsHost } else { Join-Path $stateHost 'inputs' })
    $storedMap += ';/inputs=' + $hosts['DOWNSTREAM_INPUTS_HOST'].Replace('/', '\')
  }
  $hosts['DOWNSTREAM_STORED_MAP'] = $storedMap
  $maps += ('/export=' + $hosts['DOWNSTREAM_EXPORT_HOST'])
  $maps += ('/hoststate=' + $hosts['DOWNSTREAM_STATE_HOST'])
}

$receiptDirectory = Join-Path $env:LOCALAPPDATA 'Mentat\compute\receipts'
$receipt = Join-Path $receiptDirectory ($RunId + '.json')
$containerName = 'mentat-compute-' + $RunId.ToLowerInvariant()
$composeArguments = @('compose', '-f', $composeFile, 'run', '--rm', '--no-deps', '-T', '--name', $containerName, $Job, 'run', $Job, '--run-id', $RunId, '--') + $JobArgs
$timeoutMinutes = [int]$spec.minutes + 5          # the job enforces its own cap inside the container; this is the backstop

$plan = [ordered]@{
  job = $Job; run_id = $RunId; image = $image; docker = $DockerPath; compose_arguments = $composeArguments
  environment = $hosts; fec_keys = [bool]$spec.keys; network = [bool]$spec.network; timeout_minutes = $timeoutMinutes
  receipt = $receipt; verify_maps = $maps
}
if ($downstream) {
  $plan['runtime'] = 'container'
  $plan['fec_keys_in_container'] = $false
  $plan['downstream'] = [ordered]@{
    job_plan = $downstreamInfo; state_host = $stateHost; volume = $volume; stored_path_map = $storedMap; live_requested = ($JobArgs -contains '--live')
    broker = $(if ($spec.broker) { [ordered]@{ script = $hostPoolTool; scope = 'spending-evidence-v2'; run_id = [string]$downstreamInfo.collector_run_id; archive_root = $stateHost
                                                max_attempts = [int]$downstreamInfo.max_requests; families = [string]$downstreamInfo.families
                                                keys = 'staged for the broker only (owner-only folder, wiped at the end); never mounted into the container' } } else { $null })
    refuses_before_docker = @('state folder under OneDrive or %TEMP%', 'frontier.sqlite.MOVED.json absent or a frontier.sqlite still at the state folder', 'free space below DOWNSTREAM_MIN_FREE_GB (30) plus 0.35 MB per request', 'inputs/import folder missing')
    refuses_after_docker_starts = @('volume missing (create with -InitVolume) or without the label com.mentat.keep=true', 'a collector process or a downstream container already running')
  }
}
if ($PlanOnly) { $plan | ConvertTo-Json -Depth 6; return }

# --- the run ----------------------------------------------------------------------------------------------------------------
$logDirectory = Join-Path $env:LOCALAPPDATA 'Mentat\logs\compute'
$lockDirectory = Join-Path $env:LOCALAPPDATA 'Mentat\compute\locks'
foreach ($folder in $logDirectory, $lockDirectory, $receiptDirectory) { New-Item -ItemType Directory -Force -Path $folder | Out-Null }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$outLog = Join-Path $logDirectory "$Job-$stamp.out.log"
$errLog = Join-Path $logDirectory "$Job-$stamp.err.log"
$summaryLog = Join-Path $logDirectory "$Job.log"

# One run of a job at a time, and never beside the Windows task that still does the same work (F9: the lock is blind across the VM).
$legacyTask = @{ 'anchors' = 'MentatNightlyAnchors'; 'refit-detect' = 'MentatRefitWatcher' }[$Job]
if ($legacyTask) {
  $task = Get-ScheduledTask -TaskName $legacyTask -ErrorAction SilentlyContinue
  if ($task -and $task.State -eq 'Running') { Stop-Refused "$legacyTask is running; a container run would be a second writer." 75 }
}
try { $lockStream = [IO.File]::Open((Join-Path $lockDirectory "$Job.lock"), 'OpenOrCreate', 'ReadWrite', 'None') }
catch [IO.IOException] { Stop-Refused "Another run of $Job holds its lock." 75 }

$staged = $false
$hostPool = $null
$hostPoolDirectory = $null
$dockerCode = $null
$code = 1
$problems = @()
try {
  do {
  Write-Summary "START $Job run=$RunId image=$image"
  Send-GlassBox 'run.start' 'running' "Compute job ${Job}: wrapper start" $image
  $null = Invoke-Native $python.Path @($secretTool, 'sweep') -StdoutOnly       # key files a power-off left behind

  if ($downstream) {
    $refusals = @()
    if ($stateHost -like '*\OneDrive\*' -or ($env:TEMP -and $stateHost -like ($env:TEMP + '*'))) { $refusals += 'the state folder must not be under OneDrive or %TEMP% (Storage Sense wipes %TEMP%)' }
    if (-not (Test-Path -LiteralPath (Join-Path $stateHost 'frontier.sqlite.MOVED.json') -PathType Leaf)) { $refusals += 'frontier.sqlite.MOVED.json is absent: the cutover to the volume has not been recorded' }
    if (Test-Path -LiteralPath (Join-Path $stateHost 'frontier.sqlite') -PathType Leaf) { $refusals += 'a frontier.sqlite still exists in the state folder; the ledger in the volume would fork it' }
    if ($Job -eq 'collect-downstream-import') { if (-not (Test-Path -LiteralPath (Join-Path ($hosts['DOWNSTREAM_IMPORT_HOST'].Replace('/', '\')) 'frontier.sqlite') -PathType Leaf)) { $refusals += 'DOWNSTREAM_IMPORT_HOST holds no frontier.sqlite' } }
    elseif (-not (Test-Path -LiteralPath $hosts['DOWNSTREAM_INPUTS_HOST'] -PathType Container)) { $refusals += 'DOWNSTREAM_INPUTS_HOST is not a folder' }
    $minFree = if ($env:DOWNSTREAM_MIN_FREE_GB) { [double]$env:DOWNSTREAM_MIN_FREE_GB } else { 30 }
    $freeBytes = (New-Object IO.DriveInfo (Split-Path -Qualifier $stateHost)).AvailableFreeSpace
    if ($freeBytes -lt ($minFree * 1GB + [double]$downstreamInfo.max_requests * 0.35MB)) { $refusals += ('free space {0:N1} GB is below the floor ({1:N0} GB plus 0.35 MB per requested attempt)' -f ($freeBytes / 1GB), $minFree) }
    if ($refusals.Count) { $code = 2; $problems += ($refusals | ForEach-Object { "REFUSED: $_" }); break }
  }
  if (-not (Start-DockerEngine)) { $code = 3; $problems += "Docker did not start within $DockerStartSeconds seconds."; break }
  $skew = Get-ClockSkew $image
  if ($null -eq $skew) { $code = 3; $problems += 'Could not read the Docker VM clock (is the image pullable by its digest?).'; break }
  if ([math]::Abs($skew) -gt $MaxClockSkewSeconds) {
    $null = Invoke-Native $WslPath @('-d', 'docker-desktop', '-u', 'root', '-e', 'hwclock', '-s')       # ponytail: WSL2 clocks drift after sleep; unverified until Docker is installed
    $skew = Get-ClockSkew $image
    if ($null -eq $skew -or [math]::Abs($skew) -gt $MaxClockSkewSeconds) {
      $code = 3; $problems += ('The Docker VM clock is off by {0:N1} s and a resync did not fix it; restart Docker Desktop.' -f $skew); break
    }
  }

  if ($downstream) {
    $volumeInfo = Invoke-Native $DockerPath @('volume', 'inspect', '--format', '{{json .Labels}}', $volume) -StdoutOnly
    $volumeLabel = $null
    if ($volumeInfo.Code -eq 0) { try { $volumeLabel = [string](($volumeInfo.Output | ConvertFrom-Json).'com.mentat.keep') } catch { $volumeLabel = $null } }
    if ($volumeInfo.Code -ne 0) {
      if (-not $InitVolume) { $code = 2; $problems += "REFUSED: the Docker volume $volume does not exist; create it once, deliberately, with -InitVolume."; break }
      $created = Invoke-Native $DockerPath @('volume', 'create', '--label', 'com.mentat.keep=true', $volume) -StdoutOnly
      if ($created.Code -ne 0) { $code = 2; $problems += "REFUSED: the Docker volume $volume could not be created."; break }
    }
    elseif ($volumeLabel -cne 'true') { $code = 2; $problems += "REFUSED: the Docker volume $volume lacks the label com.mentat.keep=true; it was not made for this ledger."; break }
    if ($InitVolume) {      # an empty named volume belongs to root; the job runs as uid 10001 with every capability dropped
      $owned = Invoke-Native $DockerPath @('run', '--rm', '--pull=never', '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL', '--cap-add', 'CHOWN', '--security-opt', 'no-new-privileges:true',
        '--mount', "type=volume,source=$volume,target=/frontier", '--entrypoint', 'chown', $image, '10001:10001', '/frontier') -StdoutOnly
      if ($owned.Code -ne 0) { $code = 2; $problems += "The Docker volume $volume could not be handed to the job's uid."; break }
    }
    $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*collect_committee_activity.py*' })
    if ($running.Count) { $code = 75; $problems += ('A collector process is already running (pid ' + (($running | ForEach-Object { $_.ProcessId }) -join ', ') + ').'); break }
    # By compose project and service label, not by container name: the container is named for the run id (mentat-compute-ds-b04), so a name filter never saw an explicit run id (review F5).
    # `docker ps --format '{{.Labels}}'` prints each container's labels as ONE comma-joined key=value string (never a JSON map: `{{json .Labels}}` would print that string quoted); only `docker volume inspect` formats a real map (delta review D1).
    $others = Invoke-Native $DockerPath @('ps', '--filter', 'label=com.docker.compose.project=mentat-compute', '--format', '{{.Labels}}') -StdoutOnly
    $busy = @($others.Output -split "`n" | ForEach-Object { if ($_ -match '(?:^|,)com\.docker\.compose\.service=(collect-downstream[^,\s]*)') { $Matches[1] } })
    if ($busy.Count) { $code = 75; $problems += ('A downstream container is already running (' + ($busy -join ', ') + ').'); break }
    if ($Job -ne 'collect-downstream') {      # the three jobs write one volume: one lock for all of them
      try { $sharedLock = [IO.File]::Open((Join-Path $lockDirectory 'collect-downstream.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
      catch [IO.IOException] { $code = 75; $problems += 'Another downstream run holds the shared lock.'; break }
    }
    New-Item -ItemType Directory -Force -Path (Join-Path $stateHost 'exports') | Out-Null
    if ($Job -eq 'collect-downstream-recover' -and (Test-Path -LiteralPath (Join-Path $stateHost ('responses-' + [string]$downstreamInfo.collector_run_id)) -PathType Container)) {
      # host side, no key, no network: the unpaired or truncated files of a crashed run go aside before the container replays what is left
      $quarantine = Invoke-Native $python.Path @($hostPoolTool, 'quarantine', '--archive-root', $stateHost, '--run-id', [string]$downstreamInfo.collector_run_id) -StdoutOnly
      if ($quarantine.Code -ne 0) { $code = 2; $problems += 'The host quarantine of the crashed run failed; nothing was recovered.'; break }
    }
  }
  if ($spec.keys) {
    $stage = Invoke-Native $python.Path @($secretTool, 'stage', '--run-id', $RunId) -StdoutOnly
    if ($stage.Code -ne 0) { $code = 2; $problems += 'The FEC keys could not be staged (no enabled key, or the DPAPI store is unreadable).'; break }
    $staged = $true
    $hosts['FEC_KEYS_FILE'] = ConvertTo-Slash (($stage.Output | ConvertFrom-Json).path)
  }
  if ($Job -eq 'collect-fanout') {
    $sourceIndex = [Array]::IndexOf($JobArgs, '--source-database')
    if ($sourceIndex -lt 0 -or $sourceIndex + 1 -ge $JobArgs.Length) { $code = 2; $problems += 'Host pool requires an explicit job source database.'; break }
    $hostPoolDirectory = Split-Path -Parent $hosts['FEC_KEYS_FILE']
    $brokerArguments = @($hostPoolTool, '--run-directory', $hostPoolDirectory, '--watch-pid', [string]$PID, '--source-database', $JobArgs[$sourceIndex + 1], '--project-root', $projectRoot)
    $hostPool = Start-Process -FilePath $python.Path -ArgumentList (($brokerArguments | ForEach-Object { Format-Argument $_ }) -join ' ') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $hostPoolDirectory 'host-pool.out.log') -RedirectStandardError (Join-Path $hostPoolDirectory 'host-pool.err.log')
    $hostPoolConfig = Join-Path $hostPoolDirectory 'request-pool.json'
    $brokerDeadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Path -LiteralPath $hostPoolConfig -PathType Leaf) -and -not $hostPool.HasExited -and (Get-Date) -lt $brokerDeadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $hostPoolConfig -PathType Leaf)) { $code = 2; $problems += 'Host request pool did not become ready; collection refused.'; break }
    $hosts['REQUEST_POOL_CONFIG_FILE'] = ConvertTo-Slash $hostPoolConfig
  }
  if ($downstream -and $spec.broker) {
    $evidenceStage = Invoke-Native $python.Path @($secretTool, 'stage', '--run-id', $RunId) -StdoutOnly
    if ($evidenceStage.Code -ne 0) { $code = 2; $problems += 'The FEC keys could not be staged for the host broker (no enabled key, or the DPAPI store is unreadable).'; break }
    $staged = $true
    $hostPoolDirectory = Split-Path -Parent (($evidenceStage.Output | ConvertFrom-Json).path)
    $evidenceArguments = @($hostPoolTool, '--scope', 'spending-evidence-v2', '--run-directory', $hostPoolDirectory, '--watch-pid', [string]$PID, '--project-root', $projectRoot,
      '--run-id', [string]$downstreamInfo.collector_run_id, '--archive-root', $stateHost, '--max-attempts', [string]$downstreamInfo.max_requests, '--families', [string]$downstreamInfo.families)
    $hostPool = Start-Process -FilePath $python.Path -ArgumentList (($evidenceArguments | ForEach-Object { Format-Argument ([string]$_) }) -join ' ') -WindowStyle Hidden -PassThru `
      -RedirectStandardOutput (Join-Path $hostPoolDirectory 'host-pool.out.log') -RedirectStandardError (Join-Path $hostPoolDirectory 'host-pool.err.log')
    $evidenceConfig = Join-Path $hostPoolDirectory 'request-pool.json'
    $evidenceDeadline = (Get-Date).AddSeconds(15)
    while (-not (Test-Path -LiteralPath $evidenceConfig -PathType Leaf) -and -not $hostPool.HasExited -and (Get-Date) -lt $evidenceDeadline) { Start-Sleep -Milliseconds 100 }
    if (-not (Test-Path -LiteralPath $evidenceConfig -PathType Leaf)) { $code = 2; $problems += 'The host evidence broker did not become ready; collection refused.'; break }
    $hosts['REQUEST_POOL_CONFIG_FILE'] = ConvertTo-Slash $evidenceConfig
  }
  foreach ($name in $hosts.Keys) { [Environment]::SetEnvironmentVariable($name, [string]$hosts[$name], 'Process') }
  $composeArguments = @($composeArguments | ForEach-Object { Format-Argument $_ })
  $child = Start-Process -FilePath $DockerPath -ArgumentList ($composeArguments -join ' ') -NoNewWindow -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog
  $null = $child.Handle        # PS 5.1: ExitCode stays empty unless the handle is opened before exit
  if ($child.WaitForExit($timeoutMinutes * 60000)) { $dockerCode = $child.ExitCode }
  else {
    $null = Invoke-Native $DockerPath @('rm', '-f', $containerName)
    $null = Invoke-Native 'taskkill.exe' @('/T', '/F', '/PID', [string]$child.Id)
    $dockerCode = 124
    $problems += "Killed after $timeoutMinutes minutes."
  }

  # The exit code is one input. The job is complete only when its receipt verifies from this side (DE-053).
  $verifyArguments = @($jobTool, 'verify', '--receipt', $receipt, '--job', $Job, '--run-id', $RunId)
  foreach ($map in $maps) { $verifyArguments += '--map'; $verifyArguments += $map }
  $verdict = Invoke-Native $python.Path $verifyArguments -StdoutOnly
  $verified = $false
  try { $answer = $verdict.Output | ConvertFrom-Json; $verified = [bool]$answer.ok; $problems += @($answer.problems) } catch { $problems += 'The receipt check gave no answer.' }
  if ($dockerCode -eq 124) { $code = 124 }
  elseif ($dockerCode -eq 0 -and $verified) { $code = 0 }
  else { $code = 1; if ($dockerCode -ne 0) { $problems += "docker exited $dockerCode" } }
  if ($downstream -and $Job -eq 'collect-downstream-import' -and $code -eq 0) {
    # Host side, after the import verified (review F4): the earlier container runs' receipts go into the state folder, so the re-imported ledger does not see their response folders as legacy crashes.
    $restored = Invoke-Native $python.Path @($volumeTool, 'restore-receipts', '--state', $stateHost, '--expect-sha256', [string]$downstreamInfo.expect_sha256) -StdoutOnly
    try { $restoreAnswer = $restored.Output | ConvertFrom-Json } catch { $restoreAnswer = $null }
    if ($restored.Code -ne 0 -or $null -eq $restoreAnswer) {
      $code = 1; $problems += ('RECEIPT RESTORE FAILED (the ledger itself was imported; repeat only: python downstream_volume.py restore-receipts --state <state> --expect-sha256 <hash>): ' + $(if ($restoreAnswer) { @($restoreAnswer.problems) -join '; ' } else { 'no answer' }))
    }
    else { Write-Summary ('RESTORED receipts of {0} earlier run(s); already present {1}; not restored {2}' -f @($restoreAnswer.restored).Count, @($restoreAnswer.already_present).Count, @($restoreAnswer.not_restored).Count) }
  }
  } while ($false)
}
catch { $code = 1; $problems += "wrapper error: $_" }
finally {
  if ($hostPool) {
    try { [IO.File]::WriteAllBytes((Join-Path $hostPoolDirectory 'owner.stop'), [byte[]]@()) } catch { $code = 1; $problems += 'Host pool owner stopfile could not be written.' }
    if (-not $hostPool.WaitForExit(150000)) { $code = 1; $problems += 'Owned host pool did not stop cleanly; secret cleanup may be incomplete.'; $hostPool.Kill(); $null = $hostPool.WaitForExit(10000) }
  }
  if ($downstream -and $spec.broker -and $hostPool -and $null -ne $dockerCode) {
    # The broker has exited and published BROKER-<run>.json. What it charged, what it journalled and what the collector receipt counts must agree (review F3): a gap is an attempt that
    # reached the provider with no record, so the run is a failure whatever docker said, and nothing is promoted.
    $reconciled = Invoke-Native $python.Path @($volumeTool, 'reconcile', '--state', $stateHost, '--run-id', [string]$downstreamInfo.collector_run_id) -StdoutOnly
    try { $reconciledAnswer = $reconciled.Output | ConvertFrom-Json } catch { $reconciledAnswer = $null }
    if ($reconciled.Code -ne 0 -or $null -eq $reconciledAnswer -or -not $reconciledAnswer.ok) {
      $problems += ('BROKER RECONCILIATION FAILED: ' + $(if ($reconciledAnswer) { @($reconciledAnswer.problems) -join '; ' } else { 'no answer' }))
      if ($code -eq 0) { $code = 1 }
    }
  }
  if ($staged) {
    $wiped = Invoke-Native $python.Path @($secretTool, 'wipe', '--run-id', $RunId) -StdoutOnly
    if ($wiped.Code -ne 0) { $code = 1; $problems += 'THE KEY FILE COULD NOT BE WIPED; delete the folder under %LOCALAPPDATA%\Melange\compute\run\ by hand.' }
  }
  $detail = ($problems | Where-Object { $_ }) -join '; '
  if ($downstream -and $detail -and $code -in 2, 75) { [Console]::Error.WriteLine($detail) }
  if ($detail) { Add-Content -Encoding utf8 -Path $errLog -Value $detail }
  Write-Summary "END exit=$code out=$outLog err=$errLog $detail"
  Send-GlassBox 'run.finish' $(if ($code -eq 0) { 'completed' } else { 'failed' }) "Compute job ${Job}: wrapper $(if ($code -eq 0) { 'verified' } else { 'failed' })" $(if ($detail) { $detail } else { "exit $code" })
  if ($sharedLock) { $sharedLock.Dispose() }
  $lockStream.Dispose()
}
exit $code
