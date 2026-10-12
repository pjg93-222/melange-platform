<#
.SYNOPSIS
  Keep the congress.gov full-text collector running across cf_clearance
  rotations. Re-mints the cookie in the browser and relaunches with --resume.

.DESCRIPTION
  congress.gov rotates cf_clearance roughly every 20-30 minutes. The collector
  allows itself exactly one immediate replay plus one clearance refresh; when
  that refresh lands mid-rotation it stops cleanly with:

    "Harvest stopped after one permitted immediate replay/clearance refresh
     failed; no candidate was deleted or silently skipped."

  That is correct fail-closed behaviour -- it just needs someone to mint a fresh
  cookie and start it again, which is what this does. Restarting is free:
  --resume re-verifies collected pages locally and issues no request for them.

  Re-minting means navigating the user's own headful browser to an ordinary bill
  page and letting Cloudflare's managed challenge complete by itself, then
  reading the cookie jar. No challenge is solved, forged, or bypassed. If the
  browser stays challenged, this waits.
#>
param(
    [string]$Workspace = "C:\Users\pgoff\OneDrive\Desktop\Melange Systems",
    [string]$OutRelative = "audits\legfix5\congress_advanced_pages_20260725\text_relevance_httr_full_20260727z",
    [string]$RecordsRelative = "audits\legfix5\congress_advanced_pages_20260725\congress_advanced_records.ndjson",
    [string]$RRuntime = "C:\Program Files\R\R-4.6.0\bin\x64\Rterm.exe",
    # Needed so a machine restart or a browser crash is recoverable without a
    # human: re-minting clearance drives the browser, so the browser must exist.
    [string]$ChromeExe = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [string]$ChromeProfile = "C:\Users\pgoff\.melange\chrome-harvest",
    [string]$EventsDirectory = "C:\Users\pgoff\.claude\glassbox\events",
    [int]$Workers = 6,
    [double]$Delay = 1,
    [int]$Port = 9222,
    [int]$PollSeconds = 30,
    [int]$MaxRelaunches = 200,
    [int]$MaxHours = 12,
    [string]$Lane = "codex"
)

