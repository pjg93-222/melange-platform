<#
.SYNOPSIS
  Retire abandoned rows from the shared Glass Box event stream.

.DESCRIPTION
  The Glass Box viewer derives a row per lane/source/run from the event stream
  and shows the latest status for each. A lane that dies without writing a
  terminal event therefore sits at "running" forever, so the dashboard fills
  with work that stopped hours ago and real activity gets lost in the noise.

  This reaps ONLY abandoned rows: latest status is a live one (running/queued/
  dispatched/pending), the newest event for that row is older than -StaleMinutes,
  AND no live process is still writing it. Rows that are legitimately blocked or
  completed are never touched -- a blocked lane is waiting on purpose and a
  completed lane is a result.

  Nothing is deleted. A terminal "abandoned" event is appended so the row goes
  quiet, and the original events stay on disk as evidence.
#>
param(
    [string]$EventsDirectory = "C:\Users\pgoff\.claude\glassbox\events",
    [int]$StaleMinutes = 20,
    [switch]$Apply,
    [string]$Lane = "claude"
)

$ErrorActionPreference = 'Stop'

# Statuses that mean "this row is still in flight". Deliberately excludes
# blocked (waiting on purpose) and completed/failed (already terminal).
$LiveStatuses = @('running', 'queued', 'dispatched', 'pending', 'in_progress', 'started')
$TerminalStatuses = @('completed', 'complete', 'failed', 'blocked', 'abandoned', 'cancelled', 'needs-review')

if (-not (Test-Path $EventsDirectory)) { throw "events directory not found: $EventsDirectory" }

$cutoff = (Get-Date).ToUniversalTime().AddMinutes(-$StaleMinutes)

# Any PID currently writing the stream is by definition not abandoned.
$livePids = @{}
foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) { $livePids[$p.Id] = $true }

$rows = @{}
foreach ($f in (Get-ChildItem $EventsDirectory -File -Filter '*.json' -ErrorAction SilentlyContinue)) {
    try { $e = Get-Content $f.FullName -Raw -ErrorAction Stop | ConvertFrom-Json } catch { continue }
    $lane   = if ($e.lane)   { [string]$e.lane }   else { 'unknown' }
    $source = if ($e.source) { [string]$e.source } else { 'unknown' }
    $runId  = if ($e.run_id) { [string]$e.run_id } else { 'unknown' }
    $key = '{0}|{1}|{2}' -f $lane, $source, $runId

    # PID is encoded in the filename by the emitters (evt-<ts>-<tag>-<pid>-...).
    $writerPid = $null
    if ($f.Name -match '-(\d{2,7})-') { $writerPid = [int]$Matches[1] }

    $existing = $rows[$key]
    if ($null -eq $existing -or $f.LastWriteTimeUtc -gt $existing.When) {
        $rows[$key] = [pscustomobject]@{
            Lane = $lane; Source = $source; RunId = $runId
            Status = if ($e.status) { [string]$e.status } else { 'unknown' }
            Summary = [string]$e.summary
            When = $f.LastWriteTimeUtc
            Pid = $writerPid
            File = $f.Name
        }
    }
}

$abandoned = @()
$kept = @()
foreach ($r in $rows.Values) {
    $isLive = $LiveStatuses -contains $r.Status
    $isStale = $r.When -lt $cutoff
    $pidAlive = ($null -ne $r.Pid) -and $livePids.ContainsKey($r.Pid)
    if ($isLive -and $isStale -and -not $pidAlive) { $abandoned += $r } else { $kept += $r }
}

Write-Output ("rows on stream        : " + $rows.Count)
Write-Output ("abandoned (reapable)  : " + $abandoned.Count)
Write-Output ("kept                  : " + $kept.Count)
Write-Output ""
Write-Output "=== KEPT (blocked / completed / live) ==="
$kept | Sort-Object When -Descending | Select-Object -First 20 |
    Format-Table @{n='age_min';e={[int]((Get-Date).ToUniversalTime() - $_.When).TotalMinutes}},
                 Status, Lane, Source, @{n='pid';e={$_.Pid}} -AutoSize
Write-Output "=== ABANDONED ==="
$abandoned | Sort-Object When | Format-Table @{n='age_min';e={[int]((Get-Date).ToUniversalTime() - $_.When).TotalMinutes}},
                 Status, Lane, Source, RunId, @{n='pid';e={$_.Pid}} -AutoSize

if (-not $Apply) { Write-Output "`n(dry run; pass -Apply to retire these rows)"; return }

$n = 0
foreach ($r in $abandoned) {
    $payload = @{
        schema_version = 1
        timestamp_utc  = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        lane = $r.Lane; source = $r.Source; run_id = $r.RunId
        kind = 'lane.abandoned'; status = 'abandoned'; stage = 'reaped'
        summary = ('Abandoned: no event for {0} min and writer process is gone' -f `
                   [int]((Get-Date).ToUniversalTime() - $r.When).TotalMinutes)
        detail = ('Retired by reap_abandoned_glassbox_tasks. Last status was "{0}". ' +
                  'Original events are retained; only a terminal marker was appended.') -f $r.Status
        current = 0; total = 0
        reaped_from_pid = $r.Pid
    }
    $name = ('evt-{0}-reaper-{1}-{2}.json' -f `
        (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff'), $PID, $n) -replace '[^A-Za-z0-9._-]+','-'
    $tmp = Join-Path $EventsDirectory ($name + '.tmp')
    ($payload | ConvertTo-Json -Depth 6) | Out-File -FilePath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination (Join-Path $EventsDirectory $name) -Force
    $n++
    Start-Sleep -Milliseconds 3
}
Write-Output ("`nretired {0} abandoned row(s); nothing deleted" -f $n)
