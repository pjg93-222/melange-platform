import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import wave

import pytest

from fec.speech import build_member_longform_turn_features as mod
from fec.speech import verify_member_longform_speaker_continuity as checks


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def json_file(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def jsonl(path, values):
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def wav(path, seconds, value=b"\x01\x00"):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(value * round(seconds * 16000))


def features(path):
    return {key: float(index + 1) for index, key in enumerate(mod.FEATURE_COLUMNS)}


def case(tmp_path, modern=False, duration=183.25):
    source_wav = tmp_path / "source.wav"
    turn_wav = tmp_path / "turn.wav"
    words_path = tmp_path / "words.jsonl"
    wav(source_wav, duration + 4)
    wav(turn_wav, duration)
    words = [{"word": "word" + str(index), "start_sec": 2 + index,
              "end_sec": 2.5 + index} for index in range(20)]
    jsonl(words_path, words)
    source = {"member": "Example Member", "source_id": "source", "wav": str(source_wav),
              "wav_sha256": sha(source_wav), "published": "2020-01-01", "channel": "Example interview"}
    transcript = {**source, "transcript": str(words_path), "transcript_sha256": sha(words_path)}
    source_path, transcript_path = tmp_path / "sources.jsonl", tmp_path / "transcripts.jsonl"
    jsonl(source_path, [source])
    jsonl(transcript_path, [transcript])
    leadership = tmp_path / "leadership.json"
    json_file(leadership, {"members": {}})
    turn = {
        "schema": "member-interview-long-turn-v1",
        "status": "HUMAN_BOUNDARY_REVIEWED_SOURCE_IDENTITY_VERIFIED",
        "turn_id": "example-01", "member": "Example Member", "source_id": "source",
        "title": "Example interview", "channel": "Example producer", "url": "https://example.test/interview",
        "source_audio_sha256": sha(source_wav), "source_transcript_sha256": sha(words_path),
        "turn_audio_wav": str(turn_wav), "turn_audio_sha256": sha(turn_wav),
        "start_sec": 2., "end_sec": duration + 2, "duration_seconds": duration,
        "word_count": len(words), "transcript_text": " ".join(word["word"] for word in words),
        "contract": {"complete_reviewed_turn_not_fixed_window": True,
                     "maximum_turn_seconds": None, "automatic_trait_or_rank_effect": False},
        "identity_anchor_windows_inside_turn": 3,
    }
    turn_path, receipt_path = tmp_path / "turns.jsonl", tmp_path / "turns.receipt.json"
    receipt = {"schema": mod.MODERN_RECEIPT if modern else "member-interview-long-turn-receipt-v1",
               "status": "PASS", "artifact": str(turn_path.resolve()), "counts": {"turns": 1}, "inputs": {}}
    chain = {}
    chain_rows = {}
    if modern:
        turn.update(human_reviewer="Example Reviewer", human_reviewed_at="2026-09-04T13:00:00+00:00")
        base = {**transcript}
        chain_rows = {
            "selection": {**base, "status": "SELECTED_HARD_NAME_CUE_CANDIDATE", "cues": [{"phrase": "Example Member"}]},
            "candidates": {**base, "status": "FULL_TURN_CANDIDATE_IDENTITY_UNVERIFIED",
                           "turns": [{"start_sec": 2., "end_sec": duration + 2}]},
            "continuity": {**base, "evaluated_windows": [
                {"turn_index": 0, "window_index": index, "accepted": True} for index in range(3)]},
            "reviews": {"member": "Example Member", "source_id": "source", "turn_id": "example-01",
                        "start_sec": 2., "end_sec": duration + 2,
                        "reviewer_type": "HUMAN", "reviewer_name": "Example Reviewer",
                        "reviewed_at": turn["human_reviewed_at"],
                        "review_attestation": "personally_listened_and_reviewed_identity_boundaries_and_semantics",
                        "review_status": "ACCEPT", "identity_review": "ACCEPT", "boundary_review": "ACCEPT",
                        "semantic_review": "ACCEPT", "identity_review_note": "Named member heard through complete turn.",
                        "boundary_review_note": "Listened to both handoffs.",
                        "semantic_findings": [{"kind": "public_reply", "finding": "A multi-minute answer.",
                                               "meeting_implication": "Confirm the allotted meeting duration.",
                                               "meeting_phase": "open", "scope": "One public answer only."}]},
        }
        candidate = chain_rows["candidates"]
        candidate_turn = candidate["turns"][0]
        windows = [{"turn_index": 0, "window_index": index, "start_sec": start,
                    "end_sec": start + 8, "accepted": True, "anchor_eligible": True,
                    "turn_binding_sha256": checks.turn_binding_sha256(candidate, candidate_turn)}
                   for index, start in enumerate(checks.candidate_starts(candidate_turn))]
        coverage = checks.summarize_turn_coverage(candidate, candidate_turn, 0, windows, budget_exhausted=False)
        chain_rows["continuity"].update(
            evaluated_windows=windows, coverage_contract_version=checks.CONTINUITY_CONTRACT_VERSION,
            turn_coverage=[coverage],
        )
        chain_rows["reviews"].update(wav_sha256=turn["source_audio_sha256"],
                                     transcript_sha256=turn["source_transcript_sha256"],
                                     candidate_turn_binding_sha256=coverage["turn_binding_sha256"])
        turn.update(turn_coverage=coverage, candidate_turn_binding_sha256=coverage["turn_binding_sha256"],
                    identity_review=chain_rows["reviews"]["identity_review_note"],
                    boundary_review=chain_rows["reviews"]["boundary_review_note"],
                    semantic_findings=copy.deepcopy(chain_rows["reviews"]["semantic_findings"]))
        chain = {name: tmp_path / (name + ".jsonl") for name in chain_rows}
    fixture = SimpleNamespace(**locals())
    save(fixture)
    return fixture


def save(fixture):
    for name, row in fixture.chain_rows.items():
        jsonl(fixture.chain[name], [row])
        fixture.receipt["inputs"][name + "_sha256"] = sha(fixture.chain[name])
    if fixture.modern:
        fixture.turn["review_row_sha256"] = mod.row_hash(fixture.chain_rows["reviews"])
    fixture.turn["row_sha256"] = mod.row_hash({key: value for key, value in fixture.turn.items() if key != "row_sha256"})
    jsonl(fixture.turn_path, [fixture.turn])
    fixture.receipt["artifact_sha256"] = sha(fixture.turn_path)
    json_file(fixture.receipt_path, fixture.receipt)


def build(fixture, **kwargs):
    return mod.build_artifact([(fixture.turn_path, fixture.receipt_path)], [fixture.source_path],
                              [fixture.transcript_path], fixture.leadership, measure=features,
                              review_chain=fixture.chain, generated_at="2026-09-04T15:00:00Z", **kwargs)


@pytest.mark.parametrize("mutation", ["source", "artifact", "producer_binding"])
def test_measurement_reuse_requires_unchanged_sources_artifact_and_producer(tmp_path, mutation):
    fixture = case(tmp_path)
    artifact = build(fixture)
    out, receipt_path = tmp_path / "features.json", tmp_path / "features.receipt.json"
    json_file(out, artifact)
    receipt = {
        "schema": "member-longform-turn-features-receipt-v1", "status": "PASS_MEASUREMENT_ONLY",
        "artifact": str(out.resolve()), "artifact_sha256": sha(out),
        "contract": artifact["contract"], "inputs": artifact["inputs"],
    }
    json_file(receipt_path, receipt)
    assert mod.current_measurement(out, receipt_path)
    if mutation == "source":
        fixture.words_path.write_text("changed source", encoding="utf-8")
    elif mutation == "artifact":
        artifact["turns"][0]["profile_measurement_eligible"] = True
        json_file(out, artifact)
    else:
        producer = str(Path(mod.__file__).resolve())
        receipt["inputs"] = [item for item in receipt["inputs"] if item["path"] != producer]
        artifact["inputs"] = receipt["inputs"]
        json_file(out, artifact)
        receipt["artifact_sha256"] = sha(out)
        json_file(receipt_path, receipt)
    assert not mod.current_measurement(out, receipt_path)


def test_long_complete_turn_measured_without_ceiling_and_anchors_untouched(tmp_path):
    fixture = case(tmp_path, duration=601.125)
    before = {path: sha(path) for path in (fixture.turn_wav, fixture.source_wav, fixture.turn_path)}
    seen = []
    def measured(path):
        seen.append(path)
        with wave.open(str(path)) as audio:
            assert audio.getnframes() / audio.getframerate() == 601.125
        return features(path)
    result = mod.build_artifact([(fixture.turn_path, fixture.receipt_path)], [fixture.source_path],
                                [fixture.transcript_path], fixture.leadership, measure=measured)
    row = result["turns"][0]
    assert seen == [fixture.turn_wav.resolve()]
    assert row["measured_duration_seconds"] == 601.125
    assert row["identity_anchor_windows_inside_turn"] == 3
    assert result["contract"]["maximum_turn_seconds"] is None
    assert {path: sha(path) for path in before} == before


def test_legacy_status_never_conveys_new_review_authority(tmp_path):
    result = build(case(tmp_path))
    row = result["turns"][0]
    assert row["authority"] == "LEGACY_REVIEW_AUTHORITY_UNRESOLVED"
    assert row["profile_measurement_eligible"] is False
    assert row["baseline_measurement_eligible"] is False
    assert result["members"]["Example Member"]["candidate_only"]["turns"] == 1
    assert result["counts"]["qualified_turns"] == 0
    assert result["contract"]["automatic_trait_or_rank_effect"] is False
    assert result["contract"]["stance_allocation_or_persuasion_effect"] is False


def test_legacy_added_human_labels_cannot_qualify(tmp_path):
    fixture = case(tmp_path)
    fixture.turn.update(human_reviewer="Someone", human_reviewed_at="2026-09-04")
    save(fixture)
    assert build(fixture)["turns"][0]["profile_measurement_eligible"] is False


def test_modern_full_review_chain_is_qualified_but_never_reranks(tmp_path):
    result = build(case(tmp_path, modern=True))
    row = result["turns"][0]
    assert row["authority"] == "HASH_BOUND_NAMED_HUMAN_REVIEW"
    assert row["profile_measurement_eligible"] is True
    assert row["baseline_measurement_eligible"] is True
    assert row["new_human_review_performed_by_measurement_builder"] is False
    assert result["members"]["Example Member"]["qualified_baseline"]["turns"] == 1
    assert result["members"]["Example Member"]["recurring_communication_pattern_established"] is False


def test_modern_missing_review_chain_is_candidate_only(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.chain = {}
    row = build(fixture)["turns"][0]
    assert row["authority"] == "MODERN_REVIEW_CHAIN_NOT_PROVIDED"
    assert row["profile_measurement_eligible"] is False


@pytest.mark.parametrize("field,value,match", [
    ("reviewer_type", "AI", "named_human_review_missing"),
    ("reviewer_name", "", "named_human_review_missing"),
    ("review_attestation", "reviewed metadata", "whole_turn_review_attestation_missing"),
    ("identity_review", "PENDING", "human_review_not_accepted"),
    ("boundary_review_note", "", "human_review_notes_missing"),
    ("source_id", "different", "human_review_turn_binding"),
    ("end_sec", 12., "human_review_turn_binding"),
    ("reviewed_at", None, "human_review_provenance_missing"),
])
def test_modern_human_review_gate_cannot_be_bypassed(tmp_path, field, value, match):
    fixture = case(tmp_path, modern=True)
    fixture.chain_rows["reviews"][field] = value
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match=match):
        build(fixture)


def test_modern_identity_anchors_still_required(tmp_path):
    fixture = case(tmp_path, modern=True)
    for window in fixture.chain_rows["continuity"]["evaluated_windows"]:
        window["accepted"] = False
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match="complete_turn_continuity_not_verified"):
        build(fixture)


