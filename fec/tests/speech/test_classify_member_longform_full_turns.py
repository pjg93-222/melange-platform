import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/classify_member_longform_full_turns.py"
spec = importlib.util.spec_from_file_location("classify_member_longform_full_turns", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def timed(text, start=0.0, width=1.0):
    return [
        {"word": token, "start_sec": start + index * width, "end_sec": start + (index + 1) * width}
        for index, token in enumerate(text.split())
    ]


def test_snap_preserves_a_multi_minute_turn_without_a_ceiling():
    words = timed("These are the opening words of my answer " + "detail " * 600)
    claim = {
        "start_sec": 0,
        "end_sec": 606,
        "opening_words": "These are the opening words",
        "handoff_basis": "host hands off; host resumes",
    }
    result = module.snap_complete_turn(claim, words)
    assert result is not None
    assert result["duration_seconds"] > 600


def test_opening_words_must_be_literal_and_boundary_local():
    words = timed("These are the opening words of my answer and more detail")
    claim = {
        "start_sec": 0,
        "end_sec": 10,
        "opening_words": "not actually in this transcript",
        "handoff_basis": "claimed",
    }
    assert module.snap_complete_turn(claim, words) is None


def test_name_cue_must_precede_first_turn():
    cues = [{"start_sec": 5, "end_sec": 7}]
    assert module.cue_precedes_first_turn(cues, [{"start_sec": 30}]) is True
    assert module.cue_precedes_first_turn(cues, [{"start_sec": 400}]) is False


def test_classifier_output_cannot_directly_change_profiles():
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"maximum_turn_seconds": None' in text
    assert '"multi_minute_turns_preserved_as_one_turn": True' in text
    assert '"profile_effect_before_speaker_gate_and_review": "NONE"' in text


def source_fixture(tmp_path):
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(16000)
        target.writeframes(b"\x01\x00" * 16000 * 40)
    words = timed("These are the opening words of my complete answer and a few more details before the host resumes", start=3)
    transcript = tmp_path / "words.jsonl"
    transcript.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
    return {"member": "Example Member", "source_id": "recording-one", "wav": str(audio),
            "wav_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "transcript": str(transcript), "transcript_sha256": hashlib.sha256(transcript.read_bytes()).hexdigest(),
            "status": "SELECTED_HARD_NAME_CUE_CANDIDATE", "cues": [{"start_sec": 0, "end_sec": 1, "phrase": "Example Member"}]}


def fake_factory(calls, *, stop_reason="end_turn", error=None):
    raw = json.dumps({"identity_sequence": "MATCH", "identity_basis": "Named host handoff.", "turns": [
        {"start_sec": 3, "end_sec": 20, "opening_words": "These are the opening words", "handoff_basis": "Both handoffs clear."}]})
    class Client:
        def __init__(self, **kwargs):
            calls.append({"client": kwargs})
            self.messages = self
        def create(self, **kwargs):
            calls.append({"request": kwargs})
            if error:
                raise error
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=raw)], stop_reason=stop_reason,
                                   usage={"input_tokens": 432, "output_tokens": 123}, id="response-test", model=module.MODEL)
    return Client


def test_explicit_timeout_zero_retries_and_exact_usage(tmp_path):
    source, calls = source_fixture(tmp_path), []
    result = module.classify_source(source, "test-secret", module.settings(16000, 180), "manifest-hash",
                                    client_factory=fake_factory(calls))
    assert calls[0]["client"] == {"api_key": "test-secret", "timeout": 180, "max_retries": 0}
    assert len([call for call in calls if "request" in call]) == 1
    assert result["status"] == "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"
    assert result["usage"] == {"input_tokens": 432, "output_tokens": 123}
    assert result["billing_cost_usd"] is None
    assert result["speaker_identity_authority"] is False
    assert result["final_profile_authority"] is False
    assert result["human_semantic_review"] is False


@pytest.mark.parametrize("reason", ["max_tokens", "pause_turn", "tool_use", None])
def test_noncompleted_response_is_refused_even_with_valid_json(tmp_path, reason):
    calls = []
    result = module.classify_source(source_fixture(tmp_path), "key", module.settings(8, 20), "input",
                                    client_factory=fake_factory(calls, stop_reason=reason))
    assert result["status"] == "CLASSIFIER_RESPONSE_REFUSED"
    assert result["turns"] == []
    assert result["usage"]["output_tokens"] == 123
    assert len(calls) == 2
    if reason == "max_tokens":
        assert result["refusal_reason"] == "truncated_max_tokens"


@pytest.mark.parametrize("kind", ["wav", "transcript"])
def test_bad_physical_input_fails_before_api_request(tmp_path, kind):
    source, calls = source_fixture(tmp_path), []
    source[kind + "_sha256"] = "wrong-hash"
    result = module.classify_source(source, "key", module.settings(8000, 20), "input",
                                    client_factory=fake_factory(calls))
    assert result["status"] == "CLASSIFIER_ERROR"
    assert result["api_requests_attempted"] == 0
    assert not calls


