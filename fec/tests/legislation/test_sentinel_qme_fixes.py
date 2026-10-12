"""QME 2026-10-01 review fixes to refresh_sentinel_admissions.

Duplicate-category signs, bool directions, persistent-error retry starvation, unreadable cached evidence, and the
checkpoint written after the post-loop sweep. Fixture-only: tmp_path roots, no network, no model call.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_sentinel_mixed_directions import (  # noqa: E402
    CATEGORIES, KEY, MODEL, OTHER, TRANSPORT, World, published, R, SA)

CHECKPOINT = Path('data/legislation/sentinel_admission_checkpoint.json')
MH = 'Mortgage & Housing'


def row(category, direction):
    return {'category': category, 'direction': direction}


def test_duplicate_category_with_conflicting_signs_is_mixed():
    value = {'disposition': 'include', 'categories': [row('A', 1), row('A', -1)]}
    with pytest.raises(R.MixedCategoryDirections) as caught:
        R._require_single_direction(value)
    assert R._mixed_rationale(caught.value.directions) == 'mixed category directions (+1 and -1): A -1, A +1'
    # The normal no-duplicate rendering is unchanged, and a repeated same sign is not mixed.
    assert R._mixed_rationale({'B': 1, 'A': -1}) == 'mixed category directions (+1 and -1): A -1, B +1'
    R._require_single_direction({'disposition': 'include', 'categories': [row('A', 1), row('A', 1), row('B', 0)]})


def test_bool_direction_is_not_a_plus_one_and_none_categories_is_empty():
    R._require_single_direction({'disposition': 'include', 'categories': [row('A', True), row('B', -1)]})
    R._require_single_direction({'disposition': 'include', 'categories': [row('A', False), row('B', 1)]})
    R._require_single_direction({'disposition': 'include', 'categories': None})


def test_published_duplicate_category_include_is_held_before_fetch(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, prior={MH: 1})
    dup = dict(world.previous['decisions'][KEY])
    dup['categories'] = [dup['categories'][0], {**dup['categories'][0], 'direction': -1}]
    for relative in (SA.RELATIVE, CHECKPOINT):
        document = json.loads((tmp_path / relative).read_text(encoding='utf-8'))
        document['decisions'][KEY] = dup
        R.write_json(tmp_path / relative, document)
    world.classifier(monkeypatch)  # tripwire
    assert world.run() == 0
    assert world.calls == [] and world.collected == []
    generation, installed, _checkpoint = world.outputs()
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    hold = generation['decisions'][KEY]
    assert hold['disposition'] == 'hold'
    assert hold['rationale'] == f'mixed category directions (+1 and -1): {MH} -1, {MH} +1'
    assert KEY not in generation['bills']


def test_erroring_held_key_is_demoted_in_the_retry_order(tmp_path, monkeypatch):
    """H1 is the oldest hold but raised a generic error last run (it is in the checkpoint's errors); it must not be
    re-picked first. NEW is unseen, so it sorts ahead of every hold; limit 2 leaves room for exactly one hold."""
    world = World(tmp_path, monkeypatch)
    h1, h2, new = '119:hr:7101', '119:hr:7102', '119:hr:7103'

    def hold(key, checked_at):
        return {'canonical_key': key, 'disposition': 'hold', 'confidence': 'low', 'rationale': 'ValueError: x',
                'categories': [], 'authority': 'automated_evidence_availability_gate', 'checked_at': checked_at,
                'evidence': []}
    document = json.loads((tmp_path / CHECKPOINT).read_text(encoding='utf-8'))
    document['decisions'].update({h1: hold(h1, '2026-09-01T00:00:00+00:00'), h2: hold(h2, '2026-09-20T00:00:00+00:00')})
    document['errors'] = {h1: 'RuntimeError: boom'}
    R.write_json(tmp_path / CHECKPOINT, document)
    discovery = tmp_path / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
    discovery.write_text(''.join(json.dumps({'canonical_key': k}) + '\n' for k in (KEY, OTHER, h1, h2, new)),
                         encoding='utf-8')

    def classify(key, texts, categories, **kwargs):
        raise RuntimeError('boom')
    monkeypatch.setattr(R, 'classify', classify)
    R.main(['--root', str(tmp_path), '--model', MODEL, '--transport', TRANSPORT, '--limit', '2'])
    assert world.collected == [OTHER, h2, new]


def test_tampered_cached_evidence_still_raises(tmp_path, monkeypatch):
    """Changed bytes are the integrity alarm (test_sentinel_decision_stability): never silently reclassified."""
    world = World(tmp_path, monkeypatch, prior={MH: 1})
    Path(world.sources[0]['path']).write_bytes(b'<bill>tampered</bill>')
    prior = world.previous['decisions'][KEY]
    with pytest.raises(ValueError, match='hash mismatch'):
        R.reusable_decision(KEY, world.texts, CATEGORIES, published=prior, checkpoint=prior,
                            model=MODEL, transport=TRANSPORT)


def test_unreadable_cached_evidence_falls_through_to_classify(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, prior={MH: 1})
    Path(world.sources[0]['path']).unlink()
    prior = world.previous['decisions'][KEY]
    assert R.reusable_decision(KEY, world.texts, CATEGORIES, published=prior, checkpoint=prior,
                               model=MODEL, transport=TRANSPORT) is None
    world.classifier(monkeypatch, {MH: 1})
    assert world.run() == 0
    assert world.calls == [KEY]
    generation, installed, _checkpoint = world.outputs()
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    assert generation['decisions'][KEY]['disposition'] == 'include'
    assert 'cache_hit' not in generation['decisions'][KEY]['classifier']


def test_other_exceptions_while_loading_cached_evidence_still_escape(monkeypatch):
    def boom(decision):
        raise KeyError('url')
    monkeypatch.setattr(R, 'bound_decision_texts', boom)
    prior = {'evidence_fingerprint': 'x', 'policy': R.POLICY, 'classifier': {'model': MODEL, 'transport': TRANSPORT},
             'evidence': []}
    with pytest.raises(KeyError):
        R.reusable_decision(KEY, {'u': 't'}, CATEGORIES, published=prior, checkpoint=None,
                            model=MODEL, transport=TRANSPORT)


def test_checkpoint_carries_the_post_sweep_hold(tmp_path, monkeypatch):
    """OTHER is an admitted mixed include the --keys run never selects, so only the post-loop sweep holds it. The
    last checkpoint write used to precede the sweep, leaving the checkpoint with the mixed include."""
    world = World(tmp_path, monkeypatch, prior={MH: 1})
    mixed = published(OTHER, {MH: 1, 'CDFI Fund': -1}, world.other_texts, world.other_sources)
    for relative in (SA.RELATIVE, CHECKPOINT):
        document = json.loads((tmp_path / relative).read_text(encoding='utf-8'))
        document['decisions'][OTHER] = mixed
        R.write_json(tmp_path / relative, document)
    world.classifier(monkeypatch)  # tripwire
    assert world.run() == 0
    generation, _installed, checkpoint = world.outputs()
    assert generation['decisions'][OTHER]['disposition'] == 'hold'
    assert checkpoint['decisions'] == generation['decisions']
    assert checkpoint['errors'] == generation['errors']
