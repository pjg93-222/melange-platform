"""Offline regression checks for public identity reference ingestion."""
import hashlib
import http.client
import json
import os
import socket
import sqlite3
from pathlib import Path

import pytest

from fec.surfaces import staffer_identity_web as web


def extract(html, name='Greg Mesack'):
    parser = web.PageText()
    parser.feed(html)
    parser.flush()
    return web.subject_text(parser.blocks, name)


@pytest.fixture
def index(tmp_path, monkeypatch):
    seeds = tmp_path / 'seeds.json'
    seeds.write_text(json.dumps({'references': [{
        'url': 'https://official.example/greg', 'name': 'Greg Mesack',
        'source_kind': 'official_biography', 'title': 'Greg Mesack biography'}]}))
    monkeypatch.setattr(web, 'SEEDS', seeds)
    return web.IdentityWebIndex(tmp_path)


def test_dedicated_heading_excludes_neighbor_biography_and_navigation():
    text = extract('<nav>Greg Mesack Other Organization</nav>'
                   '<h2>Greg Mesack</h2><p>Greg served Alpha Council before Beta League.</p>'
                   '<h2>Jane Jones</h2><p>Jane served Contaminating Foundation.</p>')
    assert 'Alpha Council' in text and 'Beta League' in text
    assert 'Contaminating' not in text and 'Other Organization' not in text


def test_different_person_biography_is_a_boundary():
    text = extract('<h1>Greg Mesack</h1><p>Greg served Alpha Council.</p>'
                   '<p>Jane Jones is director of Contaminating Foundation.</p>')
    assert 'Contaminating' not in text


def test_passing_joint_mention_does_not_claim_other_person_career():
    text = extract('<p>Greg Mesack attended with John Smith.</p>'
                   '<p>John previously served at Contaminating Foundation.</p>')
    assert 'Contaminating' not in text


def test_subject_absent_returns_no_reference():
    assert extract('<h1>Jane Jones</h1><p>Jane works at Alpha Council.</p>') == ''


def test_single_subject_pdf_standalone_name_line_scopes_biography():
    text = web.subject_text([(False, 'Speaker Biography\nGreg Mesack\n'
        'As the senior vice president, Mesack leads Alpha Council. '
        'Prior to this role, Greg worked at Beta League.')], 'Greg Mesack')
    assert 'Alpha Council' in text and 'Beta League' in text


def test_two_named_people_in_single_biography_block_rejected():
    text = extract('<p>Greg Mesack serves Alpha Council. Jane Jones leads Beta League.</p>')
    assert text == ''


@pytest.mark.parametrize('url', [
    'http://official.example/bio', 'https://user:pass@official.example/bio',
    'https://official.example:8443/bio', 'file:///public/bio',
])
def test_invalid_public_urls_rejected_before_dns(url, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid URL should not reach DNS')
    monkeypatch.setattr(web.socket, 'getaddrinfo', forbidden)
    with pytest.raises(ValueError):
        web.public_url(url)


def test_any_private_dns_answer_rejects_reference(monkeypatch):
    monkeypatch.setattr(web.socket, 'getaddrinfo', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443)),
    ])
    with pytest.raises(ValueError):
        web.public_url('https://official.example/bio')


def test_mocked_fetch_hashes_actual_subject_text_and_response(index, monkeypatch):
    raw = (b'<h1>Greg Mesack</h1><p>Greg Mesack previously worked at Alpha Council, '
           b'and now leads federal advocacy for Beta League with a focus on public policy.</p>'
           b'<h2>Jane Jones</h2><p>Contaminating Foundation</p>')
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url: (raw, 'text/html', url))
    ref = index.fetch_reference('https://official.example/greg', 'Greg Mesack')
    assert ref['sha256'] == hashlib.sha256(ref['text'].encode()).hexdigest()
    assert ref['response_sha256'] == hashlib.sha256(raw).hexdigest()
    assert ref['subject_scoped'] is True
    assert 'Contaminating' not in ref['text']
    assert index.references() == [ref]


def test_disallowed_site_never_downloads_reference(index, monkeypatch):
    monkeypatch.setattr(index, 'allowed', lambda url: False)
    monkeypatch.setattr(web, 'download', lambda *a: pytest.fail('Disallowed reference fetched'))
    with pytest.raises(ValueError):
        index.fetch_reference('https://official.example/greg', 'Greg Mesack')
    assert index.references() == []


def test_queue_and_reference_survive_restart_without_network(index, monkeypatch):
    index.enqueue([{'display_name': 'Greg Mesack', 'node_ids': ['a', 'b']}])
    raw = b'<h1>Greg Mesack</h1><p>Greg Mesack served Alpha Council before Beta League, where he directs federal advocacy and public policy engagement.</p>'
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url: (raw, 'text/html', url))
    assert index.process_one()
    expected = index.references()
    assert len(expected) == 1
    monkeypatch.setattr(web, 'download', lambda *a: pytest.fail('Offline cache read reached network'))
    reopened = web.IdentityWebIndex(index.path.parents[2])
    assert reopened.references() == expected
    assert reopened.status()['checked'] == 1
    assert not reopened.process_one()


