# -*- coding: utf-8 -*-
"""Join classified bill positions back onto the interaction ledger.

The ledger ships ``position=''`` / ``position_basis='not_disclosed'`` because the
LD-2 form has no position field.  This step fills that slot from the Haiku
classifier's output and, crucially, RELABELS THE BASIS so the page can never
present an inference as a disclosure.

Basis vocabulary written here:
  not_disclosed      -- the ledger default; no classification exists for this row
  inferred_<variant> -- a model read the filer's own paragraph and guessed

WHAT THE NUMBERS ACTUALLY SUPPORT.  See ``VALIDATION`` -- benchmarked against
Kim's published end results, not a curated sample.  The five-class label is what
ships, and it is the useful one: 0.82 against his llm-lane annotations, where the
old shipped prompt scored 0.409.  But SUPPORT and OPPOSE remain weak (recall 0.49,
precision 0.42) and AMEND is not recovered at all (0 found against his 23).  So a
"Support" badge is a lead, not a fact, and the absence of "Amend" says nothing.
``ACCURACY_NOTE`` is that sentence and it travels with the data.

The join key is (paragraph, bill) -- the same pair the classifier deduplicated on --
so one classification fills every ledger row that shares that paragraph and bill.
"""

import os as _o, sys as _s; _s.path.insert(0, _o.path.dirname(_o.path.dirname(_o.path.abspath(__file__))))

import argparse
import collections
import csv
import hashlib
import json
import os
import sys

from lobbying import lobby_interactions as ledger

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


IngestError = ledger.IngestError

# BENCHMARKED AGAINST KIM'S PUBLISHED END RESULTS, not against a curated sample.
# 876 (organisation, bill) pairs where his dataset (doi:10.7910/DVN/D0QWM2) and our
# filings overlap; five-class scored on the 335 pairs annotated by his llm lane,
# because his gnn lane emits no Amend or Monitor at all.
#
# Variant kim_llm4 -- his four-way forced choice, Support/Oppose/Amend/Monitor with
# no non-committal escape. The shipped prompt's "Mention" absorbed 59.5% of labels
# and scored 0.409 on five-class; this scores 0.821.
VALIDATION = {
    "variant": "kim_llm4",
    "benchmark": "Kim published end results, 876 overlapping pairs",
    "five_class_accuracy_llm_pairs": 0.8209,
    "five_class_pairs_scored": 335,
    "shipped_prompt_five_class_accuracy": 0.409,
    "three_class_accuracy_all_pairs": 0.8790,
    "all_engage_baseline": 0.8950,
    "support_recall": 0.4941,
    "support_precision": 0.4200,
    "support_count_ours_vs_kim": [42, 42],
    "amend_recall": 0.0,
    "amend_count_ours_vs_kim": [0, 23],
    "accuracy_caveat": (
        "Three-class accuracy is near-useless here: predicting Engage for every "
        "pair scores 0.8950, so the shipped prompt's 0.8961 was +0.001 over "
        "guessing. Selection was on five-class accuracy and on reproducing Kim's "
        "label counts, not on three-class accuracy."
    ),
}
ACCURACY_NOTE = (
    "Positions are INFERRED by a model reading the filer's own description, not "
    "disclosed by the filer. Benchmarked against In Song Kim's published labels on "
    "876 overlapping organisation-bill pairs, this reproduces his Support count "
    "exactly (42 against 42) and scores 0.82 on his five-class scheme. But SUPPORT "
    "and OPPOSE remain weak — recall 0.49 at precision 0.42 — and AMEND is not "
    "recovered at all (0 found against his 23). Treat a Support or Oppose badge as "
    "a lead worth checking against the filing text beside it, never as a fact, and "
    "treat the absence of an Amend label as saying nothing."
)
DIRECTIONAL = ("Support", "Oppose")
# Must cover every label any shipped variant can emit. "Engage" is Kim's own
# fifth class and belongs here: load_classifications treats an unmapped label as
# unusable, so omitting it silently discarded every Engage row rather than
# failing loudly. "Mention" is the legacy shipped-prompt label, kept so an older
# classification file still joins.
THREE_CLASS = {
    "Support": "Support",
    "Oppose": "Oppose",
    "Amend": "Engage",
    "Monitor": "Engage",
    "Engage": "Engage",
    "Mention": "Engage",
}


def _note_id(paragraph):
    """Must match lobbying.lobby_interactions._note_id exactly -- it is the join key."""
    return hashlib.sha256(paragraph.encode("utf-8")).hexdigest()[:16]


