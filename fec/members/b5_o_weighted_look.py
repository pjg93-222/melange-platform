# -*- coding: utf-8 -*-
"""One-look script for big5_o_panel_reliability_prereg.md (2026-07-16, user-authorized).

Evaluates the FROZEN shipped O composite (big5_gold_anchored.json traits.O.members) against
reliability-weighted gold panels (senator weights from the appendix's preserved rater counts,
w = k*rho1/(1+(k-1)*rho1), rho1=0.25 fixed pre-look).  Criterion-side round: no refit, no
selection, the weighting is the only degree of freedom and it is pinned in the prereg.

Gate: every computing path (--look AND --rederive) refuses unless the prereg carries the locked
status marker, no newline-anchored '## RESULT-OW' heading, every pinned SHA-256 AND count matches,
and (for --look) the in-process battery passes.  No-arg = status print.  --simulate = battery
only.  --rederive = fresh-process recomputation for bar 6 (gated identically to the look; after
the RESULT is recorded it refuses, preserving one-look).

Writes NOTHING (stdout only).  Data reads resolve from paths.ROOT (set MONEYBALL_ROOT to the live
tree when running from a worktree).  No LLM calls; arithmetic only.

Adversarial review (pre-lock, 2026-07-16): all required changes incorporated — --rederive gated;
count pins implemented (with the shipped n>=30 element); battery recalibrated (plant .60 vs the
.30 recovery bar; NULL bound <=5/200 matching the measured 0.77% dual-dominance null rate;
confound-removal check made deterministic); full-RESULT re-derivation comparison; promised
secondaries implemented; covariate non-constancy abort added.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.b5_external_gold_validate import map_gold  # noqa: E402  (import-safe: defs + __main__ guard)
from paths import ROOT  # noqa: E402

CODE_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir))
PREREG = os.path.join(CODE_ROOT, "big5_o_panel_reliability_prereg.md")
RESULT_HEADING = "\n## RESULT-OW"
RHO1 = 0.25
PERM_K = 9999
PARTIAL_K = 5000
SEED_PERM = 20260716          # weighted_perm_p: SEED_PERM + panel_index
SEED_PARTIAL_BASE = 100       # wpartial: SEED_PERM + 100 + panel_index
BATTERY_PERM_K = 2000         # planted-check permutation K
BATTERY_PARTIAL_K = 500       # battery partial permutation K
TRAIT = "O"
HET_TRAITS = ("C", "E", "A", "ES")
BARS_R = 0.20
BARS_P = 0.10
BAR_PARTIAL = 0.10
MIN_PANEL_N = 30              # the shipped association-gate n element, enforced at the gate

EXPECTED_PINS = ("data/stance/big5_senate_expert_gold.json", "data/stance/big5_gold_anchored.json",
                 "data/engine/congestion.json", "fec/legislators.csv")


def abort(message):
    print("!! ABORT (not consumed): %s" % message)
    raise SystemExit(2)


# ---------------------------------------------------------------- weighted statistics (the
# complete list of new weighted statistics per the prereg)

def wmean(x, w):
    x = np.asarray(x, float); w = np.asarray(w, float)
    return float(np.sum(w * x) / np.sum(w))


def wcorr(x, y, w):
    x = np.asarray(x, float); y = np.asarray(y, float); w = np.asarray(w, float)
    mx, my = wmean(x, w), wmean(y, w)
    cov = np.sum(w * (x - mx) * (y - my))
    vx = np.sum(w * (x - mx) ** 2)
    vy = np.sum(w * (y - my) ** 2)
    if vx <= 0 or vy <= 0:
        return None
    return float(cov / math.sqrt(vx * vy))


def weighted_perm_p(x, y, w, seed, k=PERM_K):
    """One-sided: x fixed; (y, w) rows permuted together (the weight travels with its senator)."""
    x = np.asarray(x, float); y = np.asarray(y, float); w = np.asarray(w, float)
    observed = wcorr(x, y, w)
    rng = np.random.default_rng(seed)
    exceed = 0
    idx = np.arange(len(y))
    for _ in range(k):
        p = rng.permutation(idx)
        if wcorr(x, y[p], w[p]) >= observed:
            exceed += 1
    return observed, (exceed + 1) / (k + 1)


def wresid(v, Z, w):
    """Weighted least squares residual (intercept included)."""
    v = np.asarray(v, float); w = np.asarray(w, float)
    Z1 = np.column_stack([np.ones(len(v))] + [np.asarray(z, float) for z in Z])
    sw = np.sqrt(w)
    b, *_ = np.linalg.lstsq(Z1 * sw[:, None], v * sw, rcond=None)
    return v - Z1 @ b


def wpartial(x, y, Z, w, seed, k=PARTIAL_K):
    """Frisch-Waugh weighted partial r + one-sided exact perm p on the residual pairing."""
    rx, ry = wresid(x, Z, w), wresid(y, Z, w)
    observed = wcorr(rx, ry, w)
    rng = np.random.default_rng(seed)
    idx = np.arange(len(ry))
    exceed = 0
    for _ in range(k):
        p = rng.permutation(idx)
        if wcorr(rx, ry[p], w[p]) >= observed:
            exceed += 1
    return observed, (exceed + 1) / (k + 1)


def weight_of(k, rho1=RHO1):
    return k * rho1 / (1.0 + (k - 1) * rho1)


# ---------------------------------------------------------------- inputs

def load_root(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return json.load(handle)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def load_inputs():
    gold = load_root("data/stance/big5_senate_expert_gold.json")
    roster_rows = load_root("data/engine/congestion.json")
    roster = [row["member"] for row in roster_rows]
    panels, _ = map_gold(gold, roster)
    anchored = load_root("data/stance/big5_gold_anchored.json")
    trait_block = anchored["traits"][TRAIT]
    composite = {name: row[TRAIT] for name, row in (trait_block.get("members") or {}).items()}
    shipped_unweighted = {name: row["r"] for name, row
                          in trait_block["validation"]["panels"].items()}
    # Covariates from legislators.csv via the congestion bioguide join (congestion.json has NO
    # party field — adversarial-review catch; legislators party values are 'Republican'/
    # 'Democrat'/'Independent').
    by_bio = {}
    with open(os.path.join(ROOT, "fec", "legislators.csv"), encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("bioguide_id"):
                by_bio[row["bioguide_id"]] = row
    name_to_bio = {row["member"]: row["bioguide"] for row in roster_rows if row.get("bioguide")}
    female, party = {}, {}
    for name in roster:
        record = by_bio.get(name_to_bio.get(name, ""), {})
        female[name] = 1.0 if record.get("gender") == "F" else 0.0
        party[name] = 1.0 if record.get("party") == "Republican" else 0.0
    return panels, composite, female, party, shipped_unweighted


def panel_arrays(panel, composite, female, party):
    rows = []
    for name, record in sorted(panel.items()):
        value = record.get(TRAIT)
        if value is None or name not in composite:
            continue
        rows.append((name, composite[name], float(value), weight_of(record["ratings_n"]),
                     female[name], party[name], record))
    names = [row[0] for row in rows]
    x = np.asarray([row[1] for row in rows])
    y = np.asarray([row[2] for row in rows])
    w = np.asarray([row[3] for row in rows])
    Z = [np.asarray([row[4] for row in rows]), np.asarray([row[5] for row in rows])]
    records = [row[6] for row in rows]
    return names, x, y, w, Z, records


def assert_covariates_nondegenerate(panels, composite, female, party):
    """A silently degraded covariate join would delete bar 5's controls (the ES-confound history
    is why bar 5 exists) — hard abort unless each covariate has >=5 ones and >=5 zeros among each
    panel's evaluated members."""
    for panel_name, panel in sorted(panels.items()):
        _, _, _, _, Z, _ = panel_arrays(panel, composite, female, party)
        for label, column in zip(("female", "party_R"), Z):
            ones = int(np.sum(column == 1.0))
            zeros = int(np.sum(column == 0.0))
            if ones < 5 or zeros < 5:
                abort("covariate %s degenerate over %s evaluated members (ones=%d zeros=%d)"
                      % (label, panel_name, ones, zeros))


