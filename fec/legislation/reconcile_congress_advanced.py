#!/usr/bin/env python3
"""Normalize Congress.gov Advanced Search CSVs and reconcile coverage.

Inputs are the official CSV downloads and run manifest produced by
``collect_congress_advanced.mjs``.  No category is promoted into scoring here;
the output is a Congress-qualified candidate corpus plus an auditable gap
report against the historical member-by-bill archive.
"""

from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import csv
import hashlib
import html
import json
import re
from collections import defaultdict
from datetime import date, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUN = (
    ROOT
    / "audits"
    / "legfix5"
    / "congress_advanced_run_20260725"
    / "run_manifest.json"
)
DEFAULT_ARCHIVE = ROOT / "congress_bills_archive_20260624_115822.xlsx"
DEFAULT_REGISTRY = (
    ROOT / "source_data" / "legislation" / "acu_legislation_search_registry.json"
)
WINDOW_START = date.fromisoformat("2020-07-25")
WINDOW_END = date.fromisoformat("2026-07-25")

URL_KIND = {
    "house-bill": "hr",
    "senate-bill": "s",
    "house-resolution": "hres",
    "senate-resolution": "sres",
    "house-joint-resolution": "hjres",
    "senate-joint-resolution": "sjres",
    "house-concurrent-resolution": "hconres",
    "senate-concurrent-resolution": "sconres",
    "house-amendment": "hamdt",
    "senate-amendment": "samdt",
}
DISPLAY_PREFIX = {
    "hr": "H.R.",
    "s": "S.",
    "hres": "H.Res.",
    "sres": "S.Res.",
    "hjres": "H.J.Res.",
    "sjres": "S.J.Res.",
    "hconres": "H.Con.Res.",
    "sconres": "S.Con.Res.",
    "hamdt": "H.Amdt.",
    "samdt": "S.Amdt.",
}
ARCHIVE_NUMBER_RE = re.compile(
    r"^\s*(H\.R\.|S\.|H\.Res\.|S\.Res\.|H\.J\.Res\.|S\.J\.Res\.|"
    r"H\.Con\.Res\.|S\.Con\.Res\.|H\.Amdt\.|S\.Amdt\.)\s*(\d+)\s*$",
    re.IGNORECASE,
)
ARCHIVE_KIND = {
    "h.r.": "hr",
    "s.": "s",
    "h.res.": "hres",
    "s.res.": "sres",
    "h.j.res.": "hjres",
    "s.j.res.": "sjres",
    "h.con.res.": "hconres",
    "s.con.res.": "sconres",
    "h.amdt.": "hamdt",
    "s.amdt.": "samdt",
}


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def html_to_text(value: str) -> str:
    if not value:
        return ""
    parser = _HTMLText()
    parser.feed(html.unescape(value))
    return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = defaultdict(int)
    output: list[str] = []
    for raw in headers:
        name = raw.strip() or "unnamed"
        counts[name] += 1
        suffix = "" if counts[name] == 1 else f"_{counts[name]}"
        output.append(name + suffix)
    return output


