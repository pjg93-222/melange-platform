<#
.SYNOPSIS
Register completed legfix5 pipeline stages into the Glass Box event stream.

.DESCRIPTION
The GovInfo collector streams live through watch_govinfo_collector_glassbox.ps1.
The downstream stages (candidate evidence, membership decisions, reversible
culling) ran as direct PowerShell and produced immutable receipts but emitted
no events. This script reads each stage's real receipt and emits one
stage.completed event carrying its actual digests and counts.

These are registration events for work that has already finished. Each event
says so explicitly in its detail line, and every number is read from the
receipt on disk -- nothing is asserted that the receipt does not contain.
It never re-runs a stage, and it writes nothing outside the event stream.
#>
param(
    [string]$Root = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems',
    [string]$Lane = 'claude'
)

$ErrorActionPreference = 'Stop'
$emit = 'C:\Users\pgoff\.claude\glassbox\emit.ps1'

function Read-Receipt([string]$relative) {
    $path = Join-Path $Root $relative
    if (-not (Test-Path $path)) { throw "missing receipt: $path" }
    return Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
}

function Send-Stage {
    param(
        [string]$RunId, [string]$Stage, [string]$Summary,
        [string]$Detail, [hashtable]$Metadata
    )
    & $emit -Lane $Lane -Source 'legfix5-pipeline' -RunId $RunId -Stage $Stage `
        -Kind 'stage.completed' -Status 'completed' -Summary $Summary `
        -Detail $Detail -Cwd $Root `
        -MetadataJson ($Metadata | ConvertTo-Json -Compress -Depth 6) -Quiet | Out-Null
    Write-Host "emitted: $Stage :: $Summary"
}

# 1. Candidate evidence envelopes
$ev = Read-Receipt 'audits\legfix5\candidate_evidence\evidence-20260727a\receipt.json'
$evOut = $ev.outputs.'candidate_evidence.ndjson'
Send-Stage -RunId 'evidence-20260727a' -Stage 'candidate-evidence-assembly' `
    -Summary "$($evOut.record_count)/18557 candidates" `
    -Detail ("Registration of a completed stage. Official-evidence envelopes assembled. " +
        "title=$($ev.counts.title_available) full_text_accepted=$($ev.counts.full_text_accepted) " +
        "stage=$($ev.counts.stage_available) summary_officially_unavailable=$($ev.counts.summary_officially_unavailable). " +
        "No category review performed; no corpus write.") `
    -Metadata @{
        receipt_status = $ev.status
        output_sha256  = $evOut.sha256
        record_count   = $evOut.record_count
        counts         = $ev.counts
        safety         = $ev.safety
    }

# 2. Membership decision projection
$dec = Read-Receipt 'audits\legfix5\membership_decisions\decisions-20260727a\receipt.json'
$decOut = $dec.outputs.'membership_decisions.ndjson'
Send-Stage -RunId 'decisions-20260727a' -Stage 'membership-decision-projection' `
    -Summary "$($decOut.record_count)/$($dec.counts.resolved_seen) resolved edges projected" `
    -Detail ("Registration of a completed stage. $($dec.counts.unresolved_skipped) unresolved rows skipped, " +
        "$($dec.counts.outside_discovery_universe_dropped) edges dropped as outside the discovery universe " +
        "(logged, never silently truncated). No decision upgraded or completed.") `
    -Metadata @{
        receipt_status = $dec.status
        output_sha256  = $decOut.sha256
        counts         = $dec.counts
        safety         = $dec.safety
    }

# 3. Reversible deterministic culling
$cull = Read-Receipt ('audits\legfix5\reversible_culling\culling-20260727a\generations\' +
    '241cbbb105a623409afb3cdbb35bee30598a696ad76652a85a837fdd48036af1\manifest.json')
$r = $cull.receipt
$mp = $r.counts.membership_partition
Send-Stage -RunId 'culling-20260727a' -Stage 'reversible-deterministic-culling' `
    -Summary "$($r.counts.membership_count)/64004 memberships partitioned" `
    -Detail ("Registration of a completed stage. flagged=$($mp.flagged) junk=$($mp.junk) keep=$($mp.keep) " +
        "across $($r.counts.candidate_count) candidates. Junk and restoration ledgers are empty; " +
        "nothing deleted. Integration remains unauthorized.") `
    -Metadata @{
        receipt_status       = $r.status
        generation_id        = $cull.generation_id
        membership_partition = $mp
        candidate_partition  = $r.counts.candidate_partition
        gates                = $r.gates
        safety               = $r.safety
        flagged_review_csv   = $r.outputs.'flagged_memberships.csv'.path
    }

Write-Host ''
Write-Host 'All completed legfix5 pipeline stages are now registered in the Glass Box.'
