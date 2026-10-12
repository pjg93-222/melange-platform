# -*- coding: utf-8 -*-
"""Assemble bill-position work items for the full-scope interaction ledger.

The shipped ``lobbying.classify_bill_positions.assemble_work_items`` is worklist-driven and
scoped to the calibration corpus.  The interaction ledger spans the whole 2021-2026
full-scope sweep, so this module walks the SAME filings the ledger walked, using the
SAME attribution, and hands each one to the shipped
``lobbying.classify_bill_positions.work_items_from_payload``.

That matters: the prompt, the title resolution, the not-in-BILLSTATUS skip rule, and
the work-item validation are all the code that produced the published 94.7% agreement
report.  Nothing about the classification is re-implemented here -- this module only
decides WHICH filings get classified.

COVERAGE IS NARROWER THAN THE LEDGER, ON PURPOSE.  ``work_items_from_payload`` emits
an item only for a FORMALLY CITED bill (``resolved["parsed"]``), not for a title-only
match.  Classifying "what position did they take on H.R.1181" is only answerable when
the filer actually named H.R.1181.  Ledger rows that reached a member through a title
match therefore keep ``position_basis='not_disclosed'`` and are reported as such.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import json
import os
import sys

from lobbying import build_network_2021_rebuild as builder
from lobbying import classify_bill_positions as classifier
from lobbying import lobby_interactions as ledger

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


IngestError = builder.IngestError
YEARS = ledger.YEARS
FIRST_SOURCE_CONGRESS = ledger.FIRST_SOURCE_CONGRESS


def assemble(paths, years=YEARS, progress=None):
    harvested, _scope_receipts = ledger.load_scope(paths)
    crosswalk_indexes = builder.bridge3_crosswalk_indexes()

    congresses = sorted(
        set(range(FIRST_SOURCE_CONGRESS, builder.congress_for_year(max(years)) + 1))
    )
    bill_people_maps = {}
    title_indexes = {}
    prompt_title_items = {}
    for congress in congresses:
        zip_types = (
            builder.HISTORICAL_ZIP_TYPES
            if congress in {115, 116}
            else builder.ZIP_TYPES
        )
        congress_map = builder.load_sponsor_map(
            builder.billstatus_dir_for_congress(paths, congress),
            congress=congress,
            zip_types=zip_types,
        )
        bill_people_maps[congress] = congress_map[0]
        title_indexes[congress] = congress_map[3]
        prompt_title_items.update(
            classifier.load_prompt_title_items(
                builder.billstatus_dir_for_congress(paths, congress),
                congress,
                zip_types,
            )
        )
        if progress:
            progress(f"  titles loaded for Congress {congress}")

    items = []
    seen = set()
    skipped_not_in_billstatus = {}
    per_year = collections.Counter()
    duplicates = 0

    for filing_year in sorted(years):
        congress = builder.congress_for_year(filing_year)
        title_index = title_indexes[congress]
        year_people_maps = {
            source: bill_people_maps[source]
            for source in congresses
            if source <= congress
        }
        for lob_id, filing in ledger._iter_scope_filings(
            paths, filing_year, harvested, crosswalk_indexes
        ):
            produced = classifier.work_items_from_payload(
                filing,
                filing["filing_uuid"],
                lob_id,
                filing_year,
                filing.get("filing_period") or "",
                title_index,
                prompt_title_items,
                year_people_maps,
                skipped_not_in_billstatus=skipped_not_in_billstatus,
            )
            for item in produced:
                # One call per distinct (paragraph, bill) -- the same paragraph
                # cited by many filings is one classification, joined back to
                # every row that shares it.
                content_hash = classifier.work_item_content_hash(item)
                if content_hash in seen:
                    duplicates += 1
                    continue
                seen.add(content_hash)
                items.append(item)
                per_year[filing_year] += 1
        if progress:
            progress(
                f"  {filing_year}: work_items={per_year[filing_year]} "
                f"(cumulative {len(items)}, deduped {duplicates})"
            )

    items.sort(key=classifier.work_item_sort_key)
    receipt = {
        "generated_by": "fec/lobbying/lobby_position_items.py",
        "years": sorted(years),
        "congresses_loaded": congresses,
        "work_items": len(items),
        "duplicate_content_hashes_collapsed": duplicates,
        "work_items_by_year": dict(sorted(per_year.items())),
        "bills_not_in_billstatus": classifier.bill_not_in_billstatus_receipt(
            skipped_not_in_billstatus
        ),
        "coverage_rule": (
            "A work item exists only for a FORMALLY CITED bill. Ledger rows that "
            "reached a member through a title-only match are not classified and "
            "keep position_basis='not_disclosed'."
        ),
    }
    receipt.update(classifier.title_availability_receipt(items))
    return items, receipt


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Assemble full-scope bill-position work items"
    )
    parser.add_argument("--output", default=None)
    parser.add_argument("--report", default=None)
    parser.add_argument("--years", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    paths = builder.default_paths()
    years = (
        tuple(int(value) for value in args.years.split(","))
        if args.years
        else YEARS
    )
    out_dir = os.path.join(paths["derived_dir"], "interactions")
    os.makedirs(out_dir, exist_ok=True)
    output = args.output or os.path.join(out_dir, "position_work_items.jsonl")
    report_path = args.report or os.path.join(
        out_dir, "position_work_items_receipt.json"
    )
    try:
        items, receipt = assemble(
            paths, years=years, progress=lambda line: print(line, flush=True)
        )
    except IngestError as exc:
        print(f"POSITION ITEMS ERROR: {exc}")
        return 2
    classifier.write_jsonl_atomic(output, items)
    classifier.write_json_atomic(report_path, receipt)
    print(
        f"position work items: {receipt['work_items']} "
        f"(collapsed {receipt['duplicate_content_hashes_collapsed']} duplicates) -> {output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
