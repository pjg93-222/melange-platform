"""Conservative classifier holds cannot admit or silently remove active bills."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R


KEY = '119:hr:9798'
URL = 'https://www.govinfo.gov/content/pkg/BILLS-119hr9798ih/xml/BILLS-119hr9798ih.xml'


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('rejection,confidence', [
    ('unsupported_exclusion', 'high'),
    ('low_confidence_include', 'low'),
    ('low_confidence_include', 'medium'),
    ('low_confidence_exclude', 'low'),
    ('low_confidence_exclude', 'medium'),
    ('wrong_identity', 'high'),
    ('invalid_category_quote', 'high'),
    ('unrecognized_validation', 'high'),
])
def test_only_exact_new_candidate_validation_rejections_become_retryable_holds(
        tmp_path, monkeypatch, active, rejection, confidence):
    bill = {'sponsor': {'bioguide': 'unresolved-fixture'}, 'original_cosponsors': [], 'cosponsors': []}
    previous = {'bills': {KEY: bill} if active else {},
                'decisions': {KEY: {'disposition': 'include', 'evidence': []}} if active else {}}
    files = {
        'data/legislation/sentinel_admissions.json': previous,
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
    texts = {URL: 'Complete official text whose operative effect remains unresolved.'}
    monkeypatch.setattr(R, 'collect', lambda *args: ({}, texts, []))

    def classify(*args, **kwargs):
        value = {'canonical_key': KEY, 'disposition': 'exclude', 'confidence': confidence,
                 'rationale': 'The classifier proposes exclusion without affirmative evidence.',
                 'categories': []}
        if rejection.startswith('low_confidence_'):
            value['disposition'] = rejection.removeprefix('low_confidence_')
            value['beneficiary_class'] = 'cu_primary'
            value['categories'] = [{'category': 'Payments', 'direction': 1,
                                    'rationale': 'The complete quoted official provision is the proposed basis.',
                                    'source_url': URL, 'quote': texts[URL]}]
            value['exclusion_source_url'] = URL
            value['exclusion_quote'] = texts[URL]
        elif rejection == 'wrong_identity':
            value['canonical_key'] = '119:hr:1'
        elif rejection == 'invalid_category_quote':
            value['categories'] = [{'category': 'Payments', 'direction': 1,
                                    'rationale': 'An invented quotation must remain a validation failure.',
                                    'source_url': URL, 'quote': 'Invented quotation absent from official evidence.'}]
        elif rejection == 'unrecognized_validation':
            # Similar wording must not broaden the exact-message hold policy.
            raise ValueError('only high-confidence evidence may resolve a candidate: invalid evidence')
        return R.validate_decision(value, KEY, texts, ['Payments']), {}

    monkeypatch.setattr(R, 'classify', classify)
    # Paul 2026-09-30 ("Hold low-confidence + longer timeout"): a below-high-confidence decision is a hold for
    # every key, a previously admitted (active) bill included, and the bill is not admitted. Other validation
    # rejections keep the new-candidate-only rule.
    hold_expected = rejection.startswith('low_confidence_') or (not active and rejection == 'unsupported_exclusion')
    assert R.main(['--root', str(tmp_path), '--keys', KEY, '--model', 'fixture', '--transport', 'api']) == (0 if hold_expected else 1)
    generation = json.loads(next((tmp_path / 'audits/sentinel_admissions').glob('*/generation.json')).read_text())
    if hold_expected:
        assert generation['status'] == 'PASS'
        assert generation['errors'] == {}
        assert generation['bills'] == {}
        assert generation['memberships'] == []
        decision = generation['decisions'][KEY]
        assert decision['disposition'] == 'hold'
        assert decision['confidence'] == 'low'
        assert decision['categories'] == []
        assert decision['authority'] == 'automated_classifier_validation_gate'
        expected_rationale = ('below-high-confidence decision (%s)' % confidence
                              if rejection.startswith('low_confidence_') else
                              'ValueError: exclusion needs affirmative official evidence, not absence')
        assert decision['rationale'] == expected_rationale
        assert decision['evidence'] == []
        assert json.loads(published.read_text())['decisions'][KEY] == decision
        checkpoint = json.loads((tmp_path / 'data/legislation/sentinel_admission_checkpoint.json').read_text())
        assert checkpoint['decisions'][KEY] == decision
        assert checkpoint['errors'] == {}
        # Without a decision fingerprint, the hold must be classified again.
        assert R.reusable_decision(KEY, texts, ['Payments'], published=decision, checkpoint=None,
                                   model='fixture', transport='api') is None
    else:
        assert generation['status'] == 'FAIL'
        assert KEY in generation['errors']
        assert published.read_bytes() == before

def test_cli_classifier_call_is_bounded_at_480_s_and_reports_stdout(monkeypatch):
    """Paul 2026-09-30: the per-call claude-cli bound is 480 s (was 240 s), and a failed call names its return
    code and stdout -- the expired-login message went to stdout, which the old stderr-only text dropped."""
    import subprocess
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 1, stdout='Failed to authenticate: OAuth session expired', stderr='')

    monkeypatch.setenv('APPDATA', str(Path(__file__).parent))
    monkeypatch.delenv('MELANGE_CLASSIFIER_COMMAND', raising=False)
    monkeypatch.setattr(R.subprocess, 'run', fake_run)
    with pytest.raises(RuntimeError) as caught:
        R.classify(KEY, {URL: 'Complete official text.'}, ['Payments'], model='fixture', key_path=None,
                   transport='claude-cli')
    assert R.CLASSIFIER_TIMEOUT_SECONDS == 480
    assert seen['timeout'] == 480
    assert 'rc 1' in str(caught.value) and 'OAuth session expired' in str(caught.value)


def test_below_high_confidence_is_still_rejected_by_the_validator():
    """The rule stays: only high-confidence evidence may RESOLVE a candidate (same message, still a ValueError)."""
    value = {'canonical_key': KEY, 'disposition': 'include', 'confidence': 'medium', 'rationale': 'fixture',
             'beneficiary_class': 'cu_primary', 'categories': []}
    with pytest.raises(ValueError) as caught:
        R.validate_decision(value, KEY, {URL: 'Complete official text.'}, ['Payments'])
    assert str(caught.value) == 'only high-confidence evidence may resolve a candidate'
    assert isinstance(caught.value, R.BelowHighConfidence) and caught.value.confidence == 'medium'
