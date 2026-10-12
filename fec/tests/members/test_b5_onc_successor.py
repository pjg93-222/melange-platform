# -*- coding: utf-8 -*-
"""TDD contracts for the locked O/N/C new-criteria cross-panel successor."""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
import traceback
from pathlib import Path

import numpy as np

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))

import members.b5_onc_new_criteria as criteria  # noqa: E402
import members.b5_onc_dual_panel_look as successor  # noqa: E402


ORIGINAL_SCORE_SHA = "2b351af4c8fd077cc7fd10b05001a2843f850a3a83f956d5ac75b644a4317c13"
ORIGINAL_GOLD_SHA = "ff5a948361f478cf7d7da2ce84e333594b242cb65574760dd285a8dbd57afe7a"


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def test_original_score_and_gold_are_preserved_byte_for_byte():
    base = ROOT / "originals" / "big5_onc_20260716"
    score = base / "big5_gold_anchored.original.json"
    gold = base / "big5_senate_expert_gold.original.json"
    assert score.is_file() and gold.is_file()
    assert sha256(score) == ORIGINAL_SCORE_SHA
    assert sha256(gold) == ORIGINAL_GOLD_SHA
    doc = json.loads(score.read_text(encoding="utf-8"))
    assert {trait: doc["traits"][trait]["status"] for trait in ("O", "N", "C")} == {
        "O": "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED",
        "N": "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED",
        "C": "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED",
    }


def test_criterion_builder_has_no_gold_dependency_or_production_output():
    source = (FEC / "members" / "b5_onc_new_criteria.py").read_text(encoding="utf-8")
    assert "big5_senate_expert_gold" not in source
    assert "big5_gold_anchored" not in source
    assert "big5_onc_new_criteria.json.candidate" in source
    assert "big5_gold_anchored.json\"" not in source


def test_policy_domain_lexicon_is_locked_and_complete():
    domains = json.loads((ROOT / "data" / "members" / "b5_onc_policy_domains.json").read_text(encoding="utf-8"))
    assert list(domains) == sorted(domains)
    assert len(domains) == 18
    assert all(len(set(terms)) == len(terms) >= 8 for terms in domains.values())


def test_document_selection_is_order_invariant_and_volume_bounded():
    rows = [("pkg-%03d" % index, ("economy health school " * 200))
            for index in range(240)]
    first = criteria.select_documents("Member A", "INTERVIEW", rows)
    second = criteria.select_documents("Member A", "INTERVIEW", list(reversed(rows)))
    assert first == second
    assert len(first) == criteria.O_MAX_PACKAGES == 200
    assert all(len(tokens) == criteria.O_TOKENS_PER_PACKAGE == 250
               for _, tokens in first)


def test_o_measure_rewards_cross_domain_breadth_not_repeated_volume():
    domains = {
        "one": ["economy"], "two": ["health"], "three": ["school"],
        "four": ["climate"],
    }
    narrow = [("p%d" % i, ["economy"] * 250) for i in range(12)]
    broad_tokens = (["economy", "health", "school", "climate"] * 63)[:250]
    broad = [("p%d" % i, broad_tokens) for i in range(12)]
    n = criteria.measure_o_source(narrow, domains, enforce_floors=False)
    b = criteria.measure_o_source(broad, domains, enforce_floors=False)
    assert b["domain_entropy"] > n["domain_entropy"]
    doubled = criteria.measure_o_source(narrow + narrow, domains, enforce_floors=False)
    assert abs(doubled["domain_entropy"] - n["domain_entropy"]) < 1e-12


def test_n_measure_is_dynamic_and_deterministic():
    flat = {"p%d" % i: ["calm steady progress today"] * 8 for i in range(8)}
    volatile = {"p%d" % i: [
        "calm safe good progress today",
        "angry fear crisis threat failure",
    ] * 4 for i in range(8)}
    f = criteria.measure_n_packages(flat, enforce_floors=False)
    v = criteria.measure_n_packages(volatile, enforce_floors=False)
    assert v["lability"] > f["lability"]
    assert v == criteria.measure_n_packages(dict(reversed(list(volatile.items()))),
                                             enforce_floors=False)
    assert set(v["halves"]) == {"0", "1"}


def test_c_execution_consistency_requires_both_modes():
    assert criteria.execution_consistency(1.0, 1.0) == 1.0
    assert criteria.execution_consistency(2.0, -1.0) == -1.0
    assert criteria.execution_consistency(-1.0, 2.0) == -1.0
    assert criteria.execution_consistency(0.5, 0.4) > criteria.execution_consistency(0.5, -0.4)


