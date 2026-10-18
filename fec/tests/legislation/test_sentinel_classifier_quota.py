"""Classifier quota stop (2026-10-14): a rate/spend limit ends the per-key loop instead of billing every remaining key.

Nothing here touches the network, claude.exe, or the live admissions generation. The CLI transport is a fake
subprocess.run, the API transport a fake urlopen, and the evidence fetch a stub, so no test can cost a call.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R
from legislation import sentinel_admissions as SA

KEY = '119:hr:8810'
KEYS = ['119:hr:11', '119:hr:12', '119:hr:13']
# The text shape recorded in data/legislation/sentinel_admission_checkpoint.json for 119:hr:8810 (rc 1, HTTP 429).
SPEND_LIMIT = ("You've hit your monthly spend limit · raise it at claude.ai/settings/usage?from=cc_cli_limit_message "
               "· your weekly limit resets 6pm (America/Chicago)")
QUOTA_STDOUT = json.dumps({
    'type': 'result', 'subtype': 'success', 'is_error': True, 'api_error_status': 429, 'duration_ms': 683, 'num_turns': 1,
    'result': SPEND_LIMIT, 'uuid': '2fea65fe-a906-48e0-82a4-5731c5cfce55'}, separators=(',', ':'), ensure_ascii=False)


def cli(returncode, stdout='', stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def classify_cli(monkeypatch, completed):
    monkeypatch.setattr(R.classifier_runtime, 'claude_command', lambda: 'claude')
    monkeypatch.setattr(R.subprocess, 'run', Mock(return_value=completed))
    return R.classify(KEY, {}, ['Payments'], model='fixture', key_path=Path('unused'), transport='claude-cli')


@pytest.mark.parametrize('stdout, stderr', [
    (QUOTA_STDOUT, ''),                                       # the real 429 envelope
    ('', "You've hit your monthly spend limit"),               # text only, on stderr
    ('Usage Limit reached for this period', ''),               # case-insensitive "usage limit"
    ('{"is_error": true, "api_error_status": 429}', ''),       # spaced JSON
])
def test_cli_nonzero_exit_with_a_rate_limit_is_quota_exhausted(monkeypatch, stdout, stderr):
    with pytest.raises(R.ClassifierQuotaExhausted, match=r'tool-free classifier failed \(rc 1\)'):
        classify_cli(monkeypatch, cli(1, stdout, stderr))
    assert issubclass(R.ClassifierQuotaExhausted, RuntimeError)


@pytest.mark.parametrize('envelope', [
    {'is_error': True, 'api_error_status': 429, 'result': 'Request failed'},   # status field alone
    {'is_error': True, 'result': 'You have reached your usage limit'},         # text alone
])
def test_cli_zero_exit_error_envelope_with_a_rate_limit_is_quota_exhausted(monkeypatch, envelope):
    with pytest.raises(R.ClassifierQuotaExhausted, match='tool-free classifier: '):
        classify_cli(monkeypatch, cli(0, json.dumps(envelope)))


def test_ordinary_cli_failures_keep_the_old_runtime_error(monkeypatch):
    with pytest.raises(RuntimeError, match=r'tool-free classifier failed \(rc 1\)') as failed:
        classify_cli(monkeypatch, cli(1, '{"is_error":true,"api_error_status":500}', 'boom'))
    assert type(failed.value) is RuntimeError
    with pytest.raises(RuntimeError, match='tool-free classifier: overloaded') as failed:
        classify_cli(monkeypatch, cli(0, json.dumps({'is_error': True, 'api_error_status': 529, 'result': 'overloaded'})))
    assert type(failed.value) is RuntimeError


def test_a_successful_envelope_that_merely_mentions_a_limit_is_not_a_quota_stop(monkeypatch):
    # Only an error envelope is inspected: a clean answer whose text says "spend limit" is just a bad payload.
    with pytest.raises(json.JSONDecodeError):
        classify_cli(monkeypatch, cli(0, json.dumps({'is_error': False, 'result': 'discusses a spend limit'})))


def test_api_transport_429_is_quota_exhausted_but_other_statuses_are_not(monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'fixture-not-a-key')
    monkeypatch.setattr(R.time, 'sleep', Mock())
    for code, expected in ((429, R.ClassifierQuotaExhausted), (400, urllib.error.HTTPError)):
        error = urllib.error.HTTPError('https://api.anthropic.com/v1/messages', code, 'status', {}, None)
        monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=error))
        with pytest.raises(expected) as raised:
            R.classify(KEY, {}, ['Payments'], model='fixture', key_path=Path('unused'), transport='api')
        assert type(raised.value) is expected


# ---- the per-key loop -------------------------------------------------------------------------------------------

def snapshot(key):
    return {'key': key, 'display': 'H.R.' + key.split(':')[2], 'congress': 119, 'sponsor': {'bioguide': 'B001'},
            'original_cosponsors': [], 'cosponsors': [], 'areas': []}


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Three never-seen measures, an empty published generation, a stubbed evidence fetch and a counted fake CLI."""
    previous = {'schema': SA.SCHEMA, 'status': 'PASS', 'checked_at': '2026-09-01T00:00:00+00:00', 'policy': R.POLICY,
                'bills': {}, 'decisions': {}, 'bill_catalog': {}, 'memberships': [], 'sources': [], 'errors': {}}
    R.write_json(tmp_path / SA.RELATIVE, previous)
    R.write_json(tmp_path / 'member_desk_bundle.json', {})
    R.write_json(tmp_path / 'bill_repository_live.json', {'bills': {}})
    R.write_json(tmp_path / 'source_data/legislation/acu_legislation_search_registry.json', {'categories': []})
    R.write_json(tmp_path / 'data/engine/sentinel_admission_policy.json', {'transport': 'claude-cli', 'model': 'fixture'})
    records = tmp_path / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(''.join(json.dumps({'canonical_key': key}) + '\n' for key in KEYS), encoding='utf-8')

    monkeypatch.setattr(R, 'build_ledger_view', lambda bundle: {'roster': {}})
    monkeypatch.setattr(R, 'reusable_decision', lambda *args, **kwargs: None)
    monkeypatch.setattr(R, 'collect', lambda key, directory, stamp: (
        snapshot(key), {'https://example.invalid/text': 'official text'},
        [{'url': 'https://example.invalid/text', 'path': str(tmp_path / 'text.xml'), 'sha256': '1' * 64}]))
    monkeypatch.setattr(R.classifier_runtime, 'claude_command', lambda: 'claude')
    return tmp_path


