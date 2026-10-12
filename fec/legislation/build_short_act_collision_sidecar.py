#!/usr/bin/env python3
"""Flag the stemmed short-act alias collisions the ledger's own guard misses.

The review-ledger builder rolls a non-dispositive title-alias probe hit up onto
its parent SCORING category and drops the probe edge. That is deliberate and
lossless -- ORPHAN_EDGE_CLASSIFICATION_2026-07-27.md proves the arithmetic
closes exactly and that no discovery gap exists.

The defect is what survives the roll-up. congress.gov phrase search stems, so
("AFFORD Act") matches "Affordability Act". The ledger's guard
`short_act_title_requires_independent_evidence` only fires when the alias
LITERAL appears in the title, so it caught 109 of 454 and is silent on 345.
Those 345 land at review_priority 9 with likely_false_positive_reason null and
their probe provenance stripped -- indistinguishable from an ordinary
unresolved membership, even though the title already on the row shows what they
are: Health Care Affordability Act filed under CDFI Fund, Refuge From Cruel
Trapping Act under Fraud Prevention.

This writes a SIDECAR rather than touching membership_review_ledger.ndjson. The
ledger is immutable under the culling contract, and a 200 MB append-only
artifact is not something to rewrite to fix a labelling gap. Any review must
join this sidecar on (canonical_key, query_id) and honour the raised priority.

Read-only over its inputs. Writes one NDJSON sidecar and one JSON summary.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import pathlib
from collections import Counter
from datetime import datetime, timezone

SCHEMA = "acu-short-act-collision-sidecar-v1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classification", required=True, type=pathlib.Path)
    ap.add_argument("--out-ndjson", required=True, type=pathlib.Path)
    ap.add_argument("--out-summary", required=True, type=pathlib.Path)
    args = ap.parse_args()

    rows, header = [], None
    with open(args.classification, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if header is None and rec.get("schema"):
                header = rec
                continue
            rows.append(rec)

    def guard_fired(r: dict) -> bool:
        v = r.get("short_act_guard_fired")
        if isinstance(v, bool):
            return v
        return bool(r.get("matched_short_act_title_aliases"))

    out, stats = [], Counter()
    for r in rows:
        key = r.get("canonical_key")
        qid = r.get("query_id") or r.get("ledger_query_id") or r.get("parent_category_id")
        fired = guard_fired(r)
        stats["total"] += 1
        stats["guard_fired" if fired else "guard_silent"] += 1
        if fired:
            continue  # already surfaced at priority 2 with a stated reason
        stats[f"cat_{qid}"] += 1
        out.append({
            "schema": SCHEMA,
            "canonical_key": key,
            "query_id": qid,
            "review_bucket_override": "ambiguous_short_act_stemmed_collision",
            "review_priority_override": 2,
            "likely_false_positive_reason":
                "rolled up from a non-dispositive title-alias probe via STEMMED phrase "
                "match, so the alias literal never appears in the title and the ledger's "
                "short-act guard did not fire",
            "source_title_alias_probe_id": (r.get("supplying_title_alias_probes") or [None])[0],
            "probe_alias": (r.get("supplying_probe_aliases") or [None])[0],
            "membership_non_dispositive_true_source": True,
            "title": r.get("measure_title"),
            "ledger_review_priority_was": r.get("ledger_review_priority"),
            "ledger_review_bucket_was": r.get("ledger_review_bucket"),
            "provenance_note":
                "ledger row on disk carries record_kind=category, scoring_category=true and "
                "empty source_title_alias_probe_ids; this sidecar restores the provenance the "
                "on-disk vintage stripped. Join on (canonical_key, query_id).",
            "human_ratified": False,
        })

    args.out_ndjson.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_ndjson, "w", encoding="utf-8", newline="\n") as sink:
        for o in out:
            sink.write(json.dumps(o, ensure_ascii=False) + "\n")

    body = args.out_ndjson.read_bytes()
    summary = {
        "schema": SCHEMA + "-summary",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_classification": str(args.classification),
        "source_sha256": hashlib.sha256(args.classification.read_bytes()).hexdigest(),
        "orphans_examined": stats["total"],
        "guard_already_fired": stats["guard_fired"],
        "guard_silent_flagged_here": stats["guard_silent"],
        "by_category": {k[4:]: v for k, v in stats.items() if k.startswith("cat_")},
        "sidecar_rows": len(out),
        "sidecar_sha256": hashlib.sha256(body).hexdigest(),
        "ledger_mutated": False,
        "note": "Additive sidecar. membership_review_ledger.ndjson is immutable and untouched.",
    }
    args.out_summary.write_text(json.dumps(summary, indent=1), encoding="utf-8", newline="\n")

    print(f"orphans examined     : {stats['total']}")
    print(f"  guard already fired: {stats['guard_fired']}")
    print(f"  GUARD SILENT       : {stats['guard_silent']}  <- flagged here")
    print(f"sidecar rows         : {len(out)}")
    print(f"by category          : {summary['by_category']}")
    print(f"ledger mutated       : False")
    print(f"wrote                : {args.out_ndjson}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