def test_review_chain_receipt_hash_is_required(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.chain_rows["reviews"]["reviewer_name"] = "Changed person"
    jsonl(fixture.chain["reviews"], [fixture.chain_rows["reviews"]])
    with pytest.raises(mod.FeatureRefusal, match="input_hash_mismatch"):
        build(fixture)


def test_only_three_early_windows_cannot_qualify_a_long_turn(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.chain_rows["continuity"]["evaluated_windows"] = fixture.chain_rows["continuity"]["evaluated_windows"][:3]
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match="complete_turn_continuity_not_verified"):
        build(fixture)


def test_stored_turn_coverage_cannot_replace_recomputed_coverage(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.turn["turn_coverage"] = {**fixture.turn["turn_coverage"], "continuity_gate_passed": False}
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match="materialized_turn_coverage_binding"):
        build(fixture)


def test_review_timestamp_requires_timezone(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.chain_rows["reviews"]["reviewed_at"] = "2026-09-04T13:00:00"
    fixture.turn["human_reviewed_at"] = "2026-09-04T13:00:00"
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match="human_review_date_invalid"):
        build(fixture)


def test_missing_semantic_review_findings_not_silently_accepted(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.chain_rows["reviews"]["semantic_findings"] = []
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match="human_review_semantics_incomplete"):
        build(fixture)


def test_pure_description_needs_no_meeting_advice(tmp_path):
    fixture = case(tmp_path, modern=True)
    finding = {"kind": "reply_duration", "finding": "An extended public answer.", "scope": "One public interview."}
    fixture.chain_rows["reviews"]["semantic_findings"] = [finding]
    fixture.turn["semantic_findings"] = [finding]
    save(fixture)
    assert build(fixture)["turns"][0]["profile_measurement_eligible"] is True


@pytest.mark.parametrize("key,match", [("wav_sha256", "human_review_source_hash_binding"),
                                      ("transcript_sha256", "human_review_source_hash_binding"),
                                      ("candidate_turn_binding_sha256", "human_review_candidate_binding")])
def test_human_review_must_bind_source_and_candidate_hashes(tmp_path, key, match):
    fixture = case(tmp_path, modern=True)
    fixture.chain_rows["reviews"][key] = "old-or-wrong-hash"
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match=match):
        build(fixture)


