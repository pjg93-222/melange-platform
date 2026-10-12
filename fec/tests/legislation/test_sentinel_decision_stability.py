"""Recurring evidence refresh must not randomly re-adjudicate unchanged law."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R

KEY = '119:hr:7'
BILL_URL = 'https://www.govinfo.gov/content/pkg/BILLS-119hr7ih/xml/BILLS-119hr7ih.xml'
CODE_URL = 'https://uscode.house.gov/view.xhtml?req=%28title%3A12%20section%3A1786a%20edition%3Aprelim%29'
QUOTE = 'The authority shall examine the service providers described in this section.'
LAW = 'This authority ceased to be effective as of December 31, 2001.'
MODEL = 'sonnet'
TRANSPORT = 'claude-cli'


def evidence(directory, date='September 7, 2026', law=LAW):
    directory.mkdir(parents=True, exist_ok=True)
    sources = []
    documents = [
        (BILL_URL, f'<bill><section>{QUOTE}</section></bill>'.encode(), {}),
        (CODE_URL, (
            '<html><title>12 USC 1786a: Omitted</title><body>'
            f'<div>Text contains those laws in effect on {date}</div>'
            '<p>From Title 12-BANKS AND BANKING</p>'
            f'<p>{law}</p></body></html>'
        ).encode(), {'kind': 'statute', 'title': '12', 'section': '1786a'}),
    ]
    for url, raw, extra in documents:
        sha = hashlib.sha256(raw).hexdigest()
        path = directory / (sha + ('.html' if extra else '.xml'))
        path.write_bytes(raw)
        sources.append({'url': url, 'path': str(path), 'sha256': sha, **extra})
    return R.bound_decision_texts({'canonical_key': KEY, 'evidence': sources}), sources


def decision(texts, sources, disposition='hold'):
    return {
        'canonical_key': KEY, 'disposition': disposition,
        'confidence': 'low' if disposition == 'hold' else 'high',
        'beneficiary_class': 'cu_primary', 'rationale': 'The net effect is not resolved by official evidence.',
        'categories': [{'category': 'Payments', 'direction': 0 if disposition == 'hold' else 1,
                        'rationale': 'The official operative provision leaves the net effect unresolved.',
                        'source_url': BILL_URL, 'quote': QUOTE}],
        'policy': R.POLICY, 'checked_at': '2026-09-08T21:25:41Z', 'evidence': sources,
        'classifier': {'model': MODEL, 'transport': TRANSPORT, 'response_id': 'original'},
        'evidence_fingerprint': R.evidence_fingerprint(texts, model=MODEL, transport=TRANSPORT, legacy=True),
    }


def reuse(texts, published, checkpoint=None, **kwargs):
    return R.reusable_decision(KEY, texts, {'Payments'}, published=published, checkpoint=checkpoint,
                               model=kwargs.get('model', MODEL), transport=TRANSPORT)


def test_daily_code_banner_does_not_change_substantive_fingerprint(tmp_path):
    old, old_sources = evidence(tmp_path / 'old')
    current, current_sources = evidence(tmp_path / 'new', 'September 11, 2026')
    assert old_sources[1]['sha256'] != current_sources[1]['sha256']
    assert old != current
    old_hash = R.evidence_fingerprint(old, model=MODEL, transport=TRANSPORT)
    assert old_hash == R.evidence_fingerprint(current, model=MODEL, transport=TRANSPORT)
    assert R.evidence_fingerprint(old, model=MODEL, transport=TRANSPORT, legacy=True) != R.evidence_fingerprint(current, model=MODEL, transport=TRANSPORT, legacy=True)
    assert 'September 11, 2026' in current[CODE_URL]  # raw parsed evidence is untouched
    assert LAW in R.substantive_texts(current)[CODE_URL]  # legal date is retained


def test_published_hold_wins_over_stochastic_checkpoint_include(tmp_path):
    old, old_sources = evidence(tmp_path / 'old')
    current, current_sources = evidence(tmp_path / 'new', 'September 11, 2026')
    published = decision(old, old_sources)
    checkpoint = decision(current, current_sources, 'include')
    recovered, metadata = reuse(current, published, checkpoint)
    assert recovered['disposition'] == 'hold' and recovered['confidence'] == 'low'
    assert metadata['cache_source'] == 'published'
    assert metadata['fingerprint_migrated'] is True
    assert recovered['categories'][0]['quote'] == QUOTE


@pytest.mark.parametrize('changed_law', [
    'This authority continues to be effective without expiration.',
    'This authority ceased to be effective as of December 31, 2027.',
])
def test_actual_statutory_change_invalidates_cached_verdict(tmp_path, changed_law):
    old, old_sources = evidence(tmp_path / 'old')
    current, _sources = evidence(tmp_path / 'new', 'September 11, 2026', changed_law)
    assert reuse(current, decision(old, old_sources)) is None


def test_non_house_code_text_is_never_date_normalized():
    text = 'Text contains those laws in effect on September 7, 2026 From Title 12'
    documents = {BILL_URL: text, 'https://example.com/view.xhtml': text}
    assert R.substantive_texts(documents) == documents


def test_policy_or_model_change_prevents_reuse(tmp_path, monkeypatch):
    texts, sources = evidence(tmp_path)
    published = decision(texts, sources)
    assert reuse(texts, published, model='different-model') is None
    monkeypatch.setattr(R, 'POLICY', 'a-new-policy')
    assert reuse(texts, published) is None


def test_migration_requires_hash_bound_evidence_and_original_fingerprint(tmp_path):
    texts, sources = evidence(tmp_path)
    published = decision(texts, sources)
    corrupted = copy.deepcopy(published)
    corrupted['evidence_fingerprint'] = '0' * 64
    with pytest.raises(ValueError, match='fingerprint does not match'):
        reuse(texts, corrupted)
    Path(sources[1]['path']).write_bytes(b'changed source')
    with pytest.raises(ValueError, match='evidence hash mismatch'):
        reuse(texts, published)


@pytest.mark.parametrize('law,expected_classifications', [(LAW, 0), ('The authority is now permanent.', 1)])
def test_complete_refresh_reuses_hold_or_classifies_changed_law(tmp_path, monkeypatch, law, expected_classifications):
    old, old_sources = evidence(tmp_path / 'old')
    current, current_sources = evidence(tmp_path / 'new', 'September 11, 2026', law)
    published = decision(old, old_sources)
    checkpoint = decision(current, current_sources, 'include')
    # For a real law change there is no completed checkpoint decision, so the
    # full refresh must call the classifier. Date-only noise must preserve hold
    # even when an interrupted checkpoint carries a different verdict.
    cached = {} if expected_classifications else {KEY: checkpoint}
    files = {
        'data/legislation/sentinel_admissions.json': {'bills': {}, 'decisions': {KEY: published}},
        'data/legislation/sentinel_admission_checkpoint.json': {'decisions': cached},
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
    discovery.write_text(json.dumps({'canonical_key': KEY}) + '\n')
    monkeypatch.setattr(R, 'collect', lambda *args: ({}, current, current_sources))
    calls = []

    def classify(*args, **kwargs):
        calls.append(args)
        return decision(current, current_sources), {'model': MODEL, 'transport': TRANSPORT}

    monkeypatch.setattr(R, 'classify', classify)
    assert R.main(['--root', str(tmp_path), '--keys', KEY, '--model', MODEL, '--transport', TRANSPORT]) == 0
    assert len(calls) == expected_classifications
    result = json.loads((tmp_path / 'data/legislation/sentinel_admissions.json').read_text())
    assert result['decisions'][KEY]['disposition'] == 'hold'
    assert not result['bills']
    assert result['decisions'][KEY]['evidence'] == current_sources
