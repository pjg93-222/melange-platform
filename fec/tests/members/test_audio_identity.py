# -*- coding: utf-8 -*-
"""TDD battery for speaker enrollment (b5_audio_enroll) + verification (b5_audio_verify2).

audio-identity design 2026-07-16 (user-directed build).  Written RED-first, before either module
existed.  Synthetic harmonic "voices" (stacked sines with formant-ish band emphasis, 16 kHz) drive
the deterministic spectral-test backend; no model weights, no network, no LLM anywhere.  Calibrated
against the backend: same-voice disjoint halves cosine ~= 1.0, cross-voice ~= 0.70, so the fixture
thresholds artifact (theta_accept 0.90 / theta_enroll 0.90 / eer 0.80) separates cleanly.

Standalone: python fec/tests/members/test_audio_identity.py
"""
from __future__ import annotations

import json
import math
import os
import hashlib
import sys
import tempfile
import traceback

import numpy as np

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from audio_rules import (ENROLL_MIN_CLIPS, ENROLL_MIN_EVENTS, ENROLL_MIN_SEC,
                         IDENTITY_MIN_COVERAGE, IDENTITY_MIN_MARGIN)  # noqa: E402
from members.b5_audio_embed import (  # noqa: E402
    BackendRefused,
    Wav2Vec2Backend,
    _chunk_bounds,
    cosine,
    get_backend,
)
import members.b5_audio_enroll as enroll  # noqa: E402
import members.b5_audio_verify2 as verify  # noqa: E402

SR = 16000
EVIDENCE = "chyron-regex:REP. TEST MEMBER (R-XX)"
THETA_ENROLL = 0.90
THRESHOLDS_DOC = {
    "schema": "b5-audio-identity-thresholds-v1",
    "status": "CALIBRATION_PASS",
    "theta_accept": 0.90,
    "theta_enroll": THETA_ENROLL,
    "theta_eer": 0.80,
    "eer_rate": 0.03,
    "far_at_theta_accept": 0.0,
    "calibration": {"method": "synthetic-fixture", "note": "test-only, not a real calibration"},
}


def voice(f0, formants, seconds=6.0, sr=SR, phase=0.7):
    """Deterministic synthetic 'voice': harmonic stack + Gaussian formant emphasis."""
    t = np.arange(int(seconds * sr)) / float(sr)
    sig = np.zeros_like(t)
    k = 1
    while k * f0 < sr / 2.0 and k <= 40:
        f = k * f0
        boost = sum(np.exp(-0.5 * ((f - fc) / 200.0) ** 2) for fc in formants)
        sig = sig + (1.0 / k) * (1.0 + 2.0 * boost) * np.sin(2 * np.pi * f * t + phase * k)
        k += 1
    return sig / np.max(np.abs(sig))


VOICE_A = voice(112.0, (500.0, 1500.0, 2500.0), phase=0.7)
VOICE_B = voice(285.0, (900.0, 1900.0, 3300.0), phase=0.3)
BACKEND = get_backend("spectral-test")


def slices(sig, n):
    """n disjoint equal slices of a signal (each >= 1 s for the 6 s fixtures)."""
    step = sig.size // n
    return [sig[i * step:(i + 1) * step] for i in range(n)]


def clip(samples, event_id, seconds=70.0, evidence=EVIDENCE):
    return {"samples": samples, "sr": SR, "event_id": event_id,
            "seconds": seconds, "identity_evidence": evidence}


def good_clips_a(n=3):
    parts = slices(VOICE_A, 4)
    events = ["evt-1", "evt-2", "evt-3", "evt-4"]
    return [clip(parts[i], events[i]) for i in range(n)]


def write_thresholds(dirpath):
    path = os.path.join(dirpath, "b5_audio_identity_thresholds.json.candidate")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(THRESHOLDS_DOC, handle)
    return path


def bank_a():
    return enroll.build_bank({"Test Member A": good_clips_a()}, BACKEND, THETA_ENROLL)


