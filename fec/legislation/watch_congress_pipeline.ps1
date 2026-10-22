<#
    Read-only live view of the congress pipeline, the daily gate and the media
    collection. Refreshes every five seconds and writes nothing anywhere.

    Every file this reads is rewritten atomically by a running producer, so a
    read can land on a missing or half-written document. Each one is wrapped:
    an unreadable file prints "(not yet)" and the watcher keeps going.

    Windows PowerShell 5.1 compatible: no ternary, no ??, no &&.
#>
param(
    [string]$Root = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems',
    [int]$IntervalSeconds = 5
)

$ErrorActionPreference = 'Continue'

try {
    $Host.UI.RawUI.WindowTitle = 'CONGRESS PIPELINE - LIVE'
} catch {
    # Some hosts have no settable window title; the view works either way.
}

function Read-TextShared {
    <#
        Read a file the producer may be replacing underneath us.

        Get-Content takes a share mode that excludes delete/rename, so a poll
        landing in the wrong millisecond made the writer's os.replace fail with
        WinError 5 and killed a whole scheduled run (2026-09-13 22:42Z). Opening
        with FileShare ReadWrite,Delete lets the rename proceed while we read.
        Only the tail is read for large files, so a multi-megabyte step log does
        not cost a full re-read every cycle.
    #>
    param([string]$Path, [int]$MaxBytes = 262144)
    $stream = $null
    $reader = $null
    try {
        if (-not (Test-Path -LiteralPath $Path)) { return $null }
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open,
                                         [System.IO.FileAccess]::Read,
                                         [System.IO.FileShare]'ReadWrite, Delete')
        if ($stream.Length -gt $MaxBytes) {
            [void]$stream.Seek(-$MaxBytes, [System.IO.SeekOrigin]::End)
        }
        $reader = New-Object System.IO.StreamReader($stream)
        return $reader.ReadToEnd()
    } catch {
        return $null
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        elseif ($null -ne $stream) { $stream.Dispose() }
    }
}

function Read-JsonSafe {
    param([string]$Path)
    try {
        $text = Read-TextShared -Path $Path
        if ([string]::IsNullOrWhiteSpace($text)) { return $null }
        return ($text | ConvertFrom-Json -ErrorAction Stop)
    } catch {
        return $null
    }
}

function Read-TailSafe {
    param([string]$Path, [int]$Count)
    try {
        $text = Read-TextShared -Path $Path
        if ([string]::IsNullOrEmpty($text)) { return @() }
        # 0..-1 counts backwards in PowerShell and would resurrect the last
        # element, so a one-element array is emptied explicitly.
        $lines = @($text -split "`r?`n")
        while ($lines.Count -gt 0 -and $lines[$lines.Count - 1] -eq '') {
            if ($lines.Count -eq 1) { $lines = @() }
            else { $lines = @($lines[0..($lines.Count - 2)]) }
        }
        if ($lines.Count -eq 0) { return @() }
        if ($lines.Count -le $Count) { return $lines }
        return @($lines[($lines.Count - $Count)..($lines.Count - 1)])
    } catch {
        return @()
    }
}

function Get-Field {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($null -eq $Object.PSObject) { return $null }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) { return $null }
    return $property.Value
}

function Show-Value {
    param($Value)
    if ($null -eq $Value) { return '(not yet)' }
    if ($Value -is [string] -and $Value.Length -eq 0) { return '(empty)' }
    return [string]$Value
}

function Get-Elapsed {
    param($Stamp)
    if ($null -eq $Stamp) { return '' }
    try {
        $started = [datetime]::Parse([string]$Stamp, [Globalization.CultureInfo]::InvariantCulture,
                                     [Globalization.DateTimeStyles]::RoundtripKind)
        $span = (Get-Date).ToUniversalTime() - $started.ToUniversalTime()
        return ('{0:00}:{1:00}:{2:00}' -f [int]$span.TotalHours, $span.Minutes, $span.Seconds)
    } catch {
        return ''
    }
}

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Host ('-- ' + $Title + ' ' + ('-' * [Math]::Max(1, 68 - $Title.Length))) -ForegroundColor DarkGray
}

function Get-LiveStatus {
    <#
        A producer that is killed cannot mark its own receipt, so it reads RUNNING
        for a dead pid forever. Believe RUNNING only while the recorded owner still
        runs and began no later than started_at (a recycled pid began after it).
        An unreadable start time stays RUNNING: cannot tell, so the owner is live.
    #>
    param($Document)
    $status = Show-Value (Get-Field $Document 'status')
    if ($status -ne 'RUNNING') { return $status }
    $ownerPid = 0
    if (-not [int]::TryParse([string](Get-Field $Document 'owner_pid'), [ref]$ownerPid) -or $ownerPid -le 0) {
        return 'INTERRUPTED'
    }
    $process = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    if ($null -eq $process) { return 'INTERRUPTED' }
    try {
        $stamp = [datetime]::Parse([string](Get-Field $Document 'started_at'), [Globalization.CultureInfo]::InvariantCulture,
                                   [Globalization.DateTimeStyles]::RoundtripKind)
        # 2 s slack: the owner starts before it stamps started_at, so only a clearly later start is a recycled pid.
        if ($process.StartTime.ToUniversalTime() -gt $stamp.ToUniversalTime().AddSeconds(2)) { return 'INTERRUPTED' }
    } catch {
        # Start time or stamp unreadable: cannot tell, so the owner is treated as live.
    }
    return $status
}

