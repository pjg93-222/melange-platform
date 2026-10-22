from fec.speech import discover_gap_youtube_turns as D


def test_ordered_alignment_offset_uses_anchor_position_not_first_token_index():
    # anchor at position 5 starts with "the", which also occurs at position 0
    turn = ["the"] + [f"a{i}" for i in range(1, 5)] + ["the"] + [f"b{i}" for i in range(1, 16)]
    assert len(turn) == 21
    prefix = [f"p{i}" for i in range(10)]
    corrupted = [f"x{i}" for i in range(5)] + turn[5:]
    result = D.ordered_alignment(prefix + corrupted, [], minimum_ratio=0.5) if False else None
    caption = prefix + corrupted
    begin, _end, _ratio = D.ordered_alignment(turn, caption, minimum_ratio=0.5)
    assert begin == 10


def test_receipt_status_and_member_count_ignore_errors():
    rows = [
        {"member": "A", "status": "CAPTION_ERROR"},
        {"member": "B", "status": "NO_CAPTIONS"},
        {"member": "C", "status": "DISCOVERY_ONLY"},
    ]
    status, members = D.summarize_rows(rows)
    assert status == "PARTIAL_CAPTION_ERRORS_VISIBLE"
    assert members == 1
    assert D.summarize_rows(rows[1:])[0] == "PASS_DISCOVERY_ONLY"


def test_fetch_failure_classification(tmp_path, monkeypatch):
    class TranscriptsDisabled(Exception):
        pass

    class IpBlocked(Exception):
        pass

    def make(exc):
        def boom(video_id, root):
            raise exc
        return boom

    gap = {"member": "M"}
    cand = {"video_id": "v"}
    for exc, want in ((TranscriptsDisabled("x"), "NO_CAPTIONS"), (IpBlocked("y"), "CAPTION_ERROR")):
        monkeypatch.setattr(D, "fetch_caption", make(exc))
        rows = D.align_candidates([gap], {"M": [cand]}, {"M": []}, tmp_path, workers=1, minimum_ratio=0.5)
        assert rows[0]["status"] == want
