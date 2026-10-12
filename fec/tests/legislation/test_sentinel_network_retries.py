"""Admission transport retries must preserve complete, validated evidence."""
from __future__ import annotations

from collections import Counter
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


KEY = '119:hr:7'
BILL_URL = 'https://www.govinfo.gov/content/pkg/BILLS-119hr7ih/xml/BILLS-119hr7ih.xml'
CODE_URL = 'https://uscode.house.gov/view.xhtml'


class Response(io.BytesIO):
    def __init__(self, body=b'', *, url=CODE_URL, error=None):
        super().__init__(body)
        self.url = url
        self.error = error

    def read(self, *args):
        if self.error:
            raise self.error
        return super().read(*args)


@pytest.fixture(autouse=True)
def no_network_or_delay(monkeypatch):
    monkeypatch.setattr(R.time, 'sleep', Mock())
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=AssertionError('unexpected network')))
    monkeypatch.setattr(R.GF, 'network_fetcher', Mock(side_effect=AssertionError('unexpected network')))


def wrapped_timeout(error=None):
    try:
        raise error or TimeoutError('read timed out')
    except Exception as error:
        try:
            raise R.GF.FallbackError('official fetch failed') from error
        except R.GF.FallbackError as wrapped:
            return wrapped


@pytest.mark.parametrize('error', [
    TimeoutError('read'), urllib.error.URLError(TimeoutError('read')), wrapped_timeout(),
    R.http.client.RemoteDisconnected('Remote end closed connection without response'),
    ConnectionResetError('Connection reset'),
    urllib.error.URLError(ConnectionResetError('Connection reset')),
    wrapped_timeout(R.http.client.RemoteDisconnected('Remote end closed connection without response')),
    R.http.client.IncompleteRead(b'partial official evidence', 64),
    urllib.error.URLError(R.http.client.IncompleteRead(b'partial official evidence', 64)),
    wrapped_timeout(R.http.client.IncompleteRead(b'partial official evidence', 64)),
    wrapped_timeout(urllib.error.URLError(R.http.client.IncompleteRead(b'partial official evidence', 64))),
])
def test_typed_transient_read_retries_twice_then_returns_complete_result(error):
    operation = Mock(side_effect=[error, error, b'complete'])
    assert R._retry_read(operation, source='official fixture') == b'complete'
    assert operation.call_count == 3
    assert [call.args[0] for call in R.time.sleep.call_args_list] == [2, 5]


def test_exhaustion_preserves_original_exception_and_stops_after_three_attempts():
    error = wrapped_timeout()
    operation = Mock(side_effect=error)
    with pytest.raises(R.GF.FallbackError) as caught:
        R._retry_read(operation, source='official fixture')
    assert caught.value is error
    assert isinstance(caught.value.__cause__, TimeoutError)
    assert operation.call_count == 3
    assert R.time.sleep.call_count == 2


@pytest.mark.parametrize('wrap', [lambda error: error, urllib.error.URLError, wrapped_timeout])
def test_incomplete_read_exhaustion_preserves_original_error_and_bound(wrap, capsys):
    partial = b'private partial response bytes'
    error = wrap(R.http.client.IncompleteRead(partial, 128))
    operation = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        R._retry_read(operation, source='official fixture')
    assert caught.value is error
    assert operation.call_count == 3
    assert [call.args[0] for call in R.time.sleep.call_args_list] == [2, 5]
    assert partial.decode() not in capsys.readouterr().out


def test_http_response_status_is_not_retried_even_with_incomplete_read_cause():
    error = urllib.error.HTTPError(CODE_URL, 503, 'service unavailable', {}, None)
    error.__cause__ = R.http.client.IncompleteRead(b'partial error response', 128)
    operation = Mock(side_effect=error)
    with pytest.raises(urllib.error.HTTPError) as caught:
        R._retry_read(operation, source='official fixture')
    assert caught.value is error
    assert operation.call_count == 1
    R.time.sleep.assert_not_called()