function Get-StatusColor {
    param([string]$Status)
    if ($Status -eq 'PASS') { return 'Green' }
    if ($Status -eq 'RUNNING') { return 'Cyan' }
    if ($Status -eq 'PARTIAL') { return 'Yellow' }
    if ($Status -eq 'SKIPPED_SMOKE') { return 'DarkGray' }
    if ($Status -eq 'SKIPPED_PARTIAL') { return 'Yellow' }
    if ($Status -eq 'DEFERRED') { return 'Yellow' }
    if ($Status -eq 'RETRYING') { return 'Yellow' }
    if ($Status -eq 'BUSY') { return 'Yellow' }
    if ($Status -eq 'NOT_RUN') { return 'DarkGray' }
    if ($Status -eq '(not yet)') { return 'DarkGray' }
    return 'Red'
}

$statusPath = Join-Path $Root 'data\engine\congress_pipeline_status.json'
$dailyPath = Join-Path $Root 'data\engine\daily_refresh_state.json'
$mediaPath = Join-Path $Root 'data\engine\daily_media_refresh_receipt.json'
$livePath = Join-Path $Root 'data\engine\congress_pipeline_live.log'

while ($true) {
    $lines = New-Object System.Collections.Generic.List[object]

    # ---- congress pipeline ------------------------------------------------
    $status = Read-JsonSafe -Path $statusPath
    $runStatus = Get-LiveStatus $status
    $mode = Show-Value (Get-Field $status 'mode')
    $activeStep = Show-Value (Get-Field $status 'active_step')
    $startedAt = Get-Field $status 'started_at'
    $ownerPid = Show-Value (Get-Field $status 'owner_pid')
    $elapsed = Get-Elapsed $startedAt

    $stepRows = @()
    $steps = Get-Field $status 'steps'
    if ($null -ne $steps) {
        foreach ($step in @($steps)) {
            $name = Show-Value (Get-Field $step 'step')
            $code = Get-Field $step 'returncode'
            $rowStatus = Get-Field $step 'status'
            if ($null -eq $rowStatus) {
                if ($null -eq $code) { $rowStatus = 'RUNNING' }
                elseif ([int]$code -eq 0) { $rowStatus = 'OK' }
                else { $rowStatus = 'FAIL' }
            }
            # A DEFERRED or retried step carries its explanation in 'note'.
            $note = ''
            $stepNote = Get-Field $step 'note'
            if ($null -ne $stepNote) { $note = [string]$stepNote }
            $firstCode = Get-Field $step 'first_returncode'
            if ($null -ne $firstCode -and $note -eq '') {
                $note = 'recovered on retry (first exit ' + [string]$firstCode + ')'
            }
            $partial = Get-Field $step 'partial_sources'
            if ($null -ne $partial) { $note = 'PARTIAL: ' + ((@($partial)) -join ', ') }
            $press = Get-Field $step 'press_partial'
            if ($null -ne $press) {
                $pending = Get-Field $press 'pending_body_urls'
                if ($null -ne $pending) { $note = $note + ' pending_body_urls=' + [string]$pending }
            }
            $duration = Get-Field $step 'duration_seconds'
            if ($null -eq $duration) { $duration = '' }
            $stepRows += (New-Object PSObject -Property ([ordered]@{
                Step = $name
                Status = $rowStatus
                RC = Show-Value $code
                Seconds = $duration
                Note = $note
            }))
        }
    }

    # ---- daily gate -------------------------------------------------------
    $daily = Read-JsonSafe -Path $dailyPath
    $today = (Get-Date).ToString('yyyy-MM-dd')
    $todayRecord = $null
    $days = Get-Field $daily 'days'
    if ($null -ne $days) { $todayRecord = Get-Field $days $today }

    # ---- media collection -------------------------------------------------
    $media = Read-JsonSafe -Path $mediaPath

    # ---- render -----------------------------------------------------------
    Clear-Host
    Write-Host ('CONGRESS PIPELINE - LIVE    ' + (Get-Date).ToString('yyyy-MM-dd HH:mm:ss') +
                '    root: ' + $Root) -ForegroundColor White

    Write-Section 'pipeline'
    Write-Host ('  status     : ') -NoNewline
    Write-Host $runStatus -ForegroundColor (Get-StatusColor $runStatus)
    Write-Host ('  mode       : ' + $mode)
    Write-Host ('  active step: ' + $activeStep)
    Write-Host ('  started    : ' + (Show-Value $startedAt) + '   elapsed: ' + $elapsed)
    Write-Host ('  owner pid  : ' + $ownerPid)
    $failure = Get-Field $status 'error'
    if ($null -ne $failure) { Write-Host ('  error      : ' + $failure) -ForegroundColor Red }
    if ($stepRows.Count -gt 0) {
        $stepRows | Format-Table -AutoSize | Out-String -Width 200 | Write-Host
    } else {
        Write-Host '  steps      : (not yet)'
    }

    Write-Section 'daily gate'
    if ($null -eq $todayRecord) {
        Write-Host ('  ' + $today + ': (not yet)')
    } else {
        Write-Host ('  ' + $today + ' status: ' + (Show-Value (Get-Field $todayRecord 'status')))
        $success = Get-Field $todayRecord 'success'
        if ($null -eq $success) {
            Write-Host '  success   : (not yet)' -ForegroundColor DarkGray
        } else {
            Write-Host ('  success   : ' + (Show-Value (Get-Field $success 'attempt_id'))) -ForegroundColor Green
        }
        $attempts = Get-Field $todayRecord 'attempts'
        foreach ($attempt in @($attempts)) {
            $id = [string](Show-Value (Get-Field $attempt 'id'))
            if ($id.Length -gt 12) { $id = $id.Substring(0, 12) }
            $line = '   - ' + $id.PadRight(12) + ' ' +
                    ([string](Show-Value (Get-Field $attempt 'trigger'))).PadRight(10) + ' ' +
                    ([string](Show-Value (Get-Field $attempt 'status'))).PadRight(11) + ' ' +
                    (Show-Value (Get-Field $attempt 'started_at'))
            Write-Host $line
            $attemptError = Get-Field $attempt 'error'
            if ($null -ne $attemptError) { Write-Host ('       error: ' + $attemptError) -ForegroundColor Red }
        }
    }

    Write-Section 'media collection'
    if ($null -eq $media) {
        Write-Host '  (not yet)'
    } else {
        $mediaStatus = Get-LiveStatus $media
        Write-Host ('  status     : ') -NoNewline
        Write-Host $mediaStatus -ForegroundColor (Get-StatusColor $mediaStatus)
        Write-Host ('  mode       : ' + (Show-Value (Get-Field $media 'mode')))
        $partialSources = Get-Field $media 'partial_sources'
        if ($null -eq $partialSources -or @($partialSources).Count -eq 0) {
            Write-Host '  partial    : none'
        } else {
            Write-Host ('  partial    : ' + ((@($partialSources)) -join ', ')) -ForegroundColor Yellow
        }
        $pressPartial = Get-Field $media 'press_partial'
        if ($null -ne $pressPartial) {
            Write-Host ('  press      : pending_body_urls=' +
                        (Show-Value (Get-Field $pressPartial 'pending_body_urls')) +
                        '  failed_bodies=' + (Show-Value (Get-Field $pressPartial 'failed_bodies'))) -ForegroundColor Yellow
        }
        foreach ($source in @(Get-Field $media 'sources')) {
            $sourceStatus = [string](Show-Value (Get-Field $source 'status'))
            $text = '   - ' + ([string](Show-Value (Get-Field $source 'source'))).PadRight(22) +
                    $sourceStatus.PadRight(15) +
                    ([string](Show-Value (Get-Field $source 'duration_seconds'))).PadLeft(9) + 's'
            Write-Host $text -ForegroundColor (Get-StatusColor $sourceStatus)
        }
    }

    Write-Section 'progress log (last 12)'
    $tail = Read-TailSafe -Path $livePath -Count 12
    if ($tail.Count -eq 0) {
        Write-Host '  (not yet)'
    } else {
        foreach ($entry in $tail) { Write-Host ('  ' + $entry) }
    }

    $activeLog = $null
    foreach ($step in @($steps)) {
        $candidate = Get-Field $step 'log'
        if ($null -ne $candidate) { $activeLog = [string]$candidate }
    }
    if ($activeStep -ne '(not yet)' -and $null -ne $status) {
        $runDir = Get-Field $status 'run_dir'
        if ($null -ne $runDir) {
            $guess = Join-Path ([string]$runDir) ($activeStep + '.log')
            if (Test-Path -LiteralPath $guess) { $activeLog = $guess }
        }
    }
    Write-Section ('step log (last 6)')
    if ($null -eq $activeLog) {
        Write-Host '  (not yet)'
    } else {
        Write-Host ('  ' + $activeLog) -ForegroundColor DarkGray
        $stepTail = Read-TailSafe -Path $activeLog -Count 6
        if ($stepTail.Count -eq 0) {
            Write-Host '  (not yet)'
        } else {
            foreach ($entry in $stepTail) { Write-Host ('  ' + $entry) }
        }
    }

    Write-Host ''
    Write-Host ('refreshing every ' + $IntervalSeconds + 's - Ctrl+C to stop') -ForegroundColor DarkGray
    Start-Sleep -Seconds $IntervalSeconds
}
