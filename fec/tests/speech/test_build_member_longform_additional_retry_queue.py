import json

from fec.speech import build_member_longform_additional_retry_queue as mod


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_additional_retry_excludes_both_prior_sources(tmp_path, monkeypatch):
    primary_cues = tmp_path / "primary-cues.jsonl"
    retry_cues = tmp_path / "retry-cues.jsonl"
    discovery = tmp_path / "discovery.jsonl"
    primary_acq = tmp_path / "primary-acq.jsonl"
    retry_acq = tmp_path / "retry-acq.jsonl"
    roles = tmp_path / "roles.json"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    write_jsonl(primary_cues, [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(retry_cues, [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(primary_acq, [{"member": "Example Member", "source_id": "one", "url": "u1"}])
    write_jsonl(retry_acq, [{"member": "Example Member", "source_id": "two", "url": "u2"}])
    roles.write_text('{"members": {}}', encoding="utf-8")
    write_jsonl(discovery, [
        {"member": "Example Member", "video_id": "one", "url": "u1", "title": "Example Member interview"},
        {"member": "Example Member", "video_id": "two", "url": "u2", "title": "Example Member podcast"},
        {"member": "Example Member", "video_id": "three", "url": "u3", "title": "Representative Example Member interview"},
    ])
    monkeypatch.setattr("sys.argv", [
        "prog", "--primary-cues", str(primary_cues), "--retry-cues", str(retry_cues),
        "--discovery", str(discovery), "--primary-acquisition", str(primary_acq),
        "--retry-acquisition", str(retry_acq), "--roles", str(roles),
        "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["primary"]["video_id"] == "three"
    assert row["contract"]["earlier_recordings_discarded"] is False


def test_resolved_retry_is_not_queued_again(tmp_path, monkeypatch):
    files = [tmp_path / f"input-{index}.jsonl" for index in range(5)]
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    roles = tmp_path / "roles.json"
    write_jsonl(files[0], [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(files[1], [{"member": "Example Member", "status": "HARD_IN_BAND_NAME_CUE_CANDIDATE"}])
    write_jsonl(files[2], [])
    write_jsonl(files[3], [{"member": "Example Member", "source_id": "one"}])
    write_jsonl(files[4], [{"member": "Example Member", "source_id": "two"}])
    roles.write_text('{"members": {}}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "prog", "--primary-cues", str(files[0]), "--retry-cues", str(files[1]),
        "--discovery", str(files[2]), "--primary-acquisition", str(files[3]),
        "--retry-acquisition", str(files[4]), "--roles", str(roles),
        "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    assert out.read_text(encoding="utf-8") == ""


def test_current_leader_is_not_sent_to_current_era_retry(tmp_path, monkeypatch):
    files = [tmp_path / f"leader-{index}.jsonl" for index in range(5)]
    roles = tmp_path / "roles.json"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    write_jsonl(files[0], [{"member": "Leader One", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(files[1], [])
    write_jsonl(files[2], [{
        "member": "Leader One", "video_id": "third", "url": "u3",
        "title": "Leader One interview", "duration_seconds": 1800,
    }])
    write_jsonl(files[3], [{"member": "Leader One", "source_id": "one"}])
    write_jsonl(files[4], [])
    roles.write_text(
        '{"members": {"Leader One": {"current_role_assumed_on": "2025-01-03"}}}',
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", [
        "prog", "--primary-cues", str(files[0]), "--retry-cues", str(files[1]),
        "--discovery", str(files[2]), "--primary-acquisition", str(files[3]),
        "--retry-acquisition", str(files[4]), "--roles", str(roles),
        "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    assert out.read_text(encoding="utf-8") == ""


def test_supplemental_direct_audio_can_fill_discovery_gap(tmp_path, monkeypatch):
    files = [tmp_path / f"supplemental-{index}.jsonl" for index in range(6)]
    roles = tmp_path / "roles.json"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    write_jsonl(files[0], [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(files[1], [])
    write_jsonl(files[2], [])
    write_jsonl(files[3], [{
        "member": "Example Member", "resolved_source_type": "apple_directory_audio",
        "resolved_url": "https://podcasts.example/episode", "audio_url": "https://audio.example/episode.mp3",
        "title": "A Conversation with Example Member", "provider": "Example Show",
        "duration_seconds": 2400,
    }])
    write_jsonl(files[4], [{"member": "Example Member", "source_id": "one", "url": "u1"}])
    write_jsonl(files[5], [])
    roles.write_text('{"members": {}}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "prog", "--primary-cues", str(files[0]), "--retry-cues", str(files[1]),
        "--discovery", str(files[2]), "--supplemental-discovery", str(files[3]),
        "--primary-acquisition", str(files[4]), "--retry-acquisition", str(files[5]),
        "--roles", str(roles), "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["primary"]["source_type"] == "apple_directory_audio"
    assert row["primary"]["audio_url"] == "https://audio.example/episode.mp3"


def test_later_retry_excludes_all_prior_sources(tmp_path, monkeypatch):
    primary_cues = tmp_path / "primary-cues.jsonl"
    retry_cues = tmp_path / "retry-cues.jsonl"
    retry2_cues = tmp_path / "retry2-cues.jsonl"
    discovery = tmp_path / "discovery.jsonl"
    primary_acq = tmp_path / "primary-acq.jsonl"
    retry_acq = tmp_path / "retry-acq.jsonl"
    retry2_acq = tmp_path / "retry2-acq.jsonl"
    roles = tmp_path / "roles.json"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    for path in (primary_cues, retry_cues, retry2_cues):
        write_jsonl(path, [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(primary_acq, [{"member": "Example Member", "source_id": "one", "url": "u1"}])
    write_jsonl(retry_acq, [{"member": "Example Member", "source_id": "two", "url": "u2"}])
    write_jsonl(retry2_acq, [{"member": "Example Member", "source_id": "three", "url": "u3"}])
    write_jsonl(discovery, [
        {"member": "Example Member", "video_id": source_id, "url": url,
         "title": f"Example Member interview {source_id}"}
        for source_id, url in (("one", "u1"), ("two", "u2"), ("three", "u3"), ("four", "u4"))
    ])
    roles.write_text('{"members": {}}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "prog", "--primary-cues", str(primary_cues), "--retry-cues", str(retry_cues),
        "--additional-cues", str(retry2_cues), "--discovery", str(discovery),
        "--primary-acquisition", str(primary_acq), "--retry-acquisition", str(retry_acq),
        "--additional-acquisition", str(retry2_acq), "--roles", str(roles),
        "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["primary"]["video_id"] == "four"
    saved_receipt = json.loads(receipt.read_text(encoding="utf-8"))
    assert saved_receipt["inputs"]["additional_cues"][0]["sha256"]
    assert saved_receipt["inputs"]["additional_acquisitions"][0]["sha256"]


def test_later_retry_with_hard_cue_is_not_queued(tmp_path, monkeypatch):
    primary_cues = tmp_path / "primary-cues.jsonl"
    retry_cues = tmp_path / "retry-cues.jsonl"
    retry2_cues = tmp_path / "retry2-cues.jsonl"
    discovery = tmp_path / "discovery.jsonl"
    primary_acq = tmp_path / "primary-acq.jsonl"
    retry_acq = tmp_path / "retry-acq.jsonl"
    retry2_acq = tmp_path / "retry2-acq.jsonl"
    roles = tmp_path / "roles.json"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    write_jsonl(primary_cues, [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(retry_cues, [{"member": "Example Member", "status": "NO_FULL_NAME_CUE_FOUND"}])
    write_jsonl(retry2_cues, [{"member": "Example Member", "status": "HARD_IN_BAND_NAME_CUE_CANDIDATE"}])
    write_jsonl(discovery, [{"member": "Example Member", "video_id": "four", "url": "u4",
                            "title": "Example Member interview"}])
    write_jsonl(primary_acq, [{"member": "Example Member", "source_id": "one"}])
    write_jsonl(retry_acq, [{"member": "Example Member", "source_id": "two"}])
    write_jsonl(retry2_acq, [{"member": "Example Member", "source_id": "three"}])
    roles.write_text('{"members": {}}', encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "prog", "--primary-cues", str(primary_cues), "--retry-cues", str(retry_cues),
        "--additional-cues", str(retry2_cues), "--discovery", str(discovery),
        "--primary-acquisition", str(primary_acq), "--retry-acquisition", str(retry_acq),
        "--additional-acquisition", str(retry2_acq), "--roles", str(roles),
        "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    assert out.read_text(encoding="utf-8") == ""
