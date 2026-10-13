"""Frozen local BILLSTATUS -> canonical full-history evidence, never network I/O.

This is a whole-chamber effectiveness universe reader, deliberately independent
of the CU discovery/enrichment pipeline.  ``iter_archive_bills`` yields
``(bill, roles)`` pairs.  Date-only joining is inclusive and withdrawal exclusive;
the scoring policy, not this evidence reader, chooses a timing half-life.

Milestone ``state=observed, reached=False`` means that the supplied history has
no qualifying action, not that future advancement is impossible.  Missing
histories use ``state=unknown, reached=None``.  No preceding stage is inferred
from a later one: discharge and direct calendar/floor paths need not have AIC.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date
import hashlib
import html
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable, Iterator
from xml.etree import ElementTree as ET
import zipfile

MAPPER_VERSION = "federal-full-history-v1.0.0-shadow"
STAGES = ("BILL", "AIC", "ABC", "PASS", "LAW")
TOP_LEVEL_TYPES = ("hr", "s", "hjres", "sjres", "hres", "sres", "hconres", "sconres")
LAW_TYPES = frozenset(("hr", "s", "hjres", "sjres"))
DATE_CONVENTION = "date-only: joined_date <= stage_date < withdrawn_date; same-day withdrawal earns no stage"


def _text(node: ET.Element, path: str, default: str = "") -> str:
    return (node.findtext(path) or default).strip()


def _date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return None


def _plain(value: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html.unescape(value)).split())


def _within(value: str | None, as_of: str | None) -> bool:
    return bool(value and (not as_of or value <= as_of))


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _action_chamber(action: dict[str, Any]) -> str | None:
    source = action["source_system"]
    name = source.get("name", "").lower()
    code = action["code"]
    # Library of Congress event codes are chamber-specific, not ordinal stages.
    loc_house = {"1000", "1010", "5000", "5500", "8000", "9000", "21000"}
    loc_senate = {"10000", "14000", "14500", "14900", "17000", "18000", "23000"}
    if "house" in name or code.startswith("H") or code == "Intro-H" or code in loc_house:
        return "House"
    if "senate" in name or code.startswith("S") or code in loc_senate:
        return "Senate"
    committees = action.get("committees", [])
    chambers = {"House" if x.startswith("h") else "Senate" for x in committees if x.startswith(("h", "s"))}
    if len(chambers) == 1:
        return chambers.pop()
    text = action["text"].lower()
    if re.search(r"(?:in|by|on the|to the) (?:the )?senate\b|^senate\b", text):
        return "Senate"
    if re.search(r"(?:in|by|on the|to the) (?:the )?house\b|^house\b", text):
        return "House"
    return None


def _evidence(node: ET.Element) -> dict[str, Any]:
    row = {
        "date": _date(_text(node, "actionDate")),
        "time": _text(node, "actionTime") or None,
        "code": _text(node, "actionCode"),
        "type": _text(node, "type"),
        "text": _plain(_text(node, "text")),
        "source_system": {"code": _text(node, "sourceSystem/code"), "name": _text(node, "sourceSystem/name")},
        "committees": sorted({_text(c, "systemCode") for c in node.findall("committees/item") if _text(c, "systemCode")}),
    }
    row["chamber"] = _action_chamber(row)
    return row


def classify_action(action: dict[str, Any], chamber: str) -> list[str]:
    """Conservative source/code/type/text map, restricted to the home chamber.

    Actual reporting is AIC and ABC, whereas an order to report is only AIC.
    Committee of the Whole is floor action.  A rule passing for another measure,
    failed suspension, cloture, and motions to proceed are never bill passage.
    """
    text, code, kind = action["text"].lower(), action["code"], action["type"].lower()
    if re.search(r"\bbecame (?:public|private) law\b|\benacted over (?:the )?president", text) or (kind == "becamelaw" and code == "36000" and "signed by president" in text):
        return ["LAW"]
    if action.get("chamber") != chamber:
        return []
    result = []
    referral = re.match(r"(?:read (?:once|twice) and )?(?:referred|rereferred|re-referred)\b", text)
    discharged = bool(re.search(r"\b(?:committee\b.*\bdischarged|discharged from (?:the )?committee)", text))
    reported = bool(re.search(r"\breported (?:adversely )?(?:\([^)]*\) )?(?:by|in|to|an original|without|with)\b|\bfiled (?:a )?written report\b|(?:supplemental|committee) report filed", text))
    reported = reported and not bool(re.search(r"\border(?:ed|ing) (?:to be )?reported\b|rules committee resolution", text))
    substantive_committee = bool(re.search(r"\bhearings? held\b|\b(?:mark[- ]?up|consideration and mark[- ]?up) (?:held|session held)\b|\bordered (?:to be )?reported\b|\bcommittee (?:consideration|mark[- ]?up)\b|\bcommittee\b.*\bbusiness meeting\b|\bforwarded by subcommittee to full committee\b|\bdefeated measure\b|\bmotion to report measure defeated\b", text))
    if not referral and not discharged and (substantive_committee or (reported and kind == "committee")):
        result.append("AIC")
    # Merely filing a motion to place on a calendar is not calendar placement.
    calendar = bool(re.search(r"\bplaced on (?:the )?(?:senate legislative|union|house|private|consensus|calendar)\b", text))
    floor = kind in ("floor", "resolvingdifferences", "discharge")
    if reported or discharged or calendar or floor:
        result.append("ABC")
    passage_code = (chamber == "House" and code in ("8000", "H37100")) or (chamber == "Senate" and code == "17000")
    passage_text = bool(re.search(r"^passed(?:/agreed to)? in (?:house|senate)\b|^on passage\s+passed\b|^passed (?:house|senate)\b|^on motion to suspend the rules and pass\b.*\bagreed to\b|^(?:joint )?resolution agreed to in (?:house|senate)\b", text))
    failed = bool(re.search(r"failed|not agreed|not passed|motion to (?:reconsider|proceed)|cloture", text))
    if (passage_code or passage_text) and not failed:
        if "ABC" not in result:
            result.append("ABC")
        result.append("PASS")
    return result


def _summaries(bill: ET.Element, as_of: str | None) -> tuple[list[dict[str, Any]], int]:
    rows, censored = [], 0
    for item in list(bill.findall("summaries/summary")) + list(bill.findall("summaries/item")):
        action_date = _date(_text(item, "actionDate"))
        updated = _date(_text(item, "updateDate"))
        if as_of and ((action_date and action_date > as_of) or (updated and updated > as_of)):
            censored += 1
            continue
        content = _plain(_text(item, "cdata/text") or _text(item, "text"))
        if content:
            rows.append({"date": action_date, "updated_date": updated, "version_code": _text(item, "versionCode"), "text": content})
    return sorted(rows, key=lambda x: (x["date"] or "", x["version_code"])), censored


def _roles(bill: ET.Element, identity: str, introduced: str | None, as_of: str | None) -> list[dict[str, Any]]:
    rows, seen, sponsors = [], set(), set()
    for container, default_role in (("sponsors", "sponsor"), ("cosponsors", "cosponsor")):
        for item in bill.findall(container + "/item"):
            bioguide = _text(item, "bioguideId")
            joined = introduced if default_role == "sponsor" else _date(_text(item, "sponsorshipDate"))
            original_value = _text(item, "isOriginalCosponsor").lower()
            role = "original" if default_role == "cosponsor" and original_value in ("true", "y", "1") else default_role
            withdrawn_raw = _date(_text(item, "sponsorshipWithdrawnDate") or _text(item, "withdrawnDate"))
            withdrawn = withdrawn_raw if _within(withdrawn_raw, as_of) else None
            if joined and as_of and joined > as_of:
                continue
            key = (bioguide, role, joined, withdrawn)
            if key in seen:
                continue
            seen.add(key)
            problems = []
            if not bioguide:
                problems.append("missing_bioguide_id")
            if not introduced or not joined:
                problems.append("missing_join_or_introduction_date")
            delay = (date.fromisoformat(joined) - date.fromisoformat(introduced)).days if joined and introduced else None
            if delay is not None and delay < 0:
                problems.append("joining_precedes_introduction")
            if role == "original" and delay != 0:
                problems.append("original_join_date_differs_from_introduction")
            if default_role == "cosponsor" and original_value not in ("true", "false", "y", "n", "1", "0"):
                problems.append("unknown_original_status")
            if withdrawn and joined and withdrawn < joined:
                problems.append("withdrawal_precedes_joining")
            if default_role == "cosponsor" and bioguide in sponsors:
                problems.append("sponsor_also_listed_as_cosponsor")
            if role == "sponsor":
                sponsors.add(bioguide)
            evidence = {"source_system": "GovInfo BILLSTATUS", "container": container, "sponsorship_date": joined, "original_flag": original_value or None, "withdrawn_date": withdrawn}
            rows.append({
                "measure_id": identity, "bioguide_id": bioguide, "role": role,
                "joined_date": joined, "withdrawn_date": withdrawn, "introduced_date": introduced,
                "days_from_introduction": delay, "display_name": _text(item, "fullName"),
                "party": _text(item, "party"), "state": _text(item, "state"), "district": _text(item, "district"),
                "chronology_status": "verified" if not problems else "quarantined",
                "chronology_exceptions": problems, "timing_multiplier": None,
                "timing_policy": "deferred_to_versioned_scorer", "date_convention": DATE_CONVENTION,
                "membership_service_status": "not_reconciled_to_dated_member_roster", "evidence": [evidence],
            })
    # Preserve episodes for rejoining, but flag overlapping conflicting episodes.
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[row["bioguide_id"]].append(row)
    for group in groups.values():
        episodes = sorted(group, key=lambda r: r["joined_date"] or "")
        for previous, current in zip(episodes, episodes[1:]):
            if previous["role"] == "sponsor" or not previous["withdrawn_date"] or not current["joined_date"] or previous["withdrawn_date"] > current["joined_date"]:
                current["chronology_status"] = "quarantined"
                current["chronology_exceptions"].append("overlapping_or_conflicting_role_episodes")
    return sorted(rows, key=lambda r: (r["bioguide_id"], r["joined_date"] or "", r["role"]))


def _candidate_features(bill: ET.Element, roles: list[dict[str, Any]], as_of: str | None) -> dict[str, Any]:
    cosponsors = [r for r in roles if r["role"] != "sponsor" and r["chronology_status"] == "verified"]
    amendments: dict[str, dict[str, Any]] = {}
    for item in list(bill.findall("amendments/amendment")) + list(bill.findall("amendments/item")):
        congress, typ, number = _text(item, "congress"), _text(item, "type").lower(), _text(item, "number")
        if not all((congress, typ, number)):
            continue
        submitted = _date(_text(item, "submittedDate"))
        proposed = _date(_text(item, "proposedDate"))
        actions = [_evidence(a) for a in list(item.findall("actions/actions/item")) + list(item.findall("actions/item"))]
        actions = [a for a in actions if _within(a["date"], as_of)]
        if as_of and not _within(submitted, as_of) and not _within(proposed, as_of) and not actions:
            continue
        status = set()
        if _within(submitted, as_of):
            status.add("submitted")
        if _within(proposed, as_of):
            status.add("proposed")
        for action in actions:
            text = action["text"].lower()
            if re.search(r"\b(?:proposed|offered)\b", text):
                status.add("proposed")
            if re.search(r"\bamendment\b.*\bagreed to\b", text) and not re.search(r"not agreed|failed|motion to", text):
                status.add("agreed")
            if "failed" in text or "not agreed to" in text:
                status.add("failed")
            if "withdrawn" in text:
                status.add("withdrawn")
        identity = f"{congress}:{typ}:{int(number)}"
        amendments[identity] = {"amendment_id": identity, "submitted_date": submitted if _within(submitted, as_of) else None, "proposed_date": proposed if _within(proposed, as_of) else None, "observed_statuses": sorted(status), "action_history_present": bool(actions)}
    return {
        "use": "corroborating_candidate_only_no_automatic_significance_multiplier",
        "unique_cosponsor_count": len({r["bioguide_id"] for r in cosponsors}),
        "original_cosponsor_count": len({r["bioguide_id"] for r in cosponsors if r["role"] == "original"}),
        "later_cosponsor_count": len({r["bioguide_id"] for r in cosponsors if r["role"] == "cosponsor"}),
        "active_cosponsor_count": len({r["bioguide_id"] for r in cosponsors if not r["withdrawn_date"]}),
        "withdrawn_cosponsor_count": len({r["bioguide_id"] for r in cosponsors if r["withdrawn_date"]}),
        "cosponsor_party_breadth": sorted({r["party"] for r in cosponsors if r["party"]}),
        "cosponsor_state_breadth": len({r["state"] for r in cosponsors if r["state"]}),
        "unique_amendment_count": len(amendments),
        "amendment_status_counts": {s: sum(s in a["observed_statuses"] for a in amendments.values()) for s in ("submitted", "proposed", "agreed", "failed", "withdrawn")},
        "amendments": sorted(amendments.values(), key=lambda a: a["amendment_id"]),
        "amendment_coverage": "nested_official_links_only_not_certified_complete",
        "as_of": as_of,
    }


def parse_bill_xml(xml_bytes: bytes | str, as_of: str | None = None, source_path: str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Parse only the top-level bill; never mistake related bills for this bill."""
    if as_of and _date(as_of) != as_of:
        raise ValueError("as_of must be an ISO YYYY-MM-DD date")
    root = ET.fromstring(xml_bytes)
    # Namespace-independent without descendant bill queries (nested amendments).
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    bill = root if root.tag == "bill" else root.find("bill")
    if bill is None:
        raise ValueError("BILLSTATUS record has no top-level bill")
    congress, typ, number = int(_text(bill, "congress")), _text(bill, "type").lower(), int(_text(bill, "number"))
    identity = f"{congress}:{typ}:{number}"
    introduced, title = _date(_text(bill, "introducedDate")), _plain(_text(bill, "title"))
    chamber = _text(bill, "originChamber")
    if chamber not in ("House", "Senate"):
        raise ValueError(f"{identity}: unsupported or missing originating chamber {chamber!r}")
    summaries, summaries_censored = _summaries(bill, as_of)
    constitutional = typ in ("hjres", "sjres") and bool(re.search(r"propos(?:ing|es|e) (?:an? |the )?(?:amendment|constitutional amendment)|(?:amendment to|amending) the constitution", title + " " + " ".join(x["text"] for x in summaries), re.I))
    reason = "constitutional_amendment_joint_resolution" if constitutional else ("non_law_measure_type" if typ not in LAW_TYPES else None)
    if as_of and introduced and introduced > as_of:
        reason = "introduced_after_as_of"
    eligible = reason is None
    actions_raw = [_evidence(a) for a in bill.findall("actions/item")]
    actions = [a for a in actions_raw if _within(a["date"], as_of)]
    actions.sort(key=lambda a: (a["date"], a["time"] or "", a["code"], a["text"]))
    unique_actions = {json.dumps(a, sort_keys=True): a for a in actions}
    actions = list(unique_actions.values())
    history_unknown = not actions_raw or any(not a["date"] for a in actions_raw)
    milestones = {}
    for stage in STAGES:
        unknown = history_unknown and stage != "BILL"
        milestones[stage] = {"date": None, "state": "not_applicable" if not eligible else ("unknown" if unknown else "observed"), "reached": False if not eligible or not unknown else None, "evidence": [], "basis": reason if not eligible else ("missing_or_undated_action_history" if unknown else "no_qualifying_action_in_supplied_history")}
    if eligible and _within(introduced, as_of):
        milestones["BILL"] = {"date": introduced, "state": "observed", "reached": True, "evidence": [{"date": introduced, "code": "introducedDate", "type": "introduction_metadata", "text": "Official introducedDate", "source_system": {"name": "GovInfo BILLSTATUS"}}], "basis": "official_introduction_date"}
    for action in actions:
        mapped = classify_action(action, chamber) if eligible else []
        action["mapped_stages"] = mapped
        for stage in mapped:
            current = milestones[stage]
            if current["date"] is None or action["date"] < current["date"]:
                milestones[stage] = {"date": action["date"], "state": "observed", "reached": True, "evidence": [dict(action)], "basis": "official_action"}
            elif action["date"] == current["date"]:
                current["evidence"].append(dict(action))
    roles = _roles(bill, identity, introduced, as_of)
    features = _candidate_features(bill, roles, as_of)
    exceptions = []
    if not actions_raw:
        exceptions.append("missing_action_history")
    if any(not a["date"] for a in actions_raw):
        exceptions.append("undated_action_history")
    if introduced and any(a["date"] and a["date"] < introduced for a in actions):
        exceptions.append("official_action_precedes_introduction")
    if introduced and any(m["date"] and m["date"] < introduced for m in milestones.values()):
        exceptions.append("official_milestone_precedes_introduction")
    if not introduced:
        exceptions.append("missing_introduction_date")
    if not any(r["role"] == "sponsor" for r in roles):
        exceptions.append("missing_sponsor")
    if any(r["chronology_status"] != "verified" for r in roles):
        exceptions.append("quarantined_role_chronology")
    if not summaries:
        exceptions.append("no_as_of_summary")
    law_metadata = [{"type": _text(x, "type"), "number": _text(x, "number")} for x in bill.findall("laws/item")]
    # Undated laws metadata corroborates identity only; it cannot date LAW.
    if eligible and law_metadata and not milestones["LAW"]["reached"] and not as_of:
        exceptions.append("law_metadata_without_dated_enactment_action")
        milestones["LAW"].update(state="unknown", reached=None, basis="undated_law_metadata")
    record = {
        "schema_version": 1, "mapper_version": MAPPER_VERSION, "measure_id": identity,
        "congress": congress, "measure_type": typ, "number": number, "chamber": chamber,
        "introduced_date": introduced, "title": title, "summaries": summaries,
        "summary": "\n\n".join(x["text"] for x in summaries), "eligible": eligible,
        "exclusion_reason": reason, "advocacy_eligible": reason != "introduced_after_as_of",
        "milestones": milestones, "actions": actions, "candidate_features": features,
        "as_of": as_of, "coverage_exceptions": exceptions,
        "coverage": {"action_history_rows": len(actions_raw), "as_of_action_rows": len(actions), "censored_action_rows": sum(bool(a["date"] and as_of and a["date"] > as_of) for a in actions_raw), "summary_rows": len(summaries), "censored_summary_rows": summaries_censored, "full_operative_text": "not_in_billstatus_archive", "historical_title_availability": "retrospective_snapshot_not_certified_as_published"},
        "source": {"system": "GovInfo BILLSTATUS", "path": source_path, "record_sha256": hashlib.sha256(xml_bytes.encode() if isinstance(xml_bytes, str) else xml_bytes).hexdigest(), "record_updated_date": _text(bill, "updateDate"), "url": _text(bill, "legislationUrl")},
    }
    return record, roles