$ErrorActionPreference = 'Stop'
$out = Join-Path $Workspace $OutRelative
$runId = 'fulltext-clearance-supervisor-{0}' -f (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
if (-not (Test-Path $EventsDirectory)) { New-Item -ItemType Directory $EventsDirectory -Force | Out-Null }

function Emit {
    param([string]$Status, [string]$Stage, [string]$Summary, [string]$Detail, [int]$Current, [int]$Total)
    $payload = @{
        schema_version = 1
        timestamp_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        lane = $Lane; source = 'congress-fulltext-clearance-supervisor'; run_id = $runId
        kind = 'supervisor.progress'; status = $Status; stage = $Stage
        summary = $Summary; detail = $Detail; current = $Current; total = $Total
    }
    $name = ('evt-{0}-clrsup-{1}-{2}.json' -f `
        (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff'), $PID, $Stage) -replace '[^A-Za-z0-9._-]+','-'
    $tmp = Join-Path $EventsDirectory ($name + '.tmp')
    ($payload | ConvertTo-Json -Depth 6) | Out-File -FilePath $tmp -Encoding utf8
    Move-Item -LiteralPath $tmp -Destination (Join-Path $EventsDirectory $name) -Force
}

function Receipts { @(Get-ChildItem (Join-Path $out 'receipts') -Recurse -File -Filter 'receipt.json' -ErrorAction SilentlyContinue).Count }

# The corpus size is fixed. Any progress event for this lane that reports a
# different denominator is describing a subset (e.g. a filtered remaining-work
# queue) and will make a viewer flip between two different percentages for what
# looks like the same job. That happened once; this makes it self-detecting.
$CorpusTotal = 18557

function Assert-SingleProgressDenominator {
    # Only events written by a process that is STILL ALIVE can describe current
    # state. A dead emitter's last event lingers on the stream for as long as the
    # window allows and will otherwise raise a false alarm about a conflict that
    # was already fixed -- which is exactly what happened the first time.
    $cut = (Get-Date).ToUniversalTime().AddMinutes(-3)
    $alive = @{}
    foreach ($proc in (Get-Process -ErrorAction SilentlyContinue)) { $alive[$proc.Id] = $true }
    $totals = @{}
    foreach ($f in (Get-ChildItem $EventsDirectory -File -Filter '*.json' -ErrorAction SilentlyContinue |
                    Where-Object { $_.LastWriteTimeUtc -ge $cut })) {
        # Skip events whose writing process has exited; they are history.
        if ($f.Name -match '-(\d{2,7})-' -and -not $alive.ContainsKey([int]$Matches[1])) { continue }
        try { $e = Get-Content $f.FullName -Raw -ErrorAction Stop | ConvertFrom-Json } catch { continue }
        if (([string]$e.kind) -notlike '*progress*') { continue }
        if (([string]$e.source) -notlike '*congress*full*text*') { continue }
        $names = $e.PSObject.Properties.Name
        if ($names -notcontains 'total' -or $null -eq $e.total) { continue }
        $t = [long]$e.total
        if ($t -le 0) { continue }
        $key = '{0}|{1}' -f ([string]$e.lane), ([string]$e.source)
        if (-not $totals.ContainsKey($key)) { $totals[$key] = @{} }
        $totals[$key][$t] = $true
    }
    $offenders = @()
    foreach ($k in $totals.Keys) {
        $distinct = @($totals[$k].Keys)
        if ($distinct.Count -gt 1 -or ($distinct.Count -eq 1 -and $distinct[0] -ne $CorpusTotal)) {
            $offenders += ('{0} reporting total(s) {1}' -f $k, ($distinct -join ', '))
        }
    }
    if ($offenders.Count -gt 0) {
        Emit -Status 'failed' -Stage 'progress-denominator-conflict' `
             -Summary ('PROGRESS DENOMINATOR CONFLICT: {0}' -f ($offenders -join ' ; ')) `
             -Detail ([string]::Format(
                      "Every congress full-text progress event must report total={0}. A different " +
                      "denominator means a subset is being published as if it were corpus coverage, " +
                      "which makes the dashboard flip between two percentages for one job.", $CorpusTotal)) `
             -Current (Receipts) -Total $CorpusTotal
        return $false
    }
    return $true
}

# The collector's own progress.json counts only the FILTERED queue, so once the
# remaining-records filter is in play its total is the outstanding count rather
# than 18,557 and any viewer bound to it badly understates the run. This writes
# the true overall figure for display.
$OverallProgressPath = Join-Path $out 'overall_progress.json'
function Write-OverallProgress {
    param([string]$Status = 'running', [string]$Detail = '')
    $have = Receipts
    $payload = @{
        schema_version = 1
        updated_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        lane = $Lane
        source = 'congress-fulltext-overall'
        run_id = $runId
        kind = 'collector.progress'
        status = $Status
        stage = 'overall-corpus-coverage'
        current = $have
        total = 18557
        summary = ('{0}/18557 official pages collected ({1}%)' -f $have, [math]::Round(100.0 * $have / 18557, 1))
        detail = $Detail
        output_directory = $out
    }
    try { ($payload | ConvertTo-Json -Depth 6) | Out-File -FilePath ($OverallProgressPath + '.tmp') -Encoding utf8
          Move-Item -LiteralPath ($OverallProgressPath + '.tmp') -Destination $OverallProgressPath -Force } catch {}
}

# Browser re-mint. Long enough for the managed challenge to finish on its own --
# probing at 8s reports "Just a moment" even when the flow is about to succeed.
$remintScript = Join-Path $env:TEMP ("clrsup_remint_{0}.R" -f $PID)
@'
suppressPackageStartupMessages({ library(chromote) })
args <- commandArgs(trailingOnly = TRUE)
port <- as.integer(args[[1]])
set_default_chromote_object(Chromote$new(browser = ChromeRemote$new(host = "127.0.0.1", port = port)))
s <- ChromoteSession$new(); s$default_timeout <- 180
try({ s$Page$navigate("https://www.congress.gov/bill/117th-congress/house-bill/3664/text")
      s$Page$loadEventFired(wait_ = TRUE) }, silent = TRUE)
deadline <- Sys.time() + 90; ok <- FALSE
repeat {
  Sys.sleep(6)
  m <- tryCatch(s$Runtime$evaluate("document.body && document.body.innerText.indexOf('Shown Here:') >= 0")$result$value,
                error = function(e) FALSE)
  if (isTRUE(m)) { ok <- TRUE; break }
  if (Sys.time() > deadline) break
}
try(s$close(), silent = TRUE)
cat("REMINT_OK:", ok, "\n")
'@ | ForEach-Object { [System.IO.File]::WriteAllText($remintScript, $_, (New-Object System.Text.UTF8Encoding($false))) }
# NB: written WITHOUT a BOM. Out-File -Encoding utf8 on PowerShell 5.1 emits one,
# and Rterm fails to parse the file with: unexpected input in "<BOM>".

function Test-DebugPort {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $ok = $c.ConnectAsync('127.0.0.1', $Port).Wait(3000)
        $c.Close()
        return $ok
    } catch { return $false }
}

# Re-minting drives the browser, so the browser has to exist. Chrome dying is a
# distinct failure from a cookie rotation and was previously unhandled: the
# supervisor kept heartbeating while every re-mint hung against a closed port,
# and collection sat still with no error anywhere.
function Assert-Browser {
    if (Test-DebugPort) { return $true }
    Emit -Status 'running' -Stage 'chrome-restart' `
         -Summary 'Chrome debug port is closed; restarting the harvest browser' `
         -Detail ("Port {0} did not accept a connection. Re-mint requires a live browser." -f $Port) `
         -Current (Receipts) -Total $CorpusTotal
    Start-Process $ChromeExe -ArgumentList @(
        "--remote-debugging-port=$Port", "--user-data-dir=$ChromeProfile",
        '--no-first-run', '--no-default-browser-check',
        'https://www.congress.gov/bill/117th-congress/house-bill/3664/text'
    ) | Out-Null
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 3
        if (Test-DebugPort) { return $true }
    }
    Emit -Status 'blocked' -Stage 'chrome-unavailable' `
         -Summary 'Chrome did not come up on the debug port' `
         -Detail 'Waiting rather than relaunching the collector into a dead browser.' `
         -Current (Receipts) -Total $CorpusTotal
    return $false
}

