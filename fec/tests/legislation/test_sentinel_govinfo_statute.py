"""GovInfo USCODE fallback for the statute evidence (D-259): when the House Code service is down, a cited section is read from
GovInfo's most recent USCODE edition, resolved through the keyless link service, instead of deferring every bill that cites one.

No test touches the network. The House Code page is a Mock urlopen, the link service a fake no-redirect opener, and the GovInfo
content fetch a stub GF.network_fetcher serving pages derived from the recorded www.govinfo.gov granule (fixtures/).
"""
from __future__ import annotations

import hashlib
import http.client
import io
import json
from pathlib import Path
import re
import sys
import urllib.error
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_sentinel_source_unavailable import (  # noqa: E402  (sibling helpers, as test_press_backlog does)
    BILL, BILL_URL, GOVINFO_BODY, GOVINFO_LINK, GOVINFO_URL, KEY, MAINTENANCE, REAL_GOVINFO_STATUTE, SA, SNAPSHOT,
    STATUS_URL, TEMPORARY_FORMAT, Page, R, govinfo_page, link_service, main_again, run, world)

STAMP = '2026-10-20T00:00:00+00:00'
BILL3 = b'Sections amend 12 U.S.C. 1757, 12 U.S.C. 1786a and 15 U.S.C. 1691 for federal credit unions.'
LINK = 'https://www.govinfo.gov/link/uscode/{}/{}?type=usc&year=mostrecent&link-type=html'


def content_url(title, section, chain='chap14-subchapI'):
    return (f'https://www.govinfo.gov/content/pkg/USCODE-2024-title{title}/html/'
            f'USCODE-2024-title{title}-{chain}-sec{section}.htm')


def collect_harness(monkeypatch, *, bill=BILL, house=TEMPORARY_FORMAT, pages=None):
    """Stub everything collect() reads and bring the real govinfo_statute back. Returns (urlopen mock, fetched urls)."""
    pages, fetched = pages or {}, []

    def fetcher(timeout):
        def fetch(url, cap):
            fetched.append(url)
            if url == BILL_URL:
                return bill
            if url == STATUS_URL:
                return b'<billstatus/>'
            if url in pages:
                return pages[url]
            raise R.GF.FallbackError('official fetch failed: ' + url)
        return fetch

    monkeypatch.setattr(R.time, 'sleep', Mock())
    monkeypatch.setattr(R.GF, 'network_fetcher', fetcher)
    monkeypatch.setattr(R.BS, 'parse_billstatus_xml', lambda *a, **k: {'canonical_key': KEY})
    monkeypatch.setattr(R.GF, '_bill_text_urls', lambda parsed: ([BILL_URL], None))
    monkeypatch.setattr(R.GF, '_parse_bill_text', lambda raw, **k: raw.decode())
    monkeypatch.setattr(R.GF, '_select_title', lambda parsed: 'Fixture')
    monkeypatch.setattr(R, '_govinfo_snapshot', lambda *a, **k: dict(SNAPSHOT))
    monkeypatch.setattr(R, 'govinfo_statute', REAL_GOVINFO_STATUTE)
    opened = Mock(side_effect=house if isinstance(house, Exception) else lambda *a, **k: Page(house))
    monkeypatch.setattr(R.urllib.request, 'urlopen', opened)
    return opened, fetched


def collect_bill(tmp_path):
    return R.collect(KEY, tmp_path / 'evidence', STAMP)


# ---- the fallback itself ----------------------------------------------------------------------------------------

