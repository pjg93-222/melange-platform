# -*- coding: utf-8 -*-
"""Tests for fec/members/b5_audio_diarize.py -- audio-identity design 2026-07-16 (user-directed build).

Synthetic fixtures only (in-memory numpy harmonic stacks, 16 kHz); no model weights, no network,
no committed binaries.  Backend = the deterministic test-only "spectral-test" embedding.

Covers the five contract behaviors:
  1. two-speaker separation with boundary recovery within 1.0 s of truth
  2. sub-MIN_SEGMENT_SEC fragments dropped (plus VAD exclusion of leading silence)
  3. determinism (two runs -> identical segment lists)
  4. loud broadband click (gavel surrogate) excluded from every speech segment
  5. crossfaded 1 s overlap region yields no confident single-speaker segment covering it

Note on test 1: the frozen fixture uses 3 s speaker blocks, which sit below the production
MIN_SEGMENT_SEC (5.0 s) floor, so that test passes an explicit min_segment_sec=1.0 to observe
boundary geometry; test 2 exercises the production default.
"""
from __future__ import annotations

import os
import sys
import traceback

import numpy as np

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from audio_rules import IDENTITY_TURN_TRIM_SEC, MIN_SEGMENT_SEC  # noqa: E402
from members.b5_audio_diarize import diarize, reject_spikes, simple_vad  # noqa: E402
from members.b5_audio_embed import get_backend  # noqa: E402

SR = 16000
VOICE_A = (110.0, (1.0, 0.8, 0.6, 0.5, 0.4, 0.3))
VOICE_B = (380.0, (1.0, 0.5, 0.9, 0.3, 0.7, 0.2))


def make_voice(spec, seconds, sr=SR, amp=0.25):
    """Deterministic harmonic stack normalized to a peak of `amp` (no RNG)."""
    f0, weights = spec
    t = np.arange(int(round(seconds * sr)), dtype=np.float64) / sr
    out = np.zeros_like(t)
    for h, w in enumerate(weights, start=1):
        out += w * np.sin(2.0 * np.pi * f0 * h * t)
    peak = float(np.max(np.abs(out))) or 1.0
    return amp * out / peak


def segments_covering(segments, t):
    return [s for s in segments if s["start"] < t < s["end"]]


def confident(segments):
    return [s for s in segments if not s["overlap"]]


def test_two_speaker_separation():
    """(1) alternating 3 s A/B blocks: >= 2 clusters, boundaries recovered within 1.0 s."""
    blocks = [(VOICE_A, 3.0), (VOICE_B, 3.0), (VOICE_A, 3.0), (VOICE_B, 3.0)]
    audio = np.concatenate([make_voice(spec, sec) for spec, sec in blocks])
    backend = get_backend("spectral-test")
    segs = confident(diarize(audio, SR, backend, min_segment_sec=1.0))
    assert segs, "no confident segments returned"

    # cluster separation: both A blocks share one label, both B blocks another, and they differ
    def label_at(t):
        covering = segments_covering(segs, t)
        assert covering, "no confident segment covers t=%.2f" % t
        return covering[0]["cluster"]

    a1, a2 = label_at(1.5), label_at(7.5)
    b1, b2 = label_at(4.5), label_at(10.5)
    assert a1 == a2, "A blocks split across clusters (%r vs %r)" % (a1, a2)
    assert b1 == b2, "B blocks split across clusters (%r vs %r)" % (b1, b2)
    assert a1 != b1, "A and B blocks collapsed into one cluster"
    assert len({s["cluster"] for s in segs}) >= 2, "fewer than 2 clusters"

    # no segment crosses a true boundary by more than 1.0 s
    for s in segs:
        for boundary in (3.0, 6.0, 9.0):
            if s["start"] < boundary < s["end"]:
                crossing = min(s["end"] - boundary, boundary - s["start"])
                assert crossing <= 1.0, (
                    "segment %r crosses boundary %.1f by %.2f s" % (s, boundary, crossing))

    # every true boundary is recovered by some segment edge within 1.0 s
    edges = [s["start"] for s in segs] + [s["end"] for s in segs]
    for boundary in (3.0, 6.0, 9.0):
        nearest = min(abs(e - boundary) for e in edges)
        assert nearest <= 1.0, "boundary %.1f not recovered (nearest edge %.2f s away)" % (
            boundary, nearest)


