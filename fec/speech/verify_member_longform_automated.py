"""Produce explicit machine verifications only for reproducible passing turns.

No human attestations are generated. Non-passing turns remain in a diagnostic
file. Findings are limited to actual response duration; no personality or
persuasion inference is manufactured from the automated acceptance.
"""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from fec.speech import source_media_recovery as SMR
from fec.speech import longform_automated_authority as auto
from fec.speech import materialize_member_longform_reviewed_turns as materializer
from fec.speech import build_member_longform_review_queue as queue
from fec.speech import classify_member_longform_full_turns as physical


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("selection", "candidates", "candidate-receipt", "continuity", "out", "receipt", "diagnostics"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    paths = {name: getattr(args, name) for name in ("selection", "candidates", "candidate_receipt", "continuity")}
    original = {key: SMR.sha256_file(path) for key, path in paths.items()}
    selections = materializer.source_index(SMR.read_jsonl(args.selection, required=True), "selection")
    candidates = materializer.source_index(SMR.read_jsonl(args.candidates, required=True), "candidates")
    continuity = materializer.source_index(SMR.read_jsonl(args.continuity, required=True), "continuity")
    accepted, diagnostics = [], []
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()
    for key, candidate in candidates.items():
        selection, acoustic = selections.get(key) or {}, continuity.get(key) or {}
        try:
            physical.validate_source(selection)
        except Exception as exc:
            diagnostics.append({"member": key[0], "source_id": key[1], "status": "HELD", "reason": str(exc)})
            continue
        for index, turn in enumerate(candidate.get("turns") or []):
            turn_id = f"{queue.slug(key[0])}-{queue.slug(key[1])}-{index + 1:02d}"
            try:
                coverage = queue.qualified_turn_coverage(candidate, acoustic, index, turn)
                if not coverage:
                    raise ValueError("complete_interior_acoustic_check_did_not_pass")
                if not (acoustic.get("anchored_member") is True or acoustic.get("supplemental_member") is True):
                    raise ValueError("cold_start_continuity_is_not_positive_speaker_identity")
                review = {"schema": "member-longform-automated-verification-v1",
                          "member": key[0], "source_id": key[1], "turn_id": turn_id,
                          "start_sec": turn["start_sec"], "end_sec": turn["end_sec"],
                          "wav_sha256": selection["wav_sha256"], "transcript_sha256": selection["transcript_sha256"],
                          "candidate_turn_binding_sha256": coverage["turn_binding_sha256"],
                          "reviewer_type": "AUTOMATED", "reviewer_name": auto.METHOD,
                          "reviewed_at": stamp, "review_attestation": auto.ATTESTATION,
                          "authorization": auto.authorization_binding(),
                          "review_status": "ACCEPT", "identity_review": "ACCEPT", "boundary_review": "ACCEPT", "semantic_review": "ACCEPT",
                          "identity_review_note": "Source-bound named handoff and complete-interior anchored or independent-reference acoustic checks passed; no human listening is asserted.",
                          "boundary_review_note": "Both response endpoints reproduce exact first/last-word agreement from two supplied automated annotation passes; no duration cap or fragment intersection.",
                          "boundary_evidence": {"receipt_path": str(args.candidate_receipt.resolve()),
                                                "receipt_sha256": original["candidate_receipt"]},
                          "semantic_findings": auto.duration_findings(turn)}
                auto.verify_boundaries(review, selection, candidate, turn)
                accepted.append(review)
                diagnostics.append({"turn_id": turn_id, "member": key[0], "source_id": key[1], "status": "AUTOMATED_PASS"})
            except (ValueError, KeyError, OSError) as exc:
                diagnostics.append({"turn_id": turn_id, "member": key[0], "source_id": key[1], "status": "HELD", "reason": str(exc)})
    if any(SMR.sha256_file(path) != original[key] for key, path in paths.items()):
        raise ValueError("verification_input_changed")
    SMR.write_jsonl_atomic(args.out, accepted)
    SMR.write_jsonl_atomic(args.diagnostics, diagnostics)
    counts = {"automated_pass_turns": len(accepted), "members_with_passing_turns": len({r["member"] for r in accepted}),
              "held_rows": sum(r["status"] == "HELD" for r in diagnostics), "human_reviews_performed": 0}
    SMR.write_json_atomic(args.receipt, {"schema": "member-longform-automated-verification-receipt-v1",
        "status": "PASS_AUTOMATED_CHECKS" if accepted else "NO_PASSING_TURNS", "generated_at": stamp,
        "counts": counts, "inputs": {key: {"path": str(path.resolve()), "sha256": original[key]} for key, path in paths.items()},
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
        "contract": {"human_review_required": False, "human_attestations_generated": False,
                     "failed_acoustic_checks_promoted": False, "maximum_turn_seconds": None,
                     "single_source_observation_is_recurring_pattern": False}})
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
