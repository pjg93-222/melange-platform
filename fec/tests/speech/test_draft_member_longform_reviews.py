import json

from fec.speech import draft_member_longform_reviews as mod


def test_duration_draft_is_descriptive_and_awaits_boundary_review():
    finding = mod.duration_finding(145.0)
    assert finding["kind"] == "observed_multi_minute_answer"
    assert "2.4 minutes" in finding["finding"]
    assert "meeting_implication" not in finding
    assert "meeting_phase" not in finding
    assert "speaker handoffs await review" in finding["finding"]
    assert "not a universal private preference" in finding["scope"]


def test_personal_checkin_draft_remains_candidate_without_meeting_advice():
    findings = mod.structured_findings({
        "preceding_context": "Before policy, how are you and how is your health now?",
        "turn_transcript": "I appreciate you asking. " + "recovery detail " * 60,
        "duration_seconds": 71,
    })
    finding = next(row for row in findings if row["kind"].startswith("personal_checkin"))
    assert "meeting_phase" not in finding
    assert "meeting_implication" not in finding
    assert "who initiated" in finding["scope"]
    assert "human review" in finding["scope"]


def test_principle_then_boundary_draft_is_surface_order_only():
    findings = mod.structured_findings({
        "preceding_context": "What should Congress do?",
        "turn_transcript": (
            "Fundamentally the important thing is that public service should protect people. "
            + "context " * 60
            + " However in practice we need to choose a limited implementation path."
        ),
        "duration_seconds": 90,
    })
    finding = next(row for row in findings if row["kind"] == "principle_then_operational_detail_candidate")
    assert "meeting_phase" not in finding
    assert "discourse-order candidate" in finding["scope"]


def test_drafts_never_claim_review_authority(tmp_path, monkeypatch):
    queue = tmp_path / "queue.jsonl"
    out = tmp_path / "out.jsonl"
    receipt = tmp_path / "receipt.json"
    queue.write_text(json.dumps({
        "status": "IDENTITY_BOUNDARY_SEMANTIC_REVIEW_REQUIRED",
        "turn_id": "example-01", "member": "Example Member", "source_id": "source",
        "source_era": "NOT_CURRENT_FORMAL_LEADERSHIP", "start_sec": 10, "end_sec": 80,
        "duration_seconds": 70, "anchored_member": True,
        "literal_name_cues": [{"phrase": "Example Member"}],
        "candidate_handoff_basis": "Host yields; host resumes.",
        "preceding_context": "question", "turn_transcript": "answer", "following_context": "next question",
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr("sys.argv", [
        "prog", "--queue", str(queue), "--out", str(out), "--receipt", str(receipt),
    ])
    assert mod.main() == 0
    row = json.loads(out.read_text(encoding="utf-8").strip())
    assert row["review_status"] == "PENDING_HUMAN"
    assert row["reviewer_type"] == "AUTOMATED_DRAFT_NOT_AUTHORITY"
    assert row["profile_effect"] == "NONE_UNTIL_HUMAN_ACCEPTANCE_AND_MATERIALIZATION"