def test_interrupted_queue_item_recovered_on_restart(index):
    index.enqueue([{'display_name': 'Greg Mesack', 'node_ids': ['a', 'b']}])
    with index.connect() as db:
        db.execute("UPDATE queue SET state='checking'")
    reopened = web.IdentityWebIndex(index.path.parents[2])
    with reopened.connect() as db:
        assert db.execute('SELECT state FROM queue').fetchone()[0] == 'queued'


def queue_row(index, key='gregory mesack'):
    with index.connect() as db:
        db.row_factory = __import__('sqlite3').Row
        return dict(db.execute('SELECT * FROM queue WHERE key=?', (key,)).fetchone())


def test_enqueue_people_includes_singletons_and_groups_but_not_institutions(index):
    index.enqueue_people({
        'a': {'label': 'Greg Mesack', 'kind': 'person'},
        'b': {'label': 'Gregory Mesack', 'kind': 'person'},
        'c': {'label': 'Jane Jones', 'kind': 'person'},
        'org': {'label': 'Alpha Council', 'kind': 'organization'},
    })
    with index.connect() as db:
        rows = db.execute('SELECT node_ids FROM queue').fetchall()
    assert sorted(i for row in rows for i in json.loads(row[0])) == ['a', 'b', 'c']
    assert index.status()['total_people'] == 3


def test_stable_source_fingerprint_preserves_progress_but_change_requeues(index):
    nodes = {'a': {'label': 'Greg Mesack', 'kind': 'person', 'source_summary': 'Alpha Council'}}
    index.enqueue_people(nodes)
    with index.connect() as db:
        db.execute("UPDATE queue SET state='referenced',next_check=9999999999,attempts=2")
    before = queue_row(index)
    index.enqueue_people(nodes)
    assert queue_row(index) == before
    nodes['a']['source_summary'] = 'Beta League'
    index.enqueue_people(nodes)
    changed = queue_row(index)
    assert changed['signature'] != before['signature']
    assert changed['state'] == 'queued' and changed['next_check'] == 0


def test_inventory_iteration_order_does_not_reset_fingerprint(index):
    nodes = {'a': {'label': 'Greg Mesack', 'kind': 'person'},
             'b': {'label': 'Gregory Mesack', 'kind': 'person'}}
    index.enqueue_people(nodes)
    before = queue_row(index)['signature']
    index.enqueue_people(dict(reversed(list(nodes.items()))))
    assert queue_row(index)['signature'] == before


def test_overall_source_revision_skips_inventory_scan_until_revision_changes(index):
    nodes = {'a': {'label': 'Greg Mesack', 'kind': 'person', 'source_summary': 'Alpha Council'}}
    index.enqueue_people(nodes, source_revision='source-one')
    before = queue_row(index)
    class MustNotScan(dict):
        def items(self):
            pytest.fail('Stable overall source revision rescanned inventory')
    index.enqueue_people(MustNotScan(nodes), source_revision='source-one')
    assert queue_row(index) == before
    nodes['a']['source_summary'] = 'Beta League'
    index.enqueue_people(nodes, source_revision='source-two')
    assert queue_row(index)['signature'] != before['signature']


def test_failure_backoff_prevents_early_retry_and_increases_delay(index, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    index.enqueue([{'display_name': 'Greg Mesack', 'node_ids': ['a']}])
    calls = []
    def fail(*args):
        calls.append(args)
        raise OSError('Temporary unavailable')
    monkeypatch.setattr(index, 'fetch_reference', fail)
    assert index.process_one()
    first = queue_row(index)
    assert first['state'] == 'retry'
    assert first['next_check'] == 1300
    clock[0] = 1299
    assert not index.process_one() and len(calls) == 1
    clock[0] = 1300
    assert index.process_one()
    assert queue_row(index)['next_check'] == 1900
    assert len(calls) == 2


@pytest.mark.parametrize('accepted', [True, False])
def test_completed_reference_or_unresolved_lookup_revisited_after_period(index, monkeypatch, accepted):
    clock = [1000.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    monkeypatch.setattr(index, 'fetch_reference', lambda *args: {'text': 'evidence'} if accepted else None)
    index.enqueue([{'display_name': 'Greg Mesack', 'node_ids': ['a']}])
    assert index.process_one()
    row = queue_row(index)
    assert row['state'] == ('referenced' if accepted else 'unresolved')
    assert row['next_check'] > clock[0]
    assert not index.process_one()
    clock[0] = row['next_check']
    assert index.process_one()
    assert queue_row(index)['attempts'] == 2


def test_text_update_invalidates_reference_revision_with_constant_document_count(index, monkeypatch):
    bodies = [b'<h1>Greg Mesack</h1><p>Greg served Alpha Council before Beta League, leading federal advocacy, policy development and professional outreach.</p>',
              b'<h1>Greg Mesack</h1><p>Greg served Alpha Council before Gamma League, leading federal advocacy, policy development and professional outreach.</p>']
    current = [0]
    ticks = iter([101, 102, 103])
    monkeypatch.setattr(web.time, 'time_ns', lambda: next(ticks))
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url: (bodies[current[0]], 'text/html', url))
    index.fetch_reference('https://official.example/greg', 'Greg Mesack')
    before = index.revision()
    index.fetch_reference('https://official.example/greg', 'Greg Mesack')
    assert index.revision() == before
    current[0] = 1
    index.fetch_reference('https://official.example/greg', 'Greg Mesack')
    assert index.revision() != before
    assert len(index.references()) == 1
    assert 'Gamma League' in index.references()[0]['text']


@pytest.mark.parametrize('query,subject', [('John A Smith', 'John B Smith'),
                                         ('John Smith Jr', 'John Smith Sr')])
def test_conflicting_subject_initial_or_suffix_is_not_labeled_as_query(index, monkeypatch, query, subject):
    raw = ('<h1>' + subject + '</h1><p>' + subject +
           ' served Alpha Council before Beta League, leading federal advocacy and policy outreach.</p>').encode()
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url: (raw, 'text/html', url))
    assert index.fetch_reference('https://official.example/john', query) is None
    assert index.references() == []


