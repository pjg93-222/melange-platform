param(
    [int]$WaitPidPrimary = 0,
    [int]$WaitPidLeadership = 0,
    [int]$WaitPidSecondary = 0
)

$ErrorActionPreference = 'Stop'
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$Workspace = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
$LogRoot = 'C:\melange-data\asr_backfill_logs'
$Log = Join-Path $LogRoot 'profile-ready-20260903.log'
Set-Location -LiteralPath $Workspace
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

foreach ($ProcessIdToWaitFor in @($WaitPidPrimary, $WaitPidLeadership, $WaitPidSecondary)) {
    if ($ProcessIdToWaitFor -le 0) {
        continue
    }
    $Running = Get-Process -Id $ProcessIdToWaitFor -ErrorAction SilentlyContinue
    if ($null -ne $Running) {
        Wait-Process -Id $ProcessIdToWaitFor
    }
}

function Invoke-LoggedPython {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    ('[' + (Get-Date -Format o) + '] python ' + ($Arguments -join ' ')) |
        Tee-Object -FilePath $Log -Append
    & python @Arguments 2>&1 | Tee-Object -FilePath $Log -Append
    if ($LASTEXITCODE -ne 0) {
        throw "python stage failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

function Get-CurrentSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required artifact is missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-CurrentArtifactReceipt {
    param(
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$ArtifactPath,
        [Parameter(Mandatory = $true)][string[]]$AllowedStatuses,
        [hashtable]$InputBindings = @{}
    )
    if (-not (Test-Path -LiteralPath $ReceiptPath -PathType Leaf)) {
        throw "Required receipt is missing: $ReceiptPath"
    }
    $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($AllowedStatuses -notcontains [string]$Receipt.status) {
        throw "Receipt status is not ready ($($Receipt.status)): $ReceiptPath"
    }
    $ResolvedArtifact = (Resolve-Path -LiteralPath $ArtifactPath).Path
    $RecordedOutputPath = [string]$Receipt.output.path
    if (-not $RecordedOutputPath) {
        throw "Receipt lacks an output path: $ReceiptPath"
    }
    $ResolvedRecordedOutput = [System.IO.Path]::GetFullPath($RecordedOutputPath)
    if (-not [string]::Equals(
        $ResolvedArtifact, $ResolvedRecordedOutput, [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Receipt output path is stale: $ReceiptPath"
    }
    $ArtifactSha256 = Get-CurrentSha256 -Path $ResolvedArtifact
    if ($ArtifactSha256 -ne ([string]$Receipt.output.sha256).ToLowerInvariant()) {
        throw "Receipt output hash is stale: $ReceiptPath"
    }
    foreach ($InputName in $InputBindings.Keys) {
        $Property = $Receipt.inputs.PSObject.Properties[$InputName]
        if ($null -eq $Property -or -not [string]$Property.Value) {
            throw "Receipt lacks input hash '$InputName': $ReceiptPath"
        }
        $InputSha256 = Get-CurrentSha256 -Path ([string]$InputBindings[$InputName])
        if ($InputSha256 -ne ([string]$Property.Value).ToLowerInvariant()) {
            throw "Receipt input hash '$InputName' is stale: $ReceiptPath"
        }
    }
    ('[' + (Get-Date -Format o) + '] validated current artifact ' + $ArtifactPath) |
        Tee-Object -FilePath $Log -Append
}

function Assert-CurrentAdditionalRetryInputs {
    param(
        [Parameter(Mandatory = $true)][string]$ReceiptPath,
        [Parameter(Mandatory = $true)][string]$CuesPath,
        [Parameter(Mandatory = $true)][string]$AcquisitionPath
    )
    $Receipt = Get-Content -LiteralPath $ReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $CueInputs = @($Receipt.inputs.additional_cues)
    $AcquisitionInputs = @($Receipt.inputs.additional_acquisitions)
    if ($CueInputs.Count -lt 1 -or $AcquisitionInputs.Count -lt 1) {
        throw "Additional-retry receipt lacks prior-source lineage: $ReceiptPath"
    }
    if ((Get-CurrentSha256 -Path $CuesPath) -ne ([string]$CueInputs[-1].sha256).ToLowerInvariant()) {
        throw "Additional-retry cue lineage is stale: $ReceiptPath"
    }
    if ((Get-CurrentSha256 -Path $AcquisitionPath) -ne ([string]$AcquisitionInputs[-1].sha256).ToLowerInvariant()) {
        throw "Additional-retry acquisition lineage is stale: $ReceiptPath"
    }
}

function Get-Utf8JsonlRowCount {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return 0
    }
    return @(
        Get-Content -LiteralPath $Path -Encoding UTF8 |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    ).Count
}

('[' + (Get-Date -Format o) + '] starting roster profile-ready candidate pipeline') |
    Tee-Object -FilePath $Log -Append

# Reconcile the queue against acquisition provenance after the upstream writer
# exits.  Reviewed replacements are detected by source ID/URL, so no member
# name (and no non-ASCII text) has to cross the native command-line boundary.
Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.acquire_member_longform_audio',
    '--queue', 'data\speech\member_longform_acquisition_queue_v1.jsonl',
    '--out', 'data\speech\member_longform_acquisition_v1.jsonl',
    '--receipt', 'data\speech\member_longform_acquisition_v1.receipt.json',
    '--media-root', 'C:\melange-data\member_longform_audio_v1',
    '--workers', '1', '--resume', '--retry-errors', '--retry-provider-resolution'
)

Assert-CurrentArtifactReceipt `
    -ReceiptPath 'data\speech\member_longform_acquisition_v1.receipt.json' `
    -ArtifactPath 'data\speech\member_longform_acquisition_v1.jsonl' `
    -AllowedStatuses @('COMPLETE') `
    -InputBindings @{ queue_sha256 = 'data\speech\member_longform_acquisition_queue_v1.jsonl' }
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'data\speech\member_leadership_prerole_acquisition_v1.receipt.json' `
    -ArtifactPath 'data\speech\member_leadership_prerole_acquisition_v1.jsonl' `
    -AllowedStatuses @('COMPLETE') `
    -InputBindings @{ queue_sha256 = 'data\speech\member_leadership_prerole_acquisition_queue_v1.jsonl' }
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'data\speech\member_longform_no_cue_retry_acquisition_v1.receipt.json' `
    -ArtifactPath 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl' `
    -AllowedStatuses @('COMPLETE') `
    -InputBindings @{ queue_sha256 = 'data\speech\member_longform_no_cue_retry_queue_v1.jsonl' }

# Retry the signed original-language caption route at a conservative pace.
# This route succeeds for many sources that the ordinary transcript endpoint
# reports as HTTP 429, and prevents unnecessary full-audio Whisper work.
Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.transcribe_member_longform_captions',
    '--acquisition', 'data\speech\member_longform_acquisition_v1.jsonl',
    '--out', 'C:\melange-data\member_longform_transcripts_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_transcripts_v1.receipt.json',
    '--transcript-root', 'C:\melange-data\member_longform_transcripts_v1',
    '--resume', '--retry-errors', '--retry-local-asr', '--workers', '1', '--request-delay', '5'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.transcribe_member_longform_captions',
    '--acquisition', 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl',
    '--out', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.receipt.json',
    '--transcript-root', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1',
    '--resume', '--retry-errors', '--retry-local-asr', '--workers', '1', '--request-delay', '5'
)

# Only sources still lacking a caption reach this expensive local-ASR fallback.
Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.transcribe_member_longform_local_asr',
    '--acquisition', 'data\speech\member_longform_acquisition_v1.jsonl',
    '--transcripts', 'C:\melange-data\member_longform_transcripts_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_transcripts_v1.receipt.json',
    '--cpu-threads', '8', '--shortest-first', '--retry-errors'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.transcribe_member_longform_local_asr',
    '--acquisition', 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl',
    '--transcripts', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.receipt.json',
    '--transcript-root', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1',
    '--cpu-threads', '8', '--shortest-first', '--retry-errors'
)

Assert-CurrentArtifactReceipt `
    -ReceiptPath 'C:\melange-data\member_longform_transcripts_v1.receipt.json' `
    -ArtifactPath 'C:\melange-data\member_longform_transcripts_v1.jsonl' `
    -AllowedStatuses @('COMPLETE_LOCALIZATION_ONLY') `
    -InputBindings @{ acquisition_sha256 = 'data\speech\member_longform_acquisition_v1.jsonl' }
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'C:\melange-data\member_leadership_prerole_transcripts_v1.receipt.json' `
    -ArtifactPath 'C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl' `
    -AllowedStatuses @('COMPLETE_LOCALIZATION_ONLY') `
    -InputBindings @{ acquisition_sha256 = 'data\speech\member_leadership_prerole_acquisition_v1.jsonl' }
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.receipt.json' `
    -ArtifactPath 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl' `
    -AllowedStatuses @('COMPLETE_LOCALIZATION_ONLY') `
    -InputBindings @{ acquisition_sha256 = 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl' }

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.screen_member_longform_identity_cues',
    '--transcripts', 'C:\melange-data\member_longform_transcripts_v1.jsonl',
    '--output', 'C:\melange-data\member_longform_identity_cues_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_identity_cues_v1.receipt.json'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.screen_member_longform_identity_cues',
    '--transcripts', 'C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl',
    '--output', 'C:\melange-data\member_leadership_prerole_identity_cues_v1.jsonl',
    '--receipt', 'C:\melange-data\member_leadership_prerole_identity_cues_v1.receipt.json'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.screen_member_longform_identity_cues',
    '--transcripts', 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl',
    '--output', 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.receipt.json'
)

Assert-CurrentArtifactReceipt `
    -ReceiptPath 'C:\melange-data\member_longform_identity_cues_v1.receipt.json' `
    -ArtifactPath 'C:\melange-data\member_longform_identity_cues_v1.jsonl' `
    -AllowedStatuses @('PASS_CANDIDATE_SCREEN_ONLY') `
    -InputBindings @{ transcripts_sha256 = 'C:\melange-data\member_longform_transcripts_v1.jsonl' }
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'C:\melange-data\member_leadership_prerole_identity_cues_v1.receipt.json' `
    -ArtifactPath 'C:\melange-data\member_leadership_prerole_identity_cues_v1.jsonl' `
    -AllowedStatuses @('PASS_CANDIDATE_SCREEN_ONLY') `
    -InputBindings @{ transcripts_sha256 = 'C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl' }
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.receipt.json' `
    -ArtifactPath 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl' `
    -AllowedStatuses @('PASS_CANDIDATE_SCREEN_ONLY') `
    -InputBindings @{ transcripts_sha256 = 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl' }

# A failed literal-name screen does not discard a recording.  Queue and
# acquire a third, distinct long-form source for those unresolved members.
$Retry2QueuePath = 'data\speech\member_longform_no_cue_retry2_queue_v1.jsonl'
$Retry2QueueReceiptPath = 'data\speech\member_longform_no_cue_retry2_queue_v1.receipt.json'
$Retry2AcquisitionPath = 'data\speech\member_longform_no_cue_retry2_acquisition_v1.jsonl'
$Retry2AcquisitionReceiptPath = 'data\speech\member_longform_no_cue_retry2_acquisition_v1.receipt.json'
$Retry2TranscriptsPath = 'C:\melange-data\member_longform_no_cue_retry2_transcripts_v1.jsonl'
$Retry2TranscriptsReceiptPath = 'C:\melange-data\member_longform_no_cue_retry2_transcripts_v1.receipt.json'
$Retry2CuesPath = 'C:\melange-data\member_longform_no_cue_retry2_identity_cues_v1.jsonl'
$Retry2CuesReceiptPath = 'C:\melange-data\member_longform_no_cue_retry2_identity_cues_v1.receipt.json'
$Retry3QueuePath = 'data\speech\member_longform_no_cue_retry3_queue_v1.jsonl'
$Retry3QueueReceiptPath = 'data\speech\member_longform_no_cue_retry3_queue_v1.receipt.json'
$Retry3AcquisitionPath = 'data\speech\member_longform_no_cue_retry3_acquisition_v1.jsonl'
$Retry3AcquisitionReceiptPath = 'data\speech\member_longform_no_cue_retry3_acquisition_v1.receipt.json'
$Retry3TranscriptsPath = 'C:\melange-data\member_longform_no_cue_retry3_transcripts_v1.jsonl'
$Retry3TranscriptsReceiptPath = 'C:\melange-data\member_longform_no_cue_retry3_transcripts_v1.receipt.json'
$Retry3CuesPath = 'C:\melange-data\member_longform_no_cue_retry3_identity_cues_v1.jsonl'
$Retry3CuesReceiptPath = 'C:\melange-data\member_longform_no_cue_retry3_identity_cues_v1.receipt.json'
$AbsentOptionalRoot = Join-Path `
    ([System.IO.Path]::GetTempPath()) `
    ('melange-profile-ready-absent-' + [guid]::NewGuid().ToString('N'))
$Retry2SelectionTranscripts = Join-Path $AbsentOptionalRoot 'retry2-transcripts.jsonl'
$Retry2SelectionAcquisition = Join-Path $AbsentOptionalRoot 'retry2-acquisition.jsonl'
$Retry2SelectionCues = Join-Path $AbsentOptionalRoot 'retry2-cues.jsonl'
$Retry3SelectionTranscripts = Join-Path $AbsentOptionalRoot 'retry3-transcripts.jsonl'
$Retry3SelectionAcquisition = Join-Path $AbsentOptionalRoot 'retry3-acquisition.jsonl'
$Retry3SelectionCues = Join-Path $AbsentOptionalRoot 'retry3-cues.jsonl'

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.build_member_longform_additional_retry_queue',
    '--out', $Retry2QueuePath, '--receipt', $Retry2QueueReceiptPath
)
Assert-CurrentArtifactReceipt `
    -ReceiptPath $Retry2QueueReceiptPath `
    -ArtifactPath $Retry2QueuePath `
    -AllowedStatuses @('PASS_RETRY_QUEUE_ONLY') `
    -InputBindings @{
        primary_cues_sha256 = 'C:\melange-data\member_longform_identity_cues_v1.jsonl'
        retry_cues_sha256 = 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl'
        primary_acquisition_sha256 = 'data\speech\member_longform_acquisition_v1.jsonl'
        retry_acquisition_sha256 = 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl'
    }

$Retry2QueueCount = Get-Utf8JsonlRowCount -Path $Retry2QueuePath
$Retry3QueueCount = 0
if ($Retry2QueueCount -gt 0) {
    Invoke-LoggedPython -Arguments @(
        '-m', 'fec.speech.acquire_member_longform_audio',
        '--queue', $Retry2QueuePath,
        '--out', $Retry2AcquisitionPath,
        '--receipt', $Retry2AcquisitionReceiptPath,
        '--media-root', 'C:\melange-data\member_longform_no_cue_retry2_audio_v1',
        '--workers', '1', '--resume', '--retry-errors', '--retry-provider-resolution'
    )
    Assert-CurrentArtifactReceipt `
        -ReceiptPath $Retry2AcquisitionReceiptPath `
        -ArtifactPath $Retry2AcquisitionPath `
        -AllowedStatuses @('COMPLETE') `
        -InputBindings @{ queue_sha256 = $Retry2QueuePath }

    Invoke-LoggedPython -Arguments @(
        '-m', 'fec.speech.transcribe_member_longform_captions',
        '--acquisition', $Retry2AcquisitionPath,
        '--out', $Retry2TranscriptsPath,
        '--receipt', $Retry2TranscriptsReceiptPath,
        '--transcript-root', 'C:\melange-data\member_longform_no_cue_retry2_transcripts_v1',
        '--resume', '--retry-errors', '--retry-local-asr', '--workers', '1', '--request-delay', '5'
    )

    Invoke-LoggedPython -Arguments @(
        '-m', 'fec.speech.transcribe_member_longform_local_asr',
        '--acquisition', $Retry2AcquisitionPath,
        '--transcripts', $Retry2TranscriptsPath,
        '--receipt', $Retry2TranscriptsReceiptPath,
        '--transcript-root', 'C:\melange-data\member_longform_no_cue_retry2_transcripts_v1',
        '--cpu-threads', '8', '--shortest-first', '--retry-errors'
    )
    Assert-CurrentArtifactReceipt `
        -ReceiptPath $Retry2TranscriptsReceiptPath `
        -ArtifactPath $Retry2TranscriptsPath `
        -AllowedStatuses @('COMPLETE_LOCALIZATION_ONLY') `
        -InputBindings @{ acquisition_sha256 = $Retry2AcquisitionPath }

    Invoke-LoggedPython -Arguments @(
        '-m', 'fec.speech.screen_member_longform_identity_cues',
        '--transcripts', $Retry2TranscriptsPath,
        '--output', $Retry2CuesPath,
        '--receipt', $Retry2CuesReceiptPath
    )
    Assert-CurrentArtifactReceipt `
        -ReceiptPath $Retry2CuesReceiptPath `
        -ArtifactPath $Retry2CuesPath `
        -AllowedStatuses @('PASS_CANDIDATE_SCREEN_ONLY') `
        -InputBindings @{ transcripts_sha256 = $Retry2TranscriptsPath }
    $Retry2SelectionTranscripts = $Retry2TranscriptsPath
    $Retry2SelectionAcquisition = $Retry2AcquisitionPath
    $Retry2SelectionCues = $Retry2CuesPath

    # A fourth source is considered only after the third source has completed
    # the same acquisition, transcription, and literal-name-cue gates.
    Invoke-LoggedPython -Arguments @(
        '-m', 'fec.speech.build_member_longform_additional_retry_queue',
        '--additional-cues', $Retry2CuesPath,
        '--additional-acquisition', $Retry2AcquisitionPath,
        '--out', $Retry3QueuePath, '--receipt', $Retry3QueueReceiptPath
    )
    Assert-CurrentArtifactReceipt `
        -ReceiptPath $Retry3QueueReceiptPath `
        -ArtifactPath $Retry3QueuePath `
        -AllowedStatuses @('PASS_RETRY_QUEUE_ONLY') `
        -InputBindings @{
            primary_cues_sha256 = 'C:\melange-data\member_longform_identity_cues_v1.jsonl'
            retry_cues_sha256 = 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl'
            primary_acquisition_sha256 = 'data\speech\member_longform_acquisition_v1.jsonl'
            retry_acquisition_sha256 = 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl'
        }
    Assert-CurrentAdditionalRetryInputs `
        -ReceiptPath $Retry3QueueReceiptPath `
        -CuesPath $Retry2CuesPath `
        -AcquisitionPath $Retry2AcquisitionPath

    $Retry3QueueCount = Get-Utf8JsonlRowCount -Path $Retry3QueuePath
    if ($Retry3QueueCount -gt 0) {
        Invoke-LoggedPython -Arguments @(
            '-m', 'fec.speech.acquire_member_longform_audio',
            '--queue', $Retry3QueuePath,
            '--out', $Retry3AcquisitionPath,
            '--receipt', $Retry3AcquisitionReceiptPath,
            '--media-root', 'C:\melange-data\member_longform_no_cue_retry3_audio_v1',
            '--workers', '1', '--resume', '--retry-errors', '--retry-provider-resolution'
        )
        Assert-CurrentArtifactReceipt `
            -ReceiptPath $Retry3AcquisitionReceiptPath `
            -ArtifactPath $Retry3AcquisitionPath `
            -AllowedStatuses @('COMPLETE') `
            -InputBindings @{ queue_sha256 = $Retry3QueuePath }

        Invoke-LoggedPython -Arguments @(
            '-m', 'fec.speech.transcribe_member_longform_captions',
            '--acquisition', $Retry3AcquisitionPath,
            '--out', $Retry3TranscriptsPath,
            '--receipt', $Retry3TranscriptsReceiptPath,
            '--transcript-root', 'C:\melange-data\member_longform_no_cue_retry3_transcripts_v1',
            '--resume', '--retry-errors', '--retry-local-asr', '--workers', '1', '--request-delay', '5'
        )

        Invoke-LoggedPython -Arguments @(
            '-m', 'fec.speech.transcribe_member_longform_local_asr',
            '--acquisition', $Retry3AcquisitionPath,
            '--transcripts', $Retry3TranscriptsPath,
            '--receipt', $Retry3TranscriptsReceiptPath,
            '--transcript-root', 'C:\melange-data\member_longform_no_cue_retry3_transcripts_v1',
            '--cpu-threads', '8', '--shortest-first', '--retry-errors'
        )
        Assert-CurrentArtifactReceipt `
            -ReceiptPath $Retry3TranscriptsReceiptPath `
            -ArtifactPath $Retry3TranscriptsPath `
            -AllowedStatuses @('COMPLETE_LOCALIZATION_ONLY') `
            -InputBindings @{ acquisition_sha256 = $Retry3AcquisitionPath }

        Invoke-LoggedPython -Arguments @(
            '-m', 'fec.speech.screen_member_longform_identity_cues',
            '--transcripts', $Retry3TranscriptsPath,
            '--output', $Retry3CuesPath,
            '--receipt', $Retry3CuesReceiptPath
        )
        Assert-CurrentArtifactReceipt `
            -ReceiptPath $Retry3CuesReceiptPath `
            -ArtifactPath $Retry3CuesPath `
            -AllowedStatuses @('PASS_CANDIDATE_SCREEN_ONLY') `
            -InputBindings @{ transcripts_sha256 = $Retry3TranscriptsPath }
        $Retry3SelectionTranscripts = $Retry3TranscriptsPath
        $Retry3SelectionAcquisition = $Retry3AcquisitionPath
        $Retry3SelectionCues = $Retry3CuesPath
    }
}

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.select_member_longform_profile_sources',
    '--retry2-transcripts', $Retry2SelectionTranscripts,
    '--retry2-acquisition', $Retry2SelectionAcquisition,
    '--retry2-cues', $Retry2SelectionCues,
    '--retry3-transcripts', $Retry3SelectionTranscripts,
    '--retry3-acquisition', $Retry3SelectionAcquisition,
    '--retry3-cues', $Retry3SelectionCues
)

