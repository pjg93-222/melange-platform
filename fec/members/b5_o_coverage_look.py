# -*- coding: utf-8 -*-
"""One-look evaluator for the locked O coverage-recovery successor."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import members.b5_onc_dual_panel_look as panel_eval  # noqa: E402
from members.b5_external_gold_validate import map_gold  # noqa: E402

OUTPUT_BASENAME = "big5_o_coverage_result.json.candidate"
SCHEMA = "b5-o-coverage-result-v1"
WEIGHTS = {"original": .70, "new": .30}
PANELS = ("academic_experts", "senate_insiders")
SEEDS = {"academic_experts": 20260916, "senate_insiders": 20260917}

ORIGINAL_SCORE_SHA = "2b351af4c8fd077cc7fd10b05001a2843f850a3a83f956d5ac75b644a4317c13"
ORIGINAL_GOLD_SHA = "ff5a948361f478cf7d7da2ce84e333594b242cb65574760dd285a8dbd57afe7a"
STOPPED_CRITERION_SHA = "eb16cde3c160f7fc14280a3c6b0ae4754a913606fcd2e78fa7a4fd1c8dea8e12"
PRIOR_RESULT_SHA = "fc3bdbfa59671f61891c34d38e13839f57092c2110d760a12e221e7fc43ba5e9"


class LookRefused(RuntimeError):
    pass


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_prereg_gate(text, criteria_sha256):
    if "**STATUS: LOCKED 2026-07-16.**" not in text:
        raise LookRefused("preregistration is not locked")
    if "\n## RESULT-O-RECOVERY" in text:
        raise LookRefused("the O recovery look was already consumed")
    if "\n## CRITERIA FREEZE ADDENDUM" not in text:
        raise LookRefused("CRITERIA FREEZE ADDENDUM is absent")
    match = re.search(r"(?m)^criteria_sha256:\s*`([0-9a-f]{64})`\s*$", text)
    if not match:
        raise LookRefused("criteria freeze hash is absent or malformed")
    if match.group(1) != str(criteria_sha256).lower():
        raise LookRefused("criteria artifact hash differs from the freeze addendum")
    return True


def build_candidate_score(original_doc, criterion_doc):
    old_rows = (((original_doc.get("traits") or {}).get("O") or {}).get("members") or {})
    new_rows = criterion_doc.get("members") or {}
    old = {name: float(row["O"]) for name, row in old_rows.items()
           if isinstance(row, dict) and row.get("O") is not None}
    new = {name: float(row["O"]) for name, row in new_rows.items()
           if isinstance(row, dict) and row.get("O") is not None}
    names = sorted(set(old) & set(new))
    old_z = panel_eval._zmap({name: old[name] for name in names})
    new_z = panel_eval._zmap({name: new[name] for name in names})
    combined = {name: WEIGHTS["original"] * old_z[name] + WEIGHTS["new"] * new_z[name]
                for name in names}
    return {
        "weights": dict(WEIGHTS),
        "score": panel_eval._zmap(combined),
        "original_same_roster": old_z,
    }


def _require_sha(path, expected, label):
    actual = sha256_file(path)
    if actual != expected:
        raise LookRefused("%s fingerprint mismatch" % label)
    return actual


def run(original_path, gold_path, criteria_path, prereg_path, data_root,
        stopped_path, prior_result_path, permutations=9999):
    _require_sha(original_path, ORIGINAL_SCORE_SHA, "preserved original score")
    _require_sha(gold_path, ORIGINAL_GOLD_SHA, "preserved original gold")
    _require_sha(stopped_path, STOPPED_CRITERION_SHA, "stopped O criterion")
    _require_sha(prior_result_path, PRIOR_RESULT_SHA, "prior O/N/C result")
    criteria_sha = sha256_file(criteria_path)
    with open(prereg_path, encoding="utf-8") as handle:
        validate_prereg_gate(handle.read(), criteria_sha)
    with open(criteria_path, encoding="utf-8") as handle:
        criteria = json.load(handle)
    if criteria.get("schema") != "b5-o-coverage-recovery-v1":
        raise LookRefused("criterion artifact schema mismatch")
    if not (criteria.get("readiness") or {}).get("ready_for_look"):
        raise LookRefused("criterion did not clear the locked pre-look readiness ladder")
    if criteria.get("stopped_criterion_sha256") != STOPPED_CRITERION_SHA:
        raise LookRefused("criterion does not bind the stopped control")

    with open(original_path, encoding="utf-8") as handle:
        original = json.load(handle)
    with open(gold_path, encoding="utf-8") as handle:
        gold = json.load(handle)
    roster, covariates = panel_eval._load_covariates(data_root)
    panels, unmatched = map_gold(gold, roster)
    built = build_candidate_score(original, criteria)
    candidate_panels = {}
    original_panels = {}
    for panel_name in PANELS:
        seed = SEEDS[panel_name]
        candidate_panels[panel_name] = panel_eval.evaluate_panel(
            built["score"], panels[panel_name], "O", covariates,
            seed=seed, permutations=permutations)
        original_panels[panel_name] = panel_eval.evaluate_panel(
            built["original_same_roster"], panels[panel_name], "O", covariates,
            seed=seed, permutations=permutations)
    passed = all(row["pass"] for row in candidate_panels.values())
    return {
        "schema": SCHEMA,
        "status": ("DUAL_PANEL_SUCCESSOR_CANDIDATE_PASS" if passed
                   else "DUAL_PANEL_SUCCESSOR_MISS"),
        "pass": passed,
        "production_consumers": 0,
        "criteria_sha256": criteria_sha,
        "original_score_sha256": ORIGINAL_SCORE_SHA,
        "gold_sha256": ORIGINAL_GOLD_SHA,
        "stopped_criterion_sha256": STOPPED_CRITERION_SHA,
        "prior_result_sha256": PRIOR_RESULT_SHA,
        "unmatched_historic_names": len(unmatched),
        "candidate": {
            "coverage_n": len(built["score"]),
            "weights": dict(WEIGHTS),
            "panels": candidate_panels,
        },
        "original_same_roster_context": {
            "status_unchanged": True,
            "panels": original_panels,
        },
        "method": {
            "one_look": True,
            "readiness_path": criteria["readiness"]["path"],
            "panel_controls": ["female", "republican"],
            "chamber_context": "constant Senate; excluded per preregistration",
            "permutations": int(permutations),
            "seeds": dict(SEEDS),
        },
    }


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    preserved = os.path.join(root, "originals", "big5_onc_20260716")
    parser = argparse.ArgumentParser(description="Run locked O coverage-recovery one look")
    parser.add_argument("--original", default=os.path.join(
        preserved, "big5_gold_anchored.original.json"))
    parser.add_argument("--gold", default=os.path.join(
        preserved, "big5_senate_expert_gold.original.json"))
    parser.add_argument("--criteria", default=os.path.join(
        root, "data", "members", "big5_o_coverage_recovery.json.candidate"))
    parser.add_argument("--prereg", default=os.path.join(
        root, "big5_o_coverage_recovery_prereg.md"))
    parser.add_argument("--stopped", default=os.path.join(
        root, "data", "members", "big5_onc_new_criteria.json.candidate"))
    parser.add_argument("--prior-result", default=os.path.join(
        root, "data", "members", "big5_onc_crosspanel_successor.json.candidate"))
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--out", default=OUTPUT_BASENAME)
    parser.add_argument("--look", action="store_true")
    parser.add_argument("--permutations", type=int, default=9999,
                        help="test-only override; locked look requires 9999")
    args = parser.parse_args(argv)
    criteria_sha = sha256_file(args.criteria) if os.path.isfile(args.criteria) else "missing"
    if not args.look:
        print("O recovery look not taken | criteria=%s" % criteria_sha)
        return 0
    if args.permutations != 9999:
        raise LookRefused("locked look requires exactly 9999 permutations")
    if not str(args.out).endswith(".candidate"):
        raise LookRefused("result output must end in .candidate")
    document = run(
        args.original, args.gold, args.criteria, args.prereg, args.data_root,
        args.stopped, args.prior_result, permutations=args.permutations)
    from moneyball_core import dump_atomic
    dump_atomic(document, args.out, sort_keys=True, indent=1)
    print("O coverage recovery: %s" % document["status"])
    for panel_name in PANELS:
        row = document["candidate"]["panels"][panel_name]
        print("  %s n=%d r=%.4f p=%.4f partial=%.4f pass=%s" % (
            panel_name, row["n"], row["r"], row["permutation_p_one_sided"],
            row["partial_female_party"], row["pass"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
