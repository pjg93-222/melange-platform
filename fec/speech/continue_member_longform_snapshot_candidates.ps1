param(
    [Parameter(Mandatory = $true)][string]$RunRoot,
    [ValidateRange(1, 8)][int]$CpuThreads = 2,
    [ValidateRange(60, 7200)][int]$ContextSeconds = 1800,
    [ValidateRange(128, 8192)][int]$MaxNewTokens = 1500
)

# Consume a separately created immutable snapshot. This never acquires audio,
# writes live source selection, creates attestations, or publishes profiles.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$CandidateWorkspace = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
Set-Location -LiteralPath $CandidateWorkspace
$CandidateRoot = (Resolve-Path -LiteralPath $RunRoot).Path
$CandidateLog = Join-Path $CandidateRoot 'candidate-continuation.log'
$CandidateLock = [System.IO.File]::Open(
    (Join-Path $CandidateRoot 'candidate-writer.lock'), 'OpenOrCreate', 'ReadWrite', 'None'
)

function Invoke-CandidatePython {
    param([string[]]$Arguments)
    ('[' + (Get-Date -Format o) + '] python ' + ($Arguments -join ' ')) |
        Tee-Object -FilePath $CandidateLog -Append
    & python @Arguments 2>&1 | Tee-Object -FilePath $CandidateLog -Append
    if ($LASTEXITCODE -ne 0) { throw "Candidate stage failed: $LASTEXITCODE" }
}

function Assert-CandidateReceipt {
    param([string]$ReceiptPath, [string]$ArtifactPath, [string]$InputPath,
          [string]$InputName, [string]$ExpectedStatus)
    $CandidateReceipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($CandidateReceipt.status -ne $ExpectedStatus) { throw "Candidate receipt not ready: $ReceiptPath" }
    if ([System.IO.Path]::GetFullPath([string]$CandidateReceipt.output.path) -ne $ArtifactPath) {
        throw 'Candidate output path mismatch'
    }
    if ((Get-FileHash -LiteralPath $ArtifactPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $CandidateReceipt.output.sha256) {
        throw 'Candidate output hash mismatch'
    }
    if ((Get-FileHash -LiteralPath $InputPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $CandidateReceipt.inputs.$InputName) {
        throw 'Candidate input hash mismatch'
    }
    return $CandidateReceipt
}

try {
    Invoke-CandidatePython -Arguments @('-m', 'fec.speech.snapshot_member_longform_ready_sources',
                                      '--verify-run-root', $CandidateRoot)
    # Fail before local inference unless the installed continuity verifier has
    # the whole-interior coverage contract. A sampled prefix is not a full turn.
    Invoke-CandidatePython -Arguments @('-c',
        'from fec.speech import verify_member_longform_speaker_continuity as v; assert v.CONTINUITY_CONTRACT_VERSION == "whole-turn-interior-coverage-v2", "whole-turn continuity unavailable"')
    $CandidateSources = Join-Path $CandidateRoot 'ready_sources.jsonl'
    $CandidateTurns = Join-Path $CandidateRoot 'full_turn_candidates.jsonl'
    $CandidateTurnsReceipt = Join-Path $CandidateRoot 'full_turn_candidates.receipt.json'
    $CandidateContinuity = Join-Path $CandidateRoot 'speaker_continuity.jsonl'
    $CandidateContinuityReceipt = Join-Path $CandidateRoot 'speaker_continuity.receipt.json'
    $CandidateClassifierArgs = @(
        '-u', '-m', 'fec.speech.classify_member_longform_full_turns_local',
        '--input', $CandidateSources, '--output', $CandidateTurns,
        '--receipt', $CandidateTurnsReceipt, '--cpu-threads', [string]$CpuThreads,
        '--segments-per-member', '2', '--context-seconds', [string]$ContextSeconds,
        '--max-new-tokens', [string]$MaxNewTokens
    )
    # Exact tokenizer count; no model weights, output writes, or silent prompt
    # truncation. Overlapping cue regions may cover more than ContextSeconds.
    Invoke-CandidatePython -Arguments (@($CandidateClassifierArgs) + @('--preflight-only'))
    Invoke-CandidatePython -Arguments $CandidateClassifierArgs
    $null = Assert-CandidateReceipt -ReceiptPath $CandidateTurnsReceipt -ArtifactPath $CandidateTurns `
        -InputPath $CandidateSources -InputName 'identity_cues_sha256' -ExpectedStatus 'PASS_CANDIDATES_ONLY'
    Invoke-CandidatePython -Arguments @('-m', 'fec.speech.snapshot_member_longform_ready_sources',
                                      '--verify-run-root', $CandidateRoot)
    Invoke-CandidatePython -Arguments @(
        '-u', '-m', 'fec.speech.verify_member_longform_speaker_continuity',
        '--input', $CandidateTurns, '--output', $CandidateContinuity,
        '--receipt', $CandidateContinuityReceipt, '--max-windows-per-member', '0'
    )
    # Detailed continuity results may reject candidates. Completion here is
    # completion of a diagnostic gate, never approval of member turn boundaries.
    $CandidateContinuityResult = Assert-CandidateReceipt -ReceiptPath $CandidateContinuityReceipt `
        -ArtifactPath $CandidateContinuity -InputPath $CandidateTurns `
        -InputName 'turn_candidates_sha256' -ExpectedStatus 'PASS_REVIEW_QUEUE_ONLY'
    if ($CandidateContinuityResult.coverage_contract_version -ne 'whole-turn-interior-coverage-v2') {
        throw 'Continuity receipt lacks whole-turn interior coverage contract'
    }
    if ($CandidateContinuityResult.contract.max_windows_per_member -ne 0 -or
        $CandidateContinuityResult.contract.complete_turn_interior_coverage_required -ne $true) {
        throw 'Continuity receipt did not apply unlimited whole-turn coverage'
    }
    ('[' + (Get-Date -Format o) + '] CANDIDATE-ONLY STAGES FINISHED. Identity and complete-turn human boundary review remain required; no profiles changed.') |
        Tee-Object -FilePath $CandidateLog -Append
}
finally { $CandidateLock.Dispose() }
