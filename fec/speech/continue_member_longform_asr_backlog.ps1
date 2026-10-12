param(
    [Parameter(Mandatory = $true)]
    [int]$WaitPid,

    [Parameter(Mandatory = $true)]
    [ValidateSet('primary', 'leadership-secondary')]
    [string]$Lane
)

$ErrorActionPreference = 'Stop'
$Workspace = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
$LogRoot = 'C:\melange-data\asr_backfill_logs'
Set-Location -LiteralPath $Workspace
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
$Log = Join-Path $LogRoot ($Lane + '-20260903.log')

$running = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
if ($null -ne $running) {
    Wait-Process -Id $WaitPid
}

('[' + (Get-Date -Format o) + '] starting ' + $Lane) | Tee-Object -FilePath $Log -Append

if ($Lane -eq 'primary') {
    & python -m fec.speech.transcribe_member_longform_local_asr `
        --acquisition 'data\speech\member_longform_acquisition_v1.jsonl' `
        --transcripts 'C:\melange-data\member_longform_transcripts_v1.jsonl' `
        --receipt 'C:\melange-data\member_longform_transcripts_v1.receipt.json' `
        --cpu-threads 4 --shortest-first --retry-errors 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "primary local ASR failed: $LASTEXITCODE" }

    & python -m fec.speech.screen_member_longform_identity_cues `
        --transcripts 'C:\melange-data\member_longform_transcripts_v1.jsonl' `
        --output 'C:\melange-data\member_longform_identity_cues_v1.jsonl' `
        --receipt 'C:\melange-data\member_longform_identity_cues_v1.receipt.json' 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "primary identity-cue screen failed: $LASTEXITCODE" }
}
else {
    & python -m fec.speech.screen_member_longform_identity_cues `
        --transcripts 'C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl' `
        --output 'C:\melange-data\member_leadership_prerole_identity_cues_v1.jsonl' `
        --receipt 'C:\melange-data\member_leadership_prerole_identity_cues_v1.receipt.json' 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "leadership identity-cue screen failed: $LASTEXITCODE" }

    & python -m fec.speech.transcribe_member_longform_local_asr `
        --acquisition 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl' `
        --transcripts 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl' `
        --receipt 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.receipt.json' `
        --transcript-root 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1' `
        --cpu-threads 4 --shortest-first --retry-errors 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "second-source local ASR failed: $LASTEXITCODE" }

    & python -m fec.speech.screen_member_longform_identity_cues `
        --transcripts 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl' `
        --output 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl' `
        --receipt 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.receipt.json' 2>&1 |
        Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) { throw "second-source identity-cue screen failed: $LASTEXITCODE" }
}

('[' + (Get-Date -Format o) + '] completed ' + $Lane) | Tee-Object -FilePath $Log -Append
