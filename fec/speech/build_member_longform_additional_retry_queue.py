#!/usr/bin/env python3
"""Queue another independent recording when all earlier recordings lack cues.

The first invocation normally follows the primary and first retry cue screens.
Later invocations may pass matching ``--additional-cues`` and
``--additional-acquisition`` pairs, allowing a fourth or later recording to be
selected without discarding any earlier source.  Search metadata grants no
identity or profile authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

from fec.speech import build_member_longform_no_cue_retry_queue as retry
from fec.speech import source_media_recovery as SMR


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PRIMARY_CUES = Path(r"C:\melange-data\member_longform_identity_cues_v1.jsonl")
DEFAULT_RETRY_CUES = Path(r"C:\melange-data\member_longform_no_cue_retry_identity_cues_v1.jsonl")
DEFAULT_DISCOVERY = ROOT / "data/speech/member_longform_youtube_discovery_v1.jsonl"
DEFAULT_SUPPLEMENTAL_DISCOVERY = ROOT / "data/speech/member_longform_general_web_candidates_v1.jsonl"
DEFAULT_PRIMARY_ACQUISITION = ROOT / "data/speech/member_longform_acquisition_v1.jsonl"
DEFAULT_RETRY_ACQUISITION = ROOT / "data/speech/member_longform_no_cue_retry_acquisition_v1.jsonl"
DEFAULT_ROLES = ROOT / "data/speech/member_leadership_role_cutoffs_v1.json"
DEFAULT_OVERRIDES = retry.DEFAULT_OVERRIDES
DEFAULT_OUT = ROOT / "data/speech/member_longform_no_cue_retry2_queue_v1.jsonl"
DEFAULT_RECEIPT = ROOT / "data/speech/member_longform_no_cue_retry2_queue_v1.receipt.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def cue_map(path: Path) -> dict[str, dict]:
    return {str(row["member"]): row for row in SMR.read_jsonl(path, required=True)}


def normalized_candidate(row: dict, default_source_type: str | None = "youtube") -> dict | None:
    """Normalize YouTube discovery and manually resolved podcast candidates.

    Rows without ``resolved_source_type`` take ``default_source_type``; pass
    ``None`` (supplemental general-web rows) to drop unresolved rows.
    """
    source_type = str(row.get("resolved_source_type") or default_source_type or "")
    if source_type not in {"youtube", "apple_directory_audio"}:
        return None
    url = row.get("resolved_url") or row.get("url")
    if not url:
        return None
    return {
        **row,
        "source_type": source_type,
        "url": url,
        "channel": row.get("channel") or row.get("provider"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary-cues", type=Path, default=DEFAULT_PRIMARY_CUES)
    parser.add_argument("--retry-cues", type=Path, default=DEFAULT_RETRY_CUES)
    parser.add_argument("--discovery", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument(
        "--supplemental-discovery", type=Path, default=DEFAULT_SUPPLEMENTAL_DISCOVERY
    )
    parser.add_argument("--primary-acquisition", type=Path, default=DEFAULT_PRIMARY_ACQUISITION)
    parser.add_argument("--retry-acquisition", type=Path, default=DEFAULT_RETRY_ACQUISITION)
    parser.add_argument(
        "--additional-cues",
        type=Path,
        action="append",
        default=[],
        help="Cue ledger for an already-tried later source; repeat in source order.",
    )
    parser.add_argument(
        "--additional-acquisition",
        type=Path,
        action="append",
        default=[],
        help="Acquisition ledger paired with --additional-cues; repeat in source order.",
    )
    parser.add_argument("--roles", type=Path, default=DEFAULT_ROLES)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    args = parser.parse_args()
    if len(args.additional_cues) != len(args.additional_acquisition):
        parser.error(
            "--additional-cues and --additional-acquisition must be supplied "
            "the same number of times"
        )

    primary_cues = cue_map(args.primary_cues)
    retry_cues = cue_map(args.retry_cues)
    additional_cues = [cue_map(path) for path in args.additional_cues]
    primary_acquisition = {
        str(row["member"]): row for row in SMR.read_jsonl(args.primary_acquisition, required=True)
    }
    retry_acquisition = {
        str(row["member"]): row for row in SMR.read_jsonl(args.retry_acquisition, required=True)
    }
    additional_acquisitions = [
        {str(row["member"]): row for row in SMR.read_jsonl(path, required=True)}
        for path in args.additional_acquisition
    ]
    roles = set(
        (json.loads(args.roles.read_text(encoding="utf-8-sig")).get("members") or {}).keys()
    )
    overridden = {
        str(row["member"]) for row in SMR.read_jsonl(args.overrides)
    } if args.overrides.exists() else set()
    unresolved = {
        member for member, row in primary_cues.items()
        if row.get("status") == "NO_FULL_NAME_CUE_FOUND"
        and (retry_cues.get(member) or {}).get("status") != "HARD_IN_BAND_NAME_CUE_CANDIDATE"
        and all(
            (cues.get(member) or {}).get("status") != "HARD_IN_BAND_NAME_CUE_CANDIDATE"
            for cues in additional_cues
        )
        and member not in roles
    }
    # A reviewed override is the primary source; never fall back to the
    # unreviewed discovery pool for these members.
    deferred_to_override = unresolved & overridden
    unresolved -= overridden
    grouped: dict[str, list[dict]] = {}
    discovery_rows = [
        normalized_candidate(raw, "youtube")
        for raw in SMR.read_jsonl(args.discovery, required=True)
    ]
    if args.supplemental_discovery.is_file():
        discovery_rows.extend(
            normalized_candidate(raw, None)
            for raw in SMR.read_jsonl(args.supplemental_discovery)
        )
    for row in discovery_rows:
        if row:
            grouped.setdefault(str(row["member"]), []).append(row)

    rows = []
    for member in sorted(unresolved):
        excluded_ids = {
            str((primary_acquisition.get(member) or {}).get("source_id") or ""),
            str((retry_acquisition.get(member) or {}).get("source_id") or ""),
            *(
                str((acquisition.get(member) or {}).get("source_id") or "")
                for acquisition in additional_acquisitions
            ),
        } - {""}
        excluded_urls = {
            str((primary_acquisition.get(member) or {}).get("url") or ""),
            str((retry_acquisition.get(member) or {}).get("url") or ""),
            *(
                str((acquisition.get(member) or {}).get("url") or "")
                for acquisition in additional_acquisitions
            ),
        } - {""}
        alternatives = [
            row for row in grouped.get(member, [])
            if (
                not row.get("video_id")
                or str(row.get("video_id")) not in excluded_ids
            )
            and str(row.get("url") or "") not in excluded_urls
        ]
        alternatives.sort(key=lambda row: retry.contextual_score(member, row), reverse=True)
        if not alternatives:
            continue
        compact = [{
            "source_type": row["source_type"],
            "url": row.get("url"),
            "video_id": row.get("video_id"),
            "audio_url": row.get("audio_url"),
            "title": row.get("title"),
            "channel": row.get("channel"),
            "published": row.get("published"),
            "duration_seconds": row.get("duration_seconds"),
            "candidate_rank": row.get("candidate_rank"),
            "metadata_relevance_score": row.get("metadata_relevance_score"),
            "contextual_retry_score": retry.contextual_score(member, row)[0],
        } for row in alternatives[:6]]
        rows.append({
            "schema": "member-longform-no-cue-retry-queue-v1",
            "status": "ADDITIONAL_SOURCE_CANDIDATE_READY_FOR_ACQUISITION",
            "member": member,
            "primary": compact[0],
            "alternatives": compact,
            "candidate_count": len(alternatives),
            "acquisition_route": "multi_provider_audio_download",
            "next_step": "download_hash_transcribe_repeat_in_band_name_gate",
            "contract": {
                "earlier_recordings_discarded": False,
                "search_metadata_establishes_identity": False,
                "profile_effect_before_promotion": "NONE",
            },
        })

    SMR.write_jsonl_atomic(args.out, rows)
    receipt = {
        "schema": "member-longform-additional-retry-queue-receipt-v1",
        "status": "PASS_RETRY_QUEUE_ONLY",
        "generated_at": utc_now(),
        "counts": {
            "unresolved_after_primary_and_first_retry": len(unresolved),
            "additional_source_queued": len(rows),
            "without_discovered_additional_source": len(unresolved) - len(rows),
            "deferred_to_reviewed_primary_override": len(deferred_to_override),
        },
        "contract": {
            "earlier_recordings_are_preserved": True,
            "selected_source_differs_from_prior_sources": True,
            "profile_effect": "NONE_UNTIL_FULL_PROMOTION",
        },
        "inputs": {
            "primary_cues_sha256": SMR.sha256_file(args.primary_cues),
            "retry_cues_sha256": SMR.sha256_file(args.retry_cues),
            "discovery_sha256": SMR.sha256_file(args.discovery),
            "supplemental_discovery_sha256": (
                SMR.sha256_file(args.supplemental_discovery)
                if args.supplemental_discovery.is_file() else None
            ),
            "primary_acquisition_sha256": SMR.sha256_file(args.primary_acquisition),
            "retry_acquisition_sha256": SMR.sha256_file(args.retry_acquisition),
            "additional_cues": [
                {"path": str(path.resolve()), "sha256": SMR.sha256_file(path)}
                for path in args.additional_cues
            ],
            "additional_acquisitions": [
                {"path": str(path.resolve()), "sha256": SMR.sha256_file(path)}
                for path in args.additional_acquisition
            ],
            "overrides_sha256": (
                SMR.sha256_file(args.overrides)
                if args.overrides.is_file() else None
            ),
            "leadership_roles_sha256": SMR.sha256_file(args.roles),
        },
        "output": {"path": str(args.out.resolve()), "sha256": SMR.sha256_file(args.out)},
    }
    SMR.write_json_atomic(args.receipt, receipt)
    print(json.dumps(receipt["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