def test_auth_or_credit_error_is_not_retried_and_key_is_redacted(tmp_path):
    source, calls = source_fixture(tmp_path), []
    error = RuntimeError("credit failed for test-secret")
    result = module.classify_source(source, "test-secret", module.settings(8000, 20), "input",
                                    client_factory=fake_factory(calls, error=error))
    assert result["status"] == "CLASSIFIER_ERROR"
    assert result["api_requests_attempted"] == 1
    assert len(calls) == 2
    assert "test-secret" not in json.dumps(result)
    assert "[REDACTED]" in result["error"]


def test_prompt_includes_entire_transcript_without_character_cap(tmp_path):
    source, calls = source_fixture(tmp_path), []
    path = Path(source["transcript"])
    words = [json.loads(line) for line in path.read_text().splitlines()]
    words[-1]["word"] = "detail " * 40000 + "FINAL_TRANSCRIPT_MARKER"
    path.write_text("".join(json.dumps(word) + "\n" for word in words), encoding="utf-8")
    source["transcript_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    result = module.classify_source(source, "key", module.settings(8000, 20), "input",
                                    client_factory=fake_factory(calls))
    sent = calls[1]["request"]["messages"][0]["content"]
    assert len(sent) > 240000
    assert sent.endswith("FINAL_TRANSCRIPT_MARKER")
    assert result["prompt_characters"] == len(sent)
    assert result["prompt_sha256"] == hashlib.sha256(sent.encode()).hexdigest()


def test_resume_binds_source_hashes_input_row_and_settings(tmp_path):
    source = source_fixture(tmp_path)
    config = module.settings(8000, 20)
    existing = {**source, "source_input_sha256": module.row_hash(source), "classifier_settings": config,
                "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED"}
    assert not module.needs_processing(source, existing, config)
    for key in ("member", "source_id", "wav_sha256", "transcript_sha256"):
        assert module.needs_processing({**source, key: "different"}, existing, config)
    assert module.needs_processing({**source, "cues": []}, existing, config)
    assert module.needs_processing(source, existing, module.settings(16000, 20))
    assert module.needs_processing(source, existing, module.settings(8000, 40))
    existing["status"] = "CLASSIFIER_ERROR"
    assert not module.needs_processing(source, existing, config)
    assert module.needs_processing(source, existing, config, retry_errors=True)


def test_request_budget_refuses_before_reading_key(tmp_path, monkeypatch):
    source = source_fixture(tmp_path)
    input_path = tmp_path / "input.jsonl"
    input_path.write_text(json.dumps(source) + "\n" + json.dumps({**source, "source_id": "recording-two"}) + "\n")
    monkeypatch.setattr("sys.argv", ["classifier", "--input", str(input_path), "--output", str(tmp_path / "out.jsonl"),
                                   "--receipt", str(tmp_path / "receipt.json"), "--key", str(tmp_path / "NO-KEY"),
                                   "--max-requests", "1"])
    with pytest.raises(SystemExit, match="request budget exceeded before API use"):
        module.main()


def test_two_recordings_for_one_member_use_separate_resume_keys(tmp_path, monkeypatch):
    source = source_fixture(tmp_path)
    input_path = tmp_path / "input.jsonl"
    output = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    key = tmp_path / "key.txt"
    key.write_text("fake-test-only-key")
    input_path.write_text(json.dumps(source) + "\n" + json.dumps({**source, "source_id": "recording-two"}) + "\n")
    calls = []
    def fake_classify(row, api_key, config, input_hash):
        calls.append(module.source_key(row))
        return {**row, "source_input_sha256": module.row_hash(row), "classifier_settings": config,
                "status": "SCREENED_OUT", "turns": [], "api_requests_attempted": 1}
    monkeypatch.setattr(module, "classify_source", fake_classify)
    monkeypatch.setattr("sys.argv", ["classifier", "--input", str(input_path), "--output", str(output),
                                   "--receipt", str(receipt), "--key", str(key), "--workers", "1", "--max-requests", "2"])
    assert module.main() == 0
    assert set(calls) == {("Example Member", "recording-one"), ("Example Member", "recording-two")}
    assert len(output.read_text().splitlines()) == 2
    calls.clear()
    key.unlink()
    assert module.main() == 0
    assert calls == []


