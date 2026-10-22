"""A FRESH classifier answer naming an unknown or repeated scoring category (119:hr:10082 failed the 2026-10-20 run) is the
classifier answering outside its output schema. On a new key that is a retryable hold; on an admitted key the published verdict is
carried forward under the D-162 30-day limit; and the same defect in a cached, carried or published verdict is registry drift
and still fails the refresh."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_sentinel_source_unavailable import (  # noqa: E402  (sibling helpers, as test_press_backlog does)
    BILL_URL, KEY, R, SA, STATUTE, main_again, run, world, write_json_file)

ROW = {'category': 'Payments', 'direction': 1, 'source_url': BILL_URL,
       'quote': 'federal credit unions may offer member payment accounts',
       'rationale': 'New payment powers help credit unions compete for members.'}
UNKNOWN = [{**ROW, 'category': 'Investment Authority'}]
DUPLICATE = [ROW, ROW]
DEFECTS = [pytest.param(UNKNOWN, "unknown 'Investment Authority'", id='unknown'),
           pytest.param(DUPLICATE, "duplicate 'Payments'", id='duplicate')]


def refused(rows):
    """A fresh classify() whose model answer carries `rows`: validate_decision (the real one) rejects it."""
    value = {'canonical_key': KEY, 'disposition': 'include', 'confidence': 'high', 'rationale': 'fixture',
             'beneficiary_class': 'cu_primary', 'categories': rows}
    return Mock(side_effect=lambda key, texts, categories, **kw: (R.validate_decision(value, key, texts, categories), {}))


def test_the_category_defect_is_its_own_error_type():
    assert issubclass(R.InvalidScoringCategory, ValueError)
    with pytest.raises(R.InvalidScoringCategory, match="unknown or duplicate scoring category: unknown 'Retired'"):
        R.validate_decision({'canonical_key': KEY, 'disposition': 'include', 'confidence': 'high', 'rationale': 'x',
                             'beneficiary_class': 'cu_primary', 'categories': [{**ROW, 'category': 'Retired'}]},
                            KEY, {BILL_URL: ROW['quote']}, ['Payments'])


@pytest.mark.parametrize('rows, detail', DEFECTS)
def test_fresh_category_defect_on_a_new_key_is_a_hold(tmp_path, monkeypatch, rows, detail):
    root, _ = world(tmp_path, monkeypatch, admitted=False, statute=STATUTE)
    monkeypatch.setattr(R, 'classify', refused(rows))
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['errors'] == {} and generation['bills'] == {} and generation['classifier_calls'] == 1
    decision = generation['decisions'][KEY]
    assert (decision['disposition'], decision['authority']) == ('hold', 'automated_classifier_validation_gate')
    assert decision['rationale'] == f'classifier output refused: unknown or duplicate scoring category: {detail}'
    assert generation['classifier_invalid_output'] == {KEY: decision['rationale'].split('classifier output refused: ')[1]}


@pytest.mark.parametrize('rows, detail', DEFECTS)
def test_fresh_category_defect_on_an_admitted_key_carries_the_published_verdict(tmp_path, monkeypatch, rows, detail):
    root, checked = world(tmp_path, monkeypatch, admitted=True, age_days=11, statute=STATUTE)
    monkeypatch.setattr(R, 'classify', refused(rows))
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (0, 'PASS', True)
    assert generation['errors'] == {} and generation['classifier_calls'] == 1
    decision = generation['decisions'][KEY]
    assert KEY in generation['bills'] and decision['disposition'] == 'include'
    assert decision['carried_forward'] == 'classifier_invalid_output'
    assert decision['checked_at'] == decision['last_verified_at'] == checked          # never a forged verification date
    assert decision['carried_at'] == generation['checked_at']
    assert detail in generation['classifier_invalid_output'][KEY]
    assert KEY not in generation['source_unavailable_deferred_keys'] and generation['carry_expired_keys'] == []
    assert KEY in SA.load(root)['bills']                                              # the consumer accepts the carry in its window


def test_the_classifier_carry_obeys_the_thirty_day_limit(tmp_path, monkeypatch):
    root, checked = world(tmp_path, monkeypatch, admitted=True, age_days=31, statute=STATUTE)
    monkeypatch.setattr(R, 'classify', refused(UNKNOWN))
    code, generation, _ = run(root)
    assert (code, generation['status']) == (0, 'PASS') and generation['errors'] == {}
    assert KEY not in generation['bills'] and KEY in generation['bill_catalog']
    assert generation['decisions'][KEY]['carry_expired'] == 'classifier_invalid_output'
    assert generation['carry_expired'][KEY]['last_verified_at'] == checked and generation['carry_expired_keys'] == [KEY]
    assert KEY in generation['source_unavailable_deferred_keys']
    assert KEY not in SA.load(root)['bills']


def test_load_drops_a_classifier_carry_past_thirty_days_like_a_source_carry(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=11, statute=STATUTE)
    monkeypatch.setattr(R, 'classify', refused(UNKNOWN))
    assert run(root)[0] == 0
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY]['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    write_json_file(path, document)
    loaded = SA.load(root)
    assert KEY not in loaded['bills'] and loaded['carry_expired_keys'] == [KEY]


def test_a_published_verdict_with_a_bad_category_is_not_carried(tmp_path, monkeypatch):
    # Registry drift: the published verdict names a category the registry no longer has. Carrying it would hide that.
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=11, statute=STATUTE)
    write_json_file(root / 'source_data/legislation/acu_legislation_search_registry.json', {'categories': [{'id': 'Other'}]})
    monkeypatch.setattr(R, 'classify', refused(UNKNOWN))
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert 'not carried forward' in generation['errors'][KEY] and 'InvalidScoringCategory' in generation['errors'][KEY]


def test_a_cached_verdict_category_defect_still_fails(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=False, statute=STATUTE)

    def reused(*args, **kwargs):
        raise R.InvalidScoringCategory('Retired Category', False)

    monkeypatch.setattr(R, 'reusable_decision', reused)
    monkeypatch.setattr(R, 'classify', Mock(side_effect=AssertionError('a cached verdict must not be re-billed')))
    code, generation, _ = run(root)
    assert code == 1 and generation['status'] == 'FAIL' and 'Retired Category' in generation['errors'][KEY]
    assert generation['decisions'].get(KEY) is None


@pytest.mark.parametrize('kind', ['source_unavailable', 'classifier_invalid_output'])
def test_no_llm_does_not_keep_either_kind_of_carry_past_thirty_days(tmp_path, monkeypatch, kind):
    root, _ = world(tmp_path, monkeypatch, admitted=True, age_days=45, statute=STATUTE)
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY].update(carried_forward=kind, carried_at=(datetime.now(timezone.utc) - timedelta(days=1)).isoformat())
    write_json_file(path, document)
    generation = main_again(root, '--no-llm')
    assert generation['status'] == 'PASS' and KEY not in generation['bills']
    assert generation['decisions'][KEY]['carry_expired'] == kind and generation['carry_expired_keys'] == [KEY]
