"""Hop rows are bit-identical to the ones pair() produced before the cold-path round.

Three changes feed a hop row: ExposureIndex.overlaps (an exact test that skips two people who never stood in one
workplace together), ExposureIndex.pair_row (the part of pair() a row reads) and payroll_people kept on the work index.
Each is checked here against the code it replaced, on seeded worlds that carry every kind of evidence the work index
takes in: directory, payroll-row and payroll-trajectory episodes, report-period (modeled) and presence-within-report
(uncertain) windows, shared roles and interns, duplicates, workplace teams under a parent, kappa 0 and explicit kappas,
episodes that run past as_of, saturating pairs, touching and one-day overlaps and old, stale contact. Every comparison
is on the bytes of the floats, and each check has a negative control: a deliberately wrong replacement must be caught.
"""
from array import array
from bisect import bisect_left
from datetime import date, timedelta
import functools
import math
import random
import struct
import sys

import pytest

from fec.staffer_network import closeness_math
from fec.staffer_network.closeness_math import ExposureIndex
from fec.staffer_network.member_context import MemberContext
from fec.staffer_network.relationship_uncertainty import uncertainty
from fec.surfaces import staffer_closeness_routes as closeness
from fec.surfaces.staffer_closeness_routes import ClosenessRoutes, _Meter, _Neighbors, _RowCache, _hop_cost
from fec.surfaces.staffer_distinct_routes import evidence_contexts
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.staffer_work_sources import payroll_people
from fec.surfaces.test_staffer_closeness_bidirectional import (
    AS_OF, random_queries, settled_options, stripped, synthetic_router)

CUTOFF = date.fromisoformat(AS_OF)
TITLES = ('Legislative Assistant', 'Staff Assistant', 'Chief of Staff', 'SHARED EMPLOYEE', 'INTERN FROM JAN. 12')


def bits(value):
    """The float's bytes: 0.0 and -0.0, and every last digit, are different here."""
    return struct.pack('<d', value)


