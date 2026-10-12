# -*- coding: utf-8 -*-
"""Build an isolated greenlit-legislation + primacy display-tier shadow.

The treatment ``ranks_python.json`` remains the rank authority.  This builder
adds newly Greenlit, current-roster scoring amendments to a copy of the member
desk act universe, classifies them with the ratified primacy authority ladder,
recomputes primacy factors, and applies the conduct/delivery ladder to the
display tier only.

It never writes the production workbook, production ranks, production member
bundle, or production primacy artifacts.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import copy
import csv
import hashlib
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

from paths import ROOT


PACKAGE_DEFAULT = Path(ROOT) / "audits" / "primacy_shadow_20260728" / "ship"
STAGE_DEFAULT = (
    Path(ROOT) / "audits" / "legfix5" / "staged_integration" / "greenlight-20260728a"
)
SHADOW_DEFAULT = (
    Path(ROOT)
    / "audits"
    / "legfix5"
    / "shadow_recompute"
    / "greenlight-20260728a"
    / "cfpb-route-corrected-v5"
)
PARTITION_DEFAULT = (
    Path(ROOT)
    / "audits"
    / "legfix5"
    / "opus_adjudication"
    / "amendment_culling_opus5_20260728a"
    / "amendment_partition_3703.ndjson"
)
CONTENT_DEFAULT = (
    Path(ROOT)
    / "audits"
    / "legfix5"
    / "opus_adjudication"
    / "original_method_evidence"
    / "amendment_content_v2.ndjson"
)
OUTPUT_DEFAULT = (
    Path(ROOT)
    / "audits"
    / "legfix5"
    / "primacy_merged"
    / "greenlight-20260728a-v1"
)


# Same hierarchy as PRIMACY_IMPLEMENTATION_SPEC_2026-07-28.md:
# CU operative text wins first; otherwise an in-universe amendment inherits its
# parent's beneficiary class; content-family rules follow.  Stance/direction is
# deliberately absent because the treatment Leg% already carries it.
NEW_SCORING_CLASSIFICATIONS = {
    "116:hamdt:247": (
        "shared",
        "L6:cross-industry-family",
        "HMDA reporting requirements apply across depository institutions; treatment direction "
        "separately records the adverse CU effect.",
    ),
    "116:hamdt:253": (
        "shared",
        "L6:cross-industry-family",
        "CFPB regular-appropriations reform is cross-industry.",
    ),
    "116:samdt:1049": (
        "shared",
        "L6:cross-industry-family",
        "Federal elder-fraud prevention office is cross-industry.",
    ),
    "116:samdt:2713": (
        "shared",
        "L6:cross-industry-family",
        "Fraud and Scam Reduction Act is cross-industry.",
    ),
    "117:hamdt:97": (
        "shared",
        "L6:parent-inheritance",
        "SAFE Banking amendment inherits shared treatment from in-universe H.R.4350.",
    ),
    "117:samdt:4704": (
        "shared",
        "L6:parent-inheritance",
        "CFPB appropriations amendment inherits shared treatment from in-universe H.R.4350.",
    ),
    "117:samdt:4811": (
        "shared",
        "L6:parent-inheritance",
        "Financial-account reporting amendment inherits shared treatment from H.R.4350.",
    ),
    "118:hamdt:696": (
        "shared",
        "L6:content-cbdc",
        "CBDC restriction follows the existing content-cbdc rule: shared, not CU-primary.",
    ),
    "118:samdt:3048": (
        "shared",
        "L6:cross-industry-family",
        "HELPER Act mortgage eligibility is cross-industry.",
    ),
    "119:samdt:2247": (
        "crypto_primary",
        "L6:parent-inheritance",
        "Amendment to S.1582 inherits the GENIUS Act parent's crypto-primary class.",
    ),
    "119:samdt:2280": (
        "crypto_primary",
        "L6:parent-inheritance",
        "Amendment to S.1582 inherits the GENIUS Act parent's crypto-primary class.",
    ),
    "119:samdt:3673": (
        "shared",
        "L6:cross-industry-family",
        "Elder-financial-fraud prevention is cross-industry.",
    ),
    "119:samdt:4167": (
        "shared",
        "L6:cross-industry-family",
        "Romance-scam prevention is cross-industry.",
    ),
    "119:samdt:4320": (
        "shared",
        "L6:parent-inheritance",
        "HELPER Act amendment inherits shared treatment from H.R.6644.",
    ),
    "119:samdt:4348": (
        "shared",
        "L6:parent-inheritance",
        "Crypto-ATM fraud amendment inherits shared treatment from H.R.6644 under the "
        "ratified amendment-parent rule.",
    ),
    "119:samdt:4736": (
        "crypto_primary",
        "L6:content-crypto",
        "Crypto-ATM operator registration and virtual-currency fraud relief are crypto-primary.",
    ),
    "119:samdt:6241": (
        "shared",
        "L6:cross-industry-family",
        "BSA currency and suspicious-activity reporting thresholds are cross-industry.",
    ),
}


def read_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def read_ndjson(path: Path):
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=1, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)


def write_ndjson(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import %s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def key_to_display(canonical_key: str):
    congress, measure_type, number = canonical_key.split(":")
    prefix = {
        "hamdt": "H.Amdt.",
        "samdt": "S.Amdt.",
        "hr": "H.R.",
        "s": "S.",
        "hjres": "H.J.Res.",
        "sjres": "S.J.Res.",
    }.get(measure_type)
    if prefix is None:
        raise ValueError("unsupported canonical key %s" % canonical_key)
    return prefix + number, congress


def bundle_key(bill: dict):
    return str(bill.get("bill") or "").strip(), str(bill.get("congress") or "").strip()


def canonical_amendment_key(bill: dict):
    display, congress = bundle_key(bill)
    match = re.fullmatch(r"([HS])\.Amdt\.(\d+)", display)
    if not match:
        return None
    return "%s:%s:%s" % (
        congress,
        "hamdt" if match.group(1) == "H" else "samdt",
        match.group(2),
    )


def clean_summary(text: str, limit: int = 220) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def congress_url(canonical_key: str) -> str:
    congress, measure_type, number = canonical_key.split(":")
    chamber = "house" if measure_type == "hamdt" else "senate"
    return "https://www.congress.gov/amendment/%sth-congress/%s-amendment/%s" % (
        congress,
        chamber,
        number,
    )


def tier_for_slot(slot: int) -> int:
    if slot <= 12:
        return 1
    if slot <= 36:
        return 2
    if slot <= 84:
        return 3
    return 0


def build(args) -> dict:
    package = Path(args.package).resolve()
    stage = Path(args.stage).resolve()
    shadow = Path(args.shadow).resolve()
    partition_path = Path(args.partition).resolve()
    content_path = Path(args.content).resolve()
    output = Path(args.output).resolve()
    if output.exists() and any(output.iterdir()) and not args.overwrite:
        raise RuntimeError("output exists and is not empty: %s" % output)
    output.mkdir(parents=True, exist_ok=True)

    production_rank = Path(ROOT) / "ranks_python.json"
    production_workbook = Path(ROOT) / "Moneyball Main Version - Blended.xlsx"
    protected_before = {
        str(production_rank): sha256(production_rank),
        str(production_workbook): sha256(production_workbook),
    }

    bundle_path = Path(ROOT) / "member_desk_bundle.json"
    treatment_rank_path = shadow / "treatment" / "ranks_python.json"
    edge_path = stage / "integrated_membership_edges.ndjson"
    decision_path = stage / "integrated_decisions.ndjson"
    event_path = shadow / "shadow_event_ledger.ndjson"
    existing_tag_path = package / "bill_beneficiary_tags.ndjson"
    grievance_path = package / "grievance_ledger.ndjson"
    delivery_path = package / "delivery_ledger.ndjson"

    bundle = read_json(bundle_path)
    merged_bundle = copy.deepcopy(bundle)
    treatment_ranks = read_json(treatment_rank_path)
    edges = read_ndjson(edge_path)
    decisions = read_ndjson(decision_path)
    events = read_ndjson(event_path)
    partition = {row["canonical_key"]: row for row in read_ndjson(partition_path)}
    content = {row["canonical_key"]: row for row in read_ndjson(content_path)}
    existing_tags = read_ndjson(existing_tag_path)
    tag_by_key = {(row["bill"], str(row["congress"])): row for row in existing_tags}

    scoring_keys = {
        row["canonical_key"]
        for row in edges
        if row["integration_state"] == "included_scoring_ready"
    }
    membership_only_keys = {
        row["canonical_key"]
        for row in edges
        if row["integration_state"] == "included_membership_only_non_scoring"
    }
    existing_bundle_amendments = {
        key
        for member in bundle["members"]
        for bill in member.get("bills") or []
        for key in [canonical_amendment_key(bill)]
        if key
    }
    new_scoring_keys = scoring_keys - existing_bundle_amendments
    if new_scoring_keys != set(NEW_SCORING_CLASSIFICATIONS):
        raise RuntimeError(
            "new scoring candidate set drifted; expected %d, got %d; missing=%s extra=%s"
            % (
                len(NEW_SCORING_CLASSIFICATIONS),
                len(new_scoring_keys),
                sorted(set(NEW_SCORING_CLASSIFICATIONS) - new_scoring_keys),
                sorted(new_scoring_keys - set(NEW_SCORING_CLASSIFICATIONS)),
            )
        )

    event_by_key = {}
    areas_by_key = {}
    for event in events:
        key = event["canonical_key"]
        event_by_key.setdefault(key, event)
        areas_by_key.setdefault(key, set()).add(str(event.get("category") or "").strip())
    decision_by_key = {}
    for decision in decisions:
        decision_by_key.setdefault(decision.get("canonical_key"), decision)

    member_by_name = {row["name"]: row for row in merged_bundle["members"]}
    added_to_bundle = []
    historical_only = []
    extended_tags = list(existing_tags)
    reconciliation = []

    for key in sorted(scoring_keys):
        display, congress = key_to_display(key)
        classification = tag_by_key.get((display, congress))
        event = event_by_key.get(key, {})
        state = "covered_existing_primacy_tag"
        if key in new_scoring_keys:
            cls, authority, rationale = NEW_SCORING_CLASSIFICATIONS[key]
            source = partition[key]
            text = content[key]
            if not source.get("text_available") or not text.get("amendment_text"):
                raise RuntimeError("accepted operative text missing for %s" % key)
            if event.get("roster_status") == "mapped_current_roster":
                member_name = event.get("member")
                if member_name not in member_by_name:
                    raise RuntimeError("current-roster member not in bundle: %s" % member_name)
                parent = re.sub(
                    r"^Amends (?:Bill|Treaty):\s*", "", str(text.get("amends") or "")
                ).strip()
                bill_row = {
                    "role": "sponsor",
                    "bill": display,
                    "title": "Amendment to %s" % (parent or "parent measure"),
                    "sponsor": member_name,
                    "ncos": 0,
                    "congress": congress,
                    "action": str(text.get("latest_action") or ""),
                    "summary": clean_summary(text.get("amendment_text")),
                    "url": congress_url(key),
                    "areas": sorted(area for area in areas_by_key.get(key, set()) if area),
                }
                if any(bundle_key(row) == (display, congress) for row in member_by_name[member_name]["bills"]):
                    raise RuntimeError("duplicate member-act insertion %s %s" % (member_name, key))
                member_by_name[member_name]["bills"].append(bill_row)
                classification = {
                    "bill": display,
                    "congress": congress,
                    "title": bill_row["title"],
                    "class": cls,
                    "act_weight": {
                        "shared": 0.5,
                        "crypto_primary": 0.15,
                    }[cls],
                    "authority": authority,
                    "rationale": rationale,
                    "acu_position": "",
                    "confidence": 0.9,
                    "areas": bill_row["areas"],
                    "roster_role_weight": 1.0,
                    "member_links": 1,
                    "tagged": "2026-07-29",
                    "source_candidate": key,
                }
                extended_tags.append(classification)
                tag_by_key[(display, congress)] = classification
                added_to_bundle.append(key)
                state = "classified_and_added_current_roster"
            else:
                historical_only.append(key)
                classification = {
                    "bill": display,
                    "congress": congress,
                    "class": cls,
                    "authority": authority,
                    "rationale": rationale,
                }
                state = "classified_historical_off_current_roster"
        reconciliation.append(
            {
                "canonical_key": key,
                "integration_state": "included_scoring_ready",
                "primacy_state": state,
                "member": event.get("member"),
                "roster_status": event.get("roster_status"),
                "class": classification.get("class") if classification else None,
                "authority": classification.get("authority") if classification else None,
                "direction": event.get("direction"),
                "direction_consumed_by": "treatment_leg_percentile",
                "beneficiary_consumed_by": "primacy_factor",
                "reason": (decision_by_key.get(key) or {}).get("reason"),
            }
        )

    for key in sorted(membership_only_keys):
        reconciliation.append(
            {
                "canonical_key": key,
                "integration_state": "included_membership_only_non_scoring",
                "primacy_state": "held_visible_non_scoring",
                "member": None,
                "roster_status": "not_admitted_to_scoring_act_universe",
                "class": None,
                "authority": None,
                "direction": None,
                "direction_consumed_by": None,
                "beneficiary_consumed_by": None,
                "reason": (decision_by_key.get(key) or {}).get("reason"),
            }
        )

    merged_bundle_path = output / "member_desk_bundle.primacy_merged.json"
    merged_tags_path = output / "bill_beneficiary_tags.primacy_merged.ndjson"
    write_json(merged_bundle_path, merged_bundle)
    write_ndjson(merged_tags_path, extended_tags)
    write_ndjson(output / "candidate_reconciliation.ndjson", reconciliation)

    PF = load_module(package / "primacy_factors.py", "merged_primacy_factors")
    TU = load_module(package / "tier_util_primacy.patch.py", "merged_tier_util")
    tags = PF.load_tags(str(merged_tags_path))
    cu_cells = {}
    if os.environ.get("PRIMACY_CU_TENSOR", "1") != "0":
        cu_cells = PF.load_cu_tensor_cells(Path(ROOT) / PF.CU_TENSOR)
    members, baseline = PF.compute(
        merged_bundle, tags, cu_cells=cu_cells
    )
    factor_doc = {
        "_doc": "Merged greenlit-legislation treatment + primacy display-tier factors",
        "_constants": {
            "role_weights": PF.ROLE_W,
            "act_weights": PF.ACT_W,
            "letter_weight": PF.LETTER_W,
            "shrink_anchor": PF.SHRINK_ANCHOR,
            "roster_baseline": round(baseline, 6),
        },
        "_merge": {
            "treatment_rank_sha256": sha256(treatment_rank_path),
            "base_act_universe": len(existing_tags),
            "merged_act_universe": len(tags),
            "added_current_roster_scoring_amendments": len(added_to_bundle),
        },
        "members": {
            name: {
                key: (round(value, 6) if isinstance(value, float) else value)
                for key, value in record.items()
            }
            for name, record in sorted(members.items())
        },
    }
    factor_path = output / "primacy_factors.primacy_merged.json"
    write_json(factor_path, factor_doc)

    names = [row["name"] for row in merged_bundle["members"]]
    pcts = treatment_ranks["pcts"]
    factors = {name: row["factor_shrunk"] for name, row in members.items()}
    old_factor_doc = read_json(package / "data" / "engine" / "primacy_factors.json")
    old_factors = {
        name: float(row["factor_shrunk"])
        for name, row in old_factor_doc["members"].items()
    }
    breaches = TU.load_open_breaches(str(package))
    delivered = TU.load_delivery_credit(str(package))

    def display_order(factor_values):
        # D-031: pinned to the 2026-07-29 factor board this merge was built against;
        # the live default ('per_area_v1') needs a grade map this script does not load.
        scores = TU.primacy_scores(pcts, factor_values, mode='factor')
        raw = sorted(
            [name for name in names if name in scores],
            key=lambda name: (-scores[name], name),
        )
        final, detail = TU.apply_conduct_ladder(raw, breaches, delivered)
        return scores, raw, final, detail

    scores, raw, final, detail = display_order(factors)
    old_scores, old_raw, old_final, _ = display_order(old_factors)
    if set(final) != set(names) or len(final) != len(names):
        raise RuntimeError("merged conduct ladder did not preserve the roster")

    old_slot = {name: i + 1 for i, name in enumerate(old_final)}
    new_slot = {name: i + 1 for i, name in enumerate(final)}
    rank_map = treatment_ranks["rank"]
    rows = []
    for slot, name in enumerate(final, start=1):
        factor = members[name]
        conduct = detail.get(name, {})
        rows.append(
            {
                "slot": slot,
                "member": name,
                "tier": tier_for_slot(slot),
                "primacy_score": round(scores[name], 9),
                "leg_pct_treatment": round(float(pcts[name]["leg"]), 9),
                "factor_raw": round(float(factor["factor_raw"]), 9),
                "factor_shrunk": round(float(factor["factor_shrunk"]), 9),
                "agenda_credit": factor["agenda_credit"],
                "treatment_overall_rank": int(rank_map[name]),
                "overall_rank_after_primacy": int(rank_map[name]),
                "overall_rank_delta": 0,
                "prior_primacy_treatment_slot": old_slot[name],
                "primacy_merge_slot_delta": slot - old_slot[name],
                "gross_severity": conduct.get("gross_severity"),
                "delivered_credit": conduct.get("delivered_credit"),
                "net_severity": conduct.get("net_severity"),
                "tier_ceiling": conduct.get("tier_ceiling"),
            }
        )

    board_csv = output / "merged_primacy_board.csv"
    with board_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    moved = [row for row in rows if row["primacy_merge_slot_delta"]]
    tier_changes = [
        row
        for row in rows
        if tier_for_slot(row["prior_primacy_treatment_slot"]) != row["tier"]
    ]
    delta_doc = {
        "schema": "greenlit-legislation-primacy-merged-delta-v1",
        "delta_convention": "merged_primacy_slot - prior_primacy_treatment_slot",
        "summary": {
            "members": len(rows),
            "overall_rank_changed": 0,
            "overall_rank_unchanged": len(rows),
            "display_slots_changed": len(moved),
            "display_slots_unchanged": len(rows) - len(moved),
            "max_abs_display_delta": max(
                abs(row["primacy_merge_slot_delta"]) for row in rows
            ),
            "tier_changed": len(tier_changes),
            "tier1_membership_changed": set(final[:12]) != set(old_final[:12]),
        },
        "tier1": final[:12],
        "largest_display_changes": sorted(
            moved,
            key=lambda row: abs(row["primacy_merge_slot_delta"]),
            reverse=True,
        )[:25],
        "tier_changes": tier_changes,
        "members": rows,
    }
    delta_path = output / "merged_rank_tier_delta.json"
    write_json(delta_path, delta_doc)

    protected_after = {
        str(production_rank): sha256(production_rank),
        str(production_workbook): sha256(production_workbook),
    }
    merged_bundle_keys = {
        "%s|%s" % bundle_key(bill)
        for member in merged_bundle["members"]
        for bill in member.get("bills") or []
    }
    merged_tag_keys = set(tags)
    gates = {
        "all_60_scoring_ready_candidates_reconciled": len(
            [
                row
                for row in reconciliation
                if row["integration_state"] == "included_scoring_ready"
            ]
        )
        == 60,
        "all_13_membership_only_candidates_held_visible_non_scoring": len(
            [
                row
                for row in reconciliation
                if row["primacy_state"] == "held_visible_non_scoring"
            ]
        )
        == 13,
        "17_new_scoring_candidates_classified": len(new_scoring_keys) == 17,
        "13_current_roster_amendments_added": len(added_to_bundle) == 13,
        "4_historical_candidates_excluded_from_current_roster_factor": len(
            historical_only
        )
        == 4,
        "merged_bundle_tag_universe_closed": merged_bundle_keys == merged_tag_keys,
        "merged_roster_closed_533": len(members) == len(names) == 533,
        "overall_rank_delta_exactly_zero": all(
            row["overall_rank_delta"] == 0 for row in rows
        ),
        "conduct_ladder_permutation": len(final) == len(set(final)) == len(names),
        "production_hashes_unchanged": protected_before == protected_after,
    }
    status = (
        "PASS_STAGED_MERGED_PRIMACY_TREATMENT_SHADOW"
        if all(gates.values())
        else "FAIL_STAGED_MERGED_PRIMACY_TREATMENT_SHADOW"
    )
    receipt = {
        "schema": "greenlit-legislation-primacy-merged-receipt-v1",
        "status": status,
        "method": {
            "rank_authority": str(treatment_rank_path),
            "direction_layer": "treatment Leg percentile",
            "beneficiary_layer": "primacy factor",
            "display_formula": "treatment Leg% * merged factor_shrunk",
            "conduct_formula": "gross grievance severity - delivered credit",
            "rank_contract": "primacy may not write or change overall rank",
        },
        "counts": {
            "base_act_universe": len(existing_tags),
            "merged_act_universe": len(tags),
            "scoring_ready_candidates": len(scoring_keys),
            "scoring_ready_already_in_bundle": len(
                scoring_keys - new_scoring_keys
            ),
            "new_scoring_candidates": len(new_scoring_keys),
            "new_current_roster_scoring_amendments": len(added_to_bundle),
            "new_historical_scoring_amendments": len(historical_only),
            "membership_only_visible_non_scoring": len(membership_only_keys),
            "members": len(names),
            "display_slots_changed_by_universe_merge": len(moved),
            "tier_changes_by_universe_merge": len(tier_changes),
        },
        "gates": gates,
        "tier1": final[:12],
        "inputs": {
            str(path): sha256(path)
            for path in (
                bundle_path,
                treatment_rank_path,
                edge_path,
                decision_path,
                event_path,
                existing_tag_path,
                partition_path,
                content_path,
                grievance_path,
                delivery_path,
            )
        },
        "outputs": {
            path.name: {"sha256": sha256(path), "bytes": path.stat().st_size}
            for path in (
                merged_bundle_path,
                merged_tags_path,
                output / "candidate_reconciliation.ndjson",
                factor_path,
                board_csv,
                delta_path,
            )
        },
        "safety": {
            "production_workbook_written": False,
            "production_rank_written": False,
            "production_member_bundle_written": False,
            "production_primacy_written": False,
            "external_sync_performed": False,
        },
        "protected_hashes_before": protected_before,
        "protected_hashes_after": protected_after,
    }
    receipt_path = output / "merged_primacy_receipt.json"
    write_json(receipt_path, receipt)
    if status.startswith("FAIL"):
        raise RuntimeError(
            "merge gates failed: %s"
            % sorted(name for name, ok in gates.items() if not ok)
        )
    print(
        "%s | acts %d->%d | current additions %d | rank delta 0 | tier changes %d"
        % (
            status,
            len(existing_tags),
            len(tags),
            len(added_to_bundle),
            len(tier_changes),
        )
    )
    return receipt


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", default=str(PACKAGE_DEFAULT))
    parser.add_argument("--stage", default=str(STAGE_DEFAULT))
    parser.add_argument("--shadow", default=str(SHADOW_DEFAULT))
    parser.add_argument("--partition", default=str(PARTITION_DEFAULT))
    parser.add_argument("--content", default=str(CONTENT_DEFAULT))
    parser.add_argument("--output", default=str(OUTPUT_DEFAULT))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        build(args)
    except Exception as exc:
        print("FATAL:", exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
