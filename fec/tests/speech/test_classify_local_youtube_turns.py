from fec.speech import classify_local_youtube_turns as C


def test_hard_signal_requires_first_and_surname_not_generic_office():
    assert C.evidence_names_member("Representative Craig Goldman", "Craig Goldman")
    assert C.evidence_names_member("Congressman Kevin Mullen", "Kevin Mullin")
    assert C.evidence_names_member("Democrat Valerie Fushi", "Valerie Foushee")
    assert C.evidence_names_member("Republican Morgan Latterill", "Morgan Luttrell")
    assert not C.evidence_names_member("the representative from Texas", "Craig Goldman")
    assert not C.evidence_names_member("Mr. Goldman", "Craig Goldman")


def test_hard_signal_accepts_approved_first_name_alias_only_with_surname():
    assert C.evidence_names_member("Congressman Tim Kennedy", "Timothy Kennedy")
    assert C.evidence_names_member("Senator Jim Justice", "James Justice")
    assert C.evidence_names_member("Representative Greg Murphy", "Gregory Murphy")
    assert not C.evidence_names_member("Congressman Tom Kennedy", "Timothy Kennedy")
    assert not C.evidence_names_member("Congressman Tim Scott", "Timothy Kennedy")


def test_named_evidence_recovery_is_literal_pre_span_and_closest():
    words = [
        {"word": "Representative", "start_sec": 1.0, "end_sec": 1.4},
        {"word": "Christian", "start_sec": 1.4, "end_sec": 1.8},
        {"word": "Menophee", "start_sec": 1.8, "end_sec": 2.2},
        {"word": "welcome", "start_sec": 2.2, "end_sec": 2.6},
        {"word": "Christian", "start_sec": 20.0, "end_sec": 20.4},
        {"word": "Menophee", "start_sec": 20.4, "end_sec": 20.8},
        {"word": "answers", "start_sec": 22.0, "end_sec": 22.4},
    ]
    found = C.find_named_evidence_before_span(words, "Christian Menefee", 21.0)
    assert found["evidence"] == "Christian Menophee"
    assert found["end_sec"] == 20.8
    assert C.find_named_evidence_before_span(words, "Christian Menefee", 1.0) is None


def test_snap_span_enforces_twelve_seconds_and_counts_inset_windows():
    words = [
        {"start_sec": 10.0, "end_sec": 10.5},
        {"start_sec": 22.0, "end_sec": 22.5},
        {"start_sec": 34.0, "end_sec": 34.5},
    ]
    span = C.snap_span({"start_sec": 10.1, "end_sec": 34.4}, words, 40.0)
    assert span["start_sec"] == 10.0
    assert span["end_sec"] == 34.5
    assert span["two_second_inset_exact_windows"] == 2
    assert C.snap_span({"start_sec": 10.1, "end_sec": 10.4}, words, 40.0) is None


def test_claimed_opening_snaps_boundary_and_must_exist_nearby():
    words = [
        {"word": word, "start_sec": 13.0 + index * 0.4, "end_sec": 13.35 + index * 0.4}
        for index, word in enumerate(
            "you are a first time congressman yeah you know I think it shows".split()
        )
    ]
    assert C.snap_to_claimed_opening(
        "reply beginning with 'Thank you for having me'", words, 13.0
    ) is None
    assert C.snap_to_claimed_opening(
        "response from 'Yeah, you know, I think it shows'", words, 15.0
    ) == 15.4
    assert C.snap_to_claimed_opening(
        "literal opening words: 'Yeah, you know, I think it shows'", words, 15.0
    ) == 15.4
    assert C.snap_to_claimed_opening("continuous target response", words, 13.0) == 13.0
