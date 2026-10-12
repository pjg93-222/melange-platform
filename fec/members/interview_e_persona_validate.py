# -*- coding: utf-8 -*-
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as MB_ROOT

"""Locked two-stage validation for the observed interview-E criterion and expressiveness-v2."""

import argparse
import collections
import csv
import hashlib
import json
import math
import re

import numpy as np

_s.stdout.reconfigure(encoding="utf-8", errors="replace")
B = MB_ROOT
PREREG = _o.path.join(B, "big5_e_interview_persona_prereg.md")
LABELS = _o.path.join(B, "interview_e_persona_halves.json.candidate")
OUT = _o.path.join(B, "interview_e_persona_validation.json")
K = 5000
SALTS = {"primary": "e-persona-primary-v1", "rederive": "e-persona-rederive-v1"}
PINS = {
    "social_media.db": "21f2e9f6c13d44f5bed27cabfc9414b445ba8b0f047e88b101ac5267112ab6ec",
    "data/engine/congestion.json": "ddc9d746600dbdfc60b76c54537662e19c758cbfb9d7a478a71b7bf90cd727a6",
    _o.path.join("fec", "legislators.csv"): "c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391",
    _o.path.join("fec", "members", "behavioral_roles_v2_study.py"): "07c79c7966d5c1fe8f8328ed2f971468142139800ec995f34b3f15cd58a2824d",
    "behavioral_roles_v2_prereg.md": "ee6ede45daf63b9ad4894d62dad8ec7accf07e3093b6118a02c6f5bdb071e023",
    "data/money/affective_n.json": "f3601ce9338dcbe5d222a10429fd7092c13ffcda020586ecc5f91fc484cde3ce",
    "data/stance/interview_antagonism.json": "20752950b908e8b0ac5b79c06ccfae687fe6a486057e53e1915f361d58672c29",
    "data/members/big5_blue_validated.json": "876bc2ecf8672477d586f3db34472cdb2cf61ee0573e30c4b909448cd0970a8f",
    _o.path.join("fec", "interview_e_persona_rubric.md"): "51fceb122ec5463b80026301fb7676a39d909f062b75285d9d08ba182b28306c",
    _o.path.join("fec", "members", "interview_e_persona_score.py"): "5de20b376d23ccc8541e15b92ec3059dfcc4f443869691e947b745b532c49eb0",
}
RUBRIC_VERSION = "observed-interview-e-v1-cache-manual-2026-07-13"
RUBRIC_SHA256 = "51fceb122ec5463b80026301fb7676a39d909f062b75285d9d08ba182b28306c"


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_pins():
    for relpath, expected in PINS.items():
        actual = sha(_o.path.join(B, relpath))
        if actual != expected:
            raise SystemExit("input pin drift: %s expected %s got %s" % (relpath, expected, actual))


