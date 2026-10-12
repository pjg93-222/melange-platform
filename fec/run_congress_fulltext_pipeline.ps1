<#
.SYNOPSIS
  Unattended, self-healing orchestrator for the congress.gov official-full-text
  collection lane. Double-click it (or point a Scheduled Task at it) and walk
  away: it preflights the environment, launches and supervises
  fec\harvest_congress_text_httr.R, recovers from crashes / stalls / Cloudflare
  blocks on its own, and finishes with a consolidated catchnet report.

.DESCRIPTION
  WHY THIS EXISTS
    Until now the lane only survived because an agent watched it and restarted
    it by hand. Every recovery decision that agent made is encoded here:

      * process exited having satisfied its accounting assertion -> success
      * process exited early / crashed                            -> classify
        stderr, warm the browser, relaunch with --resume
      * process alive but progress frozen for N minutes           -> stalled,
        treated as blocked: re-mint clearance from the live browser; if still
        frozen after the hard-stall threshold, stop the process tree and
        relaunch with --resume
      * congress.gov serving a Cloudflare challenge               -> WAIT with
        escalating sleeps. Never relaunch into a block.

    --resume is free: a page that already has an accepted receipt plus its
    gzipped body is skipped with no HTTP request at all, so relaunching costs
    only process startup.

  WHAT IT NEVER DOES
    * It never attempts to solve, bypass, or automate past a CAPTCHA or bot
      check. It navigates the harvest browser to an ordinary bill-text URL and
      lets Cloudflare's own JavaScript run -- that is normal browsing. If the
      browser stays challenged, the only action taken is to wait.
    * It never deletes or overwrites collected evidence. receipts,
      accepted_html, quarantine, failure_attempts and batches are immutable by
      design. An "Immutable receipt collision" is reported as a FATAL condition
      requiring a NEW output directory -- never as something to clean up.
    * It never modifies fec\harvest_congress_text_httr.R. It wraps it.
    * It never starts a second collector against an output directory that
      already has a live one. If a collector is already running there it
      ADOPTS and supervises it instead (-AdoptRunningCollector, default on).

  GLASS BOX
    Nothing here runs invisibly. A pipeline-level progress event is written to
    the shared stream every poll, plus an event at every transition (preflight,
    block, relaunch, catchnet round, terminal state). Event filenames are
    unique per PROCESS (UTC timestamp with milliseconds + PID + tag), never
    derived from configuration -- deriving them from config is what made two
    runs of the same config collide once before.

.PARAMETER Workspace
  Repo root. All *Relative parameters resolve against it, and the collector
  runs with this as its working directory.

.PARAMETER RecordsRelative
  Discovery NDJSON fed to the collector as --records. Must exist and be
  non-empty or preflight refuses to start.

.PARAMETER OutRelative
  Collector output directory (--out). This is the run's identity: process
  matching, adoption, stall detection and the catchnet all key off it.

.PARAMETER PriorReceiptsRelative
  Read-only receipts from an earlier harvest (--prior_receipts), used to verify
  re-fetched bodies against the hash the original run recorded. Pass "" to omit.

.PARAMETER Limit
  --limit passed to the collector. 0 = every record. Use a small number
  (e.g. 8) with a throwaway -OutRelative to smoke-test this orchestrator.

.PARAMETER LaunchViewer
  Launch fec\open_glassbox_process_matrix.ps1 if no viewer is already running.
  Default ON. Launched via powershell -Command, never -File: the viewer takes
  string[] parameters and those do not bind under -File (the window exits
  immediately).

.PARAMETER NoViewer
  Suppress the viewer. Use THIS, not -LaunchViewer:$false, from a Scheduled
  Task or any other -File invocation: under -File PowerShell passes "$false" as
  a literal string and the switch fails to bind, killing the run before it
  starts. -NoViewer is presence/absence only, so it binds everywhere.

.PARAMETER PreflightOnly
  Run preflight, emit its verdict, and exit without launching the collector.

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File fec\run_congress_fulltext_pipeline.ps1

.EXAMPLE
  # Scheduled Task command line (see also -PreflightOnly for a dry check):
  #   Program:   powershell.exe
  #   Arguments: -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Users\pgoff\OneDrive\Desktop\Melange Systems\fec\run_congress_fulltext_pipeline.ps1" -NoViewer
  #   Start in:  C:\Users\pgoff\OneDrive\Desktop\Melange Systems
  # Use -NoViewer, never -LaunchViewer:$false -- see the -NoViewer parameter note.

.OUTPUTS
  Exit codes
    0  every selected record accounted for and no throttled record unresolved
    2  preflight could not be satisfied within -PreflightTimeoutMinutes
    3  consecutive-recovery cap reached
    4  throttled/blocked records still unresolved after every catchnet round
    5  accounting gap: selected count != accounted count, or no manifest
    6  refused: a collector is already live on this output directory and
       -AdoptRunningCollector was disabled
    7  FATAL: immutable receipt collision -- this run needs a NEW output
       directory. Nothing was deleted.
    8  wall-clock deadline (-MaxHours) exceeded
    9  fatal configuration error reported by the collector
#>
[CmdletBinding()]
param(
    # --- what to collect -----------------------------------------------------
    [string]$Workspace              = "C:\Users\pgoff\OneDrive\Desktop\Melange Systems",
    [string]$RecordsRelative        = "audits\legfix5\congress_advanced_pages_20260725\congress_advanced_records.ndjson",
    [string]$OutRelative            = "audits\legfix5\congress_advanced_pages_20260725\text_relevance_httr_full_20260727z",
    [string]$PriorReceiptsRelative  = "audits\legfix5\congress_advanced_pages_20260725\text_relevance_httr_full_stage_20260726\receipts",
    [string]$CollectorScript        = "fec\harvest_congress_text_httr.R",

    # --- collector tuning (passed through verbatim) --------------------------
    [int]$Limit                     = 0,
    [int]$Workers                   = 6,
    [int]$Attempts                  = 6,
    [double]$MinDelay               = 2,
    [double]$MaxDelay               = 300,

    # --- runtimes ------------------------------------------------------------
    [string]$RRuntime               = "C:\Program Files\R\R-4.6.0\bin\x64\Rterm.exe",
    [string]$ChromeExe              = "C:\Program Files\Google\Chrome\Application\chrome.exe",
    [string]$ChromeProfile          = "C:\Users\pgoff\.melange\chrome-harvest",
    [int]$Port                      = 9222,

    # --- content verification (never a challenge bypass) ---------------------
    [string]$ContentProbeUrl        = "https://www.congress.gov/bill/117th-congress/house-bill/3664/text",
    [string]$ContentMarker          = "Shown Here:",
    [int]$ContentSettleSeconds      = 25,

    # --- supervision --------------------------------------------------------
    [int]$PollSeconds               = 60,
    [int]$StallMinutes              = 12,
    [int]$HardStallMinutes          = 30,
    [int]$MaxConsecutiveRecoveries  = 8,
    [int]$RecoveryCooldownSeconds   = 120,
    [int]$MaxCooldownSeconds        = 1800,
    [int]$PreflightTimeoutMinutes   = 45,
    [int]$MaxHours                  = 48,
    [switch]$AdoptRunningCollector  = $true,

    # --- catchnet -----------------------------------------------------------
    [int]$CatchnetRounds            = 3,
    [int]$CatchnetCooldownSeconds   = 600,

    # --- glass box ----------------------------------------------------------
    [string]$EventsDirectory        = "C:\Users\pgoff\.claude\glassbox\events",
    [string]$Lane                   = "codex",
    [switch]$LaunchViewer           = $true,
    # -File invocations (Scheduled Task) must use -NoViewer: "-LaunchViewer:$false"
    # arrives as the literal string "$false" under -File and fails to bind.
    [switch]$NoViewer,
    [string]$ViewerScript           = "fec\open_glassbox_process_matrix.ps1",
    [string]$ViewerWindowTitle      = "GLASS BOX - PROCESS MATRIX",

    # --- modes --------------------------------------------------------------
    [switch]$PreflightOnly
)

# Never let a transient cmdlet error kill an unattended run; every risky call is
# wrapped explicitly below.
$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

# ===========================================================================
# Paths and run identity
# ===========================================================================

