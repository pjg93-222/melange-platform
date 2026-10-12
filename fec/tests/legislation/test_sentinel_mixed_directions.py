"""An include whose categories carry both +1 and -1 becomes a retryable hold.

Paul 2026-10-01, "Hold both bills (Recommended)": overall standing allows one direction per member per
measure, so a decision with Mortgage & Housing +1 and CDFI Fund -1 (119:hr:9461), or Examination &
Supervision -1 and NCUA Budget & Insurance +1 (119:hr:9490), cannot give one defensible overall
direction. It is held like a below-high-confidence decision: the same hold shape, the key leaves bills,
and publication is not blocked. A 0 beside one sign is fine. A held bill that was admitted before is
retried: bill_repository_live.json never drops it, so discovery skips it, but bill_catalog returns it to
the pool. Paul 2026-10-01, "Hold before fetch (Recommended)": a PUBLISHED include that fails the rule is
held before collect(), so the hold needs no fetch and no model call and an official-site outage (the
uscode.house.gov "Under Maintenance" page) cannot block it.

Fixture-only: tmp_path roots, no network, no model call, never the live admissions generation.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from legislation import refresh_sentinel_admissions as R
from legislation import sentinel_admissions as SA

KEY = '119:hr:7001'    # the measure under test
OTHER = '119:hr:7002'  # an unrelated published include that a --keys run must keep as it is
CURATED = '119:hr:7003'  # tracked and held, but never admitted by the sentinel
MODEL, TRANSPORT = 'sonnet', 'claude-cli'
SPONSOR = 'F000471'
CATEGORIES = ['CDFI Fund', 'Mortgage & Housing', 'Payments']
QUOTES = {
    'Mortgage & Housing': 'The Board shall expand mortgage lending authority for insured credit unions.',
    'CDFI Fund': 'The Fund shall reduce award eligibility for community development credit unions.',
    'Payments': 'A credit union may settle payment transactions through the described system.',
}
MIXED = {'Mortgage & Housing': 1, 'CDFI Fund': -1}
MIXED_RATIONALE = 'mixed category directions (+1 and -1): CDFI Fund -1, Mortgage & Housing +1'


def bill_url(key):
    congress, kind, number = key.split(':')
    return f'https://www.govinfo.gov/content/pkg/BILLS-{congress}{kind}{number}ih/xml/BILLS-{congress}{kind}{number}ih.xml'


def write_evidence(directory, key, extra=''):
    """Hash-bound official bill text holding every fixture quotation; returns (texts, sources)."""
    directory.mkdir(parents=True, exist_ok=True)
    raw = ('<bill>' + ''.join(f'<section>{quote}</section>' for quote in QUOTES.values())
           + extra + '</bill>').encode()
    sha = hashlib.sha256(raw).hexdigest()
    path = directory / (sha + '.xml')
    path.write_bytes(raw)
    sources = [{'url': bill_url(key), 'path': str(path), 'sha256': sha}]
    return R.bound_decision_texts({'canonical_key': key, 'evidence': sources}), sources


def proposal(key, directions, disposition='include', confidence='high'):
    """A classifier output before validation."""
    value = {'canonical_key': key, 'disposition': disposition, 'confidence': confidence,
             'beneficiary_class': 'shared', 'rationale': 'Fixture verdict on the complete official text.',
             'categories': [{'category': category, 'direction': direction, 'source_url': bill_url(key),
                             'quote': QUOTES[category],
                             'rationale': 'The quoted operative provision sets this direction for credit unions.'}
                            for category, direction in directions.items()]}
    if disposition == 'exclude':
        value.update(exclusion_source_url=bill_url(key), exclusion_quote=QUOTES['Payments'])
    return value


def published(key, directions, texts, sources, model=MODEL):
    """A verdict as a 2026-09-30 refresh published it, hash-bound to its evidence."""
    return {**proposal(key, directions), 'authority': 'automated_official_evidence_policy', 'policy': R.POLICY,
            'checked_at': '2026-09-30T20:09:17.250939+00:00',
            'evidence_fingerprint': R.evidence_fingerprint(texts, model=model, transport=TRANSPORT),
            'fingerprint_schema': R.FINGERPRINT_SCHEMA,
            'fingerprint_parameters': {'policy': R.POLICY, 'model': model, 'transport': TRANSPORT},
            'evidence': sources, 'classifier': {'model': model, 'transport': TRANSPORT, 'response_id': 'fixture'}}


def snapshot(key, areas=()):
    number = key.split(':')[2]
    return {'key': key, 'display': 'H.R.' + number, 'congress': 119, 'title': 'Fixture measure ' + number,
            'summary': 'A fixture summary.', 'introduced_date': '2026-09-01', 'cosponsor_count': 0,
            'url': 'https://www.congress.gov/bill/119th-congress/house-bill/' + number,
            'latest_action': {'text': 'Referred to committee'},
            'sponsor': {'bioguide': SPONSOR, 'name': 'Member One', 'date': '2026-09-01'},
            'original_cosponsors': [], 'cosponsors': [], 'areas': list(areas)}


class World:
    """A root with OTHER published as a +1 include and, when active, KEY published with `prior`.

    Live shape: every admitted bill is also in bill_repository_live.json, which never drops a bill. With
    curated_hold, CURATED is discovered, tracked there and carries a published hold, but was never admitted."""

    def __init__(self, root, monkeypatch, *, prior=None, prior_model=MODEL, moved=False, curated_hold=False):
        self.root = root
        self.texts, self.sources = write_evidence(root / 'evidence/current', KEY)
        self.other_texts, self.other_sources = write_evidence(root / 'evidence/other', OTHER)
        self.other = published(OTHER, {'Payments': 1}, self.other_texts, self.other_sources)
        bills = {OTHER: snapshot(OTHER, ['Payments'])}
        decisions = {OTHER: self.other}
        if prior is not None:
            # moved=True: the published verdict was bound to older evidence than the current text.
            texts, sources = (write_evidence(root / 'evidence/older', KEY, '<section>Since amended.</section>')
                              if moved else (self.texts, self.sources))
            decisions[KEY] = published(KEY, prior, texts, sources, model=prior_model)
            bills[KEY] = snapshot(KEY, list(prior))
        tracked = dict(bills)
        if curated_hold:
            tracked[CURATED] = snapshot(CURATED)
            decisions[CURATED] = {'canonical_key': CURATED, 'disposition': 'hold', 'confidence': 'low',
                                  'rationale': 'ValueError: no complete official operative bill text yet',
                                  'categories': [], 'authority': 'automated_evidence_availability_gate',
                                  'checked_at': '2026-09-13T00:00:00+00:00', 'evidence': []}
        self.previous = {'schema': SA.SCHEMA, 'status': 'PASS', 'checked_at': '2026-09-30T20:09:17.250939+00:00',
                         'policy': R.POLICY, 'bills': bills, 'decisions': decisions, 'bill_catalog': dict(bills),
                         'memberships': [], 'sources': [], 'errors': {}}
        files = {
            SA.RELATIVE: self.previous,
            # As in the live tree after the 2026-09-30 run, the checkpoint equals the published decisions.
            Path('data/legislation/sentinel_admission_checkpoint.json'): {'decisions': decisions, 'errors': {}},
            Path('member_desk_bundle.json'): {'members': []},
            Path('bill_repository_live.json'): {'bills': tracked},
            Path('source_data/legislation/acu_legislation_search_registry.json'):
                {'categories': [{'id': category} for category in CATEGORIES]},
        }
        for relative, value in files.items():
            R.write_json(root / relative, value)
        discovery = root / 'data/legislation/congress_advanced_current/congress_advanced_records.ndjson'
        discovery.parent.mkdir(parents=True, exist_ok=True)
        discovered = (KEY, OTHER, CURATED) if curated_hold else (KEY, OTHER)
        discovery.write_text(''.join(json.dumps({'canonical_key': k}) + '\n' for k in discovered), encoding='utf-8')
        monkeypatch.setattr(R, 'build_ledger_view', lambda bundle: {'roster': {SPONSOR: 'Member One'}})
        monkeypatch.setattr(R, 'collect', self.collect)
        self.collected = []
        self.calls = []
        self.down = set()  # keys whose cited U.S. Code section is on a site that is down

    def collect(self, key, directory, stamp):
        self.collected.append(key)
        if key in self.down:
            # What collect() raises when uscode.house.gov serves its maintenance page (2026-10-01).
            raise R.StatuteIdentityMismatch('official statute document identity mismatch: requested 12 USC 1752; '
                                            "received page title 'Under Maintenance'")
        if key == OTHER:
            return snapshot(OTHER, ['Payments']), self.other_texts, self.other_sources
        return snapshot(key), self.texts, self.sources

    def classifier(self, monkeypatch, directions=None, disposition='include', confidence='high'):
        """directions=None makes every model call a tripwire."""
        def classify(key, texts, categories, **kwargs):
            self.calls.append(key)
            if directions is None:
                raise AssertionError('the classifier was reached')
            return R.validate_decision(proposal(key, directions, disposition, confidence), key, texts, categories), {
                'model': MODEL, 'transport': TRANSPORT}
        monkeypatch.setattr(R, 'classify', classify)

    def run(self, *extra):
        return R.main(['--root', str(self.root), '--keys', KEY, '--model', MODEL, '--transport', TRANSPORT, *extra])

    def outputs(self):
        generation = json.loads(next((self.root / 'audits/sentinel_admissions').glob('*/generation.json')).read_text())
        installed = json.loads((self.root / SA.RELATIVE).read_text(encoding='utf-8'))
        checkpoint = json.loads((self.root / 'data/legislation/sentinel_admission_checkpoint.json').read_text())
        return generation, installed, checkpoint


def assert_mixed_hold(world, generation, installed, checkpoint):
    """The hold shape of the other classifier-validation holds, the key out of bills, publication not blocked."""
    assert generation['status'] == 'PASS' and generation['errors'] == {}
    assert installed == generation
    hold = generation['decisions'][KEY]
    assert set(hold) == {'canonical_key', 'disposition', 'confidence', 'rationale', 'categories', 'authority',
                         'checked_at', 'evidence'}
    assert (hold['disposition'], hold['confidence'], hold['authority']) == (
        'hold', 'low', 'automated_classifier_validation_gate')
    assert hold['rationale'] == MIXED_RATIONALE
    assert hold['categories'] == [] and hold['evidence'] == []
    assert hold['checked_at'] == generation['checked_at']
    assert checkpoint['decisions'][KEY] == hold and checkpoint['errors'] == {}
    assert KEY not in generation['bills']
    assert all(row['repository_key'] != KEY for row in generation['memberships'])
    # The unrelated published include is kept exactly as it was, and still projects its membership.
    assert generation['bills'][OTHER] == world.previous['bills'][OTHER]
    assert generation['decisions'][OTHER] == world.other
    assert [(row['repository_key'], row['role']) for row in generation['memberships']] == [(OTHER, 'sponsor')]
    # A hold carries no fingerprint, so a classifying refresh that selects the key asks the model again. That
    # the key is selected again at all is test_held_admitted_bill_is_retried.
    assert R.reusable_decision(KEY, world.texts, CATEGORIES, published=hold, checkpoint=hold,
                               model=MODEL, transport=TRANSPORT) is None


@pytest.mark.parametrize('evidence', ['unchanged', 'moved'])
@pytest.mark.parametrize('site', ['up', 'down'])
def test_published_mixed_include_is_held_before_any_fetch(tmp_path, monkeypatch, site, evidence):
    """The live case (n19_mixed_hold_keys.cmd, a --keys run): the published mixed include is held before collect(),
    so neither a site outage nor moved evidence reaches a fetch or the model. The hold is the one the post-fetch
    paths write, the run is PASS and installed, and the unselected OTHER is kept exactly as it was."""
    world = World(tmp_path, monkeypatch, prior=MIXED, moved=evidence == 'moved')
    if site == 'down':
        world.down.add(KEY)
    world.classifier(monkeypatch)  # tripwire
    assert world.run() == 0
    assert world.calls == [] and world.collected == []
    generation, installed, checkpoint = world.outputs()
    assert_mixed_hold(world, generation, installed, checkpoint)
    assert KEY in generation['bill_catalog']  # admission history stays managed, so its desk rows are removed
    assert not SA.membership_eligible({'repository_key': KEY, 'bioguide': SPONSOR, 'role': 'sponsor'}, generation)


def test_default_refresh_holds_the_mixed_include_before_fetch_and_still_collects_the_others(tmp_path, monkeypatch):
    """A default refresh selects every active bill. The mixed KEY is held with no fetch while its cited statute's
    site is down; the single-direction OTHER is still collected, and its unchanged evidence reuses its verdict."""
    world = World(tmp_path, monkeypatch, prior=MIXED)
    world.down.add(KEY)
    world.classifier(monkeypatch)  # tripwire
    assert R.main(['--root', str(tmp_path), '--model', MODEL, '--transport', TRANSPORT]) == 0
    assert world.collected == [OTHER] and world.calls == []
    generation, installed, checkpoint = world.outputs()
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    hold = generation['decisions'][KEY]
    assert (hold['disposition'], hold['authority'], hold['rationale'], hold['checked_at']) == (
        'hold', 'automated_classifier_validation_gate', MIXED_RATIONALE, generation['checked_at'])
    assert checkpoint['decisions'][KEY] == hold
    assert KEY not in generation['bills'] and KEY in generation['bill_catalog']
    other = generation['decisions'][OTHER]
    assert (other['disposition'], other['evidence_fingerprint'], other['classifier']['cache_source']) == (
        'include', world.other['evidence_fingerprint'], 'published')
    assert generation['bills'][OTHER] == world.previous['bills'][OTHER]


def test_unselected_mixed_include_is_held_too(tmp_path, monkeypatch):
    """QME 2026-10-01: a --keys run selects only KEY. OTHER, an admitted include the run never selects, carries a
    mixed-direction verdict (a legacy or hand-edited one). Without the post-loop sweep it stayed in bills, and
    SA.load refuses the whole generation for it; now it is held with the same shape as KEY."""
    world = World(tmp_path, monkeypatch, prior=MIXED)
    mixed_other = published(OTHER, MIXED, world.other_texts, world.other_sources)
    for relative in (SA.RELATIVE, Path('data/legislation/sentinel_admission_checkpoint.json')):
        document = json.loads((tmp_path / relative).read_text(encoding='utf-8'))
        document['decisions'][OTHER] = mixed_other
        R.write_json(tmp_path / relative, document)
    world.classifier(monkeypatch)  # tripwire
    assert world.run() == 0
    assert world.calls == [] and world.collected == []
    generation, installed, checkpoint = world.outputs()
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    assert generation['bills'] == {} and generation['memberships'] == []
    for key in (KEY, OTHER):
        hold = generation['decisions'][key]
        assert (hold['disposition'], hold['authority'], hold['rationale']) == (
            'hold', 'automated_classifier_validation_gate', MIXED_RATIONALE)
        assert key in generation['bill_catalog']


@pytest.mark.parametrize('site', ['up', 'down'])
def test_single_direction_admitted_include_still_collects(tmp_path, monkeypatch, site):
    """Only a published include that fails the direction rule skips collect(). A single-direction admitted bill is
    collected as before: unchanged evidence reuses its verdict, and an outage on its cited statute still fails the
    run and keeps the last-good generation installed, as the 2026-10-01 run did."""
    world = World(tmp_path, monkeypatch, prior={'Mortgage & Housing': 1, 'CDFI Fund': 0})
    if site == 'down':
        world.down.add(KEY)
    world.classifier(monkeypatch)  # tripwire
    assert world.run() == (0 if site == 'up' else 1)
    assert world.collected == [KEY] and world.calls == []
    generation, installed, checkpoint = world.outputs()
    if site == 'up':
        assert generation['status'] == 'PASS' and installed == generation
        assert generation['decisions'][KEY]['disposition'] == 'include' and KEY in generation['bills']
    else:
        assert generation['status'] == 'FAIL'
        assert generation['errors'][KEY].startswith('StatuteIdentityMismatch: ')
        assert checkpoint['errors'] == generation['errors']
        assert installed == world.previous
        assert generation['decisions'][KEY] == world.previous['decisions'][KEY]


@pytest.mark.parametrize('checkpointed,site', [(None, 'down'), (MIXED, 'down'), (MIXED, 'up')])
def test_new_candidate_still_collects(tmp_path, monkeypatch, checkpointed, site):
    """A key with no published decision is never held before fetch, even when the checkpoint of an interrupted run
    carries a mixed include: only the published verdict is checked. With the site down the candidate takes the
    existing evidence-availability hold; with it up, the checkpoint verdict comes back through reusable_decision
    and the existing post-fetch rule holds it."""
    world = World(tmp_path, monkeypatch)
    if checkpointed is not None:
        R.write_json(tmp_path / 'data/legislation/sentinel_admission_checkpoint.json', {
            'decisions': {**world.previous['decisions'],
                          KEY: published(KEY, checkpointed, world.texts, world.sources)}, 'errors': {}})
    if site == 'down':
        world.down.add(KEY)
    world.classifier(monkeypatch)  # tripwire
    assert world.run() == 0
    assert world.collected == [KEY] and world.calls == []
    generation, installed, checkpoint = world.outputs()
    if site == 'up':
        assert_mixed_hold(world, generation, installed, checkpoint)
        return
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    hold = generation['decisions'][KEY]
    assert (hold['disposition'], hold['authority']) == ('hold', 'automated_evidence_availability_gate')
    assert hold['rationale'].startswith('StatuteIdentityMismatch: ')
    assert generation['decisions'][OTHER] == world.other and KEY not in generation['bills']


@pytest.mark.parametrize('active', [False, True])
def test_fresh_mixed_include_becomes_a_hold(tmp_path, monkeypatch, active):
    # When active, the published verdict was made by another model, so it is not reusable and the key is reclassified.
    world = World(tmp_path, monkeypatch, prior={'Mortgage & Housing': 1} if active else None,
                  prior_model='older-model')
    world.classifier(monkeypatch, MIXED)
    assert world.run() == 0
    assert world.calls == [KEY]
    assert_mixed_hold(world, *world.outputs())


def test_carried_forward_mixed_include_becomes_a_hold_under_no_llm(tmp_path, monkeypatch):
    """--no-llm carries a published verdict past validate_decision when its evidence moved; the rule still holds it,
    now before collect() is reached."""
    world = World(tmp_path, monkeypatch, prior=MIXED, moved=True)
    world.classifier(monkeypatch)  # tripwire
    assert world.run('--no-llm') == 0
    assert world.calls == [] and world.collected == []
    generation, installed, checkpoint = world.outputs()
    assert generation['no_llm'] is True
    assert_mixed_hold(world, generation, installed, checkpoint)


@pytest.mark.parametrize('path', ['fresh', 'reused'])
@pytest.mark.parametrize('directions', [
    {'Mortgage & Housing': 1, 'CDFI Fund': 0},
    {'Mortgage & Housing': -1, 'CDFI Fund': 0},
    {'Mortgage & Housing': -1, 'CDFI Fund': -1},
    {'Mortgage & Housing': 1, 'CDFI Fund': 1},
])
def test_one_sign_with_or_without_neutral_stays_admitted(tmp_path, monkeypatch, path, directions):
    if path == 'reused':
        world = World(tmp_path, monkeypatch, prior=directions)
        world.classifier(monkeypatch)  # tripwire
    else:
        world = World(tmp_path, monkeypatch)
        world.classifier(monkeypatch, directions)
    assert world.run() == 0
    assert world.calls == ([] if path == 'reused' else [KEY])
    generation, installed, _checkpoint = world.outputs()
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    decision = generation['decisions'][KEY]
    assert (decision['disposition'], decision['confidence']) == ('include', 'high')
    assert {row['category']: row['direction'] for row in decision['categories']} == directions
    assert generation['bills'][KEY]['areas'] == list(directions)
    assert (KEY, 'sponsor') in [(row['repository_key'], row['role']) for row in generation['memberships']]


def test_exclude_carrying_mixed_categories_follows_existing_rules(tmp_path, monkeypatch):
    world = World(tmp_path, monkeypatch, prior={'Mortgage & Housing': 1}, prior_model='older-model')
    world.classifier(monkeypatch, MIXED, disposition='exclude')
    assert world.run() == 0
    generation, _installed, _checkpoint = world.outputs()
    decision = generation['decisions'][KEY]
    assert decision['disposition'] == 'exclude' and decision['authority'] == 'automated_official_evidence_policy'
    assert {row['category']: row['direction'] for row in decision['categories']} == MIXED
    assert KEY not in generation['bills'] and generation['errors'] == {}


def test_validator_raises_the_typed_hold_only_after_every_existing_rule():
    texts = {bill_url(KEY): ' '.join(QUOTES.values())}
    with pytest.raises(R.MixedCategoryDirections) as caught:
        R.validate_decision(proposal(KEY, MIXED), KEY, texts, CATEGORIES)
    assert isinstance(caught.value, ValueError)
    assert str(caught.value) == 'mixed category directions (+1 and -1)'
    assert caught.value.directions == MIXED
    # Existing rejections keep their precedence over the new rule.
    with pytest.raises(R.BelowHighConfidence):
        R.validate_decision(proposal(KEY, MIXED, confidence='medium'), KEY, texts, CATEGORIES)
    no_class = {**proposal(KEY, MIXED), 'beneficiary_class': 'unknown'}
    with pytest.raises(ValueError, match='included measure lacks complete scoring inputs'):
        R.validate_decision(no_class, KEY, texts, CATEGORIES)
    invented = proposal(KEY, MIXED)
    invented['categories'][0]['quote'] = 'An invented quotation absent from the official evidence.'
    with pytest.raises(ValueError, match='category evidence quote is not in the supplied official document'):
        R.validate_decision(invented, KEY, texts, CATEGORIES)
    # The classifier's own hold and an exclusion are returned as before.
    hold = proposal(KEY, MIXED, 'hold', 'low')
    assert R.validate_decision(hold, KEY, texts, CATEGORIES)['disposition'] == 'hold'
    assert R.validate_decision(proposal(KEY, MIXED, 'exclude'), KEY, texts, CATEGORIES)['disposition'] == 'exclude'


@pytest.mark.parametrize('directions,admitted', [
    ({'Mortgage & Housing': 1, 'CDFI Fund': 0}, True),
    (MIXED, False),
])
def test_published_mixed_include_is_refused_by_sentinel_admissions_load(tmp_path, directions, admitted):
    """sentinel_admissions.load re-validates every admitted decision, so a generation still carrying a mixed
    include fails closed until it is republished with the hold."""
    texts, sources = write_evidence(tmp_path / 'evidence', KEY)
    now = datetime.now(timezone.utc).isoformat()
    decision = {**published(KEY, directions, texts, sources), 'checked_at': now}
    document = {'schema': SA.SCHEMA, 'status': 'PASS', 'checked_at': now, 'policy': R.POLICY,
                'bills': {KEY: snapshot(KEY, list(directions))}, 'decisions': {KEY: decision},
                'bill_catalog': {KEY: snapshot(KEY, list(directions))}, 'memberships': [], 'sources': sources,
                'errors': {}, 'unresolved_members': [{'key': KEY, 'bioguide': SPONSOR, 'role': 'sponsor'}]}
    R.write_json(tmp_path / SA.RELATIVE, document)
    R.write_json(tmp_path / 'source_data/legislation/acu_legislation_search_registry.json',
                 {'categories': [{'id': category} for category in CATEGORIES]})
    if admitted:
        assert set(SA.load(tmp_path)['bills']) == {KEY}
    else:
        with pytest.raises(R.MixedCategoryDirections):
            SA.load(tmp_path)


@pytest.mark.parametrize('retry', ['default refresh', '--keys'])
@pytest.mark.parametrize('cause', ['mixed', 'mixed, site down', 'below high confidence'])
def test_held_admitted_bill_is_retried(tmp_path, monkeypatch, cause, retry):
    """The live shape: an admitted bill is tracked in bill_repository_live.json, so the discovery pool skips it.
    Once a hold takes it out of bills, bill_catalog still names it and returns it to the pool: the next default
    refresh takes it from the hold-retry quota and asks again, and --keys may name it. A tracked hold that was
    never admitted (CURATED) is not returned. The below-high-confidence hold of an admitted bill (D-073) is
    retried the same way, and so is a mixed hold written before fetch while the statute site was down."""
    if cause.startswith('mixed'):
        world = World(tmp_path, monkeypatch, prior=MIXED, curated_hold=True)
        world.classifier(monkeypatch)  # tripwire: the published mixed verdict is held before any fetch
        if cause == 'mixed, site down':
            world.down.add(KEY)
    else:
        world = World(tmp_path, monkeypatch, prior={'Mortgage & Housing': 1}, prior_model='older-model',
                      curated_hold=True)
        world.classifier(monkeypatch, {'Mortgage & Housing': 1}, confidence='medium')
    assert world.run() == 0
    assert world.collected == ([] if cause.startswith('mixed') else [KEY])
    held, _installed, _checkpoint = world.outputs()
    assert held['decisions'][KEY]['disposition'] == 'hold'
    assert held['decisions'][KEY]['rationale'] == (
        MIXED_RATIONALE if cause.startswith('mixed') else 'below-high-confidence decision (medium)')
    assert KEY not in held['bills'] and KEY in held['bill_catalog']
    tracked = json.loads((tmp_path / 'bill_repository_live.json').read_text(encoding='utf-8'))['bills']
    assert KEY in tracked and CURATED in tracked  # so discovery alone never offers either again

    shutil.rmtree(tmp_path / 'audits/sentinel_admissions')  # run directories are named to the second
    world.collected.clear()
    world.calls.clear()
    world.down.clear()  # the site is back
    world.classifier(monkeypatch, {'Mortgage & Housing': 1})
    extra = ['--keys', KEY] if retry == '--keys' else []
    assert R.main(['--root', str(tmp_path), '--model', MODEL, '--transport', TRANSPORT, *extra]) == 0
    generation, installed, _checkpoint = world.outputs()
    assert world.calls == [KEY]
    assert world.collected == ([KEY] if retry == '--keys' else [OTHER, KEY])
    assert generation['status'] == 'PASS' and generation['errors'] == {} and installed == generation
    decision = generation['decisions'][KEY]
    assert (decision['disposition'], decision['confidence']) == ('include', 'high')
    assert generation['bills'][KEY]['areas'] == ['Mortgage & Housing']
    assert (KEY, 'sponsor') in [(row['repository_key'], row['role']) for row in generation['memberships']]
    # A default refresh also refreshes the active OTHER: its unchanged evidence reuses the published verdict.
    assert (generation['decisions'][OTHER]['disposition'], generation['decisions'][OTHER]['evidence_fingerprint']) == (
        'include', world.other['evidence_fingerprint'])
    assert generation['decisions'][CURATED] == world.previous['decisions'][CURATED]
    assert CURATED not in generation['deferred_keys']


def test_tracked_hold_that_was_never_admitted_stays_out_of_the_pool(tmp_path, monkeypatch):
    """Only catalogued (once admitted) holds return. CURATED keeps the tracked exclusion, so --keys refuses it."""
    world = World(tmp_path, monkeypatch, prior={'Mortgage & Housing': 1}, curated_hold=True)
    world.classifier(monkeypatch)  # tripwire
    with pytest.raises(ValueError, match='canary keys must be eligible discovered measures'):
        R.main(['--root', str(tmp_path), '--keys', CURATED, '--model', MODEL, '--transport', TRANSPORT])
    assert world.collected == [] and world.calls == []