def evaluate_panel(panel_index, panel, composite, female, party):
    names, x, y, w, Z, records = panel_arrays(panel, composite, female, party)
    out = {"n": len(names)}
    if len(names) < 5:
        out.update(r=None, p=None, hetero={}, max_abs_heterotrait=None, dominant=False,
                   partial=None, partial_p=None)
        return out
    out["r"], out["p"] = weighted_perm_p(x, y, w, SEED_PERM + panel_index)
    hetero = {}
    for other in HET_TRAITS:
        pairs = [(xi, rec.get(other), wi) for xi, wi, rec in zip(x, w, records)
                 if rec.get(other) is not None]
        if len(pairs) >= 5:
            hx, hy, hw = map(np.asarray, zip(*pairs))
            hetero[other] = wcorr(hx, hy, hw)
    max_het = max((abs(v) for v in hetero.values() if v is not None), default=0.0)
    out["hetero"] = hetero
    out["max_abs_heterotrait"] = max_het
    out["dominant"] = out["r"] is not None and out["r"] > max_het
    out["partial"], out["partial_p"] = wpartial(x, y, Z, w,
                                                SEED_PERM + SEED_PARTIAL_BASE + panel_index)
    return out


def bars_pass(ins, aca):
    checks = {
        "bar1_ins_r": ins["r"] is not None and ins["r"] >= BARS_R,
        "bar2_ins_p": ins["p"] is not None and ins["p"] < BARS_P,
        "bar3_ins_dominance": bool(ins["dominant"]),
        "bar4_aca_reclear": (aca["r"] is not None and aca["r"] >= BARS_R and
                             aca["p"] is not None and aca["p"] < BARS_P and bool(aca["dominant"])),
        "bar5_partial_ins": ins["partial"] is not None and ins["partial"] >= BAR_PARTIAL,
        "bar5_partial_aca": aca["partial"] is not None and aca["partial"] >= BAR_PARTIAL,
    }
    return checks, all(checks.values())


