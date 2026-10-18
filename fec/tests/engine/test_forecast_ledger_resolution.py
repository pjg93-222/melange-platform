"""Prospective cosponsor scoring must not be consumed by routine diagnostics."""
import csv
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from engine import forecast_ledger as FL


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(FL, "_utc_now", lambda: "2026-08-31T12:00:00+00:00")
    membership = tmp_path / "interactions" / "interaction_bill_members.csv"
    membership.parent.mkdir()
    fields = ["bill_key", "bioguide_id", "sponsorship_date"]

    def write(rows, columns=fields):
        with membership.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    write([])
    freeze = {"schema": FL.FREEZE_SCHEMA, "forecaster": "cosponsor_join", "vintage": "2026Q2",
              "frozen_utc": "2026-08-06T12:00:00+00:00",
              "inputs": {"resolution_memberships_at_freeze": {"path": str(membership), "sha256": FL._sha256(membership)}},
              "predictions": [{"congress": 119, "bill": "119:hr:1", "member": name, "predicted_probability": 0.2}
                              for name in ("PRE", "SAME", "POST")]}
    freeze_path = tmp_path / "forecast_ledger" / "freezes" / "cosponsor_join" / "2026Q2.json"
    freeze_path.parent.mkdir(parents=True)
    freeze_path.write_text(json.dumps(freeze), encoding="utf-8")
    return tmp_path, membership, freeze, freeze_path, write


def row(member, joined):
    return {"bill_key": "119:hr:1", "bioguide_id": member, "sponsorship_date": joined}


def test_only_post_freeze_days_count_and_receipt_records_the_explicit_earliest_execution_date(fixture):
    root, _, freeze, path, write = fixture
    write([row("PRE", "2026-08-01"), row("SAME", "2026-08-06"), row("POST", "2026-08-10")])
    outcomes, resolution, reason = FL._resolve_cosponsor(freeze, str(path), str(root), "2026-08-15")
    assert outcomes == [0, 0, 1]
    assert reason is None
    assert resolution["resolution_after"] == "2026-08-15"
    assert resolution["membership_date_rule"] == "strictly_after_freeze_utc_date"


def test_default_diagnostic_defers_even_byte_only_membership_changes(fixture):
    root, membership, _, path, _ = fixture
    frozen_bytes = path.read_bytes()
    membership.write_bytes(membership.read_bytes() + b"\n")
    scored, notes = FL._score_unscored_freezes(str(root))
    assert scored == []
    assert any("no explicit resolution date" in note for note in notes)
    assert not (root / "forecast_ledger" / "ledger.jsonl").exists()
    assert path.read_bytes() == frozen_bytes


def test_future_threshold_defers_and_reached_threshold_scores_once(fixture):
    root, _, _, path, write = fixture
    write([row("POST", "2026-08-10")])
    frozen_bytes = path.read_bytes()
    scored, notes = FL._score_unscored_freezes(str(root), "2026-09-01")
    assert scored == [] and any("deferred until 2026-09-01" in note for note in notes)
    scored, _ = FL._score_unscored_freezes(str(root), "2026-08-31")
    assert len(scored) == 1 and scored[0]["resolved_true"] == 1
    assert scored[0]["resolution_source"]["resolution_after"] == "2026-08-31"
    ledger_path = root / "forecast_ledger" / "ledger.jsonl"
    existing = ledger_path.read_bytes()
    write([row("POST", "")])
    assert FL._score_unscored_freezes(str(root), "2026-09-01")[0] == []
    assert ledger_path.read_bytes() == existing
    assert path.read_bytes() == frozen_bytes


@pytest.mark.parametrize("joined", ["", None, "20260810", "2026-W33-1", "2026-02-30", "2026-8-10"])
def test_missing_or_malformed_sponsorship_dates_fail_closed(fixture, joined):
    root, _, _, _, write = fixture
    write([row("POST", joined)])
    with pytest.raises(FL.ForecastLedgerError, match="sponsorship_date"):
        FL._score_unscored_freezes(str(root), "2026-08-15")
    assert not (root / "forecast_ledger" / "ledger.jsonl").exists()


def test_missing_sponsorship_date_column_fails_closed(fixture):
    root, _, _, _, write = fixture
    write([{"bill_key": "119:hr:1", "bioguide_id": "POST"}], ["bill_key", "bioguide_id"])
    with pytest.raises(FL.ForecastLedgerError, match="missing columns: sponsorship_date"):
        FL._score_unscored_freezes(str(root), "2026-08-15")


