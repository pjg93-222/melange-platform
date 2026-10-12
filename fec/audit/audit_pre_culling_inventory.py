#!/usr/bin/env python3
"""Lay of the land before culling: what do we actually hold, per candidate?

Answers one question with evidence rather than counters: for all 18,557
discovered candidates, which of the fields the ACU outline depends on are
present, which are absent, and WHY. Read-only.

Distinguishes three things that are easy to conflate and have been conflated
before in this project:

  HELD          the data is on disk and readable
  CONTRACT-SHORT the data is held but does not satisfy the evidence contract
                 (e.g. summary text present via GovInfo BillStatus, which the
                 contract will not accept as evidence of record)
  ABSENT        genuinely not collected, or officially unavailable upstream

Conflating the middle case with the last one turns a policy decision into a
phantom collection job. That has already happened once.
"""
from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import sys
from collections import Counter

WORKSPACE = pathlib.Path(__file__).resolve().parents[2]
LEGFIX5 = WORKSPACE / "audits" / "legfix5"
PAGES = LEGFIX5 / "congress_advanced_pages_20260725"


def newest_evidence() -> pathlib.Path | None:
    root = LEGFIX5 / "candidate_evidence"
    best = None
    for d in sorted(root.glob("evidence-*")):
        f = d / "candidate_evidence.ndjson"
        if f.is_file() and (best is None or f.stat().st_mtime > best.stat().st_mtime):
            best = f
    return best


def find_summary_text(node):
    """Locate summary prose anywhere in a BillStatus record."""
    if isinstance(node, dict):
        for k, v in node.items():
            if "summar" in k.lower():
                if isinstance(v, str) and v.strip():
                    return v
                if isinstance(v, list):
                    for item in v:
                        if isinstance(item, dict):
                            for c in ("text", "summary_text", "value"):
                                if isinstance(item.get(c), str) and item[c].strip():
                                    return item[c]
                        elif isinstance(item, str) and item.strip():
                            return item
                if isinstance(v, dict):
                    for c in ("text", "summary_text", "value"):
                        if isinstance(v.get(c), str) and v[c].strip():
                            return v[c]
            found = find_summary_text(v)
            if found:
                return found
    elif isinstance(node, list):
        for item in node:
            found = find_summary_text(item)
            if found:
                return found
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", type=pathlib.Path, default=None)
    ap.add_argument("--billstatus", type=pathlib.Path, default=None)
    ap.add_argument("--full-text-receipts", type=pathlib.Path, default=None)
    ap.add_argument("--verify-bodies", action="store_true",
                    help="open each accepted archive (slow, proves the body exists)")
    args = ap.parse_args()

    evidence = args.evidence or newest_evidence()
    if evidence is None or not evidence.is_file():
        print("no candidate_evidence.ndjson found")
        return 2
    print("evidence      : %s" % evidence)
    print("               %d bytes" % evidence.stat().st_size)

    field = Counter()
    total = 0
    keys: list[str] = []
    with evidence.open("r", encoding="utf8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            total += 1
            keys.append(row.get("canonical_key", ""))
            oe = row.get("official_evidence") or {}
            for name in ("title", "summary", "full_text"):
                node = oe.get(name)
                status = node.get("status") if isinstance(node, dict) else "ABSENT"
                field["%s:%s" % (name, status)] += 1
            rel = row.get("relationships") or {}
            field["relationships:%s" % rel.get("status")] += 1
            st = row.get("stage") or {}
            field["stage:%s" % st.get("status")] += 1
            if isinstance(st, dict) and st.get("action_count") is not None:
                field["actions:present"] += 1
            cat = row.get("category_evidence")
            field["category_evidence:%s" % ("populated" if cat else "empty")] += 1

    print("candidates    : %d\n" % total)
    print("=== evidence-contract view ===")
    for k in sorted(field):
        print("   %-52s %6d  (%.1f%%)" % (k, field[k], 100.0 * field[k] / total))

    # What do we actually HOLD, regardless of contract status?
    bs = args.billstatus or (
        LEGFIX5 / "billstatus_bulk_enrichment" / "fetch_runs"
        / "c7ca822fd5951478a33a814d228b71ffdf69745e6d823001fefaba620282c435"
        / "parsed" / "sha256")
    print("\n=== what we HOLD: BillStatus summary prose (full sweep) ===")
    if bs.is_dir():
        files = list(bs.rglob("*.json"))
        held = unreadable = 0
        for f in files:
            try:
                row = json.loads(f.read_text("utf8"))
            except Exception:
                unreadable += 1
                continue
            if find_summary_text(row):
                held += 1
        print("   billstatus records  : %d" % len(files))
        print("   unreadable          : %d" % unreadable)
        print("   HOLD summary prose  : %d  (%.1f%% of billstatus)" % (
            held, 100.0 * held / len(files) if files else 0))
    else:
        print("   billstatus dir not found: %s" % bs)

    # Full-text archives actually on disk in the live generation.
    ft = args.full_text_receipts or (PAGES / "text_relevance_httr_full_20260727z")
    print("\n=== what we HOLD: full-text archives on disk ===")
    rec_dir, html_dir = ft / "receipts", ft / "accepted_html"
    receipts = list(rec_dir.rglob("receipt.json")) if rec_dir.is_dir() else []
    bodies = list(html_dir.rglob("page.html.gz")) if html_dir.is_dir() else []
    print("   generation          : %s" % ft.name)
    print("   receipts            : %d" % len(receipts))
    print("   archived bodies     : %d" % len(bodies))
    print("   consistent          : %s" % (len(receipts) == len(bodies)))
    if args.verify_bodies and bodies:
        ok = bad = 0
        for b in bodies[:500]:
            try:
                with gzip.open(b, "rb") as g:
                    g.read(2048)
                ok += 1
            except OSError:
                bad += 1
        print("   body readable (500) : %d ok / %d unreadable" % (ok, bad))

    print("\n=== the gap that actually blocks culling ===")
    empty_cat = field.get("category_evidence:empty", 0)
    print("   category_evidence empty on %d / %d candidates" % (empty_cat, total))
    print("   -> category_audit_ready = 0 -> semantic_review_ready = 0")
    print("   -> membership_is_finalizable is False corpus-wide, regardless of text")
    return 0


if __name__ == "__main__":
    sys.exit(main())