def test_modern_receipt_input_paths_can_resolve_hash_bound_review_chain(tmp_path):
    fixture = case(tmp_path, modern=True)
    fixture.receipt["input_paths"] = {name: str(path) for name, path in fixture.chain.items()}
    save(fixture)
    fixture.chain = {}
    assert build(fixture)["turns"][0]["profile_measurement_eligible"] is True


@pytest.mark.parametrize("published,expected,baseline", [
    ("2019-01-01", "PRE_CURRENT_LEADERSHIP_ROLE", True),
    ("2023-01-03", "CURRENT_ROLE_CONDITIONED", False),
    ("2025-01-01", "CURRENT_ROLE_CONDITIONED", False),
    (None, "LEADERSHIP_SOURCE_DATE_UNRESOLVED", False),
])
def test_current_leader_baseline_pre_role_gate(tmp_path, published, expected, baseline):
    fixture = case(tmp_path, modern=True)
    fixture.source["published"] = published
    jsonl(fixture.source_path, [fixture.source])
    json_file(fixture.leadership, {"members": {"Example Member": {"current_role_assumed_on": "2023-01-03"}}})
    row = build(fixture)["turns"][0]
    assert row["source_era"] == expected
    assert row["baseline_measurement_eligible"] is baseline


@pytest.mark.parametrize("match_hold", [True, False])
def test_exact_quality_hold_blocks_qualification_without_altering_measurements(tmp_path, match_hold):
    fixture = case(tmp_path, modern=True)
    hold_path = tmp_path / "holds.jsonl"
    jsonl(hold_path, [{"member": fixture.turn["member"], "source_id": fixture.turn["source_id"],
                      "wav_sha256": fixture.turn["source_audio_sha256"],
                      "transcript_sha256": fixture.turn["source_transcript_sha256"] if match_hold else "oldhash"}])
    row = build(fixture, quality_holds_path=hold_path)["turns"][0]
    assert row["quality_hold"] is match_hold
    assert row["profile_measurement_eligible"] is not match_hold
    assert row["features"] == features(None)


