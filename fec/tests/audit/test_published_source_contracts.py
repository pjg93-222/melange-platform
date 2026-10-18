import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from audit.published_source_contracts import file_sha256, personal_source_errors, press_source_errors, vote_source_errors


def test_display_metadata_preserves_every_source_value():
    source = {'A': {'items': [{'claim': 'A grew up in Ohio', 'category': 'childhood', 'display_eligible': True},
                              {'claim': 'A was raised in Ohio', 'category': 'childhood', 'display_eligible': True}], 'n_items': 2}}
    published = copy.deepcopy(source)
    published['A']['display_dedupe'] = {
        'schema': 'personal-display-dedupe-v1', 'suppressed_count': 1,
        'suppressed_indices': [1], 'clusters': [{'representative_index': 0, 'suppressed_indices': [1]}]}
    assert not personal_source_errors(source, published)
    changed = copy.deepcopy(published)
    changed['A']['items'][0]['claim'] = 'An unsupported claim'
    assert personal_source_errors(source, changed)
    changed = copy.deepcopy(published)
    changed['A']['display_dedupe']['suppressed_indices'] = [0, 1]
    changed['A']['display_dedupe']['suppressed_count'] = 2
    assert personal_source_errors(source, changed)
    changed = copy.deepcopy(published)
    changed['A']['new_source_field'] = 'unrecorded'
    assert personal_source_errors(source, changed)
    assert personal_source_errors(source, {})


@pytest.fixture
def press(tmp_path):
    ledger = tmp_path / 'data/engine/press_collection_receipts'
    ledger.mkdir(parents=True)
    runs, current = [], None
    for number in (1, 2):
        source = tmp_path / f'press_refresh_2026090{number}'
        source.mkdir()
        names = ('press_delta_scored.csv', 'press_delta_host_articles.csv', 'press_delta_article_mentions.csv')
        for name in (*names, 'press_network_retry_receipt.json'):
            (source / name).write_text(f'{number}:{name}', encoding='utf-8')
        hashes = {p.name: file_sha256(p) for p in source.iterdir()}
        current = {'schema': 'collection-intake-receipt-v1', 'status': 'ACCEPTED', 'inputs': {
            'press_delta_root': str(source.resolve()),
            'press_retry_receipt_sha256': hashes['press_network_retry_receipt.json'],
            'press_source_sha256': {name: hashes[name] for name in names}}}
        path = ledger / (source.name + '.json')
        path.write_text(json.dumps(current), encoding='utf-8')
        runs.append({'source_run': str(source), 'source_sha256': hashes,
                     'collection_intake': {'path': str(path), 'sha256': file_sha256(path)}})
    feed = {'promotion_mode': 'CUMULATIVE_ACCEPTED_RUN_LEDGER', 'accepted_runs': runs,
            'counts': {'accepted_runs': 2}, 'contract': {'accepted_runs_are_cumulative': True}}
    return tmp_path, feed, current


def test_cumulative_press_checks_all_runs_and_current_binding(press):
    root, feed, current = press
    assert not press_source_errors(feed, root, current)
    old = Path(feed['accepted_runs'][0]['source_run']) / 'press_delta_scored.csv'
    old.write_text('tampered', encoding='utf-8')
    assert any('source bytes differ' in error for error in press_source_errors(feed, root, current))


def test_cumulative_press_rejects_omitted_accepted_run(press):
    root, feed, current = press
    feed['accepted_runs'] = feed['accepted_runs'][1:]
    feed['counts']['accepted_runs'] = 1
    assert any('omitted' in error for error in press_source_errors(feed, root, current))


def test_cumulative_press_rejects_intake_tampering(press):
    root, feed, current = press
    path = Path(feed['accepted_runs'][0]['collection_intake']['path'])
    path.write_text('{}', encoding='utf-8')
    assert any('receipt bytes differ' in error for error in press_source_errors(feed, root, current))


def test_cumulative_press_rejects_rebound_inconsistent_receipt(press):
    root, feed, current = press
    run = feed['accepted_runs'][0]
    path = Path(run['collection_intake']['path'])
    value = json.loads(path.read_text(encoding='utf-8'))
    value['inputs']['press_source_sha256']['press_delta_scored.csv'] = '0' * 64
    path.write_text(json.dumps(value), encoding='utf-8')
    run['collection_intake']['sha256'] = file_sha256(path)
    assert any('bindings differ' in error for error in press_source_errors(feed, root, current))


def test_cumulative_press_rejects_rolling_intake_mismatch(press):
    root, feed, current = press
    current['inputs']['press_retry_receipt_sha256'] = '0' * 64
    assert any('current accepted intake' in error for error in press_source_errors(feed, root, current))