function Resolve-Under {
    param([string]$Base, [string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return "" }
    if ([System.IO.Path]::IsPathRooted($Path)) { return [System.IO.Path]::GetFullPath($Path) }
    return [System.IO.Path]::GetFullPath((Join-Path $Base $Path))
}

$RecordsPath       = Resolve-Under $Workspace $RecordsRelative
$OutDir            = Resolve-Under $Workspace $OutRelative
$PriorReceiptsPath = Resolve-Under $Workspace $PriorReceiptsRelative
$ViewerPath        = Resolve-Under $Workspace $ViewerScript
$CollectorPath     = Resolve-Under $Workspace $CollectorScript

$ProgressPath   = Join-Path $OutDir 'progress.json'
$ManifestPath   = Join-Path $OutDir 'manifest.json'
$LogDir         = Join-Path $OutDir 'orchestrator_logs'
$StatePath      = Join-Path $OutDir 'orchestrator_state.json'
$ReportPath     = Join-Path $OutDir 'catchnet_report.json'
$OutLeaf        = Split-Path -Leaf $OutDir

$RunStamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
# Run identity is process-scoped, NOT configuration-derived. Two runs of the
# same configuration must never share an id or an event filename.
$RunId    = 'congress-fulltext-pipeline-{0}-{1}' -f $RunStamp, $PID

foreach ($d in @($EventsDirectory, $OutDir, $LogDir)) {
    if (-not [string]::IsNullOrWhiteSpace($d) -and -not (Test-Path -LiteralPath $d)) {
        try { New-Item -ItemType Directory -Path $d -Force | Out-Null } catch { }
    }
}

$Script:TotalRecords        = 0
$Script:Cycle               = 0
$Script:TotalRecoveries     = 0
$Script:ConsecutiveFailures = 0
$Script:BlocksObserved      = 0
$Script:ClearanceReminted   = 0
$Script:BrowserChallenged   = 0
$Script:CatchnetRoundsRun   = 0
$Script:LastCurrent         = 0

# ===========================================================================
# Glass Box emitters (atomic tmp-then-move, per-process filenames)
# ===========================================================================

function Write-GlassboxEvent {
    param([hashtable]$Payload, [string]$Tag)
    $name = ('evt-{0}-fulltextpipe-{1}-{2}.json' -f `
        (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff'), $PID, $Tag) -replace '[^A-Za-z0-9._-]+', '-'
    $tmp   = Join-Path $EventsDirectory ($name + '.tmp')
    $final = Join-Path $EventsDirectory $name
    try {
        ($Payload | ConvertTo-Json -Depth 8) | Out-File -FilePath $tmp -Encoding utf8
        Move-Item -LiteralPath $tmp -Destination $final -Force
    } catch { }
}

function Emit {
    param(
        [string]$Tag,
        [string]$Status,
        [string]$Stage,
        [string]$Summary,
        [string]$Detail   = '',
        [int]$Current     = -1,
        [hashtable]$Extra
    )
    if ($Current -lt 0) { $Current = $Script:LastCurrent }
    $payload = @{
        schema_version    = 1
        timestamp_utc     = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        lane              = $Lane
        source            = 'congress-fulltext-pipeline'
        run_id            = $RunId
        kind              = 'pipeline.progress'
        status            = $Status
        stage             = $Stage
        summary           = $Summary
        detail            = $Detail
        current           = $Current
        total             = $Script:TotalRecords
        cycle             = $Script:Cycle
        recoveries        = $Script:TotalRecoveries
        consecutive_failures = $Script:ConsecutiveFailures
        blocks_observed   = $Script:BlocksObserved
        clearance_remints = $Script:ClearanceReminted
        catchnet_rounds   = $Script:CatchnetRoundsRun
        output_directory  = $OutDir
        orchestrator_pid  = $PID
    }
    if ($Extra) { foreach ($k in $Extra.Keys) { $payload[$k] = $Extra[$k] } }
    Write-GlassboxEvent -Payload $payload -Tag $Tag
    Write-Host ('[{0}] {1,-22} {2}' -f (Get-Date).ToString('HH:mm:ss'), $Stage, $Summary)
    if ($Detail) { Write-Host ('             {0}' -f $Detail) }
}

function Write-JsonAtomic {
    param([string]$Path, $Value, [int]$Depth = 10)
    $tmp = "$Path.$PID.tmp"
    try {
        ($Value | ConvertTo-Json -Depth $Depth) | Out-File -FilePath $tmp -Encoding utf8
        Move-Item -LiteralPath $tmp -Destination $Path -Force
        return $true
    } catch {
        try { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue } catch { }
        return $false
    }
}

function Save-OrchestratorState {
    param([string]$Phase, [string]$TerminalState = '')
    Write-JsonAtomic -Path $StatePath -Value ([pscustomobject]@{
        schema             = 'congress-fulltext-pipeline-state-v1'
        run_id             = $RunId
        orchestrator_pid   = $PID
        updated_at         = (Get-Date).ToUniversalTime().ToString('u')
        phase              = $Phase
        terminal_state     = $TerminalState
        cycle              = $Script:Cycle
        total_recoveries   = $Script:TotalRecoveries
        consecutive_failures = $Script:ConsecutiveFailures
        blocks_observed    = $Script:BlocksObserved
        clearance_remints  = $Script:ClearanceReminted
        catchnet_rounds    = $Script:CatchnetRoundsRun
        current            = $Script:LastCurrent
        total              = $Script:TotalRecords
    }) -Depth 5 | Out-Null
}

# ===========================================================================
# Chrome DevTools helpers
#
# Everything here is ordinary browsing: open a tab on a normal URL, let the
# page's own scripts run, read the resulting title/text. No challenge is ever
# inspected, answered, or automated past.
# ===========================================================================

function Invoke-CdpCommand {
    param(
        [string]$WebSocketUrl,
        [string]$Method,
        [hashtable]$Parameters = @{},
        [int]$TimeoutSeconds = 45
    )
    $socket = New-Object System.Net.WebSockets.ClientWebSocket
    $cancel = New-Object System.Threading.CancellationTokenSource
    $cancel.CancelAfter([TimeSpan]::FromSeconds($TimeoutSeconds))
    try {
        $socket.ConnectAsync([uri]$WebSocketUrl, $cancel.Token).Wait()
        $envelope = @{ id = 1; method = $Method; params = $Parameters } | ConvertTo-Json -Depth 8 -Compress
        $payload  = [System.Text.Encoding]::UTF8.GetBytes($envelope)
        $segment  = New-Object 'System.ArraySegment[byte]' -ArgumentList @(, $payload)
        $socket.SendAsync($segment, [System.Net.WebSockets.WebSocketMessageType]::Text, $true, $cancel.Token).Wait()

        $buffer      = New-Object byte[] 262144
        $readSegment = New-Object 'System.ArraySegment[byte]' -ArgumentList @(, $buffer)
        $deadline    = (Get-Date).AddSeconds($TimeoutSeconds)
        while ((Get-Date) -lt $deadline) {
            $builder = New-Object System.Text.StringBuilder
            do {
                $task = $socket.ReceiveAsync($readSegment, $cancel.Token)
                $task.Wait()
                [void]$builder.Append([System.Text.Encoding]::UTF8.GetString($buffer, 0, $task.Result.Count))
            } while (-not $task.Result.EndOfMessage)
            $text = $builder.ToString()
            if ([string]::IsNullOrWhiteSpace($text)) { continue }
            $message = $null
            try { $message = $text | ConvertFrom-Json } catch { continue }
            if ($null -ne $message -and $null -ne $message.id -and [int]$message.id -eq 1) { return $message }
        }
        return $null
    } catch {
        return $null
    } finally {
        try { $socket.Dispose() } catch { }
        try { $cancel.Dispose() } catch { }
    }
}

function Test-DebuggerUp {
    try {
        $v = Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/json/version' -f $Port) -TimeoutSec 10
        return [bool]$v.Browser
    } catch { return $false }
}

function Start-HarvestChrome {
    param([int]$WaitSeconds = 60)
    if (-not (Test-Path -LiteralPath $ChromeExe)) { return $false }
    try {
        Start-Process -FilePath $ChromeExe -ArgumentList @(
            "--remote-debugging-port=$Port",
            "--user-data-dir=$ChromeProfile",
            '--no-first-run',
            '--no-default-browser-check',
            '--restore-last-session',
            $ContentProbeUrl
        ) | Out-Null
    } catch { return $false }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        if (Test-DebuggerUp) { return $true }
    }
    return $false
}

function Test-CongressContent {
    <#
      Drive the harvest browser to an ordinary bill-text page and read what the
      site served. Returns a hashtable:
        State   : 'content' | 'challenged' | 'unreachable'
        Title   : document.title as served
        Marker  : $true when the typed main-surface marker is present
        Clear   : $true when a congress.gov cf_clearance cookie exists
      'content' requires BOTH a title containing "Congress.gov" AND the typed
      marker -- a challenge page has neither.
    #>
    if (-not (Test-DebuggerUp)) {
        return @{ State = 'unreachable'; Title = ''; Marker = $false; Clear = $false }
    }
    $tabId = $null
    $ws    = $null
    try {
        $encoded = [uri]::EscapeDataString($ContentProbeUrl)
        $tab = $null
        try   { $tab = Invoke-RestMethod -Method Put -Uri ('http://127.0.0.1:{0}/json/new?{1}' -f $Port, $encoded) -TimeoutSec 30 }
        catch { try { $tab = Invoke-RestMethod -Method Put -Uri ('http://127.0.0.1:{0}/json/new?url={1}' -f $Port, $encoded) -TimeoutSec 30 } catch { } }
        if (-not $tab) { return @{ State = 'unreachable'; Title = ''; Marker = $false; Clear = $false } }
        $tabId = $tab.id
        $ws    = $tab.webSocketDebuggerUrl

        # Let the page (including Cloudflare's own JavaScript) run and settle.
        Start-Sleep -Seconds $ContentSettleSeconds

        if (-not $ws) {
            try {
                $list = Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/json/list' -f $Port) -TimeoutSec 15
                $me = $list | Where-Object { $_.id -eq $tabId } | Select-Object -First 1
                if ($me) { $ws = $me.webSocketDebuggerUrl }
            } catch { }
        }
        if (-not $ws) { return @{ State = 'challenged'; Title = ''; Marker = $false; Clear = $false } }

        $marker = ($ContentMarker -replace "'", "\'")
        $expression = @"
JSON.stringify({
  title: document.title || '',
  marker: ((document.querySelector('#main') || document.querySelector('main') || document.body || {innerText:''}).innerText || '').indexOf('$marker') >= 0,
  bytes: (document.documentElement ? document.documentElement.outerHTML.length : 0)
})
"@
        $evaluated = Invoke-CdpCommand -WebSocketUrl $ws -Method 'Runtime.evaluate' `
                        -Parameters @{ expression = $expression; returnByValue = $true } -TimeoutSeconds 45
        $title = ''; $marker_found = $false; $bytes = 0
        if ($evaluated -and $evaluated.result -and $evaluated.result.result) {
            try {
                $parsed = $evaluated.result.result.value | ConvertFrom-Json
                $title        = [string]$parsed.title
                $marker_found = [bool]$parsed.marker
                $bytes        = [int]$parsed.bytes
            } catch { }
        }

        $clear = $false
        $cookieResult = Invoke-CdpCommand -WebSocketUrl $ws -Method 'Network.getAllCookies' -Parameters @{} -TimeoutSeconds 30
        if ($cookieResult -and $cookieResult.result -and $cookieResult.result.cookies) {
            $clear = [bool](@($cookieResult.result.cookies | Where-Object {
                $_.domain -match 'congress\.gov' -and $_.name -eq 'cf_clearance'
            }).Count -gt 0)
        }

        $state = 'challenged'
        if ($title -match 'Just a moment|Attention Required|Verifying you are human|Checking your browser') {
            $state = 'challenged'
        } elseif ($title -match 'Congress\.gov' -and $marker_found) {
            $state = 'content'
        }
        return @{ State = $state; Title = $title; Marker = $marker_found; Clear = $clear; Bytes = $bytes }
    } catch {
        return @{ State = 'unreachable'; Title = ''; Marker = $false; Clear = $false }
    } finally {
        if ($tabId) {
            try { Invoke-RestMethod -Uri ('http://127.0.0.1:{0}/json/close/{1}' -f $Port, $tabId) -TimeoutSec 15 | Out-Null } catch { }
        }
    }
}

# ===========================================================================
# Collector process helpers
# ===========================================================================

function Get-CollectorProcess {
    <#
      Match on BOTH the collector script name and this run's output directory.
      Matching on the image name alone would confuse two concurrent harvests
      writing to different output directories -- exactly the situation on this
      box today.
    #>
    $leaf = [regex]::Escape($OutLeaf)
    try {
        Get-CimInstance Win32_Process -Filter "Name='Rterm.exe'" -ErrorAction Stop |
            Where-Object { $_.CommandLine -match 'harvest_congress_text_httr\.R' -and $_.CommandLine -match $leaf } |
            Select-Object -First 1
    } catch { $null }
}

function Stop-CollectorTree {
    param([int]$CollectorPid)
    # Kills the R parent and its future(multisession) workers. Evidence is
    # untouched: every receipt and body is published by atomic rename, so a
    # killed process can only lose the in-flight fetch, which --resume redoes.
    try {
        $kids = @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$CollectorPid" -ErrorAction Stop)
        foreach ($k in $kids) { try { Stop-Process -Id $k.ProcessId -Force -ErrorAction Stop } catch { } }
    } catch { }
    try { Stop-Process -Id $CollectorPid -Force -ErrorAction Stop } catch { }
    Start-Sleep -Seconds 5
}

function Format-Arg {
    param([string]$Value)
    if ($Value -match '\s') { return '"' + $Value + '"' }
    return $Value
}

function Start-Collector {
    param([int]$AttemptNumber)
    $stdout = Join-Path $LogDir ('collector_{0}_a{1:d3}.stdout.log' -f $RunStamp, $AttemptNumber)
    $stderr = Join-Path $LogDir ('collector_{0}_a{1:d3}.stderr.log' -f $RunStamp, $AttemptNumber)
    $argList = New-Object System.Collections.Generic.List[string]
    $argList.Add('--no-echo')
    $argList.Add('--no-restore')
    $argList.Add('--file=' + (Format-Arg $CollectorScript))
    $argList.Add('--args')
    $argList.Add('--records');   $argList.Add((Format-Arg $RecordsRelative))
    $argList.Add('--out');       $argList.Add((Format-Arg $OutRelative))
    if (-not [string]::IsNullOrWhiteSpace($PriorReceiptsRelative)) {
        $argList.Add('--prior_receipts'); $argList.Add((Format-Arg $PriorReceiptsRelative))
    }
    $argList.Add('--limit');     $argList.Add([string]$Limit)
    $argList.Add('--workers');   $argList.Add([string]$Workers)
    $argList.Add('--attempts');  $argList.Add([string]$Attempts)
    $argList.Add('--min_delay'); $argList.Add(([string]$MinDelay))
    $argList.Add('--max_delay'); $argList.Add(([string]$MaxDelay))
    $argList.Add('--host');      $argList.Add('127.0.0.1')
    $argList.Add('--port');      $argList.Add([string]$Port)
    $argList.Add('--glassbox_events'); $argList.Add((Format-Arg ($EventsDirectory -replace '\\', '/')))
    $argList.Add('--resume')
    try {
        Start-Process -FilePath $RRuntime -ArgumentList $argList.ToArray() -WorkingDirectory $Workspace `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden | Out-Null
    } catch {
        return @{ Launched = $false; Reason = $_.Exception.Message; Stdout = $stdout; Stderr = $stderr }
    }
    Start-Sleep -Seconds 20
    $proc = Get-CollectorProcess
    return @{ Launched = [bool]$proc; Reason = $(if ($proc) { 'running' } else { 'exited-within-20s' }); Stdout = $stdout; Stderr = $stderr }
}

function Get-LogTail {
    param([string]$Path, [int]$Lines = 30)
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return '' }
        $tail = Get-Content -LiteralPath $Path -Tail $Lines -ErrorAction Stop
        return (($tail | Where-Object { $_ -match '\S' }) -join ' | ')
    } catch { return '' }
}

function Get-ExitClass {
    <#
      Classify why the collector is gone. Drives the recovery decision:
        receipt-collision -> FATAL, needs a NEW output directory (never a delete)
        config-error      -> FATAL, operator must fix the invocation
        accounting-gap    -> FATAL, the collector's own completeness assertion
                             tripped; re-running would hide it
        no-clearance      -> recoverable: warm the browser, then relaunch
        canary-blocked    -> recoverable: site is blocking, wait then relaunch
        crash             -> recoverable: relaunch with --resume
    #>
    param([string]$StderrText)
    if ($StderrText -match 'Immutable receipt collision')             { return 'receipt-collision' }
    if ($StderrText -match 'Dispatch accounting gap')                 { return 'accounting-gap' }
    if ($StderrText -match 'No discovery records selected|cannot open file|Unknown argument|Invalid argument|must be non-negative|must be positive') { return 'config-error' }
    if ($StderrText -match 'lacks cf_clearance|DevTools endpoint is unavailable') { return 'no-clearance' }
    if ($StderrText -match 'canary failed')                           { return 'canary-blocked' }
    if ([string]::IsNullOrWhiteSpace($StderrText))                    { return 'crash' }
    return 'crash'
}

function Read-ProgressJson {
    try {
        if (-not (Test-Path -LiteralPath $ProgressPath)) { return $null }
        return (Get-Content -LiteralPath $ProgressPath -Raw -ErrorAction Stop | ConvertFrom-Json)
    } catch { return $null }
}

function Read-ManifestJson {
    try {
        if (-not (Test-Path -LiteralPath $ManifestPath)) { return $null }
        return (Get-Content -LiteralPath $ManifestPath -Raw -ErrorAction Stop | ConvertFrom-Json)
    } catch { return $null }
}

function Get-CooldownSeconds {
    param([int]$Consecutive, [int]$BaseSeconds)
    if ($Consecutive -lt 1) { $Consecutive = 1 }
    $shift = [Math]::Min(10, $Consecutive - 1)
    return [int][Math]::Min($MaxCooldownSeconds, $BaseSeconds * [Math]::Pow(2, $shift))
}

# ===========================================================================
# Viewer
# ===========================================================================

function Test-ViewerRunning {
    try {
        $hits = @(Get-CimInstance Win32_Process -ErrorAction Stop |
                  Where-Object { $_.CommandLine -match 'open_glassbox_process_matrix' -and $_.ProcessId -ne $PID })
        return ($hits.Count -gt 0)
    } catch { return $false }
}

function Start-ViewerIfNeeded {
    if ($NoViewer -or -not $LaunchViewer) { return 'disabled' }
    if (-not (Test-Path -LiteralPath $ViewerPath)) { return 'missing' }
    if (Test-ViewerRunning) { return 'already-running' }
    # -Command, NOT -File. The viewer declares [string[]]$ProgressJson and
    # [string[]]$ArtifactPath; those do not bind through -File and the window
    # dies on startup. A single argument STRING is passed so the embedded
    # quoting survives verbatim.
    $inner = "& '{0}' -EventsDirectory '{1}' -ProgressJson '{2}' -ArtifactPath '{3}','{4}' -WindowTitle '{5}'" -f `
             $ViewerPath, $EventsDirectory, $ProgressPath, $ManifestPath, $ReportPath, $ViewerWindowTitle
    $argLine = '-NoProfile -ExecutionPolicy Bypass -STA -Command ' + $inner
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $argLine | Out-Null
        return 'launched'
    } catch { return 'launch-failed' }
}

# ===========================================================================
# Record selection helpers
# ===========================================================================

function Get-SelectedKeys {
    <#
      canonical_key for each selected record, in file order. Regex extraction
      rather than per-line ConvertFrom-Json: 18,557 JSON parses in PowerShell
      costs minutes, the regex costs seconds, and canonical_key is the only
      field needed (receipts live at receipts/<congress>/<type>/<number>).
    #>
    $keys = New-Object System.Collections.Generic.List[string]
    try {
        $n = 0
        foreach ($line in [System.IO.File]::ReadLines($RecordsPath)) {
            if ($Limit -gt 0 -and $n -ge $Limit) { break }
            $n++
            $m = [regex]::Match($line, '"canonical_key"\s*:\s*"([^"]+)"')
            if ($m.Success) { $keys.Add($m.Groups[1].Value) }
        }
    } catch { }
    return $keys
}

function Get-AcceptedKeySet {
    <#
      The collector writes receipts/<congress>/<type>/<number>/receipt.json ONLY
      on the accepted branch, so receipt existence is acceptance. Enumerating
      the tree once beats 18k Test-Path calls.
    #>
    $set = New-Object 'System.Collections.Generic.HashSet[string]'
    $receiptsRoot = Join-Path $OutDir 'receipts'
    if (-not (Test-Path -LiteralPath $receiptsRoot)) { return $set }
    try {
        foreach ($f in [System.IO.Directory]::EnumerateFiles($receiptsRoot, 'receipt.json', [System.IO.SearchOption]::AllDirectories)) {
            $rel   = $f.Substring($receiptsRoot.Length).TrimStart('\', '/')
            $parts = $rel -split '[\\/]'
            if ($parts.Count -ge 4) { [void]$set.Add(('{0}:{1}:{2}' -f $parts[0], $parts[1], $parts[2])) }
        }
    } catch { }
    return $set
}

# ===========================================================================
# CATCHNET
# ===========================================================================

$Script:ThrottleStatuses = @(429, 403, 503, 500, 502, 504)

function Get-FailureEvidence {
    <#
      Sweep both failure surfaces and merge by canonical_key:
        <out>\failure_attempts  -- attempt-level catchnet written by the
                                   collector (flat *.json today, plus an older
                                   surface/<key>/<sha>.json layout)
        <out>\quarantine        -- per-body failure-<pid>.json carrying the
                                   http_status and the five validation checks
      The collector's own failure_class field is recorded but NOT trusted for
      classification: the older layout labels HTTP 403 challenge pages
      "surface_rejected", which is exactly the conflation this report exists to
      undo. Classification below is derived from http_status + validation.
    #>
    $byKey = @{}
    $roots = @(
        @{ Path = (Join-Path $OutDir 'failure_attempts'); Filter = '*.json' },
        @{ Path = (Join-Path $OutDir 'quarantine');       Filter = 'failure-*.json' }
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root.Path)) { continue }
        $files = @()
        try { $files = @(Get-ChildItem -LiteralPath $root.Path -Filter $root.Filter -File -Recurse -ErrorAction Stop) } catch { }
        foreach ($file in $files) {
            $doc = $null
            try { $doc = Get-Content -LiteralPath $file.FullName -Raw -ErrorAction Stop | ConvertFrom-Json } catch { continue }
            if (-not $doc -or -not $doc.canonical_key) { continue }
            $key = [string]$doc.canonical_key
            if (-not $byKey.ContainsKey($key)) {
                $byKey[$key] = [pscustomobject]@{
                    canonical_key        = $key
                    expected_url         = ''
                    http_status          = $null
                    stated_failure_class = ''
                    reason               = ''
                    http_attempts        = 0
                    retry_waits          = 0
                    validation           = $null
                    saw_challenge        = $false
                    saw_throttle_status  = $false
                    saw_clean_200_reject = $false
                    saw_transport_error  = $false
                    evidence_files       = @()
                    evidence_count       = 0
                }
            }
            $row = $byKey[$key]
            if ($doc.expected_url)  { $row.expected_url = [string]$doc.expected_url }
            if ($null -ne $doc.http_status) {
                $status = [int]$doc.http_status
                $row.http_status = $status
                if ($Script:ThrottleStatuses -contains $status) { $row.saw_throttle_status = $true }
            }
            if ($doc.failure_class) { $row.stated_failure_class = [string]$doc.failure_class }
            if ($doc.reason)        { $row.reason = [string]$doc.reason }
            if ($null -ne $doc.http_attempts) { $row.http_attempts = [Math]::Max($row.http_attempts, [int]$doc.http_attempts) }
            if ($null -ne $doc.retry_waits)   { $row.retry_waits   = [Math]::Max($row.retry_waits,   [double]$doc.retry_waits) }
            if ($doc.validation) {
                $row.validation = $doc.validation
                if ($doc.validation.not_challenge -eq $false) { $row.saw_challenge = $true }
                # A genuine surface rejection: the site answered 200 with real
                # (non-challenge) HTML and the page still failed a typed guard.
                if ($doc.validation.status_200 -eq $true -and $doc.validation.not_challenge -eq $true) {
                    $row.saw_clean_200_reject = $true
                }
            }
            if ($row.reason -eq 'transport_error' -or $row.stated_failure_class -eq 'transport_error') {
                $row.saw_transport_error = $true
            }
            $row.evidence_files  = @($row.evidence_files + $file.FullName)
            $row.evidence_count  = $row.evidence_files.Count
        }
    }
    return $byKey
}

