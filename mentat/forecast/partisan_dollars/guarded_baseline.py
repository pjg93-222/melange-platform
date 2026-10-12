"""Adaptive round 3: use remainder calibration only after earlier OOF benefit."""
import copy
import baseline_refinement as ref
import calibrated_baseline as calibration

PROTOCOL = {
    "id": "pac-guarded-baseline-v1", "adaptive_exploratory_round": 3,
    "designed_after": "round001 and round002 failed their deployment gates",
    "policies": 1, "baseline": "unchanged historical_remainder",
    "calibration": calibration.PROTOCOL,
    "guard": "per chamber, compare earlier chronological calibrated predictions against original baseline, restricting to targets whose calibration had at least two earlier eligible origins and fitted status",
    "minimum_guard_origins": 1,
    "guard_gate": "use calibration only if earlier cert20 improves, or ties with better cert10, and mean normalized upper absolute error does not worsen (1e-12 tolerance); otherwise exact baseline",
    "current_fit": "refit fixed round002 calibration using all earlier target cycles; guard uses earlier out-of-time candidate predictions, never current target truth",
    "deployment_gate": ref.PROTOCOL["deployment_gate"],
    "report_scopes": "all_origins, original2022/24comparison, guard_evaluable2024only, warmup2018/20/22",
    "limitations": calibration.PROTOCOL["limitations"] + [
        "Final guard was designed after inspecting round002; this is adaptive exploratory research.",
        "Only 2024 can evaluate the guard after one earlier calibrated prediction cycle; repeated horizons are not independent elections.",
        "One earlier evaluation is a minimal safeguard, not strong evidence of future reliability."],
}


def identity(row):
    return tuple(row[k] for k in ("sector", "chamber", "party", "target", "horizon"))


def choose(baseline, candidates, chamber, target):
    eligible = [r for r in candidates if r["chamber"] == chamber and r["target"] < target and r["calibration_status"] == "fitted"]
    keys = [identity(r) for r in eligible]
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate guard candidate identity")
    expected = set(keys)
    comparable = [r for r in baseline if identity(r) in expected]
    if len(comparable) != len(keys) or {identity(r) for r in comparable} != expected:
        raise ValueError("guard baseline/candidate identities differ")
    years = sorted({r["target"] for r in eligible})
    decision = dict(chamber=chamber, target=target, earlier_evaluation_cycles=years,
                    apply_calibration=False, reason="no earlier evaluated calibration origin",
                    baseline_metrics=None, candidate_metrics=None)
    if not years:
        return decision
    b, n = ref.metrics(comparable), ref.metrics(eligible)
    tolerance = n["certified_within20"] > b["certified_within20"] or (n["certified_within20"] == b["certified_within20"] and n["certified_within10"] > b["certified_within10"])
    error = n["mean_normalized_upper_error"] <= b["mean_normalized_upper_error"] + 1e-12
    approved = tolerance and error
    return dict(decision, apply_calibration=approved, reason="earlier calibration benefit" if approved else "earlier calibration did not clear guard",
                baseline_metrics=b, candidate_metrics=n)


def evaluate(round2):
    if round2["protocol"] != calibration.PROTOCOL:
        raise ValueError("round002 protocol mismatch")
    baseline, candidates = round2["baseline"], round2["predictions"]
    expected = {(s,c,p,y,h) for s in ref.SECTORS for c in ("House","Senate") for p in ("D","R") for y in (2018,2020,2022,2024) for h in ref.PROTOCOL["horizons"]}
    for rows in (baseline,candidates):
        if len(rows)!=len(expected) or {identity(r) for r in rows}!=expected:
            raise ValueError("complete unique evaluation grid required")
    lookup={identity(r):r for r in baseline}
    model_lookup={(r["chamber"],r["target"]):r for r in round2["models"]}
    if len(model_lookup)!=8 or len(round2["models"])!=8:
        raise ValueError("exact original chronological model grid required")
    for row in candidates:
        model=model_lookup[row["chamber"],row["target"]]
        if row["calibration_status"]!=model["status"] or any(y>=row["target"] for y in model["training_cycles"]):
            raise ValueError("shadow prediction chronology mismatch")
        if model["status"]=="fitted" and len(set(model["training_cycles"]))<2:
            raise ValueError("fitted shadow model lacks two earlier origins")
        if row["issue"][:4]!=str(row["target"]) or row["cut"]>row["issue"]:
            raise ValueError("invalid issue/cut chronology")
    decisions=[choose(baseline,candidates,c,y) for y in (2018,2020,2022,2024,2026) for c in ("House","Senate")]
    choices={(d["chamber"],d["target"]):d for d in decisions}
    predicted=[]
    for row in candidates:
        choice=choices[row["chamber"],row["target"]]
        selected=copy.deepcopy(row if choice["apply_calibration"] else lookup[identity(row)])
        selected.update(family="guarded_baseline", calibration_applied=choice["apply_calibration"], guard_reason=choice["reason"])
        predicted.append(selected)
    metrics=[]
    for family,rows in (("baseline",baseline),("guarded_baseline",predicted)):
        for scope,years in (("all_origins",[2018,2020,2022,2024]),("evaluated_origins",[2022,2024]),("guard_evaluable_origins",[2024]),("warmup_origins",[2018,2020,2022])):
            for chamber in ("ALL","House","Senate"):
                for horizon in ("ALL",*ref.PROTOCOL["horizons"]):
                    group=[r for r in rows if r["target"] in years and (chamber=="ALL" or r["chamber"]==chamber) and (horizon=="ALL" or r["horizon"]==horizon)]
                    metrics.append(dict(family=family,scope=scope,chamber=chamber,horizon=horizon,**ref.metrics(group)))
    b,n=[next(r for r in metrics if r["family"]==f and r["scope"]=="evaluated_origins" and r["chamber"]=="ALL" and r["horizon"]=="ALL") for f in ("baseline","guarded_baseline")]
    tolerance=n["certified_within20"]>b["certified_within20"] or (n["certified_within20"]==b["certified_within20"] and n["certified_within10"]>b["certified_within10"])
    chambers={c:ref.metrics([r for r in predicted if r["target"] in (2022,2024) and r["chamber"]==c])["certified_within20"]>=ref.metrics([r for r in baseline if r["target"] in (2022,2024) and r["chamber"]==c])["certified_within20"] for c in ("House","Senate")}
    error=n["mean_normalized_upper_error"]<=b["mean_normalized_upper_error"]+1e-12
    regressions=[]
    for family,rows in (("baseline",baseline),("guarded_baseline",predicted)):
        for sector in ref.SECTORS:
            for chamber in ("House","Senate"):
                for party in ("D","R"):
                    for year in ("ALL",2018,2020,2022,2024):
                        for horizon in ("ALL",*ref.PROTOCOL["horizons"]):
                            group=[r for r in rows if r["sector"]==sector and r["chamber"]==chamber and r["party"]==party and (year=="ALL" or r["target"]==year) and (horizon=="ALL" or r["horizon"]==horizon)]
                            regressions.append(dict(family=family,sector=sector,chamber=chamber,party=party,target=year,horizon=horizon,**ref.metrics(group)))
    return dict(protocol=PROTOCOL,baseline=baseline,predictions=predicted,decisions=decisions,metrics=metrics,regression_metrics=regressions,
                final_2026_models=round2["final_2026_models"],
                deployment_gate=dict(passed=tolerance and all(chambers.values()) and error,tolerance_improved=tolerance,
                                     chamber_non_degradation=chambers,normalized_error_non_degradation=error,baseline=b,candidate=n))