# ---------------------------------------------------------------- enrollment

def test_same_voice_halves_embed_close():
    half = VOICE_A.size // 2
    e1 = BACKEND.embed(VOICE_A[:half], SR)
    e2 = BACKEND.embed(VOICE_A[half:], SR)
    c = cosine(e1, e2)
    assert c >= 0.98, "same synthetic voice, disjoint halves, cosine only %.4f" % c
    cross = cosine(e1, BACKEND.embed(VOICE_B[:half], SR))
    assert cross < THETA_ENROLL, "cross-voice cosine %.4f not below theta_enroll" % cross


def test_enrollment_builds_contracted_member_record():
    bank = bank_a()
    assert bank["schema"] == "b5-audio-enroll-bank-v1", bank.get("schema")
    assert bank["status"] == "RESEARCH_CANDIDATE_NOT_CONSUMED"
    assert bank["backend"] == "spectral-test"
    member = bank["members"]["Test Member A"]
    norm = math.sqrt(sum(v * v for v in member["embedding"]))
    assert abs(norm - 1.0) < 1e-9, "bank embedding not unit-norm: %r" % norm
    assert len(member["clips"]) == 3
    for row in member["clips"]:
        assert set(row) >= {"path", "event_id", "seconds", "identity_evidence", "sha256"}
        assert len(row["sha256"]) == 64
    assert member["total_sec"] >= ENROLL_MIN_SEC
    assert member["events"] >= ENROLL_MIN_EVENTS
    assert member["ratified"] == []
    json.dumps(bank)  # must be pure-python serializable


def test_wrong_speaker_clip_is_quarantined():
    parts_b = slices(VOICE_B, 4)
    clips = good_clips_a() + [clip(parts_b[0], "evt-planted")]
    bank = enroll.build_bank({"Test Member A": clips}, BACKEND, THETA_ENROLL)
    member = bank["members"]["Test Member A"]
    assert len(member["clips"]) == 3, "planted clip not removed"
    kept_events = {row["event_id"] for row in member["clips"]}
    assert "evt-planted" not in kept_events
    quarantined = [row for row in bank["quarantined"] if row["member"] == "Test Member A"]
    assert len(quarantined) == 1, "expected exactly one quarantined clip, got %r" % quarantined
    assert quarantined[0]["clip"]["event_id"] == "evt-planted"
    assert "cosine" in quarantined[0]["reason"]


def test_enrollment_refuses_too_few_clips():
    bank = enroll.build_bank({"M": good_clips_a(2)}, BACKEND, THETA_ENROLL)
    assert "M" not in bank["members"]
    assert "CLIPS" in bank["refused"]["M"], bank["refused"]


def test_enrollment_refuses_single_event():
    parts = slices(VOICE_A, 4)
    clips = [clip(parts[i], "evt-same") for i in range(3)]
    bank = enroll.build_bank({"M": clips}, BACKEND, THETA_ENROLL)
    assert "M" not in bank["members"]
    assert "EVENTS" in bank["refused"]["M"], bank["refused"]


def test_enrollment_refuses_too_few_seconds():
    parts = slices(VOICE_A, 4)
    clips = [clip(parts[i], "evt-%d" % i, seconds=30.0) for i in range(3)]  # 90 < 180
    bank = enroll.build_bank({"M": clips}, BACKEND, THETA_ENROLL)
    assert "M" not in bank["members"]
    assert "SECONDS" in bank["refused"]["M"], bank["refused"]


def test_enrollment_refuses_empty_identity_evidence():
    clips = good_clips_a()
    clips[1]["identity_evidence"] = ""
    try:
        enroll.build_bank({"M": clips}, BACKEND, THETA_ENROLL)
    except ValueError as exc:
        assert "identity_evidence" in str(exc)
    else:
        raise AssertionError("empty identity_evidence was accepted")