def test_house_outage_falls_back_to_govinfo_with_provenance(tmp_path, monkeypatch):
    page = govinfo_page()
    link_calls = link_service(monkeypatch, {'12/1757': GOVINFO_URL})
    collect_harness(monkeypatch, pages={GOVINFO_URL: page})
    snapshot, texts, sources = collect_bill(tmp_path)
    assert GOVINFO_BODY in texts[GOVINFO_URL] and not any('uscode.house.gov' in url for url in texts)
    record = sources[-1]
    assert (record['url'], record['kind'], record['title'], record['section']) == (GOVINFO_URL, 'statute', '12', '1757')
    assert (record['statute_source'], record['edition'], record['resolved_from']) == ('govinfo_uscode', 'USCODE-2024', GOVINFO_LINK)
    assert record['house_unavailable'] is True
    stored = Path(record['path'])
    assert stored.name == record['sha256'] + '.html' == hashlib.sha256(page).hexdigest() + '.html'
    assert stored.read_bytes() == page and link_calls == [GOVINFO_LINK]
    assert R._STATUTE_RUN['counts'] == {'house_code': 0, 'govinfo_uscode': 1}
    assert R._STATUTE_RUN['house_down'].startswith('12 USC 1757: ') and 'temporary format' in R._STATUTE_RUN['house_down']


def test_a_working_house_code_never_touches_govinfo(tmp_path, monkeypatch):
    house = b'<html><title>12 USC 1757: Powers</title><body>A federal credit union shall have power to lend.</body></html>'
    collect_harness(monkeypatch, house=house)
    monkeypatch.setattr(R, 'govinfo_statute', Mock(side_effect=AssertionError('House Code is up')))
    _, texts, sources = collect_bill(tmp_path)
    assert 'shall have power to lend' in next(t for url, t in texts.items() if 'uscode.house.gov' in url)
    assert 'statute_source' not in sources[-1]
    assert R._STATUTE_RUN == {'house_down': None, 'integrity_failures': 0, 'counts': {'house_code': 1, 'govinfo_uscode': 0}}


@pytest.mark.parametrize('location', [
    content_url('12', '1758'),                                                  # another section
    content_url('12', '17570'),                                                 # a longer section sharing the prefix
    content_url('15', '1757', 'chap41-subchapI'),                               # another title
    content_url('12', '1757').replace('https://www.govinfo.gov', 'https://evil.example'),
    content_url('12', '1757').replace('https://', 'http://'),
    content_url('12', '1757') + '?api_key=secret',
    'https://www.govinfo.gov/content/pkg/USCODE-2023-title12/html/USCODE-2024-title12-chap14-sec1757.htm',   # edition drift
], ids=['other-section', 'prefix-section', 'other-title', 'off-host', 'http', 'query', 'edition-drift'])
def test_govinfo_redirect_must_name_requested_title_and_section(monkeypatch, location):
    link_service(monkeypatch, {'12/1757': location})
    _, fetched = collect_harness(monkeypatch)
    with pytest.raises(R.SourceUnavailable, match=r'^govinfo_integrity: .*requested 12 USC 1757'):
        R.govinfo_statute('12', '1757')
    assert fetched == []   # a document the link service did not vouch for is never fetched
    assert R._STATUTE_RUN['integrity_failures'] == 1


def test_section_letters_are_lowercased_for_the_link_service(monkeypatch):
    url = content_url('15', '78c', 'chap2B-subchapI')
    calls = link_service(monkeypatch, {'15/78c': url})     # an upper-case 78C would be a KeyError here
    collect_harness(monkeypatch, pages={url: govinfo_page('15', '78c', heading='Definitions')})
    got_url, raw, text, provenance = R.govinfo_statute('15', '78C')
    assert calls == [LINK.format('15', '78c')] and got_url == url and provenance['resolved_from'] == calls[0]
    assert GOVINFO_BODY in text and provenance == {'statute_source': 'govinfo_uscode', 'edition': 'USCODE-2024',
                                                   'resolved_from': calls[0]}


def test_dashed_sections_resolve_and_match_either_dash_form(monkeypatch):
    url = content_url('42', '300g-6', 'chap6A-subchapXII-partB')
    link_service(monkeypatch, {'42/300g-6': url})
    for heading in ('&sect;300g-6.', '&sect;300g&ndash;6.'):
        page = govinfo_page('42', '300g-6').replace(b'&sect;300g-6.', heading.encode())
        collect_harness(monkeypatch, pages={url: page})
        assert R.govinfo_statute('42', '300g-6')[0] == url


