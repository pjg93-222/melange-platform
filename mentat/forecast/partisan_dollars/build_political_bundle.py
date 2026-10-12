"""Publish a separately identified political-feature comparison to the existing UI.

The current registered static JS asset is reread by the app without a restart.
Original published PAC/company model pointers are never changed by this tool.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from fit_political import canonical, digest, load_features, make_rows
from predict_interval import predict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
from partisan_political_forecast import ACCOUNTING_BASIS, MODEL_ID, catalog

START = "// BEGIN GENERATED PARTISAN POLITICAL CATALOG"
END = "// END GENERATED PARTISAN POLITICAL CATALOG"


def build(evaluation, current_frames, feature_panel, profiles):
    rows = make_rows(current_frames, feature_panel, predict)
    if len(rows) != 10 or {r["target"] for r in rows} != {2026}:
        raise ValueError("all five current cohorts and both chambers required")
    if any(r["as_of"] > datetime.now(timezone.utc).date().isoformat() for r in rows):
        raise ValueError("future issue cannot be published as current")
    for row in rows:
        if "truth" in row:
            raise ValueError("current rows must not contain target truth")
        row.update(mode="experimental_current_forecast", baseline_family="historical_remainder", issue=row["as_of"])
        row["feature_provenance"]["current_champion_board_used_for_training"] = False
    return dict(schema_version=1, model_id=MODEL_ID, currency="USD", accounting_basis=ACCOUNTING_BASIS,
        active_variant="both", models=evaluation["final_2026_models"], current_rows=rows,
        current_profiles=profiles, current_champion_context_date="2026-10-06",
        built_at=datetime.now(timezone.utc).isoformat(),
        status="experimental_features_historical_test_did_not_improve_baseline",
        validation_summary={"kind": "reconstructed_retrospective", "metrics": evaluation["metrics"],
            "independent_election_targets_with_fitted_features": [2022, 2024],
            "warning": "Historical test did not improve the baseline; repeated horizons are not independent elections."},
        limitations=evaluation["protocol"]["limitations"] + [
            "The 2026 forecast uses the latest completed scheduled issue: July 20, with a June 30 transaction cut, reconstructed from later source editions.",
            "Banks and credit unions use broad industry political context; P&C uses only Allstate, Liberty Mutual and Travelers.",
            "Credit arms and telecom retain baseline forecasts because matching political context is unavailable.",
            "The fitted signal uses consistently defined prior-cycle leading-lawmakers averages.",
            "The current-champions scenario substitutes the actual board mean while holding lagged industry lean fixed. Applying the proxy coefficient to current champions is an unvalidated assumption; the historical accuracy numbers do not apply to that scenario."],
        provenance={"current_frame_provenance": current_frames.get("provenance", {}),
                    "fit_protocol": evaluation["protocol"]})


def main():
    p = argparse.ArgumentParser()
    for name in ("evaluation", "frames", "features", "profiles", "output", "js"):
        p.add_argument("--" + name, required=True)
    args = p.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("refusing to overwrite a published bundle")
    with Path(args.profiles).open(encoding="utf-8-sig", newline="") as handle:
        profiles = list(csv.DictReader(handle))
    bundle = build(json.loads(Path(args.evaluation).read_bytes()), json.loads(Path(args.frames).read_bytes()),
                   load_features(args.features), profiles)
    bundle["provenance"]["input_sha256"] = {str(Path(path).resolve()): digest(path) for path in (
        args.evaluation, args.frames, args.features, args.profiles, __file__,
        Path(__file__).with_name("fit_political.py"),
        Path(__file__).resolve().parents[2] / "app/partisan_political_forecast.py")}
    raw = canonical(bundle)
    rendered = catalog(bundle)
    rendered["validation_summary"] = bundle["validation_summary"]
    import hashlib
    rendered["bundle_sha256"] = hashlib.sha256(raw).hexdigest()
    javascript = Path(args.js).read_text(encoding="utf-8")
    if (START in javascript) != (END in javascript):
        raise ValueError("incomplete generated block")
    if START in javascript:
        start, tail = javascript.split(START, 1)
        _, after = tail.split(END, 1)
        javascript = start.rstrip() + after
    block = ("\n\n" + START + "\nwindow.MENTAT_PARTISAN_POLITICAL = " +
             json.dumps(rendered, sort_keys=True, allow_nan=False) +
             ";\nwindow.dispatchEvent(new Event('mentat-political-forecast-ready'));\n" + END + "\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    out.with_name("catalog.json").write_bytes(canonical(rendered))
    Path(args.js).write_text(javascript.rstrip() + block, encoding="utf-8")
    receipt = dict(bundle_path=str(out), bundle_sha256=rendered["bundle_sha256"],
                   catalog_sha256=digest(out.with_name("catalog.json")), js_path=args.js, js_sha256=digest(args.js),
                   current_rows=len(rendered["rows"]), adjusted=sum(r["status"] == "adjusted" for r in rendered["rows"]))
    out.with_name("PUBLICATION_RECEIPT.json").write_bytes(canonical(receipt))
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
