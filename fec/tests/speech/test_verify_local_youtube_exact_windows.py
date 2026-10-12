import numpy as np
import pytest

from fec.speech import verify_local_youtube_exact_windows as V


def test_candidate_starts_apply_two_second_insets_and_exact_frames():
    starts = V.candidate_starts({"start_sec": 10.0, "end_sec": 40.0})
    assert starts == [12.0, 20.0, 28.0]
    samples = np.arange(50 * V.SAMPLE_RATE, dtype=np.int16)
    payload = V.exact_payload(samples, 12.0)
    assert len(payload) == V.WINDOW_FRAMES * 2


def test_hard_signal_binds_to_inset_exact_cut_not_coarse_span_boundary():
    span = {"start_sec": 12.95, "end_sec": 45.45}
    # ASR word timestamps overlap the raw boundary by 0.28 seconds, while the
    # first exact cut begins safely at 14.95 after the mandatory inset.
    assert V.hard_signal_is_proximate_to_first_exact_cut(13.23, span)
    assert not V.hard_signal_is_proximate_to_first_exact_cut(15.0, span)
    assert not V.hard_signal_is_proximate_to_first_exact_cut(-108.0, span)


def test_seed_uses_earliest_reply_wholly_after_identity_cue():
    spans = [
        {"start_sec": 11.51, "end_sec": 45.45},
        {"start_sec": 43.43, "end_sec": 128.09},
    ]
    assert V.first_seed_span_index(13.23, spans) == 1


def test_seed_can_fall_forward_to_a_later_proximate_reply():
    spans = [
        {"start_sec": 20.0, "end_sec": 33.0},
        {"start_sec": 45.0, "end_sec": 80.0},
        {"start_sec": 150.0, "end_sec": 190.0},
    ]
    assert V.seed_span_indices(10.0, spans) == [0, 1]


def test_residual_passes_count_existing_verified_windows():
    row = {
        "profile_floor_exact_windows": 5,
        "verified_audio_basis": {
            "anchor_exact_windows": 1,
            "nonfloor_exact_windows_total": 3,
        },
    }
    assert V.residual_passes_required(row, 5) == 2


def test_residual_passes_ignore_high_any_setting_count_when_nonfloor_is_low():
    # A member whose only audio is House floor can have a large any-setting count while
    # the non-floor descriptive layer -- the one this lane actually feeds -- has almost
    # nothing. The residual must stay positive so the recovery lane does not treat that
    # member as already covered (AUDIOA-9).
    row = {
        "profile_floor_exact_windows": 5,
        "verified_audio_basis": {
            "anchor_exact_windows": 1,
            "canonical_any_setting_exact_windows": 50,
            "nonfloor_exact_windows_total": 0,
        },
    }
    assert V.residual_passes_required(row, 5) == 4


def test_required_pass_override_is_bounded_and_unique():
    assert V.parse_required_pass_overrides(["Timothy Kennedy=5"], 5) == {
        "Timothy Kennedy": 5
    }
    with pytest.raises(V.VerificationRefusal, match="required_pass_override_value"):
        V.parse_required_pass_overrides(["Timothy Kennedy=6"], 5)
    with pytest.raises(V.VerificationRefusal, match="required_pass_override_format"):
        V.parse_required_pass_overrides(["Timothy Kennedy"], 5)


def test_voice_activity_rejects_silence_and_accepts_continuous_signal():
    silence = np.zeros(V.WINDOW_FRAMES, dtype=np.int16)
    ratio, longest = V.voice_activity(silence)
    assert ratio == 0.0
    assert longest == V.WINDOW_SECONDS

    signal = np.full(V.WINDOW_FRAMES, 2000, dtype=np.int16)
    ratio, longest = V.voice_activity(signal)
    assert ratio == 1.0
    assert longest == 0.0


def test_consolidated_run_rebinds_only_identical_existing_canonical_row(tmp_path):
    manifest = tmp_path / "canonical.jsonl"
    row = {field: f"value-{field}" for field in V.CANONICAL_REBIND_FIELDS}
    row.update({
        "outlet": "CSPAN", "sid": "video", "start": 12.0,
        "end": 20.0, "frames": V.WINDOW_FRAMES, "samplerate": V.SAMPLE_RATE,
        "member": "Target Member",
    })
    V.SMR.write_jsonl_atomic(manifest, [row])

    assert V.append_canonical_atomic(manifest, [dict(row)]) == (0, 1)

    changed = dict(row, sha256="different")
    with pytest.raises(V.VerificationRefusal, match="existing_canonical_binding_mismatch"):
        V.append_canonical_atomic(manifest, [changed])
