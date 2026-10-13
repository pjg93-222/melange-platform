"""Sentinel source-outage rule (2026-10-11): a House Code outage carries an admitted verdict, never a new one."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import urllib.error
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R
from legislation import sentinel_admissions as SA

KEY = '119:hr:7'
BILL_URL = 'https://www.govinfo.gov/content/pkg/BILLS-119hr7ih/xml/BILLS-119hr7ih.xml'
STATUS_URL = 'https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/BILLSTATUS-119hr7.xml'
BILL = b'Section 2 amends 12 U.S.C. 1757 so federal credit unions may offer member payment accounts.'
STATUTE = b'<html><title>12 USC 1757: Powers</title><body>A federal credit union shall have power to lend.</body></html>'
MAINTENANCE = (b'<html><title>Under Maintenance</title><body>Site is currently under maintenance '
               b'The site you requested is currently unavailable.</body></html>')
SNAPSHOT = {'key': KEY, 'display': 'H.R. 7', 'congress': 119, 'sponsor': {'bioguide': 'unresolved-fixture'},
            'original_cosponsors': [], 'cosponsors': [], 'areas': ['Payments']}


class Page(io.BytesIO):
    url = 'https://uscode.house.gov/view.xhtml'


def evidence(directory, url, body, **extra):
    path = directory / (hashlib.sha256(body).hexdigest() + '.bin')
    path.write_bytes(body)
    return {'url': url, 'path': str(path.resolve()), 'sha256': hashlib.sha256(body).hexdigest(), **extra}


def world(tmp_path, monkeypatch, *, admitted, age_days=11, bill_now=BILL, statute=MAINTENANCE):
    store = tmp_path / 'evidence'; store.mkdir()
    statute_url = 'https://uscode.house.gov/view.xhtml?req=' + R.urllib.parse.quote('(title:12 section:1757 edition:prelim)')
    sources = [evidence(store, STATUS_URL, b'<billstatus/>'), evidence(store, BILL_URL, BILL),
               evidence(store, statute_url, STATUTE, kind='statute', title='12', section='1757')]
    checked = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    decision = {'canonical_key': KEY, 'disposition': 'include', 'confidence': 'high', 'beneficiary_class': 'cu_primary',
                'rationale': 'Expands federal credit union payment powers.', 'policy': R.POLICY,
                'authority': 'automated_official_evidence_policy', 'checked_at': checked, 'evidence': sources,
                'evidence_fingerprint': 'fixture', 'categories': [{
                    'category': 'Payments', 'direction': 1, 'source_url': BILL_URL,
                    'quote': 'federal credit unions may offer member payment accounts',
                    'rationale': 'New payment powers help credit unions compete for members.'}]}
    previous = {'schema': SA.SCHEMA, 'status': 'PASS', 'checked_at': checked, 'sources': sources,
                'bills': {KEY: SNAPSHOT} if admitted else {}, 'decisions': {KEY: decision} if admitted else {},
                'memberships': []}
    files = {'data/legislation/sentinel_admissions.json': previous, 'member_desk_bundle.json': {'members': []},
             'bill_repository_live.json': {'bills': {}},
             'source_data/legislation/acu_legislation_search_registry.json': {'categories': [{'id': 'Payments'}]}}
    for relative, value in files.items():
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text(json.dumps(value), encoding='utf-8')
    discovery = tmp_path / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    discovery.parent.mkdir(parents=True)
    discovery.write_text(json.dumps({'canonical_key': KEY}) + '\n', encoding='utf-8')

    monkeypatch.setattr(R.time, 'sleep', Mock())
    monkeypatch.setattr(R.GF, 'network_fetcher', lambda timeout: lambda url, cap: bill_now if url == BILL_URL else b'<billstatus/>')
    monkeypatch.setattr(R.BS, 'parse_billstatus_xml', lambda *a, **k: {'canonical_key': KEY})
    monkeypatch.setattr(R.GF, '_bill_text_urls', lambda parsed: ([BILL_URL], None))
    monkeypatch.setattr(R.GF, '_parse_bill_text', lambda raw, **k: raw.decode())
    monkeypatch.setattr(R.GF, '_select_title', lambda parsed: 'Fixture')
    monkeypatch.setattr(R, '_govinfo_snapshot', lambda *a, **k: dict(SNAPSHOT))
    if isinstance(statute, Exception):
        monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=statute))
    else:
        monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(statute)))
    monkeypatch.setattr(R, 'classify', Mock(side_effect=AssertionError('an outage must never reach the classifier')))
    return tmp_path, checked


def run(root):
    published = root / 'data/legislation/sentinel_admissions.json'
    before = published.read_bytes()
    code = R.main(['--root', str(root), '--keys', KEY, '--model', 'fixture', '--transport', 'api'])
    generation = json.loads(next((root / 'audits/sentinel_admissions').glob('*/generation.json')).read_text(encoding='utf-8'))
    return code, generation, published.read_bytes() != before


NOT_FOUND = b'<html><title>Page Not Found - US Code</title><body>Page Not Found The page you requested could not be found.</body></html>'
# 2026-10-12: every section query on the degraded House Code site returns this page.
TEMPORARY_FORMAT = NOT_FOUND.replace(b'<body>', b'<body>Notice: The uscode.house.gov website is currently operating in a '
                                     b'temporary format. Some website features are temporarily unavailable. ')
UNAVAILABLE = [MAINTENANCE, TEMPORARY_FORMAT,
               urllib.error.HTTPError('https://uscode.house.gov/view.xhtml', 500, 'error', {}, None)]


def test_maintenance_page_is_source_unavailable_not_identity_mismatch():
    with pytest.raises(R.SourceUnavailable):
        R.statute_text(MAINTENANCE, '12', '1757')
    assert not issubclass(R.SourceUnavailable, R.StatuteIdentityMismatch)


def test_temporary_format_not_found_is_an_outage_but_a_plain_not_found_is_not():
    with pytest.raises(R.SourceUnavailable):
        R.statute_text(TEMPORARY_FORMAT, '12', '1757')
    with pytest.raises(R.StatuteIdentityMismatch):
        R.statute_text(NOT_FOUND, '12', '1757')


@pytest.mark.parametrize('statute', UNAVAILABLE)
def test_admitted_bill_with_unchanged_text_is_carried_forward(tmp_path, monkeypatch, statute):
    root, checked = world(tmp_path, monkeypatch, admitted=True, statute=statute)
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    decision = generation['decisions'][KEY]
    assert decision['carried_forward'] == 'source_unavailable'
    assert decision['last_verified_at'] == decision['checked_at'] == checked
    assert KEY in generation['bills'] and generation['bills'][KEY]['areas'] == ['Payments']
    loaded = SA.load(root)  # consumer accepts the carry inside the window
    assert KEY in loaded['bills']


def test_admitted_bill_with_changed_text_fails(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True, bill_now=BILL + b' Amended.')
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert 'not carried forward' in generation['errors'][KEY] and 'bill text changed' in generation['errors'][KEY]


def test_carry_older_than_thirty_days_fails(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=31)
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert 'not carried forward' in generation['errors'][KEY] and '30-day' in generation['errors'][KEY]


@pytest.mark.parametrize('statute', UNAVAILABLE)
def test_new_bill_is_never_carried_or_held_on_an_outage(tmp_path, monkeypatch, statute):
    root, _ = world(tmp_path, monkeypatch, admitted=False, statute=statute)
    code, generation, _ = run(root)
    assert generation['status'] == 'PASS' and code == 0
    assert KEY not in generation['bills'] and KEY not in generation['decisions']
    assert generation['source_unavailable_deferred_keys'] == [KEY]


def test_load_refuses_a_carry_past_thirty_days(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True)
    assert run(root)[0] == 0
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY]['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    path.write_text(json.dumps(document), encoding='utf-8')
    with pytest.raises(ValueError, match='past its verification limit'):
        SA.load(root)


def test_govinfo_5xx_is_retried_then_succeeds():
    error = urllib.error.HTTPError(BILL_URL, 500, 'Internal Server Error', {}, None)
    try:
        raise R.GF.FallbackError('official fetch failed') from error
    except R.GF.FallbackError as wrapped:
        operation = Mock(side_effect=[wrapped, b'complete'])
    sleep = Mock()
    original, R.time.sleep = R.time.sleep, sleep
    try:
        assert R._retry_read(operation, source=BILL_URL) == b'complete'
    finally:
        R.time.sleep = original
    assert operation.call_count == 2 and sleep.call_count == 1
