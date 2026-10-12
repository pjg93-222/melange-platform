"""An office route searches to its covered staff and reports the rest (Paul's ruling D-086, 2026-10-03).

Under the Staffer model (objective=work_time) an office is a set of current staff. One of them without the model's coverage
(no payroll record and not an operational staffer) used to fail the whole request as insufficient_work_coverage. Now an
office route drops the uncovered staff, searches to the covered ones and says who was left out: every office result (found,
unreachable or coverage) carries uncovered_staff_count and uncovered_staff_ids (sorted) and uncovered_basis, the coverage they lack
(payroll_or_roster, or roster alone where the comparison reads directory episodes only, so no payroll evidence can cover anyone). The
request fails as insufficient_work_coverage only when its origin is uncovered or none of the office's staff is covered, with the message and
suggested_objective it always had. An unreachable reason is judged on the covered staff. The rule holds in every search an
office request runs: the spur searches of ranked alternatives and each pass of distinct and independent routes. Person
routes, and a request that is not for an office, are unchanged.

The two routing modules as they were before the change are read from git (BASE), the way test_staffer_unreachable_hint.py
reads its base, so "unchanged" and "as the covered staff alone would give" are comparisons and not claims:

    python -m pytest fec/tests/surfaces/test_staffer_office_covered_staff.py -q
"""
from collections import Counter
import json
import random
import subprocess
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from fec.surfaces import staffer_closeness_routes as closeness
from fec.surfaces.staffer_closeness_routes import ClosenessRoutes
from fec.surfaces.staffer_live_service import LiveService
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.surfaces.test_staffer_closeness_bidirectional import roster, settled_options, synthetic_router
from fec.tests.surfaces.test_staffer_route_jobs import Served, wait_until
from fec.tests.surfaces.test_staffer_work_time_routes import episode, person

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
AS_OF = '2026-09-10'
# The tip this change was built on (staffer-unreachable-hint): the routing modules as they were before any of it.
BASE = 'a9db5fd5fc2c164bdbd591488867008649532d83'
NEW_FIELDS = {'uncovered_staff_count', 'uncovered_staff_ids', 'uncovered_basis'}   # every field an office answer gained
COUNT_FIELDS = {'uncovered_staff_count', 'uncovered_staff_ids'}                    # who was left out
COVERAGE_MESSAGE = ("These people are known, but their records do not meet this model's authenticated staffer coverage "
                    "requirements. Try Ecosystem for documented associations.")
UI = {'objective': 'work_time', 'work_sources': 'payroll_and_directory', 'include_historical': True, 'include_review': True,
      'include_unresolved': False, 'include_policy': False, 'include_user_asserted': True}


# --------------------------------------------------------------------------- the worlds
def office_node(office):
    return {'kind': 'container', 'container_kind': 'member_office', 'member_bioguide': 'M' + office[7:], 'label': 'Office ' + office[7:]}


def build(people, meetings, anchored, operational, labels=None):
    """A router over `people`. Each meeting (office, a, b, start) is two people who worked together there from `start` to
    2019-01-01; `anchored` maps an office to the people with dated work at it in 2020. `operational` is who is covered
    (None: everyone). Nobody has a payroll row, so nobody outside `operational` is covered."""
    nodes = {p: person((labels or {}).get(p, p.upper())) for p in people}
    for office in [row[0] for row in meetings] + list(anchored):
        nodes[office] = office_node(office)
    episodes = [episode(p, office, '2020-01-01', '2021-01-01') for office, staff in anchored.items() for p in staff]
    episodes += [episode(p, office, start, '2019-01-01') for office, a, b, start in meetings for p in (a, b)]
    router = AdvocacyRouter(nodes, episodes, 'covered-staff', AS_OF)
    if operational is not None:
        router.operational_staffer_ids = set(operational)
    return router


OFFICE = 'office:target'
STAFF = ('s1', 's2', 's3', 'u1', 'u2')   # the office's current staff, in id order
COVERED, LEFT_OUT = ('s1', 's2', 's3'), ['u1', 'u2']
OPERATIONAL = ('o', 'm', 's1', 's2', 's3')   # u1 and u2 are not operational staffers
MEETINGS = [('office:o-u1', 'o', 'u1', '2014-01-01'), ('office:o-s1', 'o', 's1', '2018-01-01'),
            ('office:o-s2', 'o', 's2', '2018-07-01'), ('office:o-m', 'o', 'm', '2018-01-01'), ('office:m-s3', 'm', 's3', '2018-01-01')]


def world(operational=OPERATIONAL, twin=False):
    """o reaches s1 and s2 directly and s3 through m. u1, who has no coverage, is the closest of all to o.

    Everyone on the staff has dated work at the office, so every one of them is anchored. `twin` gives u1 the displayed
    name of s1, which the distinct loop treats as one contact."""
    return build(('o', 'm', *STAFF), MEETINGS, {OFFICE: STAFF}, operational, {'u1': 'S1'} if twin else None)


def ask(router, origin='o', staff=STAFF, office=OFFICE, **extra):
    """An office request as the page makes it: every option the page sends, and the office as the destination."""
    return router.route_many(origin, staff, {**UI, '_target_office': office, **extra})


def left_out(result):
    return {key: result[key] for key in sorted(COUNT_FIELDS)}


# --------------------------------------------------------------------------- the base commit
def load_base():
    """The closeness search and the distinct/independent loop as they were at BASE, read from git."""
    loaded = {}
    for name in ('staffer_closeness_routes', 'staffer_distinct_routes'):
        try:
            source = subprocess.run(['git', '-C', str(ROOT), 'show', '%s:fec/surfaces/%s.py' % (BASE, name)],
                                    capture_output=True, check=True).stdout.decode('utf-8')
        except (OSError, subprocess.CalledProcessError):
            return None
        module = types.ModuleType('fec.surfaces.%s_base' % name)
        module.__package__, module.__file__ = 'fec.surfaces', '%s_base.py' % name
        exec(compile(source, '%s@%s' % (name, BASE[:9]), 'exec'), module.__dict__)
        loaded[name] = module
    return SimpleNamespace(closeness=loaded['staffer_closeness_routes'], distinct=loaded['staffer_distinct_routes'])


@pytest.fixture(scope='module')
def base():
    modules = load_base()
    if modules is None:
        pytest.skip('the base commit %s is not in this checkout' % BASE[:9])
    return modules


