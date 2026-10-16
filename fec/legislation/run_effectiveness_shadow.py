"""Reproduce completed-cohort and current-term comparisons from local evidence.

No network, production writes, or implicit policy promotion. Every generation
has its own manifest, canonical rows, policy, source hashes and explanations.
"""
from __future__ import annotations
import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date
import hashlib
import html
import json
import os
from pathlib import Path
import sys

FEC = Path(__file__).resolve().parents[1]
if str(FEC) not in sys.path: sys.path.insert(0, str(FEC))
from legislation.build_bill_milestones import build_artifacts, sha256_file
from legislation.build_bill_impact import FrequencyPolicy, build_impacts, publication_registry
from legislation.effectiveness_sources import read_json, read_rows, historical_roster, accepted_signals, publications_from_local
from engine.legislative_effectiveness import ScoringPolicy, compute_effectiveness
from engine.effectiveness_integration import build_shadow_comparison, write_shadow_comparison, compare_board_and_priority


INPUTS = ("legislation_routing_audit.json", "bill_repository_live.json", "member_desk_bundle.json",
          "ranks_python.json", "bill_beneficiary_tags.ndjson", "data/lobbying/industry_axes.ndjson",
          "data/engine/primacy_factors.json", "data/engine/live_legislative_pillar.json",
          "press_text_cache.jsonl", "data/stance/press_text_cache_priority.jsonl", "data/stance/press_refresh_fulltext.jsonl",
          "data/stance/press_refresh_tone.csv", "grievance_ledger.ndjson", "delivery_ledger.ndjson")
PRODUCTION = ("ranks_python.json", "data/engine/primacy_factors.json", "data/lobbying/industry_tiers.json",
              "member_desk_bundle.json", "dashboard_bundle.json", "data/engine/im_allocation.json")


def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n", encoding="utf-8", newline='\r\n')
    os.replace(temporary,path)


def write_rows(path, rows):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name+".tmp")
    with temporary.open("w",encoding="utf-8", newline='\r\n') as handle:
        for row in rows: handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False)+"\n")
    os.replace(temporary,path)


def source_hashes(paths):
    return {str(Path(p).resolve()):sha256_file(p) for p in paths if Path(p).is_file()}


def implementation_paths():
    return [FEC/"legislation"/name for name in ("build_bill_milestones.py","build_bill_impact.py","effectiveness_sources.py","run_effectiveness_shadow.py")] + [
        FEC/"engine"/name for name in ("legislative_effectiveness.py","effectiveness_integration.py","primacy_factors.py","live_legislative_pillar.py","pillar_policy.py")] + [
        FEC/"bill_rules.py", FEC/"tier_util.py", FEC/"moneyball_core.py", FEC/"legislation_measure_key.py", FEC/"lobbying/industry_tiers.py"]


