"""Assess descriptive interview observations for a methodology consultation.

This standalone candidate has no production consumers or promotion authority.
It assesses supplied review records; it does not perform a human review, prove
speaker identity, or independently verify the caller's current artifact hashes.
Historical status labels alone never qualify as human review provenance.

The input to ``assess_behavior`` is a list of observations plus one explicitly
defined behavior/actor/elicitation and ``source_bindings`` keyed by source ID.
Bindings contain the SHA-256 hashes of the current audio and transcript. An
observation's human review must bind to exactly those hashes. Settings and
production independence also require explicit review. Independence groups refer
to independently produced recordings, not channels: a reupload/syndication uses
the original group; two reviewed independent productions may share a producer.

CLI: python -m fec.speech.assess_member_behavior_evidence request.json
The request JSON supplies the keyword arguments of ``assess_behavior``. The
assessment is printed to stdout; no profile, review, or source file is written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from itertools import combinations
from pathlib import Path
from typing import Any


SCHEMA = "member-behavior-evidence-assessment-candidate-v1"
INITIATORS = {"member", "interviewer", "other"}
ELICITATIONS = {"spontaneous", "prompted", "follow_up"}
UNKNOWN = {"", "unknown", "unreviewed", "unspecified", "none", "null"}
SHA256 = re.compile(r"[0-9a-f]{64}", re.IGNORECASE)


def _known(value: Any) -> bool:
    return isinstance(value, str) and value.strip().casefold() not in UNKNOWN


def _hash(value: Any) -> str | None:
    return value.lower() if isinstance(value, str) and SHA256.fullmatch(value) else None


def _review_problems(row: dict, source_bindings: dict) -> list[str]:
    review = row.get("review") or {}
    problems = []
    if not isinstance(review, dict):
        return ["HUMAN_REVIEW_PROVENANCE_MISSING"]
    if (review.get("reviewer_type") != "human" or review.get("decision") != "accepted"
            or not _known(review.get("reviewer_id"))):
        problems.append("ACCEPTED_HUMAN_REVIEW_PROVENANCE_MISSING")
    try:
        dt.datetime.fromisoformat(str(review.get("reviewed_at") or "").replace("Z", "+00:00"))
    except ValueError:
        problems.append("HUMAN_REVIEW_DATE_MISSING_OR_INVALID")
    binding = source_bindings.get(row.get("source_id")) or {}
    for field in ("source_audio_sha256", "transcript_sha256"):
        current = _hash(binding.get(field)) if isinstance(binding, dict) else None
        observed = _hash(row.get(field))
        reviewed = _hash(review.get(field))
        if not current or observed != current or reviewed != current:
            problems.append(field.upper() + "_BINDING_MISSING_OR_STALE")
    return problems


def _context_problems(row: dict) -> list[str]:
    review = row.get("review") or {}
    if not isinstance(review, dict):
        review = {}
    problems = []
    for field in ("production_episode_id", "independence_group_id", "producer_id", "setting"):
        if not _known(row.get(field)):
            problems.append(field.upper() + "_MISSING")
    if review.get("production_independence_reviewed") is not True:
        problems.append("PRODUCTION_INDEPENDENCE_NOT_REVIEWED")
    if review.get("setting_reviewed") is not True:
        problems.append("SETTING_NOT_REVIEWED")
    return problems


def assess_behavior(
    observations: list[dict], *, member: str, behavior_code: str,
    initiated_by: str, elicitation: str, source_bindings: dict[str, dict],
) -> dict:
    """Return provisional/recurring/mixed evidence for a single observed behavior.

    ``polarity`` is ``supports`` or explicitly human-coded ``contradicts``.
    Failure to observe a behavior must not be silently coded as contradiction.
    All other source kinds, including secondhand field reports, are retained as
    exclusions. Unknown initiation or elicitation cannot be pooled into a target.
    """
    if not _known(member) or not _known(behavior_code):
        raise ValueError("member and behavior_code are required")
    if initiated_by not in INITIATORS or elicitation not in ELICITATIONS:
        raise ValueError("The target must specify a known initiated_by and elicitation")

    # Identical copies count once. Conflicting IDs fail closed, independent of order.
    unique: dict[str, dict] = {}
    for row in observations:
        if not isinstance(row, dict):
            raise ValueError("observations must contain objects")
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        identifier = row.get("observation_id")
        if not _known(identifier):
            identifier = "missing-id:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        if identifier in unique and unique[identifier] != row:
            raise ValueError("Conflicting observation_id: " + identifier)
        unique[identifier] = row

    reviewed_ids, reviewed_contradicting, eligible = [], [], {}
    exclusions = []
    for identifier, row in sorted(unique.items()):
        reasons = []
        if not _known(row.get("observation_id")):
            reasons.append("OBSERVATION_ID_MISSING")
        if row.get("member") != member or row.get("behavior_code") != behavior_code:
            reasons.append("DIFFERENT_MEMBER_OR_BEHAVIOR")
        if row.get("source_kind") != "interview":
            reasons.append("NOT_AN_INTERVIEW_OBSERVATION")
        if row.get("polarity") not in {"supports", "contradicts"}:
            reasons.append("EXPLICIT_SUPPORT_OR_CONTRADICTION_REQUIRED")
        reasons.extend(_review_problems(row, source_bindings))
        if row.get("initiated_by") != initiated_by or row.get("elicitation") != elicitation:
            reasons.append("INITIATION_OR_ELICITATION_UNKNOWN_OR_MISMATCHED")
        # Review counts belong to the exact target behavior, including who
        # initiated it and whether it was elicited. A reviewed host-prompted
        # response is not an observation of member-initiated spontaneous rapport.
        if not reasons:
            reviewed_ids.append(identifier)
            if row["polarity"] == "contradicts":
                reviewed_contradicting.append(identifier)
        # Missing context prevents independence claims, but must not erase an
        # accepted, hash-bound contradiction to this same target behavior.
        reasons.extend(_context_problems(row))
        if reasons:
            exclusions.append({"observation_id": identifier, "reasons": sorted(set(reasons))})
        else:
            eligible[identifier] = row

    supporting = sorted(key for key, row in eligible.items() if row["polarity"] == "supports")
    contradicting = sorted(reviewed_contradicting)
    contradicting_needing_context = sorted(key for key in contradicting if key not in eligible)
    independent_pairs = []
    pair_rejections = []
    independence_fields = ("production_episode_id", "independence_group_id", "setting")
    for first, second in combinations(supporting, 2):
        same = [field for field in independence_fields
                if eligible[first][field].strip().casefold() == eligible[second][field].strip().casefold()]
        # Duplicate media or transcript cannot establish independent observations,
        # even if a mirror was incorrectly given new episode/group metadata.
        for field in ("source_audio_sha256", "transcript_sha256"):
            if _hash(eligible[first][field]) == _hash(eligible[second][field]):
                same.append(field)
        if same:
            pair_rejections.append({"observation_ids": [first, second], "shared_fields": same})
        else:
            independent_pairs.append([first, second])

    reasons = []
    if contradicting:
        status = "MIXED_OR_CONTEXT_DEPENDENT"
        reasons.append("ACCEPTED_CONTRADICTORY_OBSERVATIONS_PRESERVED")
        if contradicting_needing_context:
            reasons.append("CONTRADICTORY_OBSERVATION_CONTEXT_INCOMPLETE")
    elif independent_pairs:
        status = "RECURRING_PUBLIC_PATTERN"
        reasons.append("TWO_REVIEWED_INDEPENDENT_INTERVIEWS_IN_DIFFERENT_SETTINGS")
    elif reviewed_ids:
        status = "PROVISIONAL"
        reasons.append("TWO_INDEPENDENT_INTERVIEW_DIFFERENT_SETTING_RULE_NOT_MET")
    else:
        status = "NO_REVIEWED_OBSERVATION"
        reasons.append("NO_ACCEPTED_HASH_BOUND_HUMAN_REVIEWED_INTERVIEW_OBSERVATION")

    return {
        "schema": SCHEMA,
        "status": status,
        "member": member,
        "behavior_code": behavior_code,
        "initiated_by": initiated_by,
        "elicitation": elicitation,
        "consultation_candidate": True,
        "profile_authority": False,
        "speaker_identity_authority": False,
        "automatic_promotion": False,
        "scope": "Descriptive public observations only; no personality, private preference, or persuasion inference.",
        "rule": "Same behavior and initiation/elicitation in two reviewed independent productions with distinct episodes and settings; producer alone establishes neither independence nor duplication.",
        "counts": {
            "input_observations": len(observations),
            "unique_observations": len(unique),
            "reviewed_interview_observations": len(reviewed_ids),
            "qualifying_supporting_observations": len(supporting),
            "reviewed_target_contradicting_observations": len(contradicting),
            "qualifying_contradicting_observations": sum(eligible[key]["polarity"] == "contradicts" for key in eligible),
            "contradicting_observations_needing_context": len(contradicting_needing_context),
            "supporting_production_episodes": len({eligible[key]["production_episode_id"].strip().casefold() for key in supporting}),
            "supporting_independence_groups": len({eligible[key]["independence_group_id"].strip().casefold() for key in supporting}),
            "supporting_settings": len({eligible[key]["setting"].strip().casefold() for key in supporting}),
            "independent_supporting_pairs": len(independent_pairs),
        },
        "reasons": reasons,
        "reviewed_interview_observation_ids": reviewed_ids,
        "supporting_observation_ids": supporting,
        "contradicting_observation_ids": contradicting,
        "contradicting_observation_ids_needing_context": contradicting_needing_context,
        "independent_supporting_pairs": independent_pairs,
        "nonindependent_supporting_pairs": pair_rejections,
        "excluded_observations": exclusions,
        "qualifying_contexts": [
            {"observation_id": key, **{field: row[field] for field in (
                "source_id", "production_episode_id", "independence_group_id", "producer_id", "setting", "polarity"
            )}} for key, row in sorted(eligible.items())
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="Explicit consultation request JSON")
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    print(json.dumps(assess_behavior(**request), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
