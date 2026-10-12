# -*- coding: utf-8 -*-
import os as _o, sys as _s
_s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))
from paths import ROOT as MB_ROOT

"""Two-stage source-disjoint validation of interview expressiveness against clean acoustic E.

Stage 1 builds a cross-fitted eGeMAPS->Blue-E criterion without opening interview behavior. Stage 2
opens the frozen behavioral candidates only after the criterion artifact is qualified and hash-locked.
Outputs are candidate/research artifacts consumed by no production script. Locked 2026-07-13.
"""

import argparse
import collections
import csv
import hashlib
import json
import math
import re

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, KFold

_s.stdout.reconfigure(encoding="utf-8", errors="replace")
from moneyball_core import dump_atomic

B = MB_ROOT
PREREG = _o.path.join(B, "big5_e_audio_interview_prereg.md")
CRITERION_OUT = _o.path.join(B, "audio_e_oof_criterion.json.candidate")
LOOK_OUT = _o.path.join(B, "interview_expressiveness_audio_e_validation.json")
K = 5000
ALPHAS = [1, 3, 10, 30, 100, 300, 1000]
SEED_PANELS = {
    "primary": [7, 17, 29, 43, 61],
    "rederive": [11, 23, 37, 47, 71],
}
CLIP_SALTS = {
    "primary": "audio-e-rel-primary-v1",
    "rederive": "audio-e-rel-rederive-v1",
}
PINS = {
    "data/members/b5_audio_cache.json": "4af340d1df9daa0c2df3b3f8ea29a189c384d5597d5de6ac980554664e9e9830",
    "data/members/b5_audio_clipcache.json": "b4f42a9448e3141dfcb4d9ee4a7f2260ec1611febdf110ff0849bbe0d6a3dd1f",
    "data/members/b5_audio_verify.json": "69b7cc5685c8adbf34a2382282f16b700bab0d04a8997a5611319abe22f56c84",
    "data/members/big5_blue_validated.json": "876bc2ecf8672477d586f3db34472cdb2cf61ee0573e30c4b909448cd0970a8f",
    "data/members/behavioral_roles_v2_candidate.json": "d6b7bd35e330549b40a1820b0709c9bf7e2768e3dd8456a3293b6793fec9f755",
    _o.path.join("fec", "members", "behavioral_roles_v2_study.py"): "07c79c7966d5c1fe8f8328ed2f971468142139800ec995f34b3f15cd58a2824d",
    "behavioral_dispositions.json": "57ca1e2b1731cbeed4411645c2b527a8db0136362c2d3d4654dda8faaa852e03",
    _o.path.join("fec", "legislators.csv"): "c5a6cbdbaae0bb19faa89852c29922809ececc70b97c149b4709588087e5e391",
    "data/engine/congestion.json": "ddc9d746600dbdfc60b76c54537662e19c758cbfb9d7a478a71b7bf90cd727a6",
}


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def assert_pins(stage):
    allowed = set(PINS)
    if stage == "criterion":
        allowed -= {"data/members/behavioral_roles_v2_candidate.json", "behavioral_dispositions.json", _o.path.join("fec", "members", "behavioral_roles_v2_study.py")}
    for relpath in sorted(allowed):
        actual = sha(_o.path.join(B, relpath))
        if actual != PINS[relpath]:
            raise SystemExit("input pin drift: %s expected %s got %s" % (relpath, PINS[relpath], actual))