# --------------------------------------------------------------------------- the worlds
def rich_world(seed, people=110, offices=10):
    """A seeded world with every kind of evidence the work index takes in (see the module docstring)."""
    rng = random.Random(seed)
    nodes, episodes = {}, []
    for i in range(offices):
        kind = 'committee' if i % 7 == 3 else 'leadership' if i % 7 == 5 else 'member_office'
        nodes['office:%02d' % i] = {'kind': 'container', 'container_kind': kind, 'chamber': 'senate' if i % 3 == 0 else 'house',
                                    'member_bioguide': 'M%02d' % i, 'label': 'Office %d' % i}
    nodes['office:00']['workplace_kappa'] = .4
    nodes['office:01']['workplace_kappa'] = 0        # an overlap here still gives no exposure
    teams = {}
    for parent in ('office:02', 'office:04'):
        for letter in 'ab':
            teams['%s:team-%s' % (parent, letter)] = parent
            nodes['%s:team-%s' % (parent, letter)] = {'kind': 'container', 'container_kind': 'member_office', 'chamber': 'house',
                                                      'parent_workplace_id': parent, 'label': 'Team %s%s' % (parent, letter)}
    workplaces = [w for w, n in nodes.items() if n['kind'] == 'container']
    ids = ['p%03d' % i for i in range(people)]
    for p in ids:
        nodes[p] = {'kind': 'person', 'label': 'Person ' + p[1:], 'member_ids': [], 'committee_ids': [], 'policy_ids': []}
    for p in rng.sample(ids, 6):
        nodes[p]['name_status'] = 'UNRESOLVED_SOURCE_NAME'     # not a route node unless unresolved people are included
    first = date(2008, 1, 1)

    def add(person, workplace, start, length, kind=None, title=None):
        kind = kind or rng.choices(('directory', 'payroll', 'trajectory'), (.6, .25, .15))[0]
        row = {'person_id': person, 'employer_id': workplace, 'start': start.isoformat(),
               'end_exclusive': (start + timedelta(days=length)).isoformat(), 'employment_verified': True}
        if kind == 'directory':
            row.update(provenance={'work_route_source': 'politico_profile_history'}, title=title or rng.choice(TITLES))
        elif kind == 'payroll':
            modeled = rng.random() < .5
            row.update(provenance={'method': 'payroll-identity-rows-v1'}, title=title or rng.choice(TITLES),
                       date_precision='REPORT_PERIOD' if modeled else 'DAY',
                       date_evidence='presence_within_report' if modeled and rng.random() < .6 else None,
                       review_only=rng.random() < .3)
        else:
            row.update(provenance={'source': 'verified_static_trajectory',
                                   'stint': {'title_last': title or 'Press Secretary', 'container_kind': 'member_office', 'chamber': 'house'}},
                       review_only=True)
        episodes.append(row)
        return row

    horizon = (CUTOFF - first).days
    for p in ids:
        for _ in range(rng.randint(1, 5)):
            workplace = rng.choice(sorted(teams)) if rng.random() < .15 else rng.choice(workplaces)
            start = first + timedelta(days=rng.randrange(horizon + 60))      # a few start after as_of, and are left out
            length = rng.randint(1, 1600)                                    # and a few run past it, and are cut there
            row = add(p, workplace, start, length)
            if rng.random() < .1:                                            # duplicates change nothing
                episodes.append(dict(row))
            if workplace in teams and rng.random() < .7:                     # the parent as well, same days: the team wins
                add(p, teams[workplace], start, length)
    # touching (no day together), one-day overlaps and saturating duos, each in a workplace of its own
    for k in range(6):
        for name in ('touch', 'oneday', 'duo'):
            nodes['office:%s-%d' % (name, k)] = {'kind': 'container', 'container_kind': 'member_office', 'chamber': 'house',
                                                 'member_bioguide': 'X%s%d' % (name[:2], k), 'label': '%s %d' % (name, k)}
        day = first + timedelta(days=rng.randrange(horizon - 800))
        a, b, c, d, e, f = rng.sample(ids, 6)
        add(a, 'office:touch-%d' % k, day, 200, 'directory', 'Staff Assistant')
        add(b, 'office:touch-%d' % k, day + timedelta(days=200), 200, 'directory', 'Staff Assistant')
        add(c, 'office:oneday-%d' % k, day, 200, 'directory', 'Staff Assistant')
        add(d, 'office:oneday-%d' % k, day + timedelta(days=199), 200, 'directory', 'Staff Assistant')
        add(e, 'office:duo-%d' % k, date(2010 + k, 1, 1), 3000, 'directory', 'Staff Assistant')
        add(f, 'office:duo-%d' % k, date(2010 + k, 1, 1), 3000, 'directory', 'Staff Assistant')
    router = AdvocacyRouter(nodes, episodes, 'rich-%d' % seed, AS_OF)
    members = ['M%02d' % i for i in range(offices)]
    router.member_context = MemberContext([(members[i], members[(i + 1) % offices], 1 + i % 3) for i in range(offices)])
    return router, ids


SEEDS = [pytest.param(seed, id='seed%d' % seed) for seed in (1, 2, 3, 4)]
OPTION_SETS = [pytest.param((), id='payroll-and-directory'),
               pytest.param((('work_sources', 'directory'),), id='directory-only'),
               pytest.param((('include_historical', False),), id='current-only')]


def world_index(seed, extra=()):
    router, ids = rich_world(seed)
    options = settled_options(router, **dict(extra))
    return router, ids, options, ClosenessRoutes(router).index(options)


@functools.lru_cache(maxsize=None)
def shared_world(seed, extra=()):
    """For the tests that only read the index; a test that changes one builds its own."""
    return world_index(seed, extra)


# --------------------------------------------------------------------------- what the code did before
def old_build(self, person):
    """_Neighbors.build as it was before the cold-path round: every neighbour is priced with the full pair()."""
    cache, allowed, meter = self.cache, self.allowed, self.meter
    index, rows = cache.index, cache.rows
    ids, costs, contexts = [], [], []
    derive = allowed(person)
    candidates = sorted(index.neighbors(person))
    meter.checks += len(candidates)
    for other in candidates:
        if not allowed(other):
            continue
        known = rows.get(other) if derive else None
        if known is not None:
            k = bisect_left(known[0], person)
            if k < len(known[0]) and known[0][k] == person:
                ids.append(other)
                costs.append(known[1][k])
                contexts.append(known[2][k])
            continue
        meter.pairs += 1
        raw = index.pair(*((person, other) if person < other else (other, person)))
        if not raw['strength'] > 0:
            continue
        risk = uncertainty(raw['workplaces'], self.as_of)
        ids.append(other)
        costs.append(_hop_cost(raw['strength'], risk['cost'], self.weight))
        contexts.append(cache.intern(frozenset(evidence_contexts(raw))))
    return tuple(ids), array('d', costs), tuple(contexts)


