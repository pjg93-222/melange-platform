"""Six fixed PAC remaining-dollar rules and chronological chamber selection.

No political features. Original input intervals and signed observations are
unchanged. Shared pace is a conservative D/R marginal-box normalization, not a
claim that the marginal endpoints are jointly attainable.
"""
from __future__ import annotations

import math
from statistics import median

from predict_interval import SECTORS, predict as original_predict

FAMILIES = ("baseline", "mean_remaining", "recent_half", "phase_half",
            "senate_six_year_half", "pace_sqrt")
PROTOCOL = {
    "id": "pac-baseline-refinement-v1", "families": list(FAMILIES),
    "baseline": "historical_remainder", "political_inputs": False,
    "mean_remaining": "observed plus arithmetic mean of three direct historical remainders",
    "recent_half": "observed plus half median3 remainder and half t-2 remainder",
    "phase_half": "observed plus half median3 remainder and half t-4 remainder",
    "senate_six_year_half": "Senate: observed plus half median3 and half t-6 remainder; House unchanged baseline",
    "pace_sqrt": "shared chamber factor clip(sqrt(sum current D/R observed / median historical same-cycle sum D/R observed),0.5,2); multiply each party median3 remainder, add its observed interval",
    "pace_bounds": "quotient lower=current sum lower / historical median upper; upper=current sum upper / historical median lower; monotone sqrt and clip",
    "pace_domain": "historical median observed lower>0; current observed sum lower>=0; both party median remainder lowers>=0; otherwise full baseline",
    "other_domain": "negative candidate forecast lower or invalid numeric result retains full frozen baseline; signed source observations are never clipped",
    "selection": "one family per chamber, pooled all sectors/parties/horizons; only earlier target outcomes; fewer than two earlier origins uses baseline",
    "ranking": "maximize certified20 count, then certified10 count, then minimize mean upper absolute error / positive prior D+R marginal midpoint sum; baseline-first fixed family order breaks ties",
    "normalized_error_tie_tolerance": 1e-12,
    "deployment_gate": "nested2022/2024 cert20 strictly improves, or equal cert20 with better cert10; neither chamber cert20 may decline; pooled mean normalized upper absolute error must not worsen",
    "target_cycles": [2018, 2020, 2022, 2024], "horizons": ["m12", "m15", "m18", "m21"],
    "current_target": 2026, "final_selection": "apply the same ranking using all four earlier origins",
    "limitations": ["Previously examined outcomes and reconstructed source vintages; no untouched holdout.",
        "Horizons repeat final election outcomes and are not independent elections.",
        "Intervals cover source-classification ambiguity, not future forecast or model-selection uncertainty.",
        "Six-year Senate analogue does not match specific seats or special elections.",
        "Research deployment gate is not statistical significance or a future accuracy guarantee."],
}


def _bounds(value):
    return value["lo_cents"], value["hi_cents"]


def _bound(lo, hi):
    if not all(math.isfinite(v) for v in (lo, hi)) or lo > hi:
        raise ValueError("invalid interval arithmetic")
    return {"lo_cents": lo, "hi_cents": hi}


def _clip_sqrt(value):
    return min(2.0, max(.5, math.sqrt(value)))


