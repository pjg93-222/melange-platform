"""Explicit, versioned shadow consumers of the canonical effectiveness artifact.

This module is pure except for the named readers/writer.  No candidate becomes
a production policy merely because a sidecar exists.  Missing values remain
unavailable, including in ranks; a covered zero is a separate observation.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import hashlib
import html
import json
import math
import os
from pathlib import Path

SCHEMA = "legislative-effectiveness-comparison-v1"
RESULTS_MODE = "results_only_v1"
COMPOSITE_MODE = "advocacy_composite_v1"
BOARD_MODE = "realized_results_v1"
RESULTS_GATE = "distinct_signed_measures_v1"
OUTER_WEIGHTS = {"leg": .45, "press": .10, "inf": .40, "dem": .05}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def percentiles(values):
    """Legacy-compatible first-tie percentiles, calculated only over observed values."""
    known = {key: value for key, value in values.items() if _number(value)}
    ordered = sorted(known.values())
    first = {}
    for index, value in enumerate(ordered):
        first.setdefault(value, index)
    return {key: math.floor(first[value] / max(1, len(known)-1) * 1e5) / 1e5
            for key, value in known.items()}


def legislative_percentiles(raw, *, mode="legacy", provided=None):
    """The compute_pillars Leg% interface: candidates bypass pooled O ranking."""
    if mode == "legacy":
        return percentiles(raw)
    if mode not in (RESULTS_MODE, COMPOSITE_MODE):
        raise ValueError("Unknown legislative mode: " + str(mode))
    if provided is None:
        raise ValueError("Candidate mode requires explicit chamber-normalized Leg%")
    out = {}
    for name, value in provided.items():
        if value is not None and (not _number(value) or not 0 <= value <= 1):
            raise ValueError("Invalid chamber percentile for " + name)
        out[name] = value
    return out


def competition_ranks(values):
    known = {name: value for name, value in values.items() if _number(value)}
    order = sorted(known, key=lambda name: (-known[name], name))
    result = {}
    for index, name in enumerate(order):
        result[name] = result[order[index-1]] if index and known[name] == known[order[index-1]] else index+1
    return result


def _hash(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def build_shadow_comparison(effectiveness, identity_rows, legacy_ranks, legacy_factors, *, congress):
    """Build both reporting candidates and tensor results from one term.

    Historical chamber percentiles use all observed historical member records.
    The mixed-pillar comparison uses only members with frozen legacy nonleg
    inputs, explicitly a sensitivity experiment rather than a historical rank.
    """
    selected = [row for row in effectiveness["members"] if int(row["congress"]) == int(congress)]
    index = defaultdict(list)
    for row in selected:
        key = str(row.get("bioguide_id") or row.get("bioguide"))
        if any(old['chamber'] == row['chamber'] for old in index[key]):
            raise ValueError("Duplicate member/chamber record for " + key)
        index[key].append(row)
    pct = {}
    peer_counts = {}
    for chamber in sorted({str(row["chamber"]) for row in selected}):
        peers = [row for row in selected if str(row["chamber"]) == chamber]
        for axis in ("results", "advocacy"):
            values = {str(row.get("bioguide_id") or row.get("bioguide")): row["standard"][axis].get("net") for row in peers}
            pct[(chamber, axis)] = percentiles(values)
            peer_counts[chamber + ":" + axis] = {"observed": len(pct[(chamber, axis)]), "population_rows": len(peers)}
    members = {}
    trace_index = defaultdict(list)
    for trace in effectiveness.get("contributions", []):
        term = trace.get("congress") or str(trace.get("measure_id", "")).split(":")[0]
        if str(term) == str(congress):
            trace_index[str(trace.get("bioguide_id") or trace.get("bioguide"))].append(trace)
    source_factors = legacy_factors.get("members", legacy_factors)
    for identity in identity_rows:
        name = str(identity["name"])
        if name in members:
            raise ValueError("Duplicate display member: " + name)
        bioguide = str(identity.get("bioguide") or identity.get("bioguide_id") or "")
        options = index.get(bioguide, [])
        chamber_alias = lambda value: {'h':'house', 'rep':'house', 's':'senate', 'sen':'senate'}.get(str(value).lower(), str(value).lower())
        matches = [item for item in options if chamber_alias(item['chamber']) == chamber_alias(identity.get('chamber'))]
        row = matches[0] if len(matches) == 1 else options[0] if len(options) == 1 else None
        chamber = str(row["chamber"]) if row else str(identity.get("chamber") or "unknown")
        results_pct = pct.get((chamber, "results"), {}).get(bioguide)
        advocacy_pct = pct.get((chamber, "advocacy"), {}).get(bioguide)
        composite = .7*advocacy_pct + .3*results_pct if results_pct is not None and advocacy_pct is not None else None
        old = legacy_ranks.get("pcts", {}).get(name, {})
        nonleg_available = all(_number(old.get(axis)) for axis in ("press", "inf", "dem"))
        nonleg = sum(OUTER_WEIGHTS[axis]*old[axis] for axis in ("press", "inf", "dem")) if nonleg_available else None
        tensor = row.get("tensor", {}) if row else {}
        results = tensor.get("results", {})
        factors = source_factors.get(name, {})
        traces = [trace for trace in trace_index.get(bioguide, row.get("contributions", []))
                  if not trace.get('chamber') or trace.get('chamber') == row['chamber']] if row else []
        count = results.get("distinct_measures")
        if count is None and row:
            count = (row.get("coverage") or {}).get("tensor_distinct_measures")
        members[name] = {
            "bioguide": bioguide, "congress": int(congress), "chamber": chamber,
            "results_only_leg_percentile": results_pct,
            "impact_advocacy_percentile": advocacy_pct,
            "advocacy_composite_leg_percentile": composite,
            "results_only_score": .45*results_pct + nonleg if results_pct is not None and nonleg is not None else None,
            "advocacy_composite_score": .45*composite + nonleg if composite is not None and nonleg is not None else None,
            "legacy_score": legacy_ranks.get("score", {}).get(name),
            "legacy_rank": legacy_ranks.get("rank", {}).get(name),
            "legacy_leg_percentile": old.get("leg"),
            "realized_results": results.get("net"),
            "tensor_results": deepcopy(results), "advocacy_impact": deepcopy(tensor.get("advocacy", {})),
            "orientation_factor": factors.get("factor_ranked"),
            "distinct_signed_measures": count,
            "institutional_context": deepcopy(row.get("institutional_context", [])) if row else [],
            "coverage": deepcopy(row.get("coverage", {})) if row else {"member_term": "not_covered"},
            "other_chamber_records": [{key: deepcopy(value) for key,value in item.items() if key != 'contributions'} for item in options if item is not row],
            "sponsor_reference": deepcopy(row.get("sponsor_reference", {})) if row else {},
            "standard": deepcopy(row.get("standard", {})) if row else {},
            "explanation": deepcopy(traces),
            "missing_nonleg_inputs": not nonleg_available,
        }
    for label in ("results_only", "advocacy_composite"):
        ranks = competition_ranks({name: row[label+"_score"] for name, row in members.items()})
        for name, row in members.items():
            row[label+"_rank"] = ranks.get(name)
            row[label+"_rank_delta"] = row["legacy_rank"]-ranks[name] if name in ranks and _number(row["legacy_rank"]) else None
    # Compare against legacy scores on exactly the candidate's observed roster.
    for label in ("results_only", "advocacy_composite"):
        old_ranks = competition_ranks({name: row["legacy_score"] for name,row in members.items() if row[label+"_rank"] is not None})
        for name,row in members.items():
            row[label+"_legacy_common_rank"] = old_ranks.get(name)
            row[label+"_common_rank_delta"] = old_ranks[name]-row[label+"_rank"] if name in old_ranks else None
    return {
        "schema": SCHEMA, "status": "shadow_candidate", "congress": int(congress),
        "as_of": effectiveness.get("as_of"),
        "source_sha256": {"effectiveness": _hash(effectiveness), "legacy_ranks": _hash(legacy_ranks), "legacy_factors": _hash(legacy_factors)},
        "scoring_policy": deepcopy(effectiveness.get("policy", {})),
        "modes": {RESULTS_MODE: "Within-chamber signed results percentile, used directly as Leg%",
                  COMPOSITE_MODE: "70% within-chamber impact advocacy percentile + 30% results percentile; Melange adaptation with additional activity credit",
                  BOARD_MODE: "Signed tensor bill-stage results; orientation remains a separate field"},
        "outer_weights": OUTER_WEIGHTS,
        "comparison_scope": "Frozen legacy nonlegislative inputs and current display roster; historical term results do not constitute historical reporting ranks. Rank deltas on the common observed roster are separately reported.",
        "percentile_coverage": "Within-chamber percentiles include only available accepted-scope observations. Unavailable members are excluded, not scored zero; observed/population counts accompany each chamber and track.",
        "percentile_peers": peer_counts,
        "results_evidence_gate": {"id": RESULTS_GATE, "minimum_distinct_signed_measures": 1, "status": "shadow_candidate", "uncertainty_shrinkage": "none; legacy weighted-credit units are not reused"},
        "terms": deepcopy(effectiveness.get("terms", {})), "members": members,
    }


def require_shadow_path(path, shadow_root):
    if not shadow_root:
        raise ValueError("An explicit shadow root is required for candidate integrations")
    path = Path(path).resolve()
    root = Path(shadow_root).resolve()
    if path == root or root not in path.parents:
        raise ValueError("Candidate artifact must be inside the explicit shadow root")
    # Existing production filenames may live in a stage directory, but the
    # candidate root cannot be the actual workspace root.
    if root == Path(__file__).resolve().parents[2]:
        raise ValueError("Workspace root is not a shadow output directory")
    # Reject another live checkout as a purported shadow directory as well.
    # The original source workspace is read-only throughout this rollout.
    code_root = Path(__file__).resolve().parents[2]
    for ancestor in (root, *root.parents):
        if (ancestor != code_root and (ancestor / '.git').exists()
                and (ancestor / 'Moneyball Main Version - Blended.xlsx').exists()):
            raise ValueError('A live source checkout is not a shadow output directory')
    return path


def load_shadow_comparison(path, *, shadow_root):
    path = require_shadow_path(path, shadow_root)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema") != SCHEMA or document.get("status") != "shadow_candidate":
        raise ValueError("Candidate comparison schema/status mismatch")
    if not document.get("source_sha256", {}).get("effectiveness"):
        raise ValueError("Candidate comparison lacks frozen source provenance")
    return document


def write_shadow_comparison(document, path, *, shadow_root):
    target = require_shadow_path(path, shadow_root)
    if document.get("schema") != SCHEMA or document.get("status") != "shadow_candidate":
        raise ValueError("Invalid shadow comparison")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    return target


def render_explanation(member, document):
    """Escaped shared HTML consumed by both member desk and dashboard bundles."""
    esc = lambda value: html.escape(str(value), quote=True)
    fmt = lambda value: "Unavailable" if value is None else "%.5g" % value if _number(value) else esc(value)
    out = ['<details class="effectiveness-shadow"><summary>Legislative results · shadow candidate</summary>',
           '<p>Congress %s · as of %s. Institutional power is retained in observed results. Percentiles describe the observed accepted-scope cohort; missing members are excluded.</p>' % (esc(document.get("congress")), esc(document.get("as_of"))),
           '<p>Results-only Leg%%: %s; 70/30 advocacy composite: %s. Orientation: %s. Tensor net results: %s.</p>' % tuple(fmt(member.get(key)) for key in ("results_only_leg_percentile", "advocacy_composite_leg_percentile", "orientation_factor", "realized_results")),
           '<p>Beneficial advancement: %s; harmful advancement: %s. Distinct signed measures: %s.</p>' % (fmt(member.get("tensor_results", {}).get("positive")), fmt(member.get("tensor_results", {}).get("negative")), fmt(member.get("distinct_signed_measures"))),
           '<details><summary>Institutional context and coverage</summary><pre>%s</pre></details>' % esc(json.dumps({"context": member.get("institutional_context"), "coverage": member.get("coverage"), "sponsor_reference": member.get("sponsor_reference"), "chamber_terms": {key:value for key,value in document.get("terms", {}).items() if key == str(member.get("congress"))+":"+str(member.get("chamber"))}, "percentile_peers": document.get('percentile_peers')}, ensure_ascii=False, indent=2)),
           '<details><summary>Measure, role, stage and source evidence</summary><pre>%s</pre></details>' % esc(json.dumps(member.get("explanation", []), ensure_ascii=False, indent=2)),
           '<p>Impact classes: commemorative 1, substantive 5, substantive and significant 10. Letters remain substantive advocacy (weight 5); they have no bill-law milestones. Missing evidence is not zero achievement.</p></details>']
    return "".join(out)


def attach_shadow_explanations(bundle, comparison):
    """Return an annotated copy; support desk member objects and aligned dashboard arrays."""
    out = deepcopy(bundle)
    out["effectiveness_shadow"] = {key: comparison.get(key) for key in ("schema", "status", "as_of", "congress", "source_sha256", "modes", "comparison_scope")}
    if isinstance(out.get("members"), list):
        for member in out["members"]:
            data = comparison["members"].get(member["name"])
            if data is not None:
                member["effectiveness_shadow"] = deepcopy(data)
                member["effectiveness_shadow_html"] = render_explanation(data, comparison)
    elif isinstance(out.get("name"), list):
        out["effectiveness_shadow_html"] = [render_explanation(comparison["members"][name], comparison) if name in comparison["members"] else "" for name in out["name"]]
    else:
        raise ValueError("Unrecognized display bundle shape")
    return out


def optional_shadow_explanations(bundle, output_path):
    path = os.environ.get("EFFECTIVENESS_COMPARISON_PATH")
    if not path:
        return bundle
    root = os.environ.get("EFFECTIVENESS_SHADOW_ROOT")
    require_shadow_path(output_path, root)
    return attach_shadow_explanations(bundle, load_shadow_comparison(path, shadow_root=root))


def compare_board_and_priority(comparison_path, *, source_root, shadow_root, category_scores=None):
    """Read the actual board/priority consumers and retain both generations.

    Priority is only one input into the network allocation engines. Dollar
    reallocation is explicitly not inferred by rescaling priority or old funds.
    """
    import tier_util
    import moneyball_core
    comparison = load_shadow_comparison(comparison_path, shadow_root=shadow_root)
    names = list(comparison['members'])
    legacy_tier, legacy_rank, legacy_priority = moneyball_core.primacy_engine_state(
        names, mode='factor', root=str(source_root))
    new_tier, new_rank, new_priority = moneyball_core.primacy_engine_state(
        names, mode=BOARD_MODE, effectiveness_path=str(comparison_path),
        root=str(source_root), shadow_root=str(shadow_root))
    _, _, explanations = tier_util.legislative_press_tiers(
        names, root=str(source_root), mode=BOARD_MODE, effectiveness_path=str(comparison_path),
        shadow_root=str(shadow_root), with_detail=True)
    members = {}
    for name in names:
        row = {'legacy_tier':legacy_tier[name], 'legacy_board_rank':legacy_rank[name],
               'results_tier':new_tier[name], 'results_board_rank':new_rank[name],
               'legacy_priority':legacy_priority[name], 'results_priority':new_priority[name],
               'priority_delta':new_priority[name]-legacy_priority[name],
               'orientation_factor':comparison['members'][name].get('orientation_factor'),
               'results_evidence':explanations[name]}
        if category_scores is not None and name in category_scores:
            row['topic_priority'] = {
                category: {mode:{
                    'legacy':moneyball_core.blended_engine_priority(legacy_tier[name],category_scores[name],category,mode),
                    'results':moneyball_core.blended_engine_priority(new_tier[name],category_scores[name],category,mode)}
                    for mode in ('flip','coalition')} for category in moneyball_core.IMP}
        members[name] = row
    return {'schema':'effectiveness-board-priority-comparison-v1', 'status':'shadow_candidate',
            'board_mode':BOARD_MODE, 'source_sha256':comparison['source_sha256'],
            'congress':comparison['congress'], 'as_of':comparison.get('as_of'),
            'results_gate':comparison['results_evidence_gate'],
            'members':members,
            'summary':{'tier_changes':sum(row['legacy_tier'] != row['results_tier'] for row in members.values()),
                       'priority_changes':sum(abs(row['priority_delta'])>1e-12 for row in members.values()),
                       'results_unavailable_or_gate_failed':sum(not row['results_evidence']['results_gate_qualified'] for row in members.values())},
            'allocation_dollars':{'status':'not_recomputed',
                 'reason':'Requires isolated replay of frozen network, budget, engagement and receptivity inputs; priority deltas are not dollar allocation deltas.'}}


def reporting_shadow_cli(argv):
    """compute_pillars opt-in: frozen comparison -> selected candidate rank artifact."""
    import argparse
    from engine.live_legislative_pillar import from_effectiveness
    parser = argparse.ArgumentParser(description="Frozen shadow reporting; no production writes")
    parser.add_argument("--effectiveness-shadow", required=True, help="Versioned comparison JSON")
    parser.add_argument("--shadow-root", required=True)
    parser.add_argument("--mode", required=True, choices=(RESULTS_MODE, COMPOSITE_MODE))
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    comparison = load_shadow_comparison(args.effectiveness_shadow, shadow_root=args.shadow_root)
    leg = from_effectiveness(comparison, mode=args.mode)
    label = "results_only" if args.mode == RESULTS_MODE else "advocacy_composite"
    output = {"schema": "reporting-ranks-effectiveness-shadow-v1", "status": "shadow_candidate",
              "mode": args.mode, "source_sha256": comparison["source_sha256"],
              "comparison_scope": comparison["comparison_scope"],
              "legislative_pillar": leg, "ab_weights": OUTER_WEIGHTS,
              "score": {name: row[label+"_score"] for name,row in comparison["members"].items()},
              "rank": {name: row[label+"_rank"] for name,row in comparison["members"].items()},
              "pcts": {name: {"leg": value} for name,value in leg["pillar"].items()}}
    target = require_shadow_path(args.output, args.shadow_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name+".tmp")
    temporary.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    print("Wrote %s (%s; shadow candidate)" % (target, args.mode))
