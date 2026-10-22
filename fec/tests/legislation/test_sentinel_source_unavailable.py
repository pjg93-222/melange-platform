"""Sentinel source-outage rule (2026-10-11): a House Code outage carries an admitted verdict, never a new one."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import http.client
import io
import json
from pathlib import Path
import re
import sys
import urllib.error
from types import SimpleNamespace
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

# Offline GovInfo USCODE (D-259). The page is the recorded www.govinfo.gov granule for 12 USC 1786a; govinfo_page() re-heads it
# for another title/section so every fixture keeps the real shape (title tag, comment block, section-head h3, note markup).
REAL_GOVINFO_STATUTE = R.govinfo_statute  # captured at import, before the offline fixture patches it
RECORDED_GOVINFO = (Path(__file__).parent / 'fixtures/govinfo_uscode_12_1786a.htm').read_text(encoding='utf-8')
GOVINFO_LINK = ('https://www.govinfo.gov/link/uscode/12/1757?type=usc&year=mostrecent&link-type=html')
GOVINFO_URL = ('https://www.govinfo.gov/content/pkg/USCODE-2024-title12/html/USCODE-2024-title12-chap14-subchapI-sec1757.htm')
GOVINFO_BODY = 'A Federal credit union shall have power to make loans to its members.'


def govinfo_page(title='12', section='1757', body=GOVINFO_BODY, heading='Powers'):
    page = RECORDED_GOVINFO.replace('Title 12', f'Title {title}').replace('12 U.S.C.', f'{title} U.S.C.')
    page = page.replace('&sect;1786a. Omitted', f'&sect;{section}. {heading}')
    page = re.sub(r'<p class="note-body">.*?</p>', f'<p class="statutory-body">{body}</p>', page, flags=re.S)
    return page.encode()


def link_service(monkeypatch, routes):
    """Replace the no-redirect opener. routes maps 'title/section' to a Location URL (302), an int (HTTP status) or an
    Exception; the returned list records every link requested."""
    calls = []

    def open_(request, timeout=None):
        calls.append(request.full_url)
        outcome = routes[request.full_url.split('/link/uscode/')[1].split('?')[0]]
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, int):
            raise urllib.error.HTTPError(request.full_url, outcome, 'status', {}, io.BytesIO(b''))
        raise urllib.error.HTTPError(request.full_url, 302, 'Found', {'Location': outcome}, io.BytesIO(b''))

    monkeypatch.setattr(R, '_LINK_OPENER', SimpleNamespace(open=open_))
    return calls


class Page(io.BytesIO):
    url = 'https://uscode.house.gov/view.xhtml'


def evidence(directory, url, body, **extra):
    path = directory / (hashlib.sha256(body).hexdigest() + '.bin')
    path.write_bytes(body)
    return {'url': url, 'path': str(path.resolve()), 'sha256': hashlib.sha256(body).hexdigest(), **extra}


def world(tmp_path, monkeypatch, *, admitted, age_days=11, bill_now=BILL, statute=MAINTENANCE, govinfo=None):
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
    monkeypatch.setattr(R.GF, 'network_fetcher', lambda timeout: lambda url, cap: (
        bill_now if url == BILL_URL else govinfo if url == GOVINFO_URL and govinfo is not None else b'<billstatus/>'))
    if govinfo is not None:  # `govinfo` is the GovInfo page served for 12 USC 1757; omitted, the fallback stays offline
        monkeypatch.setattr(R, 'govinfo_statute', REAL_GOVINFO_STATUTE)
        link_service(monkeypatch, {'12/1757': GOVINFO_URL})
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
               urllib.error.HTTPError('https://uscode.house.gov/view.xhtml', 500, 'error', {}, None),
               urllib.error.HTTPError('https://uscode.house.gov/view.xhtml', 403, 'forbidden', {}, None),
               TimeoutError('timed out'), http.client.RemoteDisconnected('Remote end closed connection')]


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
    # The receipt says why the statute source failed for a carried bill too, though it is not a deferral.
    assert generation['source_unavailable_reasons'][KEY].startswith('SourceUnavailable: official statute service unavailable')
    assert KEY not in generation['source_unavailable_deferred_keys']
    loaded = SA.load(root)  # consumer accepts the carry inside the window
    assert KEY in loaded['bills']


def test_admitted_bill_with_changed_text_fails(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True, bill_now=BILL + b' Amended.')
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert 'not carried forward' in generation['errors'][KEY] and 'bill text changed' in generation['errors'][KEY]


def test_carry_older_than_thirty_days_is_dropped_and_deferred(tmp_path, monkeypatch):
    # D-260: the 30-day limit used to FAIL the whole refresh, which froze every other measure with it. It now drops only
    # the bill: the verdict stays on record (flagged), the bill leaves the scored set and is retried like a deferred key.
    root, checked = world(tmp_path, monkeypatch, admitted=True, age_days=31)
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['errors'] == {}
    assert KEY not in generation['bills'] and KEY in generation['bill_catalog']   # the catalog keeps it: membership_eligible is False
    assert not [m for m in generation['memberships'] if m['repository_key'] == KEY]
    decision = generation['decisions'][KEY]
    assert decision['carry_expired'] == 'source_unavailable' and decision['carry_expired_at'] == generation['checked_at']
    assert decision['checked_at'] == checked                                      # never a forged verification date
    assert KEY in generation['source_unavailable_deferred_keys'] and KEY in generation['deferred_keys']
    assert generation['carry_expired'][KEY] == {'last_verified_at': checked, 'dropped_at': generation['checked_at'],
                                                'reason': generation['source_unavailable_reasons'][KEY]}
    assert generation['carry_expired_keys'] == [KEY]
    assert KEY not in SA.load(root)['bills']                                      # a consumer accepts the generation


def write_json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


def main_again(root, *extra):
    """One more main() on the same root. The audits dir is keyed by the second, so a finished run is renamed first."""
    audits = root / 'audits/sentinel_admissions'
    for done in audits.glob('2*'):
        done.rename(audits / f"done-{len(list(audits.glob('done-*')))}")
    assert R.main(['--root', str(root), '--model', 'fixture', '--transport', 'api', *extra]) == 0
    run_dir = next((root / 'audits/sentinel_admissions').glob('2*'))
    return json.loads((run_dir / 'generation.json').read_text(encoding='utf-8'))


def test_expired_carry_rejoins_pool_until_reverified(tmp_path, monkeypatch):
    root, checked = world(tmp_path, monkeypatch, admitted=True, age_days=31)
    write_json_file(root / 'bill_repository_live.json', {'bills': {KEY: {}}})    # admitted bills are tracked: discovery skips them
    first = main_again(root)
    assert first['carry_expired_keys'] == [KEY] and KEY not in first['bills']
    # Still down: the dropped key is picked again (it is a cached include), stays deferred and keeps its flag.
    second = main_again(root)
    assert second['processed'] == 1 and second['source_unavailable_deferred_keys'] == [KEY]
    assert second['carry_expired'] == {} and second['carry_expired_keys'] == [KEY]
    assert second['decisions'][KEY]['carry_expired'] == 'source_unavailable' and KEY not in second['bills']
    # House Code is back: a fresh verification replaces the flagged verdict (a cache hit hands back the prior fields).
    classify = Mock(return_value=({**second['decisions'][KEY], 'rationale': 'Re-verified.'}, {'transport': 'api', 'model': 'fixture'}))
    monkeypatch.setattr(R, 'classify', classify)
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(STATUTE)))
    third = main_again(root)
    decision = third['decisions'][KEY]
    classify.assert_called_once()
    assert KEY in third['bills'] and third['carry_expired_keys'] == []
    assert not {'carried_forward', 'carried_at', 'last_verified_at', 'carry_expired', 'carry_expired_at'} & set(decision)
    assert decision['checked_at'] == third['checked_at'] != checked


@pytest.mark.parametrize('days, dropped', [(45, True), (31, True), (29, False)])
def test_no_llm_does_not_keep_a_source_outage_carry_past_thirty_days(tmp_path, monkeypatch, days, dropped):
    # The source is back but --no-llm cannot re-verify, and the evidence moved, so the published carry is "kept". The 30-day
    # limit still binds that carry (D-260): past it the bill leaves the scored set exactly as on the outage path.
    root, checked = world(tmp_path, monkeypatch, admitted=True, age_days=days, statute=STATUTE)
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY].update(carried_forward='source_unavailable',
                                      carried_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    write_json_file(path, document)
    generation = main_again(root, '--no-llm')
    assert generation['status'] == 'PASS' and generation['errors'] == {}
    if dropped:
        assert KEY not in generation['bills'] and KEY in generation['bill_catalog']
        assert generation['decisions'][KEY]['carry_expired'] == 'source_unavailable'
        assert generation['carry_expired_keys'] == [KEY] and list(generation['carry_expired']) == [KEY]
        assert KEY in generation['source_unavailable_deferred_keys'] and generation['no_llm_deferred_keys'] == []
    else:
        assert KEY in generation['bills'] and generation['decisions'][KEY]['carried_forward_no_llm'] is True
        assert generation['carry_expired_keys'] == []
    assert (KEY in SA.load(root)['bills']) is not dropped        # a consumer agrees with the refresh either way


def test_no_llm_never_carries_a_dropped_verdict_back_into_the_scored_set(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=31)
    write_json_file(root / 'bill_repository_live.json', {'bills': {KEY: {}}})
    assert main_again(root)['carry_expired_keys'] == [KEY]
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(STATUTE)))
    again = main_again(root, '--no-llm')           # evidence moved and no model call is allowed: the old verdict must not return
    assert KEY not in again['bills'] and again['no_llm_deferred_keys'] == [KEY]
    assert again['decisions'][KEY]['carry_expired'] == 'source_unavailable'


@pytest.mark.parametrize('statute', UNAVAILABLE)
def test_new_bill_is_never_carried_or_held_on_an_outage(tmp_path, monkeypatch, statute):
    root, _ = world(tmp_path, monkeypatch, admitted=False, statute=statute)
    code, generation, _ = run(root)
    assert generation['status'] == 'PASS' and code == 0
    assert KEY not in generation['bills'] and KEY not in generation['decisions']
    assert generation['source_unavailable_deferred_keys'] == [KEY]


def aged_generation(tmp_path, monkeypatch, days):
    """An installed generation whose carried verdict was last verified `days` ago."""
    root, _ = world(tmp_path, monkeypatch, admitted=True)
    assert run(root)[0] == 0
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY]['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    bill = document['bills'][KEY]
    bill['sponsor'] = {'bioguide': 'S000001'}          # a resolved sponsor, so the generation carries a membership row
    document['unresolved_members'] = [{'key': KEY, 'bioguide': 'O000002', 'role': 'original'}]
    bill['original_cosponsors'] = [{'bioguide': 'O000002'}]
    document['memberships'] = [{'repository_key': KEY, 'bioguide': 'S000001', 'role': 'sponsor', 'bill': bill['display'],
                                'congress': str(bill['congress']), 'areas': bill['areas'], 'withdrawn': False, 'withdrawn_date': ''}]
    path.write_text(json.dumps(document), encoding='utf-8')
    return root


def test_load_drops_a_carry_past_thirty_days_instead_of_refusing(tmp_path, monkeypatch):
    # D-260: a verdict is never served past 30 days, and a generation is never refused for it.
    root = aged_generation(tmp_path, monkeypatch, 31)
    loaded = SA.load(root)
    assert KEY not in loaded['bills'] and loaded['carry_expired_keys'] == [KEY]
    assert not [m for m in loaded['memberships'] if m['repository_key'] == KEY]
    assert not [u for u in loaded.get('unresolved_members', []) if u['key'] == KEY]
    assert KEY in loaded['bill_catalog'] and KEY in loaded['decisions']
    assert SA.membership_eligible({'repository_key': KEY, 'bioguide': 'X000001', 'role': 'sponsor'}, loaded) is False
    assert loaded['freshness_status'] == 'STALE_LABELLED'
    assert KEY in json.loads((root / SA.RELATIVE).read_text(encoding='utf-8'))['bills']   # dropped in memory only


def test_load_names_the_dropped_keys_and_raises_the_freshness_alert(tmp_path, monkeypatch, capsys):
    root = aged_generation(tmp_path, monkeypatch, 31)
    write_json_file(root / 'data/engine/source_freshness.json', {})
    SA.load(root)
    out = capsys.readouterr().out
    assert '!!' in out and KEY in out and 'D-260' in out
    alert = json.loads((root / 'data/engine/source_freshness.json').read_text(encoding='utf-8'))['sentinel_admissions_carry_expired']
    assert alert['status'] == 'STALE_LABELLED'


def test_load_alerts_while_the_stored_generation_still_lists_expired_carries(tmp_path, monkeypatch, capsys):
    # The refresh already dropped the bill, so load() has nothing left to drop. The alert must not go quiet because of that:
    # it stays up for as long as the generation carries a backlog of expired verdicts.
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=31)
    assert run(root)[1]['carry_expired_keys'] == [KEY]
    write_json_file(root / 'data/engine/source_freshness.json', {})
    capsys.readouterr()
    loaded = SA.load(root)
    out = capsys.readouterr().out
    assert KEY not in loaded['bills'] and loaded['freshness_status'] == 'STALE_LABELLED'
    assert '!!' in out and KEY in out and 'D-260' in out
    alert = json.loads((root / 'data/engine/source_freshness.json').read_text(encoding='utf-8'))['sentinel_admissions_carry_expired']
    assert alert['status'] == 'STALE_LABELLED'


def test_load_clears_the_alert_label_when_nothing_is_expired(tmp_path, monkeypatch):
    root = aged_generation(tmp_path, monkeypatch, 10)
    write_json_file(root / 'data/engine/source_freshness.json', {'sentinel_admissions_carry_expired': {
        'status': 'STALE_LABELLED', 'checked_at': '2026-09-01T00:00:00+00:00', 'window_days': 30, 'producer': 'old'}})
    SA.load(root)
    alert = json.loads((root / 'data/engine/source_freshness.json').read_text(encoding='utf-8'))['sentinel_admissions_carry_expired']
    assert alert['status'] == 'FRESH' and alert['window_days'] == 30


def test_load_reads_the_clock_once(tmp_path, monkeypatch):
    # One `now` serves the generation's age, the 30-day drop and every individual check, so they cannot disagree inside a load.
    root = aged_generation(tmp_path, monkeypatch, 10)
    real = SA.datetime

    class Clock(real):
        reads = 0

        @classmethod
        def now(cls, tz=None):
            Clock.reads += 1
            return real.now(tz)

    monkeypatch.setattr(SA, 'datetime', Clock)
    SA.load(root)
    assert Clock.reads == 1


def test_load_keeps_a_carry_inside_thirty_days(tmp_path, monkeypatch):
    loaded = SA.load(aged_generation(tmp_path, monkeypatch, 29))
    assert KEY in loaded['bills'] and not loaded.get('carry_expired_keys') and 'freshness_status' not in loaded
    assert [m['bioguide'] for m in loaded['memberships']] == ['S000001'] and len(loaded['unresolved_members']) == 1


def test_load_still_refuses_a_carry_stamped_in_the_future(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='stamped in the future'):
        SA.load(aged_generation(tmp_path, monkeypatch, -2))


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
