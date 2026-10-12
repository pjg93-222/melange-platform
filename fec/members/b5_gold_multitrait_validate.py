# -*- coding: utf-8 -*-
"""Cross-validated Senate-gold audit and guarded Big Five composite builder.

The historical ratings are never used to score a held-out senator.  For each trait, leave-one-member-
out folds choose non-negative channel weights on the remaining rated senators; the resulting OOF
predictions are evaluated separately against the insider and academic panels.  Full-sample weights
are emitted only after the OOF verdict and are explicitly labeled gold-fitted.
"""
from __future__ import annotations

import itertools
import json
import math
import os
import sys
from collections import Counter

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from members.b5_external_gold_validate import map_gold, name_key, one_sided_permutation_p
from moneyball_core import dump_atomic
from paths import ROOT


OUT = os.path.join(ROOT, "data", "stance", "big5_gold_anchored.json")
TRAITS = ("O", "C", "E", "A", "N")
PANEL_NAMES = ("senate_insiders", "academic_experts")
MIN_PANEL_N = 30
MIN_PANEL_R = .20
MAX_P = .10
GRID_STEP = .10


def load(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as handle:
        return json.load(handle)


def corr(x, y):
    pairs = [(float(a), float(b)) for a, b in zip(x, y)
             if a is not None and b is not None and math.isfinite(float(a)) and math.isfinite(float(b))]
    if len(pairs) < 5:
        return None, len(pairs)
    a, b = map(np.asarray, zip(*pairs))
    if a.std() == 0 or b.std() == 0:
        return None, len(pairs)
    return float(np.corrcoef(a, b)[0, 1]), len(pairs)


def zmap(values):
    names = sorted(values)
    vector = np.asarray([float(values[name]) for name in names])
    sd = float(vector.std(ddof=1)) or 1.0
    mean = float(vector.mean())
    return {name: (float(values[name]) - mean) / sd for name in names}


def percentiles(values):
    import bisect
    ordered = sorted(values.values())
    denominator = max(1, len(ordered) - 1)
    return {name: round(bisect.bisect_left(ordered, value) / denominator, 3)
            for name, value in values.items()}


def simplex_weights(channel_names, minima=None):
    minima = minima or {}
    units = int(round(1 / GRID_STEP))
    minimum_units = [int(round(minima.get(name, 0) / GRID_STEP)) for name in channel_names]
    remaining = units - sum(minimum_units)
    if remaining < 0:
        return []
    out = []
    for cuts in itertools.combinations_with_replacement(range(len(channel_names)), remaining):
        counts = list(minimum_units)
        for index in cuts:
            counts[index] += 1
        out.append(tuple(round(count / units, 6) for count in counts))
    return sorted(set(out))


def gold_value(row, trait):
    value = row.get("ES" if trait == "N" else trait)
    if value is None:
        return None
    return -float(value) if trait == "N" else float(value)


def averaged_gold(panels, trait):
    values = {}
    for name in sorted(set().union(*(set(panel) for panel in panels.values()))):
        seen = [gold_value(panel[name], trait) for panel in panels.values()
                if name in panel and gold_value(panel[name], trait) is not None]
        if seen:
            values[name] = float(np.mean(seen))
    return values


def score_members(channels, weights, members):
    names = list(channels)
    return {member: sum(weights[i] * channels[name].get(member, 0.0)
                        for i, name in enumerate(names))
            for member in members}


def choose_weights(channels, target, training, minima=None):
    names = list(channels)
    candidates = simplex_weights(names, minima)
    best = None
    for weights in candidates:
        predicted = score_members(channels, weights, training)
        r, n = corr([predicted[name] for name in training], [target[name] for name in training])
        if r is None:
            continue
        # Stable deterministic tie-break: prefer higher validity, then more balanced, then lexical order.
        balance = -sum(weight * weight for weight in weights)
        key = (round(r, 12), round(balance, 12), tuple(-weight for weight in weights))
        if best is None or key > best[0]:
            best = (key, weights, r, n)
    if best is None:
        raise ValueError("no valid gold-weight solution")
    return best[1], best[2], best[3]


def oof_variant(channels, panels, trait, minima=None):
    target = averaged_gold(panels, trait)
    eligible = sorted(target)
    predictions = {}
    fold_weights = []
    for held_out in eligible:
        training = [name for name in eligible if name != held_out]
        weights, _, _ = choose_weights(channels, target, training, minima)
        predictions[held_out] = score_members(channels, weights, [held_out])[held_out]
        fold_weights.append(weights)
    final_weights, fit_r, fit_n = choose_weights(channels, target, eligible, minima)
    panel_results = {}
    for panel_index, panel_name in enumerate(PANEL_NAMES):
        panel = panels[panel_name]
        members = [name for name in panel if name in predictions and gold_value(panel[name], trait) is not None]
        same, n = corr([predictions[name] for name in members],
                       [gold_value(panel[name], trait) for name in members])
        row = {}
        for other in TRAITS:
            values = [gold_value(panel[name], other) for name in members]
            row[other] = corr([predictions[name] for name in members], values)[0]
        hetero = max(abs(value) for key, value in row.items() if key != trait and value is not None)
        if trait == "N":
            # N is the reverse pole of the appendix's ES column; row['N'] is already direction-correct.
            pass
        p = one_sided_permutation_p(
            [predictions[name] for name in members],
            [gold_value(panel[name], trait) for name in members],
            20260715 + panel_index + 17 * TRAITS.index(trait),
        )[1]
        panel_results[panel_name] = {
            "r": same, "n": n, "permutation_p_one_sided": p,
            "gold_row": row, "max_abs_heterotrait": hetero,
            "diagonal_dominant": same is not None and same > hetero,
        }
    worst = min(row["r"] if row["r"] is not None else -1 for row in panel_results.values())
    mean_r = float(np.mean([row["r"] for row in panel_results.values() if row["r"] is not None]))
    return {
        "channels": list(channels),
        "selected_weights": {name: final_weights[i] for i, name in enumerate(channels)},
        "full_gold_fit_r": fit_r, "full_gold_fit_n": fit_n,
        "fold_weight_mode": {name: Counter(fold_weights).most_common(1)[0][0][i]
                             for i, name in enumerate(channels)},
        "panels": panel_results, "worst_panel_r": worst, "mean_panel_r": mean_r,
        "predictions_are_out_of_fold": True,
    }, predictions


def raw_channels():
    blue = load("data/members/big5_blue_validated.json").get("traits") or {}
    pink = load("data/members/big5_llm_opus.json").get("traits") or {}
    dispositions = load("behavioral_dispositions.json").get("members") or {}
    roles = load("data/members/behavioral_roles_v2_candidate.json").get("members") or {}
    style = load("communication_style.json").get("members") or {}

    def blue_trait(key):
        return {name: row[key] for name, row in blue.items() if row.get(key) is not None}

    def pink_trait(key, reverse=False):
        out = {}
        for name, row in pink.items():
            value = (row.get("raw_z") or {}).get(key)
            if row.get("n_chunks", 0) > 0 and value is not None:
                out[name] = -float(value) if reverse else float(value)
        return out

    def disposition(key):
        return {name: row[key] for name, row in dispositions.items() if row.get(key) is not None}

    def role(key):
        return {name: row[key] for name, row in roles.items() if row.get(key) is not None}

    def exposure():
        return {name: row["public_exposure"]["role_adjusted_visibility_z"]
                for name, row in style.items() if (row.get("public_exposure") or {}).get("role_adjusted_visibility_z") is not None}

    raw = {
        "O": {"blue_text_o": blue_trait("O"), "pink_observer_o": pink_trait("O"),
              "legislative_breadth": disposition("breadth_z")},
        "C": {"blue_text_c": blue_trait("C"), "pink_observer_c": pink_trait("C"),
              "legislative_follow_through": disposition("diligence_z")},
        "E": {"blue_visibility_calibrated_e": blue_trait("E"), "pink_observer_e": pink_trait("E"),
              "interaction_dominance": role("dominance_v2"), "role_adjusted_visibility": exposure()},
        "A": {"blue_low_antagonism_a": blue_trait("A"), "pink_observer_a": pink_trait("A"),
              "cross_party_cooperation": disposition("cooperation_z")},
        "N": {"affective_content_n": blue_trait("N"), "pink_observer_n": pink_trait("ES", reverse=True),
              "behavioral_reactivity": disposition("reactivity_z")},
    }
    return {trait: {name: zmap(values) for name, values in channels.items()}
            for trait, channels in raw.items()}


def variants_for(trait, channels, panels):
    names = list(channels)
    baseline_names = names[:2]
    primary_minima = {name: .10 for name in baseline_names}
    baseline, baseline_predictions = oof_variant(
        {name: channels[name] for name in baseline_names}, panels, trait, primary_minima)
    extended, extended_predictions = oof_variant(channels, panels, trait, primary_minima)
    variants = {"blue_pink_baseline": baseline, "all_channels": extended}
    predictions = {"blue_pink_baseline": baseline_predictions, "all_channels": extended_predictions}
    if trait == "E":
        constrained, constrained_predictions = oof_variant(
            channels, panels, trait,
            {**primary_minima, "interaction_dominance": .10, "role_adjusted_visibility": .10},
        )
        variants["all_channels_both_behavioral_min_10pct"] = constrained
        predictions["all_channels_both_behavioral_min_10pct"] = constrained_predictions
        # Prefer a both-channel E only if it keeps the worst-panel OOF validity within .02 of baseline.
        selected = ("all_channels_both_behavioral_min_10pct"
                    if constrained["worst_panel_r"] >= baseline["worst_panel_r"] - .02
                    else max(variants, key=lambda key: (variants[key]["worst_panel_r"], variants[key]["mean_panel_r"])))
    else:
        selected = max(variants, key=lambda key: (variants[key]["worst_panel_r"], variants[key]["mean_panel_r"]))
    return selected, variants, predictions[selected]


def build():
    gold = load("data/stance/big5_senate_expert_gold.json")
    roster = [row["member"] for row in load("data/engine/congestion.json")]
    panels, unmatched = map_gold(gold, roster)
    channels_by_trait = raw_channels()
    output_traits = {}
    for trait in TRAITS:
        channels = channels_by_trait[trait]
        selected_name, variants, _ = variants_for(trait, channels, panels)
        selected = variants[selected_name]
        gates = {}
        association_gates = {}
        for panel_name, row in selected["panels"].items():
            association_gates[panel_name] = (row["n"] >= MIN_PANEL_N and row["r"] is not None and
                                             row["r"] >= MIN_PANEL_R and
                                             row["permutation_p_one_sided"] < MAX_P)
            gates[panel_name] = association_gates[panel_name] and row["diagonal_dominant"]
        fully_anchored = all(gates.values())
        active = any(association_gates.values())
        status = ("ACTIVE_EXTERNAL_GOLD_ANCHORED" if fully_anchored else
                  "ACTIVE_ONE_PANEL_ASSOCIATION_SUPPORTED" if active else
                  "WITHHELD_FAILED_GATES")
        # Full-roster scores require both independent text/observer channels. Optional behavioral
        # channels are centered and contribute zero when unobserved; missingness never becomes a low score.
        primary = list(channels)[:2]
        members = sorted(set(channels[primary[0]]) & set(channels[primary[1]]))
        selected_channels = {name: channels[name] for name in selected["channels"]}
        weights = tuple(selected["selected_weights"][name] for name in selected_channels)
        raw_scores = score_members(selected_channels, weights, members)
        standardized = zmap(raw_scores)
        pcts = percentiles(standardized)
        member_rows = {name: {trait: round(standardized[name], 6), "percentile": pcts[name]}
                       for name in members} if active else {}
        ablation = {}
        for channel in channels:
            if len(channels) <= 2:
                continue
            subset = {name: values for name, values in channels.items() if name != channel}
            result, _ = oof_variant(subset, panels, trait)
            ablation["without_" + channel] = {
                "panels": result["panels"], "worst_panel_r": result["worst_panel_r"],
                "mean_panel_r": result["mean_panel_r"],
            }
        output_traits[trait] = {
            "status": status,
            "scope": "public expert-perceived Big Five estimate; not self-report, diagnosis, or private-state measurement",
            "candidate_channels": list(channels), "selected_variant": selected_name,
            "selected_weights": selected["selected_weights"], "coverage_n": len(members),
            "members": member_rows, "validation": selected,
            "gates": gates, "association_gates": association_gates,
            "supporting_panels": [name for name, passed in association_gates.items() if passed],
            "strict_discriminant_panels": [name for name, passed in gates.items() if passed],
            "ablation": ablation,
            "gold_direction": "negative_expert_emotional_stability" if trait == "N" else "same_named_appendix_trait",
        }
        if trait == "E":
            output_traits[trait]["uses_interaction_dominance"] = selected["selected_weights"].get("interaction_dominance", 0) > 0
            output_traits[trait]["uses_role_adjusted_visibility"] = selected["selected_weights"].get("role_adjusted_visibility", 0) > 0
    return {
        "schema": "big5-gold-anchored-v2", "status": "ACTIVE_WITH_TRAIT_LEVEL_GATES",
        "design": {
            "validation": "leave-one-member-out channel-weight selection; held-out senator labels never enter their score",
            "weight_grid": GRID_STEP, "weights": "non-negative and sum to one",
            "activation_gates": {"minimum_panel_n": MIN_PANEL_N, "minimum_panel_r": MIN_PANEL_R,
                                 "maximum_one_sided_permutation_p": MAX_P,
                                 "one_panel_activation_requires_diagonal_dominance": False,
                                 "two_panel_external_gold_anchor_requires_diagonal_dominance": True,
                                 "trait_activation_rule": "at least one panel clears n/r/p association gates"},
            "optional_channel_missingness": "roster-centered zero contribution; never coded as low trait",
            "production_weights": "fit on all mapped gold after OOF validation; explicitly criterion-fitted",
        },
        "coverage": {"roster_n": len(roster), "panel_n": {name: len(panel) for name, panel in panels.items()},
                     "unmatched_historic_names": len(unmatched)},
        "traits": output_traits,
    }


def self_test():
    weights = simplex_weights(("a", "b", "c"), {"b": .1})
    assert weights and all(abs(sum(row) - 1) < 1e-9 and row[1] >= .1 for row in weights)
    assert gold_value({"ES": 6}, "N") == -6
    assert abs(sum(zmap({"a": 1, "b": 2, "c": 3}).values())) < 1e-9
    print("b5_gold_multitrait_validate self-test: 3 passed, 0 failed")


def main():
    if "--self-test" in sys.argv:
        self_test()
        return 0
    doc = build()
    dump_atomic(doc, OUT, indent=1)
    print("gold multitrait:")
    for trait, row in doc["traits"].items():
        panels = row["validation"]["panels"]
        print("  %s %-31s n=%d insider=%+.3f academic=%+.3f weights=%s" % (
            trait, row["status"], row["coverage_n"],
            panels["senate_insiders"]["r"], panels["academic_experts"]["r"],
            row["selected_weights"],
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