def base_route_many(base, router, origin, targets, options):
    """route_many as it was at BASE, on `router`: its distinct loop and its closeness search."""
    if not isinstance(getattr(router, 'closeness_routes', None), base.closeness.ClosenessRoutes):
        router.closeness_routes = base.closeness.ClosenessRoutes(router)
    options = router._options(options)
    targets = tuple(p for p in sorted(set(targets)) if p not in options['_excluded_nodes'])
    if options['route_count'] > 1 and options['alternative_strategy'] != 'ranked':
        return base.distinct.distinct_routes(router, origin, targets, options, None)
    return router.closeness_routes.route(origin, targets, options)


def dump(value, drop=(), ordered=False):
    """A dump of a result without its wall-clock time, without pairs_scored (the counter of pairs whose arithmetic ran: a result has it now and the
    base commit's has not, and it is no part of the answer) and without the fields named in `drop`: canonical, or (ordered) in the
    order the keys were put there, which is the order the page's JSON has them in."""
    skipped = {'search_elapsed_ms', 'pairs_scored', *drop}

    def clean(item):
        if isinstance(item, dict):
            return {k: clean(v) for k, v in item.items() if k not in skipped}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    return json.dumps(clean(value), sort_keys=not ordered)


# --------------------------------------------------------------------------- the rule
def test_a_mixed_coverage_office_routes_to_a_covered_staff_member_and_reports_the_rest():
    router = world()
    result = ClosenessRoutes(router).route('o', STAFF, settled_options(router, _target_office=OFFICE))
    assert result['status'] == 'found' and result['optimal']
    assert result['node_ids'] == ['o', 's1'] and result['target'] == 's1'
    assert left_out(result) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': ['u1', 'u2']}
    assert 'unavailable_person_ids' not in result
    # u1 is the closest of all to o: with everyone covered u1 wins, so s1 won above only because u1 was left out.
    everyone = world(operational=None)
    assert ClosenessRoutes(everyone).route('o', STAFF, settled_options(everyone, _target_office=OFFICE))['target'] == 'u1'


def test_the_base_commit_refused_the_mixed_office_that_this_change_routes(base):
    router = world()
    old = base.closeness.ClosenessRoutes(router).route('o', STAFF, settled_options(router, _target_office=OFFICE))
    assert old['status'] == 'insufficient_work_coverage' and old['unavailable_person_ids'] == LEFT_OUT
    assert NEW_FIELDS.isdisjoint(old)
    assert ask(router)['status'] == 'found'


def test_an_office_with_nobody_covered_is_insufficient_work_coverage_with_the_suggested_objective(base):
    result = ask(world(operational=('o', 'm')), route_count=1)
    assert result['status'] == 'insufficient_work_coverage' and not result['optimal']
    assert result['message'] == COVERAGE_MESSAGE and result['suggested_objective'] == 'evidence_cost'
    assert result['unavailable_person_ids'] == sorted(STAFF)
    assert left_out(result) == {'uncovered_staff_count': 5, 'uncovered_staff_ids': sorted(STAFF)}
    # Nothing but the two new fields differs from the base commit's answer.
    old = base_route_many(base, world(operational=('o', 'm')), 'o', STAFF, {**UI, '_target_office': OFFICE, 'route_count': 1})
    assert dump(result, drop=NEW_FIELDS) == dump(old)


@pytest.mark.parametrize('strategy', ['ranked', 'distinct', 'independent'])
@pytest.mark.parametrize('count', [1, 3, 5])
def test_a_fully_covered_office_answers_byte_for_byte_as_the_base_commit_does_apart_from_the_two_new_fields(strategy, count, base):
    request = {**UI, '_target_office': OFFICE, 'route_count': count, 'alternative_strategy': strategy}
    for operational in (None, (*OPERATIONAL, 'u1', 'u2')):   # the router names no operational staffers, or names everyone
        new = world(operational=operational).route_many('o', STAFF, request)
        old = base_route_many(base, world(operational=operational), 'o', STAFF, request)
        assert new['status'] == 'found' and dump(new, drop=NEW_FIELDS) == dump(old)
        assert dump(new, drop=NEW_FIELDS, ordered=True) == dump(old, ordered=True)   # the fields are added, nothing is moved
        for result in (new, *new.get('routes', [])):
            assert left_out(result) == {'uncovered_staff_count': 0, 'uncovered_staff_ids': []}
    assert 'unavailable_person_ids' not in new


@pytest.mark.parametrize('operational,unavailable', [
    (('m', 's1', 's2', 's3'), ['o', 'u1', 'u2']),   # the origin and two of the staff are not covered
    (('m', *STAFF), ['o'])])                         # the origin alone is not, and every staff member is
def test_an_uncovered_origin_behaves_as_before(operational, unavailable, base):
    result = ask(world(operational=operational))
    assert result['status'] == 'insufficient_work_coverage' and result['unavailable_person_ids'] == unavailable
    assert result['message'] == COVERAGE_MESSAGE and result['suggested_objective'] == 'evidence_cost'
    staff_left_out = [p for p in unavailable if p != 'o']
    assert left_out(result) == {'uncovered_staff_count': len(staff_left_out), 'uncovered_staff_ids': staff_left_out}
    old = base_route_many(base, world(operational=operational), 'o', STAFF, {**UI, '_target_office': OFFICE})
    assert dump(result, drop=NEW_FIELDS) == dump(old)


def test_a_request_for_people_that_is_not_an_office_still_fails_on_one_uncovered_person(base):
    router = world()
    result = router.route_many('o', STAFF, {**UI})
    assert result['status'] == 'insufficient_work_coverage' and result['unavailable_person_ids'] == LEFT_OUT
    assert NEW_FIELDS.isdisjoint(result)
    assert dump(result) == dump(base_route_many(base, world(), 'o', STAFF, {**UI}))
    person_route = router.route('o', 'u1', {**UI})
    assert person_route['status'] == 'insufficient_work_coverage' and person_route['unavailable_person_ids'] == ['u1']
    assert NEW_FIELDS.isdisjoint(person_route)
    assert NEW_FIELDS.isdisjoint(router.route('nobody-at-all', 's1', {**UI}))   # unknown_person


def test_people_the_reader_avoids_are_not_counted_as_left_out():
    result = ask(world(), avoid_people=('u1',))
    assert result['status'] == 'found' and left_out(result) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['u2']}
    result = ask(world(), avoid_people=('u1', 'u2', 's1'))
    assert result['target'] == 's2' and left_out(result) == {'uncovered_staff_count': 0, 'uncovered_staff_ids': []}