def _archive_paths(archive_paths: Iterable[str | Path] | str | Path) -> list[Path]:
    values = [archive_paths] if isinstance(archive_paths, (str, Path)) else archive_paths
    expanded = []
    for value in values:
        path = Path(value).resolve()
        expanded.extend(path.glob("*.zip") if path.is_dir() else [path])
    paths = sorted(set(expanded), key=str)
    if not paths:
        raise ValueError("No local BILLSTATUS ZIP archives found")
    return paths


def iter_archive_bills(archive_paths: Iterable[str | Path] | str | Path, as_of: str | None = None) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """Deterministic unique top-level records; malformed/conflicting input fails."""
    seen: dict[str, str] = {}
    for path in _archive_paths(archive_paths):
        with zipfile.ZipFile(path) as archive:
            for entry in sorted(archive.namelist()):
                if not entry.lower().endswith(".xml") or entry.endswith("/"):
                    continue
                record, roles = parse_bill_xml(archive.read(entry), as_of, f"{path}!{entry}")
                identity, digest = record["measure_id"], record["source"]["record_sha256"]
                if identity in seen:
                    if seen[identity] != digest:
                        raise ValueError(f"Conflicting frozen records for {identity}; reconcile snapshots explicitly")
                    continue
                seen[identity] = digest
                if record["exclusion_reason"] != "introduced_after_as_of":
                    yield record, roles