# ---------------------------------------------------------------- prereg gate

def prereg_text():
    if not os.path.isfile(PREREG):
        return None
    with open(PREREG, encoding="utf-8") as handle:
        return handle.read()


def parse_pins(text):
    pins = {}
    for match in re.finditer(r"^- (\S+)\s+SHA-256 `([0-9a-f]{64}|<PIN>)`", text, re.M):
        pins[match.group(1)] = match.group(2)
    counts = None
    match = re.search(r"^- COUNTS insiders/academics/coverage `(\d+|<PIN>)`/`(\d+|<PIN>)`/`(\d+|<PIN>)`",
                      text, re.M)
    if match:
        counts = tuple(match.groups())
    return pins, counts


def observed_counts(panels, composite, female, party):
    ins = panel_arrays(panels["senate_insiders"], composite, female, party)[0]
    aca = panel_arrays(panels["academic_experts"], composite, female, party)[0]
    return len(ins), len(aca), len(composite)


def gate_status(panels=None, composite=None, female=None, party=None):
    text = prereg_text()
    if text is None:
        return "PREREG_ABSENT", {}
    if "STATUS: LOCKED" not in text:
        return "NOT_LOCKED", {}
    if RESULT_HEADING in text:
        return "LOOK_CONSUMED", {}
    pins, counts = parse_pins(text)
    if sorted(pins) != sorted(EXPECTED_PINS):
        return "PINS_MISSING", {"expected": EXPECTED_PINS, "parsed": sorted(pins)}
    if any(value == "<PIN>" for value in pins.values()) or counts is None or \
            any(value == "<PIN>" for value in counts):
        return "PINS_MISSING", pins
    drift = {}
    for name, expected in pins.items():
        path = os.path.join(ROOT, name.replace("/", os.sep))
        actual = sha256_of(path) if os.path.isfile(path) else "ABSENT"
        if actual != expected:
            drift[name] = (expected, actual)
    if drift:
        return "FINGERPRINT_DRIFT", drift
    if panels is not None:
        got = observed_counts(panels, composite, female, party)
        want = tuple(int(value) for value in counts)
        if got != want:
            return "FINGERPRINT_DRIFT", {"COUNTS": (want, got)}
        if got[0] < MIN_PANEL_N or got[1] < MIN_PANEL_N:
            return "FINGERPRINT_DRIFT", {"MIN_PANEL_N": (MIN_PANEL_N, got)}
    return "READY", pins


