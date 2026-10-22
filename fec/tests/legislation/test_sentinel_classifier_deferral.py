"""Classifier timeout / non-quota failure on a measure that is not yet admitted is a deferral, not a FAIL
(Paul 2026-10-19, ruling R1; pipeline-health Fix 3).

On 2026-10-18 the 480 s claude-cli bound expired on 119:hr:8295, fell into the generic error branch, and one
unadmitted candidate failed the whole admissions step: status FAIL, the generation never installed, exit 1.
A new measure that could not be classified is unsealed work, exactly like one whose official source was down
(sentinel source-outage rule, 2026-10-11): it is deferred through the same list and tried again next run.

What does NOT change, and is pinned here: an already-admitted key still errors (its published include must not
silently outlive an unverifiable re-check), and a classifier quota stop (D-211) still ends the loop and fails.

Nothing here touches the network, claude.exe or a live generation: the CLI is a fake subprocess.run and the
evidence fetch is a stub, as in test_sentinel_classifier_quota.py.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R
from legislation import sentinel_admissions as SA

NEW = ['119:hr:11', '119:hr:12', '119:hr:13']
ADMITTED = '119:hr:99'
QUOTA_STDOUT = json.dumps({'is_error': True, 'api_error_status': 429, 'result': "You've hit your monthly spend limit"})
TIMEOUT = subprocess.TimeoutExpired('claude', R.CLASSIFIER_TIMEOUT_SECONDS)


def cli(returncode, stdout='', stderr=''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def snapshot(key):
    return {'key': key, 'display': 'H.R.' + key.split(':')[2], 'congress': 119, 'sponsor': {'bioguide': 'B001'},
            'original_cosponsors': [], 'cosponsors': [], 'areas': []}


def held(key):
    """A valid CLI answer: a hold passes validate_decision at any confidence, so no category evidence is needed."""
    return cli(0, json.dumps({'is_error': False, 'uuid': 'u', 'usage': {}, 'structured_output': {
        'canonical_key': key, 'disposition': 'hold', 'confidence': 'low', 'rationale': 'fixture hold', 'categories': []}}))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Three never-seen measures plus one already-admitted include; evidence fetch stubbed, CLI left to the test."""
    admitted = {'canonical_key': ADMITTED, 'disposition': 'include', 'confidence': 'high', 'beneficiary_class': 'cu_primary',
                'rationale': 'fixture include', 'categories': [], 'checked_at': '2026-09-01T00:00:00+00:00', 'evidence': []}
    previous = {'schema': SA.SCHEMA, 'status': 'PASS', 'checked_at': '2026-09-01T00:00:00+00:00', 'policy': R.POLICY,
                'bills': {ADMITTED: snapshot(ADMITTED)}, 'decisions': {ADMITTED: admitted},
                'bill_catalog': {ADMITTED: snapshot(ADMITTED)}, 'memberships': [], 'sources': [], 'errors': {}}
    R.write_json(tmp_path / SA.RELATIVE, previous)
    R.write_json(tmp_path / 'member_desk_bundle.json', {})
    R.write_json(tmp_path / 'bill_repository_live.json', {'bills': {}})
    R.write_json(tmp_path / 'source_data/legislation/acu_legislation_search_registry.json', {'categories': []})
    R.write_json(tmp_path / 'data/engine/sentinel_admission_policy.json', {'transport': 'claude-cli', 'model': 'fixture'})
    records = tmp_path / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(''.join(json.dumps({'canonical_key': key}) + '\n' for key in NEW), encoding='utf-8')

    monkeypatch.setattr(R, 'build_ledger_view', lambda bundle: {'roster': {}})
    monkeypatch.setattr(R, 'reusable_decision', lambda *args, **kwargs: None)
    monkeypatch.setattr(R, 'collect', lambda key, directory, stamp: (
        snapshot(key), {'https://example.invalid/text': 'official text'},
        [{'url': 'https://example.invalid/text', 'path': str(tmp_path / 'text.xml'), 'sha256': '1' * 64}]))
    monkeypatch.setattr(R.classifier_runtime, 'claude_command', lambda: 'claude')
    return tmp_path


def fake_cli(monkeypatch, outcomes, default=held):
    """subprocess.run replacement answering per canonical_key: outcomes[key] is a response, a callable or an exception."""
    calls = []

    def run(argv, **kwargs):
        key = json.loads(kwargs['input'])['canonical_key']
        calls.append(key)
        outcome = outcomes.get(key, default)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome(key) if callable(outcome) else outcome

    monkeypatch.setattr(R.subprocess, 'run', run)
    return calls


