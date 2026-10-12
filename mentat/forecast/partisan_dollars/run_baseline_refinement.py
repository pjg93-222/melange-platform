"""Run the frozen baseline improvement experiment into a new immutable folder."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from baseline_refinement import PROTOCOL, evaluate


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--protocol", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    out = Path(args.output)
    if out.exists():
        raise ValueError("refuse to overwrite a scored run")
    if json.loads(Path(args.protocol).read_bytes()) != PROTOCOL:
        raise ValueError("saved pre-score protocol does not match")
    paths = [Path(args.input), Path(args.protocol)] + [Path(__file__).with_name(name) for name in (
        "baseline_refinement.py", "predict_interval.py", "test_baseline_refinement.py", "run_baseline_refinement.py")]
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
                      "nested_choices": [{k: r[k] for k in ("chamber", "target", "family")} for r in result["selections"]],
                      "final_choices": [{k: r[k] for k in ("chamber", "target", "family")} for r in result["final_2026_choices"]],
                      "all_origins": [r for r in result["metrics"] if r["scope"] == "all_origins" and r["chamber"] == "ALL" and r["horizon"] == "ALL"]}, indent=2))


if __name__ == "__main__":
    main()