@pytest.mark.parametrize('page', [
    pytest.param(govinfo_page(section='1758'), id='another-section-heading'),
    pytest.param(govinfo_page(title='15'), id='another-title'),
    pytest.param(govinfo_page().replace(b'<h3 class="section-head">', b'<h3 class="other">'), id='no-section-heading'),
    pytest.param(b'<html><head><title>Govinfo Link Service Error</title></head><body>Error</body></html>', id='error-page'),
])
def test_govinfo_page_heading_identity_required(page):
    with pytest.raises(R.StatuteIdentityMismatch, match='requested 12 USC 1757'):
        R.govinfo_statute_text(page, '12', '1757')
    assert GOVINFO_BODY in R.govinfo_statute_text(govinfo_page(), '12', '1757')


@pytest.mark.parametrize('page', [govinfo_page(section='1758'), govinfo_page(title='15'), b'\xff\xfe not utf-8'],
                         ids=['another-section', 'another-title', 'undecodable'])
def test_a_wrong_govinfo_document_is_an_integrity_failure_not_an_identity_error(monkeypatch, page):
    link_service(monkeypatch, {'12/1757': GOVINFO_URL})
    collect_harness(monkeypatch, pages={GOVINFO_URL: page})
    with pytest.raises(R.SourceUnavailable, match=r'^govinfo_integrity: ') as raised:
        R.govinfo_statute('12', '1757')
    assert not isinstance(raised.value, R.StatuteIdentityMismatch) and R._STATUTE_RUN['integrity_failures'] == 1


# ---- failures defer with both reasons ----------------------------------------------------------------------------

def test_govinfo_400_defers_with_both_reasons(tmp_path, monkeypatch):
    link_service(monkeypatch, {'12/1757': 400})
    collect_harness(monkeypatch)
    with pytest.raises(R.SourceUnavailable) as raised:
        collect_bill(tmp_path)
    message = str(raised.value)
    assert message.startswith('official statute service unavailable for 12 USC 1757: ') and 'temporary format' in message
    assert 'GovInfo USCODE fallback: GovInfo USCODE most-recent edition has no 12 USC 1757 (HTTP 400)' in message
    assert raised.value.snapshot['key'] == KEY and BILL_URL in raised.value.texts


@pytest.mark.parametrize('route, tries, reason', [
    (404, 1, 'has no 12 USC 1757 (HTTP 404)'),
    (503, 3, 'link service HTTP 503'),
    (TimeoutError('timed out'), 3, 'link service unreachable'),
    (urllib.error.URLError(ConnectionRefusedError('refused')), 1, 'link service unreachable'),
], ids=['404', '503-retried', 'timeout-retried', 'refused'])
def test_govinfo_link_service_failures_defer(tmp_path, monkeypatch, route, tries, reason):
    calls = link_service(monkeypatch, {'12/1757': route})
    collect_harness(monkeypatch)
    with pytest.raises(R.SourceUnavailable, match='GovInfo USCODE fallback') as raised:
        collect_bill(tmp_path)
    assert reason in str(raised.value) and len(calls) == tries


@pytest.mark.parametrize('status', [401, 405, 414])
def test_a_govinfo_client_error_raises_as_a_house_client_error_does(tmp_path, monkeypatch, status):
    # Our request was wrong, not GovInfo down: no fallback reason to defer on, so it is an error, not an outage.
    link_service(monkeypatch, {'12/1757': status})
    collect_harness(monkeypatch)
    with pytest.raises(urllib.error.HTTPError) as raised:
        collect_bill(tmp_path)
    assert raised.value.code == status and not isinstance(raised.value, R.SourceUnavailable)


def test_the_redirect_response_is_closed_once_its_location_is_read(monkeypatch):
    redirect = urllib.error.HTTPError(GOVINFO_LINK, 302, 'Found', {'Location': GOVINFO_URL}, io.BytesIO(b''))

    def open_(request, timeout=None):
        raise redirect

    monkeypatch.setattr(R, '_LINK_OPENER', type('Opener', (), {'open': staticmethod(open_)}))
    collect_harness(monkeypatch, pages={GOVINFO_URL: govinfo_page()})
    assert R.govinfo_statute('12', '1757')[0] == GOVINFO_URL
    assert redirect.fp.closed


