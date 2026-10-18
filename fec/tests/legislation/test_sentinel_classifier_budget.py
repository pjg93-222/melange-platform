"""Classifier cost controls (2026-10-15): prompt caching off, source-outage keys last, a per-run call budget.

Admission criteria are untouched; these tests pin only what is spent and in what order. Nothing here touches the
network, claude.exe, or the live admissions generation: the CLI transport is a fake subprocess.run, the evidence
fetch a stub, and every classifier answer a canned hold.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R
from legislation import sentinel_admissions as SA

# Never-seen measures: with no other signal the selection order is highest number first (14, 13, 12, 11).
KEYS = ['119:hr:11', '119:hr:12', '119:hr:13', '119:hr:14']
TEXTS = {'https://example.invalid/text': 'official text'}
# sha256 of R.evidence_fingerprint(TEXTS, model='fixture', transport='claude-cli'), taken before the cache switch.
PINNED_FINGERPRINT = 'fb53d517d5c75b236e357f057d980a786eeb31064d91850be770501bcf7bb70b'
QUOTA_STDOUT = json.dumps({'is_error': True, 'api_error_status': 429, 'result': "You've hit your monthly spend limit"})


def cli(returncode, stdout='', stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def held(key):
    """A valid CLI answer: a hold passes validate_decision at any confidence, so no category evidence is needed."""
    return cli(0, json.dumps({'is_error': False, 'uuid': 'u', 'usage': {}, 'structured_output': {
        'canonical_key': key, 'disposition': 'hold', 'confidence': 'low', 'rationale': 'fixture hold', 'categories': []}}))


def hold_record(key, checked_at):
    return {'canonical_key': key, 'disposition': 'hold', 'confidence': 'low', 'rationale': 'fixture hold',
            'categories': [], 'checked_at': checked_at, 'evidence': []}


def snapshot(key):
    return {'key': key, 'display': 'H.R.' + key.split(':')[2], 'congress': 119, 'sponsor': {'bioguide': 'B001'},
            'original_cosponsors': [], 'cosponsors': [], 'areas': []}


def build(root, monkeypatch, *, keys=KEYS, published=None, hits=(), unavailable=()):
    """An empty (or `published`-seeded) generation, `keys` discovered, a stubbed fetch. Returns the collect order.

    `hits` are keys reusable_decision answers from cache (free); `unavailable` raise SourceUnavailable on collect.
    """
    previous = {'schema': SA.SCHEMA, 'status': 'PASS', 'checked_at': '2026-09-01T00:00:00+00:00', 'policy': R.POLICY,
                'bills': {}, 'decisions': {}, 'bill_catalog': {}, 'memberships': [], 'sources': [], 'errors': {},
                **(published or {})}
    R.write_json(root / SA.RELATIVE, previous)
    R.write_json(root / 'member_desk_bundle.json', {})
    R.write_json(root / 'bill_repository_live.json', {'bills': {}})
    R.write_json(root / 'source_data/legislation/acu_legislation_search_registry.json', {'categories': []})
    R.write_json(root / 'data/engine/sentinel_admission_policy.json', {'transport': 'claude-cli', 'model': 'fixture'})
    records = root / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(''.join(json.dumps({'canonical_key': key}) + '\n' for key in keys), encoding='utf-8')

    order = []

    def collect(key, directory, stamp):
        order.append(key)
        if key in unavailable:
            raise R.SourceUnavailable('fixture statute outage', snapshot=snapshot(key), texts=dict(TEXTS))
        (root / 'text.xml').write_bytes(b'official text')   # a real file, so SA.load can hash-verify the install
        return snapshot(key), dict(TEXTS), [{'url': 'https://example.invalid/text', 'path': str(root / 'text.xml'),
                                             'sha256': hashlib.sha256(b'official text').hexdigest()}]

    def reusable(key, texts, categories, **kwargs):
        if key in hits:
            return hold_record(key, '2026-09-01T00:00:00+00:00'), {'transport': 'claude-cli', 'model': 'fixture',
                                                                   'cache_hit': True}
        return None

    monkeypatch.setattr(R, 'build_ledger_view', lambda bundle: {'roster': {}})
    monkeypatch.setattr(R, 'reusable_decision', reusable)
    monkeypatch.setattr(R, 'collect', collect)
    monkeypatch.setattr(R.classifier_runtime, 'claude_command', lambda: 'claude')
    return order


def fake_cli(monkeypatch, responses):
    """subprocess.run replacement: the Nth call returns responses[N] (callable, exception or stand-in). Records env."""
    calls, envs = [], []

    def run(argv, **kwargs):
        calls.append(json.loads(kwargs['input'])['canonical_key'])
        envs.append(kwargs.get('env'))
        outcome = responses[len(calls) - 1] if len(calls) <= len(responses) else responses[-1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(calls[-1]) if callable(outcome) else outcome

    monkeypatch.setattr(R.subprocess, 'run', run)
    return calls, envs


def run_main(root, *flags):
    published = root / SA.RELATIVE
    before = published.read_bytes()
    code = R.main(['--root', str(root), *flags])
    generation = json.loads(next((root / 'audits/sentinel_admissions').glob('*/generation.json')).read_text(encoding='utf-8'))
    checkpoint = json.loads((root / 'data/legislation/sentinel_admission_checkpoint.json').read_text(encoding='utf-8'))
    return code, generation, checkpoint, published.read_bytes() != before


# ---- E1: prompt caching off, nothing else changed ----------------------------------------------------------------

def test_cli_classifier_runs_with_prompt_caching_disabled(monkeypatch):
    monkeypatch.delenv('DISABLE_PROMPT_CACHING', raising=False)
    monkeypatch.setenv('MELANGE_PROBE', 'kept')
    monkeypatch.setattr(R.classifier_runtime, 'claude_command', lambda: 'claude')
    calls, envs = fake_cli(monkeypatch, [held])
    R.classify(KEYS[0], TEXTS, ['Payments'], model='fixture', key_path=Path('unused'), transport='claude-cli')
    assert calls == [KEYS[0]]
    assert envs[0]['DISABLE_PROMPT_CACHING'] == '1'
    assert envs[0]['MELANGE_PROBE'] == 'kept'               # the child still inherits the login and everything else
    assert 'DISABLE_PROMPT_CACHING' not in os.environ       # a copy, never a mutation of this process


def test_the_cache_switch_leaves_the_fingerprint_and_its_parameters_unchanged(tmp_path, monkeypatch):
    assert R.evidence_fingerprint(TEXTS, model='fixture', transport='claude-cli') == PINNED_FINGERPRINT
    build(tmp_path, monkeypatch, keys=KEYS[:1])
    fake_cli(monkeypatch, [held])
    _, _, checkpoint, _ = run_main(tmp_path)
    decision = checkpoint['decisions'][KEYS[0]]
    assert decision['evidence_fingerprint'] == PINNED_FINGERPRINT
    assert decision['fingerprint_parameters'] == {'policy': R.POLICY, 'model': 'fixture', 'transport': 'claude-cli'}


# ---- E2: source-outage keys go last ------------------------------------------------------------------------------

def test_a_key_deferred_for_a_source_outage_last_run_is_selected_after_the_other_unseen_keys(tmp_path, monkeypatch):
    (tmp_path / 'control').mkdir()
    order = build(tmp_path / 'control', monkeypatch)
    fake_cli(monkeypatch, [held])
    run_main(tmp_path / 'control', '--limit', '2')
    assert order == ['119:hr:14', '119:hr:13']

    root = tmp_path / 'deferred'
    root.mkdir()
    order = build(root, monkeypatch, published={'source_unavailable_deferred_keys': ['119:hr:14']})
    fake_cli(monkeypatch, [held])
    _, generation, _, _ = run_main(root, '--limit', '2')
    assert order == ['119:hr:13', '119:hr:12']
    assert generation['deferred_keys'] == ['119:hr:11', '119:hr:14']


def test_deprioritised_keys_stay_eligible(tmp_path, monkeypatch):
    order = build(tmp_path, monkeypatch, published={'source_unavailable_deferred_keys': ['119:hr:14']})
    fake_cli(monkeypatch, [held])
    run_main(tmp_path, '--limit', '4')
    assert order == ['119:hr:13', '119:hr:12', '119:hr:11', '119:hr:14']


def test_a_held_key_deferred_for_an_outage_waits_behind_the_other_held_keys(tmp_path, monkeypatch):
    holds = {'decisions': {KEYS[0]: hold_record(KEYS[0], '2026-08-01T00:00:00+00:00'),
                           KEYS[1]: hold_record(KEYS[1], '2026-08-02T00:00:00+00:00')}}
    (tmp_path / 'control').mkdir()
    order = build(tmp_path / 'control', monkeypatch, keys=KEYS[:3], published=holds)
    fake_cli(monkeypatch, [held])
    run_main(tmp_path / 'control', '--limit', '3')
    assert order[0] == KEYS[0]                              # oldest hold first, as before

    root = tmp_path / 'deferred'
    root.mkdir()
    order = build(root, monkeypatch, keys=KEYS[:3],
                  published={**holds, 'source_unavailable_deferred_keys': [KEYS[0]]})
    fake_cli(monkeypatch, [held])
    run_main(root, '--limit', '3')
    assert order[0] == KEYS[1] and order[-1] == KEYS[0]


def test_the_deferral_a_run_writes_is_the_list_the_next_run_reads(tmp_path, monkeypatch):
    order = build(tmp_path, monkeypatch, unavailable=('119:hr:14',))
    fake_cli(monkeypatch, [held])
    code, generation, _, installed = run_main(tmp_path, '--limit', '2')
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert json.loads((tmp_path / SA.RELATIVE).read_text(encoding='utf-8'))['source_unavailable_deferred_keys'] == ['119:hr:14']
    assert order == ['119:hr:14', '119:hr:13']

    shutil.rmtree(tmp_path / 'audits')                      # run directories are stamped to the second
    order.clear()
    run_main(tmp_path, '--limit', '2')
    assert order == ['119:hr:13', '119:hr:12']


# ---- E3: the per-run call budget ---------------------------------------------------------------------------------

def test_a_budget_stop_is_a_normal_bounded_run_that_installs(tmp_path, monkeypatch, capsys):
    build(tmp_path, monkeypatch)
    calls, _ = fake_cli(monkeypatch, [held])
    code, generation, checkpoint, installed = run_main(tmp_path, '--limit', '4', '--max-classifier-calls', '2')
    assert calls == ['119:hr:14', '119:hr:13']              # keys 12 and 11 never reached the classifier
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['errors'] == {} and checkpoint['errors'] == {}
    assert (generation['aborted_reason'], generation['not_attempted']) == ('CLASSIFIER_CALL_BUDGET', 2)
    assert (generation['classifier_calls'], generation['max_classifier_calls']) == (2, 2)
    assert sorted(checkpoint['decisions']) == sorted(calls)  # checkpoint written, with both verdicts
    assert sorted(generation['decisions']) == sorted(calls)
    assert generation['deferred_keys'] == ['119:hr:11', '119:hr:12']  # still backlog, not lost
    assert 'classifier call budget' in capsys.readouterr().out
    assert SA.load(tmp_path)['decisions'].keys() == generation['decisions'].keys()  # the consumer accepts the install


def test_cache_hits_are_free_and_only_real_calls_spend_the_budget(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, hits=('119:hr:14', '119:hr:13'))
    calls, _ = fake_cli(monkeypatch, [held])
    code, generation, checkpoint, installed = run_main(tmp_path, '--limit', '4', '--max-classifier-calls', '1')
    assert calls == ['119:hr:12']                           # two hits went through, then one paid call hit the budget
    assert sorted(checkpoint['decisions']) == ['119:hr:12', '119:hr:13', '119:hr:14']
    assert (code, installed) == (0, True)
    assert (generation['classifier_calls'], generation['aborted_reason'], generation['not_attempted']) == (
        1, 'CLASSIFIER_CALL_BUDGET', 1)
    assert generation['deferred_keys'] == ['119:hr:11']


def test_reaching_the_budget_on_the_last_key_stops_nothing(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch, keys=KEYS[:2])
    calls, _ = fake_cli(monkeypatch, [held])
    code, generation, _, installed = run_main(tmp_path, '--limit', '4', '--max-classifier-calls', '2')
    assert len(calls) == 2 and (code, installed) == (0, True)
    assert (generation['aborted_reason'], generation['not_attempted'], generation['classifier_calls']) == (None, 0, 2)
    assert generation['deferred_keys'] == []


def test_no_llm_deferrals_make_no_calls_and_cannot_trip_the_budget(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch)
    calls, _ = fake_cli(monkeypatch, [held])
    code, generation, _, _ = run_main(tmp_path, '--limit', '4', '--no-llm', '--max-classifier-calls', '1')
    assert calls == [] and code == 0
    assert (generation['classifier_calls'], generation['aborted_reason'], generation['no_llm_deferred']) == (0, None, 4)


def test_without_a_budget_every_selected_key_is_classified(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch)
    calls, _ = fake_cli(monkeypatch, [held])
    code, generation, _, installed = run_main(tmp_path, '--limit', '4')
    assert sorted(calls) == sorted(KEYS) and (code, installed) == (0, True)
    assert (generation['classifier_calls'], generation['max_classifier_calls'], generation['aborted_reason']) == (4, None, None)


def test_a_call_that_fails_still_spends_the_budget_and_the_error_still_fails_the_run(tmp_path, monkeypatch):
    build(tmp_path, monkeypatch)
    calls, _ = fake_cli(monkeypatch, [cli(1, '', 'boom')])
    code, generation, checkpoint, installed = run_main(tmp_path, '--limit', '4', '--max-classifier-calls', '1')
    assert calls == ['119:hr:14']                           # a billed attempt counts even though it errored
    assert (code, generation['status'], installed) == (1, 'FAIL', False)   # FAIL because of the error, not the budget
    assert list(checkpoint['errors']) == calls
    assert (generation['aborted_reason'], generation['not_attempted']) == ('CLASSIFIER_CALL_BUDGET', 3)


def test_quota_exhaustion_is_still_a_failure_and_wins_over_the_budget(tmp_path, monkeypatch, capsys):
    build(tmp_path, monkeypatch)
    calls, _ = fake_cli(monkeypatch, [cli(1, QUOTA_STDOUT)])
    code, generation, checkpoint, installed = run_main(tmp_path, '--limit', '4', '--max-classifier-calls', '9')
    assert len(calls) == 1
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert checkpoint['errors'][calls[0]].startswith('CLASSIFIER_QUOTA_EXHAUSTED:')
    assert (generation['aborted_reason'], generation['not_attempted']) == ('CLASSIFIER_QUOTA_EXHAUSTED', 3)
    assert 'Run stopped on classifier quota' in capsys.readouterr().out

    shutil.rmtree(tmp_path / 'audits')
    fake_cli(monkeypatch, [cli(1, QUOTA_STDOUT)])
    _, generation, _, _ = run_main(tmp_path, '--limit', '4', '--max-classifier-calls', '1')
    assert generation['aborted_reason'] == 'CLASSIFIER_QUOTA_EXHAUSTED'   # a budget of 1 does not relabel the quota stop


@pytest.mark.parametrize('bad', ['0', '-3'])
def test_a_non_positive_budget_is_refused(tmp_path, monkeypatch, bad):
    build(tmp_path, monkeypatch)
    fake_cli(monkeypatch, [held])
    with pytest.raises(ValueError, match='--max-classifier-calls must be positive'):
        R.main(['--root', str(tmp_path), '--max-classifier-calls', bad])