function Get-DerivedClass {
    param($Row)
    if ($Row.saw_clean_200_reject) { return 'rejected' }
    if ($Row.saw_throttle_status -or $Row.saw_challenge -or $Row.saw_transport_error) { return 'throttled' }
    if ($Row.reason -match 'throttled_retry_budget_exhausted|unresolved_after_replay') { return 'throttled' }
    return 'unknown'
}

function Build-CatchnetReport {
    param([string]$TerminalState, [int]$ExitCode, [string]$Note = '')
    $selected  = Get-SelectedKeys
    $accepted  = Get-AcceptedKeySet
    $evidence  = Get-FailureEvidence
    $manifest  = Read-ManifestJson

    $rejected  = New-Object System.Collections.Generic.List[object]
    $throttled = New-Object System.Collections.Generic.List[object]
    $unknown   = New-Object System.Collections.Generic.List[object]
    $missing   = New-Object System.Collections.Generic.List[object]
    $acceptedCount = 0

    foreach ($key in $selected) {
        if ($accepted.Contains($key)) { $acceptedCount++; continue }
        if ($evidence.ContainsKey($key)) {
            $row = $evidence[$key]
            $entry = [pscustomobject]@{
                canonical_key        = $row.canonical_key
                expected_url         = $row.expected_url
                http_status          = $row.http_status
                derived_class        = (Get-DerivedClass -Row $row)
                collector_failure_class = $row.stated_failure_class
                reason               = $row.reason
                http_attempts        = $row.http_attempts
                retry_waits          = $row.retry_waits
                validation           = $row.validation
                evidence_count       = $row.evidence_count
                evidence_files       = $row.evidence_files
            }
            switch ($entry.derived_class) {
                'rejected'  { $rejected.Add($entry) }
                'throttled' { $throttled.Add($entry) }
                default     { $unknown.Add($entry) }
            }
        } else {
            $missing.Add([pscustomobject]@{
                canonical_key = $key
                derived_class = 'missing_no_evidence'
                note          = 'Selected but has neither an accepted receipt nor any failure evidence.'
            })
        }
    }

    $report = [pscustomobject]@{
        schema                    = 'congress-fulltext-catchnet-report-v1'
        generated_at              = (Get-Date).ToUniversalTime().ToString('u')
        run_id                    = $RunId
        orchestrator_pid          = $PID
        terminal_state            = $TerminalState
        exit_code                 = $ExitCode
        note                      = $Note
        records_file              = $RecordsPath
        output_directory          = $OutDir
        limit                     = $Limit
        selected_record_count     = $selected.Count
        accepted_receipt_count    = $acceptedCount
        unaccounted_count         = ($selected.Count - $acceptedCount)
        rejected_count            = $rejected.Count
        throttled_unresolved_count= $throttled.Count
        unknown_count             = $unknown.Count
        missing_no_evidence_count = $missing.Count
        catchnet_rounds_run       = $Script:CatchnetRoundsRun
        total_recoveries          = $Script:TotalRecoveries
        blocks_observed           = $Script:BlocksObserved
        clearance_remints         = $Script:ClearanceReminted
        classification_rule       = 'rejected = HTTP 200 with real non-challenge HTML that still failed a typed guard. throttled = 429/403/5xx, a Cloudflare challenge body, or a transport error. These are different problems and are never merged.'
        manifest                  = $(if ($manifest) { [pscustomobject]@{
                                        status                 = $manifest.status
                                        completed_at           = $manifest.completed_at
                                        selected_record_count  = $manifest.selected_record_count
                                        accepted_record_count  = $manifest.accepted_record_count
                                        resumed_record_count   = $manifest.resumed_record_count
                                        failed_record_count    = $manifest.failed_record_count
                                        accounted_record_count = $manifest.accounted_record_count
                                        deferred_record_count  = $manifest.deferred_record_count
                                        replayed_recovered_count = $manifest.replayed_recovered_count
                                        replayed_unresolved_count = $manifest.replayed_unresolved_count
                                        clearance_remint_count = $manifest.clearance_remint_count
                                    } } else { $null })
        rejected                  = $rejected.ToArray()
        throttled                 = $throttled.ToArray()
        unknown                   = $unknown.ToArray()
        missing_no_evidence       = $missing.ToArray()
    }
    return $report
}

