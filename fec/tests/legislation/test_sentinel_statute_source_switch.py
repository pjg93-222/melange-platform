"""An admitted bill whose evidence changed ONLY in its statute source (House Code to GovInfo, or back) keeps its published verdict.

GovInfo's USCODE edition is older than the House text a verdict was built on, so reclassifying on it adds noise, not signal. The
2026-10-20 run re-billed all 12 admitted bills on the switch and six came back as fresh holds. The reuse must win over a
checkpoint that already holds such a fresh verdict, must make no billed call, and must never carry a verdict whose BILL text
changed."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from test_sentinel_source_unavailable import (  # noqa: E402  (sibling helpers, as test_press_backlog does)
    BILL, BILL_URL, GOVINFO_URL, KEY, MAINTENANCE, SA, STATUTE, Page, R, evidence, govinfo_page, main_again, run,
    write_json_file, world)

MODEL, TRANSPORT = 'fixture', 'api'
PARAMETERS = {'policy': R.POLICY, 'model': MODEL, 'transport': TRANSPORT}
HOUSE_URL = 'https://uscode.house.gov/view.xhtml?req=' + R.urllib.parse.quote('(title:12 section:1757 edition:prelim)')


def seal(root):
    """Give the published House-era verdict the fingerprint a real run would have recorded for its own evidence."""
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    decision = document['decisions'][KEY]
    decision.update(evidence_fingerprint=R.evidence_fingerprint(R.bound_decision_texts(decision), model=MODEL, transport=TRANSPORT),
                    fingerprint_schema=R.FINGERPRINT_SCHEMA, fingerprint_parameters=PARAMETERS,
                    classifier={'model': MODEL, 'transport': TRANSPORT})
    write_json_file(path, document)
    return decision


def current_evidence(root, page):
    """The records and texts collect() produces for this bill once GovInfo serves 12 USC 1757."""
    store = root / 'checkpoint_evidence'
    store.mkdir(exist_ok=True)
    bill = evidence(store, BILL_URL, BILL)
    statute = evidence(store, GOVINFO_URL, page, kind='statute', title='12', section='1757', statute_source='govinfo_uscode',
                       edition='USCODE-2024', resolved_from='https://www.govinfo.gov/link/uscode/12/1757', house_unavailable=True)
    texts = {BILL_URL: BILL.decode(), GOVINFO_URL: R.govinfo_statute_text(page, '12', '1757')}
    return [bill, statute], texts


def checkpoint_hold(root, page):
    """A fresh hold on the GovInfo evidence, as the 2026-10-20 run left in the checkpoint: its fingerprint matches the current
    evidence, so reusable_decision would hand it back."""
    records, texts = current_evidence(root, page)
    hold = {'canonical_key': KEY, 'disposition': 'hold', 'confidence': 'low', 'rationale': 'GovInfo 2024 page says the section ceased',
            'categories': [], 'authority': 'automated_official_evidence_policy', 'policy': R.POLICY,
            'checked_at': '2026-10-20T20:12:43+00:00', 'evidence': records,
            'evidence_fingerprint': R.evidence_fingerprint(texts, model=MODEL, transport=TRANSPORT),
            'fingerprint_schema': R.FINGERPRINT_SCHEMA, 'fingerprint_parameters': PARAMETERS,
            'classifier': {'model': MODEL, 'transport': TRANSPORT}}
    write_json_file(root / 'data/legislation/sentinel_admission_checkpoint.json',
                    {'schema': 'sentinel-admission-checkpoint-v1', 'decisions': {KEY: hold}, 'errors': {}})


def admitted_world(tmp_path, monkeypatch, **kwargs):
    page = govinfo_page()
    root, checked = world(tmp_path, monkeypatch, admitted=True, govinfo=page, **kwargs)   # House down, GovInfo serves the section
    published = seal(root)
    return root, checked, page, published


def assert_reused(generation, root, checked):
    decision = generation['decisions'][KEY]
    assert (generation['status'], generation['errors']) == ('PASS', {}) and KEY in generation['bills']
    assert decision['disposition'] == 'include' and decision['statute_source_switch_reuse'] is True
    assert generation['classifier_calls'] == 0 and generation['statute_source_switch_reused'] == 1
    assert decision['checked_at'] == generation['checked_at'] != checked                 # re-verified: normal checked_at semantics
    assert not {'carried_forward', 'carried_at', 'last_verified_at', 'carried_forward_no_llm'} & set(decision)
    assert [e['url'] for e in decision['evidence'] if e.get('kind') == 'statute'] == [GOVINFO_URL]      # bound to the new evidence
    assert KEY in SA.load(root)['bills']


def test_the_published_verdict_wins_over_a_fresh_checkpoint_hold(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch)
    checkpoint_hold(root, page)
    code, generation, installed = run(root)
    assert (code, installed) == (0, True)
    assert_reused(generation, root, checked)
    assert generation['decisions'][KEY]['categories'] == published['categories']       # the verdict itself is the published one
    assert generation['decisions'][KEY]['rationale'] == published['rationale']
    checkpoint = json.loads((root / 'data/legislation/sentinel_admission_checkpoint.json').read_text(encoding='utf-8'))
    assert checkpoint['decisions'][KEY]['disposition'] == 'include'                   # the fresh hold no longer sits in the checkpoint


def test_the_reuse_is_free_under_no_llm_and_is_not_a_blind_carry(tmp_path, monkeypatch):
    root, checked, page, _ = admitted_world(tmp_path, monkeypatch)
    checkpoint_hold(root, page)
    generation = main_again(root, '--no-llm')
    assert_reused(generation, root, checked)


def test_the_next_run_is_a_plain_cache_hit_and_the_switch_back_reuses_again(tmp_path, monkeypatch):
    root, checked, page, _ = admitted_world(tmp_path, monkeypatch)
    first = main_again(root)
    assert_reused(first, root, checked)
    second = main_again(root)                                      # same GovInfo evidence: the new fingerprint matches
    assert second['statute_source_switch_reused'] == 0 and second['classifier_calls'] == 0 and KEY in second['bills']
    assert second['decisions'][KEY]['statute_source_switch_reuse'] is True and second['decisions'][KEY]['classifier']['cache_hit']
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(STATUTE)))   # House Code is back
    third = main_again(root)
    assert third['statute_source_switch_reused'] == 1 and third['classifier_calls'] == 0
    assert [e['url'] for e in third['decisions'][KEY]['evidence'] if e.get('kind') == 'statute'] == [HOUSE_URL]


def test_a_bill_whose_text_changed_is_reclassified_not_reused(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch, bill_now=BILL + b' Amended.')
    answer = {**published, 'rationale': 'Re-decided on the amended text.'}
    classify = Mock(return_value=(answer, {'transport': TRANSPORT, 'model': MODEL}))
    monkeypatch.setattr(R, 'classify', classify)
    code, generation, _ = run(root)
    classify.assert_called_once()
    assert generation['classifier_calls'] == 1 and generation['statute_source_switch_reused'] == 0
    assert 'statute_source_switch_reuse' not in generation['decisions'][KEY]


def test_with_no_published_verdict_to_reuse_the_checkpoint_is_still_used(tmp_path, monkeypatch):
    # A new key has nothing to reuse; the checkpoint behaves exactly as before.
    root, _ = world(tmp_path, monkeypatch, admitted=False, govinfo=govinfo_page())
    checkpoint_hold(root, govinfo_page())
    generation = main_again(root)
    assert generation['statute_source_switch_reused'] == 0 and generation['decisions'][KEY]['disposition'] == 'hold'
    assert generation['classifier_calls'] == 0


def test_a_clean_run_reports_zero(tmp_path, monkeypatch):
    root, _ = world(tmp_path, monkeypatch, admitted=False, statute=MAINTENANCE)
    assert run(root)[1]['statute_source_switch_reused'] == 0


# ---- the rule itself ----------------------------------------------------------------------------------------------

def setup_rule(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch)
    records, texts = current_evidence(root, page)
    return published, texts, records


def rule(published, texts, records, categories=('Payments',), **kwargs):
    return R.statute_source_switch_reuse(KEY, published, texts, records, list(categories),
                                         model=kwargs.get('model', MODEL), transport=kwargs.get('transport', TRANSPORT))


def test_the_rule_reuses_when_only_the_statute_source_moved(tmp_path, monkeypatch):
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    decision, meta = rule(published, texts, records)
    assert decision['statute_source_switch_reuse'] is True and decision['categories'] == published['categories']
    assert meta['statute_source_switch_reuse'] is True and meta['model'] == MODEL


def test_the_rule_declines_unless_the_bill_text_and_cited_sections_are_identical(tmp_path, monkeypatch):
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    assert rule(published, {**texts, BILL_URL: texts[BILL_URL] + ' Amended.'}, records) is None            # bill text changed
    assert rule(published, {k: v for k, v in texts.items() if k != BILL_URL}, records) is None             # a version vanished
    other = [dict(r, section='1758') if r.get('kind') == 'statute' else r for r in records]
    assert rule(published, texts, other) is None                                                           # other cited section
    assert rule(published, texts, [r for r in records if r.get('kind') != 'statute']) is None              # a section dropped


def test_the_rule_declines_when_nothing_moved_or_the_same_source_changed_its_text(tmp_path, monkeypatch):
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    house = next(r for r in published['evidence'] if r.get('kind') == 'statute')
    same_source = [r for r in records if r.get('kind') != 'statute'] + [dict(house)]
    changed = {**{k: v for k, v in texts.items() if k != GOVINFO_URL}, house['url']: 'The House text was amended in place.'}
    assert rule(published, changed, same_source) is None            # same host, different text: a real change, not a switch
    unchanged = {**{k: v for k, v in texts.items() if k != GOVINFO_URL}, house['url']: R.bound_decision_texts(published)[house['url']]}
    assert rule(published, unchanged, same_source) is None          # identical evidence is the ordinary cache hit, not this rule


def test_the_rule_needs_the_published_include_under_the_same_model_and_policy(tmp_path, monkeypatch):
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    assert rule(published, texts, records, model='another-model') is None
    assert rule(published, texts, records, transport='claude-cli') is None
    assert rule({**published, 'policy': 'older-policy'}, texts, records) is None
    assert rule({**published, 'disposition': 'hold'}, texts, records) is None
    assert rule({**published, 'carry_expired': 'source_unavailable'}, texts, records) is None
    assert rule(None, texts, records) is None
    assert rule({k: v for k, v in published.items() if k != 'evidence_fingerprint'}, texts, records) is None


def test_the_rule_declines_when_a_category_quote_cited_the_old_statute(tmp_path, monkeypatch):
    # The quote must still be in the current official documents; if it only existed in the House statute text, the verdict is
    # re-decided rather than carried onto evidence it does not rest on.
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    house = next(r for r in published['evidence'] if r.get('kind') == 'statute')
    row = {**published['categories'][0], 'source_url': house['url'], 'quote': 'A federal credit union shall have power to lend.'}
    assert rule({**published, 'categories': [row]}, texts, records) is None


def test_a_tampered_published_evidence_file_is_still_an_integrity_error(tmp_path, monkeypatch):
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    house = next(r for r in published['evidence'] if r.get('kind') == 'statute')
    Path(house['path']).write_bytes(b'<html><title>12 USC 1757: Powers</title><body>tampered</body></html>')
    with pytest.raises(ValueError, match='evidence hash mismatch'):
        rule(published, texts, records)


def test_the_rule_declines_when_one_section_moved_but_another_changed_in_place(tmp_path, monkeypatch):
    # 12 USC 1757 moved from House to GovInfo; 12 USC 1758 stayed on House but its text was amended there. That second change is
    # evidence, so the verdict is re-decided; with 1758 unchanged the same switch is reused.
    published, texts, records = setup_rule(tmp_path, monkeypatch)
    store = tmp_path / 'extra'
    store.mkdir()
    url = HOUSE_URL.replace('1757', '1758')
    old = evidence(store, url, b'<html><title>12 USC 1758: Other</title><body>Original wording.</body></html>',
                   kind='statute', title='12', section='1758')
    both = {**published, 'evidence': published['evidence'] + [old]}
    both['evidence_fingerprint'] = R.evidence_fingerprint(R.bound_decision_texts(both), model=MODEL, transport=TRANSPORT)
    assert rule(both, {**texts, url: '12 USC 1758: Other Amended wording.'}, records + [dict(old)]) is None
    assert rule(both, {**texts, url: R.bound_decision_texts(both)[url]}, records + [dict(old)]) is not None


# ---- the verdict is bound to the evidence it was CLASSIFIED on (classified_statute) ---------------------------------

AMENDED = STATUTE.replace(b'</body>', b' AMENDED IN PLACE: credit unions may no longer offer this.</body>')
CITED = '12 USC 1757'


def serve_house(monkeypatch, page):
    monkeypatch.setattr(R.urllib.request, 'urlopen', Mock(side_effect=lambda *a, **k: Page(page)))


def text_sha(page):
    text = R.statute_text(page, '12', '1757')
    return R.hashlib.sha256(R.substantive_texts({HOUSE_URL: text})[HOUSE_URL].encode('utf-8')).hexdigest()


def test_the_classified_evidence_of_a_legacy_verdict_is_derived_from_its_bound_evidence(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch)
    assert 'classified_statute' not in published
    expected = {CITED: {'source': 'house', 'sha256': text_sha(STATUTE)}}
    assert R.statute_signature(published['evidence'], R.bound_decision_texts(published)) == expected
    first = main_again(root)                       # House -> GovInfo: reused, and the map is written from the House evidence
    assert first['decisions'][KEY]['classified_statute'] == expected
    second = main_again(root)                      # plain cache hit: carried unchanged, not re-derived from GovInfo
    assert second['decisions'][KEY]['classified_statute'] == expected and second['statute_source_switch_reused'] == 0


def test_a_fresh_classification_records_what_it_was_classified_on(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch, bill_now=BILL + b' Amended.')
    answer = ({**published, 'rationale': 'Re-decided.'}, {'transport': TRANSPORT, 'model': MODEL})
    monkeypatch.setattr(R, 'classify', Mock(return_value=answer))
    generation = run(root)[1]
    sha = R.hashlib.sha256(R.govinfo_statute_text(page, '12', '1757').encode('utf-8')).hexdigest()
    assert generation['decisions'][KEY]['classified_statute'] == {CITED: {'source': 'govinfo', 'sha256': sha}}


def test_house_returning_with_amended_text_is_reclassified_not_reused(tmp_path, monkeypatch, capsys):
    # House text T1 is classified; GovInfo is reused and re-sealed; House returns with T2. The verdict never saw T2.
    root, checked, page, published = admitted_world(tmp_path, monkeypatch)
    first = main_again(root)
    assert first['statute_source_switch_reused'] == 1
    serve_house(monkeypatch, AMENDED)
    answer = ({**published, 'rationale': 'Re-decided on the amended House text.'}, {'transport': TRANSPORT, 'model': MODEL})
    classify = Mock(return_value=answer)
    monkeypatch.setattr(R, 'classify', classify)
    capsys.readouterr()
    third = main_again(root)
    classify.assert_called_once()
    assert third['classifier_calls'] == 1 and third['statute_source_switch_reused'] == 0
    decision = third['decisions'][KEY]
    assert 'statute_source_switch_reuse' not in decision and decision['rationale'] == 'Re-decided on the amended House text.'
    assert decision['classified_statute'] == {CITED: {'source': 'house', 'sha256': text_sha(AMENDED)}}
    out = capsys.readouterr().out
    assert KEY in out and 'declined' in out and CITED in out and 'changed in place' in out      # the reason is printed before the billed call


def test_house_flapping_with_the_classified_text_reuses_free_every_time(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch)
    expected = {CITED: {'source': 'house', 'sha256': text_sha(STATUTE)}}
    for step, house in enumerate([MAINTENANCE, STATUTE, MAINTENANCE, STATUTE]):
        if house is STATUTE:
            serve_house(monkeypatch, STATUTE)
        else:
            serve_house(monkeypatch, MAINTENANCE)
        generation = main_again(root)
        decision = generation['decisions'][KEY]
        assert generation['classifier_calls'] == 0 and KEY in generation['bills'], step
        assert decision['classified_statute'] == expected, step
    assert decision['statute_source_switch_reuse'] is True


def test_a_classifier_carry_is_never_re_sealed_by_the_rule(tmp_path, monkeypatch):
    # Re-sealing would give the carry a fresh checked_at and reset its 30-day clock without any classification.
    root, checked, page, published = admitted_world(tmp_path, monkeypatch, age_days=29)
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY].update(carried_forward='classifier_invalid_output', carried_at=checked, last_verified_at=checked)
    write_json_file(path, document)
    answer = ({**published, 'rationale': 'Re-decided.'}, {'transport': TRANSPORT, 'model': MODEL})
    classify = Mock(return_value=answer)
    monkeypatch.setattr(R, 'classify', classify)
    generation = main_again(root)
    assert generation['statute_source_switch_reused'] == 0 and generation['classifier_calls'] == 1
    assert 'carried_forward' not in generation['decisions'][KEY]        # a real classification, not a re-seal
    records, texts = current_evidence(root, page)
    declined = []
    carried = {**published, 'carried_forward': 'classifier_invalid_output'}
    assert R.statute_source_switch_reuse(KEY, carried, texts, records, ['Payments'], model=MODEL, transport=TRANSPORT,
                                         declined=declined) is None
    assert 'classifier_invalid_output' in declined[0]


def test_a_source_outage_carry_may_be_re_sealed_because_the_evidence_was_collected_again(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch, age_days=29)
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY].update(carried_forward='source_unavailable', carried_at=checked, last_verified_at=checked)
    write_json_file(path, document)
    generation = main_again(root)
    assert_reused(generation, root, checked)


def test_the_rule_still_fails_on_registry_drift_in_the_published_category(tmp_path, monkeypatch):
    root, checked, page, published = admitted_world(tmp_path, monkeypatch)
    write_json_file(root / 'source_data/legislation/acu_legislation_search_registry.json', {'categories': [{'id': 'Other'}]})
    code, generation, installed = run(root)
    assert (code, generation['status'], installed) == (1, 'FAIL', False)
    assert 'InvalidScoringCategory' in generation['errors'][KEY] and generation['classifier_calls'] == 0


def test_a_no_llm_carry_flag_does_not_survive_a_re_verification(tmp_path, monkeypatch):
    assert 'carried_forward_no_llm' in R.CARRY_FIELDS
    root, checked = world(tmp_path, monkeypatch, admitted=True, statute=STATUTE)           # House up: the evidence is unchanged
    seal(root)
    path = root / SA.RELATIVE
    document = json.loads(path.read_text(encoding='utf-8'))
    document['decisions'][KEY]['carried_forward_no_llm'] = True
    write_json_file(path, document)
    generation = main_again(root)
    assert generation['decisions'][KEY]['classifier']['cache_hit'] and 'carried_forward_no_llm' not in generation['decisions'][KEY]
