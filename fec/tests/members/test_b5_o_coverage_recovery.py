# -*- coding: utf-8 -*-
"""RED contracts for the locked O-only coverage-recovery successor."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))

import members.b5_o_coverage_recovery as recovery  # noqa: E402
import members.b5_o_coverage_look as recovery_look  # noqa: E402


OLD_CRITERION_SHA = "eb16cde3c160f7fc14280a3c6b0ae4754a913606fcd2e78fa7a4fd1c8dea8e12"
OLD_RESULT_SHA = "fc3bdbfa59671f61891c34d38e13839f57092c2110d760a12e221e7fc43ba5e9"
ORIGINAL_SCORE_SHA = "2b351af4c8fd077cc7fd10b05001a2843f850a3a83f956d5ac75b644a4317c13"
ORIGINAL_GOLD_SHA = "ff5a948361f478cf7d7da2ce84e333594b242cb65574760dd285a8dbd57afe7a"
RECOVERY_CRITERION_SHA = "879f21fafc064eeacdb778c5d021897ce6227c29734edb2c686ad056b751c5bd"
RECOVERY_RESULT_SHA = "fd0b244fc9639f3d76f5cfc791632331a815544b4e889de1ccc7676c45d2c77f"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def test_stopped_artifacts_and_originals_are_immutable():
    base = ROOT / "originals" / "big5_onc_20260716"
    assert sha256(ROOT / "data" / "members" / "big5_onc_new_criteria.json.candidate") == OLD_CRITERION_SHA
    assert sha256(ROOT / "data" / "members" / "big5_onc_crosspanel_successor.json.candidate") == OLD_RESULT_SHA
    assert sha256(base / "big5_gold_anchored.original.json") == ORIGINAL_SCORE_SHA
    assert sha256(base / "big5_senate_expert_gold.original.json") == ORIGINAL_GOLD_SHA


def test_recovery_floors_are_exact_and_still_require_domain_breadth():
    assert recovery.O_MIN_PACKAGES == 5
    assert recovery.O_MIN_TOKENS == 1000
    assert recovery.O_MIN_DOMAIN_HITS == 25
    assert recovery.O_MIN_ACTIVE_DOMAINS == 4
    good = {"package_n": 5, "token_n": 1000, "domain_hits": 25, "active_domains": 4}
    assert recovery.source_eligible(good)
    for key in good:
        bad = dict(good)
        bad[key] -= 1
        assert not recovery.source_eligible(bad), key


def test_evidence_factor_is_fixed_monotone_and_bounded():
    assert recovery.evidence_factor(10, 2000, 50) == 1.0
    expected = math.sqrt(.5 * .5 * .5)
    assert abs(recovery.evidence_factor(5, 1000, 25) - expected) < 1e-12
    assert 0 < recovery.evidence_factor(6, 1200, 30) < 1
    assert recovery.evidence_factor(6, 1200, 30) > expected
    assert recovery.evidence_factor(100, 200000, 5000) == 1.0


def test_both_sources_are_required_not_zero_imputed():
    left = {"A": 1.0, "B": 2.0}
    right = {"B": 3.0, "C": 4.0}
    assert recovery.paired_names(left, right) == ["B"]


def test_readiness_ladder_is_predeclared_and_deterministic():
    primary = recovery.classify_readiness(
        {"total": 250, "House": 150, "Senate": 30},
        {"source_r": .10, "spearman_brown": .18, "added_source_r": .05,
         "added_n": 22, "core_r": .90, "core_n": 190})
    assert primary["ready_for_look"] and primary["path"] == "PRIMARY_250"
    guarded = recovery.classify_readiness(
        {"total": 220, "House": 135, "Senate": 30},
        {"source_r": .15, "spearman_brown": .25, "added_source_r": .10,
         "added_n": 22, "core_r": .95, "core_n": 190})
    assert guarded["ready_for_look"] and guarded["path"] == "GUARDED_220"
    weak = recovery.classify_readiness(
        {"total": 249, "House": 150, "Senate": 30},
        {"source_r": .149, "spearman_brown": .25, "added_source_r": .10,
         "added_n": 22, "core_r": .95, "core_n": 190})
    assert not weak["ready_for_look"] and weak["path"] == "STOP"
    too_low = recovery.classify_readiness(
        {"total": 219, "House": 180, "Senate": 39},
        {"source_r": .50, "spearman_brown": .66, "added_source_r": .40,
         "added_n": 50, "core_r": .99, "core_n": 198})
    assert not too_low["ready_for_look"]


def test_builder_is_panel_blind_and_candidate_only():
    source = (FEC / "members" / "b5_o_coverage_recovery.py").read_text(encoding="utf-8")
    assert "big5_senate_expert_gold" not in source
    assert "big5_gold_anchored" not in source
    assert recovery.OUTPUT_BASENAME == "big5_o_coverage_recovery.json.candidate"
    assert "big5_gold_anchored.json\"" not in source


def test_recorded_criterion_has_only_o_and_reports_recovered_stratum():
    path = ROOT / "data" / "members" / recovery.OUTPUT_BASENAME
    assert path.is_file()
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert doc["schema"] == "b5-o-coverage-recovery-v1"
    assert doc["production_consumers"] == 0
    assert doc["stopped_criterion_sha256"] == OLD_CRITERION_SHA
    assert set(next(iter(doc["members"].values()))) == {"O"}
    assert doc["reliability"]["added_n"] >= 22
    assert doc["readiness"]["path"] in {"PRIMARY_250", "GUARDED_220", "STOP"}


def _fake_original():
    return {"traits": {"O": {"members": {
        "A": {"O": -1.0}, "B": {"O": 0.0}, "C": {"O": 1.0},
    }}}}


def test_candidate_is_fixed_70_30_intersection_and_does_not_mutate_inputs():
    original = _fake_original()
    criterion = {"members": {"A": {"O": -2.0}, "C": {"O": 2.0}, "D": {"O": 3.0}}}
    before = json.dumps([original, criterion], sort_keys=True)
    built = recovery_look.build_candidate_score(original, criterion)
    assert json.dumps([original, criterion], sort_keys=True) == before
    assert built["weights"] == {"original": .70, "new": .30}
    assert set(built["score"]) == {"A", "C"}
    assert set(built["original_same_roster"]) == {"A", "C"}


def test_look_gate_requires_exact_freeze_and_refuses_reuse():
    base = "**STATUS: LOCKED 2026-07-16.**\n"
    try:
        recovery_look.validate_prereg_gate(base, "a" * 64)
    except recovery_look.LookRefused as exc:
        assert "FREEZE" in str(exc).upper()
    else:
        raise AssertionError("look ran before freeze")
    frozen = base + "\n## CRITERIA FREEZE ADDENDUM\ncriteria_sha256: `%s`\n" % ("a" * 64)
    recovery_look.validate_prereg_gate(frozen, "a" * 64)
    try:
        recovery_look.validate_prereg_gate(frozen, "b" * 64)
    except recovery_look.LookRefused as exc:
        assert "differs" in str(exc)
    else:
        raise AssertionError("mismatched freeze hash was accepted")
    try:
        recovery_look.validate_prereg_gate(frozen + "\n## RESULT-O-RECOVERY\n", "a" * 64)
    except recovery_look.LookRefused as exc:
        assert "already" in str(exc).lower()
    else:
        raise AssertionError("a second look was allowed")


def test_c_result_and_output_boundary_remain_unchanged():
    assert sha256(ROOT / "data" / "members" / "big5_onc_crosspanel_successor.json.candidate") == OLD_RESULT_SHA
    source = (FEC / "members" / "b5_o_coverage_look.py").read_text(encoding="utf-8")
    assert recovery_look.OUTPUT_BASENAME == "big5_o_coverage_result.json.candidate"
    assert "big5_gold_anchored.json\"" not in source
    assert "dump_atomic(doc, OUT" not in source


def test_recorded_result_is_terminal_miss_and_preserves_o_status():
    result_path = ROOT / recovery_look.OUTPUT_BASENAME
    assert sha256(result_path) == RECOVERY_RESULT_SHA
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["schema"] == "b5-o-coverage-result-v1"
    assert result["status"] == "DUAL_PANEL_SUCCESSOR_MISS"
    assert result["pass"] is False and result["production_consumers"] == 0
    assert result["criteria_sha256"] == RECOVERY_CRITERION_SHA
    assert result["candidate"]["coverage_n"] == 279
    assert result["candidate"]["panels"]["academic_experts"]["bars"] == {
        "dominance": False, "n": True, "p": True, "partial": True, "r": True,
    }
    assert result["candidate"]["panels"]["senate_insiders"]["pass"] is False
    original = json.loads((ROOT / "originals" / "big5_onc_20260716" /
                           "big5_gold_anchored.original.json").read_text(encoding="utf-8"))
    assert original["traits"]["O"]["status"] == "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED"
    prereg = (ROOT / "big5_o_coverage_recovery_prereg.md").read_text(encoding="utf-8")
    assert "\n## RESULT-O-RECOVERY" in prereg


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print("PASS %s" % fn.__name__)
        except BaseException:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
    print("test_b5_o_coverage_recovery: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
