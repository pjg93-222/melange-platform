# -*- coding: utf-8 -*-
"""Publish a complete, slim demographic-brief artifact from generated records."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os

from fec.members import gen_demo_briefs as G


def validate_individual(name, text, raw):
    sent, _user = G.build_user(raw)
    noun = sent["geography"]["noun"]
    constraints = sent["constraints"]
    want_year = (constraints.get("industry_vintage_year")
                 if constraints.get("industry_vintage_is_current") is False
                 else None)
    return G.validate(
        text, sent, G.superlative_budget(raw), G.allowed_figures(sent), set(),
        want_year=want_year,
        forbid_word="district" if noun == "state" else None,
        require_word="state" if noun == "state" else None,
        member_name=name,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--generated", required=True)
    ap.add_argument("--evidence", required=True)
    ap.add_argument("--template", required=True)
    ap.add_argument("--repairs", default=None,
                    help="optional canonical repair ledger used for offline recovery")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    generated = json.load(open(args.generated, encoding="utf-8"))
    evidence = json.load(open(args.evidence, encoding="utf-8"))
    template = json.load(open(args.template, encoding="utf-8"))
    ledger = evidence["members"]
    records = generated.get("briefs") or {}

    missing = [name for name in ledger if name not in records]
    extras = [name for name in records if name not in ledger]
    failed = [name for name, record in records.items() if not record.get("ok")]
    if missing or extras or failed:
        raise SystemExit("cannot publish incomplete corpus: missing=%d extras=%d failed=%d"
                         % (len(missing), len(extras), len(failed)))

    invalid = []
    slim = {}
    human_repairs = 0
    historical_recoveries = 0
    phrase_count = 0
    for name in ledger:
        record = records[name]
        text = record.get("text") or "\n\n".join(
            record.get("p%d" % i, "") for i in range(1, 4))
        defects = validate_individual(name, text, ledger[name])
        if defects:
            invalid.append((name, defects))
            continue
        if record.get("offline_recovery") == "human_repair":
            human_repairs += 1
        elif record.get("offline_recovery") == "historical_draft":
            historical_recoveries += 1
        phrase_count += text.lower().count("leaned heavily toward home purchases")
        slim[name] = {
            "ok": True,
            "p1": record["p1"],
            "p2": record["p2"],
            "p3": record["p3"],
            "words": record["words"],
            "figures": record["figures"],
        }
    if invalid:
        for name, defects in invalid[:20]:
            print(name, defects)
        raise SystemExit("%d record(s) fail current deterministic validation" % len(invalid))

    stale_vintages = sum(
        (record.get("constraints") or {}).get("industry_vintage_is_current") is False
        for record in ledger.values()
    )
    with open(args.evidence, "rb") as fh:
        evidence_sha = hashlib.sha256(fh.read()).hexdigest()

    repair_artifact = None
    if args.repairs:
        repairs_document = json.load(open(args.repairs, encoding="utf-8"))
        repairs = repairs_document.get("repairs")
        if not isinstance(repairs, dict):
            raise SystemExit("repair ledger must contain a repairs object")
        if repairs_document.get("evidence_sha256") != evidence_sha:
            raise SystemExit("repair ledger evidence hash does not match evidence input")
        if len(repairs) != human_repairs:
            raise SystemExit(
                "repair ledger count (%d) does not match generated records (%d)"
                % (len(repairs), human_repairs))
        with open(args.repairs, "rb") as fh:
            repair_sha = hashlib.sha256(fh.read()).hexdigest()
        repair_artifact = {
            "file": os.path.basename(args.repairs),
            "count": len(repairs),
            "sha256": repair_sha,
            "evidence_sha256": evidence_sha,
        }

    document = dict(template)
    publication_status = template.get("status")
    if publication_status not in {
        "CANDIDATE_AWAITING_USER_RATIFICATION", "RATIFIED_FOR_DASHBOARD"
    }:
        publication_status = "CANDIDATE_AWAITING_USER_RATIFICATION"
    document.update({
        "schema": "district-demographic-briefs-v1",
        "status": publication_status,
        "generated": dt.date.today().isoformat(),
        "model": "%s with validator-guided offline recovery"
                 % generated.get("model", "claude-sonnet-5"),
        "evidence_sha256": evidence_sha,
        "counts": {"roster": len(ledger), "accepted": len(slim), "missing": 0},
        "contract": {
            "shape": "three paragraphs: district statistics, credit-union relevance, "
                     "industry specialisation",
            "words": [G.WORD_MIN, G.WORD_MAX],
            "figure_cap": G.FIGURE_CAP,
            "validation": "deterministic word band, figure cap, evidence grounding, ban "
                          "lists, thousands-separator formatting, and acceptance-time "
                          "sliding 50-brief novelty window; validator-guided human repair "
                          "was used after provider limits",
        },
        "known_limitations": [
            "No corpus-wide independent semantic review was run; every brief passed the "
            "current deterministic structural and evidence-grounding gates.",
            "%d briefs were recovered through validator-guided human repair and %d from "
            "earlier provider drafts after generation services reached their limits."
            % (human_repairs, historical_recoveries),
            "No brief asserts formal-learning attainment (no such data exists), the "
            "demographic-potential score, an absolute job total where employment "
            "validation fails, or an apportioned branch/deposit figure as a direct count.",
            "%d members sit on stale industry vintages and their briefs name the year."
            % stale_vintages,
            "The phrase 'leaned heavily toward home purchases' appears in %d briefs."
            % phrase_count,
        ],
        "briefs": slim,
    })
    if repair_artifact:
        document["repair_artifact"] = repair_artifact
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(document, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    print(json.dumps({
        "roster": len(ledger),
        "accepted": len(slim),
        "missing": 0,
        "human_repairs": human_repairs,
        "historical_recoveries": historical_recoveries,
        "stale_vintages": stale_vintages,
        "repeated_phrase_count": phrase_count,
        "output": args.out,
    }, indent=1))


if __name__ == "__main__":
    main()