def test_a_repeated_request_is_served_from_the_cache_with_the_same_left_out_staff_and_never_a_stale_one():
    router = world()
    routes = ClosenessRoutes(router)
    options = settled_options(router, _target_office=OFFICE)
    first, again = routes.route('o', STAFF, options), routes.route('o', STAFF, options)
    assert not first['cache_hit'] and again['cache_hit']
    assert left_out(again) == left_out(first) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}
    # The same covered staff searched, a different set left out: the left-out staff are part of the answer, so of its key.
    other = routes.route('o', ('s1', 's2', 's3', 'u2'), options)
    assert not other['cache_hit'] and left_out(other) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['u2']}
    assert left_out(routes.route('o', STAFF, options)) == left_out(first)
    router.operational_staffer_ids.add('u1')   # u1 gains coverage: a different set of staff is searched
    changed = routes.route('o', STAFF, options)
    assert not changed['cache_hit'] and changed['target'] == 'u1'
    assert left_out(changed) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['u2']}


# --------------------------------------------------------------------------- the same rule in every search
@pytest.mark.parametrize('strategy', ['distinct', 'independent'])
def test_distinct_and_independent_routes_search_to_the_covered_staff_in_every_pass(strategy, base):
    result = ask(world(), route_count=5, alternative_strategy=strategy)
    assert [r['node_ids'] for r in result['routes']] == [['o', 's1'], ['o', 's2'], ['o', 'm', 's3']]
    assert result['routes_returned'] == 3 and result['routes_requested'] == 5
    # The covered staff are used up, and the page is told there is no further route, as it is for a covered office.
    assert result['alternatives_stop_reason'] == 'no_further_route_under_accumulated_exclusions'
    for route in (result, *result['routes']):   # every pass reports the request's count, not the count of the staff it was given
        assert left_out(route) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}
    assert ask(world(), route_count=2, alternative_strategy=strategy)['alternatives_stop_reason'] == 'requested_count'
    # Negative control: the same search under the base commit's loop, which does not keep the dropped staff out of later
    # passes. Its last pass is given only uncovered staff and fails, and a pass whose staff lost a same-name twin counts one
    # fewer. (The twin of s1 is u1: once s1 is used, u1 is blocked as the same contact.)
    request = {**UI, '_target_office': OFFICE, 'route_count': 5, 'alternative_strategy': strategy}
    for twin, stale_counts in ((False, [2, 2, 2]), (True, [2, 1, 1])):
        router = world(twin=twin)
        router.closeness_routes = ClosenessRoutes(router)
        stale = base.distinct.distinct_routes(router, 'o', STAFF, router._options(request), None)
        assert [r['node_ids'] for r in stale['routes']] == [['o', 's1'], ['o', 's2'], ['o', 'm', 's3']]
        assert stale['alternatives_stop_reason'] == 'search_error'
        assert [r['uncovered_staff_count'] for r in stale['routes']] == stale_counts
        current = world(twin=twin).route_many('o', STAFF, request)
        assert current['alternatives_stop_reason'] == 'no_further_route_under_accumulated_exclusions'
        assert [r['uncovered_staff_count'] for r in current['routes']] == [2, 2, 2]


def test_ranked_alternatives_search_to_the_covered_staff_in_every_spur_search():
    result = ask(world(), route_count=5, alternative_strategy='ranked')
    assert result['routes_returned'] == 5
    for route in result['routes']:
        assert route['node_ids'][-1] in COVERED and set(route['node_ids']).isdisjoint(LEFT_OUT)
        assert left_out(route) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}
    assert len({tuple(r['node_ids']) for r in result['routes']}) == 5
    # Spurs that pass through a covered staff member on the way to another are searched too.
    assert any(len(r['node_ids']) == 3 and r['node_ids'][1] in COVERED for r in result['routes'])


@pytest.mark.parametrize('strategy', ['ranked', 'distinct', 'independent'])
@pytest.mark.parametrize('count', [1, 3, 5])
@pytest.mark.parametrize('twin', [False, True])
def test_a_mixed_office_answers_exactly_as_its_covered_staff_alone_would(strategy, count, twin, base):
    request = {**UI, '_target_office': OFFICE, 'route_count': count, 'alternative_strategy': strategy}
    new = world(twin=twin).route_many('o', STAFF, request)
    old = base_route_many(base, world(twin=twin), 'o', COVERED, request)   # the base commit, given only the covered staff
    assert new['status'] == old['status'] == 'found'
    assert dump(new, drop=NEW_FIELDS | {'target_count'}) == dump(old, drop={'target_count'})
    assert new['target_count'] == len(STAFF) and old['target_count'] == len(COVERED)
    assert left_out(new) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}


# --------------------------------------------------------------------------- unreachable, judged on the covered staff
FAR, APART, PROFILE = ('f1', 'u3'), ('a1', 'a2', 'u4'), ('p1', 'u5')
REASONS_COVERED = ('o', 'm', 's1', 's2', 's3', 'f1', 'a1', 'a2', 'p1', 'lone')


def reasons_world(operational=REASONS_COVERED):
    """The first world, plus four requests whose covered staff cannot be reached though an uncovered one could be.

    far:     f1 (covered) is connected only to u3 (not covered), and u3 is next to o.
    apart:   a1 and a2 (covered) are connected to each other and to u4 (not covered); o is next to u4, not to them.
    profile: p1 (covered) has no dated work at the office; u5 (not covered) has, and works with nobody.
    lone:    covered, and connected only to u1, who is not.
    """
    meetings = [*MEETINGS, ('office:o-u3', 'o', 'u3', '2018-01-01'), ('office:o-u4', 'o', 'u4', '2018-01-01'),
                ('office:lone-u1', 'lone', 'u1', '2018-01-01')]
    anchored = {OFFICE: STAFF, 'office:far': FAR, 'office:apart': APART, 'office:profile': ('u5',)}
    return build(('o', 'm', *STAFF, *FAR, *APART, *PROFILE, 'lone'), meetings, anchored, operational)


# case: (origin, office, its staff, the reason the covered staff give, who was left out, what everyone being covered answers)
REASONS = {
    'target_without_work_connections': ('o', 'office:far', FAR, ['u3'], 'found'),
    'no_path_in_work_records': ('o', 'office:apart', APART, ['u4'], 'found'),
    'office_without_work_records': ('o', 'office:profile', PROFILE, ['u5'], 'unreachable'),
    'origin_without_work_connections': ('lone', OFFICE, STAFF, LEFT_OUT, 'found'),
}


