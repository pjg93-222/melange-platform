# -*- coding: utf-8 -*-
"""Criterion-side diagnostics over the frozen Senate gold (2026-07-16; read-only, look-free).

Reproduces every motivating number cited by big5_o_panel_reliability_prereg.md and
big5_n_variant_parity_prereg.md.  LEGALITY: everything here is criterion-vs-criterion,
document-fact, or synthetic-vs-criterion — no project instrument is ever paired with the gold
(that pairing is a look and lives only behind the prereg gates).

Sections: (1) insider-vs-academic cross-panel agreement per trait, overall and stratified by
insider rater count; (2) range restriction (gold SD full panel vs current-roster matched);
(3) Spearman-Brown disattenuation grid for the C agreement; (4) within-panel halo profiles;
(5) joint-null false-pass simulation (random member vectors vs the real gold, association +
dominance gates); (6) live reactivity_z-eligibility count vs the round-4 MEM_N >= 250 data gate.

Read-only; writes nothing; safe to run any time (uses paths.ROOT — set MONEYBALL_ROOT from a
worktree).
"""
from __future__ import annotations

import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.b5_external_gold_validate import map_gold, name_key  # noqa: E402
from paths import ROOT  # noqa: E402

TRAITS = ("O", "C", "E", "A", "ES")


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return json.load(handle)