# ---------------------------------------------------------------- battery (synthetic/permuted only;
# the real composite x unpermuted-gold weighted pairing is computed nowhere in simulate mode)

def synth_gold_from(x, rng, target_r):
    noise = rng.standard_normal(len(x))
    xz = (x - x.mean()) / (x.std() or 1.0)
    return target_r * xz + math.sqrt(max(0.0, 1 - target_r ** 2)) * noise


def battery(panels, composite, female, party):
    results = {"planted": [], "null_pass_count": 0, "confound_resid_max": 0.0,
               "confound_only_refusals": [], "true_confound": []}
    ok = True
    # (a) PLANTED, seed 424242 (+draw), 5 draws: .60-target plant recovered at weighted r>=.30 and
    # perm p<.05 in BOTH panels in >=4/5 draws (2-sd margin per panel at these n).
    planted_ok = 0
    for draw in range(5):
        rng = np.random.default_rng(424242 + draw)
        draw_pass = True
        for panel_index, (panel_name, panel) in enumerate(sorted(panels.items())):
            _, x, _, w, _, _ = panel_arrays(panel, composite, female, party)
            g = synth_gold_from(x, rng, 0.60)
            r, p = weighted_perm_p(x, g, w, SEED_PERM + 200 + panel_index + draw,
                                   k=BATTERY_PERM_K)
            draw_pass = draw_pass and r is not None and r >= 0.30 and p < 0.05
        results["planted"].append(draw_pass)
        planted_ok += int(draw_pass)
    ok = ok and planted_ok >= 4
    # (b) NULL, seed 535353, 200 reps: whole gold rows permuted within panel (pairing destroyed);
    # joint r/dominance bars may pass in at most 5/200 (measured null dual rate 0.77% -> expected
    # ~1.5; a <=2 bound would fail CORRECT machinery ~20% of the time).
    rng = np.random.default_rng(535353)
    null_pass = 0
    for _ in range(200):
        joint = True
        for panel_name, panel in sorted(panels.items()):
            _, x, y, w, _, records = panel_arrays(panel, composite, female, party)
            perm = rng.permutation(len(y))
            r = wcorr(x, y[perm], w[perm])
            hetero = []
            for other in HET_TRAITS:
                pairs = [(x[i], records[perm[i]].get(other), w[perm[i]])
                         for i in range(len(records)) if records[perm[i]].get(other) is not None]
                if len(pairs) >= 5:
                    hx, hy, hw = map(np.asarray, zip(*pairs))
                    hetero.append(abs(wcorr(hx, hy, hw)))
            dominant = r is not None and r > max(hetero, default=0.0)
            joint = joint and (r is not None and r >= BARS_R and dominant)
        null_pass += int(joint)
    results["null_pass_count"] = null_pass
    ok = ok and null_pass <= 5
    # (c) CONFOUND REMOVAL, deterministic: for a criterion built purely from female/party, the
    # weighted residualization must remove BOTH covariates exactly (|wcorr(resid, Z_i, w)| < 1e-9).
    # The 5-draw threshold-refusal counts are reported only (not gating: at n~45 a stochastic
    # refusal test is a coin flip — reviewer change #3).
    resid_max = 0.0
    for draw in range(5):
        rng = np.random.default_rng(646464 + draw)
        draw_refused = True
        for panel_index, (panel_name, panel) in enumerate(sorted(panels.items())):
            _, x, _, w, Z, _ = panel_arrays(panel, composite, female, party)
            g = 0.8 * Z[0] + 0.8 * Z[1] + 0.05 * rng.standard_normal(len(x))
            rg = wresid(g, Z, w)
            for column in Z:
                if float(np.std(column)) > 0:
                    resid_max = max(resid_max, abs(wcorr(rg, column, w) or 0.0))
            partial, _ = wpartial(x, g, Z, w, SEED_PERM + 300 + panel_index + draw,
                                  k=BATTERY_PARTIAL_K)
            draw_refused = draw_refused and (partial is None or partial < BAR_PARTIAL)
        results["confound_only_refusals"].append(draw_refused)
    results["confound_resid_max"] = resid_max
    ok = ok and resid_max < 1e-9
    # (d) TRUE+CONFOUND, seed 757575 (+draw): .60 plant plus additive female/party loading -> the
    # bar-5 partial must ADMIT (>= +0.10) in >=4/5 draws.
    admitted = 0
    for draw in range(5):
        rng = np.random.default_rng(757575 + draw)
        draw_admit = True
        for panel_index, (panel_name, panel) in enumerate(sorted(panels.items())):
            _, x, _, w, Z, _ = panel_arrays(panel, composite, female, party)
            g = synth_gold_from(x, rng, 0.60) + 0.5 * Z[0] + 0.5 * Z[1]
            partial, _ = wpartial(x, g, Z, w, SEED_PERM + 400 + panel_index + draw,
                                  k=BATTERY_PARTIAL_K)
            draw_admit = draw_admit and partial is not None and partial >= BAR_PARTIAL
        results["true_confound"].append(draw_admit)
        admitted += int(draw_admit)
    ok = ok and admitted >= 4
    return ok, results