@pytest.mark.parametrize('reason', REASONS)
def test_an_unreachable_reason_is_judged_on_the_covered_staff(reason, base):
    origin, office, staff, uncovered, everyone = REASONS[reason]
    result = ask(reasons_world(), origin, staff, office)
    assert result['status'] == 'unreachable' and result['optimal'] and result['unreachable_reason'] == reason
    # With staff left out, an office nobody searched works at is worded for the staff the search could use (SEARCHED_STAFF_MESSAGES).
    said = closeness.SEARCHED_STAFF_MESSAGES.get(reason)
    assert result['message'] == (said or closeness.OFFICE_UNREACHABLE_MESSAGES.get(reason) or closeness.UNREACHABLE_MESSAGES[reason])
    assert (result['message'] == said) == (reason == 'office_without_work_records')
    assert result['suggested_objective'] == 'evidence_cost'
    assert left_out(result) == {'uncovered_staff_count': len(uncovered), 'uncovered_staff_ids': uncovered}
    # Judged on everyone, the same request reads differently: this is not the reason it would have had.
    control = ask(reasons_world(operational=None), origin, staff, office)
    assert control['status'] == everyone and left_out(control) == {'uncovered_staff_count': 0, 'uncovered_staff_ids': []}
    old = base_route_many(base, reasons_world(operational=None), origin, staff, {**UI, '_target_office': office})
    assert dump(control, drop=NEW_FIELDS) == dump(old)   # with everyone covered the answer is the base commit's, byte for byte
    if everyone == 'unreachable':
        assert control['unreachable_reason'] != reason


# --------------------------------------------------------------------------- an office nobody the search could use works at
# The reason office_without_work_records is judged on the staff the search could use: the covered staff the request did not exclude.
# "None of this office's current staff have dated employment at this office" is true only when nobody was left out or avoided,
# because a person left out (no coverage) or avoided can have dated work there, so otherwise the sentence is about the staff the
# search could use (closeness.SEARCHED_STAFF_MESSAGES). The first world is the verifier's scenarios A and B.
NOWHERE = 'office:nowhere'
THERE = ('d1', 'e1')   # dated directory work at the office: d1 is not covered (no payroll row, not an operational staffer), e1 is
AWAY = ('c1', 'c2')    # covered, listed as the office's current staff, and with dated work only at another office
PLAIN = closeness.UNREACHABLE_MESSAGES['office_without_work_records']
SEARCHED = closeness.SEARCHED_STAFF_MESSAGES['office_without_work_records']


def nowhere_world(operational=('x', 'c1', 'c2', 'e1')):
    """x is the origin and x2 a person who is on no office's staff; the rest are named in THERE and AWAY."""
    return build(('x', 'x2', *AWAY, *THERE), [], {NOWHERE: THERE, 'office:away': AWAY}, operational)


def dated_work_at_the_office(router, who):
    return any(row['employer_id'] == NOWHERE and row['start'] < row['end_exclusive'] for row in router.by_person.get(who, []))


def sentence_of(result):
    assert result['status'] == 'unreachable' and result['unreachable_reason'] == 'office_without_work_records', result['status']
    return result['message']


def test_a_person_left_out_for_want_of_coverage_can_have_dated_work_at_the_office():
    # The premise of the page's line ("lack the payroll or staffer-roster coverage this model requires"): left out is not "has no work records".
    router = nowhere_world()
    assert dated_work_at_the_office(router, 'd1') and 'd1' not in router.operational_staffer_ids
    assert not dated_work_at_the_office(router, 'c1') and 'c1' in router.operational_staffer_ids
    result = ask(router, 'x', ('c1', 'd1', 'e1'), NOWHERE)
    assert left_out(result) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['d1']}
    assert result['status'] == 'unreachable' and result['unreachable_reason'] == 'origin_without_work_connections'   # x works with nobody


def test_an_office_the_searched_staff_do_not_work_at_names_this_offices_staff_only_when_nobody_was_left_out_or_avoided():
    router = nowhere_world()
    assert sentence_of(ask(router, 'x', AWAY, NOWHERE)) == PLAIN                          # everyone listed is covered, nobody is avoided
    assert sentence_of(ask(router, 'x', AWAY, NOWHERE, avoid_people=('x2',))) == PLAIN    # a person avoided who is not on the staff changes nothing
    left = ask(router, 'x', (*AWAY, 'd1'), NOWHERE)   # d1 has dated work at the office and no coverage: left out (scenario B)
    assert left_out(left) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['d1']} and sentence_of(left) == SEARCHED
    avoided = ask(router, 'x', (*AWAY, 'e1'), NOWHERE, avoid_people=('e1',))   # e1 is covered and has dated work there: avoided
    assert left_out(avoided) == {'uncovered_staff_count': 0, 'uncovered_staff_ids': []} and sentence_of(avoided) == SEARCHED
    assert sentence_of(ask(router, 'x', (*AWAY, 'e1'), NOWHERE, _excluded_nodes=('e1',))) == SEARCHED   # the same, as the option the loop passes
    both = ask(router, 'x', (*AWAY, 'd1', 'e1'), NOWHERE, avoid_people=('e1',))
    assert left_out(both) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['d1']} and sentence_of(both) == SEARCHED
    # A person who works there and is neither left out nor avoided is a destination: the reason is not this one.
    assert ask(router, 'x', (*AWAY, 'e1'), NOWHERE)['unreachable_reason'] == 'origin_without_work_connections'
    assert PLAIN != SEARCHED and 'this office\'s current staff' in PLAIN and 'this search could use' in SEARCHED


@pytest.mark.parametrize('strategy', ['ranked', 'distinct', 'independent'])
@pytest.mark.parametrize('count', [1, 3, 5])
def test_every_strategy_and_route_count_words_the_office_the_same_way(strategy, count):
    router = nowhere_world()
    request = {'route_count': count, 'alternative_strategy': strategy}
    assert sentence_of(ask(router, 'x', AWAY, NOWHERE, **request)) == PLAIN
    assert sentence_of(ask(router, 'x', (*AWAY, 'd1'), NOWHERE, **request)) == SEARCHED
    assert sentence_of(ask(router, 'x', (*AWAY, 'e1'), NOWHERE, avoid_people=('e1',), **request)) == SEARCHED