def test_partial_response_bytes_are_discarded_before_successful_full_read(monkeypatch, capsys):
    first = Response(error=R.http.client.IncompleteRead(b'private partial evidence', 128))
    complete = b'complete and independently parseable official evidence'
    second = Response(complete)
    urlopen = Mock(side_effect=[first, second])
    monkeypatch.setattr(R.urllib.request, 'urlopen', urlopen)
    result = R._http_bytes(R.urllib.request.Request(CODE_URL), timeout=60,
                           source=CODE_URL, expected_host='uscode.house.gov', max_bytes=1024)
    assert result == complete
    assert first.closed and second.closed
    assert urlopen.call_count == 2
    R.time.sleep.assert_called_once_with(2)
    assert 'private partial evidence' not in capsys.readouterr().out


@pytest.mark.parametrize('error', [
    ValueError('official identity mismatch'),
    RuntimeError('TimeoutError: read timed out'),
    RuntimeError('RemoteDisconnected: Remote end closed connection without response'),
    urllib.error.URLError('read timed out'),
    *[urllib.error.HTTPError(CODE_URL, code, 'failure', {}, None) for code in (401, 403, 404, 429, 503)],
])
def test_validation_and_http_errors_are_not_retried(error):
    operation = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        R._retry_read(operation, source='official fixture')
    assert caught.value is error
    assert operation.call_count == 1
    R.time.sleep.assert_not_called()


@pytest.mark.parametrize('response,match', [
    (Response(b'valid', url='https://example.com/view.xhtml'), 'redirected outside'),
    (Response(b'oversize'), 'size limit'),
])
def test_statute_source_and_size_guards_remain_fail_closed(response, match, monkeypatch):
    urlopen = Mock(return_value=response)
    monkeypatch.setattr(R.urllib.request, 'urlopen', urlopen)
    with pytest.raises(ValueError, match=match):
        R._http_bytes(R.urllib.request.Request(CODE_URL), timeout=60,
                      source=CODE_URL, expected_host='uscode.house.gov', max_bytes=5)
    assert urlopen.call_count == 1
    R.time.sleep.assert_not_called()


def test_collect_retries_each_official_evidence_request_and_binds_only_complete_bytes(tmp_path, monkeypatch):
    counts = Counter()
    billstatus = b'<billstatus>complete</billstatus>'
    bill = b'<bill>Complete bill referring to 12 U.S.C. 1786a.</bill>'
    statute = b'<html><title>12 USC 1786a: Authority</title><body>The authority remains in force.</body></html>'

    def fetch(url, max_bytes):
        counts[url] += 1
        if counts[url] == 1:
            raise wrapped_timeout()
        return bill if url == BILL_URL else billstatus

    monkeypatch.setattr(R.GF, 'network_fetcher', lambda timeout: fetch)
    monkeypatch.setattr(R.BS, 'parse_billstatus_xml', lambda *args, **kwargs: {'canonical_key': KEY})
    monkeypatch.setattr(R.GF, '_bill_text_urls', lambda parsed: ([BILL_URL], None))
    monkeypatch.setattr(R.GF, '_parse_bill_text', lambda *args, **kwargs: 'Complete bill referring to 12 U.S.C. 1786a.')
    monkeypatch.setattr(R.GF, '_select_title', lambda parsed: 'Fixture')
    monkeypatch.setattr(R, '_govinfo_snapshot', lambda *args, **kwargs: {'canonical_key': KEY})
    urlopen = Mock(side_effect=[Response(error=TimeoutError('partial read failed')), Response(statute)])
    monkeypatch.setattr(R.urllib.request, 'urlopen', urlopen)

    snapshot, texts, sources = R.collect(KEY, tmp_path, '2026-09-13T00:00:00Z')
    assert snapshot['canonical_key'] == KEY
    assert sorted(counts.values()) == [2, 2]
    assert urlopen.call_count == 2
    assert R.time.sleep.call_count == 3
    assert len(sources) == 3
    assert len(texts) == 2
    for source, complete in zip(sources, [billstatus, bill, statute]):
        assert Path(source['path']).read_bytes() == complete
        assert source['sha256'] == hashlib.sha256(complete).hexdigest()


