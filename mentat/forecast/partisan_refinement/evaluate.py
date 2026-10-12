"""Offline, bounded House/Senate share experiment. Never publishes or imports the app.

Development accepts only 2012--2022 histories. The --holdout mode is reserved
historical confirmation, not an untouched test: 2024 was examined in earlier
research. It requires separate 2024-only inputs and a hash-pinned policy. All outputs are
new directories; existing paths are refused. NumPy is the only non-stdlib import.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np


FAMILIES = ("persistence", "same_phase", "equal_blend", "ridge_control_1", "ridge_control_10")
CHAMBERS = ("House", "Senate")
INDUSTRIES = ("auto_light_duty_manufacturing", "commercial_banks", "credit_unions", "property_casualty")
DEVELOPMENT_YEARS = (2012, 2014, 2016, 2018, 2020, 2022)
OUTER_YEARS = (2018, 2020, 2022)
INNER_YEARS = (2016, 2018, 2020, 2022)
TIE_TOLERANCE = 1e-12
ROW_FIELDS = ("industry", "cycle", "share", "forecast", "current_control", "growth_pct", "prior_growth_pct", "pres")
PROTOCOL = {
    "id": "partisan-refinement-five-family-v1",
    "families_in_tie_order": list(FAMILIES),
    "industries": list(INDUSTRIES),
    "outer_years": list(OUTER_YEARS),
    "inner_years": list(INNER_YEARS),
    "minimum_inner_origins": 2,
    "tie_tolerance_mean_absolute_share": TIE_TOLERANCE,
    "ridge": "pooled delta_share ~ delta_control; zero intercept; training-only RMS; mean squared loss + lambda*b^2",
    "bounds": "clip candidate forecast to [0,1], retain raw forecast and clipping flag; never clip truth or errors",
    "selection": "one family per chamber; all industries equally weighted; earlier origins only; failed family ineligible",
    "fallback": "persistence when fewer than two complete inner origins; persistence on unresolved selection",
    "incumbent": "benchmark only; earlier-cycle level OLS, train-only growth scaling, previous-cycle anchor, unbounded output",
    "holdout_year": 2024,
    "confirmation_status": "reserved historical confirmation; previously examined, not an untouched test",
    "confirmation_benchmarks": ["persistence", "same_phase", "equal_blend", "incumbent_R0"],
    "loeo_diagnostic": "fixed final chronological choice; train on every other cycle including future cycles; never selects",
    "promotion": "raw bounds violations by final family or nested policy on inner, outer, LOEO or confirmation grids block promotion; unselected families do not block",
    "scope": "whole-industry two-party candidate shares, not employee/PAC-specific or party-dollar accuracy",
    "vintage_limitation": "retrospective source editions; no historical information-availability certification",
}


class ProtocolError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def code_hash() -> str:
    return digest(Path(__file__).read_bytes())


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object,
                       parse_constant=lambda x: (_ for _ in ()).throw(ProtocolError(f"nonfinite JSON: {x}")))
    if not isinstance(value, dict):
        raise ProtocolError("input JSON must be an object")
    return value, digest(raw)


def extract_histories(bundle: dict, *, holdout: bool = False) -> dict[str, list[dict]]:
    """Read the active-bundle history shape; reject future rows instead of hiding them."""
    if holdout and "rows" in bundle:
        flat = bundle["rows"]
        if not isinstance(flat, list) or any(not isinstance(r, dict) or r.get("chamber") not in CHAMBERS for r in flat):
            raise ProtocolError("holdout rows must identify House or Senate")
        bundle = {"share_models": {"control_" + c: {"history": [r for r in flat if r["chamber"] == c]} for c in CHAMBERS}}
    models = bundle.get("share_models")
    if not isinstance(models, dict):
        raise ProtocolError("share_models object is required")
    expected_years = (2024,) if holdout else DEVELOPMENT_YEARS
    result = {}
    for chamber in CHAMBERS:
        model = models.get("control_" + chamber, {})
        source = model.get("history") if isinstance(model, dict) else None
        if not isinstance(source, list) or not source:
            raise ProtocolError(f"missing {chamber} history")
        rows, seen = [], set()
        for original in source:
            if not isinstance(original, dict) or not set(ROW_FIELDS).issubset(original):
                raise ProtocolError(f"incomplete {chamber} history row")
            row = {key: original[key] for key in ROW_FIELDS}
            year = row["cycle"]
            if type(year) is not int or year not in expected_years:
                raise ProtocolError(f"{chamber}: disallowed history year {year}; expected {expected_years}")
            if not isinstance(row["industry"], str) or not row["industry"]:
                raise ProtocolError("industry must be a nonempty string")
            for key in ROW_FIELDS[2:]:
                value = row[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ProtocolError(f"{chamber} {year}: {key} must be finite numeric")
            if not 0 <= row["share"] <= 1 or not 0 <= row["forecast"] <= 1:
                raise ProtocolError("share and forecast must be in [0,1]")
            if row["current_control"] not in (0, 1) or row["pres"] != int(year % 4 == 0):
                raise ProtocolError("invalid current control or presidential phase")
            key = row["industry"], year
            if key in seen:
                raise ProtocolError(f"duplicate history key: {key}")
            seen.add(key)
            rows.append(row)
        industries = sorted({r["industry"] for r in rows})
        if tuple(industries) != INDUSTRIES:
            raise ProtocolError(f"{chamber}: exactly the four frozen industries are required")
        if seen != {(s, y) for s in industries for y in expected_years}:
            raise ProtocolError(f"{chamber}: balanced industry/year history required")
        result[chamber] = sorted(rows, key=lambda r: (r["cycle"], r["industry"]))
    if {r["industry"] for r in result["House"]} != {r["industry"] for r in result["Senate"]}:
        raise ProtocolError("chambers must use the same industry population")
    return result


def index_rows(rows: list[dict]) -> dict[tuple[str, int], dict]:
    return {(r["industry"], r["cycle"]): r for r in rows}


def ridge_fit(training: list[dict], penalty: float) -> dict:
    """Pooled first differences; RMS scaling leaves a zero change at the origin."""
    if penalty <= 0 or not math.isfinite(penalty):
        raise ProtocolError("ridge penalty must be finite and positive")
    lookup = index_rows(training)
    pairs = [(r, lookup[(r["industry"], r["cycle"] - 2)]) for r in training
             if (r["industry"], r["cycle"] - 2) in lookup]
    if not pairs:
        raise ProtocolError("no earlier consecutive-cycle training pairs")
    x = np.array([a["forecast"] - b["forecast"] for a, b in pairs], dtype=float)
    y = np.array([a["share"] - b["share"] for a, b in pairs], dtype=float)
    rms = float(np.sqrt(np.mean(x * x)))
    slope_scaled = 0.0 if rms == 0 else float(np.mean((x / rms) * y) / (np.mean((x / rms) ** 2) + penalty))
    return {"penalty": penalty, "rms": rms, "slope_scaled": slope_scaled,
            "slope_raw": 0.0 if rms == 0 else slope_scaled / rms,
            "training_pairs": len(pairs), "pair_end_years": sorted({a["cycle"] for a, _ in pairs})}


def candidate_predictions(rows: list[dict], target_rows: list[dict], family: str,
                          *, diagnostic_training: list[dict] | None = None) -> list[dict]:
    """Target shares are never read; only forecast inputs and prior outcomes are used."""
    if family not in FAMILIES or not target_rows:
        raise ProtocolError("unknown family or empty target")
    target_years = {r["cycle"] for r in target_rows}
    if len(target_years) != 1:
        raise ProtocolError("one forecast origin per call required")
    year = next(iter(target_years))
    training = [r for r in rows if r["cycle"] < year]
    lookup = index_rows(training)
    fitting = training if diagnostic_training is None else diagnostic_training
    if any(r["cycle"] == year for r in fitting):
        raise ProtocolError("target outcome cannot enter model fitting")
    trace = ridge_fit(fitting, float(family.rsplit("_", 1)[1])) if family.startswith("ridge_") else {}
    predictions = []
    for target in sorted(target_rows, key=lambda r: r["industry"]):
        sector = target["industry"]
        previous = lookup.get((sector, year - 2))
        same = lookup.get((sector, year - 4))
        if previous is None or (family in ("same_phase", "equal_blend") and same is None):
            raise ProtocolError(f"missing anchor for {family}/{sector}/{year}")
        raw = previous["share"]
        if family == "same_phase":
            raw = same["share"]
        elif family == "equal_blend":
            raw = (previous["share"] + same["share"]) / 2
        elif family.startswith("ridge_"):
            raw += trace["slope_raw"] * (target["forecast"] - previous["forecast"])
        if not math.isfinite(raw):
            raise ProtocolError("nonfinite candidate prediction")
        prediction = min(1.0, max(0.0, raw))
        predictions.append({"industry": sector, "target": year, "family": family,
                            "prediction": prediction, "raw_prediction": raw, "clipped": prediction != raw,
                            "training_years": sorted({r["cycle"] for r in fitting}), "fit": dict(trace),
                            "future_trained_diagnostic": diagnostic_training is not None})
    return predictions


def ordered_ols(x: np.ndarray, y: np.ndarray, tolerance: float = 1e-7) -> tuple[np.ndarray, list[int]]:
    """R-lm-compatible ordered alias removal, then OLS on retained columns.

    Formula-order columns with residual norm below tol*original norm are aliased.
    Their coefficients stay zero; minimum-norm extrapolation across aliases would
    differ from R lm on early folds. Real-data numerical parity is a release gate.
    """
    keep = []
    for j in range(x.shape[1]):
        column = x[:, j]
        norm = float(np.linalg.norm(column))
        residual = column if not keep else column - x[:, keep] @ np.linalg.lstsq(x[:, keep], column, rcond=None)[0]
        if norm > 0 and float(np.linalg.norm(residual)) >= tolerance * norm:
            keep.append(j)
    if not keep:
        raise ProtocolError("incumbent has no identified columns")
    beta = np.zeros(x.shape[1])
    beta[keep] = np.linalg.lstsq(x[:, keep], y, rcond=None)[0]
    return beta, keep


def incumbent_predictions(rows: list[dict], target_rows: list[dict], chamber: str) -> list[dict]:
    """Old R0 previous-cycle calculator path, reconstructed with prior training only."""
    year = target_rows[0]["cycle"]
    training = [r for r in rows if r["cycle"] < year]
    sectors = sorted({r["industry"] for r in training})
    lookup = index_rows(training)
    scaling = {}
    for sector in sectors:
        values = [r["growth_pct"] for r in training if r["industry"] == sector]
        sd = float(np.std(values, ddof=1))
        if not math.isfinite(sd) or sd <= 0:
            raise ProtocolError("incumbent requires varying earlier-cycle growth")
        scaling[sector] = float(np.mean(values)), sd
    # R rf_spec_formula appends focal P_R after the 'others' formula.
    names = ["Intercept", *["industry:" + s for s in sectors[1:]], "RMajNow", "G_prior"]
    if chamber == "House":
        names.append("Pres")
    names.append("P_R")

    def features(row):
        mean, sd = scaling[row["industry"]]
        values = [1.0, *[float(row["industry"] == s) for s in sectors[1:]],
                  row["current_control"], (row["prior_growth_pct"] - mean) / sd]
        if chamber == "House":
            values.append(row["pres"])
        return [*values, row["forecast"]]

    x = np.array([features(r) for r in training], dtype=float)
    y = np.array([r["share"] for r in training], dtype=float)
    beta, keep = ordered_ols(x, y)
    result = []
    for target in sorted(target_rows, key=lambda r: r["industry"]):
        previous = lookup.get((target["industry"], year - 2))
        if previous is None:
            raise ProtocolError("incumbent missing previous-cycle anchor")
        delta = np.array(features(target)) - np.array(features(previous))
        point = float(previous["share"] + delta @ beta)
        result.append({"industry": target["industry"], "target": year, "family": "incumbent_R0",
                       "prediction": point, "raw_prediction": point, "clipped": False,
                       "training_years": sorted({r["cycle"] for r in training}),
                       "fit": {"terms": names, "beta": beta.tolist(), "retained": [names[j] for j in keep],
                               "growth_scaling": {s: {"mean": v[0], "sd": v[1]} for s, v in scaling.items()}}})
    return result


def score_predictions(predictions: list[dict], target_rows: list[dict], chamber: str, stage: str) -> list[dict]:
    truth = index_rows(target_rows)
    result = []
    for row in predictions:
        actual = truth[(row["industry"], row["target"])]["share"]
        error = row["prediction"] - actual
        result.append(dict(row, chamber=chamber, stage=stage, actual=actual,
                           error_pp=100 * error, absolute_error_pp=100 * abs(error)))
    return result


def choose_family(scores: dict[str, float]) -> str:
    valid = {k: v for k, v in scores.items() if k in FAMILIES and math.isfinite(v)}
    if not valid:
        return "persistence"
    minimum = min(valid.values())
    return next(f for f in FAMILIES if f in valid and valid[f] <= minimum + TIE_TOLERANCE)


def select_policy(rows: list[dict], chamber: str, before: int) -> dict:
    """Only outcomes strictly earlier than `before` may select a family."""
    available = [r for r in rows if r["cycle"] < before]
    industries = sorted({r["industry"] for r in available})
    years = [year for year in INNER_YEARS if year < before]
    records, failures, origin_scores = [], [], {f: {} for f in FAMILIES}
    for year in years:
        targets = [r for r in available if r["cycle"] == year]
        if sorted(r["industry"] for r in targets) != industries:
            failures.append({"stage": "inner", "chamber": chamber, "target": year, "family": "ALL", "reason": "incomplete target population"})
            continue
        for family in FAMILIES:
            try:
                pred = candidate_predictions(available, targets, family)
                scored = score_predictions(pred, targets, chamber, "inner")
                records.extend(scored)
                origin_scores[family][year] = float(np.mean([r["absolute_error_pp"] / 100 for r in scored]))
            except (ProtocolError, np.linalg.LinAlgError) as error:
                failures.append({"stage": "inner", "chamber": chamber, "target": year, "family": family, "reason": str(error)})
    # A common origin is complete for every fixed family: no model-specific row advantage.
    common = sorted(set.intersection(*(set(v) for v in origin_scores.values())))
    enough = len(common) >= PROTOCOL["minimum_inner_origins"]
    scores = {f: float(np.mean([origin_scores[f][y] for y in common])) for f in FAMILIES} if enough else {}
    failed_families = {r["family"] for r in failures}
    if "ALL" in failed_families:
        scores = {}
    else:
        scores = {f: v for f, v in scores.items() if f not in failed_families}
    choice = choose_family(scores) if enough else "persistence"
    return {"selected_family": choice, "selection_before": before, "common_inner_origins": common,
            "fallback": not enough or not scores, "mean_absolute_share_by_family": scores,
            "predictions": records, "failures": failures}


def summarize(records: list[dict]) -> list[dict]:
    groups = {}
    for row in records:
        for industry in (row["industry"], "__ALL__"):
            key = row["stage"], row["chamber"], row["family"], industry
            groups.setdefault(key, []).append(row)
    result = []
    for (stage, chamber, family, industry), rows in sorted(groups.items()):
        errors = np.array([r["error_pp"] for r in rows])
        result.append({"stage": stage, "chamber": chamber, "family": family, "industry": industry,
                       "n": len(rows), "origins": sorted({r["target"] for r in rows}),
                       "mae_pp": float(np.mean(np.abs(errors))), "rmse_pp": float(np.sqrt(np.mean(errors ** 2))),
                       "max_abs_error_pp": float(np.max(np.abs(errors))),
                       "clipped_count": sum(r["clipped"] for r in rows)})
    return result


def development(histories: dict[str, list[dict]]) -> dict:
    records, failures, nested, final = [], [], {}, {}
    for chamber in CHAMBERS:
        rows = histories[chamber]
        nested[chamber] = {}
        for year in OUTER_YEARS:
            targets = [r for r in rows if r["cycle"] == year]
            selection = select_policy(rows, chamber, year)
            nested[chamber][str(year)] = selection
            failures.extend(selection["failures"])
            for family in (*FAMILIES, "incumbent_R0"):
                try:
                    raw = incumbent_predictions(rows, targets, chamber) if family == "incumbent_R0" else candidate_predictions(rows, targets, family)
                    scored = [dict(r, selected_family=selection["selected_family"]) for r in score_predictions(raw, targets, chamber, "outer_candidate")]
                    records.extend(scored)
                    if family == selection["selected_family"]:
                        records.extend(dict(r, stage="outer_policy", selected_family=family, family="nested_policy") for r in scored)
                except (ProtocolError, np.linalg.LinAlgError) as error:
                    failures.append({"stage": "outer", "chamber": chamber, "target": year, "family": family, "reason": str(error)})
        final[chamber] = select_policy(rows, chamber, 2024)
        failures.extend(final[chamber]["failures"])
    diagnostic = fixed_policy_loeo(histories, {c: final[c]["selected_family"] for c in CHAMBERS})
    diagnostic["metrics"] = summarize(diagnostic["predictions"])
    candidate_rows = [r for r in records if r["family"] != "incumbent_R0"] + diagnostic["predictions"]
    # Include early inner-origin predictions in the bounds gate; keep their stage.
    for chamber in CHAMBERS:
        candidate_rows.extend(final[chamber]["predictions"])
    # Diagnostic feasibility is separate from the chronological run/policy freeze.
    return {"mode": "development", "protocol": PROTOCOL, "predictions": records,
            "metrics": summarize(records), "nested_policies": nested, "final_selection": final,
            "failures": failures, "failure_count": len(failures),
            "loeo_diagnostic": diagnostic,
            "loeo_diagnostic_failures": diagnostic["failures"],
            "loeo_diagnostic_failure_count": len(diagnostic["failures"]),
            "raw_range_violations": raw_range_violations(records),
            "candidate_promotion_blockers": raw_range_violations(candidate_rows),
            "selected_policy_promotion_blockers": raw_range_violations([
                r for r in candidate_rows if r["stage"] == "outer_policy" or r["family"] == final[r["chamber"]]["selected_family"]])}


def raw_range_violations(records: list[dict]) -> list[dict]:
    return [{k: r[k] for k in ("stage", "chamber", "industry", "target", "family", "raw_prediction", "prediction")}
            for r in records if not 0 <= r["raw_prediction"] <= 1]


def fixed_policy_loeo(histories: dict, selections: dict[str, str]) -> dict:
    """Descriptive G6 comparison only: future-trained; final choice already frozen."""
    records, failures = [], []
    for chamber in CHAMBERS:
        rows = histories[chamber]
        family = selections[chamber]
        for year in DEVELOPMENT_YEARS[1:]:
            targets = [r for r in rows if r["cycle"] == year]
            fitting = [r for r in rows if r["cycle"] != year]
            try:
                raw = candidate_predictions(rows, targets, family, diagnostic_training=fitting)
                records.extend(dict(r, selected_family=family) for r in score_predictions(raw, targets, chamber, "fixed_policy_loeo_future_trained"))
            except (ProtocolError, np.linalg.LinAlgError) as error:
                failures.append({"stage": "fixed_policy_loeo_future_trained", "chamber": chamber,
                                 "target": year, "family": family, "reason": str(error)})
    return {"predictions": records, "failures": failures}


def freeze_manifest(result: dict, input_hash: str, histories: dict, evaluator_hash: str) -> dict:
    return {"schema_version": 1, "kind": "frozen_partisan_share_policy", "protocol": PROTOCOL,
            "protocol_sha256": digest(canonical_bytes(PROTOCOL)), "input_sha256": input_hash,
            "history_sha256": digest(canonical_bytes(histories)), "evaluator_sha256": evaluator_hash,
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "training_through": 2022,
            "reserved_2024_used_for_this_run_selection": False, "reserved_2024_previously_examined": True,
            "selected_policy_promotion_blockers": result["selected_policy_promotion_blockers"],
            "candidate_promotion_blockers": result["candidate_promotion_blockers"],
            "failure_count": result["failure_count"],
            "selections": {c: {k: v for k, v in result["final_selection"][c].items() if k != "predictions"} for c in CHAMBERS}}


def verify_manifest(manifest: dict, input_hash: str, histories: dict, evaluator_hash: str) -> None:
    checks = {"schema_version": 1, "kind": "frozen_partisan_share_policy", "protocol": PROTOCOL,
              "protocol_sha256": digest(canonical_bytes(PROTOCOL)), "input_sha256": input_hash,
              "history_sha256": digest(canonical_bytes(histories)), "evaluator_sha256": evaluator_hash,
              "training_through": 2022, "reserved_2024_used_for_this_run_selection": False,
              "reserved_2024_previously_examined": True, "failure_count": 0}
    for key, expected in checks.items():
        if manifest.get(key) != expected:
            raise ProtocolError(f"frozen policy mismatch: {key}")
    if set(manifest.get("selections", {})) != set(CHAMBERS):
        raise ProtocolError("frozen policy requires both chambers")
    for chamber in CHAMBERS:
        selection = manifest["selections"][chamber]
        if selection.get("selected_family") not in FAMILIES or selection.get("selection_before") != 2024:
            raise ProtocolError("invalid frozen policy selection")


def holdout(histories: dict, target_histories: dict, manifest: dict) -> dict:
    """Reserved historical confirmation plus fixed benchmarks; never selects."""
    records, failures = [], []
    for chamber in CHAMBERS:
        rows, targets = histories[chamber], target_histories[chamber]
        if {r["industry"] for r in rows} != {r["industry"] for r in targets}:
            raise ProtocolError("holdout population differs from development")
        family = manifest["selections"][chamber]["selected_family"]
        plans = [(family, "confirmation_policy"), *[(f, "confirmation_benchmark") for f in PROTOCOL["confirmation_benchmarks"]]]
        for method, stage in plans:
            try:
                predictions = incumbent_predictions(rows, targets, chamber) if method == "incumbent_R0" else candidate_predictions(rows, targets, method)
                records.extend(dict(r, selected_family=family) for r in score_predictions(predictions, targets, chamber, stage))
            except (ProtocolError, np.linalg.LinAlgError) as error:
                failures.append({"stage": stage, "chamber": chamber, "target": 2024, "family": method, "reason": str(error)})
    return {"mode": "reserved_historical_confirmation", "protocol": PROTOCOL, "predictions": records, "metrics": summarize(records),
            "failures": failures, "failure_count": len(failures), "selection_performed": False,
            "not_an_untouched_test": True, "raw_range_violations": raw_range_violations(records),
            "candidate_promotion_blockers": raw_range_violations([r for r in records if r["family"] != "incumbent_R0"]),
            "selected_policy_promotion_blockers": raw_range_violations([r for r in records if r["stage"] == "confirmation_policy"])}


def csv_bytes(rows: list[dict]) -> bytes:
    fields = list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, sort_keys=True, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return stream.getvalue().encode("utf-8-sig")


def write_outputs(destination: Path, result: dict, manifest: dict | None = None) -> None:
    payloads = {"evaluation.json": canonical_bytes(result), "predictions.csv": csv_bytes(result["predictions"]),
                "metrics.csv": csv_bytes(result["metrics"])}
    if "loeo_diagnostic" in result:
        payloads["loeo_diagnostic_predictions.csv"] = csv_bytes(result["loeo_diagnostic"]["predictions"])
        payloads["loeo_diagnostic_metrics.csv"] = csv_bytes(result["loeo_diagnostic"]["metrics"])
    if manifest is not None:
        payloads["policy_manifest.json"] = canonical_bytes(manifest)
        payloads["policy_manifest.sha256"] = (digest(payloads["policy_manifest.json"]) + "\n").encode("ascii")
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in payloads.items():
        with (destination / name).open("xb") as handle:
            handle.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--development", action="store_true")
    modes.add_argument("--holdout", action="store_true")
    parser.add_argument("--bundle", type=Path, required=True, help="unchanged active bundle with 2012--2022 histories only")
    parser.add_argument("--output", type=Path, required=True, help="new output directory; existing paths refused")
    parser.add_argument("--holdout-input", type=Path, help="separate same-shape bundle with 2024-only history rows")
    parser.add_argument("--policy-manifest", type=Path)
    parser.add_argument("--policy-sha256", help="previously frozen exact policy_manifest.json SHA256")
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise ProtocolError("output exists; overwriting or resuming is forbidden")
        if args.development and any((args.holdout_input, args.policy_manifest, args.policy_sha256)):
            raise ProtocolError("development cannot receive holdout input or a holdout policy")
        if args.holdout and not all((args.holdout_input, args.policy_manifest, args.policy_sha256)):
            raise ProtocolError("holdout requires separate input, policy manifest, and exact SHA256")
        bundle, input_hash = read_json(args.bundle)
        histories = extract_histories(bundle)
        evaluator_hash = code_hash()
        if args.development:
            result = development(histories)
            result.update(input_sha256=input_hash, evaluator_sha256=evaluator_hash)
            result["published_g6_loeo_reference_pp"] = {
                c: bundle["share_models"]["control_" + c].get("evidence", {}).get("calculator_path_loeo_mae_by_industry", {})
                for c in CHAMBERS}
            manifest = freeze_manifest(result, input_hash, histories, evaluator_hash)
            write_outputs(args.output, result, manifest)
        else:
            manifest, actual_hash = read_json(args.policy_manifest)
            if actual_hash != args.policy_sha256:
                raise ProtocolError("frozen policy manifest SHA256 does not match")
            verify_manifest(manifest, input_hash, histories, evaluator_hash)
            # Read any held-out content only after every frozen-policy check passes.
            target_bundle, target_hash = read_json(args.holdout_input)
            target_histories = extract_histories(target_bundle, holdout=True)
            result = holdout(histories, target_histories, manifest)
            result.update(input_sha256=input_hash, holdout_input_sha256=target_hash,
                          policy_manifest_sha256=actual_hash, evaluator_sha256=evaluator_hash)
            result["holdout_metadata"] = {k: v for k, v in target_bundle.items() if k not in ("rows", "share_models")}
            write_outputs(args.output, result)
        print(f"{args.output}: {len(result['predictions'])} predictions; {result['failure_count']} failures; no publication")
        return 1 if result["failure_count"] else 0
    except (ProtocolError, OSError, json.JSONDecodeError) as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