def test_residualization_removes_nuisance_linear_signal():
    names = ["m%02d" % i for i in range(40)]
    female = {name: float(i % 2) for i, name in enumerate(names)}
    party = {name: float((i // 2) % 2) for i, name in enumerate(names)}
    chamber = {name: float((i // 4) % 2) for i, name in enumerate(names)}
    signal = {name: 3 * female[name] - 2 * party[name] + chamber[name] + math.sin(i)
              for i, name in enumerate(names)}
    result = criteria.residualize_map(signal, [female, party, chamber])
    vector = np.asarray([result[name] for name in names])
    for nuisance in (female, party, chamber):
        other = np.asarray([nuisance[name] for name in names])
        assert abs(float(np.corrcoef(vector, other)[0, 1])) < 1e-10


def fake_original():
    return {"traits": {
        trait: {"members": {
            "A": {trait: -1.0}, "B": {trait: 0.0}, "C": {trait: 1.0},
        }} for trait in ("O", "N", "C", "E", "A")
    }}


def test_fixed_candidate_weights_missingness_and_original_immutability():
    original = fake_original()
    before = json.dumps(original, sort_keys=True)
    new = {"members": {
        "A": {"O": -0.5, "N": -0.5, "C": -0.5},
        "B": {"O": 0.0, "N": 0.0},
        "C": {"O": 0.5, "N": 0.5, "C": 0.5},
    }}
    built = successor.build_candidate_scores(original, new)
    assert json.dumps(original, sort_keys=True) == before
    assert built["weights"] == {
        "O": {"original": 0.7, "new": 0.3},
        "N": {"original": 0.7, "new": 0.3},
        "C": {"original": 0.3, "new": 0.7},
    }
    assert set(built["scores"]["C"]) == {"A", "C"}
    assert "E" not in built["scores"] and "A" not in built["scores"]


def test_dual_panel_evaluator_enforces_dominance_and_partial():
    names = ["m%02d" % i for i in range(40)]
    score = {name: float(i) for i, name in enumerate(names)}
    panel = {}
    for i, name in enumerate(names):
        panel[name] = {
            "O": float(i), "C": math.sin(i * 1.7), "E": math.cos(i * 1.3),
            "A": math.sin(i * 2.3), "ES": math.cos(i * 2.9),
        }
    covariates = {
        name: {"female": float(i % 2), "republican": float((i // 2) % 2),
               "senate": float((i // 4) % 2)}
        for i, name in enumerate(names)
    }
    result = successor.evaluate_panel(
        score, panel, "O", covariates, seed=7, permutations=199)
    assert result["bars"]["n"]
    assert result["bars"]["r"] and result["bars"]["p"]
    assert result["bars"]["dominance"] and result["bars"]["partial"]
    contaminated = {name: dict(row, C=row["O"]) for name, row in panel.items()}
    bad = successor.evaluate_panel(
        score, contaminated, "O", covariates, seed=7, permutations=199)
    assert bad["bars"]["dominance"] is False


def test_prereg_gate_requires_frozen_criteria_and_refuses_second_look():
    base = "**STATUS: LOCKED 2026-07-16.**\n"
    try:
        successor.validate_prereg_gate(base, "a" * 64)
    except successor.LookRefused as exc:
        assert "CRITERIA FREEZE ADDENDUM" in str(exc)
    else:
        raise AssertionError("look ran without a criteria freeze addendum")
    ready = base + "\n## CRITERIA FREEZE ADDENDUM\ncriteria_sha256: `%s`\n" % ("a" * 64)
    successor.validate_prereg_gate(ready, "a" * 64)
    try:
        successor.validate_prereg_gate(ready + "\n## RESULT-ONC\n", "a" * 64)
    except successor.LookRefused as exc:
        assert "already" in str(exc).lower()
    else:
        raise AssertionError("a second look was allowed")


def test_look_source_cannot_write_production_artifacts():
    source = (FEC / "members" / "b5_onc_dual_panel_look.py").read_text(encoding="utf-8")
    assert "big5_onc_crosspanel_successor.json.candidate" in source
    assert "big5_gold_anchored.json\"" not in source
    assert "dump_atomic(doc, OUT" not in source


def test_recorded_result_is_terminal_candidate_and_originals_remain_held():
    result_path = ROOT / "data" / "members" / "big5_onc_crosspanel_successor.json.candidate"
    assert result_path.is_file()
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["schema"] == "b5-onc-crosspanel-successor-v1"
    assert result["status"] == "ONE_OR_MORE_TRAITS_MISS"
    assert result["production_consumers"] == 0
    assert result["traits"]["O"]["status"] == "NOT_LOOKED_CRITERION_STOP_BAR_MISS"
    assert result["traits"]["N"]["status"] == "DUAL_PANEL_SUCCESSOR_MISS"
    assert result["traits"]["C"]["status"] == "DUAL_PANEL_SUCCESSOR_MISS"
    assert result["original_score_sha256"] == ORIGINAL_SCORE_SHA
    assert result["gold_sha256"] == ORIGINAL_GOLD_SHA
    prereg = (ROOT / "big5_onc_crosspanel_successor_prereg.md").read_text(encoding="utf-8")
    assert "\n## RESULT-ONC" in prereg


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
    print("test_b5_onc_successor: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