def classifier_envelope(*, stop_reason='end_turn', invalid_quote=False):
    value = {'canonical_key': KEY, 'disposition': 'hold', 'confidence': 'low',
             'rationale': 'Evidence leaves the operative effect unresolved.', 'categories': []}
    if invalid_quote:
        value['categories'] = [{'category': 'Payments', 'direction': 1,
                                'rationale': 'A substantive rationale cannot make invented evidence valid.',
                                'source_url': BILL_URL, 'quote': 'This invented quotation does not occur in evidence.'}]
    return json.dumps({'stop_reason': stop_reason, 'content': [{'type': 'text', 'text': json.dumps(value)}],
                       'model': 'fixture-model', 'usage': {}, 'id': 'fixture-response'}).encode()


def test_classifier_api_retries_response_read_and_keeps_credentials_out_of_retry_log(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'private-fixture-key')
    urlopen = Mock(side_effect=[Response(error=TimeoutError('private-fixture-key')), Response(classifier_envelope())])
    monkeypatch.setattr(R.urllib.request, 'urlopen', urlopen)
    decision, meta = R.classify(KEY, {BILL_URL: 'Official evidence.'}, ['Payments'],
                                model='fixture-model', key_path=tmp_path / 'unused', transport='api')
    assert decision['disposition'] == 'hold'
    assert meta['response_id'] == 'fixture-response'
    assert urlopen.call_count == 2
    first_request = urlopen.call_args_list[0].args[0]
    assert first_request.get_header('X-api-key') == 'private-fixture-key'
    assert urlopen.call_args_list[1].args[0] is first_request
    output = capsys.readouterr().out
    assert 'retry 2/3' in output
    assert 'private-fixture-key' not in output


@pytest.mark.parametrize('body,match', [
    (b'not json', 'Expecting value'),
    (classifier_envelope(stop_reason='max_tokens'), 'incomplete classifier response'),
    (classifier_envelope(invalid_quote=True), 'quote is not in'),
])
def test_successful_transport_never_retries_or_accepts_invalid_classifier_output(tmp_path, monkeypatch, body, match):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'private-fixture-key')
    urlopen = Mock(return_value=Response(body))
    monkeypatch.setattr(R.urllib.request, 'urlopen', urlopen)
    with pytest.raises(ValueError, match=match):
        R.classify(KEY, {BILL_URL: 'Official evidence.'}, ['Payments'],
                   model='fixture-model', key_path=tmp_path / 'unused', transport='api')
    assert urlopen.call_count == 1
    R.time.sleep.assert_not_called()


@pytest.mark.parametrize('error', [TimeoutError('The read operation timed out'),
                                  R.http.client.IncompleteRead(b'partial evidence', 128)])
def test_exhausted_read_fails_refresh_and_preserves_published_generation(tmp_path, monkeypatch, error):
    files = {
        'data/legislation/sentinel_admissions.json': {'bills': {}, 'decisions': {}},
        'member_desk_bundle.json': {'members': []},
        'bill_repository_live.json': {'bills': {}},
        'source_data/legislation/acu_legislation_search_registry.json': {'categories': [{'id': 'Payments'}]},
    }
    for relative, value in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding='utf-8')
    discovery = tmp_path / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    discovery.parent.mkdir(parents=True)
    discovery.write_text(json.dumps({'canonical_key': KEY}) + '\n', encoding='utf-8')
    published = tmp_path / 'data/legislation/sentinel_admissions.json'
    before = published.read_bytes()
    operation = Mock(side_effect=error)
    monkeypatch.setattr(R, 'collect', lambda *args: R._retry_read(operation, source='official fixture'))
    assert R.main(['--root', str(tmp_path), '--keys', KEY, '--model', 'fixture-model', '--transport', 'api']) == 1
    assert operation.call_count == 3
    assert published.read_bytes() == before
    generation = json.loads(next((tmp_path / 'audits/sentinel_admissions').glob('*/generation.json')).read_text())
    assert generation['status'] == 'FAIL'
    assert generation['errors'] == {KEY: f'{type(error).__name__}: {error}'}
