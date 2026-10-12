#!/usr/bin/env python3
"""Refresh the ratified member profiles against the current evidence ledger.

The reviewed three-paragraph profiles retain their original wording. Sentences
asserting defensive behavior are omitted when its current evidence gate closes;
every omission is recorded, and no replacement prose is generated. Current
evidence is used to re-run the deterministic claim gates and to rebuild the
separate meeting-playbook layer.  Descriptive clip audio and verified
voiceprint audio remain independent, complementary evidence sources.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fec.members.build_member_meeting_playbooks import build as build_playbook
from fec.members.revise_member_narratives_v2 import coop_band, cross_party_hits, headed_text, mechanical_issues


DEFAULT_LEGACY = (
    ROOT
    / "audits/descriptive_profiles_20260904/previous_active"
    / "member_narratives_v2.json"
)
DEFAULT_PRE_LONGFORM = (
    ROOT
    / "data/members/member_narratives_v2_work"
    / "member_narratives_v2.pre_basis_restamp_20260903T023548Z.json"
)
DEFAULT_EVIDENCE = ROOT / "data/members/member_narrative_evidence_v4.json"
DEFAULT_ACTIVE = ROOT / "member_narratives_v2.json"
DEFAULT_RECEIPT = ROOT / "data/members/member_narratives_v2_semantic_receipt.json"
DEFAULT_CANDIDATE = ROOT / "member_narratives_v2.candidate.json"
DEFAULT_BACKUP = (
    ROOT
    / "data/members/member_narratives_v2_work"
    / "member_narratives_v2.pre_legacy_refresh_20260908.json"
)
UNQUALIFIED_LONGFORM_MEMBERS = frozenset({
    "Alex Padilla", "Maxine Waters", "Mike Johnson", "Steve Scalise",
})

DEFENSE_LANGUAGE = re.compile(
    r"\bdefen(?:s|c|d)\w*\b|\bpush\s*back\b|\bpushback\b|\bpushes\s+back\b|\bpushing\s+back\b",
    re.IGNORECASE,
)


def omit_unsupported_defense(row: dict, evidence: dict) -> tuple[dict, list[dict]]:
    """Suppress only whole unsupported sentences; preserve all retained bytes.

    This is subtraction from reviewed prose, not fresh narrative generation.
    All original mechanical gates still run on the resulting three paragraphs.
    An empty paragraph refuses publication instead of inventing replacement text.
    """
    result = dict(row)
    omissions = []
    if (evidence.get("speech_profile") or {}).get("repeated_defensive_reactions"):
        return result, omissions
    for key in ("p1", "p2", "p3"):
        paragraph = row[key]
        if not DEFENSE_LANGUAGE.search(paragraph):
            continue
        # Keep sentence separators attached to their preceding sentence, so
        # unaffected paragraphs and surviving wording are never normalized.
        sentences = re.findall(r".*?(?:[.!?](?:\s+|$)|$)", paragraph)
        retained = []
        for sentence in sentences:
            if not sentence:
                continue
            if DEFENSE_LANGUAGE.search(sentence):
                omissions.append({"paragraph": key, "text": sentence.rstrip(),
                                  "reason": "repeated_defensive_reactions gate absent"})
            else:
                retained.append(sentence)
        result[key] = "".join(retained).rstrip()
        if not result[key].strip():
            raise ValueError(f"{key}: unsupported-defense omission would empty the paragraph")
    return result, omissions


def _cross_party_claim(text: str) -> bool:
    # "coalition-building" is this corpus's shorthand for cross-party work (Dingell, Alford, Guest, Bennet).
    return bool(cross_party_hits(text)) or "coalition-building" in text.lower()


def omit_unsupported_cross_party(row: dict, evidence: dict) -> tuple[dict, list[dict]]:
    """Same subtraction contract as omit_unsupported_defense, for cross-party claims.

    A reviewed sentence about working with the other party stays only while the
    cross_party_cooperation measure covers the member.  2026-10-11: CU_IDENTITY_FIX
    (0b65a2cab) removed Sen. Menendez's Senate bills from Rep. Robert Menendez's record,
    so his cooperation_v2 coverage (and the basis of those sentences) is gone.
    """
    result = dict(row)
    omissions = []
    if coop_band(evidence) is not None:
        return result, omissions
    for key in ("p1", "p2", "p3"):
        paragraph = row[key]
        if not _cross_party_claim(paragraph):
            continue
        sentences = re.findall(r".*?(?:[.!?](?:\s+|$)|$)", paragraph)
        retained = []
        for sentence in sentences:
            if not sentence:
                continue
            if _cross_party_claim(sentence):
                omissions.append({"paragraph": key, "text": sentence.rstrip(),
                                  "reason": "cross_party_cooperation measure absent"})
            else:
                retained.append(sentence)
        result[key] = "".join(retained).rstrip()
        if not result[key].strip():
            raise ValueError(f"{key}: unsupported cross-party omission would empty the paragraph")
    return result, omissions


def load(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as handle:
        document = json.load(handle)
    if not isinstance(document, dict):
        raise SystemExit(f"{path}: expected a JSON object")
    return document


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_atomic(document: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


def _audio_population(evidence_members: dict) -> dict[str, int]:
    counts = {
        "descriptive_audio_profiles": 0,
        "verified_voiceprint_profiles": 0,
        "both_audio_layers": 0,
        "voiceprint_only_audio_profiles": 0,
        "descriptive_only_audio_profiles": 0,
        "no_audio_layer": 0,
    }
    for record in evidence_members.values():
        speech = record.get("speech_profile") or {}
        descriptive = bool(speech.get("general_audio_habits"))
        voiceprint = bool(speech.get("verified_voiceprint_baseline"))
        counts["descriptive_audio_profiles"] += int(descriptive)
        counts["verified_voiceprint_profiles"] += int(voiceprint)
        counts["both_audio_layers"] += int(descriptive and voiceprint)
        counts["voiceprint_only_audio_profiles"] += int(voiceprint and not descriptive)
        counts["descriptive_only_audio_profiles"] += int(descriptive and not voiceprint)
        counts["no_audio_layer"] += int(not descriptive and not voiceprint)
    return counts


def build_candidate(
    legacy: dict,
    pre_longform: dict,
    evidence: dict,
    *,
    legacy_path: Path,
    pre_longform_path: Path,
    evidence_path: Path,
) -> tuple[dict, dict]:
    legacy_members = legacy.get("members") or {}
    pre_longform_members = pre_longform.get("members") or {}
    evidence_members = evidence.get("members") or {}
    if legacy.get("schema") != "member-narratives-v2-api":
        raise SystemExit("legacy profile source is not member-narratives-v2-api")
    if evidence.get("schema") != "member-narrative-evidence-v4":
        raise SystemExit("evidence source is not member-narrative-evidence-v4")
    if evidence.get("status") != "ACTIVE_FAIL_CLOSED_INPUT":
        raise SystemExit("evidence source is not active fail-closed input")
    if pre_longform.get("schema") != "member-narratives-v2-api":
        raise SystemExit("pre-long-form profile source is not member-narratives-v2-api")
    if (len(legacy_members) != 533 or set(legacy_members) != set(evidence_members)
            or set(pre_longform_members) != set(evidence_members)):
        raise SystemExit("legacy profile and evidence rosters must match all 533 members")

    members = {}
    paragraphs = {}
    omitted_claims = {}
    for name in sorted(legacy_members):
        source = (pre_longform_members[name]
                  if name in UNQUALIFIED_LONGFORM_MEMBERS else legacy_members[name])
        row = {"ok": True, **{key: source.get(key) for key in ("p1", "p2", "p3")}}
        if any(not isinstance(row[key], str) or not row[key].strip() for key in ("p1", "p2", "p3")):
            raise SystemExit(f"{name}: legacy profile has an empty paragraph")
        row, omissions = omit_unsupported_defense(row, evidence_members[name])
        row, cross_party = omit_unsupported_cross_party(row, evidence_members[name])
        omissions += cross_party
        if omissions:
            omitted_claims[name] = omissions
        issues = mechanical_issues(headed_text(row), name, evidence_members[name])
        if issues:
            raise SystemExit(f"{name}: current-evidence claim gate failed: {issues!r}")
        for key in ("p1", "p2", "p3"):
            normalized = " ".join(row[key].casefold().split())
            if normalized in paragraphs:
                raise SystemExit(
                    f"duplicate paragraph: {paragraphs[normalized]} and {name}/{key}"
                )
            paragraphs[normalized] = f"{name}/{key}"
        row["meeting_playbook"] = build_playbook(evidence_members[name])
        members[name] = row

    evidence_counts = evidence.get("counts") or {}
    audio_counts = _audio_population(evidence_members)
    for key in (
        "descriptive_audio_profiles",
        "verified_voiceprint_profiles",
        "both_audio_layers",
        "voiceprint_only_audio_profiles",
        "no_audio_layer",
    ):
        if evidence_counts.get(key) != audio_counts[key]:
            raise SystemExit(
                f"evidence audio count mismatch for {key}: "
                f"declared={evidence_counts.get(key)!r} measured={audio_counts[key]}"
            )

    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    provenance = dict(legacy.get("provenance") or {})
    provenance["legacy_profile_refresh_20260908"] = {
        "completed_utc": now,
        "legacy_source": str(legacy_path.relative_to(ROOT)).replace("\\", "/"),
        "legacy_source_sha256": sha256(legacy_path),
        "pre_longform_source": str(pre_longform_path.relative_to(ROOT)).replace("\\", "/"),
        "pre_longform_source_sha256": sha256(pre_longform_path),
        "evidence_source": str(evidence_path.relative_to(ROOT)).replace("\\", "/"),
        "evidence_sha256": sha256(evidence_path),
        "ratified_profiles_retained": 529 - len(set(omitted_claims) - UNQUALIFIED_LONGFORM_MEMBERS),
        "unsupported_defense_omissions": omitted_claims,
        "profiles_with_omissions": len(omitted_claims),
        "pre_longform_profiles_retained_pending_review": sorted(UNQUALIFIED_LONGFORM_MEMBERS),
        "current_evidence_gate_pass": 533,
        "meeting_playbooks_rebuilt": 533,
        "audio_contract": (
            "Descriptive clip baselines and duration-weighted verified voiceprints are "
            "independent complementary layers on different scales. Either may exist without "
            "the other; neither replaces or backfills the other."
        ),
        "adjudication": (
            "Previously ratified source prose retained except for the recorded whole-sentence "
            "omissions when the current repeated-defensive evidence gate is absent. No new "
            "narrative prose is generated. Four profiles "
            "with candidate-only long-form claims use their pre-long-form reviewed prose until "
            "source-bound review passes. Deterministic source, scope, missing-measure, and "
            "construct-separation gates were re-run against the current ledger. No new human "
            "audio interpretation was claimed."
        ),
    }
    candidate = {
        "schema": "member-narratives-v2-api",
        "status": "ACTIVE_API_GENERATED_USER_RATIFIED",
        "basis_sha256": evidence["basis_sha256"],
        "count": 533,
        "ok_count": 533,
        "provenance": provenance,
        "members": members,
    }
    receipt = {
        "schema": "member-narratives-v2-semantic-receipt-v1",
        "status": "PASS",
        "created_utc": now,
        "basis_sha256": evidence["basis_sha256"],
        "evidence_sha256": sha256(evidence_path),
        "counts": {
            "members": 533,
            "semantic_pass": 533,
            "current_evidence_gate_pass": 533,
            "legacy_profiles_retained": 533 - len(omitted_claims),
            "ratified_profiles_retained": 529 - len(set(omitted_claims) - UNQUALIFIED_LONGFORM_MEMBERS),
            "profiles_with_omissions": len(omitted_claims),
            "unsupported_defense_sentences_omitted": sum(map(len, omitted_claims.values())),
            "pre_longform_profiles_retained_pending_review": 4,
            "meeting_playbooks": 533,
            "new_human_audio_reviews": 0,
            **audio_counts,
        },
        "review_contract": provenance["legacy_profile_refresh_20260908"]["adjudication"],
        "unsupported_defense_omissions": omitted_claims,
        "audio_contract": provenance["legacy_profile_refresh_20260908"]["audio_contract"],
    }
    return candidate, receipt


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy", type=Path, default=DEFAULT_LEGACY)
    parser.add_argument("--pre-longform", type=Path, default=DEFAULT_PRE_LONGFORM)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ACTIVE)
    parser.add_argument("--receipt", type=Path, default=DEFAULT_RECEIPT)
    parser.add_argument("--candidate-out", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--backup", type=Path, default=DEFAULT_BACKUP)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    for key in ("legacy", "pre_longform", "evidence", "artifact", "receipt", "candidate_out", "backup"):
        setattr(args, key, getattr(args, key).resolve())

    legacy = load(args.legacy)
    pre_longform = load(args.pre_longform)
    evidence = load(args.evidence)
    candidate, receipt = build_candidate(
        legacy, pre_longform, evidence, legacy_path=args.legacy,
        pre_longform_path=args.pre_longform, evidence_path=args.evidence
    )
    dump_atomic(candidate, args.candidate_out)
    receipt["candidate_sha256"] = sha256(args.candidate_out)

    if args.activate:
        if args.artifact.resolve() != DEFAULT_ACTIVE.resolve():
            raise SystemExit("--activate is allowed only for the canonical root artifact")
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        if not args.backup.exists():
            shutil.copy2(args.artifact, args.backup)
        os.replace(args.candidate_out, args.artifact)
        receipt["active_artifact_sha256"] = sha256(args.artifact)
        receipt["backup_path"] = str(args.backup.relative_to(ROOT)).replace("\\", "/")
        dump_atomic(receipt, args.receipt)
        print(
            json.dumps(
                {
                    "activated": str(args.artifact),
                    "members": 533,
                    "audio_layers": _audio_population(evidence["members"]),
                    "sha256": receipt["active_artifact_sha256"],
                },
                sort_keys=True,
            )
        )
    else:
        dump_atomic(receipt, args.candidate_out.with_suffix(".receipt.json"))
        print(
            json.dumps(
                {
                    "candidate": str(args.candidate_out),
                    "members": 533,
                    "audio_layers": _audio_population(evidence["members"]),
                    "sha256": receipt["candidate_sha256"],
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