def test_fetched_reference_records_actual_middle_name(index, monkeypatch):
    raw = b'<h1>John A Smith</h1><p>John A Smith served Alpha Council before Beta League, leading federal advocacy and policy outreach.</p>'
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url: (raw, 'text/html', url))
    ref = index.fetch_reference('https://official.example/john', 'John Smith')
    assert ref['name'] == 'John A Smith'
    assert ref['query_name'] == 'John Smith'


def test_all_name_variants_resume_and_only_complete_family_after_last(index, monkeypatch):
    index.enqueue_people({'a': {'label': 'John A Smith', 'kind': 'person'},
                          'b': {'label': 'John B Smith', 'kind': 'person'}})
    names = []
    monkeypatch.setattr(index, 'discover', lambda name: names.append(name) or [])
    assert index.process_one()
    assert names == ['John A Smith']
    assert index.status()['scanned_people'] == 0
    assert queue_row(index, 'john smith')['variant_cursor'] == 1
    reopened = web.IdentityWebIndex(index.path.parents[2])
    monkeypatch.setattr(reopened, 'discover', lambda name: names.append(name) or [])
    assert reopened.process_one()
    assert names == ['John A Smith', 'John B Smith']
    assert reopened.status()['scanned_people'] == 2
    assert not reopened.process_one()
    due = queue_row(reopened, 'john smith')['next_check']
    monkeypatch.setattr(web.time, 'time', lambda: due)
    assert reopened.process_one()
    assert names[-1] == 'John A Smith'


def test_variant_failure_retries_same_name_and_source_change_resets_cursor(index, monkeypatch):
    nodes = {'a': {'label': 'John A Smith', 'kind': 'person'},
             'b': {'label': 'John B Smith', 'kind': 'person'}}
    index.enqueue_people(nodes)
    seen = []
    def discovery(name):
        seen.append(name)
        if len(seen) == 1:
            raise OSError('Temporary offline')
        return []
    monkeypatch.setattr(index, 'discover', discovery)
    index.process_one()
    row = queue_row(index, 'john smith')
    assert row['variant_cursor'] == 0 and row['state'] == 'retry'
    monkeypatch.setattr(web.time, 'time', lambda due=row['next_check']: due)
    index.process_one()
    assert seen == ['John A Smith', 'John A Smith']
    assert queue_row(index, 'john smith')['variant_cursor'] == 1
    nodes['a']['source_summary'] = 'New source observation'
    index.enqueue_people(nodes)
    assert queue_row(index, 'john smith')['variant_cursor'] == 0


def prioritized_person(digest='profile-one'):
    return {'id': 'politico:greg', 'kind': 'person', 'label': 'Gregory Mesack',
            'profile_content_sha256': digest, 'provenance': {'receipt_sha256': 'global-one'}}