def test_member_filter_preserves_other_members_prior_rows(tmp_path, monkeypatch):
    source = source_fixture(tmp_path)
    other = {**source, "member": "Other Member"}
    input_path, output = tmp_path / "input.jsonl", tmp_path / "out.jsonl"
    key = tmp_path / "key.txt"
    key.write_text("fake-test-only-key")
    input_path.write_text(json.dumps(source) + "\n" + json.dumps(other) + "\n")
    prior = {**other, "source_input_sha256": module.row_hash(other), "classifier_settings": module.settings(8000, 180),
             "status": "SCREENED_OUT", "turns": [], "api_requests_attempted": 1}
    output.write_text(json.dumps(prior) + "\n")
    monkeypatch.setattr(module, "classify_source", lambda row, k, config, h: {
        **row, "source_input_sha256": module.row_hash(row), "classifier_settings": config,
        "status": "SCREENED_OUT", "turns": [], "api_requests_attempted": 1})
    monkeypatch.setattr("sys.argv", ["classifier", "--input", str(input_path), "--output", str(output),
                                   "--receipt", str(tmp_path / "receipt.json"), "--key", str(key), "--workers", "1",
                                   "--member", "Example Member"])
    assert module.main() == 0
    members = {json.loads(line)["member"] for line in output.read_text().splitlines()}
    assert members == {"Example Member", "Other Member"}


@pytest.mark.parametrize("status, kept", [
    ("FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED", False),
    ("SCREENED_OUT", False),
    ("CLASSIFIER_ERROR", True),
    ("CLASSIFIER_RESPONSE_REFUSED", True),
])
def test_member_filter_drops_other_members_stale_rows_but_keeps_failures(tmp_path, monkeypatch, status, kept):
    source = source_fixture(tmp_path)
    other = {**source, "member": "Other Member"}
    input_path, output, receipt = tmp_path / "input.jsonl", tmp_path / "out.jsonl", tmp_path / "receipt.json"
    key = tmp_path / "key.txt"
    key.write_text("fake-test-only-key")
    input_path.write_text(json.dumps(source) + "\n" + json.dumps(other) + "\n")
    # The prior row was classified from an earlier version of Other Member's input row.
    prior = {**other, "source_input_sha256": module.row_hash({**other, "cues": []}),
             "classifier_settings": module.settings(8000, 180), "status": status, "turns": [], "api_requests_attempted": 1}
    output.write_text(json.dumps(prior) + "\n")
    monkeypatch.setattr(module, "classify_source", lambda row, k, config, h: {
        **row, "source_input_sha256": module.row_hash(row), "classifier_settings": config,
        "status": "SCREENED_OUT", "turns": [], "api_requests_attempted": 1})
    monkeypatch.setattr("sys.argv", ["classifier", "--input", str(input_path), "--output", str(output),
                                   "--receipt", str(receipt), "--key", str(key), "--workers", "1",
                                   "--member", "Example Member"])
    assert module.main() == 0
    rows = {json.loads(line)["member"]: json.loads(line) for line in output.read_text().splitlines()}
    assert ("Other Member" in rows) is kept
    if kept:
        assert rows["Other Member"]["status"] == status
    assert json.loads(receipt.read_text())["status"] == ("PARTIAL_VISIBLE" if kept else "PASS_CANDIDATES_ONLY")


@pytest.mark.parametrize("input_changed", [False, True])
def test_member_filter_checks_other_members_rows_against_their_own_settings(tmp_path, monkeypatch, input_changed):
    # A non-default --timeout-seconds run must not discard another member's paid result for the settings
    # difference alone; only a change to that member's input drops the row.
    source = source_fixture(tmp_path)
    other = {**source, "member": "Other Member"}
    input_path, output, receipt = tmp_path / "input.jsonl", tmp_path / "out.jsonl", tmp_path / "receipt.json"
    key = tmp_path / "key.txt"
    key.write_text("fake-test-only-key")
    prior = {**other, "source_input_sha256": module.row_hash(other), "classifier_settings": module.settings(8000, 180),
             "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED", "turns": [{"duration_seconds": 17.0}]}
    output.write_text(json.dumps(prior) + "\n")
    current_other = {**other, "cues": []} if input_changed else other
    input_path.write_text(json.dumps(source) + "\n" + json.dumps(current_other) + "\n")
    monkeypatch.setattr(module, "classify_source", lambda row, k, config, h: {
        **row, "source_input_sha256": module.row_hash(row), "classifier_settings": config,
        "status": "SCREENED_OUT", "turns": [], "api_requests_attempted": 1})
    monkeypatch.setattr("sys.argv", ["classifier", "--input", str(input_path), "--output", str(output),
                                   "--receipt", str(receipt), "--key", str(key), "--workers", "1",
                                   "--member", "Example Member", "--timeout-seconds", "300"])
    assert module.main() == 0
    rows = {json.loads(line)["member"]: json.loads(line) for line in output.read_text().splitlines()}
    assert ("Other Member" in rows) is not input_changed
    if not input_changed:
        assert rows["Other Member"] == prior
    assert rows["Example Member"]["classifier_settings"] == module.settings(8000, 300.0)
    assert json.loads(receipt.read_text())["counts"]["sources_with_full_turn_candidates"] == (0 if input_changed else 1)
