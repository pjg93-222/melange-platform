<#
.SYNOPSIS
Standalone read-only progress/event watcher for the GovInfo bill-XML collector.

.DESCRIPTION
Observes an immutable collector run directory and emits sanitized Glassbox
events so the run is visible in the Glass Box process matrix. It never writes
to, retries, restarts, or kills the observed collector, and it never opens or
republishes credential material. Receipt counts come from the filesystem;
sample lines come from the collector's own stdout progress log.

.EXAMPLE
powershell -NoProfile -ExecutionPolicy Bypass -File .\fec\watch_govinfo_collector_glassbox.ps1 `
  -RunId full-18440-20260727a
#>
param(
    [Parameter(Mandatory = $true)][string]$RunId,
    [string]$Root = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems',
    [string]$OutDir = 'audits\legfix5\govinfo_bill_xml_versions',
    [int]$IntervalSeconds = 20,
    [string]$Lane = 'claude'
)

$ErrorActionPreference = 'Stop'
$emit = 'C:\Users\pgoff\.claude\glassbox\emit.ps1'

$runRoot = Join-Path $Root (Join-Path $OutDir "runs\$RunId")
$progressLog = Join-Path $Root (Join-Path $OutDir "$RunId.progress.log")
$dispatchDir = Join-Path $runRoot 'receipts\dispatch'
$acceptedDir = Join-Path $runRoot 'receipts\accepted'
$failureDir = Join-Path $runRoot 'receipts\failure'
$completed = Join-Path $runRoot 'completed.json'

function Get-Count([string]$path) {
    if (-not (Test-Path $path)) { return 0 }
    return @(Get-ChildItem -LiteralPath $path -Filter *.json -File).Count
}

function Send-Event {
    param([string]$Kind, [string]$Status, [string]$Summary, [string]$Detail, [string]$MetadataJson)
    & $emit -Lane $Lane -Source 'govinfo-xml-collector' -RunId $RunId `
        -Stage 'govinfo-official-xml-18440' -Kind $Kind -Status $Status `
        -Summary $Summary -Detail $Detail -Cwd $Root `
        -MetadataJson $MetadataJson -Quiet | Out-Null
}

$total = 18440
$last = -1

Send-Event -Kind 'stage.started' -Status 'running' `
    -Summary "0/$total" `
    -Detail 'Observing immutable GovInfo official-XML collection run (read-only watcher; no browser, cookies, or retries).' `
    -MetadataJson (@{ run_id = $RunId; run_root = $runRoot; attempts_per_job = 1; retry_waits = 0 } | ConvertTo-Json -Compress)

while ($true) {
    $accepted = Get-Count $acceptedDir
    $failed = Get-Count $failureDir
    $dispatched = Get-Count $dispatchDir
    $done = $accepted + $failed
    $pct = if ($total -gt 0) { [math]::Round(100.0 * $done / $total, 1) } else { 0 }

    $tail = ''
    if (Test-Path $progressLog) {
        $lines = @(Get-Content -LiteralPath $progressLog -Tail 1 -ErrorAction SilentlyContinue)
        if ($lines.Count -gt 0) { $tail = [string]$lines[-1] }
    }

    $isDone = Test-Path $completed

    if ($done -ne $last -or $isDone) {
        $last = $done
        $meta = @{
            dispatched     = $dispatched
            accepted       = $accepted
            quarantined    = $failed
            remaining      = $total - $done
            percent        = $pct
            active_workers = 16
            latest_record  = $tail
        } | ConvertTo-Json -Compress

        if ($isDone) {
            $receipt = Get-Content -LiteralPath $completed -Raw | ConvertFrom-Json
            $status = if ($receipt.failure_count -eq 0) { 'completed' } else { 'completed' }
            Send-Event -Kind 'stage.completed' -Status $status `
                -Summary "$done/$total" `
                -Detail ("Terminal receipt written: status=$($receipt.status) accepted=$($receipt.accepted_count) quarantined=$($receipt.failure_count) retry_waits=$($receipt.retry_waits).") `
                -MetadataJson $meta
            break
        }

        Send-Event -Kind 'record.accepted' -Status 'running' `
            -Summary "$done/$total" `
            -Detail "accepted=$accepted quarantined=$failed remaining=$($total - $done) | $tail" `
            -MetadataJson $meta
    }

    Start-Sleep -Seconds $IntervalSeconds
}
