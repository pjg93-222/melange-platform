from fec.staffer_network.compact_existing_review import case_key, ranges


def test_same_name_does_not_merge_source_people_and_dates_stay_evidence():
    a = {'person': 'politico:1', 'title': 'Director', 'status': 'MISSING_DATE_BOUND'}
    b = {**a, 'person': 'quorum:1'}
    assert case_key('career_claim_review', a, 1) != case_key('career_claim_review', b, 2)
    # Contradictory claims are one review case, not one accepted assertion.
    assert case_key('career_claim_review', a, 1) == case_key('career_claim_review', {**a, 'status': 'REVERSED_DATE_BOUNDS'}, 3)
    assert ranges([1, 2, 4, 7, 8]) == [[1, 2], [4, 4], [7, 8]]


def test_organization_group_is_comparison_only_exact_spelling():
    a = {'kind': 'client_identity', 'evidence': {'source_record': {'display_name': 'ACU'}}}
    b = {'kind': 'client_identity', 'evidence': {'source_record': {'display_name': 'ACU Inc.'}}}
    assert case_key('identity_and_organization_review', a, 1) == ('organization_comparison_only', 'ACU')
    assert case_key('identity_and_organization_review', a, 1) != case_key('identity_and_organization_review', b, 2)