def predict(frame, family):
    """One closed, truth-free feature frame in; both party forecasts out."""
    if family not in FAMILIES:
        raise ValueError("unknown baseline family")
    original = original_predict(frame, "historical_remainder")
    baseline = {r["party"]: _bound(r["prediction_lo_cents"], r["prediction_hi_cents"]) for r in original}
    remaining = {p: tuple(median(old["remaining"][p][e] for old in frame["history"])
                          for e in ("lo_cents", "hi_cents")) for p in ("D", "R")}
    scale = sum(sum(_bounds(frame["history"][-1]["final"][p])) / 2 for p in ("D", "R"))
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError("positive finite prior scale is required")
    factor, shared_reason = None, None
    if family == "pace_sqrt":
        observed = tuple(sum(frame["observed"][p][e] for p in ("D", "R")) for e in ("lo_cents", "hi_cents"))
        history = tuple(median(sum(old["observed"][p][e] for p in ("D", "R")) for old in frame["history"])
                        for e in ("lo_cents", "hi_cents"))
        if history[0] <= 0 or observed[0] < 0 or any(v[0] < 0 for v in remaining.values()):
            shared_reason = "pace domain is not certified over the full box"
        else:
            factor = (_clip_sqrt(observed[0] / history[1]), _clip_sqrt(observed[1] / history[0]))
    output = []
    for p in ("D", "R"):
        base = baseline[p]
        lo, hi = _bounds(base)
        rlo, rhi = remaining[p]
        reason, trace = shared_reason, {"historical_median_remaining": _bound(rlo, rhi)}
        unchanged = family == "baseline" or (family == "senate_six_year_half" and frame["chamber"] == "House")
        if not unchanged and reason is None:
            if family == "mean_remaining":
                rlo, rhi = (sum(old["remaining"][p][e] for old in frame["history"]) / 3 for e in ("lo_cents", "hi_cents"))
            elif family in ("recent_half", "phase_half", "senate_six_year_half"):
                index = {"recent_half": -1, "phase_half": 1, "senate_six_year_half": 0}[family]
                other = _bounds(frame["history"][index]["remaining"][p])
                rlo, rhi = .5 * rlo + .5 * other[0], .5 * rhi + .5 * other[1]
                trace["analogue_cycle"] = frame["history"][index]["cycle"]
            elif family == "pace_sqrt":
                rlo, rhi = rlo * factor[0], rhi * factor[1]
                trace["shared_pace_factor"] = _bound(*factor)
            candidate = tuple(frame["observed"][p][e] + r for e, r in zip(("lo_cents", "hi_cents"), (rlo, rhi)))
            if not all(math.isfinite(v) for v in candidate) or candidate[0] < 0 or candidate[0] > candidate[1]:
                reason = "candidate interval outside the certified formula domain"
            else:
                lo, hi = candidate
                trace["candidate_remaining"] = _bound(rlo, rhi)
        output.append({**{k: frame[k] for k in ("sector", "chamber", "target", "horizon", "issue", "cut")},
                       "party": p, "family": family, "prediction": _bound(lo, hi), "baseline": base,
                       "prior_scale_cents": scale, "fallback_reason": reason,
                       "original_baseline_fallback_reason": next(r["uncertainty_rule_fallback"] for r in original if r["party"] == p),
                       "trace": trace})
    return output


def score(row, truth):
    lo, hi = _bounds(row["prediction"])
    yl, yh = _bounds(truth)
    error = max(abs(lo - yh), abs(hi - yl))
    return dict(row, truth=_bound(yl, yh), certified_within10=yl > 0 and lo >= .9 * yh and hi <= 1.1 * yl,
                certified_within20=yl > 0 and lo >= .8 * yh and hi <= 1.2 * yl,
                absolute_error_upper_cents=error, normalized_absolute_error_upper=error / row["prior_scale_cents"],
                ape_upper_bound=max(abs(p - y) / y for p in (lo, hi) for y in (yl, yh)) if yl > 0 else None)


def metrics(rows):
    if not rows:
        raise ValueError("empty metric population")
    n = len(rows)
    return dict(n=n, certified_within20=sum(r["certified_within20"] for r in rows),
                certified_within10=sum(r["certified_within10"] for r in rows),
                mean_normalized_upper_error=math.fsum(r["normalized_absolute_error_upper"] for r in rows) / n,
                mean_absolute_error_upper_dollars=math.fsum(r["absolute_error_upper_cents"] for r in rows) / n / 100,
                new_domain_fallback_count=sum(r["fallback_reason"] is not None for r in rows),
                original_baseline_fallback_count=sum(r.get("original_baseline_fallback_reason") is not None for r in rows))


def select(scored, chamber, before_target):
    history = [r for r in scored if r["chamber"] == chamber and r["target"] < before_target]
    years = sorted({r["target"] for r in history})
    if len(years) < 2:
        return dict(chamber=chamber, target=before_target, family="baseline", earlier_origins=years,
                    reason="fewer than two earlier origins", comparisons=[])
    key_fields = ("sector", "chamber", "target", "horizon", "party")
    identity_sets = []
    for family in FAMILIES:
        members = [r for r in history if r["family"] == family]
        identities = {tuple(r[k] for k in key_fields) for r in members}
        if len(identities) != len(members):
            raise ValueError("duplicate selection identity")
        identity_sets.append(identities)
    if any(s != identity_sets[0] for s in identity_sets[1:]):
        raise ValueError("selection identity populations differ")
    comparisons = [dict(family=family, **metrics([r for r in history if r["family"] == family])) for family in FAMILIES]
    if len({r["n"] for r in comparisons}) != 1:
        raise ValueError("selection populations differ")
    best = comparisons[0]
    for candidate in comparisons[1:]:
        primary = candidate["certified_within20"], candidate["certified_within10"]
        current = best["certified_within20"], best["certified_within10"]
        if primary > current or (primary == current and candidate["mean_normalized_upper_error"] < best["mean_normalized_upper_error"] - 1e-12):
            best = candidate
    return dict(chamber=chamber, target=before_target, family=best["family"], earlier_origins=years,
                reason="fixed ranking of earlier origins only", comparisons=comparisons)


