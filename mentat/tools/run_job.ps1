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
$code = 1
$problems = @()
try {
  do {
  Write-Summary "START $Job run=$RunId image=$image"
  Send-GlassBox 'run.start' 'running' "Compute job ${Job}: wrapper start" $image
  $null = Invoke-Native $python.Path @($secretTool, 'sweep') -StdoutOnly       # key files a power-off left behind

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

  if ($spec.keys) {
    $stage = Invoke-Native $python.Path @($secretTool, 'stage', '--run-id', $RunId) -StdoutOnly
    if ($stage.Code -ne 0) { $code = 2; $problems += 'The FEC keys could not be staged (no enabled key, or the DPAPI store is unreadable).'; break }
    $staged = $true
    $hosts['FEC_KEYS_FILE'] = ConvertTo-Slash (($stage.Output | ConvertFrom-Json).path)
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
  } while ($false)
}
catch { $code = 1; $problems += "wrapper error: $_" }
finally {
  if ($staged) {
    $wiped = Invoke-Native $python.Path @($secretTool, 'wipe', '--run-id', $RunId) -StdoutOnly
    if ($wiped.Code -ne 0) { $code = 1; $problems += 'THE KEY FILE COULD NOT BE WIPED; delete the folder under %LOCALAPPDATA%\Melange\compute\run\ by hand.' }
  }
  $detail = ($problems | Where-Object { $_ }) -join '; '
  if ($detail) { Add-Content -Encoding utf8 -Path $errLog -Value $detail }
  Write-Summary "END exit=$code out=$outLog err=$errLog $detail"
  Send-GlassBox 'run.finish' $(if ($code -eq 0) { 'completed' } else { 'failed' }) "Compute job ${Job}: wrapper $(if ($code -eq 0) { 'verified' } else { 'failed' })" $(if ($detail) { $detail } else { "exit $code" })
  $lockStream.Dispose()
}
exit $code
