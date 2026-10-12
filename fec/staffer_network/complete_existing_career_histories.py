"""Authenticate deterministic career-date resolutions from evidence already held.

This produces an immutable sidecar.  It never changes the evidence ledger or the
live graph, and it fails closed whenever one undated claim could refer to more
than one dated interval.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from fec.staffer_network.operational_spine import role_state


def sha(path: Path | str) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def packed(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def norm(value) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).split())


def office(value) -> str:
    value = re.sub(r"(Democratic|Republican|Independent)(?=[A-Z-]|$).*$", "", str(value or ""))
    value = re.sub(r"^\s*(?:Office of\s+)?(?:Rep\.?|Sen\.?|Representative|Senator)\s+", "", value,
                   flags=re.I)
    return norm(value)


def normalized_title(value) -> str:
    return norm(value).replace("advisor", "adviser")


def contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and f" {phrase} " in f" {text} "


def claim_record(row) -> dict:
    keys = ("id", "person", "employer", "title", "raw_start", "raw_end",
            "start", "end_exclusive", "status", "payload")
    record = dict(zip(keys, row))
    record["payload"] = json.loads(record["payload"])
    return record


def classify_directory_claims(db: sqlite3.Connection):
    """Return decisions and gaps for undated Politico directory duplicates."""
    bounded = defaultdict(list)
    query = """SELECT id,person,employer,title,raw_start,raw_end,start,
                      end_exclusive,status,payload
               FROM claim WHERE source='directory' AND status=? ORDER BY id"""
    for row in db.execute(query, ("BOUNDED_SOURCE_INTERVAL",)):
        record = claim_record(row)
        bounded[(record["person"], record["employer"], norm(record["title"]))].append(record)

    decisions, gaps = [], []
    for row in db.execute(query, ("MISSING_DATE_BOUND",)):
        missing = claim_record(row)
        key = (missing["person"], missing["employer"], norm(missing["title"]))
        candidates = bounded.get(key, [])
        intervals = sorted({(item["start"], item["end_exclusive"]) for item in candidates})
        observed = missing["payload"].get("source_observed_at")
        exact_provenance = bool(candidates) and all(
            item["payload"].get("source_observed_at") == observed
            and norm(item["payload"].get("title")) == norm(missing["payload"].get("title"))
            for item in candidates
        )
        references = [{"claim_id": item["id"], "interval": [item["start"], item["end_exclusive"]],
                       "payload_sha256": hashlib.sha256(packed(item["payload"]).encode()).hexdigest()}
                      for item in candidates]
        base = {
            "person": missing["person"], "employer": missing["employer"],
            "title": missing["title"], "missing_claim_id": missing["id"],
            "missing_payload_sha256": hashlib.sha256(packed(missing["payload"]).encode()).hexdigest(),
            "bounded_references": references, "source_observed_at": observed,
        }
        if (len(intervals) == 1 and exact_provenance
                and role_state(missing["title"]) == "SUBSTANTIVE"):
            decisions.append({
                **base, "status": "RESOLVED_EXACT_POLITICO_DUPLICATE",
                "interval": list(intervals[0]),
                "method": "SAME_POLITICO_PERSON_OFFICE_TITLE_AND_OBSERVATION",
                "dates_invented": False, "identity_merged": False,
            })
        else:
            if not candidates:
                state = "NO_EXACT_HELD_DATE_EVIDENCE"
            elif len(intervals) > 1:
                state = "AMBIGUOUS_REPEATED_ROLE_INTERVALS"
            elif not exact_provenance:
                state = "PROVENANCE_MISMATCH"
            else:
                state = "ROLE_NOT_SUBSTANTIVE"
            gaps.append({**base, "status": state, "candidate_intervals": [list(x) for x in intervals]})
    return decisions, gaps


def classify_release_clauses(release: dict):
    """Use only accepted release bindings and their explicit directory support."""
    groups = defaultdict(lambda: {"intervals": set(), "role_ids": set(), "support": [],
                                  "source": set(), "target": set(), "decision_ids": set()})
    for binding in release.get("identity_bindings", []):
        if binding.get("kind") != "covered_politico":
            continue
        for item in binding.get("evidence", {}).get("support", []):
            if (item.get("covered_dates", {}).get("status") != "MISSING_DATE_BOUND"
                    or not item.get("dates_from_directory_only")):
                continue
            interval = tuple(item.get("directory_dates") or ())
            if len(interval) != 2:
                continue
            group = groups[item["clause_id"]]
            group["intervals"].add(interval)
            group["role_ids"].add(item["directory_role_id"])
            group["support"].append(item)
            group["source"].add(binding["source"])
            group["target"].add(binding["target"])
            group["decision_ids"].add(binding["decision_id"])

    decisions, gaps = [], []
    for clause_id, group in sorted(groups.items()):
        intervals = sorted(group.pop("intervals"))
        record = {
            "clause_id": clause_id, "source_people": sorted(group.pop("source")),
            "politico_people": sorted(group.pop("target")),
            "binding_decision_ids": sorted(group.pop("decision_ids")),
            "directory_role_ids": sorted(group.pop("role_ids")),
            "support": group["support"], "candidate_intervals": [list(x) for x in intervals],
        }
        if len(intervals) == 1:
            decisions.append({
                **record, "status": "RESOLVED_ACCEPTED_LDA_POLITICO_INTERVAL",
                "interval": list(intervals[0]),
                "method": "ACCEPTED_IDENTITY_BINDING_PLUS_EXACT_DIRECTORY_ROLE_SUPPORT",
                "dates_invented": False, "identity_merged": False,
            })
        else:
            gaps.append({**record, "status": "AMBIGUOUS_COMPOUND_CAREER_CLAUSE"})
    return decisions, gaps


def classify_historical_clauses(ledger: sqlite3.Connection, historical: sqlite3.Connection):
    """Normalize bounded historical clauses only on exact held office/title phrases."""
    catalog = defaultdict(lambda: {"employer_labels": set(), "title_labels": set(),
                                  "employer_ids": set(), "claim_ids": set()})
    for claim_id, employer_id, job, payload in ledger.execute(
            "SELECT id,employer,title,payload FROM claim "
            "WHERE source='directory' AND status='BOUNDED_SOURCE_INTERVAL'"):
        payload = json.loads(payload)
        employer_label = payload.get("employer") or ""
        employer_key, title_key = office(employer_label), normalized_title(job)
        if len(employer_key.split()) < 2 or len(title_key) < 4:
            continue
        item = catalog[(employer_key, title_key)]
        item["employer_labels"].add(employer_label)
        item["title_labels"].add(job)
        item["employer_ids"].add(employer_id)
        item["claim_ids"].add(claim_id)

    bounded = list(historical.execute(
        "SELECT cl.id,cl.claim_id,c.person,cl.raw_text,cl.assessment "
        "FROM clause cl JOIN claim c ON c.id=cl.claim_id "
        "WHERE cl.status='BOUNDED_SOURCE_INTERVAL' ORDER BY cl.id"))
    bounded_claims = {row[1] for row in bounded}
    support = defaultdict(list)
    for window_id, support_id, claim_id, filing_uuid, activity in historical.execute(
            "SELECT window_id,id,claim_id,filing_uuid,activity FROM support"):
        if claim_id in bounded_claims:
            support[claim_id].append({"window_id": window_id, "support_id": support_id,
                                      "filing_uuid": filing_uuid, "activity": activity})

    decisions, gaps = [], []
    # Match candidate office phrases by their first token, avoiding a full
    # catalog scan for every clause while preserving the exact phrase rule.
    catalog_by_token = defaultdict(list)
    for key, item in catalog.items():
        catalog_by_token[key[0].split()[0]].append((key, item))
    for clause_id, claim_id, person, raw_text, assessment in bounded:
        text = normalized_title(raw_text)
        matches = []
        for token in sorted(set(text.split())):
            for (employer_key, title_key), item in catalog_by_token.get(token, ()):
                if contains_phrase(text, employer_key) and contains_phrase(text, title_key):
                    matches.append((employer_key, title_key, item))
        # A shorter title nested inside one longer exact title is redundant.
        if len(matches) > 1 and len({item[0] for item in matches}) == 1:
            longest = max(matches, key=lambda item: len(item[1]))
            if all(contains_phrase(longest[1], item[1]) for item in matches):
                matches = [longest]
        base = {
            "clause_id": clause_id, "historical_claim_id": claim_id, "person": person,
            "raw_text": raw_text, "assessment": json.loads(assessment),
            "filing_support": sorted(support.get(claim_id, []), key=packed),
        }
        if len(matches) == 1:
            employer_key, title_key, item = matches[0]
            decisions.append({
                **base, "status": "STRUCTURED_EXACT_HELD_OFFICE_TITLE",
                "employer_key": employer_key, "title_key": title_key,
                "employer_labels": sorted(item["employer_labels"]),
                "title_labels": sorted(item["title_labels"]),
                "employer_ids": sorted(item["employer_ids"]),
                "directory_claim_ids": sorted(item["claim_ids"]),
                "method": "EXACT_CONTIGUOUS_OFFICE_AND_TITLE_PHRASES_IN_SOURCE_CLAUSE",
                "identity_merged": False, "employment_promoted": False,
            })
        else:
            candidates = [{"employer_key": employer_key, "title_key": title_key,
                           "employer_ids": sorted(item["employer_ids"]),
                           "directory_claim_ids": sorted(item["claim_ids"])}
                          for employer_key, title_key, item in matches]
            gaps.append({
                **base,
                "status": "NO_EXACT_HELD_OFFICE_TITLE" if not matches
                          else "AMBIGUOUS_EXACT_HELD_OFFICE_TITLE",
                "candidates": candidates,
            })
    return decisions, gaps


def write_gzip(path: Path, records: list[dict]) -> dict:
    identity = hashlib.sha256()
    with gzip.open(path, "wt", encoding="utf8", compresslevel=6) as stream:
        for record in records:
            line = packed(record) + "\n"
            identity.update(line.encode())
            stream.write(line)
    return {"path": str(path.resolve()), "records": len(records), "sha256": sha(path),
            "uncompressed_sha256": identity.hexdigest(), "bytes": path.stat().st_size}


def complete(ledger: Path, release_path: Path, compact_receipt_path: Path,
             historical_db: Path | None, output: Path) -> dict:
    ledger, release_path, compact_receipt_path, output = map(
        Path, (ledger, release_path, compact_receipt_path, output))
    if output.exists():
        raise ValueError("Immutable output already exists")
    release = json.loads(release_path.read_bytes())
    compact = json.loads(compact_receipt_path.read_bytes())
    if release.get("status") != "PASS" or compact.get("status") != "PASS":
        raise ValueError("Accepted release and compaction must pass")
    ledger_hash = sha(ledger)
    if release["inputs"]["ledger_sha256"] != ledger_hash:
        raise ValueError("Ledger changed after accepted release")
    if compact["accepted_release"]["sha256"] != sha(release_path):
        raise ValueError("Compaction and release bindings differ")
    for item in compact["files"]:
        if sha(item["path"]) != item["sha256"]:
            raise ValueError("Compacted evidence changed")

    uri = "file:" + ledger.resolve().as_posix() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        directory_decisions, directory_gaps = classify_directory_claims(db)
    lda_decisions, lda_gaps = classify_release_clauses(release)

    historical = None
    historical_decisions, historical_gaps = [], []
    if historical_db:
        historical_db = Path(historical_db)
        huri = "file:" + historical_db.resolve().as_posix() + "?mode=ro"
        with sqlite3.connect(huri, uri=True) as db:
            windows = [(row[0], json.loads(row[1])) for row in db.execute("SELECT id,receipt FROM window")]
            if any(receipt.get("status") != "PASS" for _, receipt in windows):
                raise ValueError("Historical holdings include a failed window")
            sidecars = []
            for suffix in ("-wal", "-shm"):
                sidecar = Path(str(historical_db) + suffix)
                if sidecar.exists():
                    sidecars.append({"path": str(sidecar.resolve()), "sha256": sha(sidecar),
                                     "bytes": sidecar.stat().st_size})
            historical = {
                "path": str(historical_db.resolve()), "sha256": sha(historical_db),
                "sqlite_sidecars": sidecars,
                "passed_windows": sorted(window for window, _ in windows),
                "bounded_clauses": db.execute(
                    "SELECT count(*) FROM clause WHERE status='BOUNDED_SOURCE_INTERVAL'").fetchone()[0],
                "unstructured_bounded_clauses": db.execute(
                    "SELECT count(*) FROM clause WHERE status='BOUNDED_SOURCE_INTERVAL' "
                    "AND json_extract(assessment,'$.structured_employer_title')='REQUIRES_ADJUDICATION'").fetchone()[0],
            }
            with sqlite3.connect(uri, uri=True) as ledger_db:
                historical_decisions, historical_gaps = classify_historical_clauses(ledger_db, db)

    decisions = sorted(directory_decisions + lda_decisions + historical_decisions,
                       key=lambda row: (row["status"], row.get("missing_claim_id", row.get("clause_id", ""))))
    gaps = sorted(directory_gaps + lda_gaps + historical_gaps,
                  key=lambda row: (row["status"], row.get("missing_claim_id", row.get("clause_id", ""))))
    output.mkdir(parents=True)
    files = [write_gzip(output / "career_date_decisions.ndjson.gz", decisions),
             write_gzip(output / "remaining_career_gaps.ndjson.gz", gaps)]
    counts = Counter(row["status"] for row in decisions + gaps)
    receipt = {
        "schema": "existing-career-completion-v1", "status": "PASS",
        "policy": "Existing evidence only; exact authenticated transfers; ambiguity held; internships excluded; no live activation.",
        "inputs": {
            "ledger": {"path": str(ledger.resolve()), "sha256": ledger_hash},
            "release": {"path": str(release_path.resolve()), "sha256": sha(release_path)},
            "compaction": {"path": str(compact_receipt_path.resolve()), "sha256": sha(compact_receipt_path)},
            "historical_holdings": historical,
        },
        "counts": dict(sorted(counts.items())), "files": files,
        "dates_invented": 0, "identities_merged": 0, "new_collection": False,
        "live_activated": False,
        "next_gate": "Review residual bounded historical clauses, then produce exact older-filing target periods.",
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--compaction", required=True, type=Path)
    parser.add_argument("--historical-db", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(complete(args.ledger, args.release, args.compaction,
                              args.historical_db, args.output), indent=2))


if __name__ == "__main__":
    main()
