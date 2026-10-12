"""One immutable evaluation of the fixed, exploratory remainder calibration."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from calibrated_baseline import PROTOCOL, evaluate
from run_baseline_refinement import digest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--protocol", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("refuse to overwrite a scored run")
    if json.loads(Path(args.protocol).read_bytes()) != PROTOCOL:
        raise ValueError("saved pre-score protocol does not match")
    paths = [Path(args.input), Path(args.protocol)] + [Path(__file__).with_name(name) for name in (
        "calibrated_baseline.py", "test_calibrated_baseline.py", "run_calibrated_baseline.py",
        "baseline_refinement.py", "run_baseline_refinement.py", "predict_interval.py")]
    pins = {str(path.resolve()): digest(path) for path in paths}
    result = evaluate(json.loads(Path(args.input).read_bytes()))
    if any(digest(path) != expected for path, expected in pins.items()):
        raise ValueError("an input changed during evaluation")
    result.update(input_sha256=pins, built_at=datetime.now(timezone.utc).isoformat(),
                  live_activation=False, original_vintage_validation=False)
    (out / "source").mkdir(parents=True)
    for path in paths[2:]:
        shutil.copyfile(path, out / "source" / path.name)
    (out / "evaluation.json").write_text(json.dumps(result, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"gate": result["deployment_gate"],
                      "models": [{k:r[k] for k in ("chamber", "target", "alpha", "status", "training_cycles")} for r in result["models"]],
                      "final_2026": [{k:r[k] for k in ("chamber", "target", "alpha", "status")} for r in result["final_2026_models"]],
                      "all_origins": [r for r in result["metrics"] if r["scope"] == "all_origins" and r["chamber"] == "ALL" and r["horizon"] == "ALL"]}, indent=2))


if __name__ == "__main__":
    main()
