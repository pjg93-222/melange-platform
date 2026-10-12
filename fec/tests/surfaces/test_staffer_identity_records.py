from copy import deepcopy

from fec.surfaces.staffer_identity_auto import resolve_identities
from fec.surfaces.staffer_identity_records import consolidate_record_pairs
from fec.surfaces.staffer_payroll_identity import employment_overlap, load_payroll_identity

PROOF = {'path': '/source/verified-records', 'sha256': 'verified-records-sha'}


def people(*ids):
    return {i: {'id': i, 'kind': 'person', 'label': 'Jamie Q Example', 'provenance': PROOF} for i in ids}


def record(a, b):
    return {'node_ids': [a, b], 'method': 'EXACT_PAYROLL_ACCOUNT_CORRECTION', 'records': [], 'overlap': ['2020-01-01', '2020-02-01']}


def resolve(nodes):
    return resolve_identities(nodes, [], [], {}, [])


def test_report_payment_windows_require_full_dated_coverage():
    window = {'start': '2020-04-01', 'end_exclusive': '2020-10-01', 'date_evidence': 'presence_within_report'}
    partial = {'start': '2020-07-01', 'end_exclusive': '2021-01-01'}
    assert employment_overlap(window, partial) is None
    whole = {'start': '2020-01-01', 'end_exclusive': '2021-01-01'}
    assert employment_overlap(window, whole) == ('2020-04-01', '2020-10-01')
    assert employment_overlap(whole, window) == employment_overlap(window, whole)
    assert employment_overlap(window, dict(window)) is None
    assert employment_overlap({'start': '2020-04-01', 'end_exclusive': '2020-04-01'}, whole) is None


def test_actual_title_dates_prevent_a_whole_career_title_match():
    nodes = people('a', 'b')
    def ep(person, title, start, end):
        return {'person_id': person, 'employer_id': 'office', 'title': title, 'start': start,
                'end_exclusive': end, 'employment_verified': True, 'provenance': PROOF}
    episodes = [ep('a', 'Legislative Assistant', '2018-01-01', '2020-01-01'),
                ep('a', 'Legislative Director', '2021-01-01', '2022-01-01'),
                ep('b', 'Legislative Director', '2019-01-01', '2020-01-01')]
    assert not resolve_identities(nodes, episodes, [], {}, [])['identity_groups']
    episodes[-1]['start'], episodes[-1]['end_exclusive'] = '2021-06-01', '2021-12-01'
    assert resolve_identities(nodes, episodes, [], {}, [])['identity_groups']


def test_new_record_pairs_cannot_chain_unproved_endpoints():
    nodes = people('a', 'b', 'c')
    merged = consolidate_record_pairs(resolve(nodes), nodes, [record('a', 'b'), record('b', 'c')], PROOF)
    assert merged['identity_aliases'] == {'b': 'a'}
    assert merged['record_reconciliation']['rejected']['transitive_component_conflict'] == 1


def test_explicit_separation_middle_names_and_birth_dates_are_enforced():
    nodes = people('a', 'b')
    assert not consolidate_record_pairs(resolve(nodes), nodes, [record('a', 'b')], PROOF, [('a', 'b')])['identity_groups']
    nodes['b']['label'] = 'Jamie R Example'
    assert not consolidate_record_pairs(resolve(nodes), nodes, [record('a', 'b')], PROOF)['identity_groups']
    nodes['b']['label'] = 'Jamie Q Example'
    nodes['a']['dob'], nodes['b']['date_of_birth'] = '1980-01-01', '1990-01-01'
    assert not consolidate_record_pairs(resolve(nodes), nodes, [record('a', 'b')], PROOF)['identity_groups']


def test_existing_identity_proofs_and_all_source_ids_survive_new_merge():
    nodes = people('a', 'b', 'c')
    key = {'namespace': 'test-id', 'value': '123', 'verified_unique': True, 'provenance': PROOF}
    nodes['a']['external_ids'] = [key]
    nodes['b']['external_ids'] = [key]
    baseline = resolve(nodes)
    original = deepcopy(baseline)
    merged = consolidate_record_pairs(baseline, nodes, [record('b', 'c')], PROOF)
    assert baseline == original
    assert merged['identity_aliases'] == {'b': 'a', 'c': 'a'}
    group = merged['identity_groups'][0]
    assert group['source_person_ids'] == ['a', 'b', 'c']
    assert group['provenance']['evidence_methods'] == ['EXACT_PAYROLL_ACCOUNT_CORRECTION', 'SHARED_UNIQUE_EXTERNAL_ID']
    assert group['provenance']['prior_identity_groups'] == baseline['identity_groups']
    assert merged['record_reconciliation']['new_alias_reductions'] == 1
    assert not group['canonical_identity_promoted']
    # Denial of the alias endpoint applies to its established primary as well.
    assert consolidate_record_pairs(baseline, nodes, [record('b', 'c')], PROOF, [('b', 'c')])['identity_aliases'] == {'b': 'a'}


def test_conflict_in_hidden_source_name_blocks_a_new_group_merge():
    nodes = people('a', 'b', 'c')
    key = {'namespace': 'test-id', 'value': '123', 'verified_unique': True, 'provenance': PROOF}
    nodes['a']['label'] = 'Jamie Example'
    nodes['a']['external_ids'], nodes['b']['external_ids'] = [key], [key]
    baseline = resolve(nodes)
    nodes['c']['label'] = 'Jamie R Example'
    assert consolidate_record_pairs(baseline, nodes, [record('a', 'c')], PROOF)['identity_aliases'] == baseline['identity_aliases']


def test_pinned_bundle_and_source_tampering_are_rejected(tmp_path):
    import gzip
    import hashlib
    import json
    import pytest
    from fec.surfaces.staffer_payroll_identity import VERSION
    source = tmp_path / 'source.txt'
    source.write_text('source')
    bundle = {'version': VERSION, 'episodes': [], 'record_pairs': [], 'counts': {},
              'sources': [{'path': 'source.txt', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}]}
    target = tmp_path / 'bundle.gz'
    target.write_bytes(gzip.compress(json.dumps(bundle).encode()))
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'path': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}))
    assert load_payroll_identity(tmp_path, config)['episodes'] == []
    source.write_text('changed')
    with pytest.raises(ValueError, match='source hash mismatch'):
        load_payroll_identity(tmp_path, config)
    target.write_bytes(b'changed')
    with pytest.raises(ValueError, match='bundle hash mismatch'):
        load_payroll_identity(tmp_path, config)
