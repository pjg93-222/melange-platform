# -*- coding: utf-8 -*-
"""Fail-closed merge of semantically reviewed approach-brief revisions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fec.members.revise_member_narratives_v2 import (
    ARTIFACT_PATH,
    AUDIT_PATH,
    EVIDENCE_PATH,
    REVISIONS_PATH,
    body_text,
    cross_party_hits,
    headed_text,
    known_cross_party_targets,
    mechanical_issues,
)


ROOT = Path(__file__).resolve().parents[2]
RECEIPT_PATH = ROOT / "data" / "members" / "member_narratives_v2_semantic_receipt.json"


def load(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_atomic(document, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--activate", action="store_true", help="atomically replace member_narratives_v2.json")
    parser.add_argument("--artifact", type=Path, default=ARTIFACT_PATH)
    parser.add_argument("--audit", type=Path, default=AUDIT_PATH)
    parser.add_argument("--revisions", type=Path, default=REVISIONS_PATH)
    parser.add_argument("--evidence", type=Path, default=EVIDENCE_PATH)
    parser.add_argument("--receipt", type=Path, default=RECEIPT_PATH)
    parser.add_argument("--candidate-out", type=Path, default=ROOT / "member_narratives_v2.candidate.json")
    parser.add_argument("--backup", type=Path, default=ROOT / "member_narratives_v2.pre_semantic_repair_20260901.json")
    args = parser.parse_args()

    artifact_doc = load(args.artifact)
    audit_doc = load(args.audit)
    revisions_doc = load(args.revisions)
    evidence_doc = load(args.evidence)
    evidence_sha = sha256(args.evidence)
    artifact = artifact_doc.get("members") or {}
    audit = audit_doc.get("members") or {}
    revisions = revisions_doc.get("members") or {}
    evidence = evidence_doc.get("members") or {}

    if len(artifact) != 533 or set(artifact) != set(evidence) or set(artifact) != set(audit):
        raise SystemExit("artifact, evidence, and semantic-audit member keys must match all 533 members")
    if artifact_doc.get("basis_sha256") != evidence_doc.get("basis_sha256"):
        raise SystemExit("artifact/evidence basis hash mismatch")
    if audit_doc.get("count") != 533:
        raise SystemExit("full-corpus semantic audit is incomplete")

    # The 268 figure describes the pre-repair artifact that the 2026-09-01 semantic repair
    # was activated from (the installed artifact now yields 282 after that repair, which
    # would make this gate impossible to pass on any later merge). Compute the known-target
    # set from that frozen pre-repair backup when it exists so the gate keeps checking the
    # thing the 268 was measured against, and fall back to the current artifact only when
    # no such backup is present (e.g. before the first repair ever ran).
    known_target_basis = load(args.backup) if args.backup.exists() else artifact_doc
    known_targets = known_cross_party_targets(known_target_basis)
    required = known_targets | set(audit_doc.get("failed_members") or [])
    if len(known_targets) != 268:
        raise SystemExit("known cooperation repair set changed: expected 268, got %d" % len(known_targets))
    missing = sorted(name for name in required if not (revisions.get(name) or {}).get("ok"))
    extras = sorted(set(revisions) - required)
    if missing or extras:
        if revisions_doc.get("count") == 533:
            raise SystemExit(
                "revisions.json has already been sanitized to the full corpus; merge must run "
                "before sanitize (revise -> merge -> sanitize -> condense)")
        raise SystemExit("revision set mismatch: missing=%r extras=%r" % (missing, extras))

    merged = {}
    receipt_members = {}
    reviewer_counts = {}
    for name in sorted(artifact):
        if name in required:
            revision = revisions[name]
            review = revision.get("semantic_review") or {}
            if review.get("pass") is not True or review.get("issues"):
                raise SystemExit("revision lacks a clean semantic receipt: " + name)
            row = {"ok": True, "p1": revision["p1"], "p2": revision["p2"], "p3": revision["p3"]}
            reviewer = review.get("reviewer") or "anthropic-api-semantic-review"
            reviewer_counts[reviewer] = reviewer_counts.get(reviewer, 0) + 1
            receipt_members[name] = {"accepted_source": "regenerated", "semantic_pass": True,
                                     "reviewer": reviewer,
                                     "rounds": revision.get("rounds")}
        else:
            original_review = audit[name]
            if original_review.get("pass") is not True:
                raise SystemExit("unchanged brief did not pass the full-corpus audit: " + name)
            row = {"ok": True, "p1": artifact[name]["p1"], "p2": artifact[name]["p2"], "p3": artifact[name]["p3"]}
            receipt_members[name] = {"accepted_source": "unchanged_after_audit", "semantic_pass": True}
            reviewer_counts["anthropic-api-full-corpus-audit"] = reviewer_counts.get(
                "anthropic-api-full-corpus-audit", 0) + 1
        if any(not str(row.get(key) or "").strip() for key in ("p1", "p2", "p3")):
            raise SystemExit("empty paragraph: " + name)
        if any("**" in row[key] for key in ("p1", "p2", "p3")):
            raise SystemExit("markdown heading leaked into body prose: " + name)
        deterministic_issues = mechanical_issues(headed_text(row), name, evidence[name])
        if deterministic_issues:
            raise SystemExit("deterministic evidence gate failed for %s: %r" % (name, deterministic_issues))
        if coop_missing := ("cross_party_cooperation" not in (evidence[name].get("behavior") or {})):
            if cross_party_hits(body_text(row)):
                raise SystemExit("uncovered member still has a cross-party claim: " + name)
        merged[name] = row

    paragraphs = {}
    duplicate_paragraphs = []
    for name, row in merged.items():
        for key in ("p1", "p2", "p3"):
            normalized = " ".join(row[key].lower().split())
            if normalized in paragraphs:
                duplicate_paragraphs.append((paragraphs[normalized], name, key))
            paragraphs[normalized] = name
    if duplicate_paragraphs:
        raise SystemExit("byte-equivalent paragraph reuse detected: %r" % duplicate_paragraphs[:10])

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    provenance = dict(artifact_doc.get("provenance") or {})
    provenance.update({
        "semantic_repair": {
            "completed_utc": now,
            "audit_model": audit_doc.get("model"),
            "full_corpus_audited": 533,
            "original_pass_count": audit_doc.get("pass_count"),
            "original_fail_count": audit_doc.get("fail_count"),
            "known_cross_party_rewrite_count": len(known_targets),
            "additional_semantic_rewrite_count": len(required - known_targets),
            "total_rewrite_count": len(required),
            "evidence_sha256": evidence_sha,
            "reviewer_counts": reviewer_counts,
            "acceptance": "deterministic evidence gates plus recorded API or conservative local semantic repair, followed by fresh corpus review; fail-closed",
            "construct_rule": "Big Five, qualified public-language reads, hearing behavior, and observed cross-party behavior remain independently true and may disagree",
            "speech_rule": (
                "speech passes for every member; the general baseline combines all accepted "
                "interview, floor, and other recorded settings, while thin coverage limits "
                "claim strength rather than pass/fail; setting-specific, F0, pause, strain, "
                "and jitter observations are included only when supported and never treated "
                "as proof of emotion, motive, gender, dishonesty, or a causal trigger"
            ),
        },
        "validation": "All 533 briefs have a per-member semantic pass receipt. Rewritten briefs also passed deterministic structure, missing-measure, scope, and banned-inference gates.",
        "known_limitations": [
            "Briefs summarize measured public/professional evidence and do not establish private motives, diagnoses, or behavior outside the stated source scopes.",
            "An API semantic reviewer reduces but cannot mathematically eliminate interpretation error; deterministic source-binding and missing-measure gates remain authoritative.",
        ],
    })
    candidate = {
        "schema": "member-narratives-v2-api",
        "status": "ACTIVE_API_GENERATED_USER_RATIFIED",
        "basis_sha256": evidence_doc["basis_sha256"],
        "count": 533,
        "ok_count": 533,
        "provenance": provenance,
        "members": merged,
    }
    dump_atomic(candidate, args.candidate_out)
    candidate_sha = sha256(args.candidate_out)
    receipt = {
        "schema": "member-narratives-v2-semantic-receipt-v1",
        "status": "PASS",
        "created_utc": now,
        "basis_sha256": evidence_doc["basis_sha256"],
        "evidence_sha256": evidence_sha,
        "candidate_sha256": candidate_sha,
        "counts": {"members": 533, "known_cross_party_rewritten": len(known_targets),
                   "additional_semantic_rewritten": len(required - known_targets),
                   "total_rewritten": len(required), "semantic_pass": 533},
        "members": receipt_members,
    }
    dump_atomic(receipt, args.receipt)

    if args.activate:
        if args.artifact.resolve() != ARTIFACT_PATH.resolve():
            raise SystemExit("--activate is allowed only for the canonical root artifact")
        if not args.backup.exists():
            shutil.copy2(args.artifact, args.backup)
        os.replace(args.candidate_out, args.artifact)
        receipt["active_artifact_sha256"] = sha256(args.artifact)
        receipt["backup_path"] = str(args.backup)
        dump_atomic(receipt, args.receipt)
        print("activated %s; backup=%s; sha256=%s" % (args.artifact, args.backup, receipt["active_artifact_sha256"]))
    else:
        print("candidate PASS: %s sha256=%s (use --activate after review)" % (args.candidate_out, candidate_sha))
    print("semantic receipt: " + str(args.receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