@pytest.mark.parametrize('field', ['press_retry_receipt_sha256', 'press_source_sha256'])
def test_cumulative_press_rejects_conflicting_archived_binding(press, field):
    root, feed, current = press
    first = Path(feed['accepted_runs'][0]['collection_intake']['path'])
    document = json.loads(first.read_text(encoding='utf-8'))
    if field == 'press_retry_receipt_sha256':
        document['inputs'][field] = '0' * 64
    else:
        document['inputs'][field]['press_delta_scored.csv'] = '0' * 64
    extra = first.with_name('press_refresh_conflicting.json')
    extra.write_text(json.dumps(document), encoding='utf-8')
    assert any('archived acceptance/source bindings differ' in error
               for error in press_source_errors(feed, root, current))


@pytest.mark.parametrize('shape', ['wrong-schema', 'list-document', 'null-document',
                                  'list-inputs', 'string-inputs', 'missing-root'])
def test_cumulative_press_rejects_malformed_archived_receipt(press, shape):
    root, feed, current = press
    first = Path(feed['accepted_runs'][0]['collection_intake']['path'])
    document = json.loads(first.read_text(encoding='utf-8'))
    if shape == 'wrong-schema':
        document['schema'] = 'unrelated-receipt-v1'
    elif shape == 'list-document':
        document = []
    elif shape == 'null-document':
        document = None
    elif shape == 'list-inputs':
        document['inputs'] = []
    elif shape == 'string-inputs':
        document['inputs'] = 'invalid'
    else:
        document['inputs'].pop('press_delta_root')
    extra = first.with_name('press_refresh_malformed.json')
    extra.write_text(json.dumps(document), encoding='utf-8')
    assert any('invalid archived acceptance receipt' in error
               for error in press_source_errors(feed, root, current))


def test_cumulative_press_accepts_duplicate_matching_archive_binding(press):
    root, feed, current = press
    first = Path(feed['accepted_runs'][0]['collection_intake']['path'])
    document = json.loads(first.read_text(encoding='utf-8'))
    document['generated_at'] = '2026-09-03T00:00:00Z'
    extra = first.with_name('press_refresh_matching.json')
    extra.write_text(json.dumps(document), encoding='utf-8')
    assert not press_source_errors(feed, root, current)


@pytest.mark.parametrize('shape', ['list', 'null', 'inputs-list', 'root-number', 'root-blank'])
def test_cumulative_press_rejects_malformed_bound_acceptance_receipt(press, shape):
    root, feed, current = press
    run = feed['accepted_runs'][0]
    path = Path(run['collection_intake']['path'])
    document = json.loads(path.read_text(encoding='utf-8'))
    if shape == 'list': document = []
    elif shape == 'null': document = None
    elif shape == 'inputs-list': document['inputs'] = ['bad']
    elif shape == 'root-number': document['inputs']['press_delta_root'] = 7
    else: document['inputs']['press_delta_root'] = ' '
    path.write_text(json.dumps(document), encoding='utf-8')
    run['collection_intake']['sha256'] = file_sha256(path)
    assert any('invalid acceptance receipt' in value for value in press_source_errors(feed, root, current))


@pytest.mark.parametrize('shape', ['feed-list', 'feed-null', 'runs-dict', 'run-null',
                                  'counts-list', 'contract-list', 'run-path-number',
                                  'hashes-list', 'binding-list', 'binding-path-number'])
def test_cumulative_press_rejects_malformed_ledger_shapes(press, shape):
    root, feed, current = press
    if shape == 'feed-list': feed = []
    elif shape == 'feed-null': feed = None
    elif shape == 'runs-dict': feed['accepted_runs'] = {'bad': 'run'}
    elif shape == 'run-null': feed['accepted_runs'][0] = None
    elif shape == 'counts-list': feed['counts'] = ['bad']
    elif shape == 'contract-list': feed['contract'] = ['bad']
    elif shape == 'run-path-number': feed['accepted_runs'][0]['source_run'] = 7
    elif shape == 'hashes-list': feed['accepted_runs'][0]['source_sha256'] = ['bad']
    elif shape == 'binding-list': feed['accepted_runs'][0]['collection_intake'] = ['bad']
    else: feed['accepted_runs'][0]['collection_intake']['path'] = 7
    assert press_source_errors(feed, root, current)


@pytest.mark.parametrize('shape', ['list', 'null', 'inputs-list', 'root-number', 'root-blank'])
def test_cumulative_press_rejects_malformed_current_intake(press, shape):
    root, feed, current = press
    if shape == 'list': current = []
    elif shape == 'null': current = None
    elif shape == 'inputs-list': current['inputs'] = ['bad']
    elif shape == 'root-number': current['inputs']['press_delta_root'] = 7
    else: current['inputs']['press_delta_root'] = ' '
    assert any('current accepted intake' in value for value in press_source_errors(feed, root, current))