def load_classifications(path, variant):
    """(note_id, bill_key) -> label, from the classifier's JSONL output."""
    if not os.path.isfile(path):
        raise IngestError(f"missing classification output {path}")
    labels = {}
    conflicts = 0
    unusable = 0
    schema_misses = 0
    total = 0
    by_label = collections.Counter()
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            total += 1
            row = json.loads(line)
            if (row.get("classification_status") or "") != "ok":
                unusable += 1
                continue
            # Kim's schema names this label_5_class; the legacy shipped
            # classifier called it our_position. Accept either, and treat a row
            # carrying NEITHER as a schema error rather than a silent skip --
            # reading the wrong field name is how this join once reported 0
            # matches against 3,517 perfectly good keys.
            label = (
                row.get("label_5_class") or row.get("our_position") or ""
            ).strip()
            if not label:
                schema_misses += 1
                unusable += 1
                continue
            if label not in THREE_CLASS:
                unusable += 1
                continue
            bill_key = row.get("bill_key") or {}
            canonical = "{}:{}:{}".format(
                bill_key.get("congress"),
                str(bill_key.get("type") or "").lower(),
                bill_key.get("number"),
            )
            key = (_note_id(row.get("paragraph") or ""), canonical)
            previous = labels.get(key)
            if previous is not None and previous != label:
                # The classifier deduplicated on exactly this pair, so a
                # disagreement means the inputs were not what we think.
                conflicts += 1
                continue
            labels[key] = label
            by_label[label] += 1
    if total and schema_misses == total:
        raise IngestError(
            f"{path} has {total} rows and none carry label_5_class or "
            "our_position; this is a schema mismatch, not an empty result"
        )
    return labels, {
        "classification_rows": total,
        "usable": len(labels),
        "unusable": unusable,
        "conflicting_duplicates": conflicts,
        "rows_missing_a_label_field": schema_misses,
        "by_label": dict(sorted(by_label.items())),
        "variant": variant,
    }


def join(ledger_path, labels, variant, out_path=None):
    with open(ledger_path, "r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise IngestError("interaction ledger is empty")
    basis = f"inferred_{variant}"
    filled = 0
    counts = collections.Counter()
    three = collections.Counter()
    for row in rows:
        key = (row["note_id"], row["bill_key"])
        label = labels.get(key)
        if not label:
            # No classification: the row keeps the honest default.
            row["position"] = ""
            row["position_basis"] = ledger.POSITION_NOT_DISCLOSED
            continue
        row["position"] = label
        row["position_basis"] = basis
        filled += 1
        counts[label] += 1
        three[THREE_CLASS[label]] += 1
    out_path = out_path or ledger_path
    ledger._write_csv_atomic(out_path, ledger.LEDGER_FIELDS, rows)
    return {
        "ledger_rows": len(rows),
        "rows_with_an_inferred_position": filled,
        "rows_left_not_disclosed": len(rows) - filled,
        "coverage": round(filled / len(rows), 4) if rows else 0.0,
        "by_label": dict(sorted(counts.items())),
        "by_three_class": dict(sorted(three.items())),
        "directional_rows": sum(counts[label] for label in DIRECTIONAL),
        "position_basis": basis,
        "validation": VALIDATION,
        "accuracy_note": ACCURACY_NOTE,
    }


def _build_parser():
    parser = argparse.ArgumentParser(
        description="Join classified positions onto the interaction ledger"
    )
    parser.add_argument("--ledger", default=None)
    parser.add_argument("--classifications", default=None)
    parser.add_argument("--variant", default="zeroshot")
    parser.add_argument("--out", default=None)
    parser.add_argument("--report", default=None)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        print("POSITION JOIN ERROR: LOCALAPPDATA is not set")
        return 2
    root = os.path.join(local, "Melange", "lda_rebuild", "derived", "interactions")
    ledger_path = args.ledger or os.path.join(root, "interaction_ledger.csv")
    classifications = args.classifications or os.path.join(
        root, "bill_positions.jsonl"
    )
    report_path = args.report or os.path.join(root, "position_join_receipt.json")
    try:
        labels, load_receipt = load_classifications(classifications, args.variant)
        receipt = join(ledger_path, labels, args.variant, out_path=args.out)
    except IngestError as exc:
        print(f"POSITION JOIN ERROR: {exc}")
        return 2
    receipt["classification_input"] = load_receipt
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(receipt, handle, indent=2, sort_keys=True)
    print(
        "position join: "
        f"{receipt['rows_with_an_inferred_position']:,} of {receipt['ledger_rows']:,} "
        f"ledger rows carry an inferred position "
        f"({receipt['coverage']:.1%}); "
        f"{receipt['directional_rows']:,} directional"
    )
    print(f"  labels: {receipt['by_label']}")
    print(f"  three-class: {receipt['by_three_class']}")
    print(f"  wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
