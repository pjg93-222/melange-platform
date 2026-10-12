import importlib.util
import builtins
import json
from argparse import Namespace
from pathlib import Path
import wave

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/classify_member_longform_full_turns_local.py"
spec = importlib.util.spec_from_file_location("local_full_turns", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_cue_segments_merge_overlaps_and_do_not_cap_turn_duration():
    cues = [
        {"start_sec": 100.0, "end_sec": 102.0},
        {"start_sec": 200.0, "end_sec": 202.0},
        {"start_sec": 2500.0, "end_sec": 2502.0},
    ]
    assert module.cue_segments(cues, 5_000.0, 600.0, 2) == [
        (10.0, 802.0), (2410.0, 3102.0)
    ]
    assert module.shared.MIN_COMPLETE_TURN_SECONDS == 3.0


def test_local_classifier_has_no_identity_or_profile_authority():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"speaker_identity_authority": False' in text
    assert '"final_profile_authority": False' in text
    assert '"maximum_turn_seconds": None' in text
    assert '"profile_effect_before_speaker_gate_and_human_review": "NONE"' in text
    assert '"--retry-screened-out"' in text


def resume_fixture():
    settings = module.classifier_settings(Namespace(
        model=Path("test-model"), context_seconds=1800.0, segments_per_member=2,
        max_new_tokens=1500, cpu_threads=6,
    ))
    source = {
        "member": "Example Member", "source_id": "recording-a",
        "wav_sha256": "audio-a", "transcript_sha256": "words-a",
        "cues": [{"phrase": "Example Member", "start_sec": 1.0, "end_sec": 2.0}],
    }
    existing = {
        **source, "name_cues": source["cues"], "classifier_settings": settings,
        "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED",
    }
    return source, existing, settings


def test_resume_reuses_only_current_evidence_and_settings():
    source, existing, settings = resume_fixture()
    assert not module.classification_needs_processing(source, existing, settings)
    assert module.classification_needs_processing(source, existing, settings, refresh=True)


@pytest.mark.parametrize("key", ["member", "source_id", "wav_sha256", "transcript_sha256"])
def test_resume_reclassifies_changed_or_missing_evidence_binding(key):
    source, existing, settings = resume_fixture()
    assert module.classification_needs_processing({**source, key: "replacement"}, existing, settings)
    assert module.classification_needs_processing({**source, key: None}, {**existing, key: None}, settings)


@pytest.mark.parametrize("key", [
    "model_id", "model_revision", "model_path", "context_seconds",
    "segments_per_member", "max_new_tokens", "cpu_threads", "contract_version",
])
def test_resume_reclassifies_changed_inference_request(key):
    source, existing, settings = resume_fixture()
    assert module.classification_needs_processing(source, existing, {**settings, key: "changed"})


def test_resume_reclassifies_legacy_rows_and_changed_cues():
    source, existing, settings = resume_fixture()
    legacy = {key: value for key, value in existing.items() if key != "classifier_settings"}
    assert module.classification_needs_processing(source, legacy, settings)
    assert module.classification_needs_processing({**source, "cues": []}, existing, settings)


def test_resume_keeps_explicit_error_and_screened_out_retry_behavior():
    source, existing, settings = resume_fixture()
    failed = {**existing, "status": "LOCAL_CLASSIFIER_ERROR"}
    assert module.classification_needs_processing(source, failed, settings)
    screened = {**existing, "status": "SCREENED_OUT"}
    assert not module.classification_needs_processing(source, screened, settings)
    assert module.classification_needs_processing(source, screened, settings, retry_screened_out=True)


def test_context_limit_uses_smallest_real_limit():
    assert module.context_limit(Namespace(max_position_embeddings=32768), Namespace(model_max_length=10**30)) == 32768
    assert module.context_limit(Namespace(max_position_embeddings=32768), Namespace(model_max_length=16384)) == 16384


def test_oversized_input_cannot_silently_truncate():
    module.require_token_budget(30000, 1500, 32768)
    with pytest.raises(ValueError, match="transcript was not truncated"):
        module.require_token_budget(32000, 1500, 32768)


def test_output_exhaustion_is_not_accepted_even_if_partial_json_parses():
    with pytest.raises(ValueError, match="output_budget_exhausted"):
        module.require_complete_output([1, 2, 3], 3, 99)
    module.require_complete_output([1, 2, 99], 3, [99, 100])
    module.require_complete_output([1, 99], 3, 99)


def test_multiple_sources_for_one_member_remain_separate_in_cache():
    source, existing, settings = resume_fixture()
    second = {**source, "source_id": "recording-b", "wav_sha256": "audio-b"}
    second_prior = {**existing, **second}
    cache = module.current_source_cache([source, second], [existing, second_prior])
    assert len(cache) == 2
    assert cache[(source["member"], "recording-a")] == existing
    assert cache[(source["member"], "recording-b")] == second_prior
    assert not module.classification_needs_processing(second, cache[module.source_key(second)], settings)


def test_cache_prunes_removed_sources_but_keeps_other_current_members():
    source, existing, _ = resume_fixture()
    other_current = {**source, "member": "Other Member", "source_id": "other-source"}
    other_prior = {**existing, **other_current}
    removed_source = {**existing, "source_id": "removed-source"}
    removed_member = {**existing, "member": "Removed Member"}
    cache = module.current_source_cache([source, other_current], [existing, other_prior, removed_source, removed_member])
    assert set(cache) == {module.source_key(source), module.source_key(other_current)}
    # --member selects inference requests after the whole-cohort cache is built.
    requested_sources = [row for row in [source, other_current] if row["member"] == source["member"]]
    assert len(requested_sources) == 1
    assert module.source_key(other_current) in cache


@pytest.mark.parametrize("duplicate_in", ["input", "prior_output"])
def test_duplicate_exact_member_source_pairs_are_refused(duplicate_in):
    source, existing, _ = resume_fixture()
    sources = [source, dict(source)] if duplicate_in == "input" else [source]
    priors = [existing, dict(existing)] if duplicate_in == "prior_output" else [existing]
    with pytest.raises(ValueError, match="missing_or_duplicate_source_binding:" + duplicate_in):
        module.current_source_cache(sources, priors)


@pytest.mark.parametrize("prior_contract", ["local-full-turn-complete-output-v3", "local-full-turn-source-bound-cache-v4"])
def test_older_cache_requires_explicit_v5_reclassification(prior_contract):
    source, existing, settings = resume_fixture()
    assert settings["contract_version"] == "local-full-turn-physical-input-bounds-v5"
    existing["classifier_settings"] = {**settings, "contract_version": prior_contract}
    assert module.classification_needs_processing(source, existing, settings)


def physical_fixture(tmp_path, *, last_end=10.0, channels=1):
    wav = tmp_path / "source.wav"
    with wave.open(str(wav), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(b"\0\0" * channels * 16000 * 10)
    transcript = tmp_path / "words.jsonl"
    words = [{"word": "Example", "start_sec": 1.0, "end_sec": 1.5},
             {"word": "Member", "start_sec": 1.5, "end_sec": 2.0},
             {"word": "answer", "start_sec": 2.0, "end_sec": last_end}]
    transcript.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
    return {"member": "Example Member", "source_id": "source", "status": "SELECTED_HARD_NAME_CUE_CANDIDATE",
            "wav": str(wav), "wav_sha256": module.SMR.sha256_file(wav),
            "transcript": str(transcript), "transcript_sha256": module.SMR.sha256_file(transcript),
            "cues": [{"phrase": "Example Member", "start_sec": 1.0, "end_sec": 2.0}]}


def test_shared_physical_bounds_allow_only_existing_tolerance(tmp_path):
    source = physical_fixture(tmp_path, last_end=10.09)
    assert module.physical_source_errors([source]) == []
    bad = physical_fixture(tmp_path, last_end=11.735)
    errors = module.physical_source_errors([bad])
    assert errors[0]["error"] == "transcript_timestamp_bounds_or_order"


@pytest.mark.parametrize("preflight", [True, False])
def test_out_of_audio_refusal_precedes_tokenizer_weights_or_generation(tmp_path, monkeypatch, capsys, preflight):
    source = physical_fixture(tmp_path, last_end=11.735)
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text(json.dumps(source) + "\n", encoding="utf-8")
    output, receipt = tmp_path / "output.jsonl", tmp_path / "receipt.json"
    before = Path(source["transcript"]).read_bytes()
    original_import = builtins.__import__
    def guard_import(name, *args, **kwargs):
        if name in {"torch", "transformers"}:
            raise AssertionError("model runtime must not load before physical validation")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guard_import)
    argv = [str(SCRIPT), "--input", str(manifest), "--output", str(output), "--receipt", str(receipt)]
    if preflight:
        argv.append("--preflight-only")
    monkeypatch.setattr("sys.argv", argv)
    assert module.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["model_weights_loaded"] is False
    assert result["transcript_clipped"] is False
    assert result["errors"][0]["error"] == "transcript_timestamp_bounds_or_order"
    assert not output.exists() and not receipt.exists()
    assert Path(source["transcript"]).read_bytes() == before


def test_physically_invalid_cache_cannot_be_admitted(tmp_path):
    source = physical_fixture(tmp_path)
    prior = {**source, "wav_sha256": "obsolete-audio-hash", "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"}
    cache = module.current_source_cache([source], [prior])
    errors = module.physical_source_errors([source] + list(cache.values()))
    assert errors == [{"member": source["member"], "source_id": source["source_id"], "error": "wav_hash_mismatch"}]


def test_wrong_pcm_format_is_refused(tmp_path):
    source = physical_fixture(tmp_path, channels=2)
    assert module.physical_source_errors([source])[0]["error"] == "source_wav_contract"


def test_nonmonotonic_or_nonfinite_words_are_refused(tmp_path):
    source = physical_fixture(tmp_path)
    transcript = Path(source["transcript"])
    for start in (0.5, float("nan")):
        words = [{"word": "one", "start_sec": 1, "end_sec": 2},
                 {"word": "two", "start_sec": start, "end_sec": 3}]
        transcript.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
        source["transcript_sha256"] = module.SMR.sha256_file(transcript)
        assert module.physical_source_errors([source])[0]["error"] == "transcript_timestamp_bounds_or_order"


def test_invalid_superseded_cache_does_not_block_valid_reclassification(tmp_path, monkeypatch):
    source = physical_fixture(tmp_path)
    manifest = tmp_path / "sources.jsonl"
    manifest.write_text(json.dumps(source) + "\n", encoding="utf-8")
    output = tmp_path / "output.jsonl"
    output.write_text(json.dumps({**source, "wav_sha256": "old-hash",
                                 "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"}) + "\n", encoding="utf-8")
    original_import = builtins.__import__
    def guard_import(name, *args, **kwargs):
        if name == "torch":
            raise RuntimeError("VALIDATION_PASSED_RUNTIME_NOT_LOADED_IN_TEST")
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guard_import)
    monkeypatch.setattr("sys.argv", [str(SCRIPT), "--input", str(manifest), "--output", str(output),
                                     "--receipt", str(tmp_path / "receipt.json")])
    with pytest.raises(RuntimeError, match="VALIDATION_PASSED_RUNTIME_NOT_LOADED_IN_TEST"):
        module.main()