def test_prioritize_preserves_family_and_persists_digest(index):
    index.enqueue_people({'lda:greg': {'kind': 'person', 'label': 'Greg Mesack'},
                          'payroll:greg': {'kind': 'person', 'label': 'Gregory Mesack'}})
    before = queue_row(index)
    assert index.prioritize_people([prioritized_person()])['changed_people'] == 1
    row = queue_row(index)
    assert set(json.loads(row['node_ids'])) == {'lda:greg', 'payroll:greg', 'politico:greg'}
    assert set(json.loads(row['names_json'])) == {'Greg Mesack', 'Gregory Mesack'}
    assert before['signature'] and row['signature'] is None  # membership changed: next inventory pass re-fingerprints
    assert row['priority'] == 200 and row['next_check'] == 0
    with index.connect() as db:
        db.execute("UPDATE queue SET state='retry',next_check=9999999999,attempts=4")
    reopened = web.IdentityWebIndex(index.path.parents[2])
    node = prioritized_person()
    node['provenance']['receipt_sha256'] = 'unrelated-global-update'
    assert reopened.prioritize_people([node])['unchanged_people'] == 1
    unchanged = queue_row(reopened)
    assert unchanged['state'] == 'retry' and unchanged['next_check'] == 9999999999
    assert unchanged['attempts'] == 4
    assert reopened.prioritize_people([prioritized_person('profile-two')])['changed_people'] == 1
    assert queue_row(reopened)['state'] == 'queued'
    assert queue_row(reopened)['next_check'] == 0


def test_prioritize_during_check_defers_refresh_without_duplicate_claim(index, monkeypatch):
    index.prioritize_people([prioritized_person()])
    def fetch(*args):
        before = queue_row(index)
        assert before['state'] == 'checking'
        index.prioritize_people([prioritized_person('new-content')])
        row = queue_row(index)
        assert row['state'] == 'checking' and row['priority_refresh_pending'] == 1
        assert index.process_one() is False
        return None
    monkeypatch.setattr(index, 'fetch_reference', fetch)
    assert index.process_one()
    row = queue_row(index)
    assert row['state'] == 'queued' and row['next_check'] == 0
    assert row['priority_refresh_pending'] == 0 and row['variant_cursor'] == 0


def test_prioritize_mapping_ignores_nonpeople_and_keeps_other_families(index):
    index.enqueue_people({'other': {'kind': 'person', 'label': 'Jane Smith'}})
    before = queue_row(index, 'jane smith')
    assert index.prioritize_people({'politico:greg': prioritized_person(),
                                   'org': {'kind': 'organization', 'label': 'Council'}})['prioritized_families'] == 1
    assert queue_row(index, 'jane smith') == before


def test_receipt_binding_change_keeps_family_progress_but_structural_change_resets(index):
    node = prioritized_person()
    node['employment_history'] = [
        {'organization': 'Congress', 'provenance': {'sha256': 'stable-profile',
            'receipt_sha256': 'receipt-one', 'accepted_prefix_sha256': 'prefix-one'}},
        {'organization': 'LDA employer', 'report_year': 2025}]
    nodes = {node['id']: node}
    index.enqueue_people(nodes)
    with index.connect() as db:
        db.execute("UPDATE queue SET state='retry',next_check=9999999999,attempts=4")
    before = queue_row(index)
    node['employment_history'][0]['provenance'].update(
        receipt_sha256='receipt-two', accepted_prefix_sha256='prefix-two')
    index.enqueue_people(nodes)
    assert queue_row(index) == before
    node['employment_history'][1]['report_year'] = 2026
    index.enqueue_people(nodes)
    assert queue_row(index)['state'] == 'queued'
    assert queue_row(index)['signature'] != before['signature']


def test_unhashed_history_receipt_is_not_silently_ignored(index):
    node = prioritized_person()
    node['employment_history'] = [{'provenance': {'receipt_sha256': 'one'}}]
    index.enqueue_people({node['id']: node})
    before = queue_row(index)['signature']
    node['employment_history'][0]['provenance']['receipt_sha256'] = 'two'
    index.enqueue_people({node['id']: node})
    assert queue_row(index)['signature'] != before


def test_priority_boost_expires_only_after_successful_cycle(index, monkeypatch):
    index.prioritize_people([prioritized_person()])
    monkeypatch.setattr(index, 'fetch_reference', lambda *args: (_ for _ in ()).throw(OSError('offline')))
    assert index.process_one()
    row = queue_row(index)
    assert row['state'] == 'retry' and row['priority'] == 200
    monkeypatch.setattr(web.time, 'time', lambda due=row['next_check']: due)
    monkeypatch.setattr(index, 'fetch_reference', lambda *args: None)
    assert index.process_one()
    row = queue_row(index)
    assert row['state'] == 'unresolved' and row['priority'] == web.SEED_PRIORITY  # seeded family
    assert row['next_check'] > web.time.time()
    index.prioritize_people([prioritized_person()])
    assert queue_row(index) == row


def test_inventory_content_change_during_check_keeps_claim_and_refreshes(index, monkeypatch):
    nodes = {'a': {'kind': 'person', 'label': 'Greg Mesack', 'stints': []}}
    index.enqueue_people(nodes)
    def fetch(*args):
        nodes['a']['stints'] = [{'office': 'new-office'}]
        index.enqueue_people(nodes)
        assert queue_row(index)['state'] == 'checking'
        assert index.process_one() is False
        return None
    monkeypatch.setattr(index, 'fetch_reference', fetch)
    assert index.process_one()
    assert queue_row(index)['next_check'] == 0
    assert queue_row(index)['state'] == 'queued'


