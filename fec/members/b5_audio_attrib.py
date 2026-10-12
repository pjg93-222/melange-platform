# -*- coding: utf-8 -*-
"""Attribution AND-rule for the speaker-identity layer, audio-identity design 2026-07-16
(user-directed build).

Pure decision layer: consumes PRE-COMPUTED inputs -- the transcript-identity resolution (check T)
and the acoustic-verification decision record (check A) -- and applies the fail-closed AND-rule.
A unit of audio is attributable to a member ONLY when both independent checks name the SAME member
and the acoustic decision is ACCEPT.  Every other combination is EXCLUDED with an auditable reason
code.  Verification, never identification: an acoustic REJECT (or an ACCEPT against the wrong
enrollment) excludes the unit and appends it to the mismatch ledger for human review -- the unit is
never reassigned to a better-matching member.

A witness turn whose voice is ACCEPTED as an enrolled member poisons the WHOLE hearing (the diarization/
alignment for that event can no longer be trusted), so ``hearing_excluded`` and ``attribute_units``
propagate that exclusion across every unit sharing the hearing.

This module imports nothing from the other identity modules; it codes against the frozen record
contracts only.  It does not consume the calibrated cosine threshold at all -- the ACCEPT/REJECT/
INDETERMINATE decision arrives pre-computed, so no threshold literal may appear here.

Outputs are RESEARCH candidates consumed by NOTHING until user ratification; every write goes
through ``dump_atomic`` to an explicit path that must end in .json.candidate and must not be a
protected live artifact (see ``guard_out_path``).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from moneyball_core import dump_atomic
from audio_rules import IDENTITY_MIN_COVERAGE, IDENTITY_MIN_MARGIN

CORPORA = ("houselive", "wj", "hearings", "interviews")
UNRESOLVED_TOKENS = ("UNIDENTIFIED", "UNRESOLVABLE", "UNRESOLVED", "UNKNOWN")
# Live narrative-layer artifacts this research path must never write (runtime guard + source rule).
PROTECTED_BASENAMES = ("b5_audio_reviews_v2.json", "b5_audio_descriptors_v2.json")


def guard_out_path(path):
    """Refuse protected live artifacts and any non-.candidate output path.  Returns None if OK."""
    base = os.path.basename(str(path))
    for protected in PROTECTED_BASENAMES:
        if base == protected or base.startswith(protected):
            raise ValueError("refusing to write protected live artifact basename: %s" % base)
    if not base.endswith(".json.candidate"):
        raise ValueError("identity outputs must end in .json.candidate, got: %s" % base)
    return None


def _no_transcript_identity(check_t_member):
    if check_t_member is None:
        return True
    name = str(check_t_member).strip()
    return (not name) or name.upper() in UNRESOLVED_TOKENS


def _verdict(unit, status, reason_code, member=None):
    check_t = unit.get("check_t_member")
    return {
        "unit_id": unit.get("unit_id"),
        "member": member,
        "status": status,
        "reason_code": reason_code,
        "check_t": check_t if check_t is not None else None,
        "check_a": unit.get("acoustic"),
    }


def attribute_unit(unit):
    """Apply the fail-closed AND-rule to one unit; return the frozen attribution verdict record.

    Precedence (most poisonous first): witness-member collision, overlap/dash-interrupt,
    missing transcript identity, missing enrollment, then the acoustic decision.
    """
    acoustic = unit.get("acoustic")
    if (unit.get("witness_turn") and unit.get("acoustic_member")
            and acoustic and acoustic.get("decision") == "ACCEPT"):
        # A witness voice matching an enrolled member: the event's speaker labels are suspect.
        return _verdict(unit, "EXCLUDED", "witness_member_collision")
    if unit.get("witness_turn"):
        # Witnesses are explicit nonmembers for this measurement. A rejected/indeterminate
        # comparison is not a collision and must not poison otherwise valid member turns.
        return _verdict(unit, "EXCLUDED", "nonmember_witness")
    if unit.get("dash_interrupt") or unit.get("overlap"):
        return _verdict(unit, "EXCLUDED", "overlap_or_interrupt")
    if _no_transcript_identity(unit.get("check_t_member")):
        return _verdict(unit, "EXCLUDED", "no_transcript_identity")
    if not unit.get("enrolled"):
        return _verdict(unit, "EXCLUDED", "no_enrollment")

    if not acoustic:
        return _verdict(unit, "EXCLUDED", "acoustic_indeterminate")
    decision = acoustic.get("decision")
    if decision == "REJECT":
        return _verdict(unit, "EXCLUDED", "acoustic_mismatch")
    if decision == "INDETERMINATE":
        explicit = acoustic.get("reason_code")
        if explicit:
            return _verdict(unit, "EXCLUDED", str(explicit))
        coverage = acoustic.get("coverage")
        if coverage is not None and coverage < IDENTITY_MIN_COVERAGE:
            return _verdict(unit, "EXCLUDED", "coverage_below_min")
        margin = acoustic.get("margin")
        if margin is not None and margin < IDENTITY_MIN_MARGIN:
            return _verdict(unit, "EXCLUDED", "margin_below_min")
        return _verdict(unit, "EXCLUDED", "acoustic_indeterminate")
    if decision == "ACCEPT":
        member = str(unit.get("check_t_member")).strip()
        if unit.get("acoustic_member") == member:
            return _verdict(unit, "ELIGIBLE", "dual_check_agree", member=member)
        # ACCEPT against a DIFFERENT enrollment: verification-never-identification.
        return _verdict(unit, "EXCLUDED", "acoustic_mismatch")
    # Unknown/absent decision value: fail closed.
    return _verdict(unit, "EXCLUDED", "acoustic_indeterminate")


def hearing_excluded(units):
    """True when any unit of this hearing carries a witness-member collision."""
    return any(attribute_unit(u)["reason_code"] == "witness_member_collision" for u in units)


def attribute_units(units):
    """Per-unit verdicts (input order) with whole-hearing witness-collision exclusion applied.

    Hearings-corpus units are grouped by their optional ``hearing_id`` (absent ids share one
    group); any collision in a group flips every otherwise-ELIGIBLE verdict in that group to
    EXCLUDED / hearing_witness_collision.  Already-excluded verdicts keep their own reasons.
    """
    verdicts = [attribute_unit(u) for u in units]
    poisoned_hearings = set()
    for unit, verdict in zip(units, verdicts):
        if unit.get("corpus") == "hearings" and verdict["reason_code"] == "witness_member_collision":
            poisoned_hearings.add(unit.get("hearing_id") or "")
    if poisoned_hearings:
        for unit, verdict in zip(units, verdicts):
            if (unit.get("corpus") == "hearings"
                    and (unit.get("hearing_id") or "") in poisoned_hearings
                    and verdict["status"] == "ELIGIBLE"):
                verdict["status"] = "EXCLUDED"
                verdict["reason_code"] = "hearing_witness_collision"
                verdict["member"] = None
    return verdicts


def _counts(verdicts):
    by_reason = {}
    for verdict in verdicts:
        by_reason[verdict["reason_code"]] = by_reason.get(verdict["reason_code"], 0) + 1
    return {
        "total": len(verdicts),
        "eligible": sum(1 for v in verdicts if v["status"] == "ELIGIBLE"),
        "excluded": sum(1 for v in verdicts if v["status"] == "EXCLUDED"),
        "by_reason": by_reason,
    }


def _write(obj, path, written):
    guard_out_path(path)
    dump_atomic(obj, path, sort_keys=True, indent=1)
    written.append(path)


def process_units(units, out_prefix):
    """Attribute every unit and write per-corpus verdict artifacts plus the two ledgers.

    Writes <out_prefix>_<corpus>.json.candidate for each corpus present, plus
    <out_prefix>_rejects_ledger.json.candidate (every EXCLUDED verdict) and
    <out_prefix>_mismatch_ledger.json.candidate (acoustic_mismatch units only, for human review).
    Deterministic: same inputs produce byte-identical artifacts.
    """
    for unit in units:
        if unit.get("corpus") not in CORPORA:
            raise ValueError("unknown corpus %r on unit %r" % (unit.get("corpus"),
                                                               unit.get("unit_id")))
    verdicts = attribute_units(units)
    by_corpus = {}
    for unit, verdict in zip(units, verdicts):
        by_corpus.setdefault(unit["corpus"], []).append(verdict)

    written = []
    for corpus in sorted(by_corpus):
        corpus_verdicts = by_corpus[corpus]
        artifact = {
            "schema": "b5-audio-attrib-v1",
            "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
            "corpus": corpus,
            "verdicts": corpus_verdicts,
            "counts": _counts(corpus_verdicts),
        }
        _write(artifact, "%s_%s.json.candidate" % (out_prefix, corpus), written)

    rejects = [dict(corpus=unit["corpus"], **verdict)
               for unit, verdict in zip(units, verdicts) if verdict["status"] == "EXCLUDED"]
    mismatches = [dict(corpus=unit["corpus"], acoustic_member=unit.get("acoustic_member"),
                       **verdict)
                  for unit, verdict in zip(units, verdicts)
                  if verdict["reason_code"] == "acoustic_mismatch"]
    _write({"schema": "b5-audio-attrib-rejects-v1",
            "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
            "rows": rejects},
           "%s_rejects_ledger.json.candidate" % out_prefix, written)
    _write({"schema": "b5-audio-attrib-mismatch-v1",
            "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
            "rows": mismatches},
           "%s_mismatch_ledger.json.candidate" % out_prefix, written)
    return {"written": written, "verdicts": verdicts,
            "counts": {corpus: _counts(rows) for corpus, rows in sorted(by_corpus.items())}}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Apply the fail-closed attribution AND-rule to pre-computed identity checks "
                    "(research candidate outputs only).")
    parser.add_argument("--units", required=True,
                        help="JSON file: list of unit records (frozen unit contract)")
    parser.add_argument("--out-prefix", default=None,
                        help="explicit output prefix; artifacts are <prefix>_<corpus>"
                             ".json.candidate plus the rejects/mismatch ledgers")
    args = parser.parse_args(argv)
    out_prefix = args.out_prefix
    if out_prefix is None:
        from paths import ROOT
        out_prefix = os.path.join(ROOT, "audio_pilot", "b5_audio_attrib")
    import json
    with open(args.units, encoding="utf-8") as handle:
        units = json.load(handle)
    result = process_units(units, out_prefix)
    for corpus, counts in sorted(result["counts"].items()):
        print("%s: %d units, %d eligible, %d excluded"
              % (corpus, counts["total"], counts["eligible"], counts["excluded"]))
    for path in result["written"]:
        print("wrote %s" % path)


if __name__ == "__main__":
    main()
