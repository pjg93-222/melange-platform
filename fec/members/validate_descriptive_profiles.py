"""Pure, fail-closed publication checks for descriptive profile prose.

The receipt attests reproducible template/length checks, not a human review of audio.
"""
SCHEMA = "member-narratives-v3-descriptive"
STATUS = "ACTIVE_GROUNDED_DESCRIPTIVE"
CONTRACT = "descriptive-public-record-v1"

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))   # fec/, for roster (appended: never shadows a caller's modules)
import roster  # noqa: E402

# Publication length bound.  These two numbers do different jobs and only one of them
# is an editorial choice.
#
#   MIN_WORDS is a STRUCTURAL tripwire.  A profile that falls under it has lost a
#   paragraph -- truncated, empty, or never composed -- and must not publish.  The
#   shipped gate test (fec/tests/surfaces/test_descriptive_profile_publication.py, the
#   `p1="one word", words=136` case) depends on exactly this.
#
#   MAX_WORDS was an editorial cap of 250 until 2026-09-07, when Paul ruled that word
#   count does not matter.  Enforcing 250 meant a mandatory sentence added for one
#   population -- the second audio layer, for the 74 members whose only audio is a
#   verified voiceprint -- could push a wide profile over the line and make
#   build_rows() raise, failing the whole regeneration on a length budget.
#   The bound is kept only as a RUNAWAY guard: 400 is ~1.7x the widest composed profile
#   (237 words, Hillary Scholten) and ~1.6x the widest editorial override (243 words,
#   Alex Padilla), so it leaves room for several more mandatory sentences while still
#   catching a duplicated paragraph or an unbounded loop.  It is not a promise about
#   profile length; do not restore a tighter cap without a ruling.
MIN_WORDS = 200
MAX_WORDS = 400


def word_count(row):
    return len(" ".join(row.get(key, "") for key in ("p1", "p2", "p3")).split())


def validate_descriptive_profiles(document, receipt, *, basis_sha256,
                                  evidence_sha256, active_artifact_sha256,
                                  member_names):
    errors = []
    if not isinstance(document, dict) or not isinstance(receipt, dict):
        return ["profile artifact and receipt must be objects"]
    expected = set(member_names)
    if len(expected) != roster.count():
        errors.append("expected roster must contain %d unique members" % roster.count())
    if document.get("schema") != SCHEMA or document.get("status") != STATUS:
        errors.append("unrecognized descriptive producer")
    if document.get("contract_version") != CONTRACT:
        errors.append("descriptive contract mismatch")
    if (receipt.get("schema") != "member-narratives-v3-descriptive-receipt-v1"
            or receipt.get("status") != "PASS"
            or receipt.get("contract_version") != CONTRACT):
        errors.append("missing descriptive validation receipt")
    for key, expected_hash in (("basis_sha256", basis_sha256),
                               ("evidence_sha256", evidence_sha256)):
        if not expected_hash or document.get(key) != expected_hash or receipt.get(key) != expected_hash:
            errors.append(key + " mismatch")
    if not active_artifact_sha256 or receipt.get("active_artifact_sha256") != active_artifact_sha256:
        errors.append("active artifact hash mismatch")
    members = document.get("members")
    if not isinstance(members, dict):
        return errors + ["missing members object"]
    if set(members) != expected:
        errors.append("profile roster mismatch")
    if document.get("count") != len(expected) or document.get("ok_count") != len(expected):
        errors.append("artifact counts mismatch")
    counts = receipt.get("counts") or {}
    for key in ("profiles", "word_count_pass", "grounded_template_pass"):
        if counts.get(key) != len(expected):
            errors.append("receipt count mismatch: " + key)
    for name, row in members.items():
        if not isinstance(row, dict):
            errors.append(name + ": invalid row")
            continue
        paragraphs = [row.get(key) for key in ("p1", "p2", "p3")]
        if not all(isinstance(p, str) and p.strip() for p in paragraphs):
            errors.append(name + ": missing paragraph")
            continue
        count = word_count(row)
        if not MIN_WORDS <= count <= MAX_WORDS or row.get("words") != count:
            errors.append(name + ": invalid word count")
        if row.get("ok") is not True or row.get("format") != "descriptive-v3":
            errors.append(name + ": invalid descriptive row status")
        if "meeting_playbook" in row:
            errors.append(name + ": legacy meeting playbook prohibited")
        if row.get("recurring_audio_pattern_claimed") is not False:
            errors.append(name + ": recurring audio authority not established")
    return errors