def build_rows(index, allowed, as_of, weight, order, build=None):
    """Rows for `order` from an empty cache, by the production build or by `build`; returns ({person: row}, meter)."""
    cache, meter = _RowCache(index), _Meter()
    neighbors = _Neighbors(cache, allowed, as_of, weight, meter)
    if build is not None:
        neighbors.build = build.__get__(neighbors)
    for person in order:
        list(neighbors(person))
    return {p: (row[0], row[1].tobytes(), row[2]) for p, row in cache.rows.items()}, meter


def row_mismatches(index, allowed, as_of, weight, order):
    """The persons whose row differs from the old build's (by ids, cost bytes or contexts), and the two meters."""
    new, new_meter = build_rows(index, allowed, as_of, weight, order)
    old, old_meter = build_rows(index, allowed, as_of, weight, order, old_build)
    assert new.keys() == old.keys()
    return [p for p in old if new[p] != old[p]], new_meter, old_meter


def pair_mismatches(index, persons, pair_row=None, overlaps=None):
    """Compare pair_row and overlaps with pair() on every candidate pair of `persons`: (mismatches, cases seen)."""
    pair_row = pair_row or index.pair_row
    overlaps = overlaps or index.overlaps
    wrong, seen, done = [], {}, set()
    for person in persons:
        for other in sorted(index.neighbors(person)):
            key = (person, other) if person < other else (other, person)
            if key in done:
                continue
            done.add(key)
            full, row, overlap = index.pair(*key), pair_row(*key), overlaps(*key)
            cases = set()
            if not overlap:
                cases.add('no overlap')
                if full['strength'] != 0 or full['workplaces']:
                    wrong.append(('skipped a pair that has strength', key))
            elif not full['strength'] > 0:
                cases.add('overlap without strength')
            if bits(full['strength']) != bits(row['strength']) or len(full['workplaces']) != len(row['workplaces']):
                wrong.append(('strength or workplace count', key))
                continue
            for a, b in zip(full['workplaces'], row['workplaces']):
                for field in ('employer_id', 'context_group', 'last_overlap_day'):
                    if a[field] != b[field]:
                        wrong.append((field, key))
                for field in ('effective_exposure_days', 'exposure_weighted_last_overlap_day'):
                    if bits(a[field]) != bits(b[field]):
                        wrong.append((field, key))
            if full['strength'] > 0:
                cases.add('strength')
                if len(full['workplaces']) > 1:
                    cases.add('several workplaces')
                if full['strength'] == 1.:
                    cases.add('saturated')
                if any(w['context_group'] != w['employer_id'] for w in full['workplaces']):
                    cases.add('team context')
                years = uncertainty(full['workplaces'], AS_OF)['weighted_years_since_overlap']
                cases.add('stale (over 5 years)' if years > 5 else 'recent (under 1 year)' if years < 1 else 'between')
                evidence = [e for q in key for e in index.source_episodes.get(q, ())]
                if any(e['modeled_payroll_interval'] for e in evidence):
                    cases.add('modeled report period')
                if any(e.get('date_evidence') == 'presence_within_report' for e in evidence):
                    cases.add('presence within report')
                if any(e['work_source'] == 'payroll_trajectory' for e in evidence):
                    cases.add('payroll trajectory')
            for case in cases:
                seen[case] = seen.get(case, 0) + 1
    return wrong, seen


