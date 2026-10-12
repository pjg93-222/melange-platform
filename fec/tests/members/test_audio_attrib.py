# -*- coding: utf-8 -*-
"""TDD contracts for the attribution AND-rule, audio-identity design 2026-07-16 (user-directed build).

RED-first: written before fec/members/b5_audio_attrib.py existed.  The module under test is a PURE decision
layer: it consumes pre-computed transcript-identity (check T) and acoustic-verification (check A)
inputs and applies the fail-closed AND-rule -- a unit is attributable only when BOTH independent
checks name the SAME member and the acoustic decision is ACCEPT.  Verification, never
identification: a mismatching unit is EXCLUDED and ledgered, never reassigned to a better match.

Dependency-light: numpy-free, network-free, no model weights.  All fixture units are plain dicts
built to the frozen cross-module record contracts.  Artifacts land in a tempfile scratch dir and
must be .json.candidate ONLY.

Standalone: python fec/tests/members/test_audio_attrib.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from pathlib import Path

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

import members.b5_audio_attrib as ATTRIB  # noqa: E402
from audio_rules import IDENTITY_MIN_COVERAGE, IDENTITY_MIN_MARGIN  # noqa: E402

VERDICT_KEYS = {"unit_id", "member", "status", "reason_code", "check_t", "check_a"}


def make_unit(**overrides):
    """A unit that passes every gate (dual-check agreement) unless a field is overridden."""
    base = {
        "unit_id": "hearing-001:turn-04",
        "corpus": "hearings",
        "check_t_member": "Don Bacon",
        "acoustic": {"decision": "ACCEPT", "min_cosine": 0.72, "margin": 0.25, "coverage": 0.95},
        "acoustic_member": "Don Bacon",
        "enrolled": True,
        "witness_turn": False,
        "dash_interrupt": False,
        "overlap": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------- truth table, case by case

def test_dual_check_agree_is_the_only_eligible_path():
    verdict = ATTRIB.attribute_unit(make_unit())
    assert set(verdict) == VERDICT_KEYS, "verdict keys must match the frozen contract: %r" % sorted(verdict)
    assert verdict["status"] == "ELIGIBLE", verdict
    assert verdict["reason_code"] == "dual_check_agree", verdict
    assert verdict["member"] == "Don Bacon", verdict
    assert verdict["unit_id"] == "hearing-001:turn-04", verdict


def test_no_transcript_identity_excludes():
    for check_t in (None, "", "UNIDENTIFIED", "unidentified", "UNRESOLVABLE"):
        verdict = ATTRIB.attribute_unit(make_unit(check_t_member=check_t))
        assert verdict["status"] == "EXCLUDED", (check_t, verdict)
        assert verdict["reason_code"] == "no_transcript_identity", (check_t, verdict)
        assert verdict["member"] is None, (check_t, verdict)


def test_not_enrolled_excludes():
    verdict = ATTRIB.attribute_unit(make_unit(enrolled=False))
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "no_enrollment", verdict
    assert verdict["member"] is None, verdict


def test_acoustic_reject_is_mismatch_never_reassigned():
    unit = make_unit(acoustic={"decision": "REJECT", "min_cosine": 0.21, "margin": 0.02,
                               "coverage": 0.95})
    verdict = ATTRIB.attribute_unit(unit)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "acoustic_mismatch", verdict
    assert verdict["member"] is None, verdict


def test_accept_naming_a_different_member_is_still_a_mismatch():
    # Verification-never-identification: an ACCEPT against the WRONG enrollment must not
    # reassign the unit to the acoustically-matching member.
    unit = make_unit(acoustic_member="Darren Soto")
    verdict = ATTRIB.attribute_unit(unit)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "acoustic_mismatch", verdict
    assert verdict["member"] is None, verdict


def test_acoustic_indeterminate_excludes():
    unit = make_unit(acoustic={"decision": "INDETERMINATE", "min_cosine": 0.55, "margin": None,
                               "coverage": 0.90})
    verdict = ATTRIB.attribute_unit(unit)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "acoustic_indeterminate", verdict


def test_missing_acoustic_record_fails_closed():
    verdict = ATTRIB.attribute_unit(make_unit(acoustic=None))
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "acoustic_indeterminate", verdict
    assert verdict["check_a"] is None, verdict


def test_coverage_and_margin_failures_surface_their_reason_codes():
    low_cov = make_unit(acoustic={"decision": "INDETERMINATE", "min_cosine": 0.70, "margin": 0.30,
                                  "coverage": IDENTITY_MIN_COVERAGE - 0.10})
    verdict = ATTRIB.attribute_unit(low_cov)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "coverage_below_min", verdict

    low_margin = make_unit(acoustic={"decision": "INDETERMINATE", "min_cosine": 0.70,
                                     "margin": IDENTITY_MIN_MARGIN - 0.05, "coverage": 0.95})
    verdict = ATTRIB.attribute_unit(low_margin)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "margin_below_min", verdict

    explicit = make_unit(acoustic={"decision": "INDETERMINATE", "min_cosine": 0.70, "margin": 0.30,
                                   "coverage": 0.95, "reason_code": "verifier_specific_reason"})
    verdict = ATTRIB.attribute_unit(explicit)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "verifier_specific_reason", verdict


def test_overlap_or_dash_interrupt_excludes():
    for field in ("dash_interrupt", "overlap"):
        verdict = ATTRIB.attribute_unit(make_unit(**{field: True}))
        assert verdict["status"] == "EXCLUDED", (field, verdict)
        assert verdict["reason_code"] == "overlap_or_interrupt", (field, verdict)


def test_witness_member_collision_excludes_and_outranks_overlap():
    unit = make_unit(witness_turn=True, overlap=True)
    verdict = ATTRIB.attribute_unit(unit)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "witness_member_collision", verdict
    # A witness only poisons the hearing when the acoustic verifier ACCEPTS a member match.
    rejected_match = make_unit(
        witness_turn=True,
        acoustic={"decision": "REJECT", "min_cosine": 0.21, "margin": 0.02, "coverage": 0.95},
    )
    verdict = ATTRIB.attribute_unit(rejected_match)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "nonmember_witness", verdict
    # A witness turn never becomes member-eligible, even if malformed upstream fields still name
    # a member. It is excluded locally without poisoning the whole hearing unless CHECK-A accepts.
    plain_witness = make_unit(witness_turn=True, acoustic_member=None, acoustic=None,
                              check_t_member="WITNESS Jane Doe", enrolled=False)
    verdict = ATTRIB.attribute_unit(plain_witness)
    assert verdict["status"] == "EXCLUDED", verdict
    assert verdict["reason_code"] == "nonmember_witness", verdict


# ---------------------------------------------------------------- whole-hearing exclusion

def hearing_units():
    good = make_unit(unit_id="hearing-009:turn-01", hearing_id="hearing-009")
    witness = make_unit(unit_id="hearing-009:turn-07", hearing_id="hearing-009",
                        witness_turn=True, check_t_member="WITNESS Jane Doe")
    other = make_unit(unit_id="hearing-010:turn-02", hearing_id="hearing-010",
                      check_t_member="Darren Soto", acoustic_member="Darren Soto")
    return good, witness, other


def test_hearing_excluded_flags_any_witness_member_collision():
    good, witness, other = hearing_units()
    assert ATTRIB.hearing_excluded([good, witness]) is True
    assert ATTRIB.hearing_excluded([good]) is False
    assert ATTRIB.hearing_excluded([other]) is False


def test_whole_hearing_exclusion_takes_down_otherwise_eligible_units():
    good, witness, other = hearing_units()
    verdicts = {v["unit_id"]: v for v in ATTRIB.attribute_units([good, witness, other])}
    assert verdicts["hearing-009:turn-07"]["reason_code"] == "witness_member_collision"
    poisoned = verdicts["hearing-009:turn-01"]
    assert poisoned["status"] == "EXCLUDED", poisoned
    assert poisoned["reason_code"] == "hearing_witness_collision", poisoned
    assert poisoned["member"] is None, poisoned
    untouched = verdicts["hearing-010:turn-02"]
    assert untouched["status"] == "ELIGIBLE", untouched
    assert untouched["member"] == "Darren Soto", untouched


# ---------------------------------------------------------------- review-prefill evidence

def test_verdicts_carry_the_evidence_a_human_review_needs():
    verdict = ATTRIB.attribute_unit(make_unit())
    assert verdict["check_t"] == "Don Bacon", verdict
    check_a = verdict["check_a"]
    assert check_a is not None
    assert check_a["min_cosine"] == 0.72, check_a
    assert check_a["margin"] == 0.25, check_a
    assert check_a["coverage"] == 0.95, check_a
    # an excluded mismatch keeps the same evidence even though member is null
    verdict = ATTRIB.attribute_unit(make_unit(
        acoustic={"decision": "REJECT", "min_cosine": 0.21, "margin": 0.02, "coverage": 0.95}))
    assert verdict["check_t"] == "Don Bacon", verdict
    assert verdict["check_a"]["min_cosine"] == 0.21, verdict


# ---------------------------------------------------------------- artifacts, guard, determinism

def batch_units():
    good, witness, _ = hearing_units()
    return [
        good,
        witness,
        make_unit(unit_id="wj-001:turn-03", corpus="wj", hearing_id=None,
                  check_t_member="Darren Soto", acoustic_member="Darren Soto"),
        make_unit(unit_id="wj-002:turn-05", corpus="wj", hearing_id=None,
                  check_t_member="Darren Soto", acoustic_member="Darren Soto",
                  acoustic={"decision": "REJECT", "min_cosine": 0.18, "margin": 0.01,
                            "coverage": 0.92}),
        make_unit(unit_id="wj-003:turn-01", corpus="wj", hearing_id=None,
                  check_t_member="UNIDENTIFIED", acoustic=None, acoustic_member=None),
    ]


def read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_process_units_writes_only_candidate_artifacts_with_matching_counts():
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "b5_audio_attrib")
        result = ATTRIB.process_units(batch_units(), prefix)
        written = sorted(os.listdir(tmp))
        assert written, "process_units wrote nothing"
        for name in written:
            assert name.endswith(".json.candidate"), "non-candidate artifact written: %s" % name
        expected = {"b5_audio_attrib_hearings.json.candidate",
                    "b5_audio_attrib_wj.json.candidate",
                    "b5_audio_attrib_rejects_ledger.json.candidate",
                    "b5_audio_attrib_mismatch_ledger.json.candidate"}
        assert set(written) == expected, written
        assert sorted(os.path.basename(p) for p in result["written"]) == sorted(expected)

        for corpus, n_units in (("hearings", 2), ("wj", 3)):
            artifact = read_json(os.path.join(tmp, "b5_audio_attrib_%s.json.candidate" % corpus))
            assert artifact["schema"] == "b5-audio-attrib-v1", artifact["schema"]
            assert artifact["status"] == "RESEARCH_CANDIDATE_NOT_CONSUMED"
            verdicts = artifact["verdicts"]
            assert len(verdicts) == n_units, (corpus, len(verdicts))
            counts = artifact["counts"]
            assert counts["total"] == len(verdicts), counts
            assert counts["eligible"] == sum(1 for v in verdicts if v["status"] == "ELIGIBLE")
            assert counts["excluded"] == sum(1 for v in verdicts if v["status"] == "EXCLUDED")
            by_reason = {}
            for v in verdicts:
                by_reason[v["reason_code"]] = by_reason.get(v["reason_code"], 0) + 1
            assert counts["by_reason"] == by_reason, (counts["by_reason"], by_reason)

        # the whole hearing-009 is excluded inside the artifact too
        hearings = read_json(os.path.join(tmp, "b5_audio_attrib_hearings.json.candidate"))
        assert all(v["status"] == "EXCLUDED" for v in hearings["verdicts"]), hearings["verdicts"]

        rejects = read_json(os.path.join(tmp, "b5_audio_attrib_rejects_ledger.json.candidate"))
        reject_ids = {row["unit_id"] for row in rejects["rows"]}
        assert reject_ids == {"hearing-009:turn-01", "hearing-009:turn-07",
                              "wj-002:turn-05", "wj-003:turn-01"}, reject_ids

        mismatch = read_json(os.path.join(tmp, "b5_audio_attrib_mismatch_ledger.json.candidate"))
        mismatch_ids = {row["unit_id"] for row in mismatch["rows"]}
        assert mismatch_ids == {"wj-002:turn-05"}, mismatch_ids
        row = mismatch["rows"][0]
        assert row["check_t"] == "Darren Soto", row
        assert row["check_a"]["min_cosine"] == 0.18, row


def test_runtime_guard_refuses_the_protected_live_artifacts():
    for bad in ("b5_audio_reviews_v2.json", "b5_audio_descriptors_v2.json",
                "b5_audio_reviews_v2.json.candidate", "b5_audio_descriptors_v2.json.candidate"):
        raised = False
        try:
            ATTRIB.guard_out_path(os.path.join("anywhere", bad))
        except Exception:
            raised = True
        assert raised, "guard_out_path accepted protected basename %s" % bad
    # non-.candidate output paths are refused too
    raised = False
    try:
        ATTRIB.guard_out_path("some_output.json")
    except Exception:
        raised = True
    assert raised, "guard_out_path accepted a non-.candidate output path"
    # a normal candidate path passes
    assert ATTRIB.guard_out_path("b5_audio_attrib_wj.json.candidate") is None


def test_module_source_has_no_write_reference_to_protected_artifacts():
    source = (FEC / "members" / "b5_audio_attrib.py").read_text(encoding="utf-8-sig")
    for name in ("b5_audio_reviews_v2.json", "b5_audio_descriptors_v2.json"):
        assert name in source, "guard must know the protected name %s" % name
        for lineno, line in enumerate(source.splitlines(), 1):
            if name in line:
                assert "dump_atomic" not in line and "open(" not in line, \
                    "line %d writes to protected artifact %s: %r" % (lineno, name, line)


def test_process_units_is_deterministic_and_idempotent():
    with tempfile.TemporaryDirectory() as tmp:
        prefix = os.path.join(tmp, "b5_audio_attrib")
        ATTRIB.process_units(batch_units(), prefix)
        first = {name: (Path(tmp) / name).read_bytes() for name in os.listdir(tmp)}
        ATTRIB.process_units(batch_units(), prefix)
        second = {name: (Path(tmp) / name).read_bytes() for name in os.listdir(tmp)}
        assert first.keys() == second.keys()
        for name in first:
            assert first[name] == second[name], "artifact %s is not byte-identical on re-run" % name


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
        except Exception:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
        else:
            passed += 1
            print("PASS %s" % fn.__name__)
    print("test_audio_attrib: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
