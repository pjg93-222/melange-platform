from fec.speech import build_audio_profile_recovery_gap as GAP


def test_build_rows_keeps_ledgers_separate_and_routes_local_holdings():
    census = [{
        "member": "Example Member",
        "bioguide": "E000001",
        "chamber": "House",
        "verified_audio_profile": True,
        "verified_nonfloor_descriptive_profile": False,
        "strict_identity_prerequisite_ready": False,
        "strict_identity_prerequisite": {
            "verified_events": 2,
            "verified_unique_audio_seconds": 175.0,
        },
        "verified_audio_basis": {
            "canonical_any_setting_exact_windows": 10,
            "canonical_nonfloor_exact_windows": 0,
        },
    }]
    inventory = [{
        "package_id": "pkg:one",
        "source_family": "npr",
        "discovery": {"status": "prepared"},
        "members": [{"member": "Example Member", "turns": 3, "words": 90}],
    }]

    strict = GAP.build_rows(census, inventory, "strict")[0]
    assert strict["target_recovery"] is True
    assert strict["events_needed"] == 1
    assert strict["minutes_needed"] == 0.083333
    assert strict["verified_ledgers"] == {
        "general_profile": True,
        "nonfloor_profile": False,
        "strict_identity_prerequisite": False,
        "canonical_any_setting_exact_windows": 10,
        "canonical_nonfloor_exact_windows": 0,
        "strict_verified_events": 2,
        "strict_verified_unique_audio_seconds": 175.0,
    }
    assert strict["local_unprocessed_holdings"]["packages"] == 1
    assert strict["local_unprocessed_holdings"]["turns"] == 3
    assert strict["safety"]["speaker_identity_authority"] is False

    general = GAP.build_rows(census, inventory, "general")[0]
    assert general["target_recovery"] is False
    nonfloor = GAP.build_rows(census, inventory, "nonfloor")[0]
    assert nonfloor["target_recovery"] is True