def fallback_error(cause=None):
    error = R.GF.FallbackError('official fetch failed: ' + repr(cause))
    error.__cause__ = cause        # network_fetcher wraps the transport error exactly like this
    return error


@pytest.mark.parametrize('failure, tries, message, integrity', [
    (fallback_error(urllib.error.HTTPError(GOVINFO_URL, 500, 'Internal Server Error', {}, None)), 3,   # a govinfo 5xx retries
     'GovInfo USCODE fetch failed for 12 USC 1757', 0),
    (fallback_error(TimeoutError('timed out')), 3, 'GovInfo USCODE fetch failed for 12 USC 1757', 0),
    # A FallbackError with no transport cause is one of network_fetcher's guards: oversize, final-URL drift, a refused
    # redirect, a non-200 success. That is an integrity failure, counted, and never retried.
    (fallback_error(), 1, r'govinfo_integrity: fetch guard refused 12 USC 1757', 1),
], ids=['govinfo-5xx-retried', 'timeout-retried', 'guard-refusal'])
def test_govinfo_content_fetch_failures_defer(tmp_path, monkeypatch, failure, tries, message, integrity):
    link_service(monkeypatch, {'12/1757': GOVINFO_URL})
    collect_harness(monkeypatch)
    base, attempts = R.GF.network_fetcher, []

    def failing(timeout):
        fetch = base(timeout)

        def serve(url, cap):
            if url != GOVINFO_URL:
                return fetch(url, cap)
            attempts.append(url)
            raise failure
        return serve

    monkeypatch.setattr(R.GF, 'network_fetcher', failing)
    with pytest.raises(R.SourceUnavailable, match=message):
        collect_bill(tmp_path)
    assert len(attempts) == tries and R._STATUTE_RUN['integrity_failures'] == integrity


# ---- the run-level House breaker ----------------------------------------------------------------------------------

def test_breaker_skips_house_after_first_outage_for_the_rest_of_the_run(tmp_path, monkeypatch):
    chains = {('12', '1757'): 'chap14-subchapI', ('12', '1786a'): 'chap14-subchapII', ('15', '1691'): 'chap41-subchapIV'}
    link_service(monkeypatch, {f'{t}/{s}': content_url(t, s, chain) for (t, s), chain in chains.items()})
    opened, _ = collect_harness(monkeypatch, bill=BILL3, pages={
        content_url(t, s, chain): govinfo_page(t, s) for (t, s), chain in chains.items()})
    _, texts, sources = collect_bill(tmp_path)
    assert opened.call_count == 1                        # House was asked once, for 12 USC 1757, then skipped
    statutes = [r for r in sources if r.get('kind') == 'statute']
    assert [r['section'] for r in statutes] == ['1757', '1786a', '1691']
    assert all(r['statute_source'] == 'govinfo_uscode' for r in statutes)
    assert [r['house_unavailable'] for r in statutes] == [True] * 3    # a constant: the reason lives in the receipt, not the record
    assert R._STATUTE_RUN['counts'] == {'house_code': 0, 'govinfo_uscode': 3}
    assert R._STATUTE_RUN['house_down'].startswith('12 USC 1757: ')
    # the breaker holds for the next bill of the same run too: no further House request
    collect_bill(tmp_path)
    assert opened.call_count == 1


BILL2S = b'Sections amend 12 U.S.C. 1757 and 12 U.S.C. 1786a for federal credit unions.'
HOUSE_1786A = b'<html><title>12 USC 1786a: Omitted</title><body>Section omitted.</body></html>'


def http_error(code, body=b''):
    return urllib.error.HTTPError('https://uscode.house.gov/view.xhtml', code, 'status', {}, io.BytesIO(body))


def two_section_run(tmp_path, monkeypatch, first_house_outcome):
    """A bill citing 12 USC 1757 then 12 USC 1786a. House answers `first_house_outcome` once, then serves 1786a."""
    link_service(monkeypatch, {'12/1757': GOVINFO_URL})
    collect_harness(monkeypatch, bill=BILL2S, pages={GOVINFO_URL: govinfo_page()})
    opened = Mock(side_effect=[first_house_outcome, Page(HOUSE_1786A)] + [Page(HOUSE_1786A)] * 3)
    monkeypatch.setattr(R.urllib.request, 'urlopen', opened)
    _, _, sources = collect_bill(tmp_path)
    return opened, [r for r in sources if r.get('kind') == 'statute']


