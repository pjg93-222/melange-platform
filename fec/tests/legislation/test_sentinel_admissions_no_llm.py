"""--no-llm admissions: no model call, no lost verdict, no sealed guess.

Nothing here touches the network, claude.exe, or the live admissions generation.
The classifier and both of its transports are replaced with tripwires, so a run
that reaches a model at all fails the test rather than silently costing a call.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as A

REUSED = '119:hr:1'
CARRIED = '119:hr:2'
UNSEEN = '119:hr:3'


def snapshot(key):
    number = key.split(':')[2]
    return {'key': key, 'display': 'H.R.' + number, 'congress': 119,
            'title': 'Fixture measure ' + number, 'summary': 'A fixture summary.',
            'introduced_date': '2026-01-05', 'cosponsor_count': 1,
            'url': 'https://www.congress.gov/bill/119/house-bill/' + number,
            'latest_action': {'text': 'Referred to committee'},
            'sponsor': {'bioguide': 'B001', 'name': 'Member One', 'date': '2026-01-05'},
            'original_cosponsors': [], 'cosponsors': [], 'areas': []}


def decision(key, disposition='include', **extra):
    value = {'canonical_key': key, 'disposition': disposition, 'confidence': 'high',
             'rationale': 'published fixture verdict', 'beneficiary_class': 'cu_primary',
             'categories': [{'category': 'Fixture', 'direction': 1,
                             'quote': 'a quoted operative provision from the official text',
                             'rationale': 'why this fixture provision helps credit unions',
                             'source_url': 'https://example.invalid/text'}],
             'authority': 'automated_official_evidence_policy', 'policy': A.POLICY,
             'checked_at': '2026-09-01T00:00:00+00:00',
             'evidence_fingerprint': 'published-fingerprint-for-' + key,
             'fingerprint_schema': A.FINGERPRINT_SCHEMA,
             'fingerprint_parameters': {'policy': A.POLICY, 'model': 'sonnet',
                                        'transport': 'claude-cli'},
             'evidence': [{'url': 'https://example.invalid/text',
                           'path': '/fixture/' + key.replace(':', '_') + '.xml',
                           'sha256': '0' * 64}],
             'classifier': {'model': 'sonnet', 'transport': 'claude-cli'}}
    value.update(extra)
    return value


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A minimal root with two published measures and one never-seen candidate."""
    previous = {'schema': 'sentinel-admissions-v1', 'status': 'PASS',
                'checked_at': '2026-09-01T00:00:00+00:00', 'policy': A.POLICY,
                'bills': {REUSED: snapshot(REUSED), CARRIED: snapshot(CARRIED)},
                'decisions': {REUSED: decision(REUSED), CARRIED: decision(CARRIED)},
                'bill_catalog': {REUSED: snapshot(REUSED), CARRIED: snapshot(CARRIED)},
                'memberships': [], 'sources': [], 'errors': {}}
    A.write_json(tmp_path / A.SA.RELATIVE, previous)
    A.write_json(tmp_path / 'member_desk_bundle.json', {})
    A.write_json(tmp_path / 'bill_repository_live.json', {'bills': {}})
    A.write_json(tmp_path / 'source_data/legislation/acu_legislation_search_registry.json',
                 {'categories': []})
    A.write_json(tmp_path / 'data/engine/sentinel_admission_policy.json',
                 {'transport': 'claude-cli', 'model': 'sonnet'})
    records = tmp_path / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    records.parent.mkdir(parents=True, exist_ok=True)
    records.write_text(''.join(json.dumps({'canonical_key': key}) + '\n'
                               for key in (REUSED, CARRIED, UNSEEN)), encoding='utf-8')

    monkeypatch.setattr(A, 'build_ledger_view', lambda bundle: {'roster': {'B001': 'Member One'}})
    monkeypatch.setattr(A, 'collect',
                        lambda key, directory, stamp: (snapshot(key),
                                                       {'https://example.invalid/text': 'official text'},
                                                       [{'url': 'https://example.invalid/text',
                                                         'path': str(tmp_path / (key.replace(':', '_') + '.xml')),
                                                         'sha256': '1' * 64}]))
    # Only REUSED still fingerprints to its published verdict; the other two do not.
    monkeypatch.setattr(A, 'reusable_decision',
                        lambda key, texts, categories, **kwargs:
                        (decision(REUSED), {'cache_hit': True, 'cache_source': 'published'})
                        if key == REUSED else None)
    return tmp_path


@pytest.fixture
def no_transport(monkeypatch):
    """Every route to a model is a tripwire for the duration of the test."""
    reached = []

    def refuse(*args, **kwargs):
        reached.append(args[:1])
        raise AssertionError('a --no-llm run reached the classifier')

    monkeypatch.setattr(A, 'classify', refuse)
    monkeypatch.setattr(A.subprocess, 'run', refuse)
    monkeypatch.setattr(A.urllib.request, 'urlopen', refuse)
    return reached


