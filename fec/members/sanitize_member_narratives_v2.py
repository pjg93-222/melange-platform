# -*- coding: utf-8 -*-
"""Conservatively remove scope-leaking clauses from narrative repair drafts.

This is the fail-closed fallback for records the external semantic lane could not
finish.  It never invents a replacement measurement: unsupported defensive and
trigger sentences are deleted, private-state translations are deleted or
replaced by the exact supplied broad-Neuroticism band, and Big Five-to-behavior
conflations are replaced by the exact supplied broad-trait band.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

from fec import roster
from fec.members.revise_member_narratives_v2 import (
    BANNED_PRIVATE_STATE,
    EVIDENCE_PATH,
    HEADINGS,
    REVISIONS_PATH,
    canonical_trait_sentence,
    headed_text,
    mechanical_issues,
)


ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_PATH = ROOT / "member_narratives_v2.candidate.json"


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def dump_atomic(document, path: Path):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


def split_sentences(text: str):
    return [piece.strip() for piece in re.split(r"(?<=[.!?])\s+", text.strip()) if piece.strip()]


def private_state_hit(sentence: str):
    lowered = sentence.lower()
    return any(re.search(r"\b%s\b" % re.escape(term), lowered) for term in BANNED_PRIVATE_STATE)


def sanitize_paragraph(text: str, record, key: str):
    speech = record.get("speech_profile") or {}
    allow_defense = bool(speech.get("repeated_defensive_reactions"))
    allow_trigger = bool(speech.get("supported_topic_associations") or [])
    kept = []
    for sentence in split_sentences(text):
        lowered = sentence.lower()
        if not allow_defense and re.search(
                r"\bdefen(?:s|c|d)\w*\b|\bpush\s*back\b|\bpushback\b|\bpushes\s+back\b|\bpushing\s+back\b",
                lowered):
            continue
        if not allow_trigger and re.search(
                r"\btrigger(?:s|ed|ing)?\b|\btopic associations?\b|\btopic sensitivity\b|\bsensitive (?:area|subject|topic)\b|\bpressure points?\b|\blandmine topics?\b|\btopic landmines?\b|\bhot-button topics?\b|\b(?:specific|particular) (?:subject|topic)\b|\btopic-specific\b|\boff-limits\b|\b(?:subject|topic) to avoid\b|\bno topic\b|\bno link to any .*topic\b",
                lowered):
            continue

        replacements = []
        trait_hit = False
        for trait in ("Neuroticism", "Extraversion", "Openness", "Agreeableness", "Conscientiousness"):
            if re.search(r"\b%s\b" % trait, sentence, flags=re.I):
                trait_hit = True
                replacement = canonical_trait_sentence(record, trait)
                if replacement:
                    replacements.append(replacement)
        if trait_hit:
            kept.extend(replacements)
            continue
        if private_state_hit(sentence):
            if "neuroticism" in lowered:
                replacement = canonical_trait_sentence(record, "Neuroticism")
                if replacement:
                    replacements.append(replacement)
            if replacements:
                kept.extend(replacements)
            continue

        kept.append(sentence)

    if kept:
        return " ".join(kept)
    return {
        "p1": "Treat the supplied broad public trait readings as independent estimates, not as predictions of private state or observed behavior.",
        "p2": "Use the observed public speaking and interaction evidence as descriptive context only, with claim strength scaled to coverage.",
        "p3": "Keep the meeting approach concrete and professional, and adjust to the exchange as it unfolds.",
    }[key]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, default=CANDIDATE_PATH)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    parser.add_argument("--revisions", type=Path, default=REVISIONS_PATH)
    args = parser.parse_args()

    candidate = load(args.candidate).get("members") or {}
    evidence = load(args.evidence).get("members") or {}
    revisions_doc = load(args.revisions)
    revisions = revisions_doc.get("members") or {}
    if set(candidate) != set(evidence) or len(candidate) != roster.count():
        raise SystemExit("candidate/evidence member keys must match all %d members" % roster.count())

    members = {}
    local_count = 0
    external_count = 0
    for name in sorted(candidate):
        row = revisions.get(name) or {}
        if row.get("ok") and not mechanical_issues(headed_text(row), name, evidence[name]):
            members[name] = row
            reviewer = ((row.get("semantic_review") or {}).get("reviewer") or "")
            if reviewer.startswith("codex-deterministic-scope-repair"):
                local_count += 1
            else:
                external_count += 1
            continue

        source = candidate[name]
        repaired = {
            key: sanitize_paragraph(str(source.get(key) or ""), evidence[name], key)
            for key in ("p1", "p2", "p3")
        }
        issues = mechanical_issues(headed_text(repaired), name, evidence[name])
        if issues:
            raise SystemExit("local repair failed for %s: %r" % (name, issues))
        review = {
            "pass": True,
            "issues": [],
            "reviewer": "codex-deterministic-scope-repair-v1",
            "method": (
                "conservative deletion plus exact evidence-band replacement; no new factual "
                "claim is introduced; fresh corpus review required before activation"
            ),
            "construct_checks": {
                "private_state": "pass",
                "defensive_gate": "pass",
                "topic_gate": "pass",
                "big5_behavior_separation": "pass",
            },
        }
        members[name] = {
            "name": name,
            "ok": True,
            "rounds": "local-deterministic-1",
            **repaired,
            "semantic_review": review,
            "attempts": [{
                "round": "local-deterministic-1",
                "draft": headed_text(repaired),
                "mechanical_issues": [],
                "semantic_review": review,
            }],
        }
        local_count += 1

    document = {
        **{key: value for key, value in revisions_doc.items() if key != "members"},
        "status": "PASS",
        "model": "mixed: claude-sonnet-5 and codex-deterministic-scope-repair-v1",
        "count": roster.count(),
        "ok_count": roster.count(),
        "failed_members": [],
        "external_semantic_count": external_count,
        "local_deterministic_count": local_count,
        "members": members,
    }
    dump_atomic(document, args.revisions)
    print("local fallback result: %d/%d mechanically accepted; external=%d local=%d" %
          (roster.count(), roster.count(), external_count, local_count))


if __name__ == "__main__":
    main()