@pytest.mark.parametrize("threshold", ["20260815", "2026-02-30"])
def test_malformed_earliest_execution_date_is_refused(fixture, threshold):
    root, _, _, _, write = fixture
    write([row("POST", "2026-08-10")])
    with pytest.raises(FL.ForecastLedgerError):
        FL._score_unscored_freezes(str(root), threshold)


@pytest.mark.parametrize("threshold", ["2026-08-01", "2026-08-06"])
def test_a_date_on_or_before_the_only_freeze_skips_it_with_a_note(fixture, threshold):
    root, _, _, path, write = fixture
    write([row("POST", "2026-08-10")])
    frozen_bytes = path.read_bytes()
    scored, notes = FL._score_unscored_freezes(str(root), threshold)
    assert scored == []
    assert any("2026Q2" in note and "non-prospective" in note for note in notes)
    assert not (root / "forecast_ledger" / "ledger.jsonl").exists()
    assert path.read_bytes() == frozen_bytes


def test_a_non_prospective_freeze_is_skipped_while_the_other_freezes_and_dyads_score(fixture):
    root, _, _, path, write = fixture          # 2026Q2 is frozen 2026-08-06, before the date below
    write([row("POST", "2026-08-10")])
    late = json.loads(path.read_text(encoding="utf-8"))
    late.update(vintage="2026Q3", frozen_utc="2026-08-20T12:00:00+00:00")   # frozen after the date
    late_path = path.with_name("2026Q3.json")
    late_path.write_text(json.dumps(late), encoding="utf-8")
    late_bytes = late_path.read_bytes()
    for year in (2026, 2027):
        (root / f"our_network_fullscope_{year}_strict.csv").write_text(
            f"lob_id,bioguide_id,year,n_bills\norg,POST,{year},1\n", encoding="utf-8")
    dyad = root / "forecast_ledger" / "freezes" / "dyad_persistence" / "2026Q2.json"
    dyad.parent.mkdir()
    dyad.write_text(json.dumps({"schema": FL.FREEZE_SCHEMA, "forecaster": "dyad_persistence", "vintage": "2026Q2",
                                "frozen_utc": "2026-08-06T12:00:00+00:00", "inputs": {}, "source_year": 2025,
                                "target_year": 2026,
                                "predictions": [{"lob_id": "org", "bioguide_id": "POST", "predicted_probability": 0.82}]}),
                    encoding="utf-8")
    scored, notes = FL._score_unscored_freezes(str(root), "2026-08-15")
    assert sorted((r["forecaster"], r["vintage"]) for r in scored) == [
        ("cosponsor_join", "2026Q2"), ("dyad_persistence", "2026Q2")]
    assert [n for n in notes if "non-prospective" in n] == [
        "cosponsor_join 2026Q3: cosponsor freeze skipped: earliest execution date 2026-08-15 "
        "is not after its freeze date 2026-08-20 (non-prospective)"]
    ledger = (root / "forecast_ledger" / "ledger.jsonl").read_text(encoding="utf-8")
    assert '"2026Q3"' not in ledger
    assert late_path.read_bytes() == late_bytes


def test_default_does_not_disable_other_forecasters(fixture):
    root, _, _, _, _ = fixture
    for year in (2026, 2027):
        (root / f"our_network_fullscope_{year}_strict.csv").write_text(
            f"lob_id,bioguide_id,year,n_bills\norg,POST,{year},1\n", encoding="utf-8")
    path = root / "forecast_ledger" / "freezes" / "dyad_persistence" / "2026Q2.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"schema": FL.FREEZE_SCHEMA, "forecaster": "dyad_persistence", "vintage": "2026Q2",
                               "frozen_utc": "2026-08-06T12:00:00+00:00", "inputs": {}, "source_year": 2025, "target_year": 2026,
                               "predictions": [{"lob_id": "org", "bioguide_id": "POST", "predicted_probability": 0.82}]}), encoding="utf-8")
    scored, _ = FL._score_unscored_freezes(str(root))
    assert [record["forecaster"] for record in scored] == ["dyad_persistence"]


def test_cli_passes_only_an_explicit_earliest_execution_date(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(FL, "run", lambda root, threshold=None: (calls.append((root, threshold)) or (0, [], [])))
    FL.main(["--data-root", str(tmp_path)])
    FL.main(["--data-root", str(tmp_path), "--cosponsor-resolution-after", "2026-08-15"])
    assert calls == [(str(tmp_path), None), (str(tmp_path), "2026-08-15")]