function Save-CatchnetReport {
    param($Report)
    Write-JsonAtomic -Path $ReportPath -Value $Report -Depth 12 | Out-Null
    # Also keep a per-run copy so a later run never overwrites this run's report.
    $stamped = Join-Path $OutDir ('catchnet_report_{0}_{1}.json' -f $RunStamp, $PID)
    Write-JsonAtomic -Path $stamped -Value $Report -Depth 12 | Out-Null
    return $stamped
}

# ===========================================================================
# PREFLIGHT
# ===========================================================================

function Invoke-Preflight {
    $deadline = (Get-Date).AddMinutes($PreflightTimeoutMinutes)

    Emit -Tag 'preflight-start' -Status 'running' -Stage 'preflight' `
         -Summary 'Preflight starting' `
         -Detail ("records={0} out={1} limit={2} workers={3} attempts={4} pacing={5}-{6}s" -f `
                  $RecordsRelative, $OutRelative, $Limit, $Workers, $Attempts, $MinDelay, $MaxDelay)

    # --- 1. static prerequisites -----------------------------------------
    foreach ($check in @(
        @{ Path = $RecordsPath;   Label = 'records NDJSON' },
        @{ Path = $CollectorPath; Label = 'collector R script' },
        @{ Path = $RRuntime;      Label = 'R runtime' }
    )) {
        if (-not (Test-Path -LiteralPath $check.Path)) {
            Emit -Tag 'preflight-fail' -Status 'failed' -Stage 'preflight' `
                 -Summary ('PREFLIGHT REFUSED: {0} not found' -f $check.Label) `
                 -Detail ('Missing path: {0}' -f $check.Path)
            return @{ Ok = $false; Reason = ('{0} not found at {1}' -f $check.Label, $check.Path) }
        }
    }
    $recordBytes = 0
    try { $recordBytes = (Get-Item -LiteralPath $RecordsPath).Length } catch { }
    if ($recordBytes -le 0) {
        Emit -Tag 'preflight-fail' -Status 'failed' -Stage 'preflight' `
             -Summary 'PREFLIGHT REFUSED: records NDJSON is empty' -Detail $RecordsPath
        return @{ Ok = $false; Reason = 'records NDJSON exists but is zero bytes' }
    }

    $selected = Get-SelectedKeys
    $Script:TotalRecords = $selected.Count
    if ($Script:TotalRecords -le 0) {
        Emit -Tag 'preflight-fail' -Status 'failed' -Stage 'preflight' `
             -Summary 'PREFLIGHT REFUSED: no selectable records' `
             -Detail ('{0} parsed to zero canonical_key values' -f $RecordsPath)
        return @{ Ok = $false; Reason = 'no canonical_key values parsed from the records NDJSON' }
    }
    Emit -Tag 'preflight-records' -Status 'running' -Stage 'preflight' -Current 0 `
         -Summary ('Records verified: {0} selected ({1:N0} bytes)' -f $Script:TotalRecords, $recordBytes) `
         -Detail $RecordsPath

    # --- 2. concurrency guard --------------------------------------------
    $live = Get-CollectorProcess
    if ($live) {
        if (-not $AdoptRunningCollector) {
            Emit -Tag 'preflight-fail' -Status 'failed' -Stage 'preflight' `
                 -Summary ('PREFLIGHT REFUSED: collector pid {0} is already live on this output directory' -f $live.ProcessId) `
                 -Detail 'A second concurrent writer would trip the immutable-receipt collision guard. Re-run with -AdoptRunningCollector to supervise it instead.'
            return @{ Ok = $false; Reason = 'a collector is already running against this output directory'; Refused = $true }
        }
        Emit -Tag 'preflight-adopt' -Status 'running' -Stage 'preflight' `
             -Summary ('Adopting live collector pid {0}' -f $live.ProcessId) `
             -Detail 'A collector is already writing to this output directory; supervising it rather than starting a second writer.'
    }

    # --- 3. browser ------------------------------------------------------
    if (-not (Test-DebuggerUp)) {
        Emit -Tag 'preflight-chrome' -Status 'running' -Stage 'preflight' `
             -Summary 'Chrome DevTools endpoint down; starting the harvest browser' `
             -Detail ('{0} --remote-debugging-port={1} --user-data-dir={2}' -f $ChromeExe, $Port, $ChromeProfile)
        if (-not (Start-HarvestChrome -WaitSeconds 90)) {
            Emit -Tag 'preflight-fail' -Status 'failed' -Stage 'preflight' `
                 -Summary 'PREFLIGHT REFUSED: Chrome never answered on the DevTools port' `
                 -Detail ('http://127.0.0.1:{0}/json/version did not respond within 90s after launch.' -f $Port)
            return @{ Ok = $false; Reason = ('Chrome DevTools endpoint 127.0.0.1:{0} never came up' -f $Port) }
        }
    }
    Emit -Tag 'preflight-chrome-ok' -Status 'running' -Stage 'preflight' `
         -Summary ('Chrome DevTools reachable on 127.0.0.1:{0}' -f $Port) -Detail ''

    # --- 4. content verification, with escalating waits ------------------
    $waits   = @(30, 60, 120, 240, 480, 900)
    $attempt = 0
    while ((Get-Date) -lt $deadline) {
        $attempt++
        $probe = Test-CongressContent
        if ($probe.State -eq 'content' -and $probe.Clear) {
            Emit -Tag 'preflight-content' -Status 'running' -Stage 'preflight' -Current 0 `
                 -Summary 'congress.gov is serving CONTENT (title + typed marker + cf_clearance)' `
                 -Detail ('probe #{0}: title="{1}" marker={2} bytes={3}' -f $attempt, $probe.Title, $probe.Marker, $probe.Bytes) `
                 -Extra @{ probe_title = $probe.Title; cf_clearance_present = $probe.Clear }
            return @{ Ok = $true; Reason = 'content served'; Adopted = [bool]$live }
        }
        if ($probe.State -eq 'content' -and -not $probe.Clear) {
            # Content is served but no cf_clearance cookie: the collector
            # hard-stops at startup without one. Another navigation usually
            # mints it. Do not launch until it exists.
            $Script:BrowserChallenged++
            $sleep = $waits[[Math]::Min($attempt - 1, $waits.Count - 1)]
            Emit -Tag 'preflight-noclear' -Status 'blocked' -Stage 'preflight' `
                 -Summary 'Content served but no cf_clearance cookie yet; waiting' `
                 -Detail ('probe #{0}: the collector aborts at startup without cf_clearance. Sleeping {1}s then re-navigating.' -f $attempt, $sleep)
            Start-Sleep -Seconds $sleep
            continue
        }
        $Script:BrowserChallenged++
        $sleep = $waits[[Math]::Min($attempt - 1, $waits.Count - 1)]
        Emit -Tag 'preflight-blocked' -Status 'blocked' -Stage 'preflight' `
             -Summary ('congress.gov is NOT serving content (state={0}); waiting {1}s' -f $probe.State, $sleep) `
             -Detail ('probe #{0}: title="{1}". No challenge is solved, bypassed, or automated -- the only action is to wait. Preflight deadline {2}.' -f `
                      $attempt, $probe.Title, $deadline.ToString('u'))
        Start-Sleep -Seconds $sleep
    }

    Emit -Tag 'preflight-fail' -Status 'failed' -Stage 'preflight' `
         -Summary ('PREFLIGHT REFUSED: congress.gov stayed blocked for {0} minutes' -f $PreflightTimeoutMinutes) `
         -Detail ('{0} content probes over {1} minutes never yielded a "Congress.gov" title carrying the "{2}" marker. Refusing to start: launching into a block only deepens it.' -f `
                  $attempt, $PreflightTimeoutMinutes, $ContentMarker)
    return @{ Ok = $false; Reason = ('congress.gov served a challenge, not content, for the whole {0}-minute preflight window' -f $PreflightTimeoutMinutes) }
}