# --------------------------------------------------------------------------- pair_row and overlaps against pair()
@pytest.mark.parametrize('extra', OPTION_SETS)
@pytest.mark.parametrize('seed', SEEDS)
def test_pair_row_and_the_overlap_test_equal_pair_bit_for_bit(seed, extra):
    router, ids, options, index = shared_world(seed, extra)
    wrong, seen = pair_mismatches(index, ids)
    assert not wrong, wrong[:5]
    assert seen['strength'] > 20, seen
    if not extra:
        # Every case the work index takes in is on the table: a world without them would prove nothing.
        for case in ('no overlap', 'overlap without strength', 'several workplaces', 'saturated', 'team context',
                     'stale (over 5 years)', 'recent (under 1 year)', 'modeled report period', 'presence within report',
                     'payroll trajectory'):
            assert seen.get(case, 0) > 0, (case, seen)
    if extra == (('work_sources', 'directory'),):
        assert seen['no overlap'] > 20, seen


def explicit_index(rows, metadata=None):
    return ExposureIndex([{'person_id': p, 'employer_id': c, 'start': s, 'end_exclusive': e, 'title': 'Staff Assistant'}
                          for p, c, s, e in rows], metadata or {'x': {'container_kind': 'member_office', 'chamber': 'house'}})


def test_touching_tenures_share_no_day_and_a_one_day_overlap_is_found():
    # a: days 10-19, b: 20-29 (touching), c: 19-24 (one day with a, five with b)
    index = explicit_index([('a', 'x', 10, 20), ('b', 'x', 20, 30), ('c', 'x', 19, 25)])
    assert not index.overlaps('a', 'b') and not index.overlaps('b', 'a')
    assert index.pair('a', 'b')['strength'] == 0 and index.pair_row('a', 'b') == {'strength': 0., 'workplaces': []}
    assert index.overlaps('a', 'c') and index.overlaps('c', 'a') and index.overlaps('b', 'c')
    for left, right in (('a', 'c'), ('b', 'c')):
        full, row = index.pair(left, right), index.pair_row(left, right)
        assert full['strength'] > 0 and bits(full['strength']) == bits(row['strength'])
    assert index.pair('a', 'c')['overlap_duration_days'] == 1


def test_the_overlap_test_equals_a_brute_force_search_on_random_segment_lists():
    rng = random.Random(5)
    index = ExposureIndex([])
    checked = {True: 0, False: 0}
    for _ in range(4000):
        for person in ('a', 'b'):
            cells = {}
            for workplace in rng.sample('wxyz', rng.randint(0, 3)):
                segments, day = [], rng.randrange(0, 30)
                for _ in range(rng.randint(1, 4)):
                    start = day + rng.randrange(0, 12)
                    segments.append((start, start + rng.randint(1, 10), 1.))
                    day = segments[-1][1] + rng.randrange(0, 3)        # sorted, never overlapping within one person
                cells[workplace] = segments
            index.cells[person] = cells
        expected = any(max(s1, s2) < min(e1, e2) for w in set(index.cells['a']) & set(index.cells['b'])
                       for s1, e1, _ in index.cells['a'][w] for s2, e2, _ in index.cells['b'][w])
        assert index.overlaps('a', 'b') == expected == index.overlaps('b', 'a')
        checked[expected] += 1
    assert min(checked.values()) > 400, checked
    assert not index.overlaps('a', 'nobody') and not index.overlaps('nobody', 'nobody2')


def test_pair_row_keeps_the_validation_pair_has():
    router, ids, options, index = shared_world(1)
    with pytest.raises(ValueError):
        index.pair_row(ids[0], ids[0])
    with pytest.raises(ValueError):
        index.pair(ids[0], ids[0])
    assert index.pair_row(ids[0], 'nobody') == {'strength': 0., 'workplaces': []}


def test_an_invalid_kappa_is_raised_by_pair_row_where_pair_raises_it():
    metadata = {'x': {'container_kind': 'member_office', 'chamber': 'house', 'workplace_kappa': 7}, 'y': {'container_kind': 'member_office'}}
    index = explicit_index([('a', 'x', 1, 9), ('b', 'x', 1, 9), ('c', 'x', 20, 30), ('a', 'y', 1, 9), ('b', 'y', 1, 9)], metadata)
    for call in (index.pair, index.pair_row):
        with pytest.raises(ValueError, match='kappa'):
            call('a', 'b')
        assert call('a', 'c')['strength'] == 0, 'no day together at x, so the invalid kappa is never read'


