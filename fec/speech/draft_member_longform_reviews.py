#!/usr/bin/env python3
"""Draft conservative review records from the roster-wide review queue.

Drafts make the eventual human decision faster but never satisfy it.  The only
automatic semantic claim is the measured duration of one candidate answer;
no motive, trait, or universal interaction preference is inferred.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re

from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_QUEUE = ROOT / "data/speech/member_longform_review_queue_v1.jsonl"
DEFAULT_OUT = ROOT / "data/speech/member_longform_review_drafts_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_review_drafts_v1.receipt.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def duration_finding(seconds: float) -> dict:
    if seconds >= 120.0:
        kind = "observed_multi_minute_answer"
        finding = f"The proposed answer interval runs {seconds / 60.0:.1f} minutes; its speaker handoffs await review."
    elif seconds >= 60.0:
        kind = "observed_extended_answer"
        finding = f"The proposed answer interval runs {seconds:.0f} seconds; its speaker handoffs await review."
    else:
        kind = "observed_subminute_answer"
        finding = f"The proposed answer interval runs {seconds:.0f} seconds."
    return {
        "kind": kind,
        "finding": finding,
        "scope": "One public long-form interview turn; a measured example, not a universal private preference.",
    }


def normalized_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").casefold())


def phrase_present(text: str, phrases: tuple[str, ...]) -> bool:
    compact = " ".join(normalized_words(text))
    return any(f" {phrase} " in f" {compact} " for phrase in phrases)


def structured_findings(item: dict) -> list[dict]:
    """Draft narrow transcript-visible descriptions, without meeting advice.

    These rules intentionally use only surface order and measured duration.
    They do not infer motives or claim stable preferences, and the resulting
    rows retain automated-draft status until a human checks the audio.
    """
    before = str(item.get("preceding_context") or "")
    answer = str(item.get("turn_transcript") or "")
    seconds = float(item.get("duration_seconds") or 0.0)
    answer_words = normalized_words(answer)
    opening = " ".join(answer_words[:60])
    later = " ".join(answer_words[60:])
    findings: list[dict] = []

    personal_prompt = phrase_present(before, (
        "how are you", "your health", "your family", "your story", "grew up",
        "personal", "tell me about yourself", "what happened to you",
    ))
    if personal_prompt and seconds >= 45.0:
        findings.append({
            "kind": "personal_checkin_before_substantive_answer_candidate",
            "finding": (
                f"The context immediately before this {seconds:.0f}-second answer contains a personal check-in "
                "or biographical prompt followed by an extended candidate response."
            ),
            "scope": (
                "Automated transcript-pattern candidate from one public interview turn; human review must confirm "
                "the speakers, prompt, response, and who initiated the personal topic."
            ),
        })

    principle_open = phrase_present(opening, (
        "i believe", "we believe", "the principle", "fundamentally", "the important thing",
        "what matters", "first of all", "at the end of the day",
    ))
    operational_later = phrase_present(later, (
        "we need to", "we have to", "we can't", "we cannot", "the limit", "the problem",
        "the next step", "the way to", "in practice", "but", "however",
    ))
    if principle_open and operational_later and seconds >= 45.0:
        findings.append({
            "kind": "principle_then_operational_detail_candidate",
            "finding": (
                "This answer uses a principle or framing marker in its first 60 words and moves later to an "
                "operational, limiting, or implementation marker."
            ),
            "scope": (
                "Automated discourse-order candidate from one public interview turn; human review must confirm "
                "that the markers perform those functions in context."
            ),
        })

    example_early = phrase_present(opening, (
        "for example", "for instance", "let me give", "i remember", "when i was",
        "in my district", "back home", "one case", "one person",
    ))
    if example_early:
        findings.append({
            "kind": "early_concrete_example_candidate",
            "finding": "A concrete-example or personal-example marker appears within the first 60 words of the answer.",
            "scope": (
                "Automated transcript-pattern candidate from one public interview turn; human review must confirm "
                "that the passage is actually functioning as an example."
            ),
        })

    return findings[:2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()

    rows = []
    for item in SMR.read_jsonl(args.queue, required=True):
        if item.get("status") != "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED":
            continue
        seconds = float(item["duration_seconds"])
        identity_basis = (
            "literal named handoff plus all-vault calibrated continuity"
            if item.get("anchored_member") else
            "literal named handoff plus independent reference continuity"
            if item.get("supplemental_member") else
            "literal named handoff plus cold-start rejection/within-recording continuity"
        )
        findings = [duration_finding(seconds), *structured_findings(item)]
        rows.append({
            "schema": "member-longform-review-draft-v1",
            "turn_id": item["turn_id"],
            "member": item["member"],
            "source_id": item["source_id"],
            "source_era": item.get("source_era"),
            "start_sec": item["start_sec"],
            "end_sec": item["end_sec"],
            "wav_sha256": item.get("wav_sha256"),
            "transcript_sha256": item.get("transcript_sha256"),
            "candidate_turn_binding_sha256": (item.get("turn_coverage") or {}).get("turn_binding_sha256"),
            "review_status": "PENDING_HUMAN",
            "reviewer_type": "AUTOMATED_DRAFT_NOT_AUTHORITY",
            "identity_review": "PENDING",
            "identity_review_note": f"Confirm {identity_basis}; cue(s): " + "; ".join(
                str(cue.get("phrase") or "") for cue in (item.get("literal_name_cues") or [])[:3]
            ),
            "boundary_review": "PENDING",
            "boundary_review_note": str(item.get("candidate_handoff_basis") or ""),
            "semantic_review": "PENDING",
            "semantic_findings": findings,
            "preceding_context": item.get("preceding_context"),
            "turn_transcript": item.get("turn_transcript"),
            "following_context": item.get("following_context"),
            "generated_at": utc_now(),
            "semantic_draft_method": "descriptive-duration-and-transcript-patterns-v3",
            "profile_effect": "NONE_UNTIL_HUMAN_ACCEPTANCE_AND_MATERIALIZATION",
        })
    SMR.write_jsonl_atomic(args.out, rows)
    receipt = {
        "schema": "member-longform-review-draft-receipt-v1",
        "status": "PASS_DRAFTS_ONLY",
        "generated_at": utc_now(),
        "counts": {
            "drafts": len(rows),
            "members": len({row["member"] for row in rows}),
            "semantic_findings": sum(len(row["semantic_findings"]) for row in rows),
            "structured_finding_candidates": sum(
                max(0, len(row["semantic_findings"]) - 1) for row in rows
            ),
        },
        "contract": {
            "automated_draft_is_human_review": False,
            "draft_has_profile_effect": False,
            "duration_is_measured_not_inferred": True,
            "structured_findings_are_surface_pattern_candidates": True,
            "structured_findings_require_human_context_review": True,
            "universal_private_preference_claims": False,
            "personalized_meeting_or_persuasion_advice_generated": False,
        },
        "inputs": {"review_queue_sha256": SMR.sha256_file(args.queue)},
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
