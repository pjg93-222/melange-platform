from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/continue_member_longform_profile_ready.ps1"


def script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_powershell_script_parses_without_execution():
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    if not powershell:
        pytest.skip("PowerShell parser is unavailable")
    escaped = str(SCRIPT).replace("'", "''")
    command = (
        f"$path = '{escaped}'; $tokens = $null; $errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        "$path, [ref]$tokens, [ref]$errors) | Out-Null; "
        "if (@($errors).Count) { $errors | ForEach-Object { $_.ToString() }; exit 1 }"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_retry3_runs_after_retry2_and_reaches_selector():
    text = script_text()
    assert "'--additional-cues', $Retry2CuesPath" in text
    assert "'--additional-acquisition', $Retry2AcquisitionPath" in text
    assert "member_longform_no_cue_retry3_queue_v1.jsonl" in text
    assert "member_longform_no_cue_retry3_acquisition_v1.jsonl" in text
    assert "member_longform_no_cue_retry3_transcripts_v1.jsonl" in text
    assert "member_longform_no_cue_retry3_identity_cues_v1.jsonl" in text
    assert "'--retry3-transcripts', $Retry3SelectionTranscripts" in text
    assert "'--retry3-acquisition', $Retry3SelectionAcquisition" in text
    assert "'--retry3-cues', $Retry3SelectionCues" in text
    assert text.index("$Retry2QueueCount = Get-Utf8JsonlRowCount") < text.index(
        "$Retry3QueueCount = Get-Utf8JsonlRowCount"
    )


def test_utf8_handling_does_not_embed_a_non_ascii_member_argument():
    text = script_text()
    assert "$env:PYTHONUTF8 = '1'" in text
    assert "$env:PYTHONIOENCODING = 'utf-8'" in text
    assert "'--member'" not in text
    assert "Pablo Hern" not in text


def test_current_selection_receipt_gates_downstream_and_human_review_remains():
    text = script_text()
    selection_receipt = (
        "-ReceiptPath 'data\\speech\\member_longform_profile_source_selection_v1.receipt.json'"
    )
    classifier = "'fec.speech.classify_member_longform_full_turns_local'"
    assert selection_receipt in text
    assert text.index(selection_receipt) < text.index(classifier)
    assert "-AllowedStatuses @('PASS_SELECTION_ONLY')" in text
    assert "-InputBindings $SelectionInputBindings" in text
    assert "'fec.speech.materialize_member_longform_reviewed_turns'" not in text
    assert "candidate and review queue complete; human review remains mandatory" in text
