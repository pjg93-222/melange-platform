"""Reproducible chronological political-feature fit; writes a new research run."""
import argparse
import json
from pathlib import Path
import sys

from fit_political import (FEATURES, PROTOCOL, aggregate, canonical, digest, fit,
                           load_features, make_rows, score, usable)
from predict_interval import predict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "app"))
from partisan_political_forecast import project


def evaluate(saved, features):
    rows = make_rows(saved, features, predict)
    models, predictions, projections = [], [], []
    for target in sorted({r["target"] for r in rows}):
        fitted = {variant: fit(rows, variant, target) for variant in FEATURES}
        models.append(dict(target=target, variants=fitted))
        for row in (r for r in rows if r["target"] == target):
            inference_row = {k: v for k, v in row.items() if k != "truth"}
            eligible = fitted["both"]["status"] == "fitted" and usable(row, FEATURES["both"])
            for variant, model in fitted.items():
                output = project(inference_row, model, variant)
                projections.append(output)
                for party in ("D", "R"):
                    predictions.append({**{k: row[k] for k in ("sector", "chamber", "target", "horizon")},
                        "variant": variant, "party": party,
                        "prediction": output["adjusted"][party], "truth": row["truth"][party],
                        "status": output["status"], "delta_cents": output["delta_cents"],
                        "clamped": output["clamped"], "feature_fit_eligible": eligible,
                        **score(output["adjusted"][party], row["truth"][party])})
    if len(predictions) != len(rows) * 6:
        raise ValueError("complete grid required")
    final_models = {variant: fit(rows, variant, 2026) for variant in FEATURES}
    return dict(protocol=PROTOCOL, training_rows=rows, outer_models=models,
                final_2026_models=final_models, predictions=predictions,
                projections=projections, metrics=aggregate(predictions))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--features", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("refusing to overwrite a scored run")
    if json.loads(Path(args.protocol).read_bytes()) != PROTOCOL:
        raise ValueError("pre-score protocol differs from implementation")
    result = evaluate(json.loads(Path(args.baseline).read_bytes()), load_features(args.features))
    result["input_sha256"] = {str(Path(path).resolve()): digest(path) for path in (
        args.baseline, args.features, args.protocol, __file__,
        Path(__file__).with_name("fit_political.py"),
        Path(__file__).resolve().parents[2] / "app/partisan_political_forecast.py")}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(canonical(result))
    print(json.dumps([r for r in result["metrics"] if r["horizon"] == "ALL" and r["chamber"] == "ALL"], indent=2))


if __name__ == "__main__":
    main()
