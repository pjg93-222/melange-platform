# -*- coding: utf-8 -*-
"""Condense over-length member approach briefs through Anthropic Messages.

Paul's instruction (verbatim): approach briefs "should be 240-300 words, if
less don't adjust, again paint a narrative." As of the 2026-09-01 semantic
repair, 455 of the 533 installed briefs exceed 300 words. This script
condenses only those over-length briefs; briefs already at or under 300
words are left untouched.

The workflow mirrors ``revise_member_narratives_v2.py``'s ``generate``
command as closely as the condensing task allows, and reuses that module's
constants, evidence/JSONL helpers, and reviewer call directly rather than
copying them.

``--dry-run``
    Read-only/no-key report of the target count and word-count distribution.
``(default)``
    Run condense rounds for every target (or ``--names``) and append one
    record per name to ``condense_progress.jsonl``. This never touches
    ``member_narratives_v2_revisions.json``.
``--finalize``
    Back up the existing revisions file, then fold every ``ok`` condensed
    record from ``condense_progress.jsonl`` into a fresh
    ``member_narratives_v2_revisions.json``, replacing only the rows that
    condensed cleanly. Members without a clean condensed record keep their
    existing (longer) row untouched.

An API key is read only from ``ANTHROPIC_API_KEY`` or an explicit
``--api-key-file``. It is never accepted on the command line, printed, or
written to logs.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from fec.members.revise_member_narratives_v2 import (
    ARTIFACT_PATH,
    EVIDENCE_PATH,
    HEADINGS,
    REVISIONS_PATH,
    WORK_DIR,
    ApiClient,
    append_jsonl,
    coop_band,
    cross_party_hits,
    directive,
    dump_atomic,
    headed_text,
    load,
    mechanical_issues,
    read_key,
    read_latest_jsonl,
    review_draft,
    strip_heading,
    trim_nulls,
)


CONDENSE_PROMPT_PATH = Path(__file__).with_name("member_narratives_v2_condense_prompt.md")
REVIEW_PROMPT_PATH = Path(__file__).with_name("member_narratives_v2_review_prompt.md")
MODEL = "claude-sonnet-5"


def stripped_body(row) -> str:
    return " ".join(str(row.get(key) or "") for key in ("p1", "p2", "p3"))


def word_count(text: str) -> int:
    return len(text.split())


def row_word_count(row) -> int:
    return word_count(stripped_body(row))


def draft_word_count(draft: str) -> int:
    text = draft
    for heading in HEADINGS:
        text = text.replace("**%s**" % heading, "")
    return word_count(text)


def duplicate_sentence_issues(draft: str):
    issues = []
    seen = set()
    for sentence in re.split(r"(?<=[.!?])\s+", draft.strip()):
        normalized = " ".join(sentence.strip().lower().split())
        if not normalized:
            continue
        if normalized in seen:
            issues.append("repeated sentence within the brief: %s" % normalized[:80])
        seen.add(normalized)
    return sorted(set(issues))


def build_targets(revisions_members, evidence, args):
    if args.names:
        missing = [name for name in args.names if name not in revisions_members or name not in evidence]
        if missing:
            raise SystemExit("unknown member name(s): %r" % missing)
        targets = list(args.names)
    elif args.all:
        targets = sorted(name for name in revisions_members if name in evidence)
    else:
        targets = sorted(
            name for name, row in revisions_members.items()
            if name in evidence and row_word_count(row) > args.max_words
        )
    if args.limit is not None:
        targets = targets[: args.limit]
    return targets


def repair_feedback(attempt):
    if not attempt or not attempt.get("draft"):
        return ""
    defects = list(attempt.get("mechanical_issues") or [])
    semantic = attempt.get("semantic_review") or {}
    if semantic.get("pass") is False:
        defects.extend(
            "%s: %s" % (issue.get("quote") or issue.get("kind"), issue.get("reason"))
            for issue in semantic.get("issues") or []
        )
    if not defects:
        return ""
    return (
        "\n\nPRIOR DRAFT TO REPAIR\n"
        + attempt["draft"]
        + "\n\nPRIOR DRAFT DEFECTS\nRevise that draft conservatively, fix every item, stay within "
          "the required word count, and return the complete three-paragraph replacement:\n"
        + "\n".join("- " + defect for defect in defects)
    )


def condense_one(args, client, writer_system, review_system, name, evidence_record, current_row):
    current_draft = headed_text(current_row)
    ledger = json.dumps(trim_nulls(evidence_record), ensure_ascii=False, indent=1)
    feedback = ""
    attempts = []
    for round_number in range(1, args.max_rounds + 1):
        request = (
            "Condense the three-paragraph approach brief below into %d to %d words total while "
            "preserving every claim, band, and scope qualifier. Use singular they/them pronouns. "
            "The name is supplied only to identify the row and must not appear in the prose.\n\n"
            "MEMBER: %s (%s)\n\nMEMBER-SPECIFIC DIRECTIVE\n%s\n\nEVIDENCE LEDGER\n%s\n\n"
            "CURRENT BRIEF TO CONDENSE\n%s%s"
            % (args.min_words, args.max_words, name, evidence_record.get("chamber") or "",
               directive(evidence_record), ledger, current_draft, feedback)
        )
        draft = client.call(system=writer_system, user=request, purpose="condense", member=name,
                            round_number=round_number, max_tokens=1600)
        mechanical = list(mechanical_issues(draft, name, evidence_record))
        words = draft_word_count(draft)
        if not (args.min_words <= words <= args.max_words):
            mechanical.append(
                "word count %d is outside the required [%d,%d] range" % (words, args.min_words, args.max_words)
            )
        mechanical.extend(duplicate_sentence_issues(draft))
        mechanical = sorted(set(mechanical))
        semantic = None
        if not mechanical:
            semantic = review_draft(client, review_system, name, evidence_record, draft, round_number)
        attempt = {"round": round_number, "draft": draft, "mechanical_issues": mechanical,
                   "semantic_review": semantic}
        attempts.append(attempt)
        if not mechanical and semantic and semantic.get("pass") is True:
            paragraphs = [paragraph.strip() for paragraph in draft.splitlines() if paragraph.strip()]
            semantic = dict(semantic)
            semantic["reviewer"] = MODEL
            return {
                "name": name, "ok": True, "rounds": "condense-%d" % round_number,
                "p1": strip_heading(paragraphs[0], HEADINGS[0]),
                "p2": strip_heading(paragraphs[1], HEADINGS[1]),
                "p3": strip_heading(paragraphs[2], HEADINGS[2]),
                "cross_party_covered": coop_band(evidence_record) is not None,
                "cross_party_label": (coop_band(evidence_record) or {}).get("label"),
                "semantic_review": semantic, "attempts": attempts, "word_count": words,
            }
        feedback = repair_feedback(attempt)
    return {"name": name, "ok": False, "rounds": args.max_rounds, "attempts": attempts,
            "defects": ["exhausted condense rounds"]}


def run_condense(args, client, evidence, revisions_members, writer_system, review_system, targets):
    progress_path = WORK_DIR / "condense_progress.jsonl"
    progress_lock = threading.Lock()
    existing = read_latest_jsonl(progress_path) if args.resume else {}
    todo = [name for name in targets if not (existing.get(name) or {}).get("ok")]
    print("condense: %d targets, %d already ok (resumed), %d to run" %
          (len(targets), len(targets) - len(todo), len(todo)), flush=True)

    def work(name):
        try:
            result = condense_one(args, client, writer_system, review_system, name,
                                   evidence[name], revisions_members[name])
        except Exception as error:  # noqa: BLE001
            result = {"name": name, "ok": False, "error": repr(error)}
        append_jsonl(progress_path, result, progress_lock)
        return result

    completed = 0
    if todo:
        # Run the first target synchronously as a canary: a bad key, a malformed
        # prompt, or a contract mismatch should surface once before spending a
        # full worker pool's worth of calls on the same defect.
        canary_name = todo[0]
        result = work(canary_name)
        completed += 1
        print("[condense %3d/%3d] %-4s %s rounds=%s" %
              (completed, len(todo), "ok" if result.get("ok") else "FAIL", result["name"],
               result.get("rounds", "-")), flush=True)
        remaining = todo[1:]
        if remaining:
            with ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {executor.submit(work, name): name for name in remaining}
                for future in as_completed(futures):
                    result = future.result()
                    completed += 1
                    print("[condense %3d/%3d] %-4s %s rounds=%s" %
                          (completed, len(todo), "ok" if result.get("ok") else "FAIL", result["name"],
                           result.get("rounds", "-")), flush=True)

    latest = read_latest_jsonl(progress_path)
    ok_count = sum(1 for name in targets if (latest.get(name) or {}).get("ok"))
    print("condense result: %d/%d accepted -> %s" % (ok_count, len(targets), progress_path))
    return progress_path


def finalize(args, evidence, revisions_doc, progress_path):
    revisions_members = dict(revisions_doc.get("members") or {})
    latest = read_latest_jsonl(progress_path)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = WORK_DIR / ("member_narratives_v2_revisions.pre_condense_%s.json" % stamp)
    dump_atomic(revisions_doc, backup_path)

    condensed = []
    for name, result in sorted(latest.items()):
        if not result.get("ok"):
            continue
        candidate_row = {"name": name, "ok": True, "p1": result["p1"], "p2": result["p2"], "p3": result["p3"]}
        issues = mechanical_issues(headed_text(candidate_row), name, evidence[name])
        words = row_word_count(candidate_row)
        if coop_band(evidence[name]) is None and cross_party_hits(stripped_body(candidate_row)):
            issues = issues + ["cross-party subject appears although the measure is absent"]
        if issues or not (args.min_words <= words <= args.max_words):
            print("finalize: skipping %s, failed re-validation at merge time: %r" % (name, issues or words))
            continue
        semantic = result.get("semantic_review") or {}
        if semantic.get("pass") is not True or semantic.get("issues"):
            print("finalize: skipping %s, semantic review is not a clean pass" % name)
            continue
        revisions_members[name] = {
            "name": name,
            "ok": True,
            "rounds": result.get("rounds"),
            "p1": result["p1"], "p2": result["p2"], "p3": result["p3"],
            "semantic_review": semantic,
            "attempts": result.get("attempts"),
        }
        condensed.append(name)

    document = {
        **{key: value for key, value in revisions_doc.items() if key != "members"},
        "count": len(revisions_members),
        "ok_count": sum(1 for row in revisions_members.values() if row.get("ok")),
        "members": revisions_members,
        "post_pass_condense": {
            "date": datetime.now(timezone.utc).date().isoformat(),
            "rule": "approach briefs condensed to 240-300 words where the prior brief exceeded 300; "
                    "briefs at or under 300 words are left unadjusted",
            "members_condensed": len(condensed),
            "targets": len(latest),
            "min_words": args.min_words,
            "max_words": args.max_words,
        },
    }
    dump_atomic(document, REVISIONS_PATH)
    print("finalize: backed up prior revisions to %s" % backup_path)
    print("finalize: condensed %d/%d target(s); wrote %s" % (len(condensed), len(latest), REVISIONS_PATH))
    return document


def print_dry_run(revisions_members, evidence, args, targets):
    all_counts = sorted(row_word_count(row) for name, row in revisions_members.items() if name in evidence)
    target_counts = sorted(row_word_count(revisions_members[name]) for name in targets)
    selection = "all members" if args.all else "word count > %d" % args.max_words
    print("condense dry-run: %d members total, %d targets (%s)" %
          (len(all_counts), len(targets), selection))
    if all_counts:
        print("all members: min=%d max=%d mean=%.1f median=%.1f" %
              (min(all_counts), max(all_counts), statistics.mean(all_counts), statistics.median(all_counts)))
    if target_counts:
        print("targets: min=%d max=%d mean=%.1f median=%.1f" %
              (min(target_counts), max(target_counts), statistics.mean(target_counts),
               statistics.median(target_counts)))
        bucket_width = 50
        floor = (min(target_counts) // bucket_width) * bucket_width
        ceiling = (max(target_counts) // bucket_width + 1) * bucket_width
        for lo in range(floor, ceiling, bucket_width):
            hi = lo + bucket_width
            count = sum(1 for value in target_counts if lo <= value < hi)
            if count:
                print("  %d-%d: %d" % (lo, hi - 1, count))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--min-words", type=int, default=240)
    parser.add_argument("--max-words", type=int, default=300)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--names", nargs="+", default=None)
    parser.add_argument("--all", action="store_true",
                        help="target every member regardless of word count")
    parser.add_argument("--writer-prompt", type=Path, default=CONDENSE_PROMPT_PATH,
                        help="writer system prompt file (default: %(default)s)")
    parser.add_argument("--review-prompt", type=Path, default=REVIEW_PROMPT_PATH,
                        help="review system prompt file (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--finalize", action="store_true",
                        help="fold condense_progress.jsonl into member_narratives_v2_revisions.json "
                             "instead of running condense rounds")
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("--workers must be between 1 and 16")
    if args.min_words >= args.max_words:
        parser.error("--min-words must be less than --max-words")

    evidence_doc = load(EVIDENCE_PATH)
    artifact_doc = load(ARTIFACT_PATH)
    revisions_doc = load(REVISIONS_PATH)
    evidence = evidence_doc.get("members") or {}
    artifact_members = artifact_doc.get("members") or {}
    revisions_members = revisions_doc.get("members") or {}
    if set(evidence) != set(revisions_members):
        raise SystemExit("evidence ledger and revisions.json member keys do not match")
    if set(evidence) != set(artifact_members):
        print("warning: installed artifact member keys differ from evidence ledger; "
              "targets are computed from member_narratives_v2_revisions.json regardless")

    targets = build_targets(revisions_members, evidence, args)

    if args.dry_run:
        print_dry_run(revisions_members, evidence, args, targets)
        return 0

    progress_path = WORK_DIR / "condense_progress.jsonl"
    if args.finalize:
        finalize(args, evidence, revisions_doc, progress_path)
        return 0

    key = read_key(args)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    client = ApiClient(key, MODEL, WORK_DIR / "api_usage.jsonl")
    writer_system = args.writer_prompt.read_text(encoding="utf-8")
    review_system = args.review_prompt.read_text(encoding="utf-8")
    run_condense(args, client, evidence, revisions_members, writer_system, review_system, targets)
    print("API usage: " + json.dumps(dict(client.stats), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
