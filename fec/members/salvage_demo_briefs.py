# -*- coding: utf-8 -*-
"""Recover validator-clean demographic briefs from the append-only draft log.

This is deliberately offline.  It rechecks every historical draft against the
current deterministic gates and the current 50-brief novelty window, appending a
new accepted progress record only when all gates pass.  An optional JSON object
of ``member name -> complete replacement text`` supports human repairs without
weakening a validator.
"""

from __future__ import annotations

import argparse
import json
import os
import re

from fec.members import gen_demo_briefs as G


def validation_context(name, text, ledger, seen_grams, masks):
    raw = ledger[name]
    sent, _user = G.build_user(raw)
    noun = sent["geography"]["noun"]
    constraints = sent["constraints"]
    want_year = (constraints.get("industry_vintage_year")
                 if constraints.get("industry_vintage_is_current") is False
                 else None)
    defects = G.validate(
        text, sent, G.superlative_budget(raw), G.allowed_figures(sent), seen_grams,
        want_year=want_year,
        forbid_word="district" if noun == "state" else None,
        require_word="state" if noun == "state" else None,
        member_name=name,
        masks=masks,
    )
    return defects, sent, raw


def accepted_record(name, text, ledger, source):
    defects, sent, raw = validation_context(name, text, ledger, set(), ())
    if defects:
        raise ValueError("cannot build accepted record for %s: %s" % (name, defects))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    found = G.figures_in(text, G.figure_name_masks(sent))
    allowed = G.allowed_figures(sent)
    return {
        "ok": True,
        "name": name,
        "rounds": 0,
        "p1": paragraphs[0],
        "p2": paragraphs[1],
        "p3": paragraphs[2],
        "text": text.strip(),
        "words": len(re.findall(r"[A-Za-z0-9$%,.'-]+", text)),
        "figures": [f[0] for f in found if not f[3]],
        "years": [f[0] for f in found if f[3]],
        "figure_sources": {
            f[0]: allowed.get(round(f[1], 6), allowed.get(round(f[2], 6)))
            for f in found
        },
        "superlative_budget": G.superlative_budget(raw),
        "attempts": [],
        "offline_recovery": source,
    }


def valid_artifact_seeds(path, ledger):
    seeds = []
    for record in G.artifact_records(path):
        defects, _sent, _raw = validation_context(
            record["name"], record["text"], ledger, set(), ())
        if not defects:
            seeds.append(record)
    return seeds


def historical_candidates(rows, names):
    candidates = {name: [] for name in names}
    seen = {name: set() for name in names}
    for row in reversed(rows):
        name = row.get("name")
        if name not in candidates or row.get("ok"):
            continue
        texts = [row.get("text")]
        texts.extend(a.get("text") for a in reversed(row.get("attempts") or []))
        for text in texts:
            if text and text not in seen[name]:
                seen[name].add(text)
                candidates[name].append(text.strip())
    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--artifact", required=True)
    ap.add_argument("--progress", required=True)
    ap.add_argument("--repairs", default=None,
                    help="optional JSON object mapping member names to full brief text")
    args = ap.parse_args()

    ledger = json.load(open(args.ledger, encoding="utf-8"))["members"]
    names = list(ledger)
    seeds = valid_artifact_seeds(args.artifact, ledger)
    rows = list(G.iter_progress(args.progress))
    accepted = {record["name"]: record for record in seeds}
    for record in rows:
        if record.get("ok"):
            accepted[record["name"]] = record

    candidates = historical_candidates(rows, names)
    repairs = {}
    if args.repairs:
        repairs_document = json.load(open(args.repairs, encoding="utf-8"))
        repairs = repairs_document.get("repairs", repairs_document)
        if not isinstance(repairs, dict):
            raise SystemExit("repairs file must be a JSON object or contain a repairs object")
        unknown = sorted(set(repairs) - set(ledger))
        if unknown:
            raise SystemExit("repair names are outside the ledger: %s" % unknown)

    masks = G.ledger_phrases(ledger)
    window, gram_counts = G.rebuild_novelty_window(
        args.progress, masks, seed_records=seeds)
    writer = G.ProgressWriter(args.progress)
    recovered = []

    def try_accept(name, text, source):
        defects, _sent, _raw = validation_context(
            name, text, ledger, set(gram_counts), masks)
        if defects:
            return False, defects
        record = accepted_record(name, text, ledger, source)
        writer.append(record)
        accepted[name] = record
        G.window_add(window, gram_counts, G.brief_grams(text, masks))
        recovered.append((name, source))
        return True, []

    pending_repairs = {name: text for name, text in repairs.items()
                       if name not in accepted}
    while True:
        added_this_cycle = 0
        for name in names:
            if name in accepted:
                continue
            for text in candidates[name]:
                ok, _defects = try_accept(name, text, "historical_draft")
                if ok:
                    added_this_cycle += 1
                    break
        for name in list(pending_repairs):
            if name in accepted:
                del pending_repairs[name]
                continue
            ok, _defects = try_accept(name, pending_repairs[name], "human_repair")
            if ok:
                added_this_cycle += 1
                del pending_repairs[name]
        if not added_this_cycle:
            break

    writer.close()
    for name, source in recovered:
        print("  %-26s OK  %s" % (name, source))
    for name, text in pending_repairs.items():
        defects, _sent, _raw = validation_context(
            name, text, ledger, set(gram_counts), masks)
        print("  %-26s REPAIR-FAIL %s" % (name, str(defects)[:400]))
    print(json.dumps({
        "accepted": len(accepted),
        "recovered": len(recovered),
        "remaining": len(names) - len(accepted),
        "pending_repairs": len(pending_repairs),
    }, indent=1))


if __name__ == "__main__":
    main()