@pytest.mark.parametrize('code', [500, 503, 403, 408])
def test_a_section_level_house_error_falls_back_for_that_section_only(tmp_path, monkeypatch, code):
    # D-259 breaker ruling: only a site-wide signal switches the run to GovInfo. One section's 5xx, 403 or 408 says nothing
    # about the next section, so House is asked again. (Sources switching when House recovers is the accepted one-off churn.)
    opened, statutes = two_section_run(tmp_path, monkeypatch, http_error(code))
    assert opened.call_count == 2 and R._STATUTE_RUN['house_down'] is None
    assert [r.get('statute_source', 'house') for r in statutes] == ['govinfo_uscode', 'house']
    assert statutes[0]['house_unavailable'] is True and 'house_unavailable' not in statutes[1]
    assert R._STATUTE_RUN['counts'] == {'house_code': 1, 'govinfo_uscode': 1}


@pytest.mark.parametrize('outcome', [
    http_error(404, TEMPORARY_FORMAT), http_error(410, TEMPORARY_FORMAT), http_error(429),   # the notice, and a throttle
    Page(TEMPORARY_FORMAT), Page(MAINTENANCE),                                               # the two site pages
    TimeoutError('timed out'), http.client.RemoteDisconnected('closed'),                     # exhausted transient failures
    urllib.error.URLError(ConnectionRefusedError('refused')),
], ids=['404-notice', '410-notice', '429', 'temporary-format', 'maintenance', 'timeout', 'disconnect', 'refused'])
def test_a_site_wide_house_signal_trips_the_breaker_for_the_rest_of_the_run(tmp_path, monkeypatch, outcome):
    link_service(monkeypatch, {'12/1757': GOVINFO_URL, '12/1786a': content_url('12', '1786a', 'chap14-subchapII')})
    collect_harness(monkeypatch, bill=BILL2S, pages={GOVINFO_URL: govinfo_page(),
                                                      content_url('12', '1786a', 'chap14-subchapII'): govinfo_page(section='1786a')})
    side_effect = (lambda *a, **k: outcome) if isinstance(outcome, Page) else outcome
    opened = Mock(side_effect=side_effect)
    monkeypatch.setattr(R.urllib.request, 'urlopen', opened)
    _, _, sources = collect_bill(tmp_path)
    tries = opened.call_count
    assert R._STATUTE_RUN['house_down'].startswith('12 USC 1757: ')
    assert [r['statute_source'] for r in sources if r.get('kind') == 'statute'] == ['govinfo_uscode'] * 2
    assert tries in (1, 3)       # one request, or the three tries of an exhausted transient failure; none for the second section


def test_main_resets_the_breaker_and_reports_statute_sources(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=False)       # House down (maintenance page), GovInfo offline
    R._STATUTE_RUN.update(house_down='left over from an earlier main()', counts={'house_code': 9, 'govinfo_uscode': 9})
    code, generation, _ = run(root)
    assert code == 0 and generation['status'] == 'PASS'
    assert R.urllib.request.urlopen.call_count == 1               # House was tried: the stale breaker did not carry over
    assert generation['statute_house_skipped_after'].startswith('12 USC 1757: ') and 'maintenance' in generation['statute_house_skipped_after']
    assert generation['statute_sources'] == {'house_code': 0, 'govinfo_uscode': 0}
    reason = generation['source_unavailable_reasons'][KEY]
    assert 'official statute service unavailable for 12 USC 1757' in reason and 'GovInfo fixture offline' in reason
    assert generation['source_unavailable_deferred_keys'] == [KEY]


# ---- evidence read back, and an admitted bill re-verified on GovInfo ------------------------------------------------