# ---------------------------------------------------------------- the look

def compute_look(panels, composite, female, party, shipped_unweighted):
    ordered = sorted(panels.items())
    out = {}
    for panel_index, (panel_name, panel) in enumerate(ordered):
        out[panel_name] = evaluate_panel(panel_index, panel, composite, female, party)
    ins, aca = out["senate_insiders"], out["academic_experts"]
    checks, passed = bars_pass(ins, aca)
    secondary = {}
    for panel_index, (panel_name, panel) in enumerate(ordered):
        names, x, y, w, Z, records = panel_arrays(panel, composite, female, party)
        # (i) sqrt(k) weighting: r, p, dominance (firewalled).
        wk = np.sqrt(np.asarray([rec["ratings_n"] for rec in records], float))
        sk_r, sk_p = weighted_perm_p(x, y, wk, SEED_PERM + 500 + panel_index, k=BATTERY_PERM_K)
        sk_het = []
        for other in HET_TRAITS:
            pairs = [(x[i], records[i].get(other), wk[i]) for i in range(len(records))
                     if records[i].get(other) is not None]
            if len(pairs) >= 5:
                hx, hy, hw = map(np.asarray, zip(*pairs))
                sk_het.append(abs(wcorr(hx, hy, hw)))
        secondary["sqrtk_" + panel_name] = {
            "r": sk_r, "p": sk_p,
            "dominant": sk_r is not None and sk_r > max(sk_het, default=0.0)}
        # (ii) shipped unweighted reproduction (drift check only).
        secondary["unweighted_" + panel_name] = {
            "recomputed_r": float(np.corrcoef(x, y)[0, 1]),
            "shipped_r": shipped_unweighted.get(panel_name)}
    # (iii) insider k>=2 unweighted subset (mechanism context only).
    names, x, y, w, Z, records = panel_arrays(panels["senate_insiders"], composite, female, party)
    keep = [i for i, rec in enumerate(records) if rec["ratings_n"] >= 2]
    if len(keep) >= 5:
        secondary["k2_unweighted_senate_insiders"] = {
            "r": float(np.corrcoef(x[keep], y[keep])[0, 1]), "n": len(keep)}
    return {"panels": out, "bars": checks, "pass": passed, "secondary_firewalled": secondary,
            "rho1": RHO1, "seed": SEED_PERM, "perm_k": PERM_K, "partial_k": PARTIAL_K}


def flat_stats(value, prefix="result"):
    """Recursively flatten the ENTIRE result for the bar-6 comparison (every reported statistic)."""
    flat = {}
    if isinstance(value, dict):
        for key in sorted(value):
            flat.update(flat_stats(value[key], prefix + "." + str(key)))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            flat.update(flat_stats(item, prefix + "." + str(index)))
    else:
        flat[prefix] = value
    return flat