def test_canonical_profile_reader_bindings_do_not_reset_family(index):
    volatile = {'verified_at': '2026-09-04T00:00:00Z', 'verified_prefix_bytes': 100,
                'verified_prefix_sha256': 'prefix-one', 'raw_line_sha256': 'line-one',
                'profile_raw_line_sha256': 'profile-line-one', 'profile_row_sha256': 'row-one',
                'byte_start': 0, 'byte_end': 99, 'observed_at': '2026-09-04T00:00:00Z',
                'prefix_binding': {'receipt': 'one'}, 'receipt_sha256': 'receipt-one',
                'accepted_prefix_sha256': 'accepted-one'}
    node = prioritized_person()
    node['employment_history'] = [{'organization': 'Congress', 'provenance': {
        'sha256': 'same-content', 'sha256_scope': 'CANONICAL_PROFILE_CONTENT',
        'source_profile_uuid': 'person-uuid', 'source_url': 'https://example.test/person',
        **volatile}}]
    nodes = {node['id']: node}
    index.enqueue_people(nodes)
    with index.connect() as db:
        db.execute("UPDATE queue SET state='referenced',next_check=9999999999,attempts=2")
    before = queue_row(index)
    proof = node['employment_history'][0]['provenance']
    for key, value in volatile.items():
        proof[key] = value + 100 if isinstance(value, int) else {'receipt': 'two'} if isinstance(value, dict) else value + '-new'
        index.enqueue_people(nodes)
        assert queue_row(index) == before, key
    proof['sha256'] = 'changed-profile-content'
    index.enqueue_people(nodes)
    assert queue_row(index)['signature'] != before['signature']
    assert queue_row(index)['next_check'] == 0


def test_nonprofile_hashed_proof_retains_observation_and_line_metadata():
    evidence = {'provenance': {'sha256': 'filing-content', 'sha256_scope': 'FILING_DOCUMENT',
                              'observed_at': '2026-09-04', 'raw_line_sha256': 'line-one',
                              'verified_prefix_bytes': 100}}
    assert web.stable_evidence(evidence) == evidence


def http_error(url, code, reason='error'):
    return web.error.HTTPError(url, code, reason, {}, None)


def outcomes(index, key):
    with index.connect() as db:
        return sorted(db.execute('SELECT url,outcome FROM reference_outcomes WHERE key=?', (key,)))


def test_prioritized_newcomer_keeps_completed_lookup_when_inventory_fingerprints_it(index, monkeypatch):
    node = prioritized_person()
    index.prioritize_people([node])
    monkeypatch.setattr(index, 'fetch_reference', lambda *args: None)
    assert index.process_one()
    done = queue_row(index)
    assert done['state'] == 'unresolved' and done['signature'] is None
    index.enqueue_people({node['id']: node})
    row = queue_row(index)
    assert row['signature'] is not None
    assert (row['state'], row['attempts'], row['next_check'], row['variant_cursor']) == ('unresolved', 1, done['next_check'], 0)
    index.enqueue_people({node['id']: node})
    assert queue_row(index) == row
    node['employment_history'] = [{'organization': 'Changed employer'}]
    index.enqueue_people({node['id']: node})
    assert queue_row(index)['state'] == 'queued' and queue_row(index)['next_check'] == 0


def test_prioritized_family_recheck_is_not_discarded_by_next_inventory_upsert(index, monkeypatch):
    hill = {'kind': 'person', 'label': 'Gregory Mesack', 'stints': [{'office': 'first office'}]}
    index.enqueue_people({'hill:greg': hill})
    monkeypatch.setattr(index, 'fetch_reference', lambda *args: None)
    assert index.process_one()
    assert queue_row(index)['state'] == 'unresolved'
    politico = prioritized_person()
    assert index.prioritize_people([politico])['changed_people'] == 1
    assert queue_row(index)['state'] == 'queued'
    assert index.process_one()
    done = queue_row(index)
    assert done['state'] == 'unresolved' and done['attempts'] == 2
    index.enqueue_people({'hill:greg': hill, politico['id']: politico})
    row = queue_row(index)
    assert row['state'] == 'unresolved' and row['next_check'] == done['next_check'] and row['attempts'] == 2
    assert row['signature'] is not None
    hill['stints'] = [{'office': 'changed office'}]
    index.enqueue_people({'hill:greg': hill, politico['id']: politico})
    assert queue_row(index)['state'] == 'queued'


def test_prioritized_newcomer_with_unchecked_inventory_variants_is_requeued(index, monkeypatch):
    politico = prioritized_person()
    index.prioritize_people([politico])
    monkeypatch.setattr(index, 'fetch_reference', lambda *args: None)
    assert index.process_one()
    index.enqueue_people({politico['id']: politico, 'hill:greg': {'kind': 'person', 'label': 'Greg Mesack'}})
    row = queue_row(index)
    assert row['state'] == 'queued' and row['next_check'] == 0 and row['variant_cursor'] == 0
    assert json.loads(row['names_json']) == ['Greg Mesack', 'Gregory Mesack']