def object_hash(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _render_review(comparison, summary, path):
    """Self-contained review surface; per-member bill/stage/source explanations."""
    rows = comparison["members"]
    def fmt(v): return "unavailable" if v is None else f"{v:.5f}" if isinstance(v,float) else str(v)
    markup = []
    for name,row in sorted(rows.items(),key=lambda it:(it[1].get("results_only_rank") or 99999,it[0])):
        changes = {key:row.get(key) for key in ("legacy_rank","results_only_rank","results_only_common_rank_delta","advocacy_composite_rank","advocacy_composite_common_rank_delta","realized_results","orientation_factor")}
        traces = row.get("explanation",[])
        payload = {"scores":changes,"standard":row.get("standard"),"tensor":row.get("tensor_results"),"institutional_context":row.get("institutional_context"),"coverage":row.get("coverage"),"contributions":traces}
        markup.append(f'<tr><td><details><summary>{html.escape(name)}</summary><pre>{html.escape(json.dumps(payload,ensure_ascii=False,indent=2))}</pre></details></td><td>{html.escape(row["chamber"])}</td>'+"".join(f'<td>{fmt(changes[k])}</td>' for k in changes)+"</tr>")
    path.write_text('<!doctype html><meta charset="utf-8"><title>Legislative effectiveness shadow review</title><style>body{font:15px system-ui;margin:28px;color:#17252c}table{border-collapse:collapse;width:100%}td,th{padding:9px;border-bottom:1px solid #ddd;text-align:left}pre{white-space:pre-wrap;max-height:420px;overflow:auto;max-width:800px}summary{cursor:pointer}input{padding:9px;width:350px}</style>'
                    +f'<h1>{comparison["congress"]} Congress — shadow comparison</h1><p>{html.escape(comparison["comparison_scope"])}</p><p>As of {comparison["as_of"]}. Impact 1/5/10; baseline SS coverage unavailable. Click a member for bill-stage calculations and institutional context.</p>'
                    +f'<details><summary>Generation and coverage</summary><pre>{html.escape(json.dumps(summary,indent=2))}</pre></details>'
                    +'<p><input id="find" placeholder="Filter member names" aria-label="Filter member names"></p><table><thead><tr><th>Member / explanations</th><th>Chamber</th><th>Legacy rank</th><th>Results rank</th><th>Common delta</th><th>Composite rank</th><th>Common delta</th><th>Tensor net</th><th>Orientation</th></tr></thead><tbody>'+"".join(markup)
                    +'</tbody></table><script>document.querySelector("#find").oninput=e=>document.querySelectorAll("tbody tr").forEach(r=>r.hidden=!r.querySelector("summary").textContent.toLowerCase().includes(e.target.value.toLowerCase()))</script>',encoding="utf-8", newline='\r\n')


def run(args):
    source=Path(args.source_root).resolve(); output=Path(args.output_root).resolve()
    if output == source or source in output.parents:
        raise ValueError("Use an isolated output directory outside the read-only original source workspace")
    if output == FEC.parent: raise ValueError("Output root must be a dedicated shadow directory")
    if date.fromisoformat(args.as_of).isoformat()!=args.as_of: raise ValueError("--as-of must be YYYY-MM-DD")
    output.mkdir(parents=True,exist_ok=True)
    write_json(output/"shadow_generation.json", {"schema":"effectiveness-shadow-generation-v1","status":"building","production_activation":False})
    service_paths = [Path(args.service_reference)/name for name in ("legislators-current.yaml","legislators-historical.yaml")] if args.service_reference else []
    paths=[source/rel for rel in INPUTS+PRODUCTION]+[source/f"votes/HS{congress}_members.csv" for congress in args.congress]+service_paths
    paths.append(Path(args.draft_root)/"cu_press_stance_tone.csv")
    initial=source_hashes(paths)
    initial_code=source_hashes(implementation_paths())
    legacy_ranks=read_json(source/"ranks_python.json",{})
    legacy_factors=read_json(source/"data/engine/primacy_factors.json",{})
    current_context={m.get("bio",{}).get("bioguide"):m for m in read_json(source/"member_desk_bundle.json",{}).get("members",[])}
    all_summaries=[]
    for congress in args.congress:
        print(f"Building Congress {congress}: full local archives",flush=True)
        target=output/str(congress); evidence=target/"data/legislation"; engine=target/"data/engine"
        engine.mkdir(parents=True,exist_ok=True)
        archive=Path(args.archive_root)/f"billstatus{congress}"
        manifest=build_artifacts(archive,evidence,as_of=args.as_of)
        missing=set(manifest["missing_archive_measure_types"].get(str(congress),[])) & {"hr","s","hjres","sjres"}
        if missing: raise ValueError("Incomplete full-chamber source archives: "+str(sorted(missing)))
        bills=read_rows(evidence/"bill_milestones.ndjson"); roles=read_rows(evidence/"member_measure_roles.ndjson")
        roster,context,population=historical_roster(source,congress,args.as_of,service_paths=service_paths)
        signals,identities,weights,letters,acceptance=accepted_signals(source,bills,congress,args.as_of)
        if len(weights)!=18: raise ValueError("All 18 canonical category priorities are required")
        documents=publications_from_local(source,args.draft_root,bills)
        registry=publication_registry(documents,as_of=args.as_of)
        frequency_policy=FrequencyPolicy()
        impacts=build_impacts(bills,registry,as_of=args.as_of,policy=frequency_policy)
        write_rows(evidence/"bill_media_evidence.ndjson",registry)
        write_rows(evidence/"bill_impact.ndjson",impacts)
        write_rows(evidence/"member_population.ndjson",roster)
        write_rows(evidence/"accepted_signals.ndjson",signals)
        write_rows(evidence/"congressional_letter_coverage.ndjson",letters)
        manifest["historical_member_population"]={"status":population["certification"],"normalization_N":population["by_chamber"],
            "convention":population["member_convention"],"receipt":"population_receipt.json"}
        manifest["artifacts"].update({p.name:{"sha256":sha256_file(p),"bytes":p.stat().st_size}
            for p in (evidence/"member_population.ndjson",evidence/"bill_impact.ndjson",evidence/"bill_media_evidence.ndjson",evidence/"accepted_signals.ndjson")})
        write_json(evidence/"effectiveness_universe_manifest.json",manifest)
        policy=ScoringPolicy(cosponsor_half_life_days=args.half_life,category_weights=weights)
        print(f"Scoring {len(bills)} measures, {len(roles)} dated roles, {len(roster)} chamber-member records",flush=True)
        score=compute_effectiveness(bills,roles,impacts,roster,signals,as_of=args.as_of,policy=policy,institutional_context=context)
        if args.verify_reproducible:
            repeat=compute_effectiveness(bills,roles,impacts,roster,signals,as_of=args.as_of,policy=policy,institutional_context=context)
            if object_hash(score)!=object_hash(repeat): raise RuntimeError("Scoring reproducibility mismatch")
            score["reproducibility"]={"same_frozen_input_repeat_equal":True,"score_sha256":object_hash(repeat)}
            del repeat
        score["evidence_manifest_sha256"]=sha256_file(evidence/"effectiveness_universe_manifest.json")
        term_end=f"{1789+2*congress}-01-03"
        score["label"]=("completed_congress_retrospective_snapshot" if args.as_of>=term_end else "historical_asof_partial_term") if congress<119 else "current_term_provisional"
        score["impact_policy"]=asdict(frequency_policy)
        score["annotation_scope"]="Current accepted CU annotations; dated official activities and source publications censored at stated cutoff"
        score["letter_coverage"]=acceptance["counts"].get("letter_signature_date_and_document_unverified",0)
        score["population_receipt"]=population
        for member in score["members"]:
            # Current titles are display context, never projected into historical service.
            current=current_context.get(member["bioguide_id"])
            if current:
                member["institutional_context"].append({"scope":"current_display_context_not_historical",
                    "committees":current.get("committees"),"formal_office":current.get("formal_role_influence_office"),
                    "current_influence_percentile":current.get("comm_n"),"adjustment_to_results":0})
        comparison=build_shadow_comparison(score,identities,legacy_ranks,legacy_factors,congress=congress)
        write_json(engine/"legislative_effectiveness_v1.json",score)
        write_shadow_comparison(comparison,engine/"legislative_effectiveness_comparison_v1.json",shadow_root=output)
        board=compare_board_and_priority(engine/"legislative_effectiveness_comparison_v1.json",source_root=source,shadow_root=output)
        write_json(engine/"board_priority_comparison_v1.json",board)
        write_json(evidence/"accepted_evidence_coverage.json",acceptance)
        write_json(evidence/"population_receipt.json",population)
        eligible_ids={b["measure_id"] for b in bills if b["eligible"]}
        classes=Counter(row["class"] for row in impacts if row["measure_id"] in eligible_ids)
        sensitivity=[]
        for half_life in sorted({90.0,args.half_life,360.0}):
            if half_life==args.half_life: alternate=score
            else: alternate=compute_effectiveness(bills,roles,impacts,roster,signals,as_of=args.as_of,
                          policy=ScoringPolicy(cosponsor_half_life_days=half_life,category_weights=weights),include_explanations=False)
            sensitivity.append({"candidate_half_life_days":half_life,"adopted":False,
                                "members":[{"bioguide":m["bioguide_id"],"chamber":m["chamber"],"standard_observed_net":m["standard"]["results"]["observed_net"],"tensor_observed_net":m["tensor"]["results"]["observed_net"]} for m in alternate["members"]]})
        write_json(engine/"half_life_sensitivity.json",sensitivity)
        summary={"congress":congress,"as_of":args.as_of,"label":score["label"],"population":population,
                 "universe":manifest,"terms":score["terms"],"impact_classes":dict(classes),"acceptance":{k:v for k,v in acceptance.items() if k!="exceptions"},
                 "publication_coverage":{"source_fragment_rows":len(documents),"deduplicated_publications":len(registry),
                    "positive_linked_publications":sum(bool(r["bill_links"]) for r in registry if r["count_eligible"]),
                    "distinct_verified_bill_links":len({link["measure_id"] for r in registry if r["count_eligible"] for link in r["bill_links"]}),
                    "complete_panel_inventory":False,"SS_thresholds":"not_adopted; no calibration from incomplete member feed"},
                 "comparison":{"results_only_ranked":sum(r["results_only_rank"] is not None for r in comparison["members"].values()),
                    "composite_ranked":sum(r["advocacy_composite_rank"] is not None for r in comparison["members"].values()),
                    "tensor_results_observed":sum(r["realized_results"] is not None for r in comparison["members"].values())},
                 "limitations":["Official snapshot completeness is local, not recertified against current remote sources.",
                    "C/S automated content coding is provisional; full operative text and independent adjudication are incomplete.",
                    "Current selected CU membership/annotations do not cover all historical legislators; absence is unknown.",
                    "Member-excerpt media inventory cannot certify no coverage or complete-panel normalized frequency.",
                    "Undocumented/undated congressional letters are substantive 5 but held out of attributable advocacy and never bill milestones.",
                    "Committee/leadership historical intervals not reconciled; party/service context retained without results adjustment."]}
        write_json(target/"shadow_summary.json",summary)
        _render_review(comparison,summary,target/"review.html")
        all_summaries.append({"congress":congress,"path":str(target),"comparison":summary["comparison"],"terms":score["terms"]})
        print(json.dumps({"congress":congress,"comparison":summary["comparison"],"media":summary["publication_coverage"]}),flush=True)
        del score,comparison,bills,roles,impacts
    final=source_hashes(paths)
    if initial!=final:
        write_json(output/"input_drift.json",{"before":initial,"after":final})
        raise RuntimeError("Frozen source changed during shadow run; generation is not complete")
    if initial_code!=source_hashes(implementation_paths()):
        raise RuntimeError("Implementation changed during generation; rerun from frozen code")
    artifacts=source_hashes(p for p in output.rglob("*") if p.is_file() and p.name!="shadow_generation.json")
    receipt={"schema":"effectiveness-shadow-generation-v1","status":"complete","production_activation":False,
             "as_of":args.as_of,"arguments":vars(args),"inputs_sha256":initial,"code_sha256":initial_code,
             "outputs_sha256":artifacts,"production_hashes_unchanged":True,"terms":all_summaries}
    write_json(output/"shadow_generation.json",receipt)
    return receipt


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root",required=True)
    parser.add_argument("--archive-root",required=True)
    parser.add_argument("--draft-root",required=True)
    parser.add_argument("--output-root",required=True)
    parser.add_argument("--service-reference")
    parser.add_argument("--congress",type=int,nargs="+",default=[118,119])
    parser.add_argument("--as-of",required=True)
    parser.add_argument("--half-life",type=float,default=180,help="Explicit shadow candidate, never adopted production policy")
    parser.add_argument("--verify-reproducible",action="store_true",help="Repeat scoring on identical frozen inputs and require identical output")
    args=parser.parse_args(argv)
    run(args)


if __name__=="__main__": main()