def test_min_segment_drop_and_vad():
    """(2) default MIN_SEGMENT_SEC drops a 3 s fragment; leading silence never enters a segment."""
    audio = np.concatenate([
        np.zeros(int(2.0 * SR)),          # silence
        make_voice(VOICE_A, 8.0),          # long block: survives the floor
        make_voice(VOICE_B, 3.0),          # sub-floor fragment: dropped
    ])
    backend = get_backend("spectral-test")
    segs = confident(diarize(audio, SR, backend))
    assert segs, "no confident segments returned"
    assert segments_covering(segs, 6.0), "the 8 s block did not survive MIN_SEGMENT_SEC"
    assert not segments_covering(segs, 12.0), "sub-MIN_SEGMENT_SEC fragment was not dropped"
    floor = MIN_SEGMENT_SEC - 2.0 * IDENTITY_TURN_TRIM_SEC
    for s in segs:
        assert s["end"] - s["start"] >= floor - 1e-9, "segment below trimmed floor: %r" % (s,)
        assert s["end"] > s["start"], "empty segment: %r" % (s,)
        assert s["start"] >= 1.0, "segment reaches into leading silence: %r" % (s,)

    # simple_vad itself marks the silence non-speech and the voiced region speech
    mask = simple_vad(audio, SR)
    assert not mask[:int(1.9 * SR)].any(), "VAD marked leading silence as speech"
    assert mask[int(3.0 * SR):int(9.0 * SR)].all(), "VAD dropped voiced samples"


def test_determinism():
    """(3) two identical calls -> byte-identical segment lists (no randomized init)."""
    blocks = [(VOICE_A, 3.0), (VOICE_B, 3.0), (VOICE_A, 3.0), (VOICE_B, 3.0)]
    audio = np.concatenate([make_voice(spec, sec) for spec, sec in blocks])
    backend = get_backend("spectral-test")
    first = diarize(audio, SR, backend, min_segment_sec=1.0, seed=7)
    second = diarize(audio, SR, backend, min_segment_sec=1.0, seed=7)
    assert first == second, "diarize() is not deterministic:\n%r\n%r" % (first, second)
    assert first, "determinism test produced no segments"


def test_click_rejection():
    """(4) a loud 50 ms broadband click inside continuous speech is in no returned segment."""
    audio = make_voice(VOICE_A, 10.0, amp=0.1)
    rng = np.random.RandomState(0)  # fixture-side determinism only
    click_start, click_len = int(5.0 * SR), int(0.05 * SR)
    audio[click_start:click_start + click_len] += rng.uniform(-0.95, 0.95, click_len)
    np.clip(audio, -1.0, 1.0, out=audio)

    spikes = reject_spikes(audio, SR)
    assert spikes[click_start:click_start + click_len].any(), "click not marked as a spike"
    assert not spikes[:int(4.0 * SR)].any(), "steady speech falsely marked as spike"

    backend = get_backend("spectral-test")
    segs = diarize(audio, SR, backend, min_segment_sec=1.0)
    conf = confident(segs)
    assert len(conf) >= 2, "speech did not split around the click: %r" % (conf,)
    assert len({s["cluster"] for s in conf}) == 1, "single voice split into clusters"
    for s in segs:  # every returned record, overlap-flagged or not
        assert not (s["start"] < 5.05 and s["end"] > 4.99), (
            "segment %r covers the click at 5.00-5.05 s" % (s,))


def test_crossfade_overlap():
    """(5) a 1 s both-voices crossfade yields no confident segment covering the overlap."""
    fade = int(1.0 * SR)
    a = make_voice(VOICE_A, 7.0)   # voiced through 7.0 s, fades out over [6, 7]
    b = make_voice(VOICE_B, 7.0)   # fades in over [6, 7], voiced through 13.0 s
    ramp = np.linspace(0.0, 1.0, fade, endpoint=False)
    a[-fade:] *= (1.0 - ramp)
    b[:fade] *= ramp
    audio = np.concatenate([a[:-fade], a[-fade:] + b[:fade], b[fade:]])
    assert audio.size == 13 * SR

    backend = get_backend("spectral-test")
    segs = confident(diarize(audio, SR, backend, n_speakers=2, min_segment_sec=1.0))
    cov_a = segments_covering(segs, 3.0)
    cov_b = segments_covering(segs, 10.0)
    assert cov_a and cov_b, "pure regions lost segments: %r" % (segs,)
    assert cov_a[0]["cluster"] != cov_b[0]["cluster"], "two voices collapsed into one cluster"
    for s in segs:
        assert not (s["start"] <= 6.0 and s["end"] >= 7.0), (
            "confident segment %r covers the whole 6.0-7.0 s overlap" % (s,))
        assert not (s["start"] < 6.5 < s["end"]), (
            "confident segment %r covers the overlap midpoint" % (s,))
        assert not (s["start"] < 5.0 and s["end"] > 8.0), (
            "confident segment %r bridges both pure-speaker zones" % (s,))


def main():
    tests = [
        test_two_speaker_separation,
        test_min_segment_drop_and_vad,
        test_determinism,
        test_click_rejection,
        test_crossfade_overlap,
    ]
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
    print("test_audio_diarize: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
