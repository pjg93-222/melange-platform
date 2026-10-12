<#
.SYNOPSIS
  Wait for the full-text collection to finish, then rebuild candidate evidence
  against the receipts it actually produced. Emits to the Glass Box.

.DESCRIPTION
  The candidate evidence on disk (evidence-20260727a) was built from the 2,886
  receipts in text_relevance_httr_full_stage_20260726. The live run has banked
  far more in a DIFFERENT directory that no evidence generation has consumed, so
  those pages are collected but invisible to every downstream gate.

  Rebuilding is additive and safe: the builder writes a NEW generation directory
  and never mutates an existing one. It does not score, sync, promote, or touch
  the main corpus, and it cannot move a membership out of flagged on its own --
  the 19-category review and the undecided memberships are separate blockers.
  What it does buy is an honest full_text_ready count.

  Deliberately does NOT run culling. Culling today reproduces the existing
  all-flagged partition under a new id and tells nobody anything.
#>
param(
    [string]$Workspace = "C:\Users\pgoff\OneDrive\Desktop\Melange Systems",
    [string]$Generation = "audits\legfix5\congress_advanced_pages_20260725\text_relevance_httr_full_20260727z",
    [string]$EventsDirectory = "C:\Users\pgoff\.claude\glassbox\events",
    [int]$CorpusTotal = 18557,
    # Collection is treated as settled when the receipt count stops moving, not
    # only when it hits the corpus total: some candidates legitimately end in a
    # terminal failure artifact rather than an accepted receipt.
    [int]$SettleMinutes = 12,
    [int]$PollSeconds = 60,
    [int]$MaxHours = 10,
    [string]$Lane = "codex"
)

$ErrorActionPreference = 'Stop'
$gen = Join-Path $Workspace $Generation
$receipts = Join-Path $gen 'receipts'
$runId = 'evidence-rebuild-{0}' -f (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
if (-not (Test-Path $EventsDirectory)) { New-Item -ItemType Directory $EventsDirectory -Force | Out-Null }

function Emit {
    param([string]$Status, [string]$Stage, [string]$Summary, [string]$Detail, [int]$Current, [int]$Total)
    $payload = [ordered]@{
        schema_version = 1
        timestamp_utc = (Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        lane = $Lane; source = 'candidate-evidence-rebuild'; run_id = $runId
        kind = 'pipeline.progress'; status = $Status; stage = $Stage
        summary = $Summary; detail = $Detail; current = $Current; total = $Total
    }
    $name = ('evt-{0}-evrebuild-{1}-{2}.json' -f `
        (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssfff'), $PID, $Stage) -replace '[^A-Za-z0-9._-]+','-'
    try {
        ($payload | ConvertTo-Json -Depth 6) | Out-File -FilePath (Join-Path $EventsDirectory ($name + '.tmp')) -Encoding utf8
        Move-Item -LiteralPath (Join-Path $EventsDirectory ($name + '.tmp')) -Destination (Join-Path $EventsDirectory $name) -Force
    } catch { }
}

function Receipts { @(Get-ChildItem $receipts -Recurse -File -Filter 'receipt.json' -ErrorAction SilentlyContinue).Count }

Emit -Status 'running' -Stage 'waiting' `
     -Summary ("Waiting for collection to settle before rebuilding evidence ({0}/{1})" -f (Receipts), $CorpusTotal) `
     -Detail ("Settles after {0} min with no new receipts, or on reaching the corpus total." -f $SettleMinutes) `
     -Current (Receipts) -Total $CorpusTotal

$deadline = (Get-Date).AddHours($MaxHours)
$last = -1
$lastChange = Get-Date
while ((Get-Date) -lt $deadline) {
    $now = Receipts
    if ($now -ne $last) { $last = $now; $lastChange = Get-Date }
    $idle = ((Get-Date) - $lastChange).TotalMinutes
    $collectorAlive = (@(Get-Process Rterm -ErrorAction SilentlyContinue)).Count -gt 0
    if ($now -ge $CorpusTotal) { break }
    if ($idle -ge $SettleMinutes -and -not $collectorAlive) { break }
    Start-Sleep -Seconds $PollSeconds
}

$final = Receipts
Emit -Status 'running' -Stage 'settled' `
     -Summary ("Collection settled at {0}/{1}; rebuilding candidate evidence" -f $final, $CorpusTotal) `
     -Detail 'Writing a NEW evidence generation; no existing generation is mutated.' `
     -Current $final -Total $CorpusTotal

$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmss')
$evidenceOut = Join-Path $Workspace ("audits\legfix5\candidate_evidence\evidence-{0}" -f $stamp)
$base = Join-Path $Workspace 'audits\legfix5\congress_advanced_pages_20260725'
$py = 'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe'

$args = @(
    (Join-Path $Workspace 'fec\legislation\build_legislation_candidate_evidence.py'),
    '--records', (Join-Path $base 'congress_advanced_records.ndjson'),
    '--memberships', (Join-Path $base 'congress_advanced_memberships.ndjson'),
    '--billstatus', (Join-Path $Workspace 'audits\legfix5\billstatus_bulk_enrichment\fetch_runs\c7ca822fd5951478a33a814d228b71ffdf69745e6d823001fefaba620282c435\parsed\sha256'),
    '--full-text-receipts', $receipts,
    '--govinfo-run', (Join-Path $Workspace 'audits\legfix5\govinfo_bill_xml_versions\runs\full-18440-20260727b'),
    '--out', $evidenceOut
)

$log = Join-Path $Workspace ('audits\legfix5\evidence_rebuild_{0}.log' -f $stamp)
& $py @args *> $log
$code = $LASTEXITCODE

$receiptPath = Join-Path $evidenceOut 'receipt.json'
$accepted = $null
if (Test-Path $receiptPath) {
    try { $accepted = (Get-Content $receiptPath -Raw | ConvertFrom-Json).counts.full_text_accepted } catch { }
}

if ($code -eq 0) {
    Emit -Status 'completed' -Stage 'rebuilt' `
         -Summary ("Candidate evidence rebuilt; full_text_accepted={0}" -f $accepted) `
         -Detail ("New generation: {0}. Culling NOT run -- it would reproduce the existing all-flagged partition." -f $evidenceOut) `
         -Current $final -Total $CorpusTotal
} else {
    Emit -Status 'failed' -Stage 'rebuild-failed' `
         -Summary ("Evidence rebuild exited {0}" -f $code) `
         -Detail ("See {0}" -f $log) -Current $final -Total $CorpusTotal
}
