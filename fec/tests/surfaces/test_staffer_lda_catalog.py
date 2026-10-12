import gzip
import json
from pathlib import Path
import pytest
from fec.surfaces.staffer_lda_catalog import build_catalog, LdaCatalog, ROLES, sha
from fec.surfaces.staffer_network_explorer import build_index


def corpus_at(root):
    root.mkdir()
    rows = {
        'lobbyist_assertions': [{'source_person_id': 'lda:lobbyist:1', 'display_name': 'Person', 'covered_position_values': ['Full retained text']}],
        'organization_assertions': [{'source_org_id': 'lda:registrant:1', 'display_name': 'Registrant', 'organization_kind': 'registrant'}, {'source_org_id': 'lda:client:1', 'display_name': 'Client', 'organization_kind': 'client'}],
        'activity_assertions': [{'source_activity_id': 'lda:activity:1', 'description': 'Full activity description', 'government_entities': ['House'], 'filing_year': 2026}],
        'industry_edges': [{'edge_id': f'lda:edge:{i}', 'subject_id': 'lda:activity:1', 'object_id': 'lda:registrant:1' if i<122 else 'lda:missing:1', 'predicate': 'FILED_BY'} for i in range(123)],
        'covered_position_assertions': [{'assertion_id': 'lda:covered-position:1', 'source_person_id': 'lda:lobbyist:1', 'source_activity_id': 'lda:activity:1', 'covered_position': 'Full retained text'}]
    }
    specs = []
    for role in ROLES:
        p = root / (role + '.ndjson.gz')
        with gzip.open(p, 'wt', encoding='utf-8') as stream:
            for row in rows[role]: stream.write(json.dumps(row)+'\n')
        specs.append({'role': role, 'path': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p), 'records': len(rows[role])})
    (root/'lda_source_native_corpus_receipt.json').write_text(json.dumps({'status': 'PASS', 'failures': [], 'files': specs}))
    return rows


def test_complete_records_disjoint_pages_missing_endpoints_and_no_operator_promotion(tmp_path):
    corpus = tmp_path/'source'; corpus_at(corpus)
    output = tmp_path/'catalog.sqlite'; receipt = build_catalog(output, corpus)
    assert receipt['counts']['relationships'] == 124
    assert receipt['counts']['missing_endpoint_assertions'] == 1
    cat = LdaCatalog(output)
    assert cat.record('lda:activity:1')['record']['government_entities'] == ['House']
    ids = []
    for offset in (0, 50, 100):
        page = cat.record('organization:lda:registrant:1', offset)
        assert page['total'] == 122 and page['has_more'] == (offset < 100)
        ids += [r['source_relationship_id'] for r in page['relationships']]
        assert all(not r['operator_included'] and not r['canonical_eligible'] for r in page['relationships'])
    assert len(ids) == len(set(ids)) == 122
    person = cat.record('lda:lobbyist:1')
    assert person['relationships'][0]['description'] == 'Full retained text'
    missing = cat.record('lda:activity:1', 100)['relationships'][-2]
    assert missing['endpoint_missing']
    nodes = {'organization:lda:registrant:1': {'id': 'organization:lda:registrant:1', 'kind': 'organization', 'node_kind': 'organization', 'label': 'Registrant'}}
    index = build_index(nodes, {}, 'r', lda_catalog=cat)['payload']
    # Ruled 2026-09-06 (D-022 item 2): the LDA catalog indexes organisations, and
    # organisations are not people. This pin was INVERTED by that ruling -- it
    # used to assert the two registrant rows entered the people index. The
    # receipt still travels, because the coverage note is computed from it.
    assert index['total'] == 0 and index['people_total'] == 0
    assert index['lda_source_catalog']['counts']['organization'] == 2
    assert not cat.record('missing')['record']
    with pytest.raises(FileExistsError): build_catalog(output, corpus)
    with output.open('ab') as stream: stream.write(b'changed')
    with pytest.raises(ValueError, match='seal mismatch'): LdaCatalog(output)


def test_input_hash_mismatch_rejected_before_publication(tmp_path):
    corpus = tmp_path/'source'; corpus_at(corpus)
    with (corpus/'industry_edges.ndjson.gz').open('ab') as stream: stream.write(b'changed')
    output = tmp_path/'catalog.sqlite'
    with pytest.raises(ValueError, match='hash/size mismatch'): build_catalog(output, corpus)
    assert not output.exists()