def test_no_llm_never_reaches_a_model_and_loses_no_published_bill(world, no_transport, capsys):
    assert A.main(['--root', str(world), '--limit', '5', '--no-llm']) == 0
    assert no_transport == []
    document = json.loads((world / A.SA.RELATIVE).read_text(encoding='utf-8'))
    assert document['status'] == 'PASS'
    assert document['no_llm'] is True

    # 1. Unchanged evidence: reuse behaves exactly as it does with the model on.
    reused = document['decisions'][REUSED]
    assert 'carried_forward_no_llm' not in reused
    assert reused['evidence_fingerprint'] != 'published-fingerprint-for-' + REUSED
    assert reused['checked_at'] == document['checked_at']

    # 2. Changed evidence with a published verdict: carried forward untouched,
    #    keeping the OLD fingerprint so the next classifying run re-decides it.
    carried = document['decisions'][CARRIED]
    assert carried['carried_forward_no_llm'] is True
    assert carried['evidence_fingerprint'] == 'published-fingerprint-for-' + CARRIED
    assert carried['checked_at'] == '2026-09-01T00:00:00+00:00'
    assert {k: v for k, v in carried.items() if k != 'carried_forward_no_llm'} == decision(CARRIED)

    # 3. No published verdict: unprocessed and unsealed. No hold is invented.
    assert UNSEEN not in document['decisions']
    assert UNSEEN not in document['bills']
    assert document['no_llm_deferred'] == 1
    assert document['no_llm_deferred_keys'] == [UNSEEN]
    assert document['errors'] == {}

    # The published generation is not corrupted: every previously published bill
    # is still admitted and still generates its membership rows.
    assert set(document['bills']) == {REUSED, CARRIED}
    assert set(document['bill_catalog']) >= {REUSED, CARRIED}
    assert sorted(row['repository_key'] for row in document['memberships']) == [REUSED, CARRIED]
    assert 'deferred: --no-llm and no published decision' in capsys.readouterr().out


def test_carried_forward_key_is_re_evaluated_by_the_next_classifying_run(world, monkeypatch):
    """The carried fingerprint must not satisfy the next run's reuse check."""
    assert A.main(['--root', str(world), '--limit', '5', '--no-llm']) == 0
    document = json.loads((world / A.SA.RELATIVE).read_text(encoding='utf-8'))
    carried = document['decisions'][CARRIED]
    # reusable_decision compares the published fingerprint against one recomputed
    # from current evidence. The carried record still holds the pre-change value,
    # so the comparison still fails and the key still goes to the model.
    assert carried['evidence_fingerprint'] == decision(CARRIED)['evidence_fingerprint']
    # The audit run directory is named to the second, so two passes inside one
    # second collide on mkdir(exist_ok=False). That is a property of the
    # producer, not of this behaviour; clear the directory between the passes.
    shutil.rmtree(world / 'audits/sentinel_admissions')
    classified = []

    def classify(key, texts, categories, **kwargs):
        classified.append(key)
        return decision(key, rationale='fresh model verdict'), {'model': 'sonnet'}

    monkeypatch.setattr(A, 'classify', classify)
    assert A.main(['--root', str(world), '--limit', '5']) == 0
    assert CARRIED in classified and REUSED not in classified


def test_without_no_llm_a_changed_key_still_goes_to_the_classifier(world, no_transport):
    """Negative control: the tripwire fires when the flag is absent."""
    assert A.main(['--root', str(world), '--limit', '5']) == 1
    document = json.loads((world / 'data/legislation/sentinel_admissions.json').read_text(encoding='utf-8'))
    # The failed run installed nothing; the published generation is unchanged.
    assert 'no_llm' not in document
    assert len(no_transport) >= 1


def test_no_llm_run_can_be_kept_out_of_the_published_generation(world, no_transport):
    before = (world / A.SA.RELATIVE).read_bytes()
    assert A.main(['--root', str(world), '--limit', '5', '--no-llm', '--no-install']) == 0
    assert (world / A.SA.RELATIVE).read_bytes() == before
    generation = sorted((world / 'audits/sentinel_admissions').iterdir())[-1] / 'generation.json'
    assert json.loads(generation.read_text(encoding='utf-8'))['no_llm_deferred_keys'] == [UNSEEN]


def test_cli_exposes_the_flag_and_defaults_it_off(world, monkeypatch):
    seen = {}

    def classify(key, texts, categories, **kwargs):
        seen[key] = True
        return decision(key), {'model': 'sonnet'}

    monkeypatch.setattr(A, 'classify', classify)
    assert A.main(['--root', str(world), '--limit', '5']) == 0
    document = json.loads((world / A.SA.RELATIVE).read_text(encoding='utf-8'))
    assert document['no_llm'] is False
    assert document['no_llm_deferred'] == 0 and document['no_llm_deferred_keys'] == []
    assert set(seen) == {CARRIED, UNSEEN}