def office_sentence_failures():
    """Every way the sentence of an office nobody the search could use works at is untrue or misjudged, over every case of a small world.

    The world has four staff (two covered with no dated work there, d1 and e1 with it) and a person who is on nobody's staff; each
    way of covering them, each set of them named, each set of them avoided (with or without the outsider). The oracle is the data:
    "this office's current staff" claims about everyone named, "the staff this search could use" about the covered staff not avoided.
    """
    failures, tally = [], Counter()
    staff = (*AWAY, *THERE)
    for uncovered_mask in range(16):   # who has no coverage: any of the four
        uncovered = {p for k, p in enumerate(staff) if uncovered_mask >> k & 1}
        router = nowhere_world(operational=('x', *(p for p in staff if p not in uncovered)))
        for named_mask in range(1, 16):
            named = tuple(p for k, p in enumerate(staff) if named_mask >> k & 1)
            for avoided_mask in range(1 << len(named)):
                for outsider in (False, True):
                    avoided = {p for k, p in enumerate(named) if avoided_mask >> k & 1}
                    result = ask(router, 'x', named, NOWHERE, avoid_people=(*sorted(avoided), *(('x2',) if outsider else ())))
                    if result.get('unreachable_reason') != 'office_without_work_records':
                        continue
                    usable = {p for p in named if p not in uncovered and p not in avoided}
                    plain = result['message'] == PLAIN
                    where = (sorted(uncovered & set(named)), sorted(avoided), named)
                    if plain and any(dated_work_at_the_office(router, p) for p in named):
                        failures.append('says nothing in this office\'s current staff works there, but one does: %r' % (where,))
                    if any(dated_work_at_the_office(router, p) for p in usable):
                        failures.append('a staff member the search could use works there, so it cannot be this reason: %r' % (where,))
                    if plain != (not (uncovered & set(named)) and not avoided):
                        failures.append('wrong sentence for %r: %r' % (where, result['message']))
                    if result['message'] not in (PLAIN, SEARCHED):
                        failures.append('an unknown sentence: %r' % result['message'])
                    tally['plain' if plain else 'searched'] += 1
    return failures, tally


def test_the_office_sentence_is_true_and_chosen_by_the_rule_in_every_case_of_a_small_world():
    failures, tally = office_sentence_failures()
    assert failures == []
    assert tally['plain'] >= 3 and tally['searched'] >= 100, dict(tally)   # the small world reaches both sentences, many times


def test_the_office_sentence_check_has_teeth(monkeypatch):
    """Negative control: an explanation that never knows staff were left out or avoided says the plain sentence where it is untrue."""
    real = closeness._explain_unreachable
    monkeypatch.setattr(closeness, '_explain_unreachable', lambda *args: real(*args[:7]))
    failures, _ = office_sentence_failures()
    assert any('says nothing in this office\'s current staff works there, but one does' in f for f in failures)
    assert any('wrong sentence' in f for f in failures)


# --------------------------------------------------------------------------- random worlds, against the base commit
LONE = ('p90001', 'p90002')   # covered, and in no record at all: connected to nobody


def paired(seed, covered_share, quantize=0):
    """Two identical small worlds, one for the code under test and one for the base commit, and the people not covered."""
    kwargs = dict(people=150 + 30 * seed, offices=14, quantize=quantize)
    new, ids, offices = synthetic_router(seed, **kwargs)
    old, _, _ = synthetic_router(seed, **kwargs)
    dropped = set(random.Random(seed).sample(ids, round(len(ids) * (1 - covered_share))))
    for router in (new, old):
        for lone in LONE:
            router.nodes[lone] = person('Person ' + lone)
        router.operational_staffer_ids = (set(ids) - dropped) | set(LONE)
    return new, old, ids, offices, dropped


def options_for(rng, ids, origin, office=None):
    options = {'objective': 'work_time', 'route_count': rng.choice((1, 3, 5)), 'uncertainty_weight': rng.choice((0., 1., 2.5)),
               'alternative_strategy': rng.choice(('ranked', 'distinct', 'independent'))}
    if office:
        options['_target_office'] = office
    if rng.random() < .4:
        options['_excluded_nodes'] = tuple(sorted(p for p in rng.sample(ids, 3) if p != origin))
    return options


def check_office(base, new, old, dropped, origin, staff, options, tally):
    """One office request. What it must answer depends on who is covered, and each case is compared with the base commit."""
    effective = tuple(p for p in sorted(set(staff)) if p not in options.get('_excluded_nodes', ()))
    uncovered, covered = [p for p in effective if p in dropped], tuple(p for p in effective if p not in dropped)
    result = new.route_many(origin, staff, options)
    if not effective:
        kind = 'empty'
    elif origin in dropped or not covered:
        kind = 'failed'
    elif not uncovered:
        kind = 'clean'
    else:
        kind = 'mixed'
    # An office none of the usable staff works at is worded for those staff when some were left out or avoided; the base commit's
    # sentence is always the plain one, so that sentence is checked here against the rule and left out of the comparison.
    reworded = set()
    if result.get('unreachable_reason') == 'office_without_work_records':
        left_or_avoided = bool(uncovered) or any(p in staff for p in options.get('_excluded_nodes', ()))
        sentences = closeness.SEARCHED_STAFF_MESSAGES if left_or_avoided else closeness.UNREACHABLE_MESSAGES
        assert result['message'] == sentences['office_without_work_records'], (origin, options)
        reworded = {'message'}
        tally['sentence:%s' % ('searched' if left_or_avoided else 'plain')] += 1
    if kind == 'mixed':   # exactly the base commit's answer for the covered staff alone
        expected = base_route_many(base, old, origin, covered, options)
        assert dump(result, drop=NEW_FIELDS | {'target_count'} | reworded) == dump(expected, drop={'target_count'} | reworded), (origin, options)
        assert result['target_count'] == len(effective) and expected['target_count'] == len(covered)
    else:   # exactly the base commit's answer, apart from the two new fields
        expected = base_route_many(base, old, origin, staff, options)
        assert dump(result, drop=NEW_FIELDS | reworded) == dump(expected, drop=reworded), (origin, options)
        if kind != 'empty':
            assert dump(result, drop=NEW_FIELDS | reworded, ordered=True) == dump(expected, drop=reworded, ordered=True), (origin, options)
    if kind == 'empty':
        assert result['status'] == 'unknown_person' and NEW_FIELDS.isdisjoint(result)
    else:
        assert left_out(result) == {'uncovered_staff_count': len(uncovered), 'uncovered_staff_ids': uncovered}, (kind, origin)
        assert (result['status'] == 'insufficient_work_coverage') == (kind == 'failed'), (kind, result['status'])
        for route in result.get('routes', []):
            assert left_out(route) == left_out(result)
            assert set(route['node_ids']).isdisjoint(uncovered), (origin, route['node_ids'])
    tally['%s:%s' % (kind, result['status'])] += 1