def test_source_audio_hash_mismatch_refused(tmp_path):
    fixture = case(tmp_path)
    wav(fixture.source_wav, fixture.duration + 4, b"\x02\x00")
    with pytest.raises(mod.FeatureRefusal, match="input_hash_mismatch"):
        build(fixture)


def test_turn_hash_mismatch_refused(tmp_path):
    fixture = case(tmp_path)
    wav(fixture.turn_wav, fixture.duration, b"\x02\x00")
    with pytest.raises(mod.FeatureRefusal, match="input_hash_mismatch"):
        build(fixture)


def test_turn_must_be_exact_source_pcm_even_with_updated_own_hash(tmp_path):
    fixture = case(tmp_path)
    wav(fixture.turn_wav, fixture.duration, b"\x02\x00")
    fixture.turn["turn_audio_sha256"] = sha(fixture.turn_wav)
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match="turn_pcm_not_exact_source_slice"):
        build(fixture)


def test_transcript_physical_hash_required(tmp_path):
    fixture = case(tmp_path)
    fixture.words_path.write_text("", encoding="utf-8")
    with pytest.raises(mod.FeatureRefusal, match="input_hash_mismatch"):
        build(fixture)


@pytest.mark.parametrize("field,value,match", [
    ("word_count", 99, "turn_transcript_word_count_mismatch"),
    ("transcript_text", "Unrelated transcript", "turn_transcript_text_mismatch"),
    ("duration_seconds", 8., "invalid_turn_duration"),
    ("status", "CANDIDATE", "unexpected_materialized_turn_status"),
    ("source_id", "another", "source_binding_missing"),
])
def test_turn_claim_validation(tmp_path, field, value, match):
    fixture = case(tmp_path)
    fixture.turn[field] = value
    save(fixture)
    with pytest.raises(mod.FeatureRefusal, match=match):
        build(fixture)