def fake_cli(monkeypatch, responses):
    """subprocess.run replacement: the Nth call returns responses[N] (a CompletedProcess stand-in) or raises it."""
    calls = []

    def run(argv, **kwargs):
        calls.append(json.loads(kwargs['input'])['canonical_key'])
        outcome = responses[len(calls) - 1] if len(calls) <= len(responses) else responses[-1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(calls[-1]) if callable(outcome) else outcome

    monkeypatch.setattr(R.subprocess, 'run', run)
    return calls


def held(key):
    """A valid CLI answer: a hold passes validate_decision at any confidence, so no category evidence is needed."""
    return cli(0, json.dumps({'is_error': False, 'uuid': 'u', 'usage': {}, 'structured_output': {
        'canonical_key': key, 'disposition': 'hold', 'confidence': 'low', 'rationale': 'fixture hold', 'categories': []}}))


def run_main(root):
    published = root / SA.RELATIVE
    before = published.read_bytes()
    code = R.main(['--root', str(root), '--limit', '5'])
    generation = json.loads(next((root / 'audits/sentinel_admissions').glob('*/generation.json')).read_text(encoding='utf-8'))
    checkpoint = json.loads((root / 'data/legislation/sentinel_admission_checkpoint.json').read_text(encoding='utf-8'))
    return code, generation, checkpoint, published.read_bytes() != before


def test_quota_on_the_first_key_stops_the_loop_after_one_call(world, monkeypatch, capsys):
    calls = fake_cli(monkeypatch, [cli(1, QUOTA_STDOUT)])
    code, generation, checkpoint, installed = run_main(world)
    assert len(calls) == 1                                  # keys 2 and 3 never reached the classifier
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert list(checkpoint['errors']) == calls and list(generation['errors']) == calls
    assert checkpoint['errors'][calls[0]].startswith('CLASSIFIER_QUOTA_EXHAUSTED:')
    assert 'monthly spend limit' in checkpoint['errors'][calls[0]]
    assert (generation['aborted_reason'], generation['not_attempted']) == ('CLASSIFIER_QUOTA_EXHAUSTED', 2)
    out = capsys.readouterr().out
    assert 'Run stopped on classifier quota' in out and 'reused on rerun' in out


def test_decisions_made_before_the_quota_hit_are_checkpointed(world, monkeypatch):
    calls = fake_cli(monkeypatch, [held, cli(1, QUOTA_STDOUT)])
    code, generation, checkpoint, installed = run_main(world)
    assert len(calls) == 2                                  # the third key is never attempted
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert list(checkpoint['decisions']) == calls[:1] and checkpoint['decisions'][calls[0]]['disposition'] == 'hold'
    assert list(checkpoint['errors']) == calls[1:]
    assert checkpoint['errors'][calls[1]].startswith('CLASSIFIER_QUOTA_EXHAUSTED:')
    assert generation['not_attempted'] == 1 and set(KEYS) - set(calls) == {KEYS[0]}


def test_an_ordinary_classifier_failure_does_not_stop_the_loop(world, monkeypatch):
    calls = fake_cli(monkeypatch, [cli(1, '', 'boom')])
    code, generation, checkpoint, installed = run_main(world)
    assert sorted(calls) == sorted(KEYS)                    # every key still got its call
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert sorted(checkpoint['errors']) == sorted(KEYS)
    assert all(reason.startswith('RuntimeError: tool-free classifier failed (rc 1)') for reason in checkpoint['errors'].values())
    assert (generation['aborted_reason'], generation['not_attempted']) == (None, 0)


# ---- statute fetch ----------------------------------------------------------------------------------------------

BILL_URL = 'https://www.govinfo.gov/content/pkg/BILLS-119hr7ih/xml/BILLS-119hr7ih.xml'
BILL = b'Section 2 amends 12 U.S.C. 1757 so federal credit unions may offer member payment accounts.'


@pytest.mark.parametrize('code, expected', [(429, R.SourceUnavailable), (500, R.SourceUnavailable), (404, urllib.error.HTTPError)])
def test_statute_fetch_429_is_an_outage_like_a_5xx_and_other_4xx_still_raise(tmp_path, monkeypatch, code, expected):
    monkeypatch.setattr(R.time, 'sleep', Mock())
    monkeypatch.setattr(R.GF, 'network_fetcher', lambda timeout: lambda url, cap: BILL if url == BILL_URL else b'<billstatus/>')
    monkeypatch.setattr(R.BS, 'parse_billstatus_xml', lambda *a, **k: {'canonical_key': '119:hr:7'})
    monkeypatch.setattr(R.GF, '_bill_text_urls', lambda parsed: ([BILL_URL], None))
    monkeypatch.setattr(R.GF, '_parse_bill_text', lambda raw, **k: raw.decode())
    monkeypatch.setattr(R.GF, '_select_title', lambda parsed: 'Fixture')
    monkeypatch.setattr(R, '_govinfo_snapshot', lambda *a, **k: snapshot('119:hr:7'))
    error = urllib.error.HTTPError('https://uscode.house.gov/view.xhtml', code, 'status', {}, None)
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=error))
    with pytest.raises(expected) as raised:
        R.collect('119:hr:7', tmp_path / 'evidence', '2026-10-14T00:00:00+00:00')
    assert type(raised.value) is expected
    if expected is R.SourceUnavailable:
        assert raised.value.snapshot['key'] == '119:hr:7' and BILL_URL in raised.value.texts