def check_people(base, new, old, origin, targets, options, tally):
    """A request that is not for an office: byte for byte the base commit's answer, with none of the new fields."""
    result = new.route_many(origin, targets, options)
    assert dump(result) == dump(base_route_many(base, old, origin, targets, options)), (origin, targets, options)
    assert NEW_FIELDS.isdisjoint(result) and all(NEW_FIELDS.isdisjoint(route) for route in result.get('routes', []))
    tally['people:%s' % result['status']] += 1


def test_office_requests_in_random_worlds_follow_the_rule_and_equal_the_base_commit_where_it_changes_nothing(base):
    tally = Counter()
    for seed, share, quantize in ((1, 1.0, 0), (1, .75, 0), (2, .7, 0), (3, .8, 90), (4, .6, 0)):
        new, old, ids, offices, dropped = paired(seed, share, quantize)
        rng = random.Random(1000 + seed)
        for office in offices:
            staff = roster(new, office)
            if len(staff) < 2:
                continue
            origin = rng.choice(ids)
            check_office(base, new, old, dropped, origin, staff, options_for(rng, ids, origin, office), tally)
            only_uncovered = tuple(p for p in staff if p in dropped)   # an office none of whose listed staff is covered
            if only_uncovered:
                check_office(base, new, old, dropped, rng.choice(ids), only_uncovered, options_for(rng, ids, ids[0], office), tally)
        for lone in LONE:   # a covered origin with no connection: the covered staff cannot be reached from it
            office = rng.choice(offices)
            check_office(base, new, old, dropped, lone, roster(new, office), options_for(rng, ids, lone, office), tally)
    print('office requests: %s' % dict(sorted(tally.items())))
    assert tally['mixed:found'] >= 20 and tally['mixed:unreachable'] >= 1 and tally['clean:found'] >= 8
    assert tally['failed:insufficient_work_coverage'] >= 10 and sum(tally.values()) >= 100


def test_person_requests_and_requests_for_a_set_of_people_equal_the_base_commit_byte_for_byte(base):
    tally = Counter()
    for seed, share, quantize in ((1, 1.0, 0), (1, .75, 0), (2, .7, 0), (3, .8, 90)):
        new, old, ids, offices, dropped = paired(seed, share, quantize)
        rng = random.Random(2000 + seed)
        for _ in range(30):
            origin, target = rng.sample(ids, 2)
            check_people(base, new, old, origin, (target,), options_for(rng, ids, origin), tally)
        for _ in range(10):   # a set of people that is not an office: one uncovered person still fails the whole request
            origin = rng.choice(ids)
            check_people(base, new, old, origin, tuple(rng.sample(ids, 4)), options_for(rng, ids, origin), tally)
        check_people(base, new, old, 'nobody-at-all', (ids[0],), options_for(rng, ids, ids[1]), tally)
        for origin, target in ((LONE[0], ids[5]), (ids[5], LONE[1]), (LONE[0], LONE[1])):   # nothing connects them
            check_people(base, new, old, origin, (target,), options_for(rng, ids, origin), tally)
    print('person requests: %s' % dict(sorted(tally.items())))
    assert tally['people:found'] >= 40 and tally['people:insufficient_work_coverage'] >= 15 and tally['people:unknown_person'] >= 4
    assert tally['people:unreachable'] >= 4


def test_the_random_worlds_are_not_vacuous_the_base_commit_refuses_what_the_rule_routes(base):
    new, old, ids, offices, dropped = paired(2, .7)
    refused = routed = 0
    for office in offices:
        staff = roster(new, office)
        origin = next(p for p in ids if p not in dropped)
        options = {**UI, '_target_office': office, 'route_count': 1, 'alternative_strategy': 'ranked'}
        if any(p in dropped for p in staff) and any(p not in dropped for p in staff):
            refused += base_route_many(base, old, origin, staff, options)['status'] == 'insufficient_work_coverage'
            routed += new.route_many(origin, staff, options)['status'] != 'insufficient_work_coverage'
    assert refused == routed >= 8


# --------------------------------------------------------------------------- through the service and the handler
def service_for(router, staff=STAFF, office=OFFICE):
    router.office_index = {office: {
        'id': office, 'kind': 'office', 'label': 'Target Office', 'staff_count': len(staff), 'records_through': '2026-09-01',
        'roster_basis': 'Currently listed directory staff',
        'roster': {p: {'person_id': p, 'label': p.upper(), 'basis': 'current_profile'} for p in staff}}}
    service = LiveService('fixture', SimpleNamespace(nodes={}))
    service.ready, service.stage = True, 'Ready'
    service.router, service.revision = router, router.revision
    return service


def test_the_service_answers_an_office_route_with_the_left_out_staff_on_the_result_and_on_every_route():
    service = service_for(world())
    answer = service.office_route('o', OFFICE, {**UI, 'route_count': 3, 'alternative_strategy': 'distinct'})
    assert answer['found'] is True and answer['candidate_count'] == 5 and answer['office']['label'] == 'Target Office'
    assert [r['node_ids'] for r in answer['routes']] == [['o', 's1'], ['o', 's2'], ['o', 'm', 's3']]
    for item in (answer, *answer['routes']):
        assert left_out(item) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}
    nobody = service_for(world(operational=('o', 'm'))).office_route('o', OFFICE, {**UI})
    assert nobody['found'] is False and nobody['status'] == 'insufficient_work_coverage'
    assert nobody['explanation'] == COVERAGE_MESSAGE and left_out(nobody)['uncovered_staff_count'] == 5


