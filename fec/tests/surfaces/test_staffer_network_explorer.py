from types import SimpleNamespace
from collections import defaultdict

from fec.surfaces.staffer_groups_index import build_groups_index
from fec.surfaces.staffer_network_explorer import build_index, graph_context_record


def test_source_filter_does_not_count_ambiguous_lda_mentions_as_identity_matches():
    a, b, c = [f'person:hill:person:{x}' for x in 'abc']
    native = 'person:lda:lobbyist:1'
    nodes = {ident: {'id': ident, 'kind': 'person', 'label': 'Person',
                     'lda_source_person_id': 'lda:lobbyist:1'} for ident in (a, b, c, native)}
    aliases = {'politico:a': a}
    facts = SimpleNamespace(people={ident: {} for ident in nodes}, coverage={})
    payload = build_index(nodes, aliases, 'r', facts=facts,
                          inventory={'verified_source_links': {'lda:lobbyist:2': c}})['payload']
    rows = {row[0]: row for row in payload['people']}
    assert rows[a][6] == 'Payroll|Politico'
    assert rows[b][6] == 'Payroll'
    assert rows[c][6] == 'Payroll|Lobbying'
    assert rows[native][6] == 'Lobbying'
    assert payload['source_counts']['Lobbying'] == 2


def test_reconciliation_conserves_source_people_and_network_scope_excludes_extra_catalog():
    import json
    a, b = 'person:hill:person:a', 'politico:b'
    nodes = {a: {'id': a, 'kind': 'person', 'label': 'Same Name', 'source_person_ids': ['lda:lobbyist:1']},
             b: {'id': b, 'kind': 'person', 'label': 'Same Name'},
             'office:a': {'id': 'office:a', 'kind': 'container', 'node_kind': 'container', 'label': 'Office'}}
    # Same names stay separate. Aliases are accepted separately from name matching.
    aliases = {'politico:a': a, 'person:lda:lobbyist:2': a, 'unrelated': 'absent'}
    facts = SimpleNamespace(people={a: {}, b: {}}, coverage={'current_staff': 1})
    catalog = SimpleNamespace(receipt={'counts': {'Quorum_person': 1}}, index_rows=lambda: [
        ['littlesis:1', 'Same Name', '', '', '', '', 'LittleSis', False, 'person'],
        ['quorum:1', 'Same Name', '', '', '', '', 'Quorum', False, 'person']])
    inventory = {'sourcecoverage': {'payroll': {'source_ids': 1}, 'lda': {'source_ids': 2}, 'politico': {'source_ids': 2}},
                 'counts': {'lda_unique_verified_spine_joins': 1}}
    result = build_index(nodes, aliases, 'r', catalog=catalog, facts=facts, inventory=inventory)
    all_rows, network = result['payload'], json.loads(result['network_raw'])
    counts = network['reconciliation']
    assert counts['source_people_before_matching'] == 5
    assert counts['verified_lda_payroll_joins'] == 1 and counts['accepted_alias_reductions'] == 2
    assert counts['balance_holds'] is True and counts['network_people'] == 2
    # The office never enters, and the unlinked LittleSis record is source
    # browsing rather than a person in this list (D-022 item 2).
    assert counts['additional_source_person_records'] == {'Quorum': 1}
    assert all_rows['people_total'] == 3 and all_rows['total'] == 3
    assert network['people_total'] == network['total'] == 2
    assert {row[0] for row in network['people']} == {a, b}
    assert all(row[13] is True for row in network['people'])
    assert result['network_etag'] != result['etag']
    assert network['revision'] == all_rows['revision'] == 'r'
    inventory['sourcecoverage']['payroll']['source_ids'] = 2
    assert build_index(nodes, aliases, 'r', facts=facts, inventory=inventory)['payload']['reconciliation']['balance_holds'] is False


