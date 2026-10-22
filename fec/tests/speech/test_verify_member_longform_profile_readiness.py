import hashlib
import json
from pathlib import Path

import pytest

from fec.speech import verify_member_longform_profile_readiness as mod
from fec.tests.speech import test_materialize_member_longform_reviewed_turns as materializer_fixtures


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def run_fixture(tmp_path: Path, monkeypatch, *, leader_era="PRE_CURRENT_LEADERSHIP_ROLE"):
    # Actual materializer contract with explicitly synthetic test reviews only.
    paths = materializer_fixtures.fixture_files(tmp_path)
    selected = json.loads(paths[0].read_text(encoding="utf-8"))
    selected.update(current_role="Chair", source_era="PRE_CURRENT_LEADERSHIP_ROLE",
                    recorded_at="2019-01-01", current_role_assumed_on="2020-01-01")
    write_jsonl(paths[0], [selected])
    review = json.loads(paths[3].read_text(encoding="utf-8"))
    review["semantic_findings"] = [{"kind": "response_length", "finding": "One complete response lasted 44 seconds.",
                                    "scope": "One reviewed public interview turn."}]
    write_jsonl(paths[3], [review])
    assert materializer_fixtures.run(paths, monkeypatch) == 0
    roster, roles, sources, out = (tmp_path / name for name in ("roster.json", "roles.json", "leaders.jsonl", "readiness.json"))
    write_json(roster, {"members": {"Example Member": {}, "Gap Leader": {}}})
    write_json(roles, {"members": {
        "Example Member": {"current_role": "Chair", "current_role_assumed_on": "2020-01-01"},
        "Gap Leader": {"current_role": "Whip", "current_role_assumed_on": "2005-01-03"},
    }})
    write_jsonl(sources, [
        {"member": "Example Member", "status": "REVIEWED_SOURCE", "recorded_at": "2019-01-01"},
        {"member": "Gap Leader", "status": "HISTORICAL_LONGFORM_SOURCE_GAP", "gap_reason": "restricted archive"},
    ])
    if leader_era != "PRE_CURRENT_LEADERSHIP_ROLE":
        alter_turn(paths, source_era=leader_era)
    monkeypatch.setattr("sys.argv", ["prog", "--roster", str(roster), "--roles", str(roles),
                                    "--leader-sources", str(sources), "--turns", str(paths[4]),
                                    "--turn-receipt", str(paths[5]), "--out", str(out)])
    return out, paths


def alter_turn(paths, **updates):
    row = json.loads(paths[4].read_text(encoding="utf-8"))
    row.update(updates)
    row["row_sha256"] = mod.materializer.row_hash({key: value for key, value in row.items() if key != "row_sha256"})
    write_jsonl(paths[4], [row])
    receipt = json.loads(paths[5].read_text(encoding="utf-8"))
    receipt["artifact_sha256"] = sha(paths[4])
    write_json(paths[5], receipt)


def test_readiness_does_not_count_documented_leadership_gap_as_verified(tmp_path, monkeypatch):
    out, _ = run_fixture(tmp_path, monkeypatch)
    with pytest.raises(SystemExit, match="documented source gaps remain missing"):
        mod.main()
    receipt = json.loads(out.read_text(encoding="utf-8"))
    assert receipt["status"] == "FAIL"
    assert receipt["counts"]["covered_or_documented_gap"] == 2
    assert receipt["counts"]["verified_member_coverage"] == 1
    assert receipt["missing_members"] == ["Gap Leader"]
    assert receipt["contract"]["meeting_advice_is_not_required"] is True


def test_readiness_rejects_post_role_baseline_for_current_leader(tmp_path, monkeypatch):
    out, _ = run_fixture(tmp_path, monkeypatch, leader_era="CURRENT_ROLE_CONDITIONED")
    with pytest.raises(SystemExit, match="profile-readiness gate failed"):
        mod.main()
    assert json.loads(out.read_text())["counts"]["invalid_rows"] == 1


@pytest.mark.parametrize("updates, expected", [
    ({"recorded_at": "2021-01-01"}, "leader_recording_date_not_pre_current_role"),
    ({"recorded_at": "not-a-date"}, "leader_recording_date_not_pre_current_role"),
    ({"current_role_assumed_on": "2030-01-01"}, "leader_recording_date_not_pre_current_role"),
    ({"human_reviewer": ""}, "named_human_review_binding"),
    ({"candidate_turn_binding_sha256": "wrong"}, "whole_turn_acoustic_coverage"),
    ({"turn_audio_sha256": "wrong"}, "physical_turn_audio_hash"),
    ({"duration_seconds": float("nan")}, "duration_span"),
    ({"semantic_findings": [{"kind": "descriptive", "finding": "An observation", "scope": "One turn", "meeting_phase": "bad"}]}, "meeting_phase"),
])
def test_readiness_checks_actual_dates_physical_audio_review_and_coverage(tmp_path, monkeypatch, updates, expected):
    out, paths = run_fixture(tmp_path, monkeypatch)
    alter_turn(paths, **updates)
    with pytest.raises(SystemExit, match="profile-readiness gate failed"):
        mod.main()
    assert expected in " ".join(json.loads(out.read_text())["errors"])


@pytest.mark.parametrize("case, expected", [("timezone", "human_review_timestamp"), ("early_windows", "whole_turn_acoustic_coverage"),
                                            ("legacy_coverage", "whole_turn_acoustic_coverage")])
def test_readiness_does_not_trust_hashes_without_rechecking_review_and_coverage(tmp_path, monkeypatch, case, expected):
    out, paths = run_fixture(tmp_path, monkeypatch)
    changed_path = paths[3] if case == "timezone" else paths[2]
    item = json.loads(changed_path.read_text())
    if case == "timezone":
        item["reviewed_at"] = "2026-09-04T20:00:00"
    elif case == "early_windows":
        item["evaluated_windows"] = item["evaluated_windows"][:3]
    else:
        item.pop("coverage_contract_version")
    write_jsonl(changed_path, [item])
    receipt = json.loads(paths[5].read_text())
    receipt["inputs"]["reviews_sha256" if case == "timezone" else "continuity_sha256"] = sha(changed_path)
    write_json(paths[5], receipt)
    with pytest.raises(SystemExit, match="profile-readiness gate failed"):
        mod.main()
    assert expected in " ".join(json.loads(out.read_text())["errors"])


def test_readiness_rejects_changed_receipt_input(tmp_path, monkeypatch):
    out, paths = run_fixture(tmp_path, monkeypatch)
    paths[3].write_text("", encoding="utf-8")
    with pytest.raises(SystemExit, match="profile-readiness gate failed"):
        mod.main()
    assert "input hash mismatch" in " ".join(json.loads(out.read_text())["errors"])


def test_automated_missing_transcript_is_row_error_not_crash(monkeypatch):
    def boom(*args):
        raise mod.SMR.RecoveryError("missing transcript")
    monkeypatch.setattr(mod.auto, "check_metadata", lambda review: None)
    monkeypatch.setattr(mod.materializer, "matching_turn", lambda candidate, row: (0, {}))
    monkeypatch.setattr(mod.auto, "verify_boundaries", boom)
    inputs = {"reviews": {"t1": {"reviewer_type": "AUTOMATED", "reviewer_name": "x", "reviewed_at": "z"}},
              "candidates": {}, "continuity": {}, "selection": {}}
    errors = mod.exact_review_errors({"turn_id": "t1", "member": "m", "source_id": "s"}, inputs)
    assert "whole_turn_acoustic_coverage" in errors
