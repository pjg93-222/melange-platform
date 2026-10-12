# -*- coding: utf-8 -*-
"""One-look script for big5_n_variant_parity_prereg.md (2026-07-16, user-authorized).

Measure-side round: two pre-declared constrained-floor variants of the N gold-anchored composite,
run through the SHIPPED leave-one-member-out OOF machinery imported verbatim from
fec/members/b5_gold_multitrait_validate.py.  Criterion untouched (both panels, unweighted).  Selection
rule pre-declared: greater (worst_panel_r, mean_panel_r).  Bars on the selected variant only.

Gate: every computing path (--look AND --rederive) refuses unless the prereg carries the locked
status marker, no newline-anchored '## RESULT-NV' heading, the pin-name set is exactly the six
declared files, every SHA-256 AND count pin matches, and (for --look) the in-process battery
passes.  No-arg = status print; --simulate battery only; --rederive = the gated fresh-process
bar-4 recomputation.

Writes NOTHING (stdout only).  Data reads resolve from paths.ROOT (set MONEYBALL_ROOT to the live
tree when running from a worktree).  No LLM calls; arithmetic only.

Adversarial review (pre-lock, 2026-07-16): all required changes incorporated — --rederive gated;
blue_pink_baseline drift-check secondary actually computed (reported-only, outside selection);
count pins parsed and enforced (with the shipped n>=30 element); pin-name set exactness; female/
party built from legislators.csv (congestion.json has NO party field — reviewer catch) with a
>=5-ones/>=5-zeros degeneracy abort; full-RESULT re-derivation comparison; battery plant raised
to .60 against the .30 recovery bar and the confound-removal check made deterministic (parity
with the O round's review).
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
from members.b5_external_gold_validate import map_gold  # noqa: E402
from members.b5_gold_multitrait_validate import averaged_gold, oof_variant, raw_channels  # noqa: E402
from paths import ROOT  # noqa: E402

CODE_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir))
PREREG = os.path.join(CODE_ROOT, "big5_n_variant_parity_prereg.md")
RESULT_HEADING = "\n## RESULT-NV"
TRAIT = "N"
SEED_PARTIAL = 20260716
PARTIAL_K = 5000
BAR_ACA_R = 0.20
BAR_INS_R = 0.30
BAR_P = 0.10
BAR_PARTIAL = 0.10
MIN_PANEL_N = 30

EXPECTED_PINS = ("data/stance/big5_senate_expert_gold.json", "data/members/big5_blue_validated.json",
                 "data/members/big5_llm_opus.json", "behavioral_dispositions.json",
                 "data/engine/congestion.json", "fec/legislators.csv")

VARIANTS = {
    "all_channels_reactivity_min_10pct": {
        "channels": ("affective_content_n", "pink_observer_n", "behavioral_reactivity"),
        "minima": {"affective_content_n": .10, "pink_observer_n": .10,
                   "behavioral_reactivity": .10},
    },
    "blue_pink_affective_min_30pct": {
        "channels": ("affective_content_n", "pink_observer_n"),
        "minima": {"affective_content_n": .30, "pink_observer_n": .10},
    },
}
# Reported-only drift check (never enters selection or bars): the shipped baseline construction.
BASELINE = {"channels": ("affective_content_n", "pink_observer_n"),
            "minima": {"affective_content_n": .10, "pink_observer_n": .10}}


def abort(message):
    print("!! ABORT (not consumed): %s" % message)
    raise SystemExit(2)


def rr(a, b):
    a = np.asarray(a, float); b = np.asarray(b, float)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def resid(v, Z):
    Z1 = np.column_stack([np.ones(len(v))] + [np.asarray(z, float) for z in Z])
    b, *_ = np.linalg.lstsq(Z1, np.asarray(v, float), rcond=None)
    return np.asarray(v, float) - Z1 @ b


def fw_partial(pred, y, Z, seed=SEED_PARTIAL, k=PARTIAL_K):
    """Frisch-Waugh partial + one-sided exact perm p (the b5_es_robustness idiom, restated
    locally because importing b5_n_matrix executes the corpus featurizer)."""
    rp, ry = resid(pred, Z), resid(y, Z)
    observed = rr(rp, ry)
    rng = np.random.default_rng(seed)
    null = np.array([rr(rp, rng.permutation(ry)) for _ in range(k)])
    return observed, float((np.sum(null >= observed) + 1) / (k + 1))


def gold_signed(record):
    value = record.get("ES")
    return None if value is None else -float(value)


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
    channels = raw_channels()[TRAIT]
    # Covariates from legislators.csv via the congestion bioguide join (congestion.json has NO
    # party field; legislators party values are 'Republican'/'Democrat'/'Independent').
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
    return panels, channels, female, party


def eligible_members(panels):
    return sorted(averaged_gold(panels, TRAIT))


def assert_covariates_nondegenerate(panels, female, party):
    members = eligible_members(panels)
    for label, mapping in (("female", female), ("party_R", party)):
        ones = sum(1 for name in members if mapping.get(name) == 1.0)
        zeros = sum(1 for name in members if mapping.get(name) == 0.0)
        if ones < 5 or zeros < 5:
            abort("covariate %s degenerate over eligible members (ones=%d zeros=%d)"
                  % (label, ones, zeros))


def run_variants(channels, panels):
    out = {}
    for name, spec in VARIANTS.items():
        subset = {channel: channels[channel] for channel in spec["channels"]}
        result, predictions = oof_variant(subset, panels, TRAIT, dict(spec["minima"]))
        out[name] = {"result": result, "predictions": predictions}
    selected = max(out, key=lambda name: (out[name]["result"]["worst_panel_r"],
                                          out[name]["result"]["mean_panel_r"]))
    return selected, out


def confound_partials(predictions, panels, female, party):
    partials = {}
    for panel_name, panel in sorted(panels.items()):
        members = [name for name in sorted(panel)
                   if name in predictions and gold_signed(panel[name]) is not None]
        pred = [predictions[name] for name in members]
        gold_vals = [gold_signed(panel[name]) for name in members]
        Z = [[female[name] for name in members], [party[name] for name in members]]
        partials[panel_name] = fw_partial(pred, gold_vals, Z)
    return partials


def bars_pass(selected_result, partials):
    panels_row = selected_result["panels"]
    aca = panels_row["academic_experts"]
    ins = panels_row["senate_insiders"]
    checks = {
        "bar1_aca": (aca["n"] >= MIN_PANEL_N and aca["r"] is not None and aca["r"] >= BAR_ACA_R and
                     aca["permutation_p_one_sided"] < BAR_P and aca["diagonal_dominant"]),
        "bar2_ins": (ins["n"] >= MIN_PANEL_N and ins["r"] is not None and ins["r"] >= BAR_INS_R and
                     ins["permutation_p_one_sided"] < BAR_P and ins["diagonal_dominant"]),
        "bar3_partial_ins": partials["senate_insiders"][0] >= BAR_PARTIAL,
        "bar3_partial_aca": partials["academic_experts"][0] >= BAR_PARTIAL,
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
    match = re.search(r"^- COUNTS insiders/academics/eligible `(\d+|<PIN>)`/`(\d+|<PIN>)`/`(\d+|<PIN>)`",
                      text, re.M)
    if match:
        counts = tuple(match.groups())
    return pins, counts


def gate_status(panels=None):
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
        got = (len(panels["senate_insiders"]), len(panels["academic_experts"]),
               len(eligible_members(panels)))
        want = tuple(int(value) for value in counts)
        if got != want:
            return "FINGERPRINT_DRIFT", {"COUNTS": (want, got)}
        if got[0] < MIN_PANEL_N or got[1] < MIN_PANEL_N:
            return "FINGERPRINT_DRIFT", {"MIN_PANEL_N": (MIN_PANEL_N, got)}
    return "READY", pins


# ---------------------------------------------------------------- battery (synthetic/permuted only)

def synthetic_panels_from(channels, panels, rng, target_r=0.60, confound=None):
    """Synthetic gold panels: ES column = -(planted signal); other columns independent noise."""
    mix = rng.dirichlet(np.ones(len(channels)))
    members = sorted(set.union(*(set(values) for values in channels.values())))
    index = {name: i for i, name in enumerate(members)}
    mixed = np.zeros(len(members))
    for weight, channel in zip(mix, channels):
        for name, value in channels[channel].items():
            mixed[index[name]] += weight * value
    mixed = (mixed - mixed.mean()) / (mixed.std() or 1.0)
    out = {}
    for panel_name, panel in panels.items():
        rows = {}
        for name in panel:
            if name not in index:
                continue
            signal = (target_r * mixed[index[name]] +
                      math.sqrt(max(0.0, 1 - target_r ** 2)) * rng.standard_normal())
            if confound is not None:
                signal += confound[0].get(name, 0.0) + confound[1].get(name, 0.0)
            row = {"ratings_n": panel[name]["ratings_n"], "ES": -signal}
            for other in ("O", "C", "E", "A"):
                row[other] = rng.standard_normal()
            rows[name] = row
        out[panel_name] = rows
    return out


def permuted_panels(panels, rng):
    out = {}
    for panel_name, panel in panels.items():
        names = sorted(panel)
        shuffled = list(rng.permutation(names))
        out[panel_name] = {name: panel[source] for name, source in zip(names, shuffled)}
    return out


def battery(channels, panels, female, party):
    results = {"planted": [], "null_pass_count": 0, "confound_resid_max": 0.0,
               "confound_only_refusals": [], "true_confound": []}
    ok = True
    # (a) PLANTED seed 424242 (+draw), 5 draws, single plant per draw, .60 target vs the .30
    # recovery bar (2-sd margin; the O review's calibration change applied for parity).
    planted_ok = 0
    for draw in range(5):
        rng = np.random.default_rng(424242 + draw)
        synth = synthetic_panels_from(channels, panels, rng)
        selected, variants = run_variants(channels, synth)
        rows = variants[selected]["result"]["panels"]
        draw_pass = all(row["r"] is not None and row["r"] >= 0.30 and
                        row["permutation_p_one_sided"] < 0.05 for row in rows.values())
        results["planted"].append({"selected": selected, "pass": draw_pass})
        planted_ok += int(draw_pass)
    ok = ok and planted_ok >= 4
    # (b) NULL seed 535353, 50 reps: permuted real gold; bars sans p (conservative); <= 1 pass.
    rng = np.random.default_rng(535353)
    null_pass = 0
    for _ in range(50):
        shuffled = permuted_panels(panels, rng)
        selected, variants = run_variants(channels, shuffled)
        rows = variants[selected]["result"]["panels"]
        aca, ins = rows["academic_experts"], rows["senate_insiders"]
        joint = (aca["r"] is not None and aca["r"] >= BAR_ACA_R and aca["diagonal_dominant"] and
                 ins["r"] is not None and ins["r"] >= BAR_INS_R and ins["diagonal_dominant"])
        null_pass += int(joint)
    results["null_pass_count"] = null_pass
    ok = ok and null_pass <= 1
    # (c) CONFOUND REMOVAL, deterministic: residualizing a purely confound-driven criterion must
    # remove BOTH covariates exactly; the 5-draw threshold-refusal counts are reported only.
    resid_max = 0.0
    for draw in range(5):
        rng = np.random.default_rng(646464 + draw)
        members = eligible_members(panels)
        synth = {}
        for panel_name, panel in panels.items():
            rows = {}
            for name in panel:
                signal = (0.9 * female.get(name, 0.0) + 0.9 * party.get(name, 0.0) +
                          0.05 * rng.standard_normal())
                row = {"ratings_n": panel[name]["ratings_n"], "ES": -signal}
                for other in ("O", "C", "E", "A"):
                    row[other] = rng.standard_normal()
                rows[name] = row
            synth[panel_name] = rows
        selected, variants = run_variants(channels, synth)
        partials = confound_partials(variants[selected]["predictions"], synth, female, party)
        for panel_name, panel in sorted(synth.items()):
            names = [n for n in sorted(panel)
                     if n in variants[selected]["predictions"] and gold_signed(panel[n]) is not None]
            gold_vals = np.asarray([gold_signed(panel[n]) for n in names])
            Z = [np.asarray([female[n] for n in names]), np.asarray([party[n] for n in names])]
            rg = resid(gold_vals, Z)
            for column in Z:
                if float(np.std(column)) > 0:
                    resid_max = max(resid_max, abs(rr(rg, column)))
        results["confound_only_refusals"].append(
            all(value[0] < BAR_PARTIAL for value in partials.values()))
    results["confound_resid_max"] = resid_max
    ok = ok and resid_max < 1e-9
    # (d) TRUE+CONFOUND seed 757575 (+draw): .60 plant + confound loading -> bar 3 admits >=4/5.
    admitted = 0
    for draw in range(5):
        rng = np.random.default_rng(757575 + draw)
        confound = ({name: 0.5 * value for name, value in female.items()},
                    {name: 0.5 * value for name, value in party.items()})
        synth = synthetic_panels_from(channels, panels, rng, confound=confound)
        selected, variants = run_variants(channels, synth)
        partials = confound_partials(variants[selected]["predictions"], synth, female, party)
        draw_admit = all(value[0] >= BAR_PARTIAL for value in partials.values())
        results["true_confound"].append(draw_admit)
        admitted += int(draw_admit)
    ok = ok and admitted >= 4
    return ok, results


# ---------------------------------------------------------------- the look

def compute_look(channels, panels, female, party):
    selected, variants = run_variants(channels, panels)
    partials = confound_partials(variants[selected]["predictions"], panels, female, party)
    checks, passed = bars_pass(variants[selected]["result"], partials)
    other = [name for name in variants if name != selected][0]
    baseline_subset = {channel: channels[channel] for channel in BASELINE["channels"]}
    baseline_result, _ = oof_variant(baseline_subset, panels, TRAIT, dict(BASELINE["minima"]))
    return {
        "selected_variant": selected,
        "selected": variants[selected]["result"],
        "confound_partials": {name: {"partial": value[0], "perm_p": value[1]}
                              for name, value in partials.items()},
        "bars": checks, "pass": passed,
        "secondary_firewalled": {
            "non_selected_variant": other,
            "non_selected": variants[other]["result"],
            "shipped_baseline_drift_check": {
                "variant": "blue_pink_baseline (reported only; outside selection and bars)",
                "panels": baseline_result["panels"],
                "selected_weights": baseline_result["selected_weights"]},
        },
    }


def flat_stats(value, prefix="result"):
    """Recursively flatten the ENTIRE result for the bar-4 comparison (every reported statistic)."""
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
        x = np.array([1., 2., 3., 4., 5., 6.])
        z = np.array([1., 0., 1., 0., 1., 0.])
        y = 2 * x + 0.1 * np.array([0.1, -0.2, 0.15, -0.05, 0.2, -0.1])
        partial, _ = fw_partial(x, y, [z], seed=1, k=200)
        assert partial > 0.9
        assert gold_signed({"ES": 6.0}) == -6.0
        assert fw_partial(x, y, [z], seed=1, k=200) == fw_partial(x, y, [z], seed=1, k=200)
        flat = flat_stats({"a": {"b": [1, {"c": None}]}, "d": True})
        assert flat == {"result.a.b.0": 1, "result.a.b.1.c": None, "result.d": True}
        assert rederive_matches({"k": 1.0}, {"k": 1.0 + 1e-12}) == {}
        assert sorted(EXPECTED_PINS) == sorted(set(EXPECTED_PINS))
        print("b5_n_variant_look self-test: 5 passed, 0 failed")
        return 0

    panels, channels, female, party = load_inputs()
    status, detail = gate_status(panels)

    if not args:
        print("prereg: %s" % PREREG)
        print("gate status: %s" % status)
        if status == "FINGERPRINT_DRIFT":
            for name, pair in detail.items():
                print("  DRIFT %s: %s" % (name, (pair,)))
        print("panels matched: %s | eligible n=%d | channel coverage: %s" % (
            {name: len(panel) for name, panel in panels.items()},
            len(eligible_members(panels)),
            {name: len(values) for name, values in channels.items()}))
        return 0

    if "--rederive" in args:
        if status != "READY":
            print("!! REDERIVE REFUSED (%s): gated identically to the look" % status)
            return 2
        assert_covariates_nondegenerate(panels, female, party)
        result = compute_look(channels, panels, female, party)
        print("REDERIVE " + json.dumps(flat_stats(result), sort_keys=True))
        return 0

    if "--simulate" in args or "--look" in args:
        assert_covariates_nondegenerate(panels, female, party)
        ok, battery_results = battery(channels, panels, female, party)
        print("BATTERY %s %s" % ("PASS" if ok else "FAIL", json.dumps(battery_results)))
        if "--simulate" in args:
            return 0
        if not ok:
            print("!! LOOK REFUSED (not consumed): simulate battery failure")
            return 2
        if status != "READY":
            print("!! LOOK REFUSED (%s)" % status)
            return 2
        result = compute_look(channels, panels, female, party)
        proc = subprocess.run([sys.executable, os.path.abspath(__file__), "--rederive"],
                              capture_output=True, text=True,
                              env=dict(os.environ, MONEYBALL_ROOT=ROOT))
        line = [l for l in proc.stdout.splitlines() if l.startswith("REDERIVE ")]
        rederived = json.loads(line[0][len("REDERIVE "):]) if line else {}
        mismatch = rederive_matches(flat_stats(result), rederived)
        if mismatch:
            print("!! DEFECT HALT: re-derivation mismatch %s" % json.dumps(sorted(mismatch)))
            return 3
        result["bars"]["bar4_rederivation"] = True
        verdict = "PASS" if result["pass"] else "MISS"
        print("=" * 72)
        print("RESULT-NV block (append verbatim under a new '## RESULT-NV (2026-07-16)' heading):")
        print(json.dumps(result, indent=1, sort_keys=True))
        print("VERDICT: %s" % verdict)
        return 0

    print("usage: b5_n_variant_look.py [--self-test|--simulate|--look|--rederive]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