def test_index_excludes_every_institution_record_and_synthetic_hub():
    """Ruled 2026-09-06 (D-022 item 2). This pin was inverted by that ruling: the
    index used to admit containers, organisations and policy areas as browseable
    context, and now they belong to the Groups index instead."""
    nodes = {
        'person:hill:person:a': {'id': 'person:hill:person:a', 'kind': 'person', 'label': 'Person'},
        'container:office:house:a': {'id': 'container:office:house:a', 'node_kind': 'container', 'kind': 'container', 'label': 'Office'},
        'organization:lda:registrant:1': {'id': 'organization:lda:registrant:1', 'node_kind': 'organization', 'kind': 'organization', 'label': 'Registrant'},
        'policy:politico:a': {'id': 'policy:politico:a', 'node_kind': 'policy_area', 'kind': 'policy_area', 'label': 'Policy'},
        'organization:user-group': {'id': 'organization:user-group', 'kind': 'organization', 'label': 'Routing hub'},
        'payroll-unresolved-record:1': {'id': 'payroll-unresolved-record:1', 'kind': 'payroll_record', 'label': 'CUMMINS'},
        'member:a': {'id': 'member:a', 'kind': 'member', 'label': 'Display anchor', 'anchor_only': True},
    }
    payload = build_index(nodes, {}, 'r')['payload']
    assert payload['total'] == 1 and payload['people_total'] == 1
    assert payload['kind_counts'] == {'person': 1}
    assert payload['source_counts'] == {'Payroll': 1}
    assert payload['person_scope']['excluded_records'] == {
        'container': 1, 'organization': 2, 'policy_area': 1, 'payroll_record': 1}


def test_no_non_person_row_can_reach_the_people_index_from_any_source():
    """Graph institutions, the LDA organisation catalog and the LittleSis
    organisation catalog are the three routes that used to feed 190,845
    organisation rows into this index. None of them may reach it now."""
    from types import SimpleNamespace
    nodes = {
        'person:hill:person:a': {'id': 'person:hill:person:a', 'kind': 'person', 'label': 'Person'},
        'container:office:house:a': {'id': 'container:office:house:a', 'node_kind': 'container',
                                     'kind': 'container', 'label': 'Office'},
    }
    catalog = SimpleNamespace(receipt={'counts': {'Quorum_person': 1}}, index_rows=lambda: [
        ['littlesis:entity:1', 'Org', '', '', '', '', 'LittleSis', False, 'organization'],
        ['littlesis:entity:2', 'Unlinked Person', '', '', '', '', 'LittleSis', False, 'person'],
        ['quorum:1', 'Quorum Person', '', '', '', '', 'Quorum', False, 'person']])
    lda_catalog = SimpleNamespace(receipt={'counts': {'organization': 54073}}, index_rows=lambda: [
        ['organization:lda:registrant:1', 'Registrant', '', '', '', '', 'Lobbying', False, 'organization']])
    payload = build_index(nodes, {}, 'r', catalog=catalog, lda_catalog=lda_catalog)['payload']
    assert payload['kind_counts'] == {'person': 2}
    assert {row[0] for row in payload['people']} == {'person:hill:person:a', 'quorum:1'}
    assert all(row[8] == 'person' for row in payload['people'])
    # The LDA catalog receipt still travels for the coverage note it feeds.
    assert payload['lda_source_catalog']['counts']['organization'] == 54073
    assert payload['person_scope']['littlesis_people_linked'] == 0


def test_a_littlesis_person_enters_only_when_an_identity_decision_attaches_it():
    from types import SimpleNamespace
    linked, unlinked = 'littlesis:entity:5', 'littlesis:entity:6'
    nodes = {
        linked: {'id': linked, 'kind': 'person', 'label': 'Linked', 'identity_alias_ids': ['politico:uuid-2']},
        unlinked: {'id': unlinked, 'kind': 'person', 'label': 'Unlinked'},
    }
    aliases = {'politico:uuid-2': linked}
    payload = build_index(nodes, aliases, 'r')['payload']
    assert {row[0] for row in payload['people']} == {linked}
    assert payload['person_scope']['littlesis_people_linked'] == 1
    # A name alone never qualifies: drop the identity decision and it leaves.
    nodes[linked].pop('identity_alias_ids')
    payload = build_index(nodes, {}, 'r')['payload']
    assert payload['people'] == []
    assert payload['person_scope']['excluded_records']['person_outside_source_scope'] == 2
    # A verified source link is the other accepted attachment.
    payload = build_index(nodes, {}, 'r',
                          inventory={'verified_source_links': {'lda:lobbyist:1': linked}})['payload']
    assert {row[0] for row in payload['people']} == {linked}


