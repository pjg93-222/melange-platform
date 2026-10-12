import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def test_full_roster_has_candidate_or_google_fallback_state():
    roster = set(json.loads((ROOT / "data/members/profile_basis_v2.json").read_text(
        encoding="utf-8-sig"))["members"])
    candidates = read_jsonl(ROOT / "data/speech/member_longform_discovery_v1.jsonl")
    fallback = read_jsonl(ROOT / "data/speech/member_longform_google_fallback_queue_v1.jsonl")
    candidate_members = {row["member"] for row in candidates}
    fallback_members = {row["member"] for row in fallback}
    assert candidate_members.isdisjoint(fallback_members)
    assert candidate_members | fallback_members == roster
    for row in fallback:
        assert len(row["google_queries"]) == 3
        assert any("youtube.com" in query for query in row["google_queries"])
        assert row["status"] == "SEARCH_REQUIRED"


def test_discovery_receipt_is_fail_closed_and_current():
    candidate_path = ROOT / "data/speech/member_longform_discovery_v1.jsonl"
    coverage_path = ROOT / "data/speech/member_longform_discovery_coverage_v1.json"
    fallback_path = ROOT / "data/speech/member_longform_google_fallback_queue_v1.jsonl"
    receipt = json.loads((ROOT / "data/speech/member_longform_discovery_v1.receipt.json").read_text(
        encoding="utf-8-sig"))
    assert receipt["contract"]["metadata_or_title_establishes_identity"] is False
    assert receipt["contract"]["candidate_may_modify_profile"] is False
    assert receipt["contract"]["promotion_requires_audio_hash_transcript_name_and_speaker_gate"] is True
    assert receipt["counts"]["members_searched"] == 533
    assert receipt["counts"]["members_with_candidates"] + receipt["counts"]["members_needing_fallback"] == 533
    assert receipt["outputs"]["candidates_sha256"] == hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    assert receipt["outputs"]["coverage_sha256"] == hashlib.sha256(coverage_path.read_bytes()).hexdigest()
    assert receipt["outputs"]["google_fallback_queue_sha256"] == hashlib.sha256(fallback_path.read_bytes()).hexdigest()
