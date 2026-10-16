# -*- coding: utf-8 -*-
"""Speaker VERIFICATION for the audio-identity layer - never identification.

audio-identity design 2026-07-16 (user-directed build).  verify_unit() answers exactly one
question: is every scored segment of this unit the claimed member's voice?  ALL segments must
reach theta_accept against the claimed member's enrollment embedding (min over segments =
min_cosine); when the rest of the bank is supplied as competitor embeddings, the margin (min over
segments of target cosine minus best competitor cosine) must reach IDENTITY_MIN_MARGIN; the
fraction of unit duration covered by scored segments must reach IDENTITY_MIN_COVERAGE (missing
coverage fails closed).  A min_cosine in [eer, theta_accept) with everything else passing is
INDETERMINATE; any floor failure is REJECT with the failing field evident in "reasons".  A unit is
NEVER reassigned to a better-matching member - there is no winner-picking over members anywhere in
this module; competitors exist only to compute the margin.

theta_accept and theta_eer are NEVER code literals: they load exclusively from the calibrated thresholds
artifact b5_audio_identity_thresholds.json.candidate, and every consumer refuses to run (exit code
2) when that artifact is missing, malformed, or records a calibration stop-bar miss. ``eer_rate``
is the observed error rate; it is never used as a cosine boundary.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from audio_rules import IDENTITY_MIN_COVERAGE, IDENTITY_MIN_MARGIN  # noqa: E402
from members.b5_audio_calibrate import sha256_file  # noqa: E402
from members.b5_audio_embed import cosine  # noqa: E402

THRESHOLDS_SCHEMA = "b5-audio-identity-thresholds-v1"
REASON_NO_ENROLLMENT = "EXCLUDED_NO_ENROLLMENT"
REASON_VERIFY_REJECT = "EXCLUDED_VERIFICATION_REJECT"
REASON_VERIFY_INDETERMINATE = "EXCLUDED_VERIFICATION_INDETERMINATE"


def _refuse(message):
    """Refuse per the thresholds contract: exit code 2, message kept on the exception."""
    sys.stderr.write(message + "\n")
    exc = SystemExit(message)
    exc.code = 2
    raise exc


def load_thresholds(path):
    """Load the calibrated thresholds artifact; refuse (exit 2) when missing or malformed."""
    if not os.path.isfile(path):
        _refuse("REFUSED: thresholds artifact missing at %s; run the calibration step that "
                "writes b5_audio_identity_thresholds.json.candidate first - the cosine "
                "threshold is never a code literal" % path)
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    return validate_thresholds_doc(doc, path)


def validate_thresholds_doc(doc, source="<memory>"):
    """Validate a thresholds object and return it; only CALIBRATION_PASS is consumable."""
    if not isinstance(doc, dict) or doc.get("schema") != THRESHOLDS_SCHEMA:
        _refuse("REFUSED: thresholds artifact at %s has schema %r, expected %r" %
                (source, doc.get("schema") if isinstance(doc, dict) else None,
                 THRESHOLDS_SCHEMA))
    if doc.get("status") != "CALIBRATION_PASS":
        _refuse("REFUSED: thresholds artifact at %s has non-passing status %r" %
                (source, doc.get("status")))
    score_kind = doc.get("score_kind") or "cosine"
    if score_kind != "cosine":
        _refuse("REFUSED: thresholds artifact at %s has score_kind %r; the centroid "
                "verifier requires cosine thresholds" % (source, score_kind))
    for key in ("theta_accept", "theta_enroll", "theta_eer", "eer_rate"):
        if not isinstance(doc.get(key), (int, float)):
            _refuse("REFUSED: thresholds artifact at %s lacks numeric %r" % (source, key))
    theta_eer = float(doc["theta_eer"])
    theta_accept = float(doc["theta_accept"])
    theta_enroll = float(doc["theta_enroll"])
    eer_rate = float(doc["eer_rate"])
    if not (-1.0 <= theta_eer <= theta_accept <= 1.0):
        _refuse("REFUSED: thresholds artifact at %s violates "
                "-1 <= theta_eer <= theta_accept <= 1" % source)
    if not (-1.0 <= theta_enroll <= 1.0):
        _refuse("REFUSED: thresholds artifact at %s has theta_enroll outside [-1,1]" %
                source)
    if not (0.0 <= eer_rate <= 1.0):
        _refuse("REFUSED: thresholds artifact at %s has eer_rate outside [0,1]" % source)
    if eer_rate > 0.05:
        _refuse("REFUSED: thresholds artifact at %s exceeds the 5%% EER stop bar" % source)
    return doc


def member_embedding(bank, member, allow_unratified=False):
    """Enrollment embedding, or None when absent/unratified under the fail-closed default."""
    row = (bank.get("members") or {}).get(member)
    if not row or not row.get("embedding"):
        return None
    if not allow_unratified and not (row.get("ratified") or []):
        return None
    return np.asarray(row["embedding"], dtype=np.float64)


def exclusion_record(unit_id, member, reason_code):
    """Attribution-verdict-shaped EXCLUDED record for callers (e.g. no enrollment bank entry)."""
    return {"unit_id": unit_id, "member": member if reason_code != REASON_NO_ENROLLMENT else None,
            "status": "EXCLUDED", "reason_code": reason_code,
            "check_t": None, "check_a": None}


def verify_unit(target_embedding, segment_embeddings, thresholds,
                competitor_embeddings=None, coverage=None):
    """Verify one unit against ONE claimed member. Returns the decision record.

    {"decision": "ACCEPT"|"REJECT"|"INDETERMINATE", "min_cosine": float,
     "margin": float|None, "coverage": float, "reasons": [str, ...]}
    """
    theta_accept = float(thresholds["theta_accept"])
    theta_eer = float(thresholds["theta_eer"])
    reasons = []
    segments = [np.asarray(seg, dtype=np.float64) for seg in (segment_embeddings or [])]
    # NaN/inf make every '<' False and min() drop NaN, so fail closed up front.
    competitor_arrays = [np.asarray(c, dtype=np.float64) for c in (competitor_embeddings or [])]
    if (not all(np.isfinite(a).all() for a in segments + competitor_arrays)
            or not np.isfinite(np.asarray(target_embedding, dtype=np.float64)).all()
            or (coverage is not None and not math.isfinite(float(coverage)))):
        return {"decision": "REJECT", "min_cosine": 0.0, "margin": None,
                "coverage": 0.0, "reasons": ["non_finite_input"]}
    if not segments:
        return {"decision": "REJECT", "min_cosine": 0.0, "margin": None,
                "coverage": float(coverage or 0.0), "reasons": ["no_scored_segments"]}
    target = np.asarray(target_embedding, dtype=np.float64)
    target_cosines = [cosine(seg, target) for seg in segments]
    min_cosine = min(target_cosines)
    margin = None
    if competitor_embeddings:
        competitors = [np.asarray(c, dtype=np.float64) for c in competitor_embeddings]
        margin = min(tc - max(cosine(seg, c) for c in competitors)
                     for seg, tc in zip(segments, target_cosines))
    if not math.isfinite(min_cosine) or (margin is not None and not math.isfinite(margin)):
        return {"decision": "REJECT", "min_cosine": 0.0, "margin": None,
                "coverage": 0.0, "reasons": ["non_finite_input"]}
    if coverage is None:
        coverage_value = 0.0
        reasons.append("coverage_missing_fails_closed")
    else:
        coverage_value = float(coverage)
        if coverage_value < IDENTITY_MIN_COVERAGE:
            reasons.append("coverage %.3f < IDENTITY_MIN_COVERAGE %.3f"
                           % (coverage_value, IDENTITY_MIN_COVERAGE))
    if margin is not None and margin < IDENTITY_MIN_MARGIN:
        reasons.append("margin %.3f < IDENTITY_MIN_MARGIN %.3f" % (margin, IDENTITY_MIN_MARGIN))
    if min_cosine < theta_eer:
        reasons.append("min_cosine %.3f < theta_eer %.3f" % (min_cosine, theta_eer))
    if reasons:
        decision = "REJECT"
    elif min_cosine < theta_accept:
        decision = "INDETERMINATE"
        reasons.append("min_cosine %.3f in [theta_eer %.3f, theta_accept %.3f)"
                       % (min_cosine, theta_eer, theta_accept))
    else:
        decision = "ACCEPT"
    return {"decision": decision, "min_cosine": float(min_cosine),
            "margin": None if margin is None else float(margin),
            "coverage": coverage_value, "reasons": reasons}


def main(argv=None):
    try:
        from paths import ROOT
    except ImportError:  # paths.py always exists in-tree; keep the CLI importable anywhere
        ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = argparse.ArgumentParser(
        description="Verify units against ONE claimed member each (never identification).")
    parser.add_argument("--thresholds",
                        default=os.path.join(ROOT, "b5_audio_identity_thresholds.json.candidate"),
                        help="calibrated thresholds artifact; consumers refuse to run without it")
    parser.add_argument("--bank", required=True, help="b5_audio_enroll_bank.json.candidate")
    parser.add_argument("--units", required=True,
                        help="JSON list: [{unit_id, member, coverage, segment_embeddings, "
                             "competitor_members?}]")
    parser.add_argument("--out", required=True,
                        help="explicit output path; must end in .json.candidate")
    parser.add_argument("--allow-unratified-candidate", action="store_true",
                        help="pilot-only override; default refuses enrollment entries whose "
                             "ratified list is empty")
    args = parser.parse_args(argv)
    if not args.out.endswith(".json.candidate"):
        raise SystemExit("REFUSED: --out must end in .json.candidate: %s" % args.out)
    from moneyball_core import dump_atomic
    thresholds = load_thresholds(args.thresholds)
    thresholds_sha256 = sha256_file(args.thresholds)
    with open(args.bank, encoding="utf-8") as handle:
        bank = json.load(handle)
    if bank.get("thresholds_sha256") != thresholds_sha256:
        _refuse("REFUSED: enrollment bank thresholds_sha256 does not match the exact "
                "calibrated thresholds artifact supplied to verification")
    bank_sha256 = sha256_file(args.bank)
    with open(args.units, encoding="utf-8") as handle:
        units = json.load(handle)
    records = []
    for unit in units:
        unit_id = unit["unit_id"]
        member = unit["member"]
        target = member_embedding(
            bank, member, allow_unratified=args.allow_unratified_candidate)
        if target is None:
            records.append(exclusion_record(unit_id, member, REASON_NO_ENROLLMENT))
            continue
        competitor_names = unit.get("competitor_members")
        if competitor_names is None:
            competitor_names = [name for name in sorted(bank.get("members") or {})
                                if name != member]
        competitors = [
            member_embedding(bank, name, allow_unratified=args.allow_unratified_candidate)
            for name in competitor_names
            if member_embedding(
                bank, name, allow_unratified=args.allow_unratified_candidate) is not None
        ]
        check_a = verify_unit(target, unit.get("segment_embeddings") or [], thresholds,
                              competitor_embeddings=competitors or None,
                              coverage=unit.get("coverage"))
        if check_a["decision"] == "ACCEPT":
            records.append({"unit_id": unit_id, "member": member, "status": "ELIGIBLE",
                            "reason_code": "VERIFIED", "check_t": None, "check_a": check_a})
        else:
            reason = (REASON_VERIFY_INDETERMINATE if check_a["decision"] == "INDETERMINATE"
                      else REASON_VERIFY_REJECT)
            records.append({"unit_id": unit_id, "member": member, "status": "EXCLUDED",
                            "reason_code": reason, "check_t": None, "check_a": check_a})
    out_doc = {"schema": "b5-audio-identity-verdicts-v1",
               "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
               "thresholds_path": args.thresholds,
               "thresholds_sha256": thresholds_sha256,
               "bank_sha256": bank_sha256,
               "allow_unratified_candidate": args.allow_unratified_candidate,
               "verdicts": records}
    dump_atomic(out_doc, args.out, indent=1)
    eligible = sum(1 for row in records if row["status"] == "ELIGIBLE")
    print("verify: %d unit(s), %d ELIGIBLE, %d EXCLUDED"
          % (len(records), eligible, len(records) - eligible))
    print("wrote %s" % args.out)


if __name__ == "__main__":
    main()
