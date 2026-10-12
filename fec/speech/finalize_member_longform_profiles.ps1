param(
    [string]$Reviews = 'data\speech\member_longform_human_reviews_v1.jsonl'
)

$ErrorActionPreference = 'Stop'
$Workspace = 'C:\Users\pgoff\OneDrive\Desktop\Melange Systems'
$LogRoot = 'C:\melange-data\asr_backfill_logs'
$Log = Join-Path $LogRoot 'profile-finalize-20260903.log'
Set-Location -LiteralPath $Workspace
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

if (-not (Test-Path -LiteralPath $Reviews)) {
    throw "Human-review JSONL is missing: $Reviews"
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

('[' + (Get-Date -Format o) + '] starting fail-closed long-form profile finalization') |
    Tee-Object -FilePath $Log -Append

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.materialize_member_longform_reviewed_turns',
    '--reviews', $Reviews
)

# This gate requires every active member to have a reviewed semantic turn,
# except a current leader with an explicit documented historical-source gap.
Invoke-LoggedPython -Arguments @(
    '-m', 'fec.speech.verify_member_longform_profile_readiness'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.members.build_member_narrative_ledger'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.members.build_member_meeting_playbooks'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'fec.surfaces.make_member_desk'
)

Invoke-LoggedPython -Arguments @(
    '-m', 'pytest',
    'fec/tests/speech/test_materialize_member_longform_reviewed_turns.py',
    'fec/tests/speech/test_verify_member_longform_profile_readiness.py',
    'fec/tests/members/test_leadership_audio_source_era.py',
    'fec/tests/members/test_member_narratives_v2_repair.py',
    '-q'
)

$Integrity = Get-Content -LiteralPath 'data\engine\dashboard_integrity_report.json' -Raw |
    ConvertFrom-Json
if ($Integrity.status -ne 'pass' -or @($Integrity.errors).Count -ne 0) {
    throw 'Rendered member desk failed dashboard integrity verification.'
}

('[' + (Get-Date -Format o) + '] PASS: reviewed evidence, profiles, playbooks, and rendered desk are current') |
    Tee-Object -FilePath $Log -Append
