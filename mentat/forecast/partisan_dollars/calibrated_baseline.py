"""Adaptive exploratory round 2: half-shrunk, earlier-cycle remainder calibration.

Midpoints define one fitted coefficient per chamber. Full source intervals are
scored conditional on that coefficient; they do not cover all possible refits.
"""
from __future__ import annotations

import math
from statistics import median

import baseline_refinement as ref

PROTOCOL = {
    "id": "pac-calibrated-baseline-v1", "adaptive_exploratory_round": 2,
    "designed_after": "pac-baseline-refinement-v1 run001 failed its chronological gate",
    "baseline": "unchanged historical_remainder", "alternative_policies": 1,
    "fit": "one alpha per chamber, shared by both parties and all industries/horizons; at least two earlier eligible election origins",
    "x": "midpoint of median3 direct historical remainder / positive prior D+R marginal midpoint sum",
    "z": "(truth midpoint - original baseline midpoint) / same normalization scale",
    "coefficient": "beta=clip(0.5*sum(w*x*z)/sum(w*x*x),-0.5,0.5); alpha=1+beta",
    "weights": "equal eligible election origins, then equal available cohorts, then equal available parties, then equal available horizons; all exclusions retained in scored grid",
    "zero_energy": "beta=0,alpha=1 with explicit reason; no epsilon",
    "eligibility": "original baseline has no prior-cycle fallback; baseline=observed+median remainder endpoints; finite positive normalization scale; nonnegative complete remaining interval",
    "prediction": "original observed interval plus positive alpha times full median3 remaining interval; signed observations never clipped; ineligible or negative final lower retains exact original baseline",
    "selection": "no family selection or parameter search; fixed half correction and clip",
    "normalization": "marginal midpoint sum is a normalization convention, not a verified joint D+R total",
    "scoring": "full marginal forecast/truth interval certification within10/20 conditional on fitted alpha",
    "gate": ref.PROTOCOL["deployment_gate"], "target_cycles": [2018, 2020, 2022, 2024],
    "limitations": ref.PROTOCOL["limitations"] + [
        "Designed after round001 results; earlier-only fitting does not make this untouched validation.",
        "Midpoint calibration is a training convention, not endpoint-squared-loss equivalence or uncertainty over all refits."],
}


def midpoint(value):
    return (value["lo_cents"] + value["hi_cents"]) / 2


def prepare_frame(frame):
    rows = ref.predict(frame, "baseline")
    for row in rows:
        row["observed"] = {e: frame["observed"][row["party"]][e] for e in ("lo_cents", "hi_cents")}
        row["remaining"] = {e: median(old["remaining"][row["party"]][e] for old in frame["history"])
                            for e in ("lo_cents", "hi_cents")}
    return rows


def eligibility(row):
    if row.get("original_baseline_fallback_reason") is not None:
        return "original baseline uses a prior-cycle fallback"
    scale = row["prior_scale_cents"]
    if not isinstance(scale, (float, int)) or isinstance(scale, bool) or not math.isfinite(scale) or scale <= 0:
        return "nonpositive or invalid normalization scale"
    r = row["remaining"]
    if not all(math.isfinite(r[e]) for e in ("lo_cents", "hi_cents")) or r["lo_cents"] < 0 or r["lo_cents"] > r["hi_cents"]:
        return "remaining interval is not nonnegative and ordered"
    for e in ("lo_cents", "hi_cents"):
        if not math.isclose(row["baseline"][e], row["observed"][e] + r[e], rel_tol=0, abs_tol=1e-7):
            return "baseline is not observed plus the direct median remainder"
    return None


def fit(rows, chamber, target):
    history = [r for r in rows if r["chamber"] == chamber and r["target"] < target]
    accepted = [r for r in history if eligibility(r) is None]
    years = sorted({r["target"] for r in accepted})
    base = dict(chamber=chamber, target=target, alpha=1.0, beta=0.0, unclipped_beta=0.0,
                training_cycles=years, training_rows=len(accepted), excluded_training_rows=len(history) - len(accepted),
                eligible_cells=[{k: r[k] for k in ("sector", "party", "target", "horizon")} for r in accepted],
                excluded_cells=[dict({k: r[k] for k in ("sector", "party", "target", "horizon")}, reason=eligibility(r))
                                for r in history if eligibility(r) is not None])
    if len(years) < 2:
        return dict(base, status="insufficient_earlier_origins", denominator=0.0, numerator=0.0, weight_sum=0.0)
    weighted = []
    for row in accepted:
        year_rows = [r for r in accepted if r["target"] == row["target"]]
        sectors = {r["sector"] for r in year_rows}
        sector_rows = [r for r in year_rows if r["sector"] == row["sector"]]
        parties = {r["party"] for r in sector_rows}
        party_rows = [r for r in sector_rows if r["party"] == row["party"]]
        weight = 1 / len(years) / len(sectors) / len(parties) / len(party_rows)
        x = midpoint(row["remaining"]) / row["prior_scale_cents"]
        z = (midpoint(row["truth"]) - midpoint(row["baseline"])) / row["prior_scale_cents"]
        if not all(math.isfinite(v) for v in (x, z, weight*x*z, weight*x*x)):
            raise ValueError("nonfinite calibration training arithmetic")
        weighted.append((weight, x, z))
    numerator = math.fsum(w*x*z for w, x, z in weighted)
    denominator = math.fsum(w*x*x for w, x, z in weighted)
    raw = .5 * numerator / denominator if denominator else 0.0
    if not all(math.isfinite(v) for v in (numerator, denominator, raw)):
        raise ValueError("nonfinite calibration coefficient arithmetic")
    beta = min(.5, max(-.5, raw))
    return dict(base, alpha=1 + beta, beta=beta, unclipped_beta=raw,
                status="fitted" if denominator else "zero_remaining_energy", numerator=numerator, denominator=denominator,
                weight_sum=math.fsum(w for w, x, z in weighted))