# ===========================================================================
# SUPERVISE
# ===========================================================================

function Invoke-SuperviseCollector {
    <#
      Runs one collector to a terminal state, healing along the way.
      -ManifestNewerThan makes a pre-existing manifest.json (from an earlier
      run into this same directory) not count as this attempt's completion.
      Returns: complete | recovery-cap | deadline | receipt-collision |
               config-error | accounting-gap
    #>
    param([datetime]$ManifestNewerThan, [datetime]$Deadline, [string]$Phase = 'collect')

    $lastCurrent  = -1
    $lastAdvance  = Get-Date
    $lastAttempt  = 0

    while ((Get-Date) -lt $Deadline) {
        $Script:Cycle++

        # --- terminal: the collector wrote its manifest -------------------
        if (Test-Path -LiteralPath $ManifestPath) {
            $written = [datetime]::MinValue
            try { $written = (Get-Item -LiteralPath $ManifestPath).LastWriteTime } catch { }
            if ($written -gt $ManifestNewerThan) {
                $mf = Read-ManifestJson
                Emit -Tag 'collector-terminal' -Status 'completed' -Stage $Phase `
                     -Summary ('Collector reached a terminal state: manifest written ({0})' -f $(if ($mf) { $mf.status } else { 'unreadable' })) `
                     -Detail ('accounted={0}/{1} accepted={2} resumed={3} failed={4} deferred={5} replay_recovered={6} replay_unresolved={7}' -f `
                              $(if ($mf) { $mf.accounted_record_count } else { '?' }),
                              $(if ($mf) { $mf.selected_record_count } else { '?' }),
                              $(if ($mf) { $mf.accepted_record_count } else { '?' }),
                              $(if ($mf) { $mf.resumed_record_count } else { '?' }),
                              $(if ($mf) { $mf.failed_record_count } else { '?' }),
                              $(if ($mf) { $mf.deferred_record_count } else { '?' }),
                              $(if ($mf) { $mf.replayed_recovered_count } else { '?' }),
                              $(if ($mf) { $mf.replayed_unresolved_count } else { '?' }))
                return 'complete'
            }
        }

        $progress = Read-ProgressJson
        $current  = 0; $pstatus = 'unknown'; $pdetail = ''; $rate = 0.0; $failures = 0
        if ($progress) {
            if ($null -ne $progress.current)       { $current  = [int]$progress.current }
            if ($null -ne $progress.status)        { $pstatus  = [string]$progress.status }
            if ($null -ne $progress.detail)        { $pdetail  = [string]$progress.detail }
            if ($null -ne $progress.rate)          { $rate     = [double]$progress.rate }
            if ($null -ne $progress.failure_count) { $failures = [int]$progress.failure_count }
        }
        $Script:LastCurrent = $current
        if ($current -gt $lastCurrent) {
            $lastCurrent = $current
            $lastAdvance = Get-Date
            # Real forward progress clears the consecutive-recovery counter:
            # the cap is on consecutive failures, not on lifetime restarts.
            $Script:ConsecutiveFailures = 0
        }
        $stalledSeconds = [int]((Get-Date) - $lastAdvance).TotalSeconds

        # The collector's own circuit breaker announces a full-batch block.
        if ($pdetail -match 'Full-batch block detected') {
            $Script:BlocksObserved++
            Emit -Tag 'block' -Status 'blocked' -Stage 'cloudflare-block' -Current $current `
                 -Summary ('Collector circuit breaker fired at {0}/{1}' -f $current, $Script:TotalRecords) `
                 -Detail ('Full-batch block; the collector is cooling down and re-minting clearance from the live browser. detail={0}' -f $pdetail)
        }

        $proc = Get-CollectorProcess

        # ------------------------------------------------------------------
        # CONDITION: process is gone with no fresh manifest -> crashed/early
        # ------------------------------------------------------------------
        if (-not $proc) {
            $stderrLog = Join-Path $LogDir ('collector_{0}_a{1:d3}.stderr.log' -f $RunStamp, $lastAttempt)
            $tail      = Get-LogTail -Path $stderrLog -Lines 40
            $class     = if ($lastAttempt -gt 0) { Get-ExitClass -StderrText $tail } else { 'not-started' }

            if ($class -eq 'receipt-collision') {
                Emit -Tag 'fatal-collision' -Status 'failed' -Stage $Phase -Current $current `
                     -Summary 'FATAL: immutable receipt collision' `
                     -Detail ('The collector refused to overwrite an existing receipt. Evidence is immutable by design -- this run needs a NEW output directory, never a deletion. Nothing was removed. stderr: {0}' -f $tail)
                return 'receipt-collision'
            }
            if ($class -eq 'config-error') {
                Emit -Tag 'fatal-config' -Status 'failed' -Stage $Phase -Current $current `
                     -Summary 'FATAL: collector rejected its invocation' `
                     -Detail ('Relaunching cannot fix a bad argument or an unreadable records file. stderr: {0}' -f $tail)
                return 'config-error'
            }
            if ($class -eq 'accounting-gap') {
                Emit -Tag 'fatal-accounting' -Status 'failed' -Stage $Phase -Current $current `
                     -Summary 'FATAL: the collector''s dispatch accounting assertion tripped' `
                     -Detail ('Selected keys were left unaccounted for. Re-running would paper over it. stderr: {0}' -f $tail)
                return 'accounting-gap'
            }

            if ($Script:ConsecutiveFailures -ge $MaxConsecutiveRecoveries) {
                Emit -Tag 'recovery-cap' -Status 'failed' -Stage $Phase -Current $current `
                     -Summary ('STOPPING: {0} consecutive recovery attempts made no progress' -f $Script:ConsecutiveFailures) `
                     -Detail ('Cap is -MaxConsecutiveRecoveries {0}. Looping further would only hammer a site that is not answering. Last stderr: {1}' -f $MaxConsecutiveRecoveries, $tail)
                return 'recovery-cap'
            }

            # Before relaunching, confirm the site is actually serving content.
            # Relaunching into a block wastes the attempt AND deepens the block:
            # the collector hard-stops at startup without cf_clearance.
            $probe = Test-CongressContent
            if ($probe.State -eq 'content' -and $probe.Clear) { $Script:ClearanceReminted++ }
            if ($probe.State -ne 'content' -or -not $probe.Clear) {
                $Script:BrowserChallenged++
                $Script:ConsecutiveFailures++
                $cool = Get-CooldownSeconds -Consecutive $Script:ConsecutiveFailures -BaseSeconds $RecoveryCooldownSeconds
                Emit -Tag 'wait-blocked' -Status 'blocked' -Stage 'await-clearance' -Current $current `
                     -Summary ('Collector down and congress.gov not serving content (state={0}); waiting {1}s' -f $probe.State, $cool) `
                     -Detail ('exit_class={0} title="{1}" cf_clearance={2}. Not relaunching into a block. No challenge is solved, bypassed, or automated. stderr: {3}' -f `
                              $class, $probe.Title, $probe.Clear, $tail)
                Start-Sleep -Seconds $cool
                continue
            }

            $Script:ConsecutiveFailures++
            $Script:TotalRecoveries++
            $lastAttempt++
            $launch = Start-Collector -AttemptNumber $lastAttempt
            Emit -Tag 'relaunch' -Status $(if ($launch.Launched) { 'running' } else { 'needs-review' }) `
                 -Stage 'relaunch' -Current $current `
                 -Summary ('Relaunched the collector with --resume (attempt {0}, recovery {1}/{2} consecutive)' -f `
                           $lastAttempt, $Script:ConsecutiveFailures, $MaxConsecutiveRecoveries) `
                 -Detail ('exit_class={0} launch={1}. --resume is free: pages with an accepted receipt are skipped with no request. Fresh log {2}. Prior stderr: {3}' -f `
                          $class, $launch.Reason, $launch.Stdout, $tail) `
                 -Extra @{ exit_class = $class; collector_stdout = $launch.Stdout; collector_stderr = $launch.Stderr }
            if (-not $launch.Launched) {
                $cool = Get-CooldownSeconds -Consecutive $Script:ConsecutiveFailures -BaseSeconds $RecoveryCooldownSeconds
                Start-Sleep -Seconds $cool
            }
            $lastAdvance = Get-Date
            Start-Sleep -Seconds $PollSeconds
            continue
        }

        # ------------------------------------------------------------------
        # CONDITION: alive but frozen
        # ------------------------------------------------------------------
        if ($stalledSeconds -ge ($HardStallMinutes * 60)) {
            # Hard stall. Verify the site first: if it is blocked, the process
            # is doing the right thing by backing off and must be left alone.
            $probe = Test-CongressContent
            if ($probe.State -eq 'content' -and $probe.Clear) {
                $Script:ClearanceReminted++
                $Script:ConsecutiveFailures++
                $Script:TotalRecoveries++
                Emit -Tag 'hard-stall' -Status 'needs-review' -Stage 'hard-stall-recovery' -Current $current `
                     -Summary ('HARD STALL: no progress for {0}m while the site serves content; restarting the collector' -f [int]($stalledSeconds / 60)) `
                     -Detail ('pid={0} stuck at {1}/{2}. Clearance re-minted, stopping the process tree and relaunching with --resume. No evidence is touched: receipts are published by atomic rename.' -f `
                              $proc.ProcessId, $current, $Script:TotalRecords)
                Stop-CollectorTree -CollectorPid ([int]$proc.ProcessId)
                $lastAttempt++
                $launch = Start-Collector -AttemptNumber $lastAttempt
                Emit -Tag 'relaunch' -Status $(if ($launch.Launched) { 'running' } else { 'needs-review' }) `
                     -Stage 'relaunch' -Current $current `
                     -Summary ('Relaunched after hard stall (attempt {0})' -f $lastAttempt) `
                     -Detail ('launch={0} log={1}' -f $launch.Reason, $launch.Stdout)
                $lastAdvance = Get-Date
            } else {
                $Script:BrowserChallenged++
                $Script:BlocksObserved++
                Emit -Tag 'stall-blocked' -Status 'blocked' -Stage 'cloudflare-block' -Current $current `
                     -Summary ('Stalled {0}m because congress.gov is not serving content (state={1})' -f [int]($stalledSeconds / 60), $probe.State) `
                     -Detail 'Leaving the collector alive: its own backoff is the correct response to a block. Waiting, not engineering around the check.'
            }
            Start-Sleep -Seconds $PollSeconds
            continue
        }

        if ($stalledSeconds -ge ($StallMinutes * 60)) {
            # Soft stall -> treat as blocked. Re-mint clearance by driving the
            # browser to an ordinary bill-text page so the collector's own
            # circuit breaker finds a usable cookie when it next re-reads the
            # jar. Non-destructive: the collector keeps running.
            $probe = Test-CongressContent
            if ($probe.State -eq 'content' -and $probe.Clear) { $Script:ClearanceReminted++ } else { $Script:BrowserChallenged++; $Script:BlocksObserved++ }
            Emit -Tag 'soft-stall' -Status $(if ($probe.State -eq 'content') { 'running' } else { 'blocked' }) `
                 -Stage 'clearance-remint' -Current $current `
                 -Summary ('Stalled {0}m at {1}/{2}; browser probe={3}' -f [int]($stalledSeconds / 60), $current, $Script:TotalRecords, $probe.State) `
                 -Detail ('Opened the bill-text page in the harvest browser and let the site''s own JavaScript settle, refreshing cf_clearance for the collector''s next re-mint. title="{0}" cf_clearance={1}. Hard-stall restart at {2}m.' -f `
                          $probe.Title, $probe.Clear, $HardStallMinutes)
            Start-Sleep -Seconds $PollSeconds
            continue
        }

        # ------------------------------------------------------------------
        # Healthy: heartbeat
        # ------------------------------------------------------------------
        Emit -Tag 'heartbeat' -Status 'running' -Stage $Phase -Current $current `
             -Summary ('{0}/{1} official pages ({2:N1}/min)' -f $current, $Script:TotalRecords, $rate) `
             -Detail ('pid={0} collector_status={1} failures={2} idle={3}s :: {4}' -f `
                      $proc.ProcessId, $pstatus, $failures, $stalledSeconds, $pdetail) `
             -Extra @{ collector_pid = $proc.ProcessId; records_per_minute = $rate; failure_count = $failures; idle_seconds = $stalledSeconds }
        Save-OrchestratorState -Phase $Phase
        Start-Sleep -Seconds $PollSeconds
    }

    Emit -Tag 'deadline' -Status 'failed' -Stage $Phase -Current $Script:LastCurrent `
         -Summary ('Wall-clock deadline reached ({0}h)' -f $MaxHours) `
         -Detail 'Stopping supervision. The collector process, if still alive, is left running and untouched; re-running this orchestrator resumes.'
    return 'deadline'
}

# ===========================================================================
# MAIN
# ===========================================================================

$overallDeadline = (Get-Date).AddHours($MaxHours)
$startedAt       = Get-Date

Emit -Tag 'start' -Status 'running' -Stage 'startup' -Current 0 `
     -Summary 'Congress full-text pipeline orchestrator starting' `
     -Detail ('run_id={0} pid={1} out={2} poll={3}s stall={4}m hard_stall={5}m recovery_cap={6} catchnet_rounds={7} max_hours={8}' -f `
              $RunId, $PID, $OutDir, $PollSeconds, $StallMinutes, $HardStallMinutes, $MaxConsecutiveRecoveries, $CatchnetRounds, $MaxHours)

$viewerState = Start-ViewerIfNeeded
Emit -Tag 'viewer' -Status 'running' -Stage 'startup' -Current 0 `
     -Summary ('Glass Box viewer: {0}' -f $viewerState) `
     -Detail ('Launched via powershell -Command (never -File: the viewer''s string[] parameters do not bind under -File). Script: {0}' -f $ViewerPath)

$preflight = Invoke-Preflight
if (-not $preflight.Ok) {
    $code = if ($preflight.Refused) { 6 } else { 2 }
    $report = Build-CatchnetReport -TerminalState 'preflight-refused' -ExitCode $code -Note $preflight.Reason
    $stamped = Save-CatchnetReport -Report $report
    Emit -Tag 'terminal' -Status 'failed' -Stage 'terminal' `
         -Summary ('TERMINAL: preflight refused -- {0}' -f $preflight.Reason) `
         -Detail ('Nothing was launched. Report: {0}. Exit code {1}.' -f $stamped, $code)
    Save-OrchestratorState -Phase 'terminal' -TerminalState 'preflight-refused'
    exit $code
}

if ($PreflightOnly) {
    Emit -Tag 'terminal' -Status 'completed' -Stage 'terminal' `
         -Summary 'PREFLIGHT ONLY: environment is ready; no collector launched' `
         -Detail ('{0} records selected; congress.gov is serving content with cf_clearance present.' -f $Script:TotalRecords)
    Save-OrchestratorState -Phase 'terminal' -TerminalState 'preflight-only'
    exit 0
}

# --- launch (unless we adopted a live collector) --------------------------
$manifestBaseline = $startedAt
if (Test-Path -LiteralPath $ManifestPath) {
    try { $manifestBaseline = (Get-Item -LiteralPath $ManifestPath).LastWriteTime } catch { }
    if ($manifestBaseline -gt $startedAt) { $manifestBaseline = $startedAt }
}

if (-not (Get-CollectorProcess)) {
    $launch = Start-Collector -AttemptNumber 0
    Emit -Tag 'launch' -Status $(if ($launch.Launched) { 'running' } else { 'needs-review' }) -Stage 'launch' -Current 0 `
         -Summary ('Collector launched with --resume: {0}' -f $launch.Reason) `
         -Detail ('stdout={0} stderr={1}' -f $launch.Stdout, $launch.Stderr) `
         -Extra @{ collector_stdout = $launch.Stdout; collector_stderr = $launch.Stderr }
}

$state = Invoke-SuperviseCollector -ManifestNewerThan $manifestBaseline -Deadline $overallDeadline -Phase 'collect'

# --- fatal supervision outcomes -------------------------------------------
$fatalCodes = @{ 'receipt-collision' = 7; 'config-error' = 9; 'accounting-gap' = 5; 'recovery-cap' = 3; 'deadline' = 8 }
if ($state -ne 'complete') {
    $code   = $fatalCodes[$state]
    $report = Build-CatchnetReport -TerminalState $state -ExitCode $code -Note 'Collector did not reach a clean terminal state; catchnet reflects the evidence on disk at that moment.'
    $stamped = Save-CatchnetReport -Report $report
    Emit -Tag 'terminal' -Status 'failed' -Stage 'terminal' -Current $Script:LastCurrent `
         -Summary ('TERMINAL: {0}' -f $state) `
         -Detail ('accepted={0}/{1} rejected={2} throttled_unresolved={3} missing={4}. Report: {5}. Exit code {6}. No evidence was deleted.' -f `
                  $report.accepted_receipt_count, $report.selected_record_count, $report.rejected_count,
                  $report.throttled_unresolved_count, $report.missing_no_evidence_count, $stamped, $code)
    Save-OrchestratorState -Phase 'terminal' -TerminalState $state
    exit $code
}

# ===========================================================================
# CATCHNET: sweep, classify, and re-run the throttled set
# ===========================================================================

$report = Build-CatchnetReport -TerminalState 'collector-complete' -ExitCode -1
Emit -Tag 'catchnet-sweep' -Status 'running' -Stage 'catchnet' -Current $Script:LastCurrent `
     -Summary ('Catchnet sweep: {0}/{1} accepted, {2} rejected, {3} throttled, {4} unknown, {5} missing' -f `
               $report.accepted_receipt_count, $report.selected_record_count, $report.rejected_count,
               $report.throttled_unresolved_count, $report.unknown_count, $report.missing_no_evidence_count) `
     -Detail 'rejected = HTTP 200 with real HTML that failed a typed guard (a page problem). throttled = 429/403/5xx, challenge body, or transport error (a pacing problem). They are reported separately and never merged.'

while ($Script:CatchnetRoundsRun -lt $CatchnetRounds -and (Get-Date) -lt $overallDeadline) {
    $retryable = $report.throttled_unresolved_count + $report.unknown_count + $report.missing_no_evidence_count
    if ($retryable -le 0) { break }

    $Script:CatchnetRoundsRun++
    $cool = Get-CooldownSeconds -Consecutive $Script:CatchnetRoundsRun -BaseSeconds $CatchnetCooldownSeconds
    Emit -Tag 'catchnet-cooldown' -Status 'running' -Stage 'catchnet' -Current $Script:LastCurrent `
         -Summary ('Catchnet round {0}/{1}: {2} record(s) to retry; cooling down {3}s first' -f `
                   $Script:CatchnetRoundsRun, $CatchnetRounds, $retryable, $cool) `
         -Detail 'Throttled and unexplained records only. Genuinely rejected pages are NOT retried -- re-requesting a page the site already answered correctly would only add load.'
    Start-Sleep -Seconds $cool

    # Fresh clearance before the round; wait rather than push if still blocked.
    $probe = Test-CongressContent
    $waited = 0
    while ($probe.State -ne 'content' -and $waited -lt 3600 -and (Get-Date) -lt $overallDeadline) {
        $Script:BrowserChallenged++
        Emit -Tag 'catchnet-blocked' -Status 'blocked' -Stage 'catchnet' -Current $Script:LastCurrent `
             -Summary ('Catchnet round {0} held: congress.gov not serving content (state={1})' -f $Script:CatchnetRoundsRun, $probe.State) `
             -Detail 'Waiting for the block to lift. Nothing is retried while the site is challenging.'
        Start-Sleep -Seconds 300
        $waited += 300
        $probe = Test-CongressContent
    }
    if ($probe.State -eq 'content' -and $probe.Clear) { $Script:ClearanceReminted++ }
    if ($probe.State -ne 'content') {
        Emit -Tag 'catchnet-abort' -Status 'blocked' -Stage 'catchnet' -Current $Script:LastCurrent `
             -Summary ('Catchnet round {0} abandoned: still blocked after an hour of waiting' -f $Script:CatchnetRoundsRun) `
             -Detail 'Reporting the throttled set as unresolved rather than hammering the site.'
        break
    }

    $baseline = Get-Date
    $launch = Start-Collector -AttemptNumber (1000 + $Script:CatchnetRoundsRun)
    Emit -Tag 'catchnet-relaunch' -Status 'running' -Stage 'catchnet' -Current $Script:LastCurrent `
         -Summary ('Catchnet round {0}: collector relaunched with --resume ({1})' -f $Script:CatchnetRoundsRun, $launch.Reason) `
         -Detail ('Only the {0} unresolved record(s) will make a request; every accepted page is skipped. log={1}' -f $retryable, $launch.Stdout)

    $state = Invoke-SuperviseCollector -ManifestNewerThan $baseline -Deadline $overallDeadline -Phase ('catchnet-round-{0}' -f $Script:CatchnetRoundsRun)
    $report = Build-CatchnetReport -TerminalState ('catchnet-round-{0}-{1}' -f $Script:CatchnetRoundsRun, $state) -ExitCode -1

    Emit -Tag 'catchnet-verify' -Status $(if ($report.throttled_unresolved_count -eq 0) { 'completed' } else { 'needs-review' }) `
         -Stage 'catchnet' -Current $Script:LastCurrent `
         -Summary ('Catchnet round {0} verified: {1}/{2} accepted, {3} throttled still unresolved' -f `
                   $Script:CatchnetRoundsRun, $report.accepted_receipt_count, $report.selected_record_count, $report.throttled_unresolved_count) `
         -Detail ('supervision state={0} rejected={1} unknown={2} missing={3}' -f `
                  $state, $report.rejected_count, $report.unknown_count, $report.missing_no_evidence_count)

    if ($state -ne 'complete') {
        Emit -Tag 'catchnet-stop' -Status 'needs-review' -Stage 'catchnet' -Current $Script:LastCurrent `
             -Summary ('Catchnet stopped after round {0}: supervision returned {1}' -f $Script:CatchnetRoundsRun, $state) `
             -Detail 'Not starting another round on top of an unhealthy state.'
        break
    }
}

# ===========================================================================
# FINAL ACCOUNTING
# ===========================================================================

$manifest    = Read-ManifestJson
$accountedOk = $false
$manifestNote = 'manifest.json missing or unreadable'
if ($manifest) {
    $sel  = [int]$manifest.selected_record_count
    $acct = [int]$manifest.accounted_record_count
    $accountedOk = ($sel -gt 0 -and $sel -eq $acct)
    $manifestNote = ('manifest status={0} selected={1} accounted={2} accepted={3} resumed={4} failed={5}' -f `
                     $manifest.status, $sel, $acct, $manifest.accepted_record_count,
                     $manifest.resumed_record_count, $manifest.failed_record_count)
}

$unresolvedThrottled = $report.throttled_unresolved_count + $report.unknown_count + $report.missing_no_evidence_count

$terminalState = 'complete'
$exitCode      = 0
if (-not $accountedOk) {
    $terminalState = 'accounting-gap'
    $exitCode      = 5
} elseif ($unresolvedThrottled -gt 0) {
    $terminalState = 'throttled-unresolved'
    $exitCode      = 4
} elseif ($report.rejected_count -gt 0) {
    $terminalState = 'complete-with-rejections'
    $exitCode      = 0
}

$report = Build-CatchnetReport -TerminalState $terminalState -ExitCode $exitCode -Note $manifestNote
$stamped = Save-CatchnetReport -Report $report

Emit -Tag 'terminal' -Status $(if ($exitCode -eq 0) { 'completed' } else { 'failed' }) -Stage 'terminal' -Current $Script:LastCurrent `
     -Summary ('TERMINAL: {0} -- {1}/{2} accepted, {3} rejected, {4} throttled unresolved (exit {5})' -f `
               $terminalState, $report.accepted_receipt_count, $report.selected_record_count,
               $report.rejected_count, $unresolvedThrottled, $exitCode) `
     -Detail ('{0}. recoveries={1} blocks={2} clearance_remints={3} catchnet_rounds={4}. Report: {5}. Elapsed {6:N1}h. No evidence was deleted or overwritten.' -f `
              $manifestNote, $Script:TotalRecoveries, $Script:BlocksObserved, $Script:ClearanceReminted,
              $Script:CatchnetRoundsRun, $stamped, ((Get-Date) - $startedAt).TotalHours) `
     -Extra @{ catchnet_report = $ReportPath; catchnet_report_stamped = $stamped; exit_code = $exitCode }

Save-OrchestratorState -Phase 'terminal' -TerminalState $terminalState
exit $exitCode