def evaluate(saved):
    expected_frames = {(s, c, y, h) for s in SECTORS for c in ("House", "Senate")
                       for y in PROTOCOL["target_cycles"] for h in PROTOCOL["horizons"]}
    actual_frames = [tuple(r[k] for k in ("sector", "chamber", "target", "horizon")) for r in saved["feature_frames"]]
    expected_truths = {(s, c, y, p) for s in SECTORS for c in ("House", "Senate")
                       for y in PROTOCOL["target_cycles"] for p in ("D", "R")}
    actual_truths = [tuple(r[k] for k in ("sector", "chamber", "target", "party")) for r in saved["truth_intervals"]]
    if set(actual_frames) != expected_frames or len(actual_frames) != len(expected_frames):
        raise ValueError("exact unique 160-frame grid required")
    if set(actual_truths) != expected_truths or len(actual_truths) != len(expected_truths):
        raise ValueError("exact unique 80-truth grid required")
    truths = {(r["sector"], r["chamber"], r["target"], r["party"]): r for r in saved["truth_intervals"]}
    candidates = [score(r, truths[r["sector"], r["chamber"], r["target"], r["party"]])
                  for frame in saved["feature_frames"] for family in FAMILIES for r in predict(frame, family)]
    selections, nested = [], []
    for year in PROTOCOL["target_cycles"]:
        for chamber in ("House", "Senate"):
            decision = select(candidates, chamber, year)
            selections.append(decision)
            for row in candidates:
                if row["target"] == year and row["chamber"] == chamber and row["family"] == decision["family"]:
                    nested.append(dict(row, selected_family=row["family"], family="chronological_policy"))
    expected = len(saved["feature_frames"]) * 2
    candidate_keys = {tuple(r[k] for k in ("sector", "chamber", "target", "horizon", "party", "family")) for r in candidates}
    nested_keys = {tuple(r[k] for k in ("sector", "chamber", "target", "horizon", "party")) for r in nested}
    if (len(candidates) != expected * len(FAMILIES) or len(nested) != expected or
            len(candidate_keys) != len(candidates) or len(nested_keys) != len(nested)):
        raise ValueError("incomplete forecast grid")
    evaluated = [r for r in nested if r["target"] in (2022, 2024)]
    baseline = [r for r in candidates if r["family"] == "baseline" and r["target"] in (2022, 2024)]
    b, n = metrics(baseline), metrics(evaluated)
    improved = n["certified_within20"] > b["certified_within20"] or (n["certified_within20"] == b["certified_within20"] and n["certified_within10"] > b["certified_within10"])
    chamber_gate = {c: metrics([r for r in evaluated if r["chamber"] == c])["certified_within20"] >=
                       metrics([r for r in baseline if r["chamber"] == c])["certified_within20"] for c in ("House", "Senate")}
    error_gate = n["mean_normalized_upper_error"] <= b["mean_normalized_upper_error"] + 1e-12
    groups = []
    for family in (*FAMILIES, "chronological_policy"):
        source = nested if family == "chronological_policy" else [r for r in candidates if r["family"] == family]
        for scope, years in (("all_origins", [2018, 2020, 2022, 2024]), ("evaluated_origins", [2022, 2024]), ("warmup_origins", [2018, 2020])):
            for chamber in ("ALL", "House", "Senate"):
                for horizon in ("ALL", *PROTOCOL["horizons"]):
                    rows = [r for r in source if r["target"] in years and (chamber == "ALL" or r["chamber"] == chamber) and (horizon == "ALL" or r["horizon"] == horizon)]
                    groups.append(dict(family=family, scope=scope, chamber=chamber, horizon=horizon, **metrics(rows)))
    regression_groups = {}
    for row in candidates + nested:
        for target in (row["target"], "ALL"):
            for horizon in (row["horizon"], "ALL"):
                key = row["family"], row["sector"], row["chamber"], row["party"], target, horizon
                regression_groups.setdefault(key, []).append(row)
    regressions = [dict(zip(("family", "sector", "chamber", "party", "target", "horizon"), key), **metrics(rows))
                   for key, rows in regression_groups.items()]
    return dict(protocol=PROTOCOL, candidates=candidates, chronological_predictions=nested,
                selections=selections, metrics=groups,
                regression_metrics=regressions,
                deployment_gate=dict(passed=improved and all(chamber_gate.values()) and error_gate,
                                     tolerance_improved=improved, chamber_non_degradation=chamber_gate,
                                     normalized_error_non_degradation=error_gate, baseline=b, candidate=n),
                final_2026_choices=[select(candidates, c, 2026) for c in ("House", "Senate")])
