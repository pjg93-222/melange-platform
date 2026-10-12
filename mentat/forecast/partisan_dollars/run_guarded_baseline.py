"""Preserve the final fixed guard evaluation separately from both failed rounds."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from guarded_baseline import PROTOCOL, evaluate
from run_baseline_refinement import digest

def main():
    p=argparse.ArgumentParser()
    for name in ("input","protocol","output"):p.add_argument("--"+name,required=True)
    args=p.parse_args();out=Path(args.output)
    if out.exists():raise ValueError("refuse to overwrite a scored run")
    if json.loads(Path(args.protocol).read_bytes())!=PROTOCOL:raise ValueError("saved protocol mismatch")
    paths=[Path(args.input),Path(args.protocol)]+[Path(__file__).with_name(name) for name in (
        "guarded_baseline.py","test_guarded_baseline.py","run_guarded_baseline.py",
        "calibrated_baseline.py","baseline_refinement.py","predict_interval.py","run_baseline_refinement.py")]
    pins={str(path.resolve()):digest(path) for path in paths}
    prior=json.loads(Path(args.input).read_bytes())
    if any(digest(path)!=expected for path,expected in prior["input_sha256"].items()):raise ValueError("original calibration source changed")
    result=evaluate(prior)
    if any(digest(path)!=expected for path,expected in pins.items()):raise ValueError("input changed during evaluation")
    result.update(input_sha256=pins,built_at=datetime.now(timezone.utc).isoformat(),live_activation=False,original_vintage_validation=False)
    (out/"source").mkdir(parents=True)
    for path in paths[2:]:shutil.copyfile(path,out/"source"/path.name)
    (out/"evaluation.json").write_text(json.dumps(result,sort_keys=True,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    print(json.dumps(dict(gate=result["deployment_gate"],decisions=[{k:r[k] for k in ("chamber","target","apply_calibration","earlier_evaluation_cycles")} for r in result["decisions"]],guard_evaluable=[r for r in result["metrics"] if r["scope"]=="guard_evaluable_origins" and r["horizon"]=="ALL"]),indent=2))

if __name__=="__main__":main()