def test_the_page_can_tell_an_uncovered_origin_from_an_office_none_of_whose_staff_is_covered():
    # The page shows "N of this office's current staff ... left out" for the second and not for the first, where no search ran. It reads the answer's origin
    # and unavailable_person_ids to tell them apart (staffer_live/staffer-route.js, test_staffer_left_out_staff.cjs), so both are pinned here on the real producer.
    uncovered_origin = service_for(world(operational=('m', 's1', 's2', 's3'))).office_route('o', OFFICE, {**UI})
    assert uncovered_origin['status'] == 'insufficient_work_coverage' and uncovered_origin['origin'] == 'o'
    assert uncovered_origin['unavailable_person_ids'] == ['o', 'u1', 'u2']
    assert left_out(uncovered_origin) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}   # counted, though no search ran
    no_staff = service_for(world(operational=('o', 'm'))).office_route('o', OFFICE, {**UI})
    assert no_staff['status'] == 'insufficient_work_coverage' and no_staff['origin'] == 'o'
    assert no_staff['unavailable_person_ids'] == sorted(STAFF) and 'o' not in no_staff['unavailable_person_ids']
    assert left_out(no_staff) == {'uncovered_staff_count': 5, 'uncovered_staff_ids': sorted(STAFF)}
    # and in the JSON the handler serves, which is what the page reads
    service = service_for(world(operational=('m', 's1', 's2', 's3')))
    server = Served(service)
    try:
        page = {'origin': 'o', 'office': OFFICE, 'objective': 'work_time', 'route_count': 1, 'include_historical': 1, 'include_review': 1,
                'include_policy': 0, 'include_unresolved': 0, 'include_user_asserted': 1}
        answer = via_http(server, '/api/staffer-office-route', **page)
        assert answer['status'] == 'insufficient_work_coverage' and answer['origin'] == 'o' and 'o' in answer['unavailable_person_ids']
        assert left_out(answer) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}
    finally:
        server.close()
        service.route_executor.shutdown(wait=True, cancel_futures=True)


def via_http(server, path, **query):
    """The page's own requests: start a background search, poll it, and read the JSON the handler serialises."""
    query = {'async': 1, 'holder': 'page-one', **query}
    pairs = '&'.join('%s=%s' % (key, str(value).replace(':', '%3A')) for key, value in query.items())
    status, answer = server.request('GET', path + '?' + pairs)
    assert status == 200
    outcome = {}

    def done():
        code, current = server.request('GET', '/api/staffer-route-result?id=' + answer['search_id'])
        outcome['value'] = current
        return code == 200 and current['status'] not in ('queued', 'searching')
    assert wait_until(done, 20), outcome
    return outcome['value']


def test_the_handler_serves_the_left_out_staff_in_the_json_of_a_finished_office_search():
    service = service_for(world())
    server = Served(service)
    try:
        page = {'origin': 'o', 'office': OFFICE, 'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'distinct',
                'include_historical': 1, 'include_review': 1, 'include_policy': 0, 'include_unresolved': 0, 'include_user_asserted': 1}
        answer = via_http(server, '/api/staffer-office-route', **page)
        assert answer['status'] == 'found' and answer['found'] is True
        assert left_out(answer) == {'uncovered_staff_count': 2, 'uncovered_staff_ids': LEFT_OUT}
        assert [left_out(route) for route in answer['routes']] == [left_out(answer)] * 3
        person_page = {**page, 'target': 'u1'}
        del person_page['office']
        person_answer = via_http(server, '/api/staffer-route', **person_page)
        assert person_answer['status'] == 'insufficient_work_coverage' and NEW_FIELDS.isdisjoint(person_answer)
    finally:
        server.close()
        service.route_executor.shutdown(wait=True, cancel_futures=True)


def test_the_sentence_for_an_office_nobody_the_search_could_use_works_at_reaches_the_page_through_the_service_and_the_handler():
    page = {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'distinct', 'include_historical': 1, 'include_review': 1,
            'include_policy': 0, 'include_unresolved': 0, 'include_user_asserted': 1}
    # who the office lists, who the reader avoids, the sentence
    for staff, avoid, sentence in ((AWAY, (), PLAIN), ((*AWAY, 'd1'), (), SEARCHED), ((*AWAY, 'e1'), ('e1',), SEARCHED)):
        service = service_for(nowhere_world(), staff, NOWHERE)
        server = Served(service)
        try:
            answer = service.office_route('x', NOWHERE, {**UI, 'route_count': 3, 'avoid_people': avoid})
            assert answer['found'] is False and answer['unreachable_reason'] == 'office_without_work_records' and answer['message'] == sentence
            over_http = via_http(server, '/api/staffer-office-route', origin='x', office=NOWHERE, avoid_people=','.join(avoid), **page)
            assert over_http['unreachable_reason'] == 'office_without_work_records' and over_http['message'] == sentence
        finally:
            server.close()
            service.route_executor.shutdown(wait=True, cancel_futures=True)


# --------------------------------------------------------------------------- what the left-out staff lack (uncovered_basis)
# The line the page shows says which coverage the left-out staff lack. Payroll evidence can cover a person only where it is read: a
# comparison of directory episodes only (work_sources=directory) has none, so there "uncovered" means "not on the staffer roster", and the
# page must not say "payroll or" (the re-check's finding D1: a person with payroll rows and no roster entry is left out of that comparison).
# The basis names what the mode can use, not whether the world happens to hold payroll rows.
def work_row(person_id, office, start, end, how):
    provenance = {'work_route_source': 'politico_profile_history'} if how == 'directory' else {'method': 'payroll-identity-rows-v1'}
    return {'person_id': person_id, 'employer_id': office, 'start': start, 'end_exclusive': end, 'employment_verified': True, 'provenance': provenance}


DIRECTORY_OFFICE = 'office:t'
DIRECTORY_STAFF = ('d1', 'p1')
MODES = (('payroll_and_directory', 'payroll_or_roster'), ('directory', 'roster'))


def directory_world(operational=('o', 'd1')):
    """o and d1 are on the staffer roster (a directory episode each at the office); p1 has payroll rows and a directory row there and is not."""
    nodes = {'o': person('Origin Person'), 'p1': person('Payroll Person'), 'd1': person('Directory Person'), DIRECTORY_OFFICE: office_node(DIRECTORY_OFFICE)}
    episodes = [work_row('o', DIRECTORY_OFFICE, '2019-01-01', '2021-01-01', 'directory'),
                work_row('p1', DIRECTORY_OFFICE, '2020-01-01', '2022-01-01', 'payroll'),
                work_row('p1', DIRECTORY_OFFICE, '2020-01-01', '2022-01-01', 'directory'),
                work_row('d1', DIRECTORY_OFFICE, '2020-06-01', '2022-01-01', 'directory')]
    router = AdvocacyRouter(nodes, episodes, 'directory-mode', AS_OF)
    router.operational_staffer_ids = set(operational)
    return router