def test_deterministic_reference_failures_complete_the_variant_and_are_recorded(index, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}})
    urls = ['https://blocked.example/bio', 'https://gone.example/bio', 'http://plain.example/bio', 'https://missing.example/bio']
    monkeypatch.setattr(index, 'discover', lambda name: list(urls))
    def fetch(url, name, kind=None, title=None):
        if url.startswith('https://blocked'):
            raise ValueError('Reference unavailable under site access rules: blocked.example')
        if url.startswith('https://gone'):
            raise http_error(url, 410, 'Gone')
        if url.startswith('https://missing'):
            raise http_error(url, 404, 'Not Found')
        raise ValueError('Reference must be a public HTTPS URL')
    monkeypatch.setattr(index, 'fetch_reference', fetch)
    assert index.process_one()
    row = queue_row(index, 'jane jones')
    assert row['state'] == 'unresolved' and row['error'] is None
    assert row['next_check'] == 1000 + 30 * 86400
    assert outcomes(index, 'jane jones') == [(url, 'unavailable') for url in sorted(urls)]
    status = index.status()
    assert status['checked'] == 1 and status['retrying'] == 0
    assert status['reference_outcomes'] == {'unavailable': 4}


@pytest.mark.parametrize('failure', [
    lambda url: http_error(url, 503, 'Service Unavailable'),
    lambda url: http_error(url, 429, 'Too Many Requests'),
    lambda url: web.socket.timeout('The read operation timed out'),
    lambda url: ConnectionResetError(10054, 'An existing connection was forcibly closed by the remote host'),
    lambda url: web.error.URLError('temporary failure in name resolution'),
    lambda url: http.client.IncompleteRead(b'partial'),
])
def test_transient_reference_failures_keep_backoff_without_terminal_outcome(index, monkeypatch, failure):
    clock = [1000.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}})
    monkeypatch.setattr(index, 'discover', lambda name: ['https://flaky.example/bio'])
    monkeypatch.setattr(index, 'fetch_reference', lambda url, *a: (_ for _ in ()).throw(failure(url)))
    assert index.process_one()
    row = queue_row(index, 'jane jones')
    assert row['state'] == 'retry' and row['next_check'] == 1300 and row['error']
    assert outcomes(index, 'jane jones') == []


def test_terminal_reference_outcome_is_not_refetched_on_retry(index, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}})
    monkeypatch.setattr(index, 'discover', lambda name: ['https://gone.example/bio', 'https://flaky.example/bio'])
    calls = []
    def fetch(url, name, kind=None, title=None):
        calls.append(url)
        if 'gone' in url:
            raise http_error(url, 403, 'Forbidden')
        raise OSError('The read operation timed out')
    monkeypatch.setattr(index, 'fetch_reference', fetch)
    assert index.process_one()
    row = queue_row(index, 'jane jones')
    assert row['state'] == 'retry' and row['error'] == 'The read operation timed out'
    assert outcomes(index, 'jane jones') == [('https://gone.example/bio', 'unavailable')]
    clock[0] = row['next_check']
    monkeypatch.setattr(index, 'fetch_reference', lambda url, *a: calls.append(url) or {'text': 'evidence'})
    assert index.process_one()
    assert calls == ['https://gone.example/bio', 'https://flaky.example/bio', 'https://flaky.example/bio']
    assert queue_row(index, 'jane jones')['state'] == 'referenced'


def test_unexpected_fetch_exception_is_recorded_and_worker_moves_on(index, monkeypatch):
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}, 'b': {'kind': 'person', 'label': 'John Smith'}})
    monkeypatch.setattr(index, 'discover', lambda name: ['https://slow.example/' + name.split()[0]])
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    def truncated(url, maximum=4_000_000):
        raise http.client.IncompleteRead(b'partial')
    monkeypatch.setattr(web, 'download', truncated)
    assert index.process_one()
    row = queue_row(index, 'jane jones')
    assert row['state'] == 'retry' and 'IncompleteRead' in row['error']
    assert index.process_one()
    assert queue_row(index, 'john smith')['state'] == 'retry'
    with index.connect() as db:
        assert db.execute("SELECT count(*) FROM queue WHERE state='checking'").fetchone()[0] == 0


def test_unreadable_pdf_is_a_terminal_reference_outcome(index, monkeypatch):
    pytest.importorskip('pypdf')
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}})
    monkeypatch.setattr(index, 'discover', lambda name: ['https://docs.example/bio.pdf'])
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url, maximum=4_000_000: (b'%PDF-1.4 truncated', 'application/pdf', url))
    assert index.process_one()
    assert queue_row(index, 'jane jones')['state'] == 'unresolved'
    assert outcomes(index, 'jane jones') == [('https://docs.example/bio.pdf', 'unavailable')]