def test_current_people_sort_first_and_carry_the_ruled_lobbying_label():
    from types import SimpleNamespace
    quorum_current = {'id': 'quorum:1', 'kind': 'person', 'label': 'Zeta Quorum',
                      'current_positions': ['Counsel for Rep. X']}
    payroll_current = {'id': 'person:hill:person:a', 'kind': 'person', 'label': 'Mid Payroll',
                       'stints': [{'container_id': 'container:office:house:a', 'status': 'current'}]}
    lobbyist = {'id': 'person:lda:lobbyist:1', 'kind': 'person', 'label': 'Aaron Past',
                'lda_status': {'label': 'Lobbyist · former Hill staff, confirmed', 'code': 'CONFIRMED'}}
    nodes = {n['id']: n for n in (quorum_current, payroll_current, lobbyist)}
    facts = SimpleNamespace(people={'politico:x': {}}, coverage={})
    payload = build_index(nodes, {}, 'r', facts=facts)['payload']
    assert payload['fields'][14:] == ['current', 'lda_status']
    assert [row[0] for row in payload['people']] == [
        'person:hill:person:a', 'quorum:1', 'person:lda:lobbyist:1']
    assert [row[14] for row in payload['people']] == [True, True, False]
    assert payload['person_scope']['current_people'] == 2
    assert payload['people'][2][15] == 'Lobbyist · former Hill staff, confirmed'
    assert payload['people'][0][15] == ''
    # A directory role reported as current counts even without stints.
    facts = SimpleNamespace(people={'person:lda:lobbyist:1': {'employment_status': 'currently_listed'}},
                            coverage={})
    payload = build_index(nodes, {}, 'r', facts=facts)['payload']
    assert payload['person_scope']['current_people'] == 3


def test_graph_context_pages_every_affiliation_and_fails_closed_for_unverified_data():
    office = {'id': 'office', 'kind': 'container', 'node_kind': 'container', 'label': 'Office'}
    nodes = {'office': office}
    edges, adjacency = [], defaultdict(list)
    for i in range(123):
        person = f'person:{i:03}'
        nodes[person] = {'id': person, 'kind': 'person', 'label': 'Same Name'}
        edges.append({'id': f'edge:{i:03}', 'source': person, 'target': 'office', 'layer': 'membership'})
        adjacency['office'].append(i)
    edges.append({'id': 'anchor', 'source': 'office', 'target': 'member:a', 'layer': 'member_anchor', 'layout_only': True})
    adjacency['office'].append(123)
    graph = SimpleNamespace(nodes=nodes, edges=edges, adjacency=adjacency,
                            verification={'status': 'PASS'}, errors=[], sources=[{'verified': True}])
    observed = []
    for offset in (0, 50, 100):
        page = graph_context_record(graph, 'office', offset)
        assert page['status'] == 'verified_graph_context' and page['total'] == 123
        assert page['has_more'] == (offset < 100)
        observed += [row['source_relationship_id'] for row in page['relationships']]
        assert all(row['other_kind'] == 'person' for row in page['relationships'])
    assert observed == [f'edge:{i:03}' for i in range(123)]
    assert not graph_context_record(graph, 'person:000')['record']
    graph.verification['status'] = 'FAIL'
    assert not graph_context_record(graph, 'office')['record']