@pytest.mark.parametrize('location', ['source', 'receipt', 'current', 'archive', 'binding'])
def test_cumulative_press_rejects_embedded_nul_paths(press, location):
    root, feed, current = press
    broken = 'invalid\x00path'
    if location == 'source': feed['accepted_runs'][0]['source_run'] = broken
    elif location == 'current': current['inputs']['press_delta_root'] = broken
    elif location == 'binding': feed['accepted_runs'][0]['collection_intake']['path'] = broken
    else:
        binding = feed['accepted_runs'][0]['collection_intake']
        path = Path(binding['path'])
        document = json.loads(path.read_text(encoding='utf-8'))
        document['inputs']['press_delta_root'] = broken
        if location == 'archive': path = path.with_name('press_refresh_invalid_path.json')
        path.write_text(json.dumps(document), encoding='utf-8')
        if location == 'receipt': binding['sha256'] = file_sha256(path)
    assert press_source_errors(feed, root, current)


def test_vote_audit_requires_fresh_source_dates_and_exact_bytes(tmp_path):
    from datetime import datetime, timezone
    (tmp_path/'data/legislation').mkdir(parents=True)
    (tmp_path/'votes').mkdir()
    archive=tmp_path/'congress_bills_archive_current.xlsx'
    archive.write_bytes(b'archive input')
    source=tmp_path/'source'; source.mkdir()
    config={'source_dir':str(source),'historical_exclusions':str(tmp_path/'excluded.csv'),'names':str(tmp_path/'names.json')}
    (tmp_path/'data/legislation/vote_source_snapshot.json').write_text(json.dumps(config),encoding='utf-8')
    inputs=[archive,source/'receipt.json',Path(config['historical_exclusions']),Path(config['names']),
            tmp_path/'data/legislation/cfpb_bill_directions.csv',tmp_path/'legislation_exclusion_ledger.ndjson',
            tmp_path/'legislation_hold_ledger.ndjson',tmp_path/'fec/legislation/vote_by_category.py',tmp_path/'fec/legislation/build_vote_category.py']
    inputs.extend(source/f'HS{c}_{kind}.csv' for c in (117,118,119) for kind in ('members','rollcalls','votes'))
    for path in inputs:
        path.parent.mkdir(parents=True,exist_ok=True); path.write_text('bound input',encoding='utf-8')
    today=datetime.now(timezone.utc).date().isoformat()
    (source/'HS119_rollcalls.csv').write_text(f'chamber,date\nHouse,{today}\nSenate,{today}\n',encoding='utf-8')
    outputs=[tmp_path/'vote_category_lean.json',tmp_path/'votes/cu_rollcall_votes_clean.csv']
    for path in outputs: path.write_text('verified output',encoding='utf-8')
    receipt={'schema':'vote-category-refresh-v1','status':'PASS','archive':str(archive.resolve()),
        'inputs_sha256':{str(p.resolve()):file_sha256(p) for p in inputs},
        'outputs_sha256':{p.name:file_sha256(p) for p in outputs},
        'latest_source_vote_dates':{c:datetime.now(timezone.utc).date().isoformat() for c in ('House','Senate')}}
    path=tmp_path/'data/legislation/vote_refresh_receipt.json'
    path.write_text(json.dumps(receipt),encoding='utf-8')
    assert not vote_source_errors(tmp_path)
    from legislation import sentinel_admissions as SA
    admitted=tmp_path/SA.RELATIVE;admitted.parent.mkdir(parents=True,exist_ok=True)
    admitted.write_text('automatic admission input',encoding='utf-8')
    assert 'incomplete vote input hash coverage' in vote_source_errors(tmp_path)
    receipt['inputs_sha256'][str(admitted.resolve())]=file_sha256(admitted)
    path.write_text(json.dumps(receipt),encoding='utf-8')
    assert not vote_source_errors(tmp_path)
    admitted.write_text('changed admission',encoding='utf-8')
    assert any('input bytes differ' in error for error in vote_source_errors(tmp_path))
    admitted.write_text('automatic admission input',encoding='utf-8')
    receipt['latest_source_vote_dates']['Senate']='2020-01-01'
    path.write_text(json.dumps(receipt),encoding='utf-8')
    assert any('coverage dates differ' in error for error in vote_source_errors(tmp_path))
    (source/'HS119_rollcalls.csv').write_text('chamber,date\nHouse,2020-01-01\nSenate,2020-01-01\n',encoding='utf-8')
    assert any('stale' in error for error in vote_source_errors(tmp_path))
    outputs[0].write_text('fresh timestamp but wrong values',encoding='utf-8')
    assert any('output bytes differ' in error for error in vote_source_errors(tmp_path))
    archive.write_bytes(b'changed bill archive')
    assert any('input bytes differ' in error for error in vote_source_errors(tmp_path))