def rederive_matches(mine, theirs):
    mismatch = {}
    for key in set(mine) | set(theirs):
        a, b = mine.get(key), theirs.get(key)
        if isinstance(a, bool) or isinstance(b, bool):
            same = isinstance(a, bool) == isinstance(b, bool) and a == b
        elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
            same = abs(a - b) < 1e-9
        else:
            same = a == b
        if not same:
            mismatch[key] = (a, b)
    return mismatch


def main():
    args = set(sys.argv[1:])
    if "--self-test" in args:
        w = weight_of(2)
        assert abs(w - (2 * .25 / 1.25)) < 1e-12
        x = np.array([1., 2., 3., 4., 5.]); y = np.array([1.1, 1.9, 3.2, 3.8, 5.1])
        ones = np.ones(5)
        assert abs(wcorr(x, y, ones) - float(np.corrcoef(x, y)[0, 1])) < 1e-12
        assert weighted_perm_p(x, y, ones, 1, k=500) == weighted_perm_p(x, y, ones, 1, k=500)
        rz = wresid(y, [x], ones)
        assert abs(wcorr(rz, x, ones)) < 1e-9
        flat = flat_stats({"a": {"b": [1, {"c": None}]}, "d": True})
        assert flat == {"result.a.b.0": 1, "result.a.b.1.c": None, "result.d": True}
        assert rederive_matches({"k": 1.0}, {"k": 1.0 + 1e-12}) == {}
        assert rederive_matches({"k": True}, {"k": 1.0}) != {}
        print("b5_o_weighted_look self-test: 6 passed, 0 failed")
        return 0

    panels, composite, female, party, shipped = load_inputs()
    status, detail = gate_status(panels, composite, female, party)

    if not args:
        print("prereg: %s" % PREREG)
        print("gate status: %s" % status)
        if status == "FINGERPRINT_DRIFT":
            for name, pair in detail.items():
                print("  DRIFT %s: %s" % (name, (pair,)))
        print("panels matched: %s | evaluable counts (ins/aca/coverage): %s" % (
            {name: len(panel) for name, panel in panels.items()},
            observed_counts(panels, composite, female, party)))
        return 0

    if "--rederive" in args:
        if status != "READY":
            print("!! REDERIVE REFUSED (%s): gated identically to the look" % status)
            return 2
        assert_covariates_nondegenerate(panels, composite, female, party)
        result = compute_look(panels, composite, female, party, shipped)
        print("REDERIVE " + json.dumps(flat_stats(result), sort_keys=True))
        return 0

    if "--simulate" in args or "--look" in args:
        assert_covariates_nondegenerate(panels, composite, female, party)
        ok, battery_results = battery(panels, composite, female, party)
        print("BATTERY %s %s" % ("PASS" if ok else "FAIL", json.dumps(battery_results)))
        if "--simulate" in args:
            return 0
        if not ok:
            print("!! LOOK REFUSED (not consumed): simulate battery failure")
            return 2
        if status != "READY":
            print("!! LOOK REFUSED (%s)" % status)
            return 2
        result = compute_look(panels, composite, female, party, shipped)
        proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--rederive"],
                              capture_output=True, text=True,
                              env=dict(os.environ, MONEYBALL_ROOT=ROOT))
        line = [l for l in proc.stdout.splitlines() if l.startswith("REDERIVE ")]
        rederived = json.loads(line[0][len("REDERIVE "):]) if line else {}
        mismatch = rederive_matches(flat_stats(result), rederived)
        if mismatch:
            print("!! DEFECT HALT: re-derivation mismatch %s" % json.dumps(sorted(mismatch)))
            return 3
        result["bars"]["bar6_rederivation"] = True
        verdict = "PASS" if result["pass"] else "MISS"
        print("=" * 72)
        print("RESULT-OW block (append verbatim under a new '## RESULT-OW (2026-07-16)' heading):")
        print(json.dumps(result, indent=1, sort_keys=True))
        print("VERDICT: %s" % verdict)
        return 0

    print("usage: b5_o_weighted_look.py [--self-test|--simulate|--look|--rederive]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