def corr(pairs):
    if len(pairs) < 3:
        return None
    a, b = map(np.asarray, zip(*pairs))
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def main():
    gold = load("data/stance/big5_senate_expert_gold.json")
    records = gold["records"]
    insiders = {name_key(r["source_name"], True): r for r in records if r["panel"] == "senate_insiders"}
    academics = {name_key(r["source_name"], True): r for r in records if r["panel"] == "academic_experts"}
    both = sorted(set(insiders) & set(academics))
    roster = [row["member"] for row in load("data/engine/congestion.json")]
    panels, _ = map_gold(gold, roster)
    shared = sorted(set(panels["senate_insiders"]) & set(panels["academic_experts"]))

    print("(1) cross-panel agreement, stratified by insider ratings_n")
    scopes = (
        ("all-both-panel", both, lambda key: (insiders[key], academics[key])),
        ("roster-shared", shared, lambda name: (panels["senate_insiders"][name],
                                                panels["academic_experts"][name])),
    )
    for scope_name, keys, get in scopes:
        for trait in TRAITS:
            for label, keep in (("all", lambda i: True),
                                ("ins_k=1", lambda i: i["ratings_n"] == 1),
                                ("ins_k>=2", lambda i: i["ratings_n"] >= 2)):
                pairs = []
                for key in keys:
                    ins_row, aca_row = get(key)
                    if (ins_row.get(trait) is not None and aca_row.get(trait) is not None
                            and keep(ins_row)):
                        pairs.append((ins_row[trait], aca_row[trait]))
                value = corr(pairs)
                print("  %-14s %-2s %-8s r=%s n=%d" % (
                    scope_name, trait, label,
                    "%+.4f" % value if value is not None else "  --  ", len(pairs)))

    print("(2) range restriction: gold SD, full panel vs roster-matched")
    for panel_name, tag in (("senate_insiders", "ins"), ("academic_experts", "aca")):
        full_rows = [r for r in records if r["panel"] == panel_name]
        roster_rows = list(panels[panel_name].values())
        for trait in TRAITS:
            full = [r[trait] for r in full_rows if r.get(trait) is not None]
            matched = [r[trait] for r in roster_rows if r.get(trait) is not None]
            sd_full = float(np.std(full, ddof=1))
            sd_matched = float(np.std(matched, ddof=1))
            print("  %s %-2s full=%.3f(n=%d) roster=%.3f(n=%d) u=%.3f" % (
                tag, trait, sd_full, len(full), sd_matched, len(matched), sd_matched / sd_full))

    print("(3) C disattenuation grid (Spearman-Brown panel reliability from ratings_n)")
    for scope_name, observed, k_ins, k_aca in (
            ("roster-shared", .357,
             float(np.mean([panels["senate_insiders"][n]["ratings_n"] for n in shared])),
             float(np.mean([panels["academic_experts"][n]["ratings_n"] for n in shared]))),
            ("all-senators", .155,
             float(np.mean([r["ratings_n"] for r in records if r["panel"] == "senate_insiders"])),
             float(np.mean([r["ratings_n"] for r in records if r["panel"] == "academic_experts"])))):
        for rho1 in (.15, .25, .40):
            rel_i = k_ins * rho1 / (1 + (k_ins - 1) * rho1)
            rel_a = k_aca * rho1 / (1 + (k_aca - 1) * rho1)
            ceiling = math.sqrt(rel_i * rel_a)
            print("  %-13s rho1=%.2f rel=%.3f/%.3f ceiling=%.3f disattenuated=%.3f" % (
                scope_name, rho1, rel_i, rel_a, ceiling, observed / ceiling))

    print("(4) within-panel halo profile (trait x trait across senators)")
    for panel_name, tag in (("senate_insiders", "INS"), ("academic_experts", "ACA")):
        rows = [r for r in records if r["panel"] == panel_name]
        for t1 in TRAITS:
            line = []
            for t2 in TRAITS:
                pairs = [(r[t1], r[t2]) for r in rows
                         if r.get(t1) is not None and r.get(t2) is not None]
                value = corr(pairs)
                line.append("%s=%+.3f" % (t2, value) if value is not None else "%s=--" % t2)
            print("  %s %s: %s" % (tag, t1, "  ".join(line)))

    print("(5) joint-null false-pass simulation (4000 random vectors vs real gold)")
    rng = np.random.default_rng(20260716)
    union = sorted(set(panels["senate_insiders"]) | set(panels["academic_experts"]))
    for trait in ("N", "O", "C"):
        source = "ES" if trait == "N" else trait
        sign = -1.0 if trait == "N" else 1.0
        pass_both = pass_strict = 0
        sims = 4000
        panel_data = {}
        for panel_name, panel in panels.items():
            names = [n for n in union if n in panel and panel[n].get(source) is not None]
            panel_data[panel_name] = (names,
                                      np.asarray([sign * panel[n][source] for n in names]),
                                      {other: np.asarray([panel[n].get(other) if panel[n].get(other)
                                                          is not None else np.nan for n in names])
                                       for other in TRAITS if other != source})
        index = {name: i for i, name in enumerate(union)}
        for _ in range(sims):
            x_union = rng.standard_normal(len(union))
            ok_both = True
            ok_strict = True
            for names, gold_vec, hetero in panel_data.values():
                x = np.asarray([x_union[index[n]] for n in names])
                r = float(np.corrcoef(x, gold_vec)[0, 1])
                if not (r >= .20):
                    ok_both = ok_strict = False
                    break
                max_het = 0.0
                for values in hetero.values():
                    mask = ~np.isnan(values)
                    if mask.sum() >= 5:
                        max_het = max(max_het, abs(float(np.corrcoef(x[mask], values[mask])[0, 1])))
                if not (r > max_het):
                    ok_strict = False
            pass_both += int(ok_both)
            pass_strict += int(ok_both and ok_strict)
        print("  %s: P(assoc r>=.20 both panels)=%.4f  P(+dominance both)=%.4f  (%d sims)" % (
            trait, pass_both / sims, pass_strict / sims, sims))

    print("(6) round-4 data gate: reactivity_z-eligible count (turns>=30, non-null cutoff)")
    interactions = load("data/lobbying/interaction_features.json")["members"]
    eligible = sum(1 for row in interactions.values()
                   if (row.get("turns") or 0) >= 30 and row.get("cutoff") is not None)
    print("  eligible=%d of %d interaction members (gate: MEM_N >= 250 within the section-17 "
          "roster; the exact intersection needs the pinned round-4 constructor)" % (
              eligible, len(interactions)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
