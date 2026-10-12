from types import SimpleNamespace
import json
import pytest
from fec.surfaces.staffer_contacts import ContactIndex, combine_contacts
from fec.surfaces.staffer_network_explorer import build_index


def source(name='Grace Ryan', office='Rep. Stephen "Steve" Lynch (D-MA-8)'):
    return {'source_id': 'quorum:1', 'name': name, 'office': office, 'title': 'Economic Policy Advisor',
            'contacts': [{'kind': 'email', 'value': 'grace@example.org', 'source_date': '2026-07-03',
                          'provenance': {'cell': 'J2'}}]}


def test_contact_matching_requires_exact_name_and_unique_current_office():
    nodes = {'p': {'id': 'p', 'kind': 'person', 'label': 'Grace J Ryan', 'aliases': ['Grace Ryan']},
             'other': {'id': 'other', 'kind': 'person', 'label': 'Grace Ryan'}}
    facts = SimpleNamespace(people={'p': {'current_roles': [{'employer': 'Rep. Stephen Lynch'}]},
                                   'other': {'current_roles': [{'employer': 'Sen. Tina Smith'}]}})
    contacts = ContactIndex(records=[source()])
    contacts.bind(nodes, facts)
    assert len(nodes['p']['contacts']) == 1 and nodes['other']['contacts'] == []
    assert nodes['p']['contacts'][0]['source_date'] == '2026-07-03'
    assert contacts.resolve_source_person('quorum:1') == {
        'source_id': 'quorum:1', 'person_id': 'p', 'route_available': True,
        'status': 'matched_current_office',
        'match_basis': 'Exact full name or retained alias and unique current member office'}
    facts.people['other']['current_roles'] = facts.people['p']['current_roles']
    contacts.bind(nodes, facts)
    assert not nodes['p']['contacts'] and not nodes['other']['contacts']
    assert contacts.resolve_source_person('quorum:1')['status'] == 'source_only'
    assert contacts.resolve_source_person('quorum:1')['person_id'] is None
    assert contacts.get('quorum:1')  # Ambiguity never drops the original source evidence.
    facts.people['other']['current_roles'] = []
    facts.people['p']['current_roles'] = []
    contacts.bind(nodes, facts)
    assert not nodes['p']['contacts']  # Former office cannot supply today's contact.
    assert contacts.resolve_source_person('missing')['status'] == 'unknown_source'


def test_duplicate_contacts_combine_proofs_and_missing_values_are_not_invented():
    contacts = combine_contacts([
        {'kind': 'phone', 'value': '(202) 555-0100', 'label': 'Phone', 'provenance': {'cell': 'K2'}},
        {'kind': 'phone', 'value': '202-555-0100', 'label': 'Office phone', 'provenance': {'cell': 'AA2'}},
        {'kind': 'email', 'value': 'unavailable'}, {'kind': 'email', 'value': ''}])
    assert len(contacts) == 1
    assert contacts[0]['label'] == 'Office phone' and len(contacts[0]['sources']) == 2


def test_all_contact_source_people_are_in_inventory_with_one_row_each():
    contacts = ContactIndex(records=[source(), source()])
    payload = build_index({}, {}, 'r', contacts=contacts)['payload']
    assert payload['people_total'] == payload['total'] == 1
    # 14 columns until 2026-09-06, when `current` and `lda_status` were appended
    # (D-022 items 3 and 7). The invariant is that the row and the field contract
    # stay the same width, whatever that width is.
    assert len(payload['fields']) == len(payload['people'][0]) == 16
    assert payload['fields'][14:] == ['current', 'lda_status']
    assert payload['people'][0][14] is False and payload['people'][0][15] == ''
    assert payload['people'][0][12][0]['value'] == 'grace@example.org'
    assert contacts.source_record('quorum:1')['node']['contacts']


def test_source_id_accepts_source_native_and_person_node_spellings():
    record = source()
    record['source_id'] = 'lda:lobbyist:147664'
    contacts = ContactIndex(records=[record])
    contacts.bind({}, SimpleNamespace(people={}))

    assert contacts.get('person:lda:lobbyist:147664') == contacts.get('lda:lobbyist:147664')
    resolved = contacts.resolve_source_person('person:lda:lobbyist:147664')
    assert resolved['source_id'] == 'lda:lobbyist:147664'
    assert resolved['status'] == 'source_only'
    assert contacts.source_record('person:lda:lobbyist:147664')['node']['id'] == 'lda:lobbyist:147664'


def test_contact_source_must_match_its_receipt(tmp_path):
    path = tmp_path / 'contacts.json'
    path.write_text(json.dumps({'records': [source()]}))
    path.with_suffix('.receipt.json').write_text(json.dumps({'sha256': 'wrong'}))
    with pytest.raises(ValueError, match='receipt'):
        ContactIndex(path)