# ---------------------------------------------------------------------------
# Groups index (D-022 item 2): offices and organisations, away from the people.
# ---------------------------------------------------------------------------
def groups_fixture():
    nodes = {
        'person:hill:person:a': {'id': 'person:hill:person:a', 'kind': 'person', 'label': 'Ada Staffer',
            'stints': [{'container_id': 'container:office:house:a', 'title_last': 'CHIEF OF STAFF', 'status': 'current'},
                       {'container_id': 'container:cmte:b', 'title_last': 'COUNSEL', 'status': 'past'}]},
        'person:lda:lobbyist:1': {'id': 'person:lda:lobbyist:1', 'kind': 'person', 'label': 'Bo Lobbyist'},
        'container:office:house:a': {'id': 'container:office:house:a', 'kind': 'container', 'node_kind': 'container',
                                     'container_kind': 'member_office', 'label': '2025 HON. A'},
        'container:cmte:b': {'id': 'container:cmte:b', 'kind': 'container', 'node_kind': 'container',
                             'container_kind': 'committee', 'label': 'House Committee B'},
        'container:admin:c': {'id': 'container:admin:c', 'kind': 'container', 'node_kind': 'container',
                              'container_kind': 'admin', 'label': 'Admin C'},
        'container:leadership:d': {'id': 'container:leadership:d', 'kind': 'container', 'node_kind': 'container',
                                   'container_kind': 'leadership', 'label': 'Leadership D'},
        'container:unresolved:e': {'id': 'container:unresolved:e', 'kind': 'container', 'node_kind': 'container',
                                   'container_kind': 'unresolved', 'label': 'Unresolved E'},
        'organization:lda:registrant:1': {'id': 'organization:lda:registrant:1', 'kind': 'organization',
                                          'node_kind': 'organization', 'label': 'HANCE SCARBOROUGH'},
    }
    facts = SimpleNamespace(
        people={'person:hill:person:a': {'is_current_staff': True, 'employment_status': 'currently_listed',
                    'latest_roles': [{'title': 'Chief of Staff', 'employer_id': 'container:office:house:a'}]},
                'person:lda:lobbyist:1': {'latest_roles': [
                    {'title': 'Reported work association', 'employer_id': 'organization:lda:registrant:1'}]}},
        features={'person:hill:person:a': {
                      'current_employment': {'container:office:house:a': {'label': '2025 HON. A'}},
                      'past_employment': {'container:cmte:b': {'label': 'House Committee B'},
                                          'container:admin:c': {'label': 'Admin C'},
                                          'container:leadership:d': {'label': 'Leadership D'},
                                          'container:unresolved:e': {'label': 'Unresolved E'},
                                          'employer:unresolved-name': {'label': 'Unresolved'}}},
                  'person:lda:lobbyist:1': {
                      'current_employment': {},
                      'past_employment': {'organization:lda:registrant:1': {'label': 'HANCE SCARBOROUGH'}}}},
        coverage={})
    return nodes, facts


def test_groups_index_shape_scope_and_current_first():
    nodes, facts = groups_fixture()
    people = build_index(nodes, {}, 'r', facts=facts)['payload']['people']
    result = build_groups_index(people, nodes, 'r', facts)
    payload = result['payload']
    assert payload['schema'] == 'melange-staffer-groups-index-v1' and payload['revision'] == 'r'
    assert set(payload) >= {'offices', 'organizations', 'counts', 'note', 'people_cap', 'office_scope'}
    assert payload['office_scope'] == ['member_office', 'committee', 'leadership']
    assert result['etag'].startswith('"groups-') and result['gzip'][:2] == b'\x1f\x8b'
    offices = {group['id']: group for group in payload['offices']}
    organizations = {group['id']: group for group in payload['organizations']}
    # Member offices, committees and leadership offices are offices. An ADMIN
    # container is NOT: administrative payroll headers are not workplaces (ruling
    # in force 2026-09-07, the same one that put CO_TENURE_EXCLUDED_CONTAINER_KINDS
    # = {'admin'} into the social-network builder). This assertion was briefly the
    # other way round -- the scope had been widened to include admin earlier the
    # same day -- and the ruling reverses that. A container of kind 'unresolved' is
    # not an office either, and neither is a feature key with no container node.
    assert set(offices) == {'container:office:house:a', 'container:cmte:b',
                            'container:leadership:d'}
    assert 'container:admin:c' not in offices and 'container:admin:c' not in organizations
    assert set(organizations) == {'organization:lda:registrant:1'}
    # the unresolved-kind container, the admin header, and the unresolved name
    assert payload['counts']['keys_outside_scope'] == 3
    # An office card and an organisation card no longer carry the same key set.
    # The 2026-09-07 employment ruling gave an organisation two counters an office
    # cannot have -- how many people it EMPLOYS (one registrant per filer) and how
    # many contract filers REPRESENT it (a client tie) -- and a person listed under
    # an organisation carries which of the two kinds of tie put them there. An
    # office has no lobbying filers, so it gained nothing and its cards and people
    # are byte-identical to what they were before the ruling; that is asserted here
    # rather than assumed.
    OFFICE_KEYS = {'id', 'kind', 'name', 'people_count', 'current_count', 'people', 'more'}
    ORG_KEYS = OFFICE_KEYS | {'employee_count', 'represented_by_count'}
    for group in payload['offices']:
        assert set(group) == OFFICE_KEYS
        assert group['kind'] == 'office'
        for person in group['people']:
            assert set(person) == {'id', 'name', 'current', 'title'}
    for group in payload['organizations']:
        assert set(group) == ORG_KEYS
        assert group['kind'] == 'organization'
        for person in group['people']:
            assert set(person) == {'id', 'name', 'current', 'title', 'membership', 'as_of'}
            # Nothing here was placed by the ruling: this fixture passes no
            # employment map, so every tie is the pre-ruling `listed` kind.
            assert person['membership'] == 'listed' and person['as_of'] == ''
    member_office = offices['container:office:house:a']
    assert member_office['name'] == '2025 HON. A'
    assert member_office['people_count'] == 1 and member_office['current_count'] == 1
    assert member_office['people'][0] == {'id': 'person:hill:person:a', 'name': 'Ada Staffer',
                                          'current': True, 'title': 'Chief of Staff'}
    # The same person is PAST in the committee they no longer serve.
    committee = offices['container:cmte:b']
    assert committee['current_count'] == 0 and committee['people'][0]['current'] is False
    assert committee['people'][0]['title'] == 'COUNSEL'
    # Groups are ordered by how much current staff they carry, then by name;
    # the two with no current staff tie and fall back to name.casefold().
    assert [group['id'] for group in payload['offices']] == [
        'container:office:house:a', 'container:cmte:b', 'container:leadership:d']


