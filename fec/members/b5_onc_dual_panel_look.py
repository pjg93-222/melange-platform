# -*- coding: utf-8 -*-
"""One-look evaluator for the locked O/N/C new-criterion successor."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.b5_external_gold_validate import map_gold  # noqa: E402

OUTPUT_BASENAME = "big5_onc_crosspanel_successor.json.candidate"
SCHEMA = "b5-onc-crosspanel-successor-v1"
TRAITS = ("C", "N", "O")
PANELS = ("academic_experts", "senate_insiders")
WEIGHTS = {
    "O": {"original": .70, "new": .30},
    "N": {"original": .70, "new": .30},
    "C": {"original": .30, "new": .70},
}
ORIGINAL_SCORE_SHA = "2b351af4c8fd077cc7fd10b05001a2843f850a3a83f956d5ac75b644a4317c13"
ORIGINAL_GOLD_SHA = "ff5a948361f478cf7d7da2ce84e333594b242cb65574760dd285a8dbd57afe7a"
CONGESTION_SHA = "ddc9d746600dbdfc60b76c54537662e19c758cbfb9d7a478a71b7bf90cd727a6"
LEGISLATORS_SHA = "c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391"


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
    if "\n## RESULT-ONC" in text:
        raise LookRefused("the O/N/C successor look was already consumed")
    if "\n## CRITERIA FREEZE ADDENDUM" not in text:
        raise LookRefused("CRITERIA FREEZE ADDENDUM is absent")
    match = re.search(r"(?m)^criteria_sha256:\s*`([0-9a-f]{64})`\s*$", text)
    if not match:
        raise LookRefused("criteria freeze hash is absent or malformed")
    if match.group(1) != str(criteria_sha256).lower():
        raise LookRefused("criteria artifact hash differs from the freeze addendum")
    return True


def _zmap(values):
    names = sorted(values)
    vector = np.asarray([float(values[name]) for name in names])
    if len(vector) < 2 or vector.std(ddof=1) == 0:
        raise ValueError("score intersection is insufficient or constant")
    mean, sd = float(vector.mean()), float(vector.std(ddof=1))
    return {name: (float(values[name]) - mean) / sd for name in names}


def build_candidate_scores(original_doc, criteria_doc):
    rows = criteria_doc.get("members") or {}
    scores, originals = {}, {}
    for trait in ("O", "N", "C"):
        old_members = ((original_doc.get("traits") or {}).get(trait) or {}).get("members") or {}
        old = {name: float(row[trait]) for name, row in old_members.items()
               if isinstance(row, dict) and row.get(trait) is not None}
        new = {name: float(row[trait]) for name, row in rows.items()
               if isinstance(row, dict) and row.get(trait) is not None}
        names = sorted(set(old) & set(new))
        old_z = _zmap({name: old[name] for name in names})
        new_z = _zmap({name: new[name] for name in names})
        weight = WEIGHTS[trait]
        combined = {name: weight["original"] * old_z[name] + weight["new"] * new_z[name]
                    for name in names}
        scores[trait] = _zmap(combined)
        originals[trait] = old_z
    return {"weights": json.loads(json.dumps(WEIGHTS)), "scores": scores,
            "original_scores_same_roster": originals}


def _corr(left, right):
    if len(left) < 5:
        return None
    a, b = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _gold_value(row, trait):
    if trait == "N":
        value = row.get("ES")
        return None if value is None else -float(value)
    value = row.get(trait)
    return None if value is None else float(value)


def _permutation_p(x, y, seed, permutations):
    observed = _corr(x, y)
    if observed is None:
        return None
    rng = np.random.default_rng(int(seed))
    y = np.asarray(y, dtype=float)
    exceed = 0
    for _ in range(int(permutations)):
        value = _corr(x, rng.permutation(y))
        exceed += int(value is not None and value >= observed)
    return float((exceed + 1) / (int(permutations) + 1))


def _partial(x, y, controls):
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    design = np.column_stack([np.ones(len(x)), np.asarray(controls, dtype=float)])
    if np.linalg.matrix_rank(design) != design.shape[1]:
        raise LookRefused("panel nuisance design is rank deficient")
    bx, *_ = np.linalg.lstsq(design, x, rcond=None)
    by, *_ = np.linalg.lstsq(design, y, rcond=None)
    return _corr(x - design @ bx, y - design @ by)


def evaluate_panel(score, panel, trait, covariates, seed, permutations=9999):
    members = sorted(name for name in score
                     if name in panel and _gold_value(panel[name], trait) is not None
                     and name in covariates)
    x = [float(score[name]) for name in members]
    y = [_gold_value(panel[name], trait) for name in members]
    own = _corr(x, y)
    hetero = {}
    for other in ("O", "C", "E", "A", "N"):
        if other == trait:
            continue
        pairs = [(float(score[name]), _gold_value(panel[name], other)) for name in members
                 if _gold_value(panel[name], other) is not None]
        hetero[other] = _corr([left for left, _ in pairs], [right for _, right in pairs])
    finite_hetero = [abs(value) for value in hetero.values() if value is not None]
    maximum = max(finite_hetero) if finite_hetero else float("inf")
    for key in ("female", "republican"):
        values = [float(covariates[name][key]) for name in members]
        if sum(value == 1 for value in values) < 5 or sum(value == 0 for value in values) < 5:
            raise LookRefused("panel covariate %s fails the >=5 ones/zeros floor" % key)
    controls = [[float(covariates[name]["female"]),
                 float(covariates[name]["republican"])] for name in members]
    partial = _partial(x, y, controls)
    p = _permutation_p(x, y, seed, permutations)
    bars = {
        "n": len(members) >= 30,
        "r": own is not None and own >= .20,
        "p": p is not None and p < .10,
        "dominance": own is not None and own > maximum,
        "partial": partial is not None and partial >= .10,
    }
    return {
        "n": len(members), "r": own, "permutation_p_one_sided": p,
        "partial_female_party": partial, "heterotrait": hetero,
        "max_abs_heterotrait": maximum, "bars": bars, "pass": all(bars.values()),
        "permutations": int(permutations), "seed": int(seed),
    }


def _load_covariates(data_root):
    congestion_path = os.path.join(data_root, "data", "engine", "congestion.json")
    legislators_path = os.path.join(data_root, "fec", "legislators.csv")
    if sha256_file(congestion_path) != CONGESTION_SHA:
        raise LookRefused("congestion fingerprint mismatch")
    if sha256_file(legislators_path) != LEGISLATORS_SHA:
        raise LookRefused("legislators fingerprint mismatch")
    with open(congestion_path, encoding="utf-8") as handle:
        roster_rows = json.load(handle)
    by_bioguide = {str(row.get("bioguide") or ""): row["member"] for row in roster_rows}
    chamber = {row["member"]: row.get("chamber") for row in roster_rows}
    out = {}
    with open(legislators_path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            name = by_bioguide.get(str(row.get("bioguide_id") or ""))
            if name:
                out[name] = {
                    "female": 1.0 if row.get("gender") == "F" else 0.0,
                    "republican": 1.0 if row.get("party") == "Republican" else 0.0,
                    "senate": 1.0 if chamber.get(name) == "Senate" else 0.0,
                }
    return [row["member"] for row in roster_rows], out


def run(original_path, gold_path, criteria_path, prereg_path, data_root, permutations=9999):
    if sha256_file(original_path) != ORIGINAL_SCORE_SHA:
        raise LookRefused("preserved original score fingerprint mismatch")
    if sha256_file(gold_path) != ORIGINAL_GOLD_SHA:
        raise LookRefused("preserved original gold fingerprint mismatch")
    criteria_sha = sha256_file(criteria_path)
    with open(prereg_path, encoding="utf-8") as handle:
        validate_prereg_gate(handle.read(), criteria_sha)
    with open(original_path, encoding="utf-8") as handle:
        original = json.load(handle)
    with open(gold_path, encoding="utf-8") as handle:
        gold = json.load(handle)
    with open(criteria_path, encoding="utf-8") as handle:
        criteria_doc = json.load(handle)
    if criteria_doc.get("schema") != "b5-onc-new-criteria-v1":
        raise LookRefused("criterion artifact schema mismatch")
    roster, covariates = _load_covariates(data_root)
    panels, unmatched = map_gold(gold, roster)
    built = build_candidate_scores(original, criteria_doc)
    output = {}
    for trait_index, trait in enumerate(TRAITS):
        if (criteria_doc.get("trait_status") or {}).get(trait) != "CRITERION_READY_FOR_ONE_LOOK":
            output[trait] = {"status": "NOT_LOOKED_CRITERION_STOP_BAR_MISS"}
            continue
        candidate_panels, original_panels = {}, {}
        for panel_index, panel_name in enumerate(PANELS):
            seed = 20260716 + 100 * trait_index + panel_index
            candidate_panels[panel_name] = evaluate_panel(
                built["scores"][trait], panels[panel_name], trait, covariates,
                seed=seed, permutations=permutations)
            original_panels[panel_name] = evaluate_panel(
                built["original_scores_same_roster"][trait], panels[panel_name], trait,
                covariates, seed=seed, permutations=permutations)
        passed = all(row["pass"] for row in candidate_panels.values())
        output[trait] = {
            "status": ("DUAL_PANEL_SUCCESSOR_CANDIDATE_PASS" if passed
                       else "DUAL_PANEL_SUCCESSOR_MISS"),
            "pass": passed,
            "candidate": {"panels": candidate_panels,
                          "coverage_n": len(built["scores"][trait]),
                          "weights": WEIGHTS[trait]},
            "original_same_roster_context": {"panels": original_panels,
                                             "status_unchanged": True},
        }
    return {
        "schema": SCHEMA,
        "status": ("ALL_THREE_DUAL_PANEL_CANDIDATE_PASS" if all(
            output.get(trait, {}).get("pass") is True for trait in TRAITS)
            else "ONE_OR_MORE_TRAITS_MISS"),
        "production_consumers": 0,
        "original_score_sha256": ORIGINAL_SCORE_SHA,
        "gold_sha256": ORIGINAL_GOLD_SHA,
        "criteria_sha256": criteria_sha,
        "unmatched_historic_names": len(unmatched),
        "traits": output,
        "method": {
            "weights": WEIGHTS,
            "panel_controls": ["female", "republican"],
            "chamber_context": "constant Senate; excluded per prereg Addendum 1",
            "one_look": True,
        },
    }


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = argparse.ArgumentParser(description="Run the locked O/N/C dual-panel one look")
    parser.add_argument("--original", default=os.path.join(
        root, "originals", "big5_onc_20260716", "big5_gold_anchored.original.json"))
    parser.add_argument("--gold", default=os.path.join(
        root, "originals", "big5_onc_20260716", "big5_senate_expert_gold.original.json"))
    parser.add_argument("--criteria", default=os.path.join(
        root, "data", "members", "big5_onc_new_criteria.json.candidate"))
    parser.add_argument("--prereg", default=os.path.join(root,
        "big5_onc_crosspanel_successor_prereg.md"))
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--out", default=os.path.join(
        root, "data", "members", OUTPUT_BASENAME))
    parser.add_argument("--look", action="store_true")
    parser.add_argument("--permutations", type=int, default=9999,
                        help="test-only override; locked look requires 9999")
    args = parser.parse_args(argv)
    criteria_sha = sha256_file(args.criteria) if os.path.isfile(args.criteria) else "missing"
    if not args.look:
        print("O/N/C look not taken | criteria=%s | pass --look after freeze addendum" % criteria_sha)
        return 0
    if args.permutations != 9999:
        raise LookRefused("locked look requires exactly 9999 permutations")
    if not str(args.out).endswith(".candidate"):
        raise LookRefused("output must end in .candidate")
    if os.path.exists(args.out):
        raise LookRefused("candidate output already exists; refusing a second write")
    doc = run(args.original, args.gold, args.criteria, args.prereg,
              args.data_root, permutations=args.permutations)
    from moneyball_core import dump_atomic
    dump_atomic(doc, args.out, sort_keys=True, indent=1)
    print(json.dumps(doc, sort_keys=True, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
