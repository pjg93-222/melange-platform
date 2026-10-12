"""Fixed ridge experiment for political context and partisan PAC dollar forecasts.

Reconstructed retrospective research, not an original-vintage backtest. A shared
coefficient vector transfers dollars between D and R, leaving the baseline total
unchanged. Midpoints define the training loss only; evaluation retains full boxes.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from statistics import median

import numpy as np

FEATURES = {"baseline": [], "lean_only": ["target_r_share"],
            "both": ["target_r_share", "champion_mean_dw"]}
FEATURE_DEFINITION = "lagged_prior_cycle_bill_attention_top25"
PROTOCOL = {
    "id": "partisan-political-transfer-v1", "variants": list(FEATURES),
    "active_variant": "both", "selection_performed": False, "ridge_lambda": 1.0,
    "coefficient_scope": "one shared vector across cohorts, chambers and horizons",
    "minimum_earlier_elections": 2, "intercept": False,
    "standardization": "training-only weighted mean and RMS deviation",
    "weights": "equal elections, then equal cohorts, then equal chambers, then equal horizons",
    "loss": "mean squared normalized symmetric D/R transfer error plus lambda times coefficient squared norm",
    "training_target": "((truth_R_mid-baseline_R_mid)-(truth_D_mid-baseline_D_mid))/2/prior_scale",
    "scale": "sum of prior D and R marginal midpoints; normalization only, not an independently verified joint total",
    "baseline": "frozen historical_remainder intervals",
    "constraint": "transfer to R clamped to [-R_remaining_lower,D_remaining_lower]; negative lower remainder falls back",
    "availability": "lagged prior completed cycle bill attention; revised historical DW scores; reconstructed source snapshots",
    "champions": "top25 prior-cycle attention members, equal-weight raw DW average; current signed board is shown separately",
    "scoring": "full marginal prediction/truth boxes for 10% and 20% dollar certification",
    "missing": "retain baseline and count all cells; never impute a missing signal as neutral",
    "limitations": ["Previously examined historical outcomes; no untouched holdout.",
        "Revised DW and source reconstruction do not establish original historical availability.",
        "Multiple horizons reuse the same election outcomes.",
        "Classification bounds do not cover coefficient or future-outcome uncertainty.",
        "Bill sponsorship/cosponsorship links are not evidence of direct contact or support.",
        "A conserving transfer cannot correct the baseline total-dollar error."],
}


def canonical(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def midpoint(bound):
    lo, hi = bound["lo_cents"], bound["hi_cents"]
    if not all(math.isfinite(v) for v in (lo, hi)) or lo > hi:
        raise ValueError("invalid interval")
    return (lo + hi) / 2


def load_features(path):
    """The input assembler preserves its source-specific names and metadata."""
    with Path(path).open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    output = {}
    for raw in rows:
        key = (raw.get("sector") or raw["industry"], raw["chamber"], int(raw.get("target") or raw["cycle"]))
        if key in output:
            raise ValueError("duplicate feature key")
        features = {}
        for src, dest in (("attention_republican_share", "target_r_share"),
                          ("champion_proxy_mean_dw_nominate_dim1", "champion_mean_dw")):
            value = raw.get(src)
            features[dest] = float(value) if raw.get("feature_status") == "ready_reconstructed" and value not in (None, "", "NA") else None
        output[key] = {"features": features, "feature_provenance": {
            **raw, "feature_definition": FEATURE_DEFINITION}}
    return output


def training_weights(rows):
    """Normalize nested grouping, so four horizons are not four elections."""
    years = {r["target"] for r in rows}
    weights = []
    for row in rows:
        same_year = [r for r in rows if r["target"] == row["target"]]
        sectors = {r["sector"] for r in same_year}
        same_sector = [r for r in same_year if r["sector"] == row["sector"]]
        chambers = {r["chamber"] for r in same_sector}
        cell = [r for r in same_sector if r["chamber"] == row["chamber"]]
        weights.append(1 / len(years) / len(sectors) / len(chambers) / len(cell))
    return np.asarray(weights, dtype=float)


def usable(row, names):
    values = row.get("features", {})
    if any(type(values.get(n)) not in (int, float) or not math.isfinite(values[n]) for n in names):
        return False
    if "target_r_share" in names and not 0 <= values["target_r_share"] <= 1:
        return False
    if "champion_mean_dw" in names and not -1 <= values["champion_mean_dw"] <= 1:
        return False
    return (type(row["prior_scale_cents"]) in (int, float) and
            math.isfinite(row["prior_scale_cents"]) and row["prior_scale_cents"] > 0 and all(
        row["remaining"][p]["lo_cents"] >= 0 for p in ("D", "R")))


def fit(rows, variant, before_target):
    """No target or later-election outcomes enter this fit, including scaling."""
    names = FEATURES[variant]
    eligible = [r for r in rows if r["target"] < before_target and usable(r, names)]
    years = sorted({r["target"] for r in eligible})
    model = dict(features=names, means=[], scales=[], coefficients=[], ridge_lambda=1.0,
                 training_cycles=years, training_row_count=len(eligible),
                 feature_definition=FEATURE_DEFINITION, coefficient_scope=PROTOCOL["coefficient_scope"],
                 intercept=False, variant=variant)
    if not names:
        return dict(model, status="baseline")
    if len(years) < 2:
        return dict(model, status="insufficient_earlier_elections")
    weights = training_weights(eligible)
    x = np.asarray([[r["features"][n] for n in names] for r in eligible], dtype=float)
    means = np.sum(weights[:, None] * x, axis=0)
    scales = np.sqrt(np.sum(weights[:, None] * (x - means) ** 2, axis=0))
    constant = scales < 1e-12
    scales[constant] = 1.0
    x = (x - means) / scales
    x[:, constant] = 0
    y = np.asarray([((midpoint(r["truth"]["R"]) - midpoint(r["baseline"]["R"])) -
                     (midpoint(r["truth"]["D"]) - midpoint(r["baseline"]["D"]))) /
                    2 / r["prior_scale_cents"] for r in eligible])
    beta = np.linalg.solve(x.T @ (weights[:, None] * x) + np.eye(len(names)), x.T @ (weights * y))
    return dict(model, status="fitted", means=means.tolist(), scales=scales.tolist(),
                coefficients=beta.tolist(), constant_features=[n for n, c in zip(names, constant) if c],
                weights_sum=float(weights.sum()))


def make_rows(saved, features, predictor):
    truths = {}
    for t in saved.get("truth_intervals", []):
        truths.setdefault((t["sector"], t["chamber"], t["target"]), {})[t["party"]] = {
            k: t[k] for k in ("lo_cents", "hi_cents")}
    rows = []
    for frame in saved["feature_frames"]:
        key = (frame["sector"], frame["chamber"], frame["target"])
        base = predictor(frame, "historical_remainder")
        baseline = {r["party"]: dict(lo_cents=r["prediction_lo_cents"], hi_cents=r["prediction_hi_cents"]) for r in base}
        remaining = {p: {endpoint: median(h["remaining"][p][endpoint] for h in frame["history"])
                         for endpoint in ("lo_cents", "hi_cents")} for p in ("D", "R")}
        if any(r["uncertainty_rule_fallback"] for r in base):
            # Never reinterpret a prior-cycle fallback as a remaining-money budget.
            remaining = {p: dict(lo_cents=-1, hi_cents=-1) for p in ("D", "R")}
        feature = features.get(key, {"features": {"target_r_share": None, "champion_mean_dw": None},
                                     "feature_provenance": {"fallback_reason": "No mapped political context"}})
        rows.append({**{k: frame[k] for k in ("sector", "chamber", "target", "horizon")},
                     "as_of": frame["issue"], "cut": frame["cut"],
                     "baseline": baseline, "remaining": remaining,
                     "prior_scale_cents": sum(midpoint(frame["history"][-1]["final"][p]) for p in ("D", "R")),
                     **({"truth": truths[key]} if key in truths else {}), **feature})
    return rows


def score(bound, truth):
    lo, hi = bound["lo_cents"], bound["hi_cents"]
    yl, yh = truth["lo_cents"], truth["hi_cents"]
    return {"certified_within10": yl > 0 and lo >= .9 * yh and hi <= 1.1 * yl,
            "certified_within20": yl > 0 and lo >= .8 * yh and hi <= 1.2 * yl,
            "absolute_error_upper_cents": max(abs(lo - yh), abs(hi - yl)),
            "ape_upper_bound": max(abs(p - y) / y for p in (lo, hi) for y in (yl, yh)) if yl > 0 else None}


def aggregate(predictions):
    groups = {}
    for row in predictions:
        for horizon in (row["horizon"], "ALL"):
            for chamber in (row["chamber"], "ALL"):
                for population in ("all_cells", "fitted_feature_cells"):
                    if population == "fitted_feature_cells" and not row["feature_fit_eligible"]:
                        continue
                    groups.setdefault((row["variant"], horizon, chamber, population), []).append(row)
    return [dict(variant=key[0], horizon=key[1], chamber=key[2], population=key[3], n=len(rows),
                 certified_within10=sum(r["certified_within10"] for r in rows),
                 certified_within20=sum(r["certified_within20"] for r in rows),
                 mean_absolute_error_upper_dollars=sum(r["absolute_error_upper_cents"] for r in rows) / len(rows) / 100,
                 adjusted_cells=sum(r["status"] == "adjusted" for r in rows))
            for key, rows in sorted(groups.items())]
