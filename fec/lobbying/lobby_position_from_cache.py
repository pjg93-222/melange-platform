# -*- coding: utf-8 -*-
"""Emit classification results from a benchmark cache, in Kim's schema.

The benchmark run classified 4,697 work items per arm and cached every label.
Those are real, paid-for classifications of real filings -- there is no reason to
buy them twice just because they were produced by the benchmark harness rather
than the production runner.

This converts a cache into the same JSONL the production runner writes, so the
downstream join cannot tell the difference and does not need to care.

SCOPE HONESTY.  A cache covers only the benchmark set -- the (organisation, bill)
pairs that overlap Kim's published dataset.  Every other ledger row keeps
``position_basis='not_disclosed'``.  The receipt records exactly which subset was
filled so a reader can never mistake partial coverage for a complete pass.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import json
import os
import sys

from lobbying import classify_bill_positions as classifier
from lobbying import lobby_position_classify as production

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Convert a benchmark cache into classification output"
    )
    parser.add_argument("--variant", default="kim_llm4")
    parser.add_argument("--cache", default=None)
    parser.add_argument("--input", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--report", default=None)
    args = parser.parse_args(argv)

    local = os.environ["LOCALAPPDATA"]
    root = os.path.join(local, "Melange", "lda_rebuild", "derived", "interactions")
    cache_path = args.cache or os.path.join(
        root, f"benchmark_cache_{args.variant}.json"
    )
    input_path = args.input or os.path.join(root, "position_work_items.jsonl")
    output = args.output or os.path.join(root, "bill_positions_kim.jsonl")
    report_path = args.report or os.path.join(root, "bill_positions_kim_report.json")

    with open(cache_path, encoding="utf-8") as handle:
        cache = json.load(handle)
    items = [
        json.loads(line)
        for line in open(input_path, encoding="utf-8")
        if line.strip()
    ]

    rows = []
    five = collections.Counter()
    three = collections.Counter()
    for item in items:
        label = cache.get(classifier.work_item_content_hash(item))
        if not label:
            continue
        collapsed = production.THREE_CLASS.get(label)
        rows.append(
            {
                "lob_id": item["lob_id"],
                "client_name": item["client_name"],
                "bill_key": item["bill_key"],
                "bill_no": item["bill_no"],
                "paragraph": item["paragraph"],
                "report_uuid": item["report_uuid"],
                "filing_year": item["filing_year"],
                "quarter": item["quarter"],
                "label_5_class": label,
                "label_3_class": collapsed,
                "annotation_source": production.ANNOTATION_SOURCE,
                "model": classifier.MODEL,
                "prompt_variant": args.variant,
                "classification_status": "ok",
            }
        )
        five[label] += 1
        three[collapsed] += 1

    temp = f"{output}.tmp.{os.getpid()}"
    with open(temp, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, output)

    n = max(len(rows), 1)
    receipt = {
        "source": "benchmark_cache",
        "cache_file": os.path.basename(cache_path),
        "prompt_variant": args.variant,
        "model": classifier.MODEL,
        "work_items_total": len(items),
        "classified": len(rows),
        "coverage_of_work_items": round(len(rows) / max(len(items), 1), 4),
        "uncovered_stay_not_disclosed": len(items) - len(rows),
        "label_5_class": dict(five.most_common()),
        "label_3_class": dict(three.most_common()),
        "directional_share": round(
            (three.get("Support", 0) + three.get("Oppose", 0)) / n, 4
        ),
        "scope_note": (
            "Covers only the work items that overlap Kim's published dataset. "
            "Every other ledger row keeps position_basis='not_disclosed'."
        ),
    }
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)

    print(f"classified {len(rows):,} of {len(items):,} work items "
          f"({receipt['coverage_of_work_items']:.1%}) from {os.path.basename(cache_path)}")
    print("  five-class :", dict(five.most_common()))
    print("  three-class:", dict(three.most_common()))
    print("  wrote", output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