def test_row_hash_required_even_when_outer_receipt_current(tmp_path):
    fixture = case(tmp_path)
    fixture.turn["word_count"] = 99
    jsonl(fixture.turn_path, [fixture.turn])
    fixture.receipt["artifact_sha256"] = sha(fixture.turn_path)
    json_file(fixture.receipt_path, fixture.receipt)
    with pytest.raises(mod.FeatureRefusal, match="turn_row_hash_mismatch"):
        build(fixture)


@pytest.mark.parametrize("receipt_field,value,match", [
    ("status", "PENDING", "invalid_turn_receipt"),
    ("artifact_sha256", "wrong", "input_hash_mismatch"),
    ("counts", {"turns": 2}, "turn_receipt_count_mismatch"),
    ("artifact", "different.jsonl", "turn_receipt_path_mismatch"),
])
def test_materialization_receipt_required(tmp_path, receipt_field, value, match):
    fixture = case(tmp_path)
    fixture.receipt[receipt_field] = value
    json_file(fixture.receipt_path, fixture.receipt)
    with pytest.raises(mod.FeatureRefusal, match=match):
        build(fixture)


def test_duplicate_or_overlapping_turn_cannot_double_count_audio(tmp_path):
    fixture = case(tmp_path)
    duplicate = {**fixture.turn, "turn_id": "example-02"}
    duplicate["row_sha256"] = mod.row_hash({key: value for key, value in duplicate.items() if key != "row_sha256"})
    jsonl(fixture.turn_path, [fixture.turn, duplicate])
    fixture.receipt["artifact_sha256"] = sha(fixture.turn_path)
    fixture.receipt["counts"]["turns"] = 2
    json_file(fixture.receipt_path, fixture.receipt)
    with pytest.raises(mod.FeatureRefusal, match="duplicate_or_overlapping_source_interval"):
        build(fixture)


def test_nonfinite_feature_is_refused(tmp_path):
    fixture = case(tmp_path)
    with pytest.raises(mod.FeatureRefusal, match="invalid_measured_features"):
        mod.build_artifact([(fixture.turn_path, fixture.receipt_path)], [fixture.source_path],
                           [fixture.transcript_path], fixture.leadership,
                           measure=lambda path: {**features(path), "pace": float("nan")})