$SelectionInputBindings = @{
    roster_sha256 = 'data\members\profile_basis_v2.json'
    roles_sha256 = 'data\speech\member_leadership_role_cutoffs_v1.json'
    leader_sources_sha256 = 'data\speech\member_leadership_prerole_reviewed_sources_v1.jsonl'
    primary_transcripts_sha256 = 'C:\melange-data\member_longform_transcripts_v1.jsonl'
    primary_acquisition_sha256 = 'data\speech\member_longform_acquisition_v1.jsonl'
    primary_cues_sha256 = 'C:\melange-data\member_longform_identity_cues_v1.jsonl'
    retry_transcripts_sha256 = 'C:\melange-data\member_longform_no_cue_retry_transcripts_v1.jsonl'
    retry_acquisition_sha256 = 'data\speech\member_longform_no_cue_retry_acquisition_v1.jsonl'
    retry_cues_sha256 = 'C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl'
    leader_transcripts_sha256 = 'C:\melange-data\member_leadership_prerole_transcripts_v1.jsonl'
    leader_acquisition_sha256 = 'data\speech\member_leadership_prerole_acquisition_v1.jsonl'
    leader_cues_sha256 = 'C:\melange-data\member_leadership_prerole_identity_cues_v1.jsonl'
}
if ($Retry2QueueCount -gt 0) {
    $SelectionInputBindings.retry2_transcripts_sha256 = $Retry2TranscriptsPath
    $SelectionInputBindings.retry2_acquisition_sha256 = $Retry2AcquisitionPath
    $SelectionInputBindings.retry2_cues_sha256 = $Retry2CuesPath
}
if ($Retry3QueueCount -gt 0) {
    $SelectionInputBindings.retry3_transcripts_sha256 = $Retry3TranscriptsPath
    $SelectionInputBindings.retry3_acquisition_sha256 = $Retry3AcquisitionPath
    $SelectionInputBindings.retry3_cues_sha256 = $Retry3CuesPath
}
Assert-CurrentArtifactReceipt `
    -ReceiptPath 'data\speech\member_longform_profile_source_selection_v1.receipt.json' `
    -ArtifactPath 'data\speech\member_longform_profile_source_selection_v1.jsonl' `
    -AllowedStatuses @('PASS_SELECTION_ONLY') `
    -InputBindings $SelectionInputBindings

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.classify_member_longform_full_turns_local',
    '--input', 'data\speech\member_longform_profile_source_selection_v1.jsonl',
    '--output', 'C:\melange-data\member_longform_full_turn_candidates_local_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_full_turn_candidates_local_v1.receipt.json',
    '--cpu-threads', '8', '--segments-per-member', '1',
    '--context-seconds', '1200', '--max-new-tokens', '600'
)