def run_main(root, *flags):
    published = root / SA.RELATIVE
    before = published.read_bytes()
    code = R.main(['--root', str(root), '--limit', '8', *flags])
    generation = json.loads(next((root / 'audits/sentinel_admissions').glob('*/generation.json')).read_text(encoding='utf-8'))
    return code, generation, published.read_bytes() != before


def without_admitted(root):
    """Drop the admitted include, so a test sees only never-admitted keys (an admitted key's answer is a success too)."""
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document.update(bills={}, decisions={}, bill_catalog={})
    R.write_json(path, document)


def test_timeout_on_a_new_key_is_deferred_and_the_run_still_passes(world, monkeypatch, capsys):
    key = NEW[0]
    calls = fake_cli(monkeypatch, {key: TIMEOUT})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (0, 'PASS', True)           # (c) PASS-able, and installed
    assert generation['errors'] == {}
    assert key in calls                                                           # it really reached the classifier
    assert key not in generation['decisions'] and key not in generation['bills']  # never admitted, never held
    assert generation['source_unavailable_deferred_keys'] == [key]                # the existing deferral list
    assert 'TimeoutExpired' in generation['classifier_deferred'][key]             # the reason stays visible
    assert generation['classifier_calls'] == len(calls)                           # a billed attempt is still counted
    assert set(NEW) - {key} <= set(calls)                                         # the loop went on to the other keys
    assert generation['aborted_reason'] is None and generation['not_attempted'] == 0
    assert generation['classifier_systemic_failure'] is False                     # one isolated timeout is not an outage
    out = capsys.readouterr().out
    assert key + ' deferred:' in out and 'TimeoutExpired' in out
    assert json.loads(out.strip().splitlines()[-1])['errors'] == 0


def test_nonzero_exit_that_is_not_a_quota_limit_on_new_keys_is_deferred(world, monkeypatch):
    calls = fake_cli(monkeypatch, {key: cli(1, '', 'boom') for key in NEW})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert sorted(calls) == sorted(NEW + [ADMITTED])                              # no key was skipped
    assert generation['errors'] == {}
    assert generation['source_unavailable_deferred_keys'] == sorted(NEW)
    assert all('tool-free classifier failed (rc 1)' in generation['classifier_deferred'][key] for key in NEW)
    assert generation['aborted_reason'] is None


def test_deferred_keys_are_checkpointed_as_no_error(world, monkeypatch):
    fake_cli(monkeypatch, {NEW[0]: TIMEOUT})
    run_main(world)
    checkpoint = json.loads((world / 'data/legislation/sentinel_admission_checkpoint.json').read_text(encoding='utf-8'))
    assert checkpoint['errors'] == {}                                             # a stale error would reorder the retry pool


def test_timeout_on_an_already_admitted_key_still_fails(world, monkeypatch):
    calls = fake_cli(monkeypatch, {ADMITTED: TIMEOUT})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)          # (d) unchanged
    assert generation['errors'][ADMITTED].startswith('TimeoutExpired')
    assert ADMITTED not in generation.get('source_unavailable_deferred_keys', [])
    assert ADMITTED not in generation.get('classifier_deferred', {})
    assert ADMITTED in calls


def test_nonzero_exit_on_an_already_admitted_key_still_fails(world, monkeypatch):
    fake_cli(monkeypatch, {ADMITTED: cli(1, '', 'boom')})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert generation['errors'][ADMITTED].startswith('RuntimeError: tool-free classifier failed (rc 1)')


def test_a_new_key_deferral_does_not_hide_an_admitted_key_failure(world, monkeypatch):
    fake_cli(monkeypatch, {NEW[0]: TIMEOUT, ADMITTED: TIMEOUT})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert list(generation['errors']) == [ADMITTED]
    assert generation['source_unavailable_deferred_keys'] == [NEW[0]]


def test_quota_exhaustion_still_stops_the_loop_and_fails(world, monkeypatch):
    # The admitted key is attempted first (it is always selected); the quota then hits the first new key.
    calls = fake_cli(monkeypatch, {key: cli(1, QUOTA_STDOUT) for key in NEW})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)          # (e) unchanged
    assert calls == [ADMITTED, NEW[2]]                                            # the quota stop ended the loop at once
    assert generation['aborted_reason'] == 'CLASSIFIER_QUOTA_EXHAUSTED' and generation['not_attempted'] == 2
    assert list(generation['errors']) == [NEW[2]]
    assert generation['errors'][NEW[2]].startswith('CLASSIFIER_QUOTA_EXHAUSTED:')
    assert generation.get('source_unavailable_deferred_keys', []) == [] and generation.get('classifier_deferred', {}) == {}


