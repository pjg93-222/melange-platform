import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "fec/speech/discover_member_longform_youtube.py"
spec = importlib.util.spec_from_file_location("youtube_discovery", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_candidate_score_requires_member_name_and_rewards_interview_context():
    good = {
        "title": "Taxes, Tips, and the State of the Union: Rep. Aaron Bean",
        "description": "A full podcast interview with Representative Aaron Bean.",
    }
    wrong = {
        "title": "Long congressional interview with another representative",
        "description": "No named guest.",
    }
    assert module.candidate_score("Aaron Bean", good) >= 10
    assert module.candidate_score("Aaron Bean", wrong) == 0


def test_candidate_score_accepts_common_public_first_name_variant():
    bernie = {
        "title": "A long-form interview with Bernie Sanders",
        "description": "The senator joins the podcast.",
    }
    assert module.candidate_score("Bernard Sanders", bernie) >= 10


def test_turn_and_identity_promotion_remain_separate_from_search():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "CANDIDATE_IDENTITY_UNVERIFIED" in text
    assert "promotion_requires_audio_hash_asr_in_band_name_full_turns_and_speaker_gate" in text
    assert "candidate_changes_profile" in text


def _run_single_member(tmp_path, monkeypatch, with_coverage=True):
    import json
    import sys
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps({"members": ["A One", "B Two"]}), encoding="utf-8")
    cong = tmp_path / "cong.json"
    cong.write_text(json.dumps([{"member": "A One", "chamber": "House", "bioguide": "A1"}]), encoding="utf-8")
    out, cov = tmp_path / "out.jsonl", tmp_path / "cov.json"
    out.write_text(json.dumps({"member": "B Two", "candidate_rank": 1}) + "\n", encoding="utf-8")
    if with_coverage:
        cov.write_text(json.dumps({"members": {"B Two": {"search_status": "SEARCHED"}}}), encoding="utf-8")
    monkeypatch.setattr(module, "search_member", lambda *a, **k: ([], None, []))
    argv = ["x", "--roster", str(roster), "--congestion", str(cong), "--out", str(out),
            "--coverage", str(cov), "--receipt", str(tmp_path / "r.json"), "--member", "A One"]
    monkeypatch.setattr(sys, "argv", argv)
    return out, cov


def test_single_member_refresh_merges_into_prior_outputs(tmp_path, monkeypatch):
    import json
    out, cov = _run_single_member(tmp_path, monkeypatch)
    module.main()
    members = json.loads(cov.read_text(encoding="utf-8"))["members"]
    assert set(members) == {"A One", "B Two"}
    assert "B Two" in out.read_text(encoding="utf-8")


def test_single_member_refresh_refuses_incomplete_prior_pair(tmp_path, monkeypatch):
    import pytest
    _run_single_member(tmp_path, monkeypatch, with_coverage=False)
    with pytest.raises(SystemExit):
        module.main()


def test_failed_single_member_refresh_preserves_prior_candidates(tmp_path, monkeypatch):
    import json
    out, cov = _run_single_member(tmp_path, monkeypatch)
    prior_row = {"member": "B Two", "candidate_rank": 1, "video_id": "keep"}
    out.write_text(json.dumps(prior_row) + "\n", encoding="utf-8")
    prior_state = {"search_status": "SEARCHED", "qualifying_candidates": 1, "error": None}
    cov.write_text(json.dumps({"members": {"B Two": prior_state}}), encoding="utf-8")
    monkeypatch.setattr(module, "search_member", lambda *a, **k: ([], "DownloadError: transient", ["q"]))
    argv = [arg if arg != "A One" else "B Two" for arg in __import__("sys").argv]
    monkeypatch.setattr("sys.argv", argv)
    module.main()
    rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows == [prior_row]
    state = json.loads(cov.read_text(encoding="utf-8"))["members"]["B Two"]
    assert state["search_status"] == "SEARCHED" and state["qualifying_candidates"] == 1
    assert state["last_refresh_error"] == "DownloadError: transient"
    receipt = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert receipt["counts"]["refresh_errors_prior_state_kept"] == 1
    assert receipt["status"] == "PARTIAL_VISIBLE"


def test_failed_refresh_without_prior_good_state_records_error(tmp_path, monkeypatch):
    import json
    out, cov = _run_single_member(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "search_member", lambda *a, **k: ([], "DownloadError: transient", ["q"]))
    module.main()
    state = json.loads(cov.read_text(encoding="utf-8"))["members"]["A One"]
    assert state["search_status"] == "ERROR"
    assert "B Two" in out.read_text(encoding="utf-8")


def test_checkpoint_keeps_prior_rows_of_members_still_pending(tmp_path, monkeypatch):
    """The 10th result checkpoints while another refresh is still running; that
    member's prior rows must survive until its own result arrives."""
    import json
    import sys
    import threading
    members = [f"M{i:02d}" for i in range(11)]
    slow = members[-1]
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps({"members": members}), encoding="utf-8")
    cong = tmp_path / "cong.json"
    cong.write_text(json.dumps([{"member": m, "chamber": "House"} for m in members]), encoding="utf-8")
    out, cov = tmp_path / "out.jsonl", tmp_path / "cov.json"
    out.write_text("".join(json.dumps({"member": m, "candidate_rank": 1}) + "\n" for m in members),
                   encoding="utf-8")
    cov.write_text(json.dumps({"members": {m: {"search_status": "SEARCHED"} for m in members}}),
                   encoding="utf-8")
    release = threading.Event()

    def search(member, *a, **k):
        if member == slow:
            assert release.wait(30)
        return [], None, ["q"]

    checkpoints = []
    real_write = module.write_jsonl_atomic

    def capture(path, rows):
        checkpoints.append([row["member"] for row in rows])
        real_write(path, rows)
        release.set()

    monkeypatch.setattr(module, "search_member", search)
    monkeypatch.setattr(module, "write_jsonl_atomic", capture)
    argv = ["x", "--roster", str(roster), "--congestion", str(cong), "--out", str(out),
            "--coverage", str(cov), "--receipt", str(tmp_path / "r.json"), "--workers", "11"]
    for m in members:
        argv += ["--member", m]
    monkeypatch.setattr(sys, "argv", argv)
    module.main()
    assert checkpoints[0] == [slow]
    assert checkpoints[-1] == []