def include_decision(key=KEY):
    return {'canonical_key': key, 'disposition': 'include', 'confidence': 'high', 'beneficiary_class': 'cu_primary',
            'rationale': 'Expands federal credit union payment powers.', 'policy': R.POLICY,
            'authority': 'automated_official_evidence_policy',
            'categories': [{'category': 'Payments', 'direction': 1, 'source_url': GOVINFO_URL,
                            'quote': 'shall have power to make loans to its members',
                            'rationale': 'The statute gives credit unions the lending power the bill extends.'}]}


def test_bound_decision_texts_and_sa_load_reparse_govinfo_evidence(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=False, govinfo=govinfo_page())
    monkeypatch.setattr(R, 'classify', Mock(return_value=(include_decision(), {'transport': 'api', 'model': 'fixture'})))
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    decision = generation['decisions'][KEY]
    assert [e['statute_source'] for e in decision['evidence'] if e.get('kind') == 'statute'] == ['govinfo_uscode']
    assert GOVINFO_BODY in R.bound_decision_texts(decision)[GOVINFO_URL]    # cached bytes parse by their host
    loaded = SA.load(root)                       # a category cites the statute: load() re-parses it and re-checks the quote
    assert KEY in loaded['bills'] and loaded['decisions'][KEY]['categories'][0]['source_url'] == GOVINFO_URL


def test_admitted_bill_reverified_via_govinfo_is_not_a_carry(tmp_path, monkeypatch):
    root, checked = world(tmp_path, monkeypatch, admitted=True, govinfo=govinfo_page())
    classify = Mock(return_value=(include_decision(), {'transport': 'api', 'model': 'fixture'}))
    monkeypatch.setattr(R, 'classify', classify)
    code, generation, installed = run(root)
    decision = generation['decisions'][KEY]
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    classify.assert_called_once()                # the House-based fingerprint no longer matches: one billed re-verification
    assert decision['checked_at'] == generation['checked_at'] != checked
    assert not {'carried_forward', 'carried_at', 'last_verified_at'} & set(decision)
    assert generation['statute_sources'] == {'house_code': 0, 'govinfo_uscode': 1}
    assert generation['source_unavailable_deferred_keys'] == [] and KEY in generation['bills']
    assert SA.load(root)['bills'][KEY]['areas'] == ['Payments']


# ---- an integrity failure is an outage for the bill, never a failed refresh (D-212) ----------------------------------

WRONG_SECTION = govinfo_page(section='1758')       # the link service named the right granule; its content is another section


def test_a_new_bill_whose_govinfo_document_fails_integrity_is_deferred_and_counted(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=False, govinfo=WRONG_SECTION)
    code, generation, _ = run(root)
    assert (code, generation['status']) == (0, 'PASS') and generation['errors'] == {}
    assert generation['source_unavailable_deferred_keys'] == [KEY] and KEY not in generation['decisions']
    assert 'govinfo_integrity:' in generation['source_unavailable_reasons'][KEY]
    assert generation['statute_integrity_failures'] == 1
    assert generation['statute_sources'] == {'house_code': 0, 'govinfo_uscode': 0}


def test_an_admitted_bill_whose_govinfo_document_fails_integrity_is_carried(tmp_path, monkeypatch):
    root, checked = world(tmp_path, monkeypatch, admitted=True, govinfo=WRONG_SECTION)
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    decision = generation['decisions'][KEY]
    assert decision['carried_forward'] == 'source_unavailable' and decision['checked_at'] == checked
    assert generation['statute_integrity_failures'] == 1 and KEY in generation['bills']


def test_an_admitted_bill_past_the_carry_limit_with_a_failed_govinfo_document_is_dropped(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=31, govinfo=WRONG_SECTION)
    code, generation, _ = run(root)
    assert (code, generation['status']) == (0, 'PASS') and generation['carry_expired_keys'] == [KEY]
    assert KEY not in generation['bills'] and generation['statute_integrity_failures'] == 1


def test_integrity_failures_are_zero_in_a_clean_run(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=False, govinfo=govinfo_page())
    monkeypatch.setattr(R, 'classify', Mock(return_value=(include_decision(), {'transport': 'api', 'model': 'fixture'})))
    assert run(root)[1]['statute_integrity_failures'] == 0


# ---- two bills citing one section: the evidence record must be the same whatever the bill's place in the run ---------

