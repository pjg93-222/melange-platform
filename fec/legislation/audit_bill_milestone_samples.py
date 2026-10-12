"""Reproduce source-interpreted edge-case checks against local BILLSTATUS ZIPs.

The fixed expectations were authored after reading original XML, not generated
by the mapper under test. This is developer review, not independent human
adjudication or an estimate of whole-corpus accuracy. No archive download occurs.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
from xml.etree import ElementTree as ET
import zipfile

from fec.legislation.build_bill_milestones import MAPPER_VERSION, STAGES, parse_bill_xml, sha256_file

# Dates are first direct source support, in BILL/AIC/ABC/PASS/LAW order.
# None is no qualifying supplied history; UNKNOWN denotes missing history.
CASES = [
    ("118:hr:1", "committee_bypass_house", ("2023-03-14", None, "2023-03-28", "2023-03-30", None), True, "Rules H.Res.260/floor consideration support ABC; referrals alone do not support AIC."),
    ("118:hr:5376", "referral_only", ("2023-09-08", None, None, None, None), True, "Only introduction and referrals, including a later subcommittee referral."),
    ("118:hr:906", "committee_only", ("2023-02-09", "2023-11-02", None, None, None), True, "Subcommittee markup and forwarding to full committee are AIC, not ABC."),
    ("118:hr:839", "amended_report_and_passage", ("2023-02-06", "2023-02-28", "2023-12-01", "2024-01-12", None), True, "Markup precedes amended committee report; House suspension passage is successful."),
    ("118:hr:3746", "enacted_bypass", ("2023-05-29", None, "2023-05-30", "2023-05-31", "2023-06-03"), True, "No substantive home-committee action in supplied history; law action is explicit."),
    ("118:hr:2670", "enacted_all_stages", ("2023-04-18", "2023-06-13", "2023-06-30", "2023-07-14", "2023-12-22"), True, "NDAA law vehicle has actual committee, report, passage and law histories."),
    ("118:s:2226", "pre_introduction_and_companion", ("2023-07-11", "2023-03-07", "2023-07-11", "2023-07-27", None), True, "Official subject hearings precede introduction; H.R.2670 enactment does not enact this measure."),
    ("118:hjres:30", "veto_failed_override", ("2023-02-07", None, "2023-02-27", "2023-02-28", None), True, "House passage remains observed despite later veto and failed override; LAW absent."),
    ("118:sjres:9", "senate_discharge_veto", ("2023-02-07", None, "2023-05-03", "2023-05-03", None), True, "Senate discharge does not invent AIC; veto override failed."),
    ("118:s:686", "senate_referral_only", ("2023-03-07", None, None, None, None), True, "RESTRICT Act referred without subsequent qualifying official action."),
    ("118:hjres:1", "constitutional_exclusion", (None, None, None, None, None), False, "Constitutional Supreme Court composition amendment is excluded from lawmaking scope."),
    ("118:hres:1", "simple_resolution_exclusion", (None, None, None, None, None), False, "Election of House officers may pass but has no lawmaking denominator eligibility."),
    ("119:s:5", "current_senate_bypass_law", ("2025-01-06", None, "2025-01-06", "2025-01-20", "2025-01-29"), True, "Direct Senate calendar placement bypassed committee; enacted Laken Riley Act."),
    ("119:hr:1", "current_original_measure_law", ("2025-05-20", "2025-05-20", "2025-05-20", "2025-05-22", "2025-07-04"), True, "Original House Budget Committee report counts AIC and ABC; signed law is explicit."),
    ("119:hr:2", "missing_history_reserved_number", ("2025-01-03", "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"), True, "Reserved-for-Speaker record has introducedDate but no action history; later stages unknown."),
    ("119:hr:9628", "invalid_original_role_date", ("2026-07-15", None, None, None, None), True, "Official Adams original join July9 precedes July15 introduction; quarantine role."),
]


def audit_samples(raw_root: str | Path, output_dir: str | Path, as_of: str = "2026-09-06") -> dict:
    root, target = Path(raw_root), Path(output_dir)
    results, by_stage, by_class = [], defaultdict(lambda: {"checks": 0, "agreed": 0}), defaultdict(lambda: {"checks": 0, "agreed": 0})
    archive_hashes = {}
    for identity, stratum, expected_dates, eligible, interpretation in CASES:
        congress, typ, number = identity.split(":")
        archive_path = root / f"billstatus{congress}" / f"BILLSTATUS-{congress}-{typ}.zip"
        entry = f"BILLSTATUS-{congress}{typ}{number}.xml"
        if str(archive_path) not in archive_hashes:
            archive_hashes[str(archive_path)] = sha256_file(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            record, roles = parse_bill_xml(archive.read(entry), as_of, f"{archive_path}!{entry}")
        checks = []
        for stage, expected in zip(STAGES, expected_dates):
            milestone = record["milestones"][stage]
            actual = "UNKNOWN" if milestone["state"] == "unknown" else milestone["date"]
            agreed = actual == expected
            checks.append({"stage": stage, "expected": expected, "actual": actual, "agreed": agreed})
            by_stage[stage]["checks"] += 1
            by_stage[stage]["agreed"] += agreed
            by_class[stratum]["checks"] += 1
            by_class[stratum]["agreed"] += agreed
        special_checks = []
        if identity == "119:hr:9628":
            special_checks.append({"check": "negative-delay original role quarantined", "agreed": any(r["bioguide_id"] == "A000370" and r["chronology_status"] == "quarantined" for r in roles)})
        if identity == "118:s:2226":
            special_checks.append({"check": "pre-introduction milestone retained and flagged", "agreed": "official_milestone_precedes_introduction" in record["coverage_exceptions"]})
        results.append({"measure_id": identity, "stratum": stratum, "title": record["title"], "interpretation": interpretation, "eligibility_agreed": record["eligible"] == eligible, "checks": checks, "special_checks": special_checks, "source": record["source"], "coverage_exceptions": record["coverage_exceptions"], "milestone_evidence": {s: record["milestones"][s]["evidence"] for s in STAGES}})
    receipt = {
        "status": "developer_source_review_not_independent_human_validation", "mapper_version": MAPPER_VERSION,
        "as_of": as_of, "records": len(results), "stage_checks": sum(x["checks"] for x in by_stage.values()),
        "stage_agreement": dict(by_stage), "stratum_agreement": dict(by_class),
        "all_agreed": all(r["eligibility_agreed"] and all(c["agreed"] for c in r["checks"] + r["special_checks"]) for r in results),
        "limitations": ["Purposive edge-case sample is not a random or independent validation sample.", "Agreement cannot be extrapolated to whole-corpus accuracy or used as production release certification.", "Impact-class accuracy and independent human adjudication remain separate release gates."],
        "source_archive_sha256": archive_hashes, "audit_code_sha256": sha256_file(__file__), "results": results,
    }
    target.mkdir(parents=True, exist_ok=True)
    (target / "official_milestone_sample_review.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def audit_enactment_coverage(raw_root: str | Path, output_dir: str | Path, as_of: str = "2026-09-06", congresses=(118, 119)) -> dict:
    """Census cross-check: undated laws metadata and typed actions vs mapper.

    Weighted LAW denominators must not be divided by five to infer bill counts:
    the corpus contains weight-one commemorative enactments.
    """
    root = Path(raw_root)
    receipt = {"status": "full_local_archive_crosscheck_not_live_inventory_certification", "as_of": as_of, "mapper_version": MAPPER_VERSION, "congresses": {}}
    for congress in congresses:
        counts = defaultdict(int)
        mismatches, after_term, hashes = [], [], {}
        term_end = f"{1789 + 2 * congress}-01-03"
        for archive_path in sorted((root / f"billstatus{congress}").glob("*.zip")):
            hashes[str(archive_path)] = sha256_file(archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                for entry in archive.namelist():
                    if not entry.endswith(".xml"):
                        continue
                    data = archive.read(entry)
                    bill_node = ET.fromstring(data).find("bill")
                    metadata = bool(bill_node.findall("laws/item"))
                    typed = any(a.findtext("type") == "BecameLaw" for a in bill_node.findall("actions/item"))
                    if not metadata and not typed:
                        continue
                    record, _ = parse_bill_xml(data, as_of, f"{archive_path}!{entry}")
                    counts["laws_metadata_records"] += metadata
                    counts["typed_enactment_records"] += typed
                    law = record["milestones"]["LAW"]
                    if law["reached"]:
                        counts["mapped_enactment_records"] += 1
                        counts[record["chamber"] + "_mapped_enactments"] += 1
                        if law["date"] > term_end:
                            after_term.append({"measure_id": record["measure_id"], "law_date": law["date"]})
                    elif any(a.findtext("type") == "BecameLaw" and (a.findtext("actionDate") or "") > as_of for a in bill_node.findall("actions/item")):
                        counts["future_enactment_metadata_censored"] += 1
                    else:
                        mismatches.append({"measure_id": record["measure_id"], "source": record["source"], "milestone": law})
        receipt["congresses"][str(congress)] = {"counts": dict(counts), "unmapped_enactment_records": mismatches, "term_end_date": term_end, "post_term_enactment_count": len(after_term), "post_term_enactments": sorted(after_term, key=lambda r: r["measure_id"]), "source_archive_sha256": hashes}
    receipt["all_source_enactments_accounted_for"] = all(not c["unmapped_enactment_records"] for c in receipt["congresses"].values())
    receipt["audit_code_sha256"] = sha256_file(__file__)
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "official_enactment_coverage_review.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--enactment-census", action="store_true", help="Also cross-check every local law-metadata or BecameLaw record")
    args = parser.parse_args()
    result = audit_samples(args.raw_root, args.output_dir)
    print(json.dumps({k: result[k] for k in ("status", "records", "stage_checks", "stage_agreement", "all_agreed")}, indent=2))
    if not result["all_agreed"]:
        raise SystemExit(1)
    if args.enactment_census:
        census = audit_enactment_coverage(args.raw_root, args.output_dir)
        print(json.dumps({"enactment_census": {c: r["counts"] for c, r in census["congresses"].items()}, "all_source_enactments_accounted_for": census["all_source_enactments_accounted_for"]}, indent=2))
        if not census["all_source_enactments_accounted_for"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
