"""Build a separate baseline comparison without changing any political variant."""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import calibrated_baseline as calibration
import guarded_baseline as guard
from run_baseline_refinement import digest

START = "// BEGIN GENERATED PARTISAN POLITICAL CATALOG"
END = "// END GENERATED PARTISAN POLITICAL CATALOG"


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)+"\n").encode("utf-8")


def refined_row(old, frame, model, decision):
    if any(k in frame for k in ("truth", "truth_intervals")):
        raise ValueError("current frame must not contain target truth")
    if any(old[k]!=frame[k] for k in ("sector","chamber","target","horizon","cut","issue")):
        raise ValueError("current comparison identities differ")
    if model["target"]!=frame["target"] or model["chamber"]!=frame["chamber"] or decision["target"]!=frame["target"] or decision["chamber"]!=frame["chamber"]:
        raise ValueError("current calibration/guard identities differ")
    if any(y>=frame["target"] for y in model["training_cycles"]+decision["earlier_evaluation_cycles"]):
        raise ValueError("current projection includes target or future outcomes")
    result=copy.deepcopy(old)
    result.update(variant="refined_baseline",status="refined_baseline" if decision["apply_calibration"] else "baseline_retained",
                  baseline_family="guarded_historical_remainder",features={},feature_provenance={},
                  delta_by_party_cents={},refinement_alpha=model["alpha"] if decision["apply_calibration"] else 1.0,
                  guard_reason=decision["reason"],issues=[],delta_cents=0.0,raw_delta_cents=0.0)
    for row in calibration.prepare_frame(frame):
        party=row["party"]
        if row["baseline"]!=old["baseline"][party]:raise ValueError("original baseline changed")
        current=calibration.project(row,model) if decision["apply_calibration"] else row
        result["adjusted"][party]=copy.deepcopy(current["prediction"])
        result["delta_by_party_cents"][party]=calibration.midpoint(current["prediction"])-calibration.midpoint(row["baseline"])
        if current["fallback_reason"]:
            result["issues"].append(party+": "+current["fallback_reason"])
    return result


def build(prior, evaluation, frames):
    if evaluation["protocol"]!=guard.PROTOCOL or not evaluation["deployment_gate"]["passed"]:
        raise ValueError("fixed guarded baseline must clear its saved research gate")
    expected={(s,c) for s in guard.ref.SECTORS for c in ("House","Senate")}
    lookup={(f["sector"],f["chamber"]):f for f in frames["feature_frames"]}
    oldkeys=[(r["sector"],r["chamber"]) for r in prior["rows"]]
    if len(lookup)!=10 or len(frames["feature_frames"])!=10 or set(lookup)!=expected or set(oldkeys)!=expected or len(oldkeys)!=10:
        raise ValueError("all five current cohorts and both chambers required")
    models={m["chamber"]:m for m in evaluation["final_2026_models"]}
    choices={d["chamber"]:d for d in evaluation["decisions"] if d["target"]==2026}
    output=copy.deepcopy(prior)
    output.update(active_variant="refined_baseline",built_at=datetime.now(timezone.utc).isoformat(),status="experimental_guarded_baseline_small_retrospective_gain")
    output["rows"]=[]
    for old in prior["rows"]:
        key=old["sector"],old["chamber"];frame=lookup[key]
        if frame["target"]!=2026 or frame["issue"]>datetime.now(timezone.utc).date().isoformat():raise ValueError("current completed issue required")
        variants=copy.deepcopy(old["variants"])
        variants["refined_baseline"]=refined_row(variants["baseline"],frame,models[key[1]],choices[key[1]])
        active=copy.deepcopy(variants["refined_baseline"]);active["variants"]=variants
        output["rows"].append(active)
    output["baseline_refinement"]=dict(model_id=guard.PROTOCOL["id"],protocol=guard.PROTOCOL,decisions=evaluation["decisions"],
        final_models=evaluation["final_2026_models"],
        validation_summary=dict(metrics=evaluation["metrics"],guard_evaluable_cycles=[2024],
            warning="Small adaptive retrospective gain; only 2024 evaluates a nondefault guard decision. No untouched holdout or future accuracy guarantee."),
        provenance=dict(current_frame_provenance=frames.get("provenance",{})),
        original_political_variants_unchanged=True)
    output["limitations"]=list(dict.fromkeys(prior.get("limitations",[])+guard.PROTOCOL["limitations"]))
    return output


def main():
    p=argparse.ArgumentParser()
    for name in ("prior-catalog","evaluation","frames","output"):p.add_argument("--"+name,required=True)
    args=p.parse_args();out=Path(args.output)
    if out.exists():raise ValueError("refuse to overwrite a versioned baseline catalog")
    paths=[Path(args.prior_catalog),Path(args.evaluation),Path(args.frames)]+[Path(__file__).with_name(n) for n in (
        "build_refined_baseline.py","guarded_baseline.py","calibrated_baseline.py","baseline_refinement.py","predict_interval.py")]
    pins={str(path.resolve()):digest(path) for path in paths}
    evaluation=json.loads(Path(args.evaluation).read_bytes())
    if any(digest(path)!=expected for path,expected in evaluation["input_sha256"].items()):raise ValueError("frozen evaluation input changed")
    prior=json.loads(Path(args.prior_catalog).read_bytes())
    rendered=build(prior,evaluation,json.loads(Path(args.frames).read_bytes()))
    rendered["baseline_refinement"]["provenance"]["input_sha256"]=pins
    rendered["baseline_refinement"]["provenance"]["prior_bundle_sha256"]=prior["bundle_sha256"]
    rendered["baseline_refinement"]["provenance"]["prior_catalog_sha256"]=digest(args.prior_catalog)
    rendered.pop("bundle_sha256",None)
    bundle=dict(schema_version=1,model_id=guard.PROTOCOL["id"],catalog=rendered,input_sha256=pins)
    raw=canonical(bundle);rendered=copy.deepcopy(rendered);rendered["bundle_sha256"]=hashlib.sha256(raw).hexdigest()
    if any(digest(path)!=expected for path,expected in pins.items()):raise ValueError("input changed during build")
    out.mkdir(parents=True)
    (out/"bundle.json").write_bytes(raw)
    (out/"catalog.json").write_bytes(canonical(rendered))
    receipt=dict(bundle_sha256=rendered["bundle_sha256"],catalog_sha256=digest(out/"catalog.json"),rows=len(rendered["rows"]),published=False)
    (out/"BUILD_RECEIPT.json").write_bytes(canonical(receipt))
    print(json.dumps(receipt,indent=2))


if __name__=="__main__":main()
