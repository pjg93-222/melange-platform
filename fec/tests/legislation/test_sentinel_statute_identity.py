"""Official citation defects must never become substitute legal evidence."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R


KEY = '119:hr:9837'


def test_unicode_dashes_and_multiple_letter_sections_are_not_truncated():
    text = '42 U.S.C. 300g–6; 12 U.S.C. 1715z–1; 42 U.S.C. 300gg–111; 42 U.S.C. 2000bb–1.'
    documents = {'official': text}
    assert R.statute_references(documents) == {
        ('42', '300g-6'), ('12', '1715z-1'), ('42', '300gg-111'), ('42', '2000bb-1'),
    }
    assert documents['official'] == text


def test_typographical_digit_one_is_never_substituted_with_letter_l():
    assert R.statute_references({'official': '12 U.S.C. 17151(d)(4)(iv); 12 U.S.C. 1715l(d)(3)'}) == {
        ('12', '17151'), ('12', '1715l'),
    }


@pytest.mark.parametrize('heading,body', [
    ('Document not Found', 'The document you were looking for does not exist.'),
    ('Document not Found', 'A misleading reference says 12 USC 17151: Requested section.'),
    ('Search Results', '12 USC 17151: Search result is not a section document.'),
    ('12 USC 1715l: Housing for moderate income and displaced families', 'A reference to 12 USC 17151: does not change identity.'),
    ('42 USC 17151: Another title', 'Wrong title.'),
    ('12 USC 171510: Another section', 'Wrong section.'),
])
def test_only_exact_requested_page_identity_is_accepted(heading, body):
    raw = f'<html><title>{heading}</title><body>{body}</body></html>'.encode()
    with pytest.raises(R.StatuteIdentityMismatch, match='requested 12 USC 17151'):
        R.statute_text(raw, '12', '17151')


def test_valid_unicode_heading_keeps_full_evidence_text():
    raw = '<html><title>42 USC 300g–6: Prohibition on use of lead pipes</title><body>Full operative legal text.</body></html>'.encode()
    text = R.statute_text(raw, '42', '300g-6')
    assert '300g–6' in text
    assert 'Full operative legal text.' in text
    with pytest.raises(R.StatuteIdentityMismatch):
        R.statute_text(raw, '42', '300g')


@pytest.mark.parametrize('active', [False, True])
def test_unavailable_exact_statute_holds_new_candidate_but_fails_active_refresh(tmp_path, monkeypatch, active):
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

    def collect(*args):
        return R.statute_text(b'<html><title>Document not Found</title><body>The document does not exist.</body></html>', '12', '17151')

    classifier = Mock(side_effect=AssertionError('unavailable evidence must not be classified'))
    monkeypatch.setattr(R, 'collect', collect)
    monkeypatch.setattr(R, 'classify', classifier)
    assert R.main(['--root', str(tmp_path), '--keys', KEY, '--model', 'fixture', '--transport', 'api']) == int(active)
    classifier.assert_not_called()
    generation = json.loads(next((tmp_path / 'audits/sentinel_admissions').glob('*/generation.json')).read_text())
    if active:
        assert generation['status'] == 'FAIL'
        assert 'requested 12 USC 17151' in generation['errors'][KEY]
        assert published.read_bytes() == before
    else:
        assert generation['status'] == 'PASS'
        decision = generation['decisions'][KEY]
        assert decision['disposition'] == 'hold'
        assert decision['confidence'] == 'low'
        assert decision['categories'] == []
        assert decision['evidence'] == []
        assert 'requested 12 USC 17151' in decision['rationale']
        assert generation['bills'] == {}
        assert generation['memberships'] == []
