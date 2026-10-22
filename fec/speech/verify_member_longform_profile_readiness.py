#!/usr/bin/env python3
"""Fail closed unless roster-wide reviewed long-form evidence is profile-ready."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import wave

from fec.speech import build_member_longform_review_queue as queue
from fec.speech import materialize_member_longform_reviewed_turns as materializer
from fec.speech import source_media_recovery as SMR
from fec.speech import longform_automated_authority as auto


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROSTER = ROOT / "data/members/profile_basis_v2.json"
DEFAULT_ROLES = ROOT / "data/speech/member_leadership_role_cutoffs_v1.json"
DEFAULT_LEADER_SOURCES = ROOT / "data/speech/member_leadership_prerole_reviewed_sources_v1.jsonl"
DEFAULT_TURNS = ROOT / "data/speech/member_longform_reviewed_turns_v1.jsonl"
DEFAULT_TURN_RECEIPT = ROOT / "data/speech/member_longform_reviewed_turns_v1.receipt.json"
DEFAULT_OUT = ROOT / "data/speech/member_longform_profile_readiness_v1.receipt.json"
VALID_PHASES = {"open", "bring", "run_the_room", "close"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def bound_inputs(receipt: dict, errors: list[str]) -> dict:
    """Read exact receipt-bound evidence bytes once, never infer a human review."""
    result = {}
    for key in ("selection", "candidates", "continuity", "reviews"):
        try:
            path = Path(receipt["input_paths"][key])
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != receipt["inputs"][key + "_sha256"]:
                raise ValueError("input hash mismatch")
            rows = [json.loads(line) for line in payload.decode("utf-8-sig").splitlines() if line.strip()]
            if key == "reviews":
                index = {str(row["turn_id"]): row for row in rows}
                if len(index) != len(rows):
                    raise ValueError("duplicate review turn id")
                result[key] = index
            else:
                result[key] = materializer.source_index(rows, key)
        except (OSError, KeyError, ValueError, TypeError, materializer.PromotionRefusal) as exc:
            errors.append(f"reviewed-turn evidence input invalid ({key}): {exc}")
            result[key] = {}
    return result


def exact_review_errors(row: dict, inputs: dict) -> list[str]:
    errors = []
    review = inputs["reviews"].get(str(row.get("turn_id") or "")) or {}
    key = (str(row.get("member") or ""), str(row.get("source_id") or ""))
    candidate = inputs["candidates"].get(key) or {}
    acoustic = inputs["continuity"].get(key) or {}
    selected = inputs["selection"].get(key) or {}
    if row.get("row_sha256") != materializer.row_hash({key: value for key, value in row.items() if key != "row_sha256"}):
        errors.append("reviewed_row_hash")
    automated = review.get("reviewer_type") == "AUTOMATED"
    if automated:
        try:
            auto.check_metadata(review)
            if (row.get("automated_verifier") != review["reviewer_name"]
                    or row.get("automated_verified_at") != review["reviewed_at"]
                    or row.get("review_row_sha256") != materializer.row_hash(review)):
                raise ValueError("automatic_review_provenance_binding")
        except (ValueError, KeyError, OSError, SMR.RecoveryError) as exc:
            errors.append("automated_review_binding:" + str(exc))
    elif (not review or review.get("reviewer_type") != "HUMAN"
            or not str(review.get("reviewer_name") or "").strip()
            or row.get("human_reviewer") != str(review.get("reviewer_name") or "").strip()
            or row.get("human_reviewed_at") != review.get("reviewed_at")
            or row.get("review_row_sha256") != materializer.row_hash(review)
            or review.get("review_attestation") != "personally_listened_and_reviewed_identity_boundaries_and_semantics"
            or any(review.get(field) != "ACCEPT" for field in
                   ("review_status", "identity_review", "boundary_review", "semantic_review"))):
        errors.append("named_human_review_binding")
    try:
        reviewed_at = dt.datetime.fromisoformat(str(review.get("reviewed_at") or "").replace("Z", "+00:00"))
        if reviewed_at.tzinfo is None:
            raise ValueError("timezone missing")
    except ValueError:
        errors.append("human_review_timestamp")
    if (row.get("identity_review") != review.get("identity_review_note")
            or row.get("boundary_review") != review.get("boundary_review_note")
            or not str(row.get("identity_review") or "").strip()
            or not str(row.get("boundary_review") or "").strip()
            or row.get("semantic_findings") != review.get("semantic_findings")):
        errors.append("human_review_findings_or_notes")
    for field, materialized in (("wav_sha256", "source_audio_sha256"),
                                ("transcript_sha256", "source_transcript_sha256")):
        if not row.get(materialized) or any(item.get(field) != row[materialized]
                                           for item in (review, selected, candidate, acoustic)):
            errors.append("source_evidence_hash_binding")
    if (review.get("member"), review.get("source_id")) != key:
        errors.append("review_member_source_binding")
    try:
        turn_index, turn = materializer.matching_turn(candidate, row)
        if automated:
            auto.verify_boundaries(review, selected, candidate, turn)
            if not (acoustic.get("anchored_member") is True or acoustic.get("supplemental_member") is True):
                errors.append("automatic_cold_start_is_not_positive_identity")
        coverage = queue.qualified_turn_coverage(candidate, acoustic, turn_index, turn)
        if (not coverage or row.get("turn_coverage") != coverage
                or row.get("candidate_turn_binding_sha256") != coverage["turn_binding_sha256"]
                or review.get("candidate_turn_binding_sha256") != coverage["turn_binding_sha256"]
                or abs(float(review["start_sec"]) - float(row["start_sec"])) > .001
                or abs(float(review["end_sec"]) - float(row["end_sec"])) > .001):
            errors.append("whole_turn_acoustic_coverage")
    except (KeyError, ValueError, TypeError, materializer.PromotionRefusal, SMR.RecoveryError):
        errors.append("whole_turn_acoustic_coverage")
    if selected.get("status") != "SELECTED_HARD_NAME_CUE_CANDIDATE" or not selected.get("cues"):
        errors.append("selected_named_handoff")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster", type=Path, default=DEFAULT_ROSTER)
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES)
    parser.add_argument("--leader-sources", type=Path, default=DEFAULT_LEADER_SOURCES)
    parser.add_argument("--turns", type=Path, default=DEFAULT_TURNS)
    parser.add_argument("--turn-receipt", type=Path, default=DEFAULT_TURN_RECEIPT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    input_files = {"roster": args.roster, "roles": args.roles, "leader_sources": args.leader_sources,
                   "turns": args.turns, "turn_receipt": args.turn_receipt}
    initial_hashes = {key: SMR.sha256_file(path) for key, path in input_files.items()}

    roster = set((read_json(args.roster).get("members") or {}).keys())
    roles = read_json(args.roles).get("members") or {}
    leader_source_rows = SMR.read_jsonl(args.leader_sources, required=True)
    leader_sources = {str(row["member"]): row for row in leader_source_rows}
    turns = SMR.read_jsonl(args.turns, required=True)
    turn_receipt = read_json(args.turn_receipt)
    errors: list[str] = []
    if not roster:
        errors.append("empty active roster")
    if not set(roles) <= roster:
        errors.append("leadership registry is not a subset of the active roster")
    if set(leader_sources) != set(roles):
        errors.append("leadership source ledger does not exactly cover the leadership registry")
    if len(leader_sources) != len(leader_source_rows):
        errors.append("duplicate leadership source rows")
    if len({row.get("turn_id") for row in turns}) != len(turns):
        errors.append("duplicate reviewed turn ids")
    if turn_receipt.get("schema") != "member-longform-reviewed-turn-receipt-v1":
        errors.append("reviewed-turn receipt schema mismatch")
    if turn_receipt.get("status") != "PASS":
        errors.append("reviewed-turn receipt is not PASS")
    if turn_receipt.get("artifact_sha256") != SMR.sha256_file(args.turns):
        errors.append("reviewed-turn receipt hash is stale")
    if Path(str(turn_receipt.get("artifact") or "")).resolve() != args.turns.resolve():
        errors.append("reviewed-turn receipt artifact path is stale")
    if not ((turn_receipt.get("contract") or {}).get("human_identity_boundary_semantic_review_required")
            or (turn_receipt.get("contract") or {}).get("user_authorized_automated_verification_allowed")):
        errors.append("reviewed-turn receipt lacks the human-review contract")
    evidence_inputs = bound_inputs(turn_receipt, errors)

    members_with_valid_turns: set[str] = set()
    invalid_rows: list[str] = []
    for index, row in enumerate(turns, 1):
        member = str(row.get("member") or "")
        row_errors = exact_review_errors(row, evidence_inputs)
        if member not in roster:
            row_errors.append("member_not_in_roster")
        if row.get("status") not in {"HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED", auto.STATUS}:
            row_errors.append("status")
        if not row.get("turn_audio_wav") or not row.get("turn_audio_sha256"):
            row_errors.append("turn_audio_binding")
        try:
            duration = float(row.get("duration_seconds") or 0.0)
            start, end = float(row["start_sec"]), float(row["end_sec"])
            if not all(math.isfinite(value) for value in (duration, start, end)) or abs(duration - (end - start)) > .001:
                row_errors.append("duration_span")
            turn_path = Path(row["turn_audio_wav"])
            if SMR.sha256_file(turn_path) != row.get("turn_audio_sha256"):
                row_errors.append("physical_turn_audio_hash")
            with wave.open(str(turn_path), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                    row_errors.append("turn_audio_format")
                if abs(wav.getnframes() / wav.getframerate() - duration) > .002:
                    row_errors.append("physical_turn_duration")
        except (TypeError, ValueError, KeyError, OSError, EOFError, wave.Error, ZeroDivisionError):
            duration = 0.0
            row_errors.append("physical_turn_audio_invalid")
        if duration < 5.0:
            row_errors.append("duration")
        findings = row.get("semantic_findings") or []
        if not isinstance(findings, list) or not findings:
            row_errors.append("semantic_findings")
        for finding in findings if isinstance(findings, list) else []:
            if not isinstance(finding, dict) or any(not str(finding.get(key) or "").strip() for key in (
                "kind", "finding", "scope"
            )):
                row_errors.append("semantic_finding_fields")
                break
            if finding.get("meeting_phase") is not None and finding.get("meeting_phase") not in VALID_PHASES:
                row_errors.append("meeting_phase")
                break
        if member in roles and row.get("source_era") != "PRE_CURRENT_LEADERSHIP_ROLE":
            row_errors.append("leader_not_prerole")
        if member in roles:
            try:
                cutoff = dt.date.fromisoformat(str(roles[member]["current_role_assumed_on"])[:10])
                recorded = dt.date.fromisoformat(str(row.get("recorded_at") or row.get("published") or "")[:10])
                row_cutoff = dt.date.fromisoformat(str(row.get("current_role_assumed_on") or "")[:10])
                if recorded >= cutoff or row_cutoff != cutoff:
                    raise ValueError("not before current role")
                selected = evidence_inputs["selection"].get((member, str(row.get("source_id") or ""))) or {}
                selected_date = dt.date.fromisoformat(str(selected.get("recorded_at") or selected.get("published") or "")[:10])
                if selected_date != recorded:
                    raise ValueError("recording date does not match selected source")
            except (ValueError, KeyError, TypeError):
                row_errors.append("leader_recording_date_not_pre_current_role")
        if row_errors:
            invalid_rows.append(f"row_{index}:{member}:{','.join(sorted(set(row_errors)))}")
        else:
            members_with_valid_turns.add(member)

    explicit_gaps = {
        member for member, row in leader_sources.items()
        if row.get("status") == "HISTORICAL_LONGFORM_SOURCE_GAP"
        and str(row.get("gap_reason") or "").strip()
    }
    # A documented missing archive is still missing audio, not verified coverage.
    covered = members_with_valid_turns
    missing = sorted(roster - covered)
    if invalid_rows:
        errors.append("invalid reviewed-turn rows: " + " | ".join(invalid_rows[:10]))
    if missing:
        errors.append("members without a verified complete turn (documented source gaps remain missing): " + ", ".join(missing[:20]))
    nonleader_gaps = explicit_gaps - set(roles)
    if nonleader_gaps:
        errors.append("nonleaders cannot be covered by a leadership historical gap")
    for key, path in input_files.items():
        try:
            unchanged = SMR.sha256_file(path) == initial_hashes[key]
        except OSError:
            unchanged = False
        if not unchanged:
            errors.append(f"readiness input changed during verification: {key}")
    for key, value in (turn_receipt.get("input_paths") or {}).items():
        try:
            unchanged = SMR.sha256_file(Path(value)) == (turn_receipt.get("inputs") or {}).get(key + "_sha256")
        except (OSError, TypeError):
            unchanged = False
        if not unchanged:
            errors.append(f"review evidence changed during readiness verification: {key}")

    receipt = {
        "schema": "member-longform-profile-readiness-receipt-v1",
        "status": "PASS" if not errors else "FAIL",
        "generated_at": utc_now(),
        "counts": {
            "active_members": len(roster),
            "members_with_valid_reviewed_semantic_turns": len(members_with_valid_turns),
            "documented_leadership_source_gaps": len(explicit_gaps),
            "covered_or_documented_gap": len((covered | explicit_gaps) & roster),
            "verified_member_coverage": len(covered & roster),
            "missing": len(missing),
            "invalid_rows": len(invalid_rows),
        },
        "missing_members": missing,
        "errors": errors,
        "contract": {
            "every_active_member_has_verified_complete_turn": not missing,
            "documented_gaps_count_as_verified": False,
            "every_admitted_turn_has_reviewed_descriptive_findings": not invalid_rows,
            "meeting_advice_is_not_required": True,
            "physical_turn_audio_hashes_and_durations_verified": not invalid_rows,
            "named_human_review_and_whole_interior_coverage_required": False,
            "source_bound_human_or_authorized_automated_verification_and_whole_interior_coverage_required": True,
            "current_leader_baseline_requires_pre_current_role": True,
            "turn_duration_ceiling_seconds": None,
            "audio_changes_traits_ranks_stance_allocation_or_engine": False,
        },
        "inputs": {key + "_sha256": value for key, value in initial_hashes.items()},
    }
    SMR.write_json_atomic(args.out, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    if errors:
        raise SystemExit("profile-readiness gate failed: " + "; ".join(errors[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
