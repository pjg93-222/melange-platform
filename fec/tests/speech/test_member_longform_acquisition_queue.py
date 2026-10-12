import hashlib
import json
from pathlib import Path

import pytest

from fec.speech import build_member_longform_acquisition_queue as builder


ROOT = Path(__file__).resolve().parents[3]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_every_roster_member_has_a_longform_source_or_candidate():
    roster = set(json.loads((ROOT / "data/members/profile_basis_v2.json").read_text(
        encoding="utf-8-sig"))["members"])
    path = ROOT / "data/speech/member_longform_acquisition_queue_v1.jsonl"
    rows = read_jsonl(path)
    receipt = json.loads((ROOT / "data/speech/member_longform_acquisition_queue_v1.receipt.json").read_text(
        encoding="utf-8"))
    assert len(rows) == 533
    assert {row["member"] for row in rows} == roster
    assert all(row.get("primary") for row in rows)
    assert all(row["contract"]["profile_effect_before_promotion"] == "NONE" for row in rows)
    assert receipt["status"] == "PASS_FULL_ROSTER_HAS_SOURCE_OR_CANDIDATE"
    assert receipt["counts"]["missing_members"] == 0
    assert receipt["counts"]["reviewed_source_overrides"] == 68
    assert receipt["contract"]["every_member_has_source_or_candidate"] is True
    assert receipt["output"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_general_web_closes_the_youtube_and_apple_gap():
    web = read_jsonl(ROOT / "data/speech/member_longform_general_web_candidates_v1.jsonl")
    assert len(web) >= 10
    assert all(row["status"] == "CANDIDATE_IDENTITY_UNVERIFIED" for row in web)
    assert all(row["profile_effect"] == "NONE_UNTIL_PROMOTED" for row in web)
    assert all(row.get("resolved_source_type") in {"youtube", "apple_directory_audio"}
               for row in web)
    assert all(row.get("video_id") or row.get("audio_url") for row in web)


def test_reviewed_replacements_precede_bad_homonym_candidates():
    rows = {row["member"]: row for row in read_jsonl(
        ROOT / "data/speech/member_longform_acquisition_queue_v1.jsonl"
    )}
    overrides = read_jsonl(
        ROOT / "data/speech/member_longform_reviewed_source_overrides_v1.jsonl"
    )
    assert len(overrides) == 68
    assert len({row["member"] for row in overrides}) == len(overrides)
    assert all(float(row["duration_seconds"]) >= 600 for row in overrides)
    assert all(row.get("published") for row in overrides)
    for member in (row["member"] for row in overrides):
        assert rows[member]["primary"]["reviewed_replacement"] is True
        assert all(
            source.get("reviewed_replacement") is True
            for source in rows[member]["alternatives"]
        )


def test_reviewed_interview_replacements_displace_debates_for_profile_work():
    rows = {row["member"]: row for row in read_jsonl(
        ROOT / "data/speech/member_longform_acquisition_queue_v1.jsonl"
    )}
    for member in ("April McClain Delaney", "Paul Tonko"):
        primary = rows[member]["primary"]
        assert "debate" not in primary["title"].casefold()
        assert "interview" in primary["title"].casefold()


@pytest.mark.parametrize("as_jsonl", [False, True])
def test_atomic_queue_writes_retry_a_short_onedrive_lock(tmp_path, monkeypatch, as_jsonl):
    target = tmp_path / ("queue.jsonl" if as_jsonl else "receipt.json")
    target.write_text("previous", encoding="utf-8")
    original_replace = builder.os.replace
    attempts = []
    def temporarily_locked(source, destination):
        attempts.append(1)
        if len(attempts) < 3:
            assert target.read_text(encoding="utf-8") == "previous"
            raise PermissionError("simulated OneDrive read lock")
        return original_replace(source, destination)
    monkeypatch.setattr(builder.os, "replace", temporarily_locked)
    payload = {"member": "Pablo Hernández Rivera"}
    if as_jsonl:
        builder.write_jsonl_atomic(target, [payload])
        assert read_jsonl(target) == [payload]
    else:
        builder.write_json_atomic(target, payload)
        assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert len(attempts) == 3