def read_congress_csv(path: Path) -> tuple[dict[str, str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header_index = next(
        (
            index
            for index, row in enumerate(rows)
            if row and row[0].strip() == "Legislation Number"
        ),
        None,
    )
    if header_index is None:
        raise ValueError(f"No Congress.gov header row in {path}")

    metadata = {
        "downloaded_on": ",".join(rows[0][1:]).strip() if rows else "",
        "search_url": rows[1][1].strip() if len(rows) > 1 and len(rows[1]) > 1 else "",
    }
    headers = unique_headers(rows[header_index])
    records: list[dict[str, str]] = []
    for raw in rows[header_index + 1 :]:
        if not any(cell.strip() for cell in raw):
            continue
        padded = raw + [""] * max(0, len(headers) - len(raw))
        values = padded[: len(headers)]
        if values[0].strip():
            records.append(dict(zip(headers, values)))
            continue
        if not records:
            raise ValueError(f"Continuation row precedes first record in {path}")
        for header, value in zip(headers, values):
            if not value.strip():
                continue
            separator = "\n" if records[-1].get(header) else ""
            records[-1][header] = records[-1].get(header, "") + separator + value
    return metadata, records


def parse_congress_url(url: str) -> tuple[int, str, int]:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if len(parts) < 4 or parts[0] not in {"bill", "amendment"}:
        raise ValueError(f"Unrecognized Congress.gov legislation URL: {url}")
    congress_match = re.fullmatch(r"(\d+)th-congress", parts[1])
    if not congress_match or parts[2] not in URL_KIND:
        raise ValueError(f"Unrecognized Congress.gov legislation URL: {url}")
    return int(congress_match.group(1)), URL_KIND[parts[2]], int(parts[3])


def parse_date(value: str) -> date | None:
    value = value.strip()
    if not value:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def date_status(primary_date: date | None) -> str:
    if primary_date is None:
        return "date_unknown"
    if primary_date < WINDOW_START:
        return "boundary_extension_before_window"
    if primary_date > WINDOW_END:
        return "after_required_window"
    return "within_required_window"


def normalize_row(row: dict[str, str]) -> dict[str, Any]:
    congress, kind, number = parse_congress_url(row["URL"])
    introduction = parse_date(row.get("Date of Introduction", ""))
    offered = parse_date(row.get("Date Offered", ""))
    submitted = parse_date(row.get("Date Submitted", ""))
    proposed = parse_date(row.get("Date Proposed", ""))
    primary_date = introduction or submitted or offered or proposed
    cosponsors = [
        value.strip()
        for key, value in row.items()
        if key.startswith("Cosponsor") and value.strip()
    ]
    subject_terms = [
        value.strip()
        for key, value in row.items()
        if key.startswith("billSubjectTerm") and value.strip()
    ]
    related_bills = [
        value.strip()
        for key, value in row.items()
        if re.fullmatch(r"Related Bill(?:_\d+)?", key) and value.strip()
    ]
    key = f"{congress}:{kind}:{number}"
    return {
        "bill_key": key,
        "congress": congress,
        "legislation_type": kind,
        "number": f"{DISPLAY_PREFIX[kind]}{number}",
        "url": row["URL"].strip(),
        "is_amendment": kind in {"hamdt", "samdt"},
        "title": row.get("Title", "").strip(),
        "sponsor": row.get("Sponsor", "").strip(),
        "sponsor_party": row.get("Party of Sponsor", "").strip(),
        "date_of_introduction": introduction.isoformat() if introduction else None,
        "date_offered": offered.isoformat() if offered else None,
        "date_submitted": submitted.isoformat() if submitted else None,
        "date_proposed": proposed.isoformat() if proposed else None,
        "primary_date": primary_date.isoformat() if primary_date else None,
        "window_status": date_status(primary_date),
        "committees": row.get("Committees", "").strip(),
        "latest_action": row.get("Latest Action", "").strip(),
        "latest_action_date": row.get("Latest Action Date", "").strip(),
        "latest_tracker_stage": row.get("Latest Tracker Stage", "").strip(),
        "cosponsors": cosponsors,
        "number_of_cosponsors": row.get("Number of Cosponsors", "").strip(),
        "policy_area": row.get("billPolicyArea", "").strip(),
        "subject_terms": subject_terms,
        "number_of_related_bills": row.get("Number of Related Bills", "").strip(),
        "related_bills": related_bills,
        "latest_summary_html": row.get("Latest Summary", "").strip(),
        "latest_summary_text": html_to_text(row.get("Latest Summary", "")),
        "amends_bill": row.get("Amends Bill", "").strip(),
        "amends_amendment": row.get("Amends Amendment", "").strip(),
        "amendment_text_latest": row.get("Amendment Text (Latest)", "").strip(),
        "categories": [],
        "category_labels": [],
        "record_kinds": [],
        "source_chunks": [],
        "source_csvs": [],
        "search_urls": [],
    }


def merge_record(
    target: dict[str, Any],
    incoming: dict[str, Any],
    chunk: dict[str, Any],
    csv_path: Path,
) -> None:
    scalar_fields = (
        "title",
        "sponsor",
        "sponsor_party",
        "date_of_introduction",
        "date_offered",
        "date_submitted",
        "date_proposed",
        "primary_date",
        "committees",
        "latest_action",
        "latest_action_date",
        "latest_tracker_stage",
        "number_of_cosponsors",
        "policy_area",
        "subject_terms",
        "number_of_related_bills",
        "related_bills",
        "latest_summary_html",
        "latest_summary_text",
        "amends_bill",
        "amends_amendment",
        "amendment_text_latest",
    )
    for field in scalar_fields:
        if not target.get(field) and incoming.get(field):
            target[field] = incoming[field]
    target["cosponsors"] = sorted(set(target["cosponsors"]) | set(incoming["cosponsors"]))
    target["subject_terms"] = sorted(
        set(target["subject_terms"]) | set(incoming["subject_terms"])
    )
    target["related_bills"] = sorted(
        set(target["related_bills"]) | set(incoming["related_bills"])
    )

    category_id = chunk["category_id"]
    if category_id not in target["categories"]:
        target["categories"].append(category_id)
        target["category_labels"].append(chunk["category_label"])
    record_kind = chunk.get("record_kind", "outline_category")
    if record_kind not in target["record_kinds"]:
        target["record_kinds"].append(record_kind)
    if chunk["chunk_key"] not in target["source_chunks"]:
        target["source_chunks"].append(chunk["chunk_key"])
    csv_relative = str(csv_path.relative_to(ROOT)).replace("\\", "/")
    if csv_relative not in target["source_csvs"]:
        target["source_csvs"].append(csv_relative)
    if chunk["search_url"] not in target["search_urls"]:
        target["search_urls"].append(chunk["search_url"])


def archive_key(congress: Any, number: Any) -> str | None:
    try:
        congress_int = int(congress)
    except (TypeError, ValueError):
        return None
    match = ARCHIVE_NUMBER_RE.match(str(number))
    if not match:
        return None
    kind = ARCHIVE_KIND[match.group(1).lower()]
    return f"{congress_int}:{kind}:{int(match.group(2))}"


def known_bill_key(bill: dict[str, Any]) -> str:
    match = re.fullmatch(r"([a-z]+)(\d+)", bill["compact_cite"])
    if not match:
        raise ValueError(f"Bad compact cite: {bill['compact_cite']}")
    return f"{bill['congress']}:{match.group(1)}:{int(match.group(2))}"


def write_candidate_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fields = [
        "bill_key",
        "congress",
        "legislation_type",
        "number",
        "is_amendment",
        "title",
        "sponsor",
        "sponsor_party",
        "primary_date",
        "window_status",
        "latest_action_date",
        "latest_tracker_stage",
        "number_of_cosponsors",
        "policy_area",
        "amends_bill",
        "categories",
        "record_kinds",
        "url",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            output = {field: record.get(field) for field in fields}
            output["categories"] = "|".join(record["categories"])
            output["record_kinds"] = "|".join(record["record_kinds"])
            writer.writerow(output)


def markdown_report(reconciliation: dict[str, Any]) -> str:
    acceptance = reconciliation["acceptance"]
    lines = [
        "# Congress.gov Advanced Search Legislative Reconciliation",
        "",
        f"Run status: **{reconciliation['run_status']}**",
        "",
        "## Coverage outcome",
        "",
        f"- Congress-qualified candidates: **{reconciliation['new_corpus']['records']:,}**",
        f"- Bills/resolutions: **{reconciliation['new_corpus']['bills_and_resolutions']:,}**",
        f"- Amendments: **{reconciliation['new_corpus']['amendments']:,}**",
        f"- Within the exact 2020-07-25 to 2026-07-25 window: **{reconciliation['new_corpus']['within_required_window']:,}**",
        f"- 116th-Congress boundary extension before the exact cutoff: **{reconciliation['new_corpus']['boundary_extension_before_window']:,}**",
        f"- Historical archive distinct bills/resolutions: **{reconciliation['historical_archive']['distinct_records']:,}**",
        f"- Historical records recovered by the new corpus: **{reconciliation['historical_archive']['recovered']:,}**",
        f"- New bills/resolutions not in the historical archive: **{reconciliation['historical_archive']['new_not_in_archive']:,}**",
        "",
        "## Acceptance gates",
        "",
        f"- Outline-named 119th-Congress measures: **{acceptance['outline_named_found']}/{acceptance['outline_named_total']}**",
        f"- Six known Credit Card Competition Act bills: **{acceptance['interchange_family_found']}/6**",
        f"- Credit-union headline recall net: **{acceptance['credit_union_recall_net_records']:,}** distinct records",
        f"- Raw CSV row-count checks: **{acceptance['csv_row_count_checks_passed']}/{acceptance['csv_row_count_checks_total']}**",
        f"- Overall acceptance: **{'PASS' if acceptance['passed'] else 'FAIL'}**",
        "",
        "## Per-category comparison",
        "",
        "| Category | Historical distinct | New distinct | Recovered | New not historical |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in reconciliation["per_category"]:
        lines.append(
            f"| {row['label']} | {row['historical_distinct']} | "
            f"{row['new_distinct']} | {row['historical_recovered']} | "
            f"{row['new_not_historical']} |"
        )
    lines.extend(
        [
            "",
            "## Promotion rule",
            "",
            "This is a candidate corpus. Search inclusion does not by itself establish "
            "ACU relevance, category membership, or SUPPORT/OPPOSE/MONITOR stance.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", type=Path, default=DEFAULT_RUN)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()

    run_path = args.run.resolve()
    run = json.loads(run_path.read_text(encoding="utf-8"))
    registry = json.loads(args.registry.resolve().read_text(encoding="utf-8"))
    out_dir = run_path.parent

    by_key: dict[str, dict[str, Any]] = {}
    csv_checks: list[dict[str, Any]] = []
    for chunk in run["chunks"]:
        if not chunk.get("raw_csv"):
            csv_checks.append(
                {
                    "chunk_key": chunk["chunk_key"],
                    "expected": chunk["result_total"],
                    "actual": 0,
                    "passed": chunk["result_total"] == 0,
                }
            )
            continue
        csv_path = ROOT / chunk["raw_csv"]
        actual_hash = sha256(csv_path)
        if actual_hash != chunk["raw_csv_sha256"]:
            raise ValueError(f"SHA-256 mismatch: {csv_path}")
        metadata, rows = read_congress_csv(csv_path)
        check = {
            "chunk_key": chunk["chunk_key"],
            "expected": chunk["result_total"],
            "actual": len(rows),
            "passed": len(rows) == chunk["result_total"],
            "search_url_matches": metadata["search_url"] == chunk["search_url"],
        }
        csv_checks.append(check)
        if not check["passed"] or not check["search_url_matches"]:
            raise ValueError(f"Congress.gov CSV reconciliation failed: {check}")

        for row in rows:
            incoming = normalize_row(row)
            target = by_key.setdefault(incoming["bill_key"], incoming)
            merge_record(target, incoming, chunk, csv_path)

    records = sorted(
        by_key.values(),
        key=lambda record: (
            record["congress"],
            record["legislation_type"],
            int(record["bill_key"].split(":")[-1]),
        ),
    )
    for record in records:
        for field in (
            "categories",
            "category_labels",
            "record_kinds",
            "source_chunks",
            "source_csvs",
            "search_urls",
        ):
            record[field] = sorted(record[field])

    archive = pd.read_excel(args.archive.resolve(), sheet_name="All Bills")
    archive_by_category: dict[str, set[str]] = defaultdict(set)
    archive_all: set[str] = set()
    category_by_label = {
        category["label"]: category["id"] for category in registry["categories"]
    }
    for row in archive.itertuples(index=False):
        key = archive_key(getattr(row, "Congress"), getattr(row, "Name"))
        if not key:
            continue
        archive_all.add(key)
        section = str(getattr(row, "Section"))
        category_id = category_by_label.get(section)
        if category_id:
            archive_by_category[category_id].add(key)

    new_all = {
        record["bill_key"]
        for record in records
        if not record["is_amendment"]
    }
    new_by_category: dict[str, set[str]] = defaultdict(set)
    for record in records:
        for category_id in record["categories"]:
            if category_id in {category["id"] for category in registry["categories"]}:
                new_by_category[category_id].add(record["bill_key"])

    per_category: list[dict[str, Any]] = []
    for category in registry["categories"]:
        historical = archive_by_category[category["id"]]
        new = new_by_category[category["id"]]
        per_category.append(
            {
                "category_id": category["id"],
                "label": category["label"],
                "scoring_category": category["scoring_category"],
                "historical_distinct": len(historical),
                "new_distinct": len(new),
                "historical_recovered": len(historical & new),
                "historical_missing": len(historical - new),
                "new_not_historical": len(new - historical),
            }
        )

    known_bills = [
        (category, bill)
        for category in registry["categories"]
        for bill in category["known_bills"]
    ]
    known_results = [
        {
            "category_id": category["id"],
            "category_label": category["label"],
            **bill,
            "bill_key": known_bill_key(bill),
            "found": known_bill_key(bill) in by_key,
        }
        for category, bill in known_bills
    ]
    interchange_family = {
        "117:s:4674",
        "117:hr:8874",
        "118:s:1838",
        "118:hr:3881",
        "119:s:3623",
        "119:hr:7035",
    }
    recall_records = sum(
        "credit_union_headline_recall_net" in record["categories"]
        for record in records
    )
    acceptance = {
        "outline_named_total": len(known_results),
        "outline_named_found": sum(result["found"] for result in known_results),
        "outline_named_missing": [
            result for result in known_results if not result["found"]
        ],
        "interchange_family_found": len(interchange_family & set(by_key)),
        "interchange_family_missing": sorted(interchange_family - set(by_key)),
        "credit_union_recall_net_records": recall_records,
        "csv_row_count_checks_total": len(csv_checks),
        "csv_row_count_checks_passed": sum(
            check["passed"] and check.get("search_url_matches", True)
            for check in csv_checks
        ),
    }
    acceptance["passed"] = (
        acceptance["outline_named_found"] == acceptance["outline_named_total"]
        and acceptance["interchange_family_found"] == 6
        and acceptance["credit_union_recall_net_records"] >= 1_000
        and acceptance["csv_row_count_checks_passed"]
        == acceptance["csv_row_count_checks_total"]
        and run["status"] == "complete"
        and not run.get("failures")
    )

    reconciliation = {
        "schema": "acu-congress-advanced-search-reconciliation-v1",
        "run_manifest": str(run_path.relative_to(ROOT)).replace("\\", "/"),
        "run_status": run["status"],
        "source_outline_sha256": registry["source"]["sha256"],
        "required_window": {
            "start": WINDOW_START.isoformat(),
            "end": WINDOW_END.isoformat(),
        },
        "new_corpus": {
            "records": len(records),
            "bills_and_resolutions": sum(
                not record["is_amendment"] for record in records
            ),
            "amendments": sum(record["is_amendment"] for record in records),
            "within_required_window": sum(
                record["window_status"] == "within_required_window"
                for record in records
            ),
            "boundary_extension_before_window": sum(
                record["window_status"] == "boundary_extension_before_window"
                for record in records
            ),
            "date_unknown": sum(
                record["window_status"] == "date_unknown" for record in records
            ),
        },
        "historical_archive": {
            "path": str(args.archive.resolve().relative_to(ROOT)).replace("\\", "/"),
            "distinct_records": len(archive_all),
            "recovered": len(archive_all & new_all),
            "missing_from_new": len(archive_all - new_all),
            "new_not_in_archive": len(new_all - archive_all),
            "missing_keys": sorted(archive_all - new_all),
        },
        "acceptance": acceptance,
        "known_bill_results": known_results,
        "csv_checks": csv_checks,
        "per_category": per_category,
    }

    candidate_json = out_dir / "legislation_candidates.json"
    candidate_csv = out_dir / "legislation_candidates.csv"
    reconciliation_json = out_dir / "reconciliation.json"
    reconciliation_md = out_dir / "RECONCILIATION.md"

    candidate_json.write_text(
        json.dumps(
            {
                "schema": "acu-congress-qualified-legislation-candidates-v1",
                "source_manifest": str(run_path.relative_to(ROOT)).replace("\\", "/"),
                "source_outline_sha256": registry["source"]["sha256"],
                "promotion_status": "candidate_only",
                "records": records,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    write_candidate_csv(candidate_csv, records)
    reconciliation_json.write_text(
        json.dumps(reconciliation, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    reconciliation_md.write_text(markdown_report(reconciliation), encoding="utf-8")

    print(
        json.dumps(
            {
                "records": len(records),
                "bills_and_resolutions": reconciliation["new_corpus"][
                    "bills_and_resolutions"
                ],
                "amendments": reconciliation["new_corpus"]["amendments"],
                "outline_named": (
                    f"{acceptance['outline_named_found']}/"
                    f"{acceptance['outline_named_total']}"
                ),
                "interchange_family": f"{acceptance['interchange_family_found']}/6",
                "credit_union_recall_net": recall_records,
                "acceptance": "PASS" if acceptance["passed"] else "FAIL",
                "outputs": [
                    str(candidate_json),
                    str(candidate_csv),
                    str(reconciliation_json),
                    str(reconciliation_md),
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