# The wider pass uses two cue regions and more output capacity. A changed
# inference configuration invalidates cached classifications; only a resume
# with the same evidence and configuration can reuse successful members.
Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.classify_member_longform_full_turns_local',
    '--input', 'data\speech\member_longform_profile_source_selection_v1.jsonl',
    '--output', 'C:\melange-data\member_longform_full_turn_candidates_local_v1.jsonl',
    '--receipt', 'C:\melange-data\member_longform_full_turn_candidates_local_v1.receipt.json',
    '--cpu-threads', '8', '--segments-per-member', '2',
    '--context-seconds', '1800', '--max-new-tokens', '900', '--retry-screened-out'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.verify_member_longform_speaker_continuity',
    '--input', 'C:\melange-data\member_longform_full_turn_candidates_local_v1.jsonl',
    '--output', 'data\speech\member_longform_speaker_continuity_v1.jsonl',
    '--receipt', 'data\speech\member_longform_speaker_continuity_v1.receipt.json'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.build_member_longform_review_queue'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.draft_member_longform_reviews'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.build_member_longform_review_app'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'pytest',
    'fec/tests/speech/test_continue_member_longform_profile_ready_script.py',
    'fec/tests/speech/test_select_member_longform_profile_sources.py',
    'fec/tests/speech/test_build_member_longform_additional_retry_queue.py',
    'fec/tests/speech/test_build_member_longform_review_queue.py',
    'fec/tests/speech/test_materialize_member_longform_reviewed_turns.py',
    'fec/tests/speech/test_draft_member_longform_reviews.py',
    'fec/tests/speech/test_build_member_longform_review_app.py',
    'fec/tests/speech/test_verify_member_longform_speaker_continuity.py',
    'fec/tests/members/test_leadership_audio_source_era.py',
    '-q'
)

('[' + (Get-Date -Format o) + '] candidate and review queue complete; human review remains mandatory') |
    Tee-Object -FilePath $Log -Append