def test_orphaned_checking_claim_is_reclaimed_after_stale_window(index, monkeypatch):
    clock = [100000.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}})
    with index.connect() as db:
        db.execute("UPDATE queue SET state='checking',claimed_at=?", (clock[0] - 10,))
    monkeypatch.setattr(index, 'discover', lambda name: [])
    assert not index.process_one()
    clock[0] += web.STALE_CLAIM_SECONDS + 1
    assert index.process_one()
    assert queue_row(index, 'jane jones')['state'] == 'unresolved'


def test_unreachable_robots_is_transient_but_fetched_rules_are_final(index, monkeypatch):
    monkeypatch.setattr(web, 'download', lambda url, maximum=4_000_000: (_ for _ in ()).throw(ConnectionResetError(10054, 'reset')))
    with pytest.raises(OSError):
        index.allowed('https://flaky.example/bio')
    monkeypatch.setattr(web, 'download', lambda url, maximum=4_000_000: (_ for _ in ()).throw(http_error(url, 503)))
    with pytest.raises(OSError):
        index.allowed('https://flaky.example/bio')
    monkeypatch.setattr(web, 'download', lambda url, maximum=4_000_000: (b'User-agent: *\nDisallow: /bio', 'text/plain', url))
    assert index.allowed('https://strict.example/bio') is False
    assert index.allowed('https://strict.example/about') is True


def test_status_reports_terminal_checks_and_attention_counts_separately(index):
    index.enqueue_people({i: {'kind': 'person', 'label': n} for i, n in
                          {'a': 'Jane Jones', 'b': 'John Smith', 'c': 'Amy Adams', 'd': 'Dana Dorsey', 'e': 'Cal Cole'}.items()})
    with index.connect() as db:
        db.execute("UPDATE queue SET state='retry',updated_at='2026-09-05T03:00:00+00:00',error='HTTP Error 503' WHERE key='jane jones'")
        db.execute("UPDATE queue SET state='retry',updated_at='2026-09-05T02:00:00+00:00',error='HTTP Error 503' WHERE key='john smith'")
        db.execute("UPDATE queue SET state='unresolved',updated_at='2026-09-05T01:00:00+00:00' WHERE key='amy adams'")
        db.execute("UPDATE queue SET state='referenced',updated_at='2026-09-05T00:30:00+00:00' WHERE key='dana dorsey'")
        db.execute("UPDATE queue SET state='queued',updated_at='2026-09-05T04:00:00+00:00',error='stale text' WHERE key='cal cole'")
    status = index.status()
    assert status['checked'] == 2 and status['retrying'] == 2 and status['attention'] == 2
    assert status['unresolved'] == 1 and status['referenced'] == 1 and status['queued'] == 3
    assert status['last_completed_at'] == '2026-09-05T01:00:00+00:00'
    assert status['errors'] == ['HTTP Error 503']


def test_seed_families_outrank_profile_prioritized_families(index, monkeypatch):
    index.prioritize_people([{'id': 'politico:aaron', 'kind': 'person', 'label': 'Aaron Aardvark', 'profile_content_sha256': 'x'}])
    index.enqueue([{'display_name': 'Greg Mesack', 'node_ids': ['a']}])
    assert queue_row(index)['priority'] == web.SEED_PRIORITY > 200  # nickname-canonical key 'gregory mesack'
    seen = []
    monkeypatch.setattr(index, 'fetch_reference', lambda url, name, *a: seen.append(name) or None)
    monkeypatch.setattr(index, 'discover', lambda name: seen.append(name) or [])
    assert index.process_one()
    assert seen == ['Greg Mesack']
    assert queue_row(index)['priority'] == web.SEED_PRIORITY


def test_references_verified_counts_distinct_content_not_name_variants(index, monkeypatch):
    raw = b'<h1>Greg Mesack</h1><p>Greg Mesack served Alpha Council before Beta League, where he directs federal advocacy and public policy engagement.</p>'
    monkeypatch.setattr(index, 'allowed', lambda url: True)
    monkeypatch.setattr(web, 'download', lambda url: (raw, 'text/html', url))
    for name in ('Greg Mesack', 'GREG MESACK', 'Gregory Mesack'):
        assert index.fetch_reference('https://official.example/greg', name)
    with index.connect() as db:
        assert db.execute('SELECT count(*) FROM documents').fetchone()[0] == 3
    assert index.status()['references_verified'] == 1


def test_reference_accepted_under_earlier_variant_marks_family_referenced(index, monkeypatch):
    index.enqueue_people({'a': {'label': 'John A Smith', 'kind': 'person'}, 'b': {'label': 'John B Smith', 'kind': 'person'}})
    monkeypatch.setattr(index, 'discover', lambda name: ['https://bio.example/' + name.replace(' ', '-')])
    monkeypatch.setattr(index, 'fetch_reference', lambda url, name, *a: {'text': 'evidence'} if name == 'John A Smith' else None)
    assert index.process_one()
    assert queue_row(index, 'john smith')['state'] == 'queued'
    assert index.process_one()
    assert queue_row(index, 'john smith')['state'] == 'referenced'