function Invoke-Remint {
    if (-not (Assert-Browser)) { return $false }
    $o = & $RRuntime --no-echo --no-restore --file="$remintScript" --args $Port 2>&1
    return (($o | Out-String) -match 'REMINT_OK:\s*TRUE')
}

function Start-Collector {
    $lock = Join-Path $out '.collector-lock'
    if ((@(Get-Process Rterm -ErrorAction SilentlyContinue)).Count -eq 0 -and (Test-Path $lock)) {
        Remove-Item -LiteralPath $lock -Recurse -Force -ErrorAction SilentlyContinue
    }
    # Feed the collector ONLY outstanding work. Its resume path decompresses and
    # re-hashes every collected page, so after a clearance rotation at N records
    # the next run would pay N verifications before fetching anything -- a cost
    # that grows with progress and resets the progress counter to zero each time.
    $remaining = Join-Path $out 'records_remaining.ndjson'
    $filter = & 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe' `
        (Join-Path $Workspace 'fec\legislation\build_remaining_records.py') `
        --records (Join-Path $Workspace $RecordsRelative) `
        --out-dir $out --emit $remaining 2>&1
    $left = 0
    foreach ($line in $filter) { if ("$line" -match 'remaining to fetch\s*:\s*(\d+)') { $left = [int]$Matches[1] } }
    Emit -Status 'running' -Stage 'filtered' `
         -Summary ("Queue narrowed to {0} outstanding record(s)" -f $left) `
         -Detail 'Collected pages are excluded from the input, so no resume re-verification is paid.' `
         -Current (Receipts) -Total 18557
    if ($left -le 0) { return $null }

    $recArg = $remaining.Substring($Workspace.Length + 1)
    $a = @('--no-echo','--no-restore','--file=fec\harvest_congress_text_httr.R','--args',
           '--records', $recArg, '--out', $OutRelative,
           '--limit','0','--workers',"$Workers",'--delay',"$Delay",
           '--host','127.0.0.1','--port',"$Port",'--resume')
    $log = Join-Path $out ('supervised_{0}.log' -f (Get-Date).ToString('HHmmss'))
    Start-Process $RRuntime -ArgumentList $a -WorkingDirectory $Workspace -PassThru `
        -RedirectStandardOutput $log -RedirectStandardError ($log + '.err')
}

Emit -Status 'running' -Stage 'start' `
     -Summary ("Clearance supervisor armed at {0} receipts" -f (Receipts)) `
     -Detail 'Relaunches the collector with --resume after each cf_clearance rotation. Resume issues no request for pages already collected.' `
     -Current (Receipts) -Total 18557

$relaunches = 0
$deadline = (Get-Date).AddHours($MaxHours)
$proc = $null

while ((Get-Date) -lt $deadline -and $relaunches -lt $MaxRelaunches) {
    $alive = (@(Get-Process Rterm -ErrorAction SilentlyContinue)).Count -gt 0
    if (-not $alive) {
        $have = Receipts
        if ($have -ge 18557) {
            Emit -Status 'completed' -Stage 'done' -Summary "All 18,557 records collected" `
                 -Detail 'Collector reported no work remaining.' -Current $have -Total 18557
            break
        }
        # The usual reason it is not alive is a cf_clearance rotation.
        $ok = Invoke-Remint
        if (-not $ok) {
            Emit -Status 'blocked' -Stage 'remint-failed' `
                 -Summary 'Browser still challenged; waiting rather than relaunching' `
                 -Detail 'No bypass attempted. Will retry on the next poll.' -Current $have -Total 18557
            Start-Sleep -Seconds ($PollSeconds * 4)
            continue
        }
        $proc = Start-Collector
        $relaunches++
        Emit -Status 'running' -Stage 'relaunched' `
             -Summary ("Clearance re-minted; collector relaunched ({0}) at {1} receipts" -f $relaunches, $have) `
             -Detail 'Resume verifies collected pages locally and re-requests none of them.' `
             -Current $have -Total 18557
        Start-Sleep -Seconds 45
    } else {
        Emit -Status 'running' -Stage 'heartbeat' `
             -Summary ("{0}/18557 collected; collector healthy" -f (Receipts)) `
             -Detail ("Relaunches so far: {0}" -f $relaunches) -Current (Receipts) -Total 18557
        Write-OverallProgress -Status 'running' -Detail ("Relaunches so far: {0}" -f $relaunches)
        [void](Assert-SingleProgressDenominator)
        Start-Sleep -Seconds $PollSeconds
    }
}

Emit -Status 'completed' -Stage 'exit' `
     -Summary ("Supervisor exiting at {0} receipts after {1} relaunch(es)" -f (Receipts), $relaunches) `
     -Detail 'Bounded by MaxHours/MaxRelaunches.' -Current (Receipts) -Total 18557
Remove-Item -LiteralPath $remintScript -Force -ErrorAction SilentlyContinue