# --------------------------------------------------------------------------- rows against the old build
@pytest.mark.parametrize('weight', [0., 1., 2.5])
@pytest.mark.parametrize('as_of', [AS_OF, '2024-01-01', '2027-06-01'], ids=['as-of', 'before-last-overlaps', 'a-year-on'])
@pytest.mark.parametrize('extra', OPTION_SETS)
@pytest.mark.parametrize('seed', SEEDS)
def test_rows_equal_the_old_builds_rows_bit_for_bit(seed, extra, as_of, weight):
    router, ids, options, index = shared_world(seed, extra)
    shuffled = list(ids)
    random.Random(seed).shuffle(shuffled)         # most rows are derived from a finished neighbour's row
    wrong, new_meter, old_meter = row_mismatches(index, lambda p: router._person_allowed(p, options), as_of, weight, shuffled)
    assert not wrong, wrong[:5]
    assert (new_meter.checks, new_meter.pairs) == (old_meter.checks, old_meter.pairs)
    assert 0 < new_meter.pairs_scored <= new_meter.pairs


@pytest.mark.parametrize('seed', SEEDS)
def test_rows_equal_the_old_builds_when_some_people_are_not_allowed(seed):
    router, ids, options, index = shared_world(seed)
    rng = random.Random(seed)
    barred = set(rng.sample(ids, len(ids) // 5))
    for allowed in (lambda p: p not in barred, lambda p: router._person_allowed(p, options) and p not in barred):
        order = list(ids)
        rng.shuffle(order)
        wrong, new_meter, old_meter = row_mismatches(index, allowed, AS_OF, 1., order)
        assert not wrong, wrong[:5]
        assert (new_meter.checks, new_meter.pairs) == (old_meter.checks, old_meter.pairs)


def test_the_counters_mean_what_they_always_meant_and_pairs_scored_counts_only_arithmetic():
    router, ids, options, index = shared_world(2)
    rows, meter = build_rows(index, lambda p: True, AS_OF, 1., ids)
    total = sum(len(index.neighbors(p)) for p in ids)
    assert meter.checks == total, 'neighbor_checks is still the candidates considered'
    assert meter.pairs == total // 2, 'each pair is priced once, whichever endpoint is expanded first'
    overlapping = {(min(p, q), max(p, q)) for p in ids for q in index.neighbors(p) if index.overlaps(p, q)}
    assert meter.pairs_scored == len(overlapping) and 0 < meter.pairs_scored < meter.pairs


def test_a_route_reports_pairs_scored_beside_neighbor_checks_and_a_warm_repeat_scores_nothing():
    router, ids, options, index = world_index(3)
    routes = ClosenessRoutes(router)
    request = settled_options(router, route_count=1)
    first = routes.route(ids[3], (ids[70],), request)
    assert first['neighbor_checks'] > 0 and 0 < first['pairs_scored'] < first['neighbor_checks']
    routes.cache.clear()
    again = routes.route(ids[3], (ids[70],), request)
    assert (again['neighbor_checks'], again['pairs_scored']) == (0, 0)
    assert stripped(again) == stripped(first)


# --------------------------------------------------------------------------- whole answers
def test_route_answers_are_byte_identical_to_the_old_builds(monkeypatch):
    compared = 0
    for seed, quantize in ((11, 0), (12, 90)):
        router, ids, offices = synthetic_router(seed, people=170, offices=14, quantize=quantize)
        rng = random.Random(seed)
        for origin, targets, office in random_queries(router, ids, offices, rng, 22, 10):
            options = settled_options(router, route_count=rng.choice((1, 3)), uncertainty_weight=rng.choice((0., 1., 2.5)),
                                      **({'_target_office': office} if office else {}))
            got = ClosenessRoutes(router).route(origin, targets, options)
            with monkeypatch.context() as patch:
                patch.setattr(closeness._Neighbors, 'build', old_build)
                expected = ClosenessRoutes(router).route(origin, targets, options)
            assert stripped(got) == stripped(expected), (seed, origin, targets)
            compared += got['status'] == 'found'
    assert compared >= 15


def test_route_answers_on_the_rich_worlds_are_byte_identical_to_the_old_builds(monkeypatch):
    compared = 0
    for seed in (1, 2):
        router, ids = rich_world(seed)
        rng = random.Random(seed)
        router.operational_staffer_ids = set(rng.sample(ids, 70))       # the rest are routable only through payroll evidence
        for _ in range(30):
            origin, target = rng.sample(sorted(router.operational_staffer_ids), 2)
            options = settled_options(router, route_count=rng.choice((1, 3)), uncertainty_weight=rng.choice((0., 1.)))
            got = ClosenessRoutes(router).route(origin, (target,), options)
            with monkeypatch.context() as patch:
                patch.setattr(closeness._Neighbors, 'build', old_build)
                expected = ClosenessRoutes(router).route(origin, (target,), options)
            assert stripped(got) == stripped(expected), (seed, origin, target)
            compared += got['status'] == 'found'
    assert compared >= 10


# --------------------------------------------------------------------------- payroll_people
@pytest.mark.parametrize('extra', OPTION_SETS)
@pytest.mark.parametrize('seed', SEEDS)
def test_payroll_people_is_the_set_every_request_used_to_compute(seed, extra):
    router, ids, options, index = shared_world(seed, extra)
    old = {p for p, rows in index.source_episodes.items() if any(r['work_source'].startswith('payroll') for r in rows)}
    assert index.payroll_people == old and isinstance(index.payroll_people, frozenset)
    assert payroll_people(index) == old
    if not extra:
        assert old and old != set(index.source_episodes), 'some people have payroll evidence and some only directory evidence'


def test_a_request_reads_payroll_people_from_the_index_and_never_scans_the_episodes_again():
    router, ids, options, index = world_index(1)
    expected, scans = payroll_people(index), []

    class Counting(dict):
        def items(self):
            scans.append(1)
            return super().items()
    index.source_episodes = Counting(index.source_episodes)
    routes = ClosenessRoutes(router)
    routes.indexes[(options['include_review'], options['include_unresolved'], options['include_historical'], options['work_sources'])] = index
    for k in range(4):
        routes.route(ids[k], (ids[60 + k],), settled_options(router, limit=100 + k))
    assert scans == [], 'payroll_people is computed once, when the index is built'
    delattr(index, 'payroll_people')            # an index made some other way gets it computed once, on first use
    for k in range(3):
        routes.route(ids[1], (ids[2 + k],), settled_options(router, limit=300 + k))
    assert len(scans) == 1 and index.payroll_people == expected


# --------------------------------------------------------------------------- negative controls: wrong replacements are caught
def test_control_a_row_pair_off_by_one_ulp_in_strength_is_caught():
    router, ids, options, index = shared_world(1)

    def one_ulp_high(left, right):
        row = index.pair_row(left, right)
        return {**row, 'strength': math.nextafter(row['strength'], math.inf) if 0 < row['strength'] < 1 else row['strength']}
    wrong, _ = pair_mismatches(index, ids, pair_row=one_ulp_high)
    assert any(kind == 'strength or workplace count' for kind, _ in wrong), 'a one-ulp difference must be seen'
    assert not pair_mismatches(index, ids)[0], 'and the real pair_row is not flagged by the same check'


def test_control_a_wrong_staleness_day_is_caught_in_the_pairs_and_in_the_rows(monkeypatch):
    router, ids, options, index = shared_world(1)
    real = ExposureIndex.pair_row
    allowed = lambda p: router._person_allowed(p, options)

    def a_day_late(self, left, right):
        row = real(self, left, right)
        return {**row, 'workplaces': [{**w, 'exposure_weighted_last_overlap_day': w['exposure_weighted_last_overlap_day'] + 1}
                                      for w in row['workplaces']]}
    assert not pair_mismatches(index, ids)[0] and not row_mismatches(index, allowed, AS_OF, 1., ids)[0]
    with monkeypatch.context() as patch:
        patch.setattr(ExposureIndex, 'pair_row', a_day_late)
        assert any(kind == 'exposure_weighted_last_overlap_day' for kind, _ in pair_mismatches(index, ids)[0])
        assert row_mismatches(index, allowed, AS_OF, 1., ids)[0], 'staleness reaches the hop costs, and the row comparison sees it'


def test_control_an_overlap_test_that_misses_a_one_day_overlap_is_unsound_and_is_caught():
    router, ids, options, index = shared_world(1)
    real = index.overlaps

    def misses_one_day(left, right):
        days = [min(x[1], y[1]) - max(x[0], y[0]) for c in set(index.cells[left]) & set(index.cells[right])
                for x in index.cells[left][c] for y in index.cells[right][c]]
        return real(left, right) and max(days) > 1
    wrong, _ = pair_mismatches(index, ids, overlaps=misses_one_day)
    assert any(kind == 'skipped a pair that has strength' for kind, _ in wrong)


def float_variant(index, left, right, accumulate='sequential', product='left', order='sorted'):
    """pair_row written out again with one float-operation choice changed; the default choices are pair_row's own."""
    a, b = index.cells.get(left, {}), index.cells.get(right, {})
    support, total = [], 0.
    for c in sorted(a.keys() & b.keys(), reverse=order == 'reversed'):
        aa, bb, i, j = a[c], b[c], 0, 0
        exposure, amounts, contributions, factor, census = 0., [], [], None, None
        while i < len(aa) and j < len(bb):
            start, end = max(aa[i][0], bb[j][0]), min(aa[i][1], bb[j][1])
            if end > start:
                if census is None:
                    factor, census = index.kappa(c), index.census[c]
                amount = (factor * aa[i][2] * bb[j][2] if product == 'left' else factor * (aa[i][2] * bb[j][2])) * census.integral(start, end)
                amounts.append(amount)
                contributions.append((start, end, amount))
                exposure += amount
            if aa[i][1] <= bb[j][1]:
                i += 1
            else:
                j += 1
        if accumulate != 'sequential':
            exposure = math.fsum(amounts) if accumulate == 'fsum' else sum(amounts)     # sum() adds with compensation from 3.12
        if exposure > 0:
            total += exposure
            last = closeness_math.overlap_support(contributions)
            support.append({'employer_id': c, 'effective_exposure_days': exposure, 'last_overlap_day': last['last_overlap_day'],
                            'exposure_weighted_last_overlap_day': last['exposure_weighted_last_overlap_day'],
                            'context_group': index.context_group(c)})
    return {'strength': closeness_math.bounded_strength(total, 1.), 'workplaces': support}


def test_control_the_comparison_is_sensitive_to_float_operation_order():
    worlds = [shared_world(seed) for seed in (1, 2, 3, 4)]
    mismatches = lambda **choice: sum(len(pair_mismatches(w[3], w[1], pair_row=lambda l, r, w=w: float_variant(w[3], l, r, **choice))[0]) for w in worlds)
    assert mismatches() == 0, 'written out again with pair_row\'s own float operations, the variant is bit-identical'
    assert mismatches(order='reversed') > 0, 'workplaces summed in another order'
    assert mismatches(product='grouped') > 0, 'kappa * (a * b) instead of (kappa * a) * b'
    assert mismatches(accumulate='fsum') > 0, 'exactly rounded instead of added one by one'
    if sys.version_info >= (3, 12):
        assert mismatches(accumulate='sum') > 0, 'sum() instead of += (compensated since Python 3.12)'


def test_control_a_row_builder_that_skips_the_wrong_pairs_is_caught_by_the_row_comparison(monkeypatch):
    router, ids, options, index = shared_world(2)
    real, calls = ExposureIndex.overlaps, [0]
    allowed = lambda p: router._person_allowed(p, options)

    def drops_every_twentieth(self, left, right):
        calls[0] += 1
        return real(self, left, right) and calls[0] % 20 != 0
    assert not row_mismatches(index, allowed, AS_OF, 1., ids)[0]
    with monkeypatch.context() as patch:
        patch.setattr(ExposureIndex, 'overlaps', drops_every_twentieth)
        assert row_mismatches(index, allowed, AS_OF, 1., ids)[0], 'a lost hop must change some row'


def test_control_a_payroll_people_that_counts_every_source_differs_from_the_real_set():
    router, ids, options, index = shared_world(1)
    assert frozenset(index.source_episodes) != index.payroll_people