def test_family_lookup_budget_defers_remaining_references_without_interrupting_a_request(index, monkeypatch):
    clock, mono = [1000.0], [0.0]
    monkeypatch.setattr(web.time, 'time', lambda: clock[0])
    monkeypatch.setattr(web.time, 'monotonic', lambda: mono[0])
    index.enqueue_people({'a': {'kind': 'person', 'label': 'Jane Jones'}})
    urls = ['https://one.example/bio', 'https://two.example/bio', 'https://three.example/bio']
    monkeypatch.setattr(index, 'discover', lambda name: list(urls))
    calls = []
    def slow(url, name, *a):
        calls.append(url)
        mono[0] += web.LOOKUP_BUDGET_SECONDS * 0.75
        return None
    monkeypatch.setattr(index, 'fetch_reference', slow)
    assert index.process_one()
    row = queue_row(index, 'jane jones')
    assert calls == urls[:2]
    assert row['state'] == 'retry' and 'deferred' in row['error'].lower() and row['next_check'] == 1300
    clock[0] = row['next_check']
    assert index.process_one()
    assert calls == urls[:2] + [urls[2]]
    assert queue_row(index, 'jane jones')['state'] == 'unresolved'


def database_shape(index):
    with index.connect() as db:
        return (list(db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name")),
                list(db.execute('SELECT * FROM queue ORDER BY key')),
                list(db.execute('SELECT * FROM reference_outcomes ORDER BY key,name,url')))


def test_live_sqlite_migrates_in_place_on_open(tmp_path):
    root = os.environ.get('STAFFER_GRAPH_TEST_SOURCE')
    live = Path(root) / 'audits/staffer_live_runtime/identity_reference_v1.sqlite' if root else None
    if not live or not live.is_file():
        pytest.skip('requires a read-only snapshot of the live identity reference sqlite')
    target_root = tmp_path / 'copy'
    target = target_root / 'audits/staffer_live_runtime/identity_reference_v1.sqlite'
    target.parent.mkdir(parents=True)
    with sqlite3.connect('file:' + live.as_posix() + '?mode=ro', uri=True) as source, sqlite3.connect(target) as copied:
        source.backup(copied)
        before = dict(copied.execute('SELECT state,count(*) FROM queue GROUP BY state'))
        columns = {row[1] for row in copied.execute('PRAGMA table_info(queue)')}
    migrated = web.IdentityWebIndex(target_root)
    with migrated.connect() as db:
        assert columns <= {row[1] for row in db.execute('PRAGMA table_info(queue)')}
        after = dict(db.execute('SELECT state,count(*) FROM queue GROUP BY state'))
    assert sum(before.values()) == sum(after.values())
    assert after.get('checking', 0) == 0
    for state in ('referenced', 'unresolved', 'retry'):
        assert before.get(state, 0) == after.get(state, 0)
    snapshot = database_shape(migrated)
    assert database_shape(web.IdentityWebIndex(target_root)) == snapshot


def test_old_sqlite_migration_adds_defaults_and_is_idempotent(tmp_path):
    target = tmp_path / 'audits/staffer_live_runtime/identity_reference_v1.sqlite'
    target.parent.mkdir(parents=True)
    with sqlite3.connect(target) as db:
        db.executescript("""CREATE TABLE queue (
            key TEXT PRIMARY KEY, name TEXT NOT NULL, node_ids TEXT NOT NULL,
            priority INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0, updated_at TEXT, error TEXT);
            INSERT INTO queue(key,name,node_ids,priority,state,attempts)
            VALUES('test person','Test Person','["p"]',10,'checking',2);""")
    migrated = web.IdentityWebIndex(tmp_path)
    with migrated.connect() as db:
        columns = {row[1] for row in db.execute('PRAGMA table_info(queue)')}
        assert {'accepted_references', 'claimed_at', 'next_check', 'variant_cursor', 'priority_refresh_pending', 'names_json', 'signature'} <= columns
        assert db.execute('SELECT state,attempts,accepted_references,claimed_at FROM queue').fetchone() == ('queued', 2, 0, 0)
        assert db.execute('SELECT count(*) FROM reference_outcomes').fetchone()[0] == 0
    assert database_shape(web.IdentityWebIndex(tmp_path)) == database_shape(migrated)


@pytest.mark.parametrize('status', [408, 429, 503])
def test_transient_robots_status_does_not_become_terminal(index, monkeypatch, status):
    def unavailable(*args):
        raise web.error.HTTPError(args[0], status, 'temporary', {}, None)
    monkeypatch.setattr(web, 'download', unavailable)
    with pytest.raises(OSError, match='robots.txt unavailable'):
        index.allowed('https://official.example/person')
    assert 'https://official.example' not in index.robots