def test_all_physical_dependencies_are_in_receipt_inputs_and_rows_hashed(tmp_path):
    fixture = case(tmp_path, modern=True)
    result = build(fixture)
    expected = {fixture.source_wav, fixture.turn_wav, fixture.words_path, fixture.turn_path,
                fixture.receipt_path, fixture.source_path, fixture.transcript_path,
                fixture.leadership, Path(mod.__file__), *fixture.chain.values(),
                Path(mod.__file__).with_name("build_member_longform_review_queue.py"),
                Path(mod.__file__).with_name("verify_member_longform_speaker_continuity.py")}
    assert {item["path"] for item in result["inputs"]} == {str(path.resolve()) for path in expected}
    assert all(item["sha256"] == sha(item["path"]) for item in result["inputs"])
    row = result["turns"][0]
    assert row["row_sha256"] == mod.row_hash({key: value for key, value in row.items() if key != "row_sha256"})


def test_output_is_reproducible_and_never_imports_existing_trait_fields(tmp_path):
    fixture = case(tmp_path)
    first = build(fixture)
    fixture.source.update(personality="assertive", motivating_appeals=["fear"], gini=.99)
    jsonl(fixture.source_path, [fixture.source])
    second = build(fixture)
    assert first["turns"] == second["turns"]
    assert first["members"] == second["members"]
    assert second == build(fixture)


def test_aggregate_keeps_candidate_and_era_strata_separate(tmp_path):
    fixture = case(tmp_path, modern=True)
    base = build(fixture)["turns"][0]
    role = {**copy.deepcopy(base), "turn_id": "role", "source_era": "CURRENT_ROLE_CONDITIONED",
            "baseline_measurement_eligible": False, "features": {key: 10. for key in mod.FEATURE_COLUMNS}}
    candidate = {**copy.deepcopy(base), "turn_id": "candidate", "profile_measurement_eligible": False,
                 "baseline_measurement_eligible": False, "features": {key: 100. for key in mod.FEATURE_COLUMNS}}
    groups = mod.aggregate([base, role, candidate])["Example Member"]
    assert groups["qualified_baseline"]["source_groups"][0]["features_arithmetic_mean_over_turns"]["pause"] == 1
    assert groups["qualified_role_conditioned"]["source_groups"][0]["features_arithmetic_mean_over_turns"]["pause"] == 10
    assert groups["candidate_only"]["source_groups"][0]["features_arithmetic_mean_over_turns"]["pause"] == 100


def test_alias_map_exactly_matches_existing_short_window_conventions():
    import ast
    for name in ("verify_member_interview_sources.py", "build_descriptive_speech.py"):
        tree = ast.parse((mod.ROOT / "fec/speech" / name).read_text(encoding="utf-8"))
        mapping = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "FEATURE_COLUMNS" for target in node.targets))
        assert mapping == mod.FEATURE_COLUMNS


def test_cli_receipt_contract(tmp_path, monkeypatch):
    fixture = case(tmp_path)
    out, receipt = tmp_path / "features.json", tmp_path / "features.receipt.json"
    monkeypatch.setattr(mod, "measure_file", lambda path, holder: features(path))
    monkeypatch.setattr("sys.argv", ["builder", "--turn-pair", str(fixture.turn_path), str(fixture.receipt_path),
                                   "--source-manifest", str(fixture.source_path), "--transcript-manifest", str(fixture.transcript_path),
                                   "--leadership", str(fixture.leadership), "--out", str(out), "--receipt", str(receipt)])
    assert mod.main() == 0
    document = json.loads(out.read_text())
    metadata = json.loads(receipt.read_text())
    assert document["status"] == "MEASURED_DELIVERY_NO_AUTOMATIC_PROFILE_PROMOTION"
    assert metadata["schema"] == "member-longform-turn-features-receipt-v1"
    assert metadata["status"] == "PASS_MEASUREMENT_ONLY"
    assert metadata["artifact"] == str(out.resolve())
    assert metadata["artifact_sha256"] == sha(out)
    assert metadata["inputs"] == document["inputs"]
    assert metadata["counts"] == document["counts"]