def corr(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    good = np.isfinite(x) & np.isfinite(y)
    x, y = x[good], y[good]
    if len(x) < 5 or x.std() == 0 or y.std() == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def sb(r):
    return 2 * r / (1 + r) if np.isfinite(r) and r > -1 else float("nan")


def residual(values, controls):
    values, controls = np.asarray(values, float), np.asarray(controls, float)
    design = np.column_stack([np.ones(len(values)), controls])
    return values - design @ np.linalg.lstsq(design, values, rcond=None)[0]


def perm_p(x, y, seed):
    x, y = np.asarray(x, float), np.asarray(y, float)
    observed = corr(x, y)
    if not np.isfinite(observed):
        # Every `nan >= nan` is False, so an undefined correlation used to report ge=0,
        # i.e. the most significant p the permutation grid can express (1/(K+1)).
        return observed, float("nan")
    rng = np.random.default_rng(seed)
    ge = sum(corr(x, rng.permutation(y)) >= observed for _ in range(K))
    return observed, (ge + 1) / (K + 1)


def stable_half(value, salt):
    return hashlib.sha256((salt + "\0" + value).encode("utf-8", "surrogatepass")).digest()[0] & 1


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
    senate = {m: 1.0 if value == "Senate" else 0.0 for m, value in chamber.items()}
    return party, gender, senate


def z_matrix(rows, n, d):
    matrix = np.full((n, d), np.nan)
    indices = sorted(rows)
    if not indices:
        return matrix
    values = np.asarray([rows[index] for index in indices], float)
    mu = values.mean(axis=0)
    sdv = values.std(axis=0, ddof=1)
    sdv[sdv < 1e-12] = 1.0
    values = np.clip((values - mu) / sdv, -5.0, 5.0)
    for row_index, member_index in enumerate(indices):
        matrix[member_index] = values[row_index]
    return matrix


def audio_inputs():
    audio = json.load(open(_o.path.join(B, "data", "members", "b5_audio_cache.json"), encoding="utf-8"))
    blue = json.load(open(_o.path.join(B, "data", "members", "big5_blue_validated.json"), encoding="utf-8"))["traits"]
    party, gender, senate = metadata()
    names = sorted(
        member for member in audio["members"]
        if member in blue and blue[member].get("E") is not None
        and member in party and member in gender and member in senate
    )
    index = {member: i for i, member in enumerate(names)}
    x = np.asarray([audio["z"][member] for member in names], float)
    y = np.asarray([blue[member]["E"] for member in names], float)
    seconds = np.asarray([audio["seconds"][member] for member in names], float)

    clipcache = json.load(open(_o.path.join(B, "data", "members", "b5_audio_clipcache.json"), encoding="utf-8"))
    verify = json.load(open(_o.path.join(B, "data", "members", "b5_audio_verify.json"), encoding="utf-8"))
    d = len(audio["feature_names"])
    half_matrices = {}
    half_seconds = {}
    for split_name, salt in CLIP_SALTS.items():
        sums = [collections.defaultdict(lambda: np.zeros(d)), collections.defaultdict(lambda: np.zeros(d))]
        secs = [collections.Counter(), collections.Counter()]
        for relpath, row in clipcache["clips"].items():
            if not verify.get(relpath, {}).get("keep") or float(row.get("sec", 0)) < 1.0:
                continue
            member = relpath.split("/")[0]
            if member not in index:
                continue
            half = stable_half(relpath, salt)
            sec = float(row["sec"])
            sums[half][index[member]] += np.asarray(row["v"], float) * sec
            secs[half][index[member]] += sec
        matrices = []
        for half in (0, 1):
            rows = {
                member_index: sums[half][member_index] / secs[half][member_index]
                for member_index in secs[half]
                if secs[half][member_index] >= 90.0
            }
            matrices.append(z_matrix(rows, len(names), d))
        half_matrices[split_name] = matrices
        half_seconds[split_name] = secs
    controls = np.asarray(
        [[party[m], gender[m], senate[m], math.log1p(audio["seconds"][m])] for m in names], float
    )
    return names, x, y, seconds, controls, half_matrices, blue


def oof_predictions(x, y, half_matrices, seeds):
    n = len(y)
    full = np.full((len(seeds), n), np.nan)
    halves = {name: [np.full((len(seeds), n), np.nan), np.full((len(seeds), n), np.nan)] for name in half_matrices}
    for seed_index, seed in enumerate(seeds):
        for train, test in KFold(5, shuffle=True, random_state=seed).split(x):
            model = GridSearchCV(Ridge(), {"alpha": ALPHAS}, cv=3, scoring="neg_mean_squared_error")
            model.fit(x[train], y[train])
            full[seed_index, test] = model.predict(x[test])
            for split_name, matrices in half_matrices.items():
                for half in (0, 1):
                    good = test[np.isfinite(matrices[half][test]).all(axis=1)]
                    if len(good):
                        halves[split_name][half][seed_index, good] = model.predict(matrices[half][good])
    full_mean = np.nanmean(full, axis=0)
    half_mean = {name: [np.nanmean(values[half], axis=0) for half in (0, 1)] for name, values in halves.items()}
    return full_mean, half_mean


def criterion_panel(panel_name, predictions, half_predictions, names, y, controls, blue, seed):
    raw_r, raw_p = perm_p(predictions, y, seed)
    rx, ry = residual(predictions, controls), residual(y, controls)
    partial_r, partial_p = perm_p(rx, ry, seed + 1)
    hetero = {}
    for trait in ("O", "C", "A", "N"):
        indices = [i for i, member in enumerate(names) if blue[member].get(trait) is not None]
        hetero[trait] = {"r": corr(predictions[indices], [blue[names[i]][trait] for i in indices]), "n": len(indices)}
    reliability = {}
    for split_name, halves in half_predictions.items():
        good = np.isfinite(halves[0]) & np.isfinite(halves[1])
        half_r = corr(halves[0][good], halves[1][good])
        reliability[split_name] = {"n": int(good.sum()), "r_half": half_r, "r_sb": sb(half_r)}
    contamination = {
        "party_R": corr(predictions, controls[:, 0]),
        "female": corr(predictions, controls[:, 1]),
        "log_audio_seconds": corr(predictions, controls[:, 3]),
    }
    gates = {
        "coverage": len(names) >= 350,
        "convergent": raw_r >= 0.20 and raw_p < 0.05,
        "partial": partial_r >= 0.15 and partial_p < 0.05,
        "diagonal": raw_r > max(abs(row["r"]) for row in hetero.values()),
        "reliability": all(row["n"] >= 250 and row["r_sb"] >= 0.60 for row in reliability.values()),
        "contamination": max(abs(value) for value in contamination.values()) <= 0.20,
    }
    return {
        "panel": panel_name,
        "n": len(names),
        "raw_r": raw_r,
        "raw_p": raw_p,
        "partial_r": partial_r,
        "partial_p": partial_p,
        "heterotrait": hetero,
        "reliability": reliability,
        "contamination": contamination,
        "gates": gates,
        "passed": all(gates.values()),
    }


def build_criterion():
    names, x, y, seconds, controls, half_matrices, blue = audio_inputs()
    panels, predictions = {}, {}
    for panel_index, (panel_name, seeds) in enumerate(SEED_PANELS.items()):
        full, halves = oof_predictions(x, y, half_matrices, seeds)
        predictions[panel_name] = full
        panels[panel_name] = criterion_panel(panel_name, full, halves, names, y, controls, blue, 2026071400 + panel_index * 10)
    repeat_r = corr(predictions["primary"], predictions["rederive"])
    gates = {
        "primary": panels["primary"]["passed"],
        "rederive": panels["rederive"]["passed"],
        "panel_repeat": repeat_r >= 0.90,
    }
    return {
        "method": "nested cross-fitted Ridge eGeMAPSv02 -> promoted Blue E; two five-seed panels",
        "names": names,
        "members": {
            member: {
                "primary": float(predictions["primary"][i]),
                "rederive": float(predictions["rederive"][i]),
                "audio_seconds": float(seconds[i]),
            }
            for i, member in enumerate(names)
        },
        "panels": panels,
        "panel_repeat_r": repeat_r,
        "gates": gates,
        "qualified": all(gates.values()),
    }


def print_criterion(result):
    print("=== CLEAN-AUDIO E CRITERION GATE ===")
    for name in ("primary", "rederive"):
        row = result["panels"][name]
        print("  %s n=%d raw=%+.3f p=%.4f partial=%+.3f p=%.4f" % (name, row["n"], row["raw_r"], row["raw_p"], row["partial_r"], row["partial_p"]))
        print("    heterotrait: %s" % " | ".join("%s=%+.3f(n=%d)" % (trait, item["r"], item["n"]) for trait, item in row["heterotrait"].items()))
        print("    reliability: %s" % " | ".join("%s SB=%.3f(n=%d)" % (split, item["r_sb"], item["n"]) for split, item in row["reliability"].items()))
        print("    contamination: %s" % " | ".join("%s=%+.3f" % item for item in row["contamination"].items()))
        print("    gates: %s -> %s" % (", ".join("%s=%s" % (gate, "PASS" if value else "MISS") for gate, value in row["gates"].items()), "PASS" if row["passed"] else "MISS"))
    print("  panel-repeat r=%+.3f -> %s" % (result["panel_repeat_r"], "PASS" if result["gates"]["panel_repeat"] else "MISS"))
    print("  CRITERION VERDICT: %s" % ("QUALIFIED" if result["qualified"] else "REJECTED"))
    print("  AUDIO_CRITERION_SHA256: %s" % sha(CRITERION_OUT))


def role_test(role, audio_scores, controls, seed):
    raw_r, raw_p = perm_p(role, audio_scores, seed)
    rx, ry = residual(role, controls), residual(audio_scores, controls)
    partial_r, partial_p = perm_p(rx, ry, seed + 1)
    return {"raw_r": raw_r, "raw_p": raw_p, "partial_r": partial_r, "partial_p": partial_p}


def look_panel(panel_name, criterion, roles, dispositions, party, gender, senate, seed):
    names = sorted(
        member for member, row in criterion["members"].items()
        if member in roles and roles[member].get("expressiveness_v2") is not None
        and roles[member].get("dominance_v2") is not None
        and member in dispositions and dispositions[member].get("reactivity_z") is not None
        and dispositions[member].get("visibility_z") is not None
        and member in party and member in gender and member in senate
    )
    audio = np.asarray([criterion["members"][member][panel_name] for member in names], float)
    controls = np.asarray(
        [[party[m], gender[m], senate[m], math.log1p(roles[m]["qualifying_turns"]), math.log1p(criterion["members"][m]["audio_seconds"])] for m in names], float
    )
    own = role_test(np.asarray([roles[m]["expressiveness_v2"] for m in names], float), audio, controls, seed)
    rival_maps = {
        "dominance_v2": {m: roles[m]["dominance_v2"] for m in names},
        "reactivity": {m: dispositions[m]["reactivity_z"] for m in names},
        "visibility": {m: dispositions[m]["visibility_z"] for m in names},
    }
    rivals = {
        name: role_test(np.asarray([values[m] for m in names], float), audio, controls, seed + 10 + index)
        for index, (name, values) in enumerate(rival_maps.items())
    }
    gates = {
        "coverage": len(names) >= 230,
        "raw": own["raw_r"] >= 0.15 and own["raw_p"] < 0.05,
        "partial": own["partial_r"] >= 0.10 and own["partial_p"] < 0.05,
        "specificity": own["partial_r"] > max(abs(row["partial_r"]) for row in rivals.values()),
    }
    return {"n": len(names), "own": own, "rivals": rivals, "gates": gates, "passed": all(gates.values())}


def look(criterion, criterion_hash):
    text = open(PREREG, encoding="utf-8").read()
    if "STATUS: LOCKED" not in text or "\n## RESULT-AUDIO-E" in text:
        raise SystemExit("LOOK REFUSED: prereg state")
    match = re.search(r"^AUDIO_CRITERION_SHA256:\s*`?([0-9a-f]{64})`?\s*$", text, re.M | re.I)
    if not match or match.group(1).lower() != criterion_hash:
        raise SystemExit("LOOK REFUSED: committed criterion hash absent or mismatched")
    if not criterion.get("qualified"):
        raise SystemExit("LOOK REFUSED: audio criterion rejected")
    roles = json.load(open(_o.path.join(B, "data", "members", "behavioral_roles_v2_candidate.json"), encoding="utf-8"))["members"]
    dispositions = json.load(open(_o.path.join(B, "behavioral_dispositions.json"), encoding="utf-8"))["members"]
    party, gender, senate = metadata()
    panels = {
        name: look_panel(name, criterion, roles, dispositions, party, gender, senate, 2026071500 + index * 20)
        for index, name in enumerate(("primary", "rederive"))
    }
    passed = all(row["passed"] for row in panels.values())
    result = {"criterion_sha256": criterion_hash, "panels": panels, "passed": passed}
    dump_atomic(result, LOOK_OUT, indent=1)
    print("=== CLEAN-AUDIO E x INTERVIEW EXPRESSIVENESS — THE ONE LOOK ===")
    for name, row in panels.items():
        own = row["own"]
        print("  %s n=%d raw=%+.3f p=%.4f partial=%+.3f p=%.4f" % (name, row["n"], own["raw_r"], own["raw_p"], own["partial_r"], own["partial_p"]))
        print("    rivals partial: %s" % " | ".join("%s=%+.3f" % (rival, values["partial_r"]) for rival, values in row["rivals"].items()))
        print("    gates: %s -> %s" % (", ".join("%s=%s" % (gate, "PASS" if value else "MISS") for gate, value in row["gates"].items()), "PASS" if row["passed"] else "MISS"))
    print("  FINAL VERDICT: %s" % ("PASS — source-disjoint cross-modal interview-E convergence" if passed else "MISS — reliable speaking style only"))
    print("wrote interview_expressiveness_audio_e_validation.json (consumed by nothing)")


def simple_oof(x, y, seed):
    predictions = np.full(len(y), np.nan)
    for train, test in KFold(5, shuffle=True, random_state=seed).split(x):
        model = GridSearchCV(Ridge(), {"alpha": [1, 10, 100]}, cv=3, scoring="neg_mean_squared_error")
        model.fit(x[train], y[train])
        predictions[test] = model.predict(x[test])
    return predictions


def simulate():
    rng = np.random.default_rng(2026071399)
    n, d = 250, 20
    gender = rng.normal(size=n)
    latent = rng.normal(size=n)
    planted_x = rng.normal(size=(n, d))
    planted_x[:, :5] += latent[:, None]
    planted_y = latent + 0.4 * gender + rng.normal(scale=0.8, size=n)
    planted = simple_oof(planted_x, planted_y, 7)
    pr, pp = perm_p(residual(planted, gender[:, None]), residual(planted_y, gender[:, None]), 1)
    null_y = rng.normal(size=n)
    null = simple_oof(rng.normal(size=(n, d)), null_y, 9)
    nr, npv = perm_p(null, null_y, 2)
    confound_x = rng.normal(size=(n, d))
    confound_x[:, :5] += gender[:, None]
    confound_y = gender + rng.normal(scale=0.3, size=n)
    confound = simple_oof(confound_x, confound_y, 11)
    cr, cp = perm_p(residual(confound, gender[:, None]), residual(confound_y, gender[:, None]), 3)
    checks = {
        "planted_admitted": pr >= 0.15 and pp < 0.05,
        "null_refused": not (nr >= 0.15 and npv < 0.05),
        "gender_only_refused": not (cr >= 0.15 and cp < 0.05),
    }
    print("=== AUDIO/INTERVIEW SYNTHETIC BATTERY ===")
    print("  planted partial r=%+.3f p=%.4f" % (pr, pp))
    print("  null raw        r=%+.3f p=%.4f" % (nr, npv))
    print("  gender-only partial r=%+.3f p=%.4f" % (cr, cp))
    for name, passed in checks.items():
        print("  %-28s %s" % (name, "PASS" if passed else "FAIL"))
    if not all(checks.values()):
        raise SystemExit("synthetic battery refused")
    print("BATTERY PASS")


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
    assert_pins("criterion" if args.criterion else "look")
    simulate()
    if args.criterion:
        result = build_criterion()
        dump_atomic(result, CRITERION_OUT, indent=1)
        print_criterion(result)
        print("wrote audio_e_oof_criterion.json.candidate (consumed by nothing)")
        return
    if not _o.path.exists(CRITERION_OUT):
        raise SystemExit("LOOK REFUSED: criterion artifact absent")
    criterion_hash = sha(CRITERION_OUT)
    criterion = json.load(open(CRITERION_OUT, encoding="utf-8"))
    look(criterion, criterion_hash)


if __name__ == "__main__":
    main()
