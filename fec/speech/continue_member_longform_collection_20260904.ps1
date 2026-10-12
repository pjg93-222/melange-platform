param([int]$CpuThreads = 4)

# Finite collection/transcription repair job. Does not generate profiles,
# select sources, certify speakers, or promote behavioral observations.
$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$CollectionWorkspace = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
$CollectionLog = 'C:\melange-data\asr_backfill_logs\collection-repair-20260904.log'
$CollectionManifest = 'C:\melange-data\member_longform_transcripts_v1.jsonl'
$CollectionLockPath = 'C:\melange-data\member-longform-collection-writer.lock'
$CollectionQualityHolds = Join-Path $CollectionWorkspace 'audits\longform_collection_20260904\quality_holds.jsonl'
Set-Location -LiteralPath $CollectionWorkspace
New-Item -ItemType Directory -Force -Path (Split-Path $CollectionLog) | Out-Null
$CollectionLock = [System.IO.File]::Open($CollectionLockPath, 'OpenOrCreate', 'ReadWrite', 'None')

function Invoke-CollectionStage {
    param([string]$Stage, [string[]]$Arguments)
    ('[' + (Get-Date -Format o) + '] START ' + $Stage) | Tee-Object -FilePath $CollectionLog -Append
    & python @Arguments 2>&1 | Tee-Object -FilePath $CollectionLog -Append
    if ($LASTEXITCODE -ne 0) { throw "Collection stage '$Stage' failed: $LASTEXITCODE" }
    ('[' + (Get-Date -Format o) + '] END ' + $Stage) | Tee-Object -FilePath $CollectionLog -Append
}

try {
    $CollectionArgs = @(
        '-u', '-m', 'fec.speech.transcribe_member_longform_local_asr',
        '--acquisition', 'data/speech/member_longform_acquisition_v1.jsonl',
        '--transcripts', $CollectionManifest,
        '--receipt', 'C:/melange-data/member_longform_transcripts_v1.receipt.json',
        '--transcript-root', 'C:/melange-data/member_longform_transcripts_v1',
        '--cpu-threads', [string]$CpuThreads, '--language', 'auto',
        '--no-condition-on-previous-text', '--shortest-first', '--retry-errors', '--bounded-feature-memory'
    )
    Invoke-CollectionStage -Stage 'source-bound transcription backlog' -Arguments $CollectionArgs

    # Retry only unchanged, specifically held transcript artifacts. Previous
    # repair output is retained, and a new hash is NOT a quality approval.
    $CollectionRows = @{}
    Get-Content -LiteralPath $CollectionManifest -Encoding UTF8 | ForEach-Object {
        if ($_.Trim()) { $CollectionRow = $_ | ConvertFrom-Json; $CollectionRows[$CollectionRow.member] = $CollectionRow }
    }
    $CollectionRepairMembers = @()
    Get-Content -LiteralPath $CollectionQualityHolds -Encoding UTF8 | ForEach-Object {
        if ($_.Trim()) {
            $CollectionHold = $_ | ConvertFrom-Json
            $CollectionRow = $CollectionRows[$CollectionHold.member]
            if ($null -ne $CollectionRow -and
                $CollectionRow.source_id -eq $CollectionHold.source_id -and
                $CollectionRow.wav_sha256 -eq $CollectionHold.wav_sha256 -and
                $CollectionRow.transcript_sha256 -eq $CollectionHold.transcript_sha256) {
                $CollectionRepairMembers += [string]$CollectionHold.member
            }
        }
    }
    if ($CollectionRepairMembers.Count -gt 0) {
        $CollectionRepairArgs = @($CollectionArgs) + @('--refresh')
        foreach ($CollectionRepairMember in ($CollectionRepairMembers | Sort-Object -Unique)) {
            $CollectionRepairArgs += @('--member', $CollectionRepairMember)
        }
        Invoke-CollectionStage -Stage 'hash-specific transcript quality repairs' -Arguments $CollectionRepairArgs
    }
    Invoke-CollectionStage -Stage 'refresh non-authoritative name-cue candidates' -Arguments @(
        '-u', '-m', 'fec.speech.screen_member_longform_identity_cues',
        '--transcripts', $CollectionManifest,
        '--output', 'C:/melange-data/member_longform_identity_cues_v1.jsonl',
        '--receipt', 'C:/melange-data/member_longform_identity_cues_v1.receipt.json'
    )
    ('[' + (Get-Date -Format o) + '] TRANSCRIPTION STAGES FINISHED. Fresh quality audit, source selection, and evidence review remain required. No profiles generated.') |
        Tee-Object -FilePath $CollectionLog -Append
}
finally {
    $CollectionLock.Dispose()
}