def corr(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    if len(x) < 5 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def sb(r):
    return 2 * r / (1 + r) if np.isfinite(r) and r > -1 else float("nan")


def residual(values, controls):
    values = np.asarray(values, float)
    controls = np.asarray(controls, float)
    design = np.column_stack([np.ones(len(values)), controls])
    return values - design @ np.linalg.lstsq(design, values, rcond=None)[0]


def meta_r(r0, r1):
    clipped = np.clip([r0, r1], -0.999999, 0.999999)
    return float(np.tanh(np.mean(np.arctanh(clipped))))


def label_rows(labels, split_name):
    out = {}
    for member, record in labels.get("members", {}).items():
        split = record.get(split_name)
        if not split:
            continue
        try:
            a = split["halves"]["A"]
            b = split["halves"]["B"]
            out[member] = {
                "A": float(a["observed_e"]),
                "B": float(b["observed_e"]),
                "packages_A": int(split["packages"][0]),
                "packages_B": int(split["packages"][1]),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


def metadata():
    congestion = json.load(open(_o.path.join(B, "data", "engine", "congestion.json"), encoding="utf-8"))
    chamber = {row["member"]: row.get("chamber") for row in congestion}
    import moneyball_core as MC

    party0 = MC.party()
    party = {m: 1.0 if party0.get(m) == "R" else 0.0 for m in chamber if party0.get(m) in ("D", "R")}
    gender = {}
    b2n = MC.bio2name()
    with open(_o.path.join(B, "fec", "legislators.csv"), encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            member = b2n.get(row.get("bioguide_id"))
            if member and row.get("gender") in ("F", "M"):
                gender[member] = 1.0 if row["gender"] == "F" else 0.0
    senate = {m: 1.0 if ch == "Senate" else 0.0 for m, ch in chamber.items()}
    return chamber, party, gender, senate


def criterion_metrics(labels):
    _chamber, party, gender, _senate = metadata()
    rows = {name: label_rows(labels, name) for name in SALTS}
    reliability = {}
    for name, values in rows.items():
        members = sorted(values)
        r = corr([values[m]["A"] for m in members], [values[m]["B"] for m in members])
        reliability[name] = {"n": len(members), "r_half": r, "r_sb": sb(r)}
    both = sorted(set(rows["primary"]) & set(rows["rederive"]))
    primary_mean = {m: (rows["primary"][m]["A"] + rows["primary"][m]["B"]) / 2 for m in rows["primary"]}
    rederive_mean = {m: (rows["rederive"][m]["A"] + rows["rederive"][m]["B"]) / 2 for m in rows["rederive"]}
    repeat_r = corr([primary_mean[m] for m in both], [rederive_mean[m] for m in both])
    all_mean = {m: (primary_mean[m] + rederive_mean[m]) / 2 for m in both}
    log_packages = {
        m: math.log1p(sum(rows[name][m]["packages_A"] + rows[name][m]["packages_B"] for name in SALTS))
        for m in both
    }
    ant = {
        m: row.get("agreeableness_z")
        for m, row in json.load(open(_o.path.join(B, "data", "stance", "interview_antagonism.json"), encoding="utf-8"))["members"].items()
        if row.get("agreeableness_z") is not None
    }
    affect = json.load(open(_o.path.join(B, "data", "money", "affective_n.json"), encoding="utf-8"))["members"]

    def joined(target):
        names = [m for m in all_mean if m in target]
        return corr([all_mean[m] for m in names], [target[m] for m in names]), len(names)

    contamination = {
        "party_R": joined(party),
        "female": joined(gender),
        "log_source_packages": joined(log_packages),
        "inverse_antagonism_A": joined(ant),
        "affective_N": joined(affect),
    }
    gates = {
        "coverage": reliability["primary"]["n"] >= 300 and reliability["rederive"]["n"] >= 300,
        "reliability": reliability["primary"]["r_sb"] >= 0.60 and reliability["rederive"]["r_sb"] >= 0.60,
        "partition_repeat": repeat_r >= 0.70,
        "demographic_volume": abs(contamination["party_R"][0]) <= 0.20
        and abs(contamination["female"][0]) <= 0.15
        and abs(contamination["log_source_packages"][0]) <= 0.20,
        "construct_separation": abs(contamination["inverse_antagonism_A"][0]) <= 0.30
        and abs(contamination["affective_N"][0]) <= 0.30,
    }
    return {
        "reliability": reliability,
        "partition_repeat_r": repeat_r,
        "intersection_n": len(both),
        "contamination": contamination,
        "gates": gates,
        "qualified": all(gates.values()),
    }


def print_criterion(metrics, label_hash):
    print("=== OBSERVED INTERVIEW-E CRITERION GATE ===")
    for name, row in metrics["reliability"].items():
        print("  %s coverage=%d half-r=%+.3f SB=%.3f" % (name, row["n"], row["r_half"], row["r_sb"]))
    print("  partition-repeat r=%+.3f n=%d" % (metrics["partition_repeat_r"], metrics["intersection_n"]))
    print(
        "  contamination: %s"
        % " | ".join("%s=%+.3f(n=%d)" % (name, row[0], row[1]) for name, row in metrics["contamination"].items())
    )
    for name, passed in metrics["gates"].items():
        print("  %-28s %s" % (name, "PASS" if passed else "MISS"))
    print("  CRITERION VERDICT: %s" % ("QUALIFIED" if metrics["qualified"] else "REJECTED"))
    print("  COLLECTION_SHA256: %s" % label_hash)


def stable_half(package, salt):
    raw = (salt + "\0" + package).encode("utf-8", "surrogatepass")
    return hashlib.sha256(raw).digest()[0] & 1


def build_half_scores(salt, chamber):
    from members.behavioral_roles_v2_study import build_scores, load_appearances

    appearances = load_appearances()
    halves = [collections.defaultdict(list), collections.defaultdict(list)]
    package_counts = [collections.Counter(), collections.Counter()]
    package_sets = [collections.defaultdict(set), collections.defaultdict(set)]
    for key, turns in appearances.items():
        half = stable_half(key[2], salt)
        halves[half][key] = turns
        if any(len(text.split()) >= 8 for text in turns):
            package_counts[half][key[0]] += 1
            package_sets[half][key[0]].add(key[2])
    return [build_scores(halves[h], chamber, 15) for h in (0, 1)], package_counts, package_sets


def crossfit(feature_name, scores, ratings, party, gender, senate, seed, partial):
    names = sorted(
        m
        for m in ratings
        if m in scores[0][feature_name]
        and m in scores[1][feature_name]
        and m in party
        and m in gender
        and m in senate
    )
    x0 = np.asarray([scores[0][feature_name][m] for m in names], float)
    x1 = np.asarray([scores[1][feature_name][m] for m in names], float)
    y0 = np.asarray([ratings[m]["B"] for m in names], float)
    y1 = np.asarray([ratings[m]["A"] for m in names], float)
    if partial:
        z0 = np.asarray(
            [[party[m], gender[m], senate[m], math.log1p(scores[0]["turns"].get(m, 0)), math.log1p(ratings[m]["packages_B"])] for m in names],
            float,
        )
        z1 = np.asarray(
            [[party[m], gender[m], senate[m], math.log1p(scores[1]["turns"].get(m, 0)), math.log1p(ratings[m]["packages_A"])] for m in names],
            float,
        )
        x0, y0 = residual(x0, z0), residual(y0, z0)
        x1, y1 = residual(x1, z1), residual(y1, z1)
    r0, r1 = corr(x0, y0), corr(x1, y1)
    observed = meta_r(r0, r1)
    rng = np.random.default_rng(seed)
    ge = 0
    for _ in range(K):
        order = rng.permutation(len(names))
        ge += meta_r(corr(x0, y0[order]), corr(x1, y1[order])) >= observed
    return {"n": len(names), "r_A_to_B": r0, "r_B_to_A": r1, "meta_r": observed, "p": (ge + 1) / (K + 1)}


def split_look(split_name, labels, chamber, party, gender, senate, seed):
    scores, package_counts, package_sets = build_half_scores(SALTS[split_name], chamber)
    ratings = label_rows(labels, split_name)
    raw = crossfit("expressiveness_v2", scores, ratings, party, gender, senate, seed, False)
    partial = crossfit("expressiveness_v2", scores, ratings, party, gender, senate, seed + 1, True)
    rivals = {
        name: crossfit(feature, scores, ratings, party, gender, senate, seed + 10 + i, True)
        for i, (name, feature) in enumerate((('dominance_v2', 'dominance_v2'), ('reactivity', 'reactivity_raw')))
    }
    overlap_count = sum(
        len(package_sets[0].get(member, set()) & package_sets[1].get(member, set()))
        for member in ratings
    )
    partition_count_mismatches = sum(
        package_counts[half].get(member, 0) != ratings[member]["packages_%s" % ("A" if half == 0 else "B")]
        for member in ratings
        for half in (0, 1)
    )
    gates = {
        "coverage": raw["n"] >= 190 and partial["n"] >= 190,
        "raw": raw["r_A_to_B"] > 0 and raw["r_B_to_A"] > 0 and raw["meta_r"] >= 0.15 and raw["p"] < 0.05,
        "partial": partial["r_A_to_B"] > 0 and partial["r_B_to_A"] > 0 and partial["meta_r"] >= 0.10 and partial["p"] < 0.05,
        "specificity": partial["meta_r"] > max(abs(row["meta_r"]) for row in rivals.values()),
        "package_disjoint": overlap_count == 0,
        "partition_counts_match": partition_count_mismatches == 0,
    }
    return {
        "raw": raw,
        "partial": partial,
        "rivals": rivals,
        "overlap_count": overlap_count,
        "partition_count_mismatches": partition_count_mismatches,
        "gates": gates,
        "passed": all(gates.values()),
    }


def simulate():
    rng = np.random.default_rng(2026071308)
    n = 220
    z = rng.normal(size=(n, 4))

    def one(rho, confound, seed):
        latent = rng.normal(size=n)
        x0 = latent + confound * z[:, 0] + rng.normal(scale=0.7, size=n)
        x1 = latent + confound * z[:, 0] + rng.normal(scale=0.7, size=n)
        y0 = rho * latent + confound * z[:, 0] + rng.normal(size=n)
        y1 = rho * latent + confound * z[:, 0] + rng.normal(size=n)
        rx0, ry0 = residual(x0, z), residual(y0, z)
        rx1, ry1 = residual(x1, z), residual(y1, z)
        r = meta_r(corr(rx0, ry0), corr(rx1, ry1))
        local = np.random.default_rng(seed)
        ge = 0
        for _ in range(2000):
            order = local.permutation(n)
            ge += meta_r(corr(rx0, ry0[order]), corr(rx1, ry1[order])) >= r
        return r, (ge + 1) / 2001

    planted = one(0.45, 0.5, 1)
    null = one(0.0, 0.0, 2)
    confounded = one(0.0, 0.8, 3)
    checks = {
        "planted_admitted": planted[0] >= 0.10 and planted[1] < 0.05,
        "null_refused": not (null[0] >= 0.10 and null[1] < 0.05),
        "confound_only_refused": not (confounded[0] >= 0.10 and confounded[1] < 0.05),
    }
    print("=== OBSERVED INTERVIEW-E SYNTHETIC BATTERY ===")
    print("  planted partial meta r=%+.3f p=%.4f" % planted)
    print("  null partial    meta r=%+.3f p=%.4f" % null)
    print("  confound-only   meta r=%+.3f p=%.4f" % confounded)
    for name, passed in checks.items():
        print("  %-28s %s" % (name, "PASS" if passed else "FAIL"))
    if not all(checks.values()):
        raise SystemExit("synthetic battery refused")
    print("BATTERY PASS")


def look(labels, label_hash):
    text = open(PREREG, encoding="utf-8").read()
    if "STATUS: LOCKED" not in text:
        raise SystemExit("LOOK REFUSED: prereg not LOCKED")
    if "\n## RESULT-EI" in text:
        raise SystemExit("LOOK REFUSED: RESULT-EI already recorded")
    match = re.search(r"^COLLECTION_SHA256:\s*`?([0-9a-f]{64})`?\s*$", text, flags=re.M | re.I)
    if not match or match.group(1).lower() != label_hash:
        raise SystemExit("LOOK REFUSED: committed collection hash absent or mismatched")
    metrics = criterion_metrics(labels)
    if not metrics["qualified"]:
        raise SystemExit("LOOK REFUSED: Stage-1 criterion no longer qualifies")
    simulate()
    chamber, party, gender, senate = metadata()
    primary = split_look("primary", labels, chamber, party, gender, senate, 2026071310)
    rederive = split_look("rederive", labels, chamber, party, gender, senate, 2026071320)
    passed = primary["passed"] and rederive["passed"]

    blue = json.load(open(_o.path.join(B, "data", "members", "big5_blue_validated.json"), encoding="utf-8"))["traits"]
    ratings = label_rows(labels, "primary")
    common = [m for m in ratings if m in blue and blue[m].get("E") is not None]
    blue_e = corr([blue[m]["E"] for m in common], [(ratings[m]["A"] + ratings[m]["B"]) / 2 for m in common])
    result = {"collection_sha256": label_hash, "criterion": metrics, "primary": primary, "rederive": rederive, "passed": passed, "secondary_blue_E_r": blue_e, "secondary_blue_E_n": len(common)}
    from moneyball_core import dump_atomic

    dump_atomic(result, OUT, indent=1)
    print("=== OBSERVED INTERVIEW-E x EXPRESSIVENESS — THE ONE LOOK ===")
    for name, row in (("primary", primary), ("rederive", rederive)):
        print(
            "  %s raw: n=%d dirs=%+.3f/%+.3f meta=%+.3f p=%.4f"
            % (name, row["raw"]["n"], row["raw"]["r_A_to_B"], row["raw"]["r_B_to_A"], row["raw"]["meta_r"], row["raw"]["p"])
        )
        print(
            "  %s partial: n=%d dirs=%+.3f/%+.3f meta=%+.3f p=%.4f | rivals dom=%+.3f react=%+.3f"
            % (name, row["partial"]["n"], row["partial"]["r_A_to_B"], row["partial"]["r_B_to_A"], row["partial"]["meta_r"], row["partial"]["p"], row["rivals"]["dominance_v2"]["meta_r"], row["rivals"]["reactivity"]["meta_r"])
        )
        print("  %s gates: %s -> %s" % (name, ", ".join("%s=%s" % (gate, "PASS" if value else "MISS") for gate, value in row["gates"].items()), "PASS" if row["passed"] else "MISS"))
    print("  secondary Blue-E same-venue r=%+.3f n=%d (no status consequence)" % (blue_e, len(common)))
    print("  FINAL VERDICT: %s" % ("PASS — cross-package observed interview-E convergence" if passed else "MISS — operational speaking style only"))
    print("wrote interview_e_persona_validation.json (consumed by nothing)")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--simulate", action="store_true")
    group.add_argument("--criterion", action="store_true")
    group.add_argument("--look", action="store_true")
    args = parser.parse_args()
    if args.simulate:
        simulate()
        return
    assert_pins()
    if not _o.path.exists(LABELS):
        raise SystemExit("label artifact absent; collect after prereg lock")
    labels = json.load(open(LABELS, encoding="utf-8"))
    if labels.get("rubric_version") != RUBRIC_VERSION or labels.get("rubric_sha256") != RUBRIC_SHA256:
        raise SystemExit("label rubric/version drift")
    label_hash = sha(LABELS)
    if args.criterion:
        print_criterion(criterion_metrics(labels), label_hash)
    else:
        look(labels, label_hash)


if __name__ == "__main__":
    main()
