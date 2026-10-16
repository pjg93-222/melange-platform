"""Authenticate and compact only already-collected twin historical LDA pages."""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def packed(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def write_rows(path, rows):
    identity = hashlib.sha256()
    count = 0
    with gzip.open(path, "wt", encoding="utf8", compresslevel=6) as stream:
        for row in rows:
            line = packed(row) + "\n"
            identity.update(line.encode())
            stream.write(line)
            count += 1
    return {"path": str(path.resolve()), "records": count, "sha256": sha(path),
            "uncompressed_sha256": identity.hexdigest(), "bytes": path.stat().st_size}


def compact(root: Path, output: Path, expected_year: int) -> dict:
    root, output = Path(root), Path(output)
    if output.exists():
        raise ValueError("Immutable output already exists")
    for name in ("A", "B"):
        if not (root / name).is_dir():
            raise ValueError("Missing twin directory: " + str(root / name))
    twins = {name: {p.name: p for p in (root / name).glob("page_*.json")} for name in ("A", "B")}
    common = sorted(set(twins["A"]) & set(twins["B"]))
    exact, divergent = [], []
    for name in common:
        a, b = twins["A"][name], twins["B"][name]
        a_hash, b_hash = sha(a), sha(b)
        if a_hash == b_hash:
            exact.append((name, a, a_hash))
        else:
            divergent.append({"page": name, "a_sha256": a_hash, "b_sha256": b_hash})
    if not exact:
        raise ValueError("No byte-identical twin pages to authenticate")

    page_rows, evidence_rows, filing_ids = [], [], set()
    expected_total = set()
    for name, path, digest in exact:
        payload = json.loads(path.read_bytes())
        expected_total.add(payload.get("count"))
        results = payload.get("results") or []
        page_filing_ids = []
        for ordinal, filing in enumerate(results):
            if filing.get("filing_year") != expected_year:
                raise ValueError("Unexpected filing year in " + name)
            filing_id = filing.get("filing_uuid")
            if not filing_id or filing_id in filing_ids:
                raise ValueError("Missing or repeated filing UUID")
            filing_ids.add(filing_id)
            page_filing_ids.append(filing_id)
            registrant = filing.get("registrant") or {}
            client = filing.get("client") or {}
            for activity_ordinal, activity in enumerate(filing.get("lobbying_activities") or []):
                for lobbyist_ordinal, item in enumerate(activity.get("lobbyists") or []):
                    person = item.get("lobbyist") or {}
                    if person.get("id") is None:
                        continue
                    evidence_rows.append({
                        "filing_uuid": filing_id, "page": name, "filing_ordinal": ordinal,
                        "activity_ordinal": activity_ordinal, "lobbyist_ordinal": lobbyist_ordinal,
                        "person": f"lda:lobbyist:{person['id']}",
                        "name_parts": {key: person.get(key) for key in
                                       ("prefix", "first_name", "nickname", "middle_name", "last_name", "suffix")},
                        "covered_position": item.get("covered_position"), "new": item.get("new"),
                        "issue": activity.get("general_issue_code"),
                        "registrant_id": f"lda:registrant:{registrant.get('id')}" if registrant.get("id") is not None else None,
                        "client_id": f"lda:client:{client.get('id')}" if client.get("id") is not None else None,
                        "filing_period": filing.get("filing_period"), "dt_posted": filing.get("dt_posted"),
                        "page_sha256": digest,
                    })
        page_rows.append({
            "page": name, "sha256": digest, "bytes_per_twin": path.stat().st_size,
            "filings": len(results), "filing_uuid_inventory_sha256":
                hashlib.sha256(packed(page_filing_ids).encode()).hexdigest(),
            "next": payload.get("next"), "previous": payload.get("previous"),
        })
    if len(expected_total) > 1:
        raise ValueError("Twin-matched pages disagree on source total")

    single_rows = []
    for twin in ("A", "B"):
        for name in sorted(set(twins[twin]) - set(twins["A" if twin == "B" else "B"])):
            path = twins[twin][name]
            single_rows.append({"twin": twin, "page": name, "path": str(path.resolve()),
                                "sha256": sha(path), "bytes": path.stat().st_size,
                                "status": "SINGLE_TWIN_UNAUTHENTICATED"})

    output.mkdir(parents=True)
    files = [
        write_rows(output / "authenticated_pages.ndjson.gz", page_rows),
        write_rows(output / "authenticated_lobbyist_evidence.ndjson.gz", evidence_rows),
        write_rows(output / "held_single_twin_pages.ndjson.gz", single_rows),
        write_rows(output / "divergent_pages.ndjson.gz", divergent),
    ]
    exact_numbers = [int(name[5:10]) for name, _, _ in exact]
    receipt = {
        "schema": "partial-historical-page-compaction-v1", "status": "PASS",
        "source_root": str(root.resolve()), "expected_year": expected_year,
        "source_reported_total_filings": next(iter(expected_total), None),
        "twin_page_counts": {name: len(value) for name, value in twins.items()},
        "authenticated_twin_pages": len(exact), "divergent_twin_pages": len(divergent),
        "single_twin_pages_held": len(single_rows), "authenticated_filings": len(filing_ids),
        "authenticated_lobbyist_observations": len(evidence_rows),
        "authenticated_pages_contiguous_from_one": exact_numbers == list(range(1, len(exact_numbers) + 1)),
        "window_complete": False, "production_eligible": False, "new_collection": False,
        "policy": "Only byte-identical twin pages are authenticated. Single-twin and divergent pages remain held and cannot enter production.",
        "files": files,
    }
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--year", required=True, type=int)
    args = parser.parse_args()
    print(json.dumps(compact(args.root, args.output, args.year), indent=2))


if __name__ == "__main__":
    main()