def test_a_person_the_people_index_dropped_can_never_appear_in_a_group():
    nodes, facts = groups_fixture()
    nodes['littlesis:entity:9'] = {'id': 'littlesis:entity:9', 'kind': 'person', 'label': 'Unlinked'}
    facts.features['littlesis:entity:9'] = {
        'current_employment': {'container:office:house:a': {'label': '2025 HON. A'}}, 'past_employment': {}}
    facts.people['littlesis:entity:9'] = {'latest_roles': []}
    people = build_index(nodes, {}, 'r', facts=facts)['payload']['people']
    assert 'littlesis:entity:9' not in {row[0] for row in people}
    payload = build_groups_index(people, nodes, 'r', facts)['payload']
    listed = {person['id'] for group in payload['offices'] for person in group['people']}
    assert 'littlesis:entity:9' not in listed
    assert payload['counts']['people_available'] == len(people)


def test_group_people_are_listed_once_and_capped_with_a_more_count():
    nodes = {'container:office:house:a': {'id': 'container:office:house:a', 'kind': 'container',
                                          'node_kind': 'container', 'container_kind': 'member_office',
                                          'label': 'Big Office'}}
    features, people_facts = {}, {}
    for index in range(60):
        ident = f'person:hill:person:{index:03}'
        # Two paths name the same office: a feature and a stint. One row must result.
        nodes[ident] = {'id': ident, 'kind': 'person', 'label': f'Person {index:03}',
                        'stints': [{'container_id': 'container:office:house:a', 'title_last': 'AIDE',
                                    'status': 'current' if index < 4 else 'past'}]}
        features[ident] = {'current_employment': {}, 'past_employment': {
            'container:office:house:a': {'label': 'Big Office'}}}
        people_facts[ident] = {'latest_roles': []}
    facts = SimpleNamespace(people=people_facts, features=features, coverage={})
    people = build_index(nodes, {}, 'r', facts=facts)['payload']['people']
    payload = build_groups_index(people, nodes, 'r', facts)['payload']
    group = payload['offices'][0]
    assert group['people_count'] == 60 and group['more'] == 10 and len(group['people']) == 50
    assert len({person['id'] for person in group['people']}) == 50
    assert group['current_count'] == 4
    assert [person['current'] for person in group['people'][:4]] == [True] * 4
    assert group['people'][4]['current'] is False
    assert payload['counts']['office_memberships'] == 60
    smaller = build_groups_index(people, nodes, 'r', facts, people_cap=5)['payload']['offices'][0]
    assert len(smaller['people']) == 5 and smaller['more'] == 55