def basis_failures():
    """Every way an office answer differs from the rule: payroll_or_roster where payroll evidence is read, roster alone where it is not."""
    failures = []

    def check(what, answer, basis, status=None, left=None):
        for item in (answer, *answer.get('routes', [])):
            if item.get('uncovered_basis') != basis:
                failures.append('%s: uncovered_basis %r, not %r' % (what, item.get('uncovered_basis'), basis))
            if left is not None and item.get('uncovered_staff_ids') != left:
                failures.append('%s: left out %r, not %r' % (what, item.get('uncovered_staff_ids'), left))
        if status is not None and answer['status'] != status:
            failures.append('%s: status %r, not %r' % (what, answer['status'], status))
    # The re-check's scenario: p1 has payroll rows and no roster entry. Payroll evidence read, p1 is covered; directory episodes only, it is not.
    for mode, basis in MODES:
        left = ['p1'] if mode == 'directory' else []
        for count, strategy in ((1, 'ranked'), (3, 'ranked'), (3, 'distinct'), (3, 'independent')):
            answer = ask(directory_world(), 'o', DIRECTORY_STAFF, DIRECTORY_OFFICE, work_sources=mode, route_count=count, alternative_strategy=strategy)
            check('%s, %d %s' % (mode, count, strategy), answer, basis, 'found', left)
        # nobody the model can use but the origin: payroll covers p1 where it is read, so only d1 is left out there, and both are left out here
        answer = ask(directory_world(operational=('o',)), 'o', DIRECTORY_STAFF, DIRECTORY_OFFICE, work_sources=mode)
        if mode == 'directory':
            check(mode + ', nobody covered', answer, basis, 'insufficient_work_coverage', ['d1', 'p1'])
        else:
            check(mode + ', one covered by payroll', answer, basis, 'found', ['d1'])
        # the origin is the one without coverage
        check(mode + ', uncovered origin', ask(directory_world(operational=('d1',)), 'o', DIRECTORY_STAFF, DIRECTORY_OFFICE, work_sources=mode),
              basis, 'insufficient_work_coverage')
        # an unreachable answer: the office the covered staff do not work at
        answer = ask(reasons_world(), 'o', PROFILE, 'office:profile', work_sources=mode)
        check(mode + ', unreachable', answer, basis, 'unreachable', ['u5'])
    return failures


def test_every_office_answer_says_which_coverage_its_left_out_staff_lack():
    assert basis_failures() == []


def test_the_basis_check_has_teeth(monkeypatch):
    """Negative control: a producer that says payroll_or_roster in every mode fails the directory-only answers, and every one of them."""
    real = ClosenessRoutes.route

    def always_payroll_or_roster(self, *args, **kwargs):
        result = real(self, *args, **kwargs)
        return {**result, 'uncovered_basis': 'payroll_or_roster'} if 'uncovered_basis' in result else result
    monkeypatch.setattr(ClosenessRoutes, 'route', always_payroll_or_roster)
    failures = basis_failures()
    assert failures and all('directory' in f for f in failures), failures
    assert any('nobody covered' in f for f in failures) and any('unreachable' in f for f in failures) and any('uncovered origin' in f for f in failures)


def test_a_person_with_payroll_rows_and_no_roster_entry_is_left_out_of_a_directory_only_comparison_and_the_basis_says_why():
    router = directory_world()
    both = ask(router, 'o', DIRECTORY_STAFF, DIRECTORY_OFFICE, work_sources='payroll_and_directory')
    assert both['status'] == 'found' and left_out(both) == {'uncovered_staff_count': 0, 'uncovered_staff_ids': []}
    directory_only = ask(directory_world(), 'o', DIRECTORY_STAFF, DIRECTORY_OFFICE, work_sources='directory')
    assert left_out(directory_only) == {'uncovered_staff_count': 1, 'uncovered_staff_ids': ['p1']} and directory_only['target'] == 'd1'
    assert directory_only['uncovered_basis'] == 'roster' and both['uncovered_basis'] == 'payroll_or_roster'
    index = router.closeness().index(router._options({'objective': 'work_time', 'work_sources': 'directory'}))
    assert 'p1' not in closeness._payroll_people(index), 'a directory-only index holds no payroll evidence, so payroll covers nobody in it'
    assert 'p1' in closeness._payroll_people(router.closeness().index(router._options({'objective': 'work_time'})))


def test_a_world_without_payroll_rows_still_names_payroll_or_roster_where_payroll_evidence_is_read():
    result = ask(reasons_world(), 'o', PROFILE, 'office:profile')
    assert result['uncovered_basis'] == 'payroll_or_roster' and result['uncovered_staff_ids'] == ['u5']
    assert ask(reasons_world(), 'o', PROFILE, 'office:profile', work_sources='directory')['uncovered_basis'] == 'roster'


def test_the_service_and_the_handler_serve_the_basis_on_the_answer_and_on_every_route():
    page = {'objective': 'work_time', 'route_count': 3, 'alternative_strategy': 'distinct', 'include_historical': 1, 'include_review': 1,
            'include_policy': 0, 'include_unresolved': 0, 'include_user_asserted': 1}
    for mode, basis in MODES:
        left = ['p1'] if mode == 'directory' else []
        service = service_for(directory_world(), DIRECTORY_STAFF, DIRECTORY_OFFICE)
        server = Served(service)
        try:
            answer = service.office_route('o', DIRECTORY_OFFICE, {**UI, 'route_count': 3, 'work_sources': mode})
            over_http = via_http(server, '/api/staffer-office-route', origin='o', office=DIRECTORY_OFFICE, work_sources=mode, **page)
            for name, served in (('service', answer), ('handler', over_http)):
                assert served['status'] == 'found' and served['routes'], (mode, name)
                for item in (served, *served['routes']):
                    assert (item['uncovered_basis'], item['uncovered_staff_ids']) == (basis, left), (mode, name, item['uncovered_basis'])
        finally:
            server.close()
            service.route_executor.shutdown(wait=True, cancel_futures=True)
    person_page = {**page, 'target': 'p1', 'origin': 'o'}
    service = service_for(directory_world(), DIRECTORY_STAFF, DIRECTORY_OFFICE)
    server = Served(service)
    try:
        assert NEW_FIELDS.isdisjoint(via_http(server, '/api/staffer-route', work_sources='directory', **person_page)), 'a person route carries none of it'
    finally:
        server.close()
        service.route_executor.shutdown(wait=True, cancel_futures=True)
