import pytest

from fec.speech import verify_member_interview_sources as vmis

VAULT = "in-band-name-plus-zero-false-accept-vault-consensus"
SUPP = "in-band-name-plus-three-accepted-event-continuity"


def test_duplicate_ids_refused():
    with pytest.raises(vmis.VerificationRefusal, match="duplicate_source_id"):
        vmis.refuse_duplicate_ids([{"video_id": "x"}, {"video_id": "x"}], "video_id")
    vmis.refuse_duplicate_ids([{"video_id": "x"}, {"video_id": "y"}], "video_id")


def test_source_identity_method_is_weakest_accepted_window():
    assert vmis.source_identity_method([{"identity_method": VAULT}, {"identity_method": VAULT}]) == VAULT
    assert vmis.source_identity_method([{"identity_method": SUPP}, {"identity_method": VAULT}]) == SUPP
    assert vmis.source_identity_method([{"identity_method": VAULT}, {"identity_method": SUPP}]) == SUPP