# ---- systemic outage guard (2026-10-19 review of R1) ------------------------------------------------------------
# A classifier that is broken for good (auth expired, model retired, network down) would otherwise defer every new
# key and let the run PASS and install. Every call deferred, and at least two non-timeout failures or three timeouts, is
# that shape (rules below).

def test_two_or_more_deferrals_and_no_successful_call_fail_the_run_as_systemic(world, monkeypatch, capsys):
    without_admitted(world)
    calls = fake_cli(monkeypatch, {key: TIMEOUT for key in NEW})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)          # same path as errors: exit 1, no install
    assert sorted(calls) == sorted(NEW)                                           # every key was still tried
    assert generation['classifier_systemic_failure'] is True
    assert list(generation['errors']) == ['CLASSIFIER_SYSTEMIC_FAILURE']          # no per-key error: the keys stay deferrals
    assert '3 classifier calls' in generation['errors']['CLASSIFIER_SYSTEMIC_FAILURE']
    assert 'TimeoutExpired' in generation['errors']['CLASSIFIER_SYSTEMIC_FAILURE']  # the shared reason is named
    assert sorted(generation['classifier_deferred']) == sorted(NEW)
    assert sorted(generation['source_unavailable_deferred_keys']) == sorted(NEW)
    out = capsys.readouterr().out
    assert json.loads(out.strip().splitlines()[-1])['classifier_systemic_failure'] is True
    assert 'classifier systemic failure' in out.lower()


def test_systemic_failure_leaves_the_checkpoint_free_of_a_non_key_error(world, monkeypatch):
    without_admitted(world)
    fake_cli(monkeypatch, {key: TIMEOUT for key in NEW})
    run_main(world)
    checkpoint = json.loads((world / 'data/legislation/sentinel_admission_checkpoint.json').read_text(encoding='utf-8'))
    assert checkpoint['errors'] == {}                                             # the marker is not a key to retry


# Systemic means a dead classifier, not slow bills (follow-up 1 to R1). Non-timeout failures (auth expired, model gone,
# overloaded) count from two; timeouts count only when every call timed out and there are three of them.

def test_two_timeouts_on_huge_bills_with_no_other_call_pass(world, monkeypatch):
    without_admitted(world)
    calls = fake_cli(monkeypatch, {key: TIMEOUT for key in NEW})
    code, generation, installed = run_main(world, '--limit', '2')

    assert len(calls) == 2
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['classifier_systemic_failure'] is False and generation['errors'] == {}
    assert sorted(generation['classifier_deferred']) == sorted(calls)


def test_two_non_timeout_failures_with_no_success_are_systemic(world, monkeypatch):
    # An expired login: every call exits rc 1. Two are enough, unlike timeouts.
    without_admitted(world)
    calls = fake_cli(monkeypatch, {key: cli(1, '', 'auth expired') for key in NEW})
    code, generation, installed = run_main(world, '--limit', '2')

    assert len(calls) == 2
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert generation['classifier_systemic_failure'] is True
    assert list(generation['errors']) == ['CLASSIFIER_SYSTEMIC_FAILURE']


def test_one_non_timeout_failure_beside_a_timeout_is_not_systemic(world, monkeypatch):
    without_admitted(world)
    calls = fake_cli(monkeypatch, {NEW[2]: cli(1, '', 'boom'), NEW[1]: TIMEOUT})
    code, generation, installed = run_main(world, '--limit', '2')

    assert calls == [NEW[2], NEW[1]]
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['classifier_systemic_failure'] is False



def test_one_overloaded_failure_and_two_timeouts_with_no_success_are_systemic(world, monkeypatch):
    # Pre-merge review: a mixed outage (one non-timeout failure, the rest timeouts, nothing succeeded) passed with every
    # new key deferred. Three deferrals and no success is an outage whatever their mix.
    without_admitted(world)
    calls = fake_cli(monkeypatch, {NEW[0]: cli(1, '', 'overloaded'), NEW[1]: TIMEOUT, NEW[2]: TIMEOUT})
    code, generation, installed = run_main(world)

    assert sorted(calls) == sorted(NEW)
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert generation['classifier_systemic_failure'] is True
    assert list(generation['errors']) == ['CLASSIFIER_SYSTEMIC_FAILURE']