def project(row, model):
    if model["chamber"] != row["chamber"] or model["target"] != row["target"]:
        raise ValueError("calibration identity mismatch")
    if any(y >= row["target"] for y in model["training_cycles"]):
        raise ValueError("calibration used target or later outcomes")
    alpha = model["alpha"]
    if isinstance(alpha, bool) or not isinstance(alpha, (float, int)) or not math.isfinite(alpha) or not .5 <= alpha <= 1.5:
        raise ValueError("invalid fixed-domain calibration coefficient")
    reason = eligibility(row)
    if model["status"] != "fitted" or len(model["training_cycles"]) < 2:
        reason = reason or model["status"]
    bounds = dict(row["baseline"])
    if reason is None:
        candidate = {e: row["observed"][e] + alpha * row["remaining"][e] for e in ("lo_cents", "hi_cents")}
        if not all(math.isfinite(v) for v in candidate.values()) or candidate["lo_cents"] < 0:
            reason = "negative or invalid candidate forecast lower bound"
        else:
            bounds = candidate
    return dict(row, family="calibrated_remaining", prediction=bounds, calibration_alpha=alpha,
                calibration_status=model["status"], fallback_reason=reason)


def evaluate(saved):
    # Exact grids are checked before fitting; no duplicates or dropped populations.
    fields = ("sector", "chamber", "target", "horizon")
    expected = {(s,c,y,h) for s in ref.SECTORS for c in ("House", "Senate") for y in PROTOCOL["target_cycles"] for h in ref.PROTOCOL["horizons"]}
    keys = [tuple(f[k] for k in fields) for f in saved["feature_frames"]]
    if set(keys) != expected or len(keys) != len(expected):
        raise ValueError("exact unique feature grid required")
    truth_keys = [tuple(t[k] for k in ("sector", "chamber", "target", "party")) for t in saved["truth_intervals"]]
    expected_truth = {(s,c,y,p) for s in ref.SECTORS for c in ("House", "Senate") for y in PROTOCOL["target_cycles"] for p in ("D", "R")}
    if set(truth_keys) != expected_truth or len(truth_keys) != len(expected_truth):
        raise ValueError("exact unique truth grid required")
    truth = dict(zip(truth_keys, saved["truth_intervals"]))
    baseline = [ref.score(r, truth[r["sector"],r["chamber"],r["target"],r["party"]]) for f in saved["feature_frames"] for r in prepare_frame(f)]
    models, predicted = [], []
    for year in PROTOCOL["target_cycles"]:
        for chamber in ("House", "Senate"):
            model = fit(baseline, chamber, year)
            models.append(model)
            for row in baseline:
                if row["target"] == year and row["chamber"] == chamber:
                    input_row = {k:v for k,v in row.items() if k not in ("truth", "certified_within10", "certified_within20", "absolute_error_upper_cents", "normalized_absolute_error_upper", "ape_upper_bound")}
                    predicted.append(ref.score(project(input_row, model), row["truth"]))
    base_eval = [r for r in baseline if r["target"] in (2022, 2024)]
    new_eval = [r for r in predicted if r["target"] in (2022, 2024)]
    b, n = ref.metrics(base_eval), ref.metrics(new_eval)
    tolerance = n["certified_within20"] > b["certified_within20"] or (n["certified_within20"] == b["certified_within20"] and n["certified_within10"] > b["certified_within10"])
    chambers = {c: ref.metrics([r for r in new_eval if r["chamber"] == c])["certified_within20"] >= ref.metrics([r for r in base_eval if r["chamber"] == c])["certified_within20"] for c in ("House", "Senate")}
    error = n["mean_normalized_upper_error"] <= b["mean_normalized_upper_error"] + 1e-12
    groups = []
    for family, source in (("baseline", baseline), ("calibrated_remaining", predicted)):
        for scope, years in (("all_origins", [2018,2020,2022,2024]), ("evaluated_origins", [2022,2024]), ("warmup_origins", [2018,2020])):
            for chamber in ("ALL", "House", "Senate"):
                for horizon in ("ALL", *ref.PROTOCOL["horizons"]):
                    rows = [r for r in source if r["target"] in years and (chamber == "ALL" or r["chamber"] == chamber) and (horizon == "ALL" or r["horizon"] == horizon)]
                    groups.append(dict(family=family, scope=scope, chamber=chamber, horizon=horizon, **ref.metrics(rows)))
    regressions = []
    for family, source in (("baseline", baseline), ("calibrated_remaining", predicted)):
        for sector in ref.SECTORS:
            for chamber in ("House", "Senate"):
                for party in ("D", "R"):
                    for year in ("ALL", *PROTOCOL["target_cycles"]):
                        for horizon in ("ALL", *ref.PROTOCOL["horizons"]):
                            rows = [r for r in source if r["sector"] == sector and r["chamber"] == chamber and r["party"] == party
                                    and (year == "ALL" or r["target"] == year) and (horizon == "ALL" or r["horizon"] == horizon)]
                            regressions.append(dict(family=family, sector=sector, chamber=chamber, party=party, target=year, horizon=horizon, **ref.metrics(rows)))
    return dict(protocol=PROTOCOL, baseline=baseline, predictions=predicted, models=models, metrics=groups, regression_metrics=regressions,
                final_2026_models=[fit(baseline,c,2026) for c in ("House", "Senate")],
                deployment_gate=dict(passed=tolerance and all(chambers.values()) and error, tolerance_improved=tolerance,
                                     chamber_non_degradation=chambers, normalized_error_non_degradation=error, baseline=b, candidate=n))
