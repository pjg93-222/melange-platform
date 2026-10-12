"""Evidence boundaries and publication failures for the descriptive rewrite."""

import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from fec.members.regenerate_descriptive_profiles import build_rows, compose
from fec.members.validate_descriptive_profiles import (
    CONTRACT, MAX_WORDS, MIN_WORDS,
    SCHEMA,
    STATUS,
    validate_descriptive_profiles,
    word_count,
)


ROOT = Path(__file__).resolve().parents[3]
PARAGRAPHS = ("p1", "p2", "p3")


def artifact_hash(document):
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@pytest.fixture(scope="module")
def evidence():
    return json.loads(
        (ROOT / "data/members/member_narrative_evidence_v4.json").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def generated(evidence):
    return build_rows(evidence)


@pytest.fixture
def publication(generated):
    document = {
        "schema": SCHEMA,
        "status": STATUS,
        "contract_version": CONTRACT,
        "basis_sha256": "basis-current",
        "evidence_sha256": "evidence-current",
        "count": 533,
        "ok_count": 533,
        "members": copy.deepcopy(generated),
    }
    receipt = {
        "schema": "member-narratives-v3-descriptive-receipt-v1",
        "status": "PASS",
        "contract_version": CONTRACT,
        "basis_sha256": "basis-current",
        "evidence_sha256": "evidence-current",
        "active_artifact_sha256": artifact_hash(document),
        "counts": {
            "profiles": 533,
            "word_count_pass": 533,
            "grounded_template_pass": 533,
        },
    }
    return document, receipt, set(generated)


def validate(publication):
    document, receipt, roster = publication
    return validate_descriptive_profiles(
        document,
        receipt,
        basis_sha256="basis-current",
        evidence_sha256="evidence-current",
        active_artifact_sha256=artifact_hash(document),
        member_names=roster,
    )


def rebind_artifact(publication):
    """Keep a negative test focused on a content check rather than a stale hash."""
    document, receipt, _ = publication
    receipt["active_artifact_sha256"] = artifact_hash(document)


def test_actual_roster_has_reproducible_three_paragraph_profiles(evidence, generated):
    assert len(evidence["members"]) == len(generated) == 533
    assert set(generated) == set(evidence["members"])
    assert generated == build_rows(copy.deepcopy(evidence))
    for name, row in generated.items():
        assert all(isinstance(row[key], str) and row[key].strip() for key in PARAGRAPHS), name
        assert all("\n" not in row[key] for key in PARAGRAPHS), name
        assert MIN_WORDS <= word_count(row) <= MAX_WORDS, name
        assert row["words"] == word_count(row), name
        assert "meeting_playbook" not in row, name
        assert row["recurring_audio_pattern_claimed"] is False, name


def test_untrusted_personality_and_persuasion_fields_cannot_change_generic_prose(evidence):
    name = "Aaron Bean"  # Exercise the general writer, not an editorial override.
    original = copy.deepcopy(evidence["members"][name])
    altered = copy.deepcopy(original)
    unsupported = {"pct": 1.0, "claim": "UNSUPPORTED_INNER_STATE_AND_PERSUASION"}
    altered["traits"] = {key: unsupported for key in ("Openness", "Agreeableness", "Neuroticism")}
    altered["qualified_reads"] = unsupported
    altered["receptivity"] = {
        "pressure_note": "pressure always works",
        "susc_read": "persuade through flattery",
    }
    altered["behavior"]["conversational_dominance"] = unsupported
    altered["behavior"]["words_per_turn"] = unsupported
    altered["speech_profile"]["motivating_appeals"] = unsupported
    altered["speech_profile"]["repeated_defensive_reactions"] = unsupported
    altered["speech_profile"]["interaction_habits"] = {
        "gini": unsupported,
        "burst": unsupported,
        "measures": {"gini": unsupported, "burst": unsupported},
    }
    altered["meeting_playbook"] = {"open": "UNSUPPORTED_INNER_STATE_AND_PERSUASION"}
    assert compose(name, altered) == compose(name, original)


@pytest.mark.parametrize("speech", [
    None,
    {},
    {"general_audio_habits": None},
    {"general_audio_habits": None, "verified_voiceprint_baseline": None},
    # A truthy-but-empty layer must read as absence, never as evidence.
    {"general_audio_habits": {}, "verified_voiceprint_baseline": {}},
])
def test_missing_speech_does_not_invent_numeric_audio_or_answer_length(speech):
    record = {
        "behavior": {"policy_breadth": {"pct": 0.6}},
        "speech_profile": speech,
    }
    row = compose("Sample Member", record)
    prose = " ".join(row[key] for key in PARAGRAPHS)
    assert "no usable attributed-word sample" in prose
    assert "no accepted audio sample" in prose
    assert "typical stored passage" not in prose
    assert not re.search(r"\b\d+(?:[,.]\d+)*\s+(?:clips?|episodes?|minutes|words)\b", prose)
    assert 200 <= word_count(row) <= 250


def test_combined_legislative_measure_retains_available_components_qualification():
    row = compose(
        "Sample Member",
        {"behavior": {"legislative_diligence": {"pct": 0.9}}},
    )
    assert "using whichever components are available" in row["p1"]
    assert "There is no usable cross-party comparison" in row["p1"]
    assert "The share-based measure of cross-party legislative connections is" not in row["p1"]


def test_valid_complete_publication_passes(publication):
    assert validate(publication) == []


@pytest.mark.parametrize("target", ["document", "receipt"])
@pytest.mark.parametrize("key", ["basis_sha256", "evidence_sha256"])
def test_validator_rejects_stale_input_hashes(publication, target, key):
    document, receipt, _ = publication
    (document if target == "document" else receipt)[key] = "stale"
    rebind_artifact(publication)
    assert key + " mismatch" in validate(publication)


def test_validator_rejects_tampered_artifact_even_at_same_word_count(publication):
    document, _, _ = publication
    row = next(iter(document["members"].values()))
    words = row["p1"].split()
    words[0] = "Tampered"
    row["p1"] = " ".join(words)
    assert row["words"] == word_count(row)
    assert "active artifact hash mismatch" in validate(publication)


@pytest.mark.parametrize("key", ["count", "ok_count"])
def test_validator_rejects_wrong_artifact_counts(publication, key):
    document, _, _ = publication
    document[key] = 532
    rebind_artifact(publication)
    assert "artifact counts mismatch" in validate(publication)


@pytest.mark.parametrize("key", ["profiles", "word_count_pass", "grounded_template_pass"])
def test_validator_rejects_wrong_receipt_counts(publication, key):
    _, receipt, _ = publication
    receipt["counts"][key] = 532
    assert "receipt count mismatch: " + key in validate(publication)


def test_validator_rejects_missing_member_even_with_updated_artifact_hash(publication):
    document, _, _ = publication
    del document["members"][next(iter(document["members"]))]
    rebind_artifact(publication)
    assert "profile roster mismatch" in validate(publication)


def test_validator_rejects_replacement_name_even_when_count_stays_533(publication):
    document, _, _ = publication
    name = next(iter(document["members"]))
    document["members"]["Not In The Roster"] = document["members"].pop(name)
    rebind_artifact(publication)
    assert len(document["members"]) == 533
    assert "profile roster mismatch" in validate(publication)


def test_validator_rejects_incomplete_expected_roster(publication):
    _, _, roster = publication
    roster.remove(next(iter(roster)))
    assert "expected roster must contain 533 unique members" in validate(publication)


@pytest.mark.parametrize("tamper", ["declared_count", "too_short", "too_long"])
def test_validator_rejects_word_count_tampering(publication, tamper):
    document, _, _ = publication
    name, row = next(iter(document["members"].items()))
    if tamper == "declared_count":
        row["words"] += 1
    elif tamper == "too_short":
        row["p1"], row["p2"], row["p3"] = "Short.", "Still short.", "No evidence detail."
        row["words"] = word_count(row)
    else:
        row["p3"] += " Additional material." * 130
        row["words"] = word_count(row)
    rebind_artifact(publication)
    assert name + ": invalid word count" in validate(publication)


@pytest.mark.parametrize("playbook", [{}, None, {"open": "Begin with a personal appeal"}])
def test_validator_rejects_any_legacy_meeting_playbook_field(publication, playbook):
    document, _, _ = publication
    name, row = next(iter(document["members"].items()))
    row["meeting_playbook"] = playbook
    rebind_artifact(publication)
    assert name + ": legacy meeting playbook prohibited" in validate(publication)


def test_validator_rejects_unestablished_recurring_audio_authority(publication):
    document, _, _ = publication
    name, row = next(iter(document["members"].items()))
    row["recurring_audio_pattern_claimed"] = True
    rebind_artifact(publication)
    assert name + ": recurring audio authority not established" in validate(publication)


# ---------------------------------------------------------------------------
# TWO AUDIO LAYERS (2026-09-07).  A member may carry a descriptive clip baseline
# (raw per-clip means), a verified voiceprint (z scores standardized within the
# legacy cache's own source), both, or neither.  Until this date the producer read
# only the first, so the 74 members whose only audio is a voiceprint were told in
# prose that no accepted audio sample existed.  These cases pin all four
# populations and the floor label, and they run on SYNTHETIC records so they hold
# regardless of what the current data plane happens to contain.
# ---------------------------------------------------------------------------
NO_AUDIO_SENTENCE = "no accepted audio sample"


def _record(audio=None, voiceprint=None):
    return {
        "behavior": {"policy_breadth": {"pct": 0.6}},
        "speech_profile": {
            "general_audio_habits": audio,
            "verified_voiceprint_baseline": voiceprint,
            "by_setting": {"house_floor": {"clips": (audio or {}).get("clips", 0)}} if audio else {},
        },
    }


def _clip_layer(clips=12, episodes=3, minutes=8.4, floor_clips=0):
    return {
        "clips": clips,
        "episodes": episodes,
        "minutes": minutes,
        "scale": "raw_clip_means",
        "floor_composition": {
            "floor_clips": floor_clips,
            "total_clips": clips,
            "share": floor_clips / clips,
            "composition": ("floor_only" if floor_clips == clips
                            else "floor_dominant" if floor_clips * 2 > clips
                            else "floor_present" if floor_clips else "no_floor"),
        },
    }


def _voiceprint_layer(minutes=34.4):
    return {
        "minutes": minutes,
        "seconds": minutes * 60.0,
        "scale": "within_source_z",
        "standardized_channels": {"pause": 0.26, "pace": -1.29},
    }


def _prose(record):
    row = compose("Sample Member", record)
    assert 200 <= word_count(row) <= 250
    return " ".join(row[key] for key in PARAGRAPHS)


def test_voiceprint_only_member_is_described_not_denied():
    """The 74-member population the divergence hit: verified audio, no clip pool."""
    prose = _prose(_record(voiceprint=_voiceprint_layer(34.4)))
    assert NO_AUDIO_SENTENCE not in prose
    assert "34.4 minutes" in prose
    assert "voiceprint" in prose
    # It must not borrow the clip vocabulary it has no basis for.
    assert "clips from" not in prose
    assert not re.search(r"\b\d+\s+clips?\b", prose)


def test_both_layers_are_described_and_kept_on_separate_scales():
    prose = _prose(_record(audio=_clip_layer(clips=23, episodes=3, minutes=16.9),
                           voiceprint=_voiceprint_layer(12.1)))
    assert NO_AUDIO_SENTENCE not in prose
    assert "23 clips from 3 episodes" in prose
    assert "16.9 minutes" in prose
    assert "12.1 minutes" in prose
    # The scale caveat is the point: the two numbers are not the same measurement.
    assert "not on the clip scale" in prose


def test_descriptive_only_member_keeps_its_existing_sentence_and_gains_no_voiceprint_claim():
    prose = _prose(_record(audio=_clip_layer(clips=23, episodes=3, minutes=16.9)))
    assert NO_AUDIO_SENTENCE not in prose
    assert "23 clips from 3 episodes" in prose
    assert "voiceprint" not in prose


@pytest.mark.parametrize("floor_clips,expected", [(23, "All of that audio"), (20, "Most of that audio")])
def test_floor_dominant_audio_is_labelled_as_floor_delivery(floor_clips, expected):
    prose = _prose(_record(audio=_clip_layer(clips=23, episodes=3, minutes=16.9,
                                             floor_clips=floor_clips)))
    assert expected in prose
    assert "House-floor recording" in prose
    assert "describe floor delivery" in prose


@pytest.mark.parametrize("floor_clips", [0, 5])
def test_non_floor_dominant_audio_carries_no_floor_delivery_caveat(floor_clips):
    prose = _prose(_record(audio=_clip_layer(clips=23, episodes=3, minutes=16.9,
                                             floor_clips=floor_clips)))
    assert "describe floor delivery" not in prose


def test_member_with_neither_layer_keeps_the_honest_disclosure():
    prose = _prose(_record())
    assert NO_AUDIO_SENTENCE in prose
    assert "voiceprint" not in prose


CACHE_ALIASES = (
    "MeanUnvoicedSegmentLength", "VoicedSegmentsPerSec", "loudness_sma3_stddevNorm",
    "F0semitoneFrom27.5Hz_sma3nz_stddevNorm", "jitterLocal_sma3nz_amean",
    "F0semitoneFrom27.5Hz_sma3nz_amean",
)


@pytest.fixture(scope="module")
def voiceprint_expected():
    """Members the PRODUCER'S INPUTS say hold a renderable verified voiceprint.

    The expectation must come from a different source than the artifact under test:
    an assertion that reads the ledger to decide what the ledger should contain
    cannot fail, and that is precisely how the original divergence survived -- the
    74 affected members had no field to be missing, because the producer wrote
    their layer under the other layer's key.
    """
    cache_path = ROOT / "data/members/b5_audio_cache.json"
    census_path = ROOT / "data/speech/congress_audio_profile_census_full_v1.jsonl"
    if not cache_path.is_file() or not census_path.is_file():
        pytest.skip("b5_audio_cache.json / audio census absent; nothing to expect against")
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    features = cache.get("feature_names") or []
    z_by_member = cache.get("z") or {}
    members = set(cache.get("members") or [])
    verified = set()
    for line in census_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("verified_audio_profile") is True:
            verified.add(row.get("member"))
    expected = set()
    for name in members & verified:
        values = z_by_member.get(name)
        if not isinstance(values, list) or len(values) != len(features):
            continue
        mapped = dict(zip(features, values))
        if any(isinstance(mapped.get(key), (int, float)) and not isinstance(mapped.get(key), bool)
               for key in CACHE_ALIASES):
            expected.add(name)
    return expected


def test_no_member_holding_a_verified_voiceprint_is_told_they_have_no_audio(
        evidence, generated, voiceprint_expected):
    """Roster-wide regression: verified audio on file may never render as none.

    Skips clean while the published ledger predates the two-layer contract -- that
    artifact is a data-plane input this suite does not build -- and asserts the
    moment it carries it, so the divergence cannot come back unnoticed.
    """
    counts = evidence.get("counts") or {}
    if "verified_voiceprint_profiles" not in counts:
        pytest.skip("member_narrative_evidence_v4.json predates the two-audio-layer contract; "
                    "rerun build_member_narrative_ledger.py")
    roster = set(evidence["members"])
    expected = voiceprint_expected & roster
    assert expected, "the input cache should name at least one roster voiceprint"
    unpublished, denied, collisions = [], [], []
    for name, record in evidence["members"].items():
        speech = record.get("speech_profile") or {}
        clip = speech.get("general_audio_habits")
        voice = speech.get("verified_voiceprint_baseline")
        for layer in (clip, voice):
            assert layer is None or isinstance(layer, dict), name
            assert layer != {}, name          # never truthy-but-empty
        if name in expected and not (isinstance(voice, dict)
                                     and (voice.get("standardized_channels") or {})
                                     and voice.get("minutes")):
            unpublished.append(name)
        holds_audio = name in expected or bool(isinstance(clip, dict) and clip.get("clips"))
        if holds_audio and NO_AUDIO_SENTENCE in " ".join(generated[name][k] for k in PARAGRAPHS):
            denied.append(name)
        if isinstance(clip, dict) and isinstance(voice, dict) and clip.get("scale") == voice.get("scale"):
            collisions.append(name)
    assert not unpublished, "verified voiceprint on file but not published: %s" % unpublished[:5]
    assert not denied, "prose denies audio the member holds: %s" % denied[:5]
    assert not collisions, "both layers share one scale label: %s" % collisions[:5]
    assert counts["verified_voiceprint_profiles"] == len(expected)
    assert (counts["both_audio_layers"] + counts["voiceprint_only_audio_profiles"]
            == counts["verified_voiceprint_profiles"])
    assert (counts["descriptive_audio_profiles"] + counts["voiceprint_only_audio_profiles"]
            + counts["no_audio_layer"] == 533)