# A non-quota is_error envelope (exit 0, e.g. "overloaded") is a classifier failure like a non-quota rc != 0 (follow-up 3).

def overloaded(status=529):
    return cli(0, json.dumps({'is_error': True, 'api_error_status': status, 'result': 'overloaded'}))


def test_is_error_envelope_on_a_new_key_is_deferred(world, monkeypatch):
    without_admitted(world)
    calls = fake_cli(monkeypatch, {NEW[1]: overloaded()})
    code, generation, installed = run_main(world)

    assert NEW[1] in calls
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['errors'] == {}
    assert generation['source_unavailable_deferred_keys'] == [NEW[1]]
    assert generation['classifier_deferred'][NEW[1]].startswith('RuntimeError: tool-free classifier: overloaded')


def test_is_error_envelope_on_an_admitted_key_still_fails(world, monkeypatch):
    fake_cli(monkeypatch, {ADMITTED: overloaded()})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert generation['errors'][ADMITTED].startswith('RuntimeError: tool-free classifier: overloaded')
    assert ADMITTED not in generation['classifier_deferred']


def test_is_error_envelopes_on_every_call_are_systemic(world, monkeypatch):
    without_admitted(world)
    fake_cli(monkeypatch, {key: overloaded() for key in NEW})
    code, generation, installed = run_main(world, '--limit', '2')

    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert generation['classifier_systemic_failure'] is True and generation['classifier_calls'] == 2


def test_a_quota_is_error_envelope_still_stops_the_loop(world, monkeypatch):
    without_admitted(world)
    envelope = cli(0, json.dumps({'is_error': True, 'api_error_status': 429, 'result': "You've hit your monthly spend limit"}))
    calls = fake_cli(monkeypatch, {key: envelope for key in NEW})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert len(calls) == 1 and generation['aborted_reason'] == 'CLASSIFIER_QUOTA_EXHAUSTED'
    assert generation['classifier_deferred'] == {}


def test_a_single_deferral_with_no_successful_call_still_passes(world, monkeypatch):
    # The 2026-10-18 shape at its smallest: one selected key, one timeout, nothing else called. Below the 2-key bar.
    without_admitted(world)
    calls = fake_cli(monkeypatch, {key: TIMEOUT for key in NEW})
    code, generation, installed = run_main(world, '--limit', '1')

    assert len(calls) == 1
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['classifier_systemic_failure'] is False and generation['errors'] == {}
    assert list(generation['classifier_deferred']) == calls


def test_two_deferrals_beside_a_successful_call_still_pass(world, monkeypatch):
    without_admitted(world)
    # Selection order is 13, 12, 11: the first two time out, the last answers.
    calls = fake_cli(monkeypatch, {NEW[2]: TIMEOUT, NEW[1]: TIMEOUT})
    code, generation, installed = run_main(world)

    assert calls == [NEW[2], NEW[1], NEW[0]]
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['classifier_systemic_failure'] is False and generation['errors'] == {}
    assert sorted(generation['classifier_deferred']) == sorted(NEW[1:])
    assert NEW[0] in generation['decisions']


def test_cache_hits_do_not_count_as_a_successful_classifier_call(world, monkeypatch):
    # A reused verdict is not a classifier call: two real calls that both fail are still no success this run.
    without_admitted(world)
    cached = {'canonical_key': NEW[0], 'disposition': 'hold', 'confidence': 'low', 'rationale': 'cached',
              'categories': [], 'evidence': []}
    monkeypatch.setattr(R, 'reusable_decision',
                        lambda key, *args, **kwargs: (cached, {'cache_hit': True}) if key == NEW[0] else None)
    fake_cli(monkeypatch, {NEW[1]: cli(1, '', 'boom'), NEW[2]: cli(1, '', 'boom')})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert generation['classifier_systemic_failure'] is True and generation['classifier_calls'] == 2


# ---- the desk's backlog count (fec/surfaces/make_member_desk.py reads len(deferred_keys)) -------------------------

def test_a_classifier_deferred_key_stays_in_deferred_keys(world, monkeypatch):
    without_admitted(world)
    fake_cli(monkeypatch, {NEW[1]: TIMEOUT})
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['deferred_keys'] == [NEW[1]]                                # attempted but unsealed: still backlog


def test_a_source_unavailable_deferred_key_stays_in_deferred_keys(world, monkeypatch):
    without_admitted(world)
    fake_cli(monkeypatch, {})
    real = R.collect

    def collect(key, directory, stamp):
        if key == NEW[1]:
            raise R.SourceUnavailable('fixture statute outage', snapshot=snapshot(key), texts={})
        return real(key, directory, stamp)

    monkeypatch.setattr(R, 'collect', collect)
    code, generation, installed = run_main(world)

    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['source_unavailable_deferred_keys'] == [NEW[1]]
    assert generation['deferred_keys'] == [NEW[1]]