def build_artifacts(archive_paths: Iterable[str | Path] | str | Path, output_dir: str | Path, as_of: str | None = None) -> dict[str, Any]:
    """Write canonical NDJSON and a hash-bound coverage manifest atomically."""
    paths, target = _archive_paths(archive_paths), Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    archives = []
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            expected = sum(n.lower().endswith(".xml") for n in archive.namelist())
        archives.append({"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path), "expected_top_level_xml_records": expected})
    counts, exclusions, exceptions, stage_counts, congress_types = Counter(), Counter(), Counter(), Counter(), defaultdict(set)
    roles_count, role_issues, max_introduced, max_updated = 0, Counter(), None, None
    bill_path, roles_path = target / "bill_milestones.ndjson", target / "member_measure_roles.ndjson"
    temporary_paths = [p.with_suffix(p.suffix + ".tmp") for p in (bill_path, roles_path)]
    with temporary_paths[0].open("w", encoding="utf-8", newline="\n") as bills_out, temporary_paths[1].open("w", encoding="utf-8", newline="\n") as roles_out:
        for record, roles in iter_archive_bills(paths, as_of):
            group = f'{record["congress"]}:{record["chamber"]}'
            counts[group + ":all"] += 1
            counts[f'{record["congress"]}:{record["measure_type"]}'] += 1
            congress_types[record["congress"]].add(record["measure_type"])
            if record["eligible"]:
                counts[group + ":eligible"] += 1
            else:
                exclusions[f'{record["congress"]}:{record["exclusion_reason"]}'] += 1
            for exception in record["coverage_exceptions"]:
                exceptions[f'{record["congress"]}:{exception}'] += 1
            for stage, milestone in record["milestones"].items():
                if milestone["reached"]:
                    stage_counts[group + ":" + stage] += 1
            introduced = record["introduced_date"]
            updated = record["source"]["record_updated_date"]
            max_introduced = max(max_introduced or "", introduced or "")
            max_updated = max(max_updated or "", updated or "")
            bills_out.write(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            for role in roles:
                roles_count += 1
                role_issues.update(role["chronology_exceptions"])
                roles_out.write(json.dumps(role, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    for temporary, destination in zip(temporary_paths, (bill_path, roles_path)):
        os.replace(temporary, destination)
    manifest = {
        "schema_version": 1, "mapper_version": MAPPER_VERSION, "status": "shadow_not_production_certified",
        "as_of": as_of, "date_convention": DATE_CONVENTION,
        "measure_scope": {"eligible_types": sorted(LAW_TYPES), "constitutional_joint_resolutions": "excluded", "letters_amendments_simple_and_concurrent_resolutions": "advocacy_only", "related_bill_law_transfer": "not_inferred"},
        "source_archives": archives, "counts": dict(sorted(counts.items())),
        "stage_counts": dict(sorted(stage_counts.items())), "exclusions": dict(sorted(exclusions.items())),
        "coverage_exceptions": dict(sorted(exceptions.items())), "role_rows": roles_count,
        "role_chronology_exceptions": dict(sorted(role_issues.items())),
        "missing_archive_measure_types": {str(c): sorted(set(TOP_LEVEL_TYPES) - types) for c, types in sorted(congress_types.items())},
        "max_introduced_date": max_introduced, "max_record_update": max_updated,
        "historical_member_population": {"status": "required_separate_dated_roster_reconciliation", "normalization_N": None, "never_use": "current_display_roster_or_raw_duplicate_roster_rows"},
        "limitations": ["Frozen archive coverage is not certified complete against a live authoritative inventory.", "Actions, role joins/withdrawals, amendments and summaries are censored by their available dates; current title metadata is retrospective.", "No committee action is invented from passage; no law is inferred from companion bills or undated law metadata.", "As-of output excludes introductions after the cutoff; archives can contain later records.", "Role date consistency does not certify membership service or committee/leadership tenure.", "Amendment evidence uses nested official links and does not certify whole-Congress amendment coverage."],
        "artifacts": {p.name: {"sha256": sha256_file(p), "bytes": p.stat().st_size} for p in (bill_path, roles_path)},
        "code_sha256": sha256_file(__file__),
    }
    manifest_path = target / "effectiveness_universe_manifest.json"
    temporary = manifest_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline='\r\n')
    os.replace(temporary, manifest_path)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", action="append", required=True, help="Local ZIP or directory; repeat for additional Congresses")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--as-of", help="ISO action/event/role cutoff YYYY-MM-DD")
    args = parser.parse_args()
    manifest = build_artifacts(args.archive, args.output_dir, args.as_of)
    print(json.dumps({"counts": manifest["counts"], "stage_counts": manifest["stage_counts"], "coverage_exceptions": manifest["coverage_exceptions"]}, indent=2))


if __name__ == "__main__":
    main()