def test_enrollment_is_deterministic_byte_identical():
    one = json.dumps(bank_a(), sort_keys=True).encode("utf-8")
    two = json.dumps(bank_a(), sort_keys=True).encode("utf-8")
    assert one == two, "enrollment bank is not byte-identical across runs"


def test_enroll_cli_writes_candidate_artifact_and_guards_out_suffix():
    import soundfile as sf
    with tempfile.TemporaryDirectory() as tmp:
        parts = slices(VOICE_A, 4)
        manifest = {"Test Member A": []}
        for i in range(3):
            wav = os.path.join(tmp, "clip%d.wav" % i)
            sf.write(wav, parts[i], SR)
            manifest["Test Member A"].append(
                {"path": wav, "sr": SR, "event_id": "evt-%d" % i,
                 "seconds": 70.0, "identity_evidence": EVIDENCE})
        man_path = os.path.join(tmp, "manifest.json")
        with open(man_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        thresholds_path = write_thresholds(tmp)
        out = os.path.join(tmp, "b5_audio_enroll_bank.json.candidate")
        enroll.main(["--manifest", man_path, "--backend", "spectral-test",
                     "--thresholds", thresholds_path, "--out", out])
        with open(out, encoding="utf-8") as handle:
            bank = json.load(handle)
        assert bank["schema"] == "b5-audio-enroll-bank-v1"
        assert "Test Member A" in bank["members"]
        assert bank["theta_enroll"] == THETA_ENROLL
        assert len(bank["thresholds_sha256"]) == 64
        try:
            enroll.main(["--manifest", man_path, "--backend", "spectral-test",
                         "--thresholds", thresholds_path,
                         "--out", os.path.join(tmp, "bank.json")])
        except SystemExit as exc:
            assert ".json.candidate" in str(exc.args[0] if exc.args else "")
        else:
            raise AssertionError("non-.json.candidate --out was accepted")


def test_ecapa_backend_refuses_without_model_dir():
    try:
        get_backend("ecapa", None)
    except BackendRefused:
        pass
    else:
        raise AssertionError("ecapa without model dir did not raise BackendRefused")


def test_wav2vec2_backend_refuses_without_model_dir():
    try:
        get_backend("wav2vec2", None)
    except BackendRefused:
        pass
    else:
        raise AssertionError("wav2vec2 without model dir did not raise BackendRefused")


def test_wav2vec2_chunk_plan_covers_audio_without_a_tiny_tail():
    bounds = _chunk_bounds(451, sr=10, chunk_seconds=20.0, min_seconds=1.0)
    assert bounds == [(0, 200), (200, 400), (400, 451)], bounds
    merged = _chunk_bounds(405, sr=10, chunk_seconds=20.0, min_seconds=1.0)
    assert merged == [(0, 200), (200, 405)], merged
    assert sum(end - start for start, end in merged) == 405


def test_wav2vec2_embedding_streams_chunks_and_pools_deterministically():
    import types
    import torch

    class FakeExtractor:
        def __call__(self, samples, sampling_rate, return_tensors):
            assert sampling_rate == 16000 and return_tensors == "pt"
            return {"input_values": torch.tensor(samples, dtype=torch.float32).unsqueeze(0)}

    class FakeModel:
        def __init__(self):
            self.calls = 0

        def __call__(self, input_values):
            self.calls += 1
            mean = input_values.mean(dim=1)
            energy = input_values.square().mean(dim=1)
            hidden = torch.stack((mean, energy), dim=1).unsqueeze(1)
            return types.SimpleNamespace(last_hidden_state=hidden)

    backend = object.__new__(Wav2Vec2Backend)
    backend._extractor = FakeExtractor()
    backend._model = FakeModel()
    samples = np.linspace(-1.0, 1.0, 45 * 16000, dtype=np.float32)
    first = backend.embed(samples, 16000)
    assert backend._model.calls == 3
    backend._model.calls = 0
    second = backend.embed(samples.copy(), 16000)
    assert backend._model.calls == 3
    assert np.allclose(first, second)
    assert abs(float(np.linalg.norm(first)) - 1.0) < 1e-12


def test_wav2vec2_raw_embedding_preserves_the_paper_classifier_input():
    import types
    import torch

    class FakeExtractor:
        def __call__(self, samples, sampling_rate, return_tensors):
            return {"input_values": torch.tensor(samples, dtype=torch.float32).unsqueeze(0)}

    class FakeModel:
        def __call__(self, input_values):
            mean = input_values.mean(dim=1)
            energy = input_values.square().mean(dim=1)
            hidden = torch.stack((mean + 2.0, energy + 3.0), dim=1).unsqueeze(1)
            return types.SimpleNamespace(last_hidden_state=hidden)

    backend = object.__new__(Wav2Vec2Backend)
    backend._extractor = FakeExtractor()
    backend._model = FakeModel()
    samples = np.linspace(-1.0, 1.0, 5 * 16000, dtype=np.float32)
    raw = backend.embed_raw(samples, 16000)
    normalized = backend.embed(samples, 16000)
    assert raw.shape == normalized.shape == (2,)
    assert not np.isclose(np.linalg.norm(raw), 1.0)
    assert np.allclose(normalized, raw / np.linalg.norm(raw))


def test_real_backends_require_and_verify_complete_hash_manifest():
    with tempfile.TemporaryDirectory() as tmp:
        model_dir = os.path.join(tmp, "wav2vec2")
        os.makedirs(model_dir)
        files = {
            "config.json": b"{}",
            "preprocessor_config.json": b"{}",
            "model.safetensors": b"test-only-model-bytes",
        }
        for name, content in files.items():
            with open(os.path.join(model_dir, name), "wb") as handle:
                handle.write(content)
        manifest = {
            "schema": "b5-audio-model-manifest-v1",
            "status": "PINNED_LOCAL_MODEL",
            "backend": "wav2vec2",
            "files": {name: hashlib.sha256(content).hexdigest()
                      for name, content in sorted(files.items())},
        }
        manifest_path = os.path.join(tmp, "model_manifest.json")
        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump(manifest, handle)
        try:
            get_backend("wav2vec2", model_dir)
        except BackendRefused:
            pass
        else:
            raise AssertionError("real backend accepted no model manifest")
        backend = get_backend("wav2vec2", model_dir, manifest_path)
        assert backend.file_hashes() == manifest["files"]
        with open(os.path.join(model_dir, "config.json"), "wb") as handle:
            handle.write(b'{"changed":true}')
        try:
            get_backend("wav2vec2", model_dir, manifest_path)
        except BackendRefused as exc:
            assert "hash" in str(exc).lower()
        else:
            raise AssertionError("corrupted model file passed the hash manifest")


# -------------------------------------------------------------- verification

def _target_and_segments():
    bank = enroll.build_bank(
        {"Test Member A": good_clips_a(), "Test Member B": [
            clip(part, "evt-b%d" % i) for i, part in enumerate(slices(VOICE_B, 3))]},
        BACKEND, THETA_ENROLL)
    target = np.asarray(bank["members"]["Test Member A"]["embedding"])
    competitor = np.asarray(bank["members"]["Test Member B"]["embedding"])
    seg = slices(VOICE_A, 4)[3]  # disjoint from every enrollment clip
    half = seg.size // 2
    segments = [BACKEND.embed(seg[:half], SR), BACKEND.embed(seg[half:], SR)]
    return target, segments, competitor


def test_verify_accepts_same_voice_disjoint_segments():
    target, segments, competitor = _target_and_segments()
    with tempfile.TemporaryDirectory() as tmp:
        thresholds = verify.load_thresholds(write_thresholds(tmp))
    record = verify.verify_unit(target, segments, thresholds,
                                competitor_embeddings=[competitor], coverage=0.95)
    assert record["decision"] == "ACCEPT", record
    assert record["min_cosine"] >= thresholds["theta_accept"]
    assert record["margin"] is not None and record["margin"] >= IDENTITY_MIN_MARGIN
    assert record["coverage"] == 0.95


def test_verify_rejects_margin_below_floor():
    target, segments, _ = _target_and_segments()
    with tempfile.TemporaryDirectory() as tmp:
        thresholds = verify.load_thresholds(write_thresholds(tmp))
    # competitor identical to the target -> margin ~ 0 < IDENTITY_MIN_MARGIN
    record = verify.verify_unit(target, segments, thresholds,
                                competitor_embeddings=[target], coverage=0.95)
    assert record["decision"] == "REJECT", record
    assert record["margin"] is not None and record["margin"] < IDENTITY_MIN_MARGIN


def test_verify_rejects_coverage_below_floor():
    target, segments, competitor = _target_and_segments()
    with tempfile.TemporaryDirectory() as tmp:
        thresholds = verify.load_thresholds(write_thresholds(tmp))
    record = verify.verify_unit(target, segments, thresholds,
                                competitor_embeddings=[competitor], coverage=0.50)
    assert record["decision"] == "REJECT", record
    assert record["coverage"] < IDENTITY_MIN_COVERAGE


def test_verify_fails_closed_on_missing_coverage():
    target, segments, competitor = _target_and_segments()
    with tempfile.TemporaryDirectory() as tmp:
        thresholds = verify.load_thresholds(write_thresholds(tmp))
    record = verify.verify_unit(target, segments, thresholds,
                                competitor_embeddings=[competitor], coverage=None)
    assert record["decision"] == "REJECT", record


def _blend(target, other, wanted_cosine):
    """Unit vector with an exact cosine to the (unit) target, built in the target/other plane."""
    target = target / np.linalg.norm(target)
    ortho = other - np.dot(other, target) * target
    ortho = ortho / np.linalg.norm(ortho)
    return wanted_cosine * target + math.sqrt(1.0 - wanted_cosine ** 2) * ortho


def test_verify_indeterminate_band_and_reject_below_theta_eer():
    target, _, competitor = _target_and_segments()
    with tempfile.TemporaryDirectory() as tmp:
        thresholds = verify.load_thresholds(write_thresholds(tmp))
    mid = _blend(target, competitor, 0.85)  # theta_eer 0.80 <= 0.85 < theta_accept 0.90
    record = verify.verify_unit(target, [mid], thresholds, coverage=0.95)
    assert record["decision"] == "INDETERMINATE", record
    low = _blend(target, competitor, 0.50)
    record = verify.verify_unit(target, [low], thresholds, coverage=0.95)
    assert record["decision"] == "REJECT", record


def test_verify_is_deterministic_byte_identical():
    target, segments, competitor = _target_and_segments()
    with tempfile.TemporaryDirectory() as tmp:
        thresholds = verify.load_thresholds(write_thresholds(tmp))
    one = json.dumps(verify.verify_unit(target, segments, thresholds,
                                        competitor_embeddings=[competitor], coverage=0.95),
                     sort_keys=True).encode("utf-8")
    two = json.dumps(verify.verify_unit(target, segments, thresholds,
                                        competitor_embeddings=[competitor], coverage=0.95),
                     sort_keys=True).encode("utf-8")
    assert one == two, "verification record is not byte-identical across runs"


def test_missing_thresholds_artifact_is_systemexit_2():
    missing = os.path.join(tempfile.gettempdir(), "no_such_dir_xyz",
                           "b5_audio_identity_thresholds.json.candidate")
    try:
        verify.load_thresholds(missing)
    except SystemExit as exc:
        assert exc.code == 2, "expected exit code 2, got %r" % (exc.code,)
        assert "thresholds artifact" in str(exc.args[0] if exc.args else "")
    else:
        raise AssertionError("missing thresholds artifact did not refuse")


def test_thresholds_refuse_failed_or_malformed_calibration():
    with tempfile.TemporaryDirectory() as tmp:
        for name, patch in (
            ("failed", {"status": "CALIBRATION_STOP_BAR_MISS"}),
            ("reversed", {"theta_eer": 0.95}),
            ("bad_rate", {"eer_rate": 1.5}),
        ):
            doc = dict(THRESHOLDS_DOC)
            doc.update(patch)
            path = os.path.join(tmp, name + ".json.candidate")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(doc, handle)
            try:
                verify.load_thresholds(path)
            except SystemExit as exc:
                assert exc.code == 2, (name, exc.code)
            else:
                raise AssertionError("%s thresholds were accepted" % name)


def test_missing_enrollment_yields_excluded_no_enrollment():
    bank = bank_a()
    assert verify.member_embedding(bank, "Test Member A") is None
    assert verify.member_embedding(bank, "Test Member A", allow_unratified=True) is not None
    assert verify.member_embedding(bank, "Rep. Nobody") is None
    bank["members"]["Test Member A"]["ratified"] = ["reviewer-1"]
    assert verify.member_embedding(bank, "Test Member A") is not None
    record = verify.exclusion_record("unit-77", "Rep. Nobody", verify.REASON_NO_ENROLLMENT)
    assert record["unit_id"] == "unit-77"
    assert record["status"] == "EXCLUDED"
    assert record["reason_code"] == "EXCLUDED_NO_ENROLLMENT"
    assert record["check_t"] is None and record["check_a"] is None


def test_verify_cli_pins_bank_to_the_same_thresholds_artifact():
    with tempfile.TemporaryDirectory() as tmp:
        thresholds_path = write_thresholds(tmp)
        threshold_sha = hashlib.sha256(open(thresholds_path, "rb").read()).hexdigest()
        bank = {
            "schema": "b5-audio-enroll-bank-v1",
            "status": "RESEARCH_CANDIDATE_NOT_CONSUMED",
            "thresholds_sha256": "0" * 64,
            "members": {
                "Test Member A": {
                    "embedding": [1.0, 0.0],
                    "ratified": ["reviewer-1"],
                },
            },
        }
        bank_path = os.path.join(tmp, "bank.json.candidate")
        units_path = os.path.join(tmp, "units.json")
        out_path = os.path.join(tmp, "verdicts.json.candidate")
        with open(bank_path, "w", encoding="utf-8") as handle:
            json.dump(bank, handle)
        with open(units_path, "w", encoding="utf-8") as handle:
            json.dump([{"unit_id": "u1", "member": "Test Member A", "coverage": 1.0,
                        "segment_embeddings": [[1.0, 0.0]]}], handle)
        try:
            verify.main(["--thresholds", thresholds_path, "--bank", bank_path,
                         "--units", units_path, "--out", out_path])
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("verification accepted a bank calibrated to another threshold file")
        bank["thresholds_sha256"] = threshold_sha
        with open(bank_path, "w", encoding="utf-8") as handle:
            json.dump(bank, handle)
        verify.main(["--thresholds", thresholds_path, "--bank", bank_path,
                     "--units", units_path, "--out", out_path])
        out = json.load(open(out_path, encoding="utf-8"))
        assert out["thresholds_sha256"] == threshold_sha
        assert len(out["bank_sha256"]) == 64
        assert out["verdicts"][0]["status"] == "ELIGIBLE"


def test_verify_module_has_no_member_argmax():
    """Verification only: the module must never rank members to pick a winner."""
    with open(os.path.join(FEC, "members", "b5_audio_verify2.py"), encoding="utf-8") as handle:
        src = handle.read()
    assert "argmax" not in src, "verification module must not argmax over members"
    assert "def identify" not in src, "identification API is forbidden in this module"


# ------------------------------------------------------------------- harness

def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print("PASS %s" % fn.__name__)
        except BaseException:
            failed += 1
            print("FAIL %s" % fn.__name__)
            traceback.print_exc()
    print("test_audio_identity: %d passed, %d failed" % (passed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