def test_deferred_keys_counts_an_unattempted_key_and_a_deferred_key_once_each(world, monkeypatch):
    # Budget of 2 calls: 13 times out (deferred), 12 answers, 11 is never reached. Both stay backlog, neither twice.
    without_admitted(world)
    fake_cli(monkeypatch, {NEW[2]: TIMEOUT})
    code, generation, installed = run_main(world, '--max-classifier-calls', '2')

    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['not_attempted'] == 1
    assert generation['deferred_keys'] == sorted([NEW[0], NEW[2]])
    assert len(generation['deferred_keys']) == len(set(generation['deferred_keys']))


# ---- per-key repeated-deferral hold (follow-up 2 to R1) ----------------------------------------------------------
# A bill that times out every run is retried, and billed, forever. After HOLD_AFTER_DEFERRALS consecutive runs the key is
# held like the 'complete evidence exceeds model envelope' holds; any other outcome for it resets the count.

def run_key(root, key):
    """One run on a single named key (a fresh run directory each time), returning the published generation."""
    import shutil
    shutil.rmtree(root / 'audits/sentinel_admissions', ignore_errors=True)
    return run_main_once(root, key)


def run_main_once(root, key):
    assert R.main(['--root', str(root), '--limit', '8', '--keys', key]) == 0
    return json.loads((root / SA.RELATIVE).read_text(encoding='utf-8'))


def test_a_key_deferred_three_runs_running_is_held_with_the_envelope_hold_shape(world, monkeypatch):
    without_admitted(world)
    key = NEW[2]
    fake_cli(monkeypatch, {key: TIMEOUT})
    first = run_key(world, key)
    second = run_key(world, key)
    assert (first['classifier_deferral_counts'], second['classifier_deferral_counts']) == ({key: 1}, {key: 2})
    assert key not in second['decisions'] and second['source_unavailable_deferred_keys'] == [key]

    third = run_key(world, key)
    assert third['classifier_deferral_counts'] == {key: 3}
    held_decision = third['decisions'][key]
    assert held_decision['disposition'] == 'hold' and held_decision['confidence'] == 'low'
    assert held_decision['authority'] == 'automated_classifier_validation_gate'
    assert held_decision['rationale'].startswith('classifier unavailable 3 runs: TimeoutExpired')
    assert (held_decision['categories'], held_decision['evidence'], held_decision['canonical_key']) == ([], [], key)
    assert 'checked_at' in held_decision and key not in third['bills']
    assert third['source_unavailable_deferred_keys'] == [] and third['errors'] == {}   # a hold, no longer unsealed work


def test_the_hold_rationale_is_written_with_the_shared_prefix_constant(world, monkeypatch):
    without_admitted(world)
    key = NEW[2]
    monkeypatch.setattr(R, 'CLASSIFIER_UNAVAILABLE_PREFIX', 'prefix-under-test')
    fake_cli(monkeypatch, {key: TIMEOUT})
    run_key(world, key); run_key(world, key)
    third = run_key(world, key)
    assert third['decisions'][key]['rationale'].startswith('prefix-under-test 3 runs: TimeoutExpired')


def test_a_successful_classification_resets_the_deferral_count(world, monkeypatch):
    without_admitted(world)
    key = NEW[2]
    outcomes = {key: TIMEOUT}
    fake_cli(monkeypatch, outcomes)
    run_key(world, key); run_key(world, key)
    outcomes.pop(key)                                                              # the classifier answers this time
    answered = run_key(world, key)
    assert answered['classifier_deferral_counts'] == {} and answered['decisions'][key]['rationale'] == 'fixture hold'

    outcomes[key] = TIMEOUT
    again = run_key(world, key)
    assert again['classifier_deferral_counts'] == {key: 1}                         # counting restarts: no premature hold
    assert again['decisions'][key]['rationale'] == 'fixture hold'


def test_deferral_counts_ignore_keys_that_are_not_attempted_this_run(world, monkeypatch):
    without_admitted(world)
    fake_cli(monkeypatch, {NEW[2]: TIMEOUT})
    run_key(world, NEW[2])
    other = run_key(world, NEW[1])                                           # a run that never selects NEW[2]
    assert other['classifier_deferral_counts'] == {NEW[2]: 1}
