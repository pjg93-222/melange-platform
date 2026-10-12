"""User-authorized machine evidence, explicitly distinct from human review.

This initial automatic route accepts exact two-pass response boundaries plus
the unchanged complete-interior acoustic gate. It never turns a draft label,
an unmatched candidate, or an acoustic failure into an accepted response.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from fec.speech import source_media_recovery as SMR
from fec.speech import reconcile_member_longform_candidate_passes as reconciler

ROOT = Path(__file__).resolve().parents[2]
AUTHORIZATION = ROOT / "data/speech/member_longform_automated_authorization_v1.json"
METHOD = "exact-two-pass-boundaries-and-whole-interior-acoustics-v1"
AUTHORITY = "HASH_BOUND_AUTOMATED_VERIFICATION"
STATUS = "AUTOMATED_BOUNDARY_VERIFIED_SOURCE_IDENTITY_VERIFIED"
ATTESTATION = "automated_checks_completed_no_human_listening_attested"


def authorization_binding() -> dict:
    doc = json.loads(AUTHORIZATION.read_text(encoding="utf-8"))
    if (doc.get("schema") != "member-longform-automated-authorization-v1"
            or doc.get("automated_verification_authorized") is not True
            or doc.get("human_signoff_required") is not False
            or doc.get("failed_checks_may_be_promoted") is not False
            or doc.get("human_attestations_may_be_fabricated") is not False
            or doc.get("speaker_thresholds_may_be_lowered") is not False
            or doc.get("complete_response_boundaries_required") is not True
            or doc.get("recurring_claims_require_independent_interviews") != 2
            or doc.get("current_leader_baseline_requires_pre_current_role") is not True):
        raise ValueError("automatic_verification_not_authorized")
    return {"path": str(AUTHORIZATION.resolve()), "sha256": SMR.sha256_file(AUTHORIZATION),
            "authorization_id": doc["authorization_id"]}


def duration_findings(turn: dict) -> list[dict]:
    seconds = round(float(turn["end_sec"]) - float(turn["start_sec"]), 6)
    return [{"kind": "observed_complete_answer_duration",
             "finding": f"One complete recorded response lasted {seconds:g} seconds.",
             "scope": "Automated source-bound observation from one public interview; a recurring pattern is not established."}]


def check_metadata(review: dict) -> None:
    if (review.get("reviewer_type") != "AUTOMATED"
            or review.get("reviewer_name") != METHOD
            or review.get("review_attestation") != ATTESTATION
            or review.get("authorization") != authorization_binding()
            or any(review.get(key) != "ACCEPT" for key in
                   ("review_status", "identity_review", "boundary_review", "semantic_review"))
            or any(not str(review.get(key) or "").strip() for key in
                   ("identity_review_note", "boundary_review_note"))):
        raise ValueError("automatic_verification_metadata_invalid")
    stamp = dt.datetime.fromisoformat(str(review.get("reviewed_at") or "").replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("automatic_verification_timestamp_invalid")


def verify_boundaries(review: dict, selection: dict, candidate: dict, turn: dict) -> list[Path]:
    """Recompute agreement from actual hash-bound annotation and word files."""
    check_metadata(review)
    proof = review.get("boundary_evidence") or {}
    receipt_path = Path(str(proof.get("receipt_path") or ""))
    if not receipt_path.is_file() or SMR.sha256_file(receipt_path) != proof.get("receipt_sha256"):
        raise ValueError("automatic_boundary_receipt_hash")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    if (receipt.get("status") != "PASS_CANDIDATES_ONLY"
            or receipt.get("contract", {}).get("version") != reconciler.CONTRACT_VERSION
            or candidate.get("classifier_contract_version") != reconciler.CONTRACT_VERSION):
        raise ValueError("automatic_boundary_method_not_supported")
    paths = [receipt_path, AUTHORIZATION]
    output = Path(str((receipt.get("output") or {}).get("path") or ""))
    if not output.is_file() or SMR.sha256_file(output) != receipt["output"].get("sha256"):
        raise ValueError("automatic_candidate_artifact_hash")
    records = SMR.read_jsonl(output, required=True)
    if records != [candidate]:
        raise ValueError("automatic_candidate_artifact_binding")
    paths.append(output)
    passes = []
    for key in ("pass_a", "pass_b"):
        path = Path(str(receipt.get("inputs", {}).get(key + "_path") or ""))
        if not path.is_file() or SMR.sha256_file(path) != receipt["inputs"].get(key + "_sha256"):
            raise ValueError("automatic_annotation_hash_binding:" + key)
        paths.append(path)
        passes.append(json.loads(path.read_text(encoding="utf-8-sig")))
    if paths[-1].resolve() == paths[-2].resolve():
        raise ValueError("automatic_annotation_passes_not_distinct")
    words = SMR.read_jsonl(Path(selection["transcript"]), required=True)
    agreed, _ = reconciler.reconcile(selection, words, passes[0], passes[1],
                                     receipt["contract"]["boundary_tolerance_seconds"])
    if agreed != candidate.get("turns") or turn not in agreed:
        raise ValueError("automatic_complete_boundaries_not_reproduced")
    if review.get("semantic_findings") != duration_findings(turn):
        raise ValueError("automatic_semantics_exceed_measured_observation")
    return paths


def provenance_fields(automated: bool) -> tuple[str, str]:
    return (("automated_verifier", "automated_verified_at") if automated
            else ("human_reviewer", "human_reviewed_at"))
