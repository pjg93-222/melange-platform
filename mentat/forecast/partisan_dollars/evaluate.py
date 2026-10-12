"""Offline partisan-dollar experiment: six fixed families, strictly earlier origins.

Primary outcomes are nominal D/R dollars in the preserved whole-industry panel.
Every evaluation year has been seen in prior research: no untouched-test claim.
No application imports, network, fitting of extra parameters, or publication.
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
import statistics
import sys

INDUSTRIES = ("auto_light_duty_manufacturing", "commercial_banks", "credit_unions", "property_casualty")
CODES = dict(zip(INDUSTRIES, ("T2100", "F03", "F05", "F09")))
CHAMBERS = ("House", "Senate")
PARTIES = ("D", "R")
YEARS = (2012, 2014, 2016, 2018, 2020, 2022, 2024)
OUTER = (2018, 2020, 2022, 2024)
INNER = (2016, 2018, 2020, 2022, 2024)
FAMILIES = ("prior_party", "same_phase_party", "equal_blend_party", "direct_party_half_growth",
            "joint_total_half_growth_prior_share", "joint_total_half_growth_blended_share")
TOL = 1e-12
PROTOCOL = {
    "id": "partisan-dollars-six-family-v1", "unit": "nominal_usd", "industries": list(INDUSTRIES),
    "parties": list(PARTIES), "chambers": list(CHAMBERS), "history_cycles": list(YEARS),
    "families_in_tie_order": list(FAMILIES), "outer_origins": list(OUTER), "inner_origins": list(INNER),
    "selection": "mean of eight industry-by-party WAPEs, equal stratum weight, all earlier inner origins",
    "minimum_inner_origins": 2, "tie_tolerance_wape_fraction": TOL, "fallback": "prior_party",
    "growth": "own series; last at most three consecutive two-year log changes ending strictly before target; median; multiply by 0.5; exponentiate; no clipping",
    "failures": "any missing/invalid cell makes the family ineligible on the complete common inner grid; never discard a stratum or failed origin",
    "zeros": "zero actual: APE and band hits undefined, positive-actual hit denominator only; exact-zero and false-positive dollars explicit; all-zero WAPE stratum makes macro selection unavailable",
    "negative_actuals": "outside contract: explicit input failure",
    "evaluation_status": "all origins previously examined; retrospective development, no untouched holdout",
    "scope": "whole-industry two-party House/Senate candidate giving; not a company, employee-only or fixed-roster PAC forecast",
    "source_vintage": "preserved retrospective source editions; not certified historical information availability",
    "final_policy": "one family per chamber selected through 2024 for 2026 research use; not independently validated",
}


class InputError(ValueError):
    pass


class ModelUnavailable(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def mean(values):
    return math.fsum(values) / len(values)


def finite_nonnegative(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise InputError(label + " must be finite and nonnegative")
    return float(value)


def validate_rows(rows):
    expected = {(c, s, y, p) for c in CHAMBERS for s in INDUSTRIES for y in YEARS for p in PARTIES}
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise InputError("history row must be an object")
        key = row.get("chamber"), row.get("industry"), row.get("cycle"), row.get("party")
        if type(row.get("cycle")) is not int or key not in expected:
            raise InputError("unexpected chamber, industry, party or cycle")
        if key in seen:
            raise InputError("duplicate history cell")
        seen.add(key)
        finite_nonnegative(row.get("actual_nominal_usd"), "actual_nominal_usd")
        finite_nonnegative(row.get("two_party_nominal_usd"), "two_party_nominal_usd")
        if row.get("opensecrets_code") != CODES[row["industry"]]:
            raise InputError("industry/source-code identity mismatch")
    if seen != expected:
        raise InputError("exact 112-cell history required; missing is not zero")
    lookup = {(r["chamber"], r["industry"], r["cycle"], r["party"]): r for r in rows}
    for c in CHAMBERS:
        for s in INDUSTRIES:
            for y in YEARS:
                d, r = (lookup[c, s, y, p] for p in PARTIES)
                total = d["actual_nominal_usd"] + r["actual_nominal_usd"]
                if not math.isclose(total, d["two_party_nominal_usd"], rel_tol=1e-12, abs_tol=1e-6) or not math.isclose(total, r["two_party_nominal_usd"], rel_tol=1e-12, abs_tol=1e-6):
                    raise InputError("D+R does not reconcile to the declared two-party total")
    return rows


def read_history(path):
    raw = Path(path).read_bytes()
    parsed = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    if parsed.fieldnames is None or len(parsed.fieldnames) != len(set(parsed.fieldnames)):
        raise InputError("missing or duplicate CSV column names")
    needed = {"chamber", "industry", "cycle", "party", "opensecrets_code", "actual_nominal_usd", "two_party_nominal_usd"}
    if not needed.issubset(parsed.fieldnames):
        raise InputError("input CSV lacks required original-dollar fields")
    rows = []
    for source in parsed:
        if None in source:
            raise InputError("CSV row has extra unnamed fields")
        try:
            row = {k: source[k] for k in ("chamber", "industry", "party", "opensecrets_code")}
            row.update(cycle=int(source["cycle"]), actual_nominal_usd=float(source["actual_nominal_usd"]),
                       two_party_nominal_usd=float(source["two_party_nominal_usd"]))
        except (TypeError, ValueError) as error:
            raise InputError("malformed numeric history field") from error
        rows.append(row)
    rows.sort(key=lambda r: (r["chamber"], r["cycle"], r["industry"], r["party"]))
    return validate_rows(rows), sha256(raw)


def half_growth(series):
    """Series maps earlier cycle to nonnegative dollars. No future target is supplied."""
    transitions = [(year - 2, year) for year in sorted(series) if year - 2 in series][-3:]
    if not transitions:
        raise ModelUnavailable("no earlier consecutive-cycle growth transition")
    logs, trace = [], []
    for start, end in transitions:
        before, after = series[start], series[end]
        if not math.isfinite(before) or not math.isfinite(after) or before <= 0 or after <= 0:
            raise ModelUnavailable("selected historical log-growth transition is nonpositive or invalid")
        change = math.log(after) - math.log(before)
        logs.append(change)
        trace.append({"start_cycle": start, "end_cycle": end, "start_nominal_usd": before,
                      "end_nominal_usd": after, "log_change": change})
    median = statistics.median(logs)
    try:
        factor = math.exp(0.5 * median)
    except OverflowError as error:
        raise ModelUnavailable("growth factor overflow") from error
    if not math.isfinite(factor) or factor <= 0:
        raise ModelUnavailable("growth factor is nonpositive or nonfinite")
    return factor, {"transitions": trace, "median_log_change": median, "shrinkage": 0.5, "growth_factor": factor}


def predict(rows, chamber, target, family):
    if chamber not in CHAMBERS or family not in FAMILIES or type(target) is not int or target % 2:
        raise ModelUnavailable("unsupported chamber, family or target")
    earlier = [r for r in rows if r["chamber"] == chamber and r["cycle"] < target]
    lookup = {(r["industry"], r["cycle"], r["party"]): r["actual_nominal_usd"] for r in earlier}
    years = sorted({r["cycle"] for r in earlier})
    result = []

    def amount(sector, year, party):
        key = sector, year, party
        if key not in lookup:
            raise ModelUnavailable(f"missing {sector}/{year}/{party}; no zero substitution")
        value = lookup[key]
        if not math.isfinite(value) or value < 0:
            raise ModelUnavailable("invalid historical giving amount")
        return value

    for sector in INDUSTRIES:
        prior = {p: amount(sector, target - 2, p) for p in PARTIES}
        need_same = family in ("same_phase_party", "equal_blend_party", "joint_total_half_growth_blended_share")
        same = {p: amount(sector, target - 4, p) for p in PARTIES} if need_same else None
        predictions, traces = {}, {}
        total_forecast = None
        if family in ("prior_party", "same_phase_party", "equal_blend_party"):
            for party in PARTIES:
                predictions[party] = prior[party] if family == "prior_party" else same[party] if family == "same_phase_party" else (prior[party] + same[party]) / 2
                traces[party] = {"prior_cycle": target - 2, "prior_nominal_usd": prior[party]}
                if same is not None:
                    traces[party].update(same_phase_cycle=target - 4, same_phase_nominal_usd=same[party])
        elif family == "direct_party_half_growth":
            for party in PARTIES:
                series = {year: amount(sector, year, party) for year in years}
                factor, trace = half_growth(series)
                predictions[party] = prior[party] * factor
                traces[party] = dict(trace, prior_cycle=target - 2, prior_nominal_usd=prior[party])
        else:
            totals = {year: math.fsum(amount(sector, year, p) for p in PARTIES) for year in years}
            factor, trace = half_growth(totals)
            prior_total = math.fsum(prior.values())
            if prior_total <= 0:
                raise ModelUnavailable("prior two-party denominator is zero")
            r_share = prior["R"] / prior_total
            if same is not None:
                same_total = math.fsum(same.values())
                if same_total <= 0:
                    raise ModelUnavailable("same-phase two-party denominator is zero")
                r_share = (r_share + same["R"] / same_total) / 2
            total_forecast = prior_total * factor
            predictions = {"R": total_forecast * r_share, "D": total_forecast * (1 - r_share)}
            shared = dict(trace, prior_cycle=target - 2, prior_two_party_nominal_usd=prior_total,
                          R_share_applied=r_share, total_forecast_nominal_usd=total_forecast)
            if same is not None:
                shared.update(same_phase_cycle=target - 4, same_phase_two_party_nominal_usd=math.fsum(same.values()))
            traces = {p: dict(shared) for p in PARTIES}
        if any(not math.isfinite(v) or v < 0 for v in predictions.values()):
            raise ModelUnavailable("nonfinite or negative prediction; no floor or clipping")
        combined = math.fsum(predictions.values())
        if total_forecast is not None and not math.isclose(combined, total_forecast, rel_tol=1e-12, abs_tol=1e-7):
            raise ModelUnavailable("joint party-dollar predictions do not conserve the total")
        for party in PARTIES:
            result.append({"chamber": chamber, "industry": sector, "party": party, "target": target,
                           "family": family, "prediction_nominal_usd": predictions[party],
                           "projected_two_party_nominal_usd": combined, "unit": "nominal_usd",
                           "training_years": years, "trace": traces[party], "clipped": False})
    return result


def score(predictions, rows, stage, selected_family=None):
    lookup = {(r["chamber"], r["industry"], r["cycle"], r["party"]): r["actual_nominal_usd"] for r in rows}
    output = []
    for prediction in predictions:
        key = prediction["chamber"], prediction["industry"], prediction["target"], prediction["party"]
        if key not in lookup:
            raise InputError("missing actual target cell")
        actual = finite_nonnegative(lookup[key], "target actual")
        predicted = finite_nonnegative(prediction["prediction_nominal_usd"], "prediction")
        error = predicted - actual
        ape = abs(error) / actual if actual > 0 else None
        output.append(dict(prediction, stage=stage, selected_family=selected_family,
                           actual_nominal_usd=actual, error_nominal_usd=error, absolute_error_nominal_usd=abs(error),
                           ape=ape, within_10pct=None if ape is None else ape <= 0.1 + TOL,
                           within_20pct=None if ape is None else ape <= 0.2 + TOL,
                           zero_actual=actual == 0, exact_zero_correct=predicted == 0 if actual == 0 else None,
                           zero_actual_false_positive_usd=predicted if actual == 0 else 0.0))
    return output


def metrics(records):
    if not records:
        raise InputError("cannot summarize an empty population")
    absolute = [r["absolute_error_nominal_usd"] for r in records]
    errors = [r["error_nominal_usd"] for r in records]
    total_actual = math.fsum(r["actual_nominal_usd"] for r in records)
    positive = [r for r in records if r["actual_nominal_usd"] > 0]
    apes = [r["ape"] for r in positive]
    hit10, hit20 = sum(r["within_10pct"] for r in positive), sum(r["within_20pct"] for r in positive)
    return {"n": len(records), "mae_nominal_usd": mean(absolute), "rmse_nominal_usd": math.sqrt(mean([e * e for e in errors])),
            "total_actual_nominal_usd": total_actual, "total_predicted_nominal_usd": math.fsum(r["prediction_nominal_usd"] for r in records),
            "wape": math.fsum(absolute) / total_actual if total_actual > 0 else None,
            "signed_bias": math.fsum(errors) / total_actual if total_actual > 0 else None,
            "mean_error_nominal_usd": mean(errors), "mape_positive_actual": mean(apes) if apes else None,
            "max_ape_positive_actual": max(apes) if apes else None,
            "max_absolute_error_nominal_usd": max(absolute), "positive_actual_count": len(positive),
            "within_10pct_count": hit10, "within_20pct_count": hit20,
            "within_10pct_rate": hit10 / len(positive) if positive else None,
            "within_20pct_rate": hit20 / len(positive) if positive else None,
            "zero_actual_count": len(records) - len(positive),
            "zero_actual_exact_count": sum(r["exact_zero_correct"] is True for r in records),
            "zero_actual_false_positive_count": sum(r["zero_actual"] and r["prediction_nominal_usd"] > 0 for r in records),
            "zero_actual_false_positive_usd": math.fsum(r["zero_actual_false_positive_usd"] for r in records)}


def macro_wape(records, origins):
    expected = {(s, p, y) for s in INDUSTRIES for p in PARTIES for y in origins}
    if len(records) != len(expected) or {(r["industry"], r["party"], r["target"]) for r in records} != expected:
        raise ModelUnavailable("incomplete common eight-stratum/origin scoring population")
    strata = {}
    for sector in INDUSTRIES:
        for party in PARTIES:
            value = metrics([r for r in records if r["industry"] == sector and r["party"] == party])["wape"]
            if value is None:
                raise ModelUnavailable("all-zero actual stratum has undefined WAPE; cannot silently discard it")
            strata[sector + ":" + party] = value
    return mean(list(strata.values())), strata


def select_family(rows, chamber, before):
    earlier = [r for r in rows if r["cycle"] < before]
    origins = [y for y in INNER if y < before]
    all_predictions, failures, scores, strata = [], [], {}, {}
    for family in FAMILIES:
        family_rows, failed = [], False
        for target in origins:
            try:
                predictions = predict(earlier, chamber, target, family)
                family_rows.extend(score(predictions, earlier, "inner_candidate"))
            except (InputError, ModelUnavailable, OverflowError) as error:
                failed = True
                failures.append({"chamber": chamber, "family": family, "target": target, "reason": str(error)})
        all_predictions.extend(family_rows)
        if failed or not origins:
            continue
        try:
            scores[family], strata[family] = macro_wape(family_rows, origins)
        except ModelUnavailable as error:
            failures.append({"chamber": chamber, "family": family, "target": None, "reason": str(error)})
    enough = len(origins) >= 2
    choice = "prior_party"
    if enough and scores:
        lowest = min(scores.values())
        choice = next(f for f in FAMILIES if f in scores and scores[f] <= lowest + TOL)
    return {"chamber": chamber, "selection_before": before, "inner_origins": origins,
            "selected_family": choice, "fallback": not enough or not scores,
            "selection_eligible": enough and choice in scores,
            "macro_wape_by_family": scores, "stratum_wape_by_family": strata,
            "ineligible_families": [f for f in FAMILIES if f not in scores], "failures": failures,
            "predictions": [dict(r, selected_family=choice) for r in all_predictions]}


def summarize(records):
    groups = {}
    for row in records:
        for industry in (row["industry"], "__ALL__"):
            for party in (row["party"], "__ALL__"):
                for target in (row["target"], "__ALL__"):
                    key = row["stage"], row["chamber"], row["family"], industry, party, str(target)
                    groups.setdefault(key, []).append(row)
    return [dict(zip(("stage", "chamber", "family", "industry", "party", "target"), key), **metrics(rows))
            for key, rows in sorted(groups.items())]


def evaluate(rows):
    validate_rows(rows)
    records, failures, nested, final = [], [], {}, {}
    for chamber in CHAMBERS:
        nested[chamber] = {}
        for target in OUTER:
            selection = select_family(rows, chamber, target)
            nested[chamber][str(target)] = selection
            for family in FAMILIES:
                try:
                    raw = predict(rows, chamber, target, family)
                    scored = score(raw, rows, "outer_candidate", selection["selected_family"])
                    records.extend(scored)
                    if family == selection["selected_family"]:
                        records.extend(dict(r, stage="outer_policy", family="nested_policy") for r in scored)
                except (InputError, ModelUnavailable, OverflowError) as error:
                    failures.append({"chamber": chamber, "target": target, "family": family,
                                     "selected_at_origin": family == selection["selected_family"], "reason": str(error)})
        final[chamber] = select_family(rows, chamber, 2026)
    macro = []
    for chamber in CHAMBERS:
        for stage, family in [("outer_candidate", f) for f in FAMILIES] + [("outer_policy", "nested_policy")]:
            selected = [r for r in records if r["chamber"] == chamber and r["stage"] == stage and r["family"] == family]
            try:
                value, strata = macro_wape(selected, OUTER)
                macro.append({"chamber": chamber, "stage": stage, "family": family, "macro_wape": value,
                              "stratum_wape": strata, "complete": True})
            except ModelUnavailable as error:
                macro.append({"chamber": chamber, "stage": stage, "family": family, "macro_wape": None,
                              "complete": False, "reason": str(error)})
    return {"schema_version": 1, "mode": "retrospective_partisan_dollar_development", "protocol": PROTOCOL,
            "predictions": records, "metrics": summarize(records), "macro_metrics": macro,
            "nested_selection": nested, "final_2026_selection": final, "failures": failures,
            "outer_failure_count": len(failures), "inner_failure_count": sum(len(v["failures"]) for choices in nested.values() for v in choices.values()) + sum(len(v["failures"]) for v in final.values()),
            "live_activation": False, "accuracy_target_claimed": False, "untouched_test_claimed": False}


def csv_bytes(rows):
    fields = list(dict.fromkeys(key for row in rows for key in row))
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({k: json.dumps(v, sort_keys=True, allow_nan=False) if isinstance(v, (dict, list)) else v for k, v in row.items()})
    return stream.getvalue().encode("utf-8-sig")


def write_outputs(destination, result, input_hash, evaluator_hash, protocol_file_hash):
    policy = {"schema_version": 1, "kind": "frozen_retrospective_partisan_dollar_policy", "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "input_sha256": input_hash, "evaluator_sha256": evaluator_hash, "protocol_file_sha256": protocol_file_hash,
              "protocol": PROTOCOL, "protocol_sha256": sha256(canonical(PROTOCOL)), "training_through": 2024,
              "intended_forecast_cycle": 2026, "untouched_test_claimed": False, "live_activation": False,
              "selections": {c: {k: v for k, v in result["final_2026_selection"][c].items() if k != "predictions"} for c in CHAMBERS}}
    result = dict(result, input_sha256=input_hash, evaluator_sha256=evaluator_hash, protocol_file_sha256=protocol_file_hash)
    payloads = {"evaluation.json": canonical(result), "predictions.csv": csv_bytes(result["predictions"]),
                "metrics.csv": csv_bytes(result["metrics"]), "macro_metrics.csv": csv_bytes(result["macro_metrics"]),
                "policy_manifest.json": canonical(policy)}
    payloads["policy_manifest.sha256"] = (sha256(payloads["policy_manifest.json"]) + "\n").encode()
    destination.mkdir(parents=True, exist_ok=False)
    for name, content in payloads.items():
        with (destination / name).open("xb") as stream:
            stream.write(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--protocol-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise InputError("output exists; overwriting or resuming is forbidden")
        protocol_hash = sha256(args.protocol.read_bytes())
        if protocol_hash != args.protocol_sha256:
            raise InputError("predeclared protocol hash mismatch")
        rows, input_hash = read_history(args.input)
        result = evaluate(rows)
        write_outputs(args.output, result, input_hash, sha256(Path(__file__).read_bytes()), protocol_hash)
        print(json.dumps({"output": str(args.output), "predictions": len(result["predictions"]),
                          "outer_failures": result["outer_failure_count"], "inner_failures": result["inner_failure_count"],
                          "final_families": {c: s["selected_family"] for c, s in result["final_2026_selection"].items()}, "live_activation": False}))
        return 1 if result["outer_failure_count"] or result["inner_failure_count"] else 0
    except (InputError, OSError, ValueError, OverflowError) as error:
        print("REFUSED: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
