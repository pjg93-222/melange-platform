# -*- coding: utf-8 -*-
"""TDD tests for transcript-to-audio turn alignment (b5_audio_align_turns).

audio-identity design 2026-07-16 (user-directed build).  All fixtures are synthetic in-memory
token streams; no audio files, no models, no network.  The whisper helper is only asserted to
REFUSE without a local model directory -- it must never be exercised with a real model here.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from audio_rules import ALIGN_MIN_HEARING_WORD_MATCH, ALIGN_MIN_TURN_WORD_MATCH  # noqa: E402
from members.b5_audio_embed import BackendRefused  # noqa: E402
from members.b5_audio_align_turns import (  # noqa: E402
    align_turns,
    align_turns_aeneas,
    asr_words_from_whisper,
)


# ---------------------------------------------------------------- fixtures
TURN_TEXTS = [
    "The chair recognizes the gentleman from Ohio.",
    "Thank you, Mister Chairman; I yield back my time.",
    "The committee stands adjourned until further notice.",
]
# Normalized token streams the module must produce from the turns above.
TURN_TOKENS = [
    ["the", "chair", "recognizes", "the", "gentleman", "from", "ohio"],
    ["thank", "you", "mister", "chairman", "i", "yield", "back", "my", "time"],
    ["the", "committee", "stands", "adjourned", "until", "further", "notice"],
]
ALL_TOKENS = [tok for toks in TURN_TOKENS for tok in toks]          # 23 tokens
TURN_SPANS = [(0, 7), (7, 16), (16, 23)]                            # token index ranges


def make_asr_words():
    """One ASR word per token; word i spans [0.5*i, 0.5*i + 0.4] seconds.

    A few words carry capitalization/punctuation so the aligner's normalization is exercised
    on the ASR side too (word 0 'The', word 6 'Ohio.', word 15 'time,').
    """
    words = []
    for i, tok in enumerate(ALL_TOKENS):
        surface = tok
        if i == 0:
            surface = "The"
        elif i == 6:
            surface = "Ohio."
        elif i == 15:
            surface = "time,"
        words.append({"word": surface, "start": round(0.5 * i, 2), "end": round(0.5 * i + 0.4, 2)})
    return words


def garble(words, indices):
    out = copy.deepcopy(words)
    for k, i in enumerate(sorted(indices)):
        out[i]["word"] = "zzgarble%d" % k   # unique garbage; never equals any transcript token
    return out


# ---------------------------------------------------------------- tests
def test_exact_match_all_turns_usable():
    result = align_turns(TURN_TEXTS, make_asr_words())
    turns = result["turns"]
    assert len(turns) == 3, "expected one record per turn, got %d" % len(turns)
    for rec, (a, b) in zip(turns, TURN_SPANS):
        assert rec["usable"] is True, rec
        assert abs(rec["word_match"] - 1.0) < 1e-12, rec
        assert rec["start"] == round(0.5 * a, 2), rec
        assert rec["end"] == round(0.5 * (b - 1) + 0.4, 2), rec
    for i, rec in enumerate(turns):
        assert rec["turn_index"] == i
        assert set(rec) == {"turn_index", "start", "end", "word_match", "usable"}, sorted(rec)
    assert abs(result["hearing_word_match"] - 1.0) < 1e-12
    assert result["hearing_usable"] is True


def test_garbled_turn_unusable_neighbors_stay_usable():
    # Garble 4 of turn 1's 9 ASR words (token indices 7, 9, 11, 13) -> word_match 5/9 < 0.80.
    words = garble(make_asr_words(), [7, 9, 11, 13])
    result = align_turns(TURN_TEXTS, words)
    turns = result["turns"]
    assert turns[1]["word_match"] < ALIGN_MIN_TURN_WORD_MATCH, turns[1]
    assert turns[1]["usable"] is False, turns[1]
    assert turns[0]["usable"] is True and turns[2]["usable"] is True, (turns[0], turns[2])
    assert abs(turns[0]["word_match"] - 1.0) < 1e-12
    assert abs(turns[2]["word_match"] - 1.0) < 1e-12
    # Boundaries of the damaged turn come from its first/last MATCHED words (tokens 8 and 15).
    assert turns[1]["start"] == round(0.5 * 8, 2), turns[1]
    assert turns[1]["end"] == round(0.5 * 15 + 0.4, 2), turns[1]


def test_hearing_level_garbling_fails_hearing_gate():
    # Garble 18 of 23 words -> hearing_word_match 5/23 < 0.70.
    keep = {0, 1, 8, 12, 20}
    words = garble(make_asr_words(), [i for i in range(23) if i not in keep])
    result = align_turns(TURN_TEXTS, words)
    assert result["hearing_word_match"] < ALIGN_MIN_HEARING_WORD_MATCH, result["hearing_word_match"]
    assert result["hearing_usable"] is False


def test_turn_boundaries_monotonic_never_overlap():
    fixtures = [
        make_asr_words(),
        garble(make_asr_words(), [7, 9, 11, 13]),
        garble(make_asr_words(), [i for i in range(23) if i not in {0, 1, 8, 12, 20}]),
    ]
    for words in fixtures:
        turns = align_turns(TURN_TEXTS, words)["turns"]
        prev_end = None
        for rec in turns:
            if rec["start"] is None:
                continue
            assert rec["start"] <= rec["end"], rec
            if prev_end is not None:
                assert rec["start"] >= prev_end, "turn %d regresses/overlaps: start %r < prev end %r" % (
                    rec["turn_index"], rec["start"], prev_end)
            prev_end = rec["end"]


def test_whisper_refuses_without_model_dir():
    for bad_dir in (None, "", os.path.join(tempfile.gettempdir(), "no_such_whisper_model_dir_xyz")):
        try:
            asr_words_from_whisper("dummy.wav", bad_dir)
        except BackendRefused:
            pass
        else:
            raise AssertionError("asr_words_from_whisper must refuse for model_dir=%r" % (bad_dir,))
    # Incomplete directory (exists but has no model files) must also refuse.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            asr_words_from_whisper("dummy.wav", tmp)
        except BackendRefused as exc:
            assert "model" in str(exc).lower(), exc
        else:
            raise AssertionError("asr_words_from_whisper must refuse an incomplete model dir")
    # Refusal must happen BEFORE any import that could trigger a download path.
    assert "faster_whisper" not in sys.modules, "refusal must precede the faster_whisper import"


def make_aeneas_syncmap():
    fragments = []
    for index, text in enumerate(TURN_TEXTS):
        fragments.append({
            "id": "f%06d" % index,
            "begin": "%.3f" % (index * 20.0),
            "end": "%.3f" % (index * 20.0 + 18.0),
            "lines": [text],
        })
    return {"fragments": fragments}


def test_aeneas_boundaries_are_primary_but_asr_word_match_is_independent():
    result = align_turns_aeneas(TURN_TEXTS, make_aeneas_syncmap(), make_asr_words())
    assert result["hearing_usable"] is True, result
    assert result["boundary_source"] == "aeneas_mfcc_dtw"
    assert result["crosscheck_source"] == "local_asr_word_match"
    assert result["validation_errors"] == []
    for index, rec in enumerate(result["turns"]):
        assert rec["start"] == index * 20.0, rec
        assert rec["end"] == index * 20.0 + 18.0, rec
        assert rec["word_match"] == 1.0 and rec["usable"] is True, rec


def test_aeneas_text_or_time_mismatch_fails_the_hearing_closed():
    for corrupt in ("text", "overlap", "count"):
        syncmap = make_aeneas_syncmap()
        if corrupt == "text":
            syncmap["fragments"][1]["lines"] = ["unrelated words from another speaker"]
        elif corrupt == "overlap":
            syncmap["fragments"][1]["begin"] = "10.000"
        else:
            syncmap["fragments"].pop()
        result = align_turns_aeneas(TURN_TEXTS, syncmap, make_asr_words())
        assert result["hearing_usable"] is False, (corrupt, result)
        assert result["validation_errors"], (corrupt, result)
        assert not any(rec["usable"] for rec in result["turns"]), (corrupt, result)


def test_determinism_two_runs_identical():
    words = garble(make_asr_words(), [7, 9, 11, 13])
    first = align_turns(TURN_TEXTS, words)
    second = align_turns([str(t) for t in TURN_TEXTS], copy.deepcopy(words))
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def main():
    tests = [
        test_exact_match_all_turns_usable,
        test_garbled_turn_unusable_neighbors_stay_usable,
        test_hearing_level_garbling_fails_hearing_gate,
        test_turn_boundaries_monotonic_never_overlap,
        test_whisper_refuses_without_model_dir,
        test_aeneas_boundaries_are_primary_but_asr_word_match_is_independent,
        test_aeneas_text_or_time_mismatch_fails_the_hearing_closed,
        test_determinism_two_runs_identical,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print("PASS %s" % test.__name__)
        except Exception as exc:
            failed += 1
            print("FAIL %s: %r" % (test.__name__, exc))
    print("test_audio_align: %d passed, %d failed" % (len(tests) - failed, failed))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