KEY2 = '119:hr:8'
BILL2_URL = BILL_URL.replace('hr7', 'hr8')


def two_bills(tmp_path, monkeypatch, *, bill_one=BILL):
    """KEY (H.R. 7) and KEY2 (H.R. 8) both cite 12 USC 1757; House Code is down (maintenance page), GovInfo serves the section."""
    root, _ = world(tmp_path, monkeypatch, admitted=False, govinfo=govinfo_page())
    records = root / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    records.write_text(''.join(json.dumps({'canonical_key': k}) + '\n' for k in (KEY, KEY2)), encoding='utf-8')
    served = {BILL_URL: bill_one, BILL2_URL: BILL, GOVINFO_URL: govinfo_page()}
    number = lambda text: '119:hr:' + re.search(r'hr(\d+)', text).group(1)
    monkeypatch.setattr(R.GF, 'network_fetcher', lambda timeout: lambda url, cap: served.get(url, b'<billstatus/>'))
    monkeypatch.setattr(R.BS, 'parse_billstatus_xml', lambda path, source_url=None, **k: {'canonical_key': number(source_url)})
    monkeypatch.setattr(R.GF, '_bill_text_urls',
                        lambda parsed: ([BILL_URL if parsed['canonical_key'] == KEY else BILL2_URL], None))
    monkeypatch.setattr(R, '_govinfo_snapshot', lambda bill, parsed, raw, **k: {
        **SNAPSHOT, 'key': parsed['canonical_key'], 'display': 'H.R. ' + parsed['canonical_key'].split(':')[2]})
    monkeypatch.setattr(R, 'classify', Mock(side_effect=lambda key, *a, **k: (
        include_decision(key), {'transport': 'api', 'model': 'fixture'})))
    return root, served


def statute_records(generation):
    return {key: [e for e in generation['decisions'][key]['evidence'] if e.get('kind') == 'statute'] for key in (KEY, KEY2)}


def test_two_bills_citing_one_section_share_one_evidence_record_and_the_generation_loads(tmp_path, monkeypatch):
    # The record used to carry the per-run, per-position House reason. The two bills' records for the same bytes then
    # differed, the {path: record} dedupe kept one, and SA.load refused the generation as "not bound to the generation".
    root, _ = two_bills(tmp_path, monkeypatch)
    generation = main_again(root)
    records = statute_records(generation)
    assert records[KEY] == records[KEY2] and len(records[KEY]) == 1 and records[KEY][0]['house_unavailable'] is True
    assert {KEY, KEY2} <= set(SA.load(root)['bills'])


def test_a_carried_decision_keeps_a_record_that_still_matches_this_runs_new_records(tmp_path, monkeypatch):
    # Run 1 admits both bills while House answers with the temporary-format page. Run 2 (--no-llm, House now answers
    # with the maintenance page, H.R. 7's text moved): H.R. 7 is carried with run 1's record, H.R. 8 is a cache hit
    # with run 2's record. The two records for the shared section must be the same record.
    root, served = two_bills(tmp_path, monkeypatch)
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(TEMPORARY_FORMAT)))
    first = main_again(root)
    assert {KEY, KEY2} <= set(first['bills'])
    served[BILL_URL] = BILL + b' Amended.'
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(MAINTENANCE)))
    second = main_again(root, '--no-llm')
    assert second['decisions'][KEY].get('carried_forward_no_llm') and second['decisions'][KEY2]['classifier'].get('cache_hit')
    records = statute_records(second)
    assert records[KEY] == records[KEY2]
    assert {KEY, KEY2} <= set(SA.load(root)['bills'])


def test_statute_document_text_dispatches_on_the_evidence_host():
    house = b'<html><title>12 USC 1757: Powers</title><body>House body.</body></html>'
    assert R.statute_document_text(house, {'url': 'https://uscode.house.gov/view.xhtml?req=x', 'title': '12', 'section': '1757'}) \
        == '12 USC 1757: Powers House body.'
    assert GOVINFO_BODY in R.statute_document_text(govinfo_page(), {'url': GOVINFO_URL, 'title': '12', 'section': '1757'})
