"""Exact bidirectional route search against the one-directional oracle and exhaustive paths.

The answer is the cheapest route, where costs equal to 12 significant digits tie and the tied route with the fewest steps wins
(then the least exact cost). Exhaustive enumeration with Fractions, a whole-graph exact reference and the verifier's 40,000
tie-heavy graphs check it:
    python -m fec.surfaces.test_staffer_closeness_bidirectional --ties [graphs per mode, default 20000]

Benchmark (defaults: 3 seeded graphs of 20,000 people in 1,000 offices, 30 person and 30 office
queries each, every run from an empty row cache):
    python -m fec.surfaces.test_staffer_closeness_bidirectional --benchmark [graphs people offices queries seed] [--save out.json]
    python -m fec.surfaces.test_staffer_closeness_bidirectional --summarize out.json [more.json ...]

Cold path (what a person waits for on a router nobody has asked yet: the first route, the page's route_count-3 request,
the work index alone, the same requests once the index is built, and the work done; defaults: 2 worlds of 20,000 people,
1,000 offices, best and median of 3 repeats):
    python -m fec.surfaces.test_staffer_closeness_bidirectional --coldpath [worlds people offices seed repeats] [--save out.json]
"""
from array import array
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from fractions import Fraction
import heapq
import itertools
import gc
import json
import math
import random
import statistics
import sys
import threading
import time

import pytest

from fec.staffer_network.closeness_math import HOP_FACTOR, MEMBER_BETA, ExposureIndex
from fec.staffer_network.member_context import MemberContext
from fec.staffer_network.relationship_uncertainty import uncertainty
from fec.surfaces import staffer_closeness_routes as closeness
from fec.surfaces import staffer_route_cancel as cancel
from fec.surfaces.staffer_closeness_routes import (
    SINK, ClosenessRoutes, _Meter, _Neighbors, _RowCache, _fewest_steps_exact, _filters, _grow_frontiers, _next_alternative,
    _search_bidirectional, _search_reference, _tube)
from fec.surfaces.staffer_distinct_routes import evidence_contexts
from fec.surfaces.staffer_route_cancel import RouteCancelled, cancel_scope
from fec.surfaces.staffer_routing import AdvocacyRouter

AS_OF = '2026-09-10'
SEARCHES = [pytest.param(_search_reference, id='oracle'), pytest.param(_search_bidirectional, id='bidirectional')]


# --------------------------------------------------------------------------- random graphs
class Case:
    def __init__(self, start, adjacency, edges, terminal, blocked_nodes, blocked_edges, excluded_contexts):
        self.start, self.adjacency, self.edges, self.terminal = start, adjacency, edges, terminal
        self.blocked_nodes, self.blocked_edges, self.excluded_contexts = blocked_nodes, blocked_edges, excluded_contexts


def draw(rng, mode, terminal=False):
    # Integer and dyadic costs add exactly in any order, so ties are true ties
    # and cost, steps and tie behaviour can be compared without rounding noise.
    if mode == 'integer':
        return float(rng.choice((0, 0, 1, 2, 3) if terminal else (1, 1, 2, 3)))
    if mode == 'dyadic':
        return rng.choice((0., .25, .5, 1., 2.)) if terminal else rng.randint(1, 16) / 8
    # Decimal and log costs agree on paper but not in the last bit of a float: sums of different parts, or of the same parts
    # in another order, round differently. These are the near ties the tie rule is for.
    if mode == 'decimal':
        return rng.choice((0., .1, .2, .3, .7)) if terminal else rng.randint(1, 30) / 10
    if mode == 'log':
        if terminal:
            return rng.choice((0., -math.log(.5), -math.log(.25), -math.log(.8)))
        return -math.log(.8 * rng.choice((1., .5, .25, .625, .3125)))
    return 0. if terminal and rng.random() < .15 else rng.uniform(0., 3.) if terminal else rng.uniform(.2, 4.)


def make_case(seed, mode, small=False):
    rng = random.Random(seed)
    n = rng.randint(3, 9) if small else int(5 + 196 * rng.random() ** 2)
    nodes = ['n%03d' % i for i in range(n)]
    universe = ['c%d' % i for i in range(6)]
    edges = {}
    for _ in range(int(n * (rng.choice((2., 3.)) if small else rng.choice((1.2, 2., 3., 5., 8.))) / 2) + 1):
        a, b = rng.sample(nodes, 2)
        edges.setdefault(tuple(sorted((a, b))), (draw(rng, mode), frozenset(rng.sample(universe, rng.randint(1, 2)))))
    adjacency = {p: [] for p in nodes}
    for (a, b), (cost, contexts) in edges.items():
        adjacency[a].append((b, cost, contexts))
        adjacency[b].append((a, cost, contexts))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    start = rng.choice(nodes)
    seeds = rng.sample(nodes, min(n, rng.choice((0, 1, 1, 2, 3, 5, 10, 30))))
    if seeds and rng.random() < .2 and start not in seeds:
        seeds[0] = start
    terminal = {p: draw(rng, mode, terminal=True) for p in seeds}
    blocked_nodes = frozenset(rng.sample(nodes, rng.randint(0, max(1, n // 10)))) if rng.random() < .5 else frozenset()
    if rng.random() < .1:
        blocked_nodes |= {start}
    if seeds and rng.random() < .15:
        blocked_nodes |= {rng.choice(seeds)}
    pool = sorted(edges)
    blocked_edges = set(rng.sample(pool, rng.randint(0, min(len(pool), max(1, n // 10))))) if rng.random() < .5 else set()
    if seeds and rng.random() < .25:
        blocked_edges.add(tuple(sorted((SINK, rng.choice(seeds)))))
    if n > 2 and rng.random() < .1:
        blocked_edges.add(tuple(sorted(rng.sample(nodes, 2))))
    excluded = frozenset(rng.sample(universe, rng.randint(1, 3))) if rng.random() < .3 else frozenset()
    return Case(start, adjacency, edges, terminal, blocked_nodes, frozenset(blocked_edges), excluded)


def neighbors_of(case, meter=None):
    def neighbors(person):
        rows = case.adjacency.get(person, ())
        if meter is not None:
            meter.checks += len(rows)
            meter.pairs += len(rows)
        return rows
    return neighbors


def run_search(search, case, meter=None):
    meter = meter or _Meter()
    return search(case.start, neighbors_of(case, meter), case.terminal, case.blocked_nodes, case.blocked_edges,
                  excluded_contexts=case.excluded_contexts, meter=meter)


def validate(case, path, cost):
    """A returned path must be usable, and its cost must be its edges summed from the origin."""
    assert path[0] == case.start and path[-1] == SINK
    people = path[:-1]
    assert len(set(people)) == len(people), 'loop'
    assert not set(people[1:]) & case.blocked_nodes, 'entered a blocked node'
    total = 0.
    for a, b in zip(people, people[1:]):
        pair = tuple(sorted((a, b)))
        assert pair in case.edges, 'no such edge'
        assert pair not in case.blocked_edges, 'blocked edge'
        edge_cost, contexts = case.edges[pair]
        assert not contexts & case.excluded_contexts, 'excluded context'
        total += edge_cost
    assert people[-1] in case.terminal, 'last person is not anchored'
    assert tuple(sorted((SINK, people[-1]))) not in case.blocked_edges, 'blocked terminal arc'
    total += case.terminal[people[-1]]
    assert total == cost, 'reported cost is not the path cost'
    return len(people) - 1


def exhaustive(case):
    """Least (cost, steps) over every simple path, independent of either search."""
    best = [None]
    def visit(person, seen, cost, steps):
        if person in case.terminal and tuple(sorted((SINK, person))) not in case.blocked_edges:
            key = (cost + case.terminal[person], steps)
            if best[0] is None or key < best[0]:
                best[0] = key
        for other, edge_cost, contexts in case.adjacency[person]:
            if (other in seen or other in case.blocked_nodes or contexts & case.excluded_contexts
                    or tuple(sorted((person, other))) in case.blocked_edges):
                continue
            visit(other, seen | {other}, cost + edge_cost, steps + 1)
    visit(case.start, {case.start}, 0., 0)
    return best[0]


TIE = Fraction(1, 10 ** 12)  # routes whose exact costs differ by at most this fraction of the cheapest tie


def make_float_case(seed, mode):
    """The verifier's tie-heavy graphs (decimal or log costs): sums that agree on paper differ in the last bit of a float."""
    rng = random.Random(seed)
    n = rng.randint(4, 40)
    nodes = ['n%03d' % i for i in range(n)]
    edges = {}
    for _ in range(int(n * rng.choice((1.5, 2., 3., 4.)))):
        a, b = rng.sample(nodes, 2)
        edges.setdefault(tuple(sorted((a, b))), (draw(rng, mode), frozenset()))
    adjacency = {p: [] for p in nodes}
    for (a, b), (cost, contexts) in edges.items():
        adjacency[a].append((b, cost, contexts))
        adjacency[b].append((a, cost, contexts))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    start = rng.choice(nodes)
    seeds = rng.sample(nodes, min(n, rng.choice((1, 2, 3, 5, 10))))
    terminal = {p: draw(rng, mode, terminal=True) for p in seeds}
    return Case(start, adjacency, edges, terminal, frozenset(), frozenset(), frozenset())


def make_ladder_case(seed):
    """Two to four routes with different step counts whose costs agree in decimal: only rounding separates them.

    At most nine nodes, with random decoy edges, blocked nodes, edges and contexts, an anchored origin and a blocked terminal arc.
    """
    rng = random.Random(seed)
    total, spare, links = rng.randint(6, 30), 7, []  # tenths; intermediate nodes still to hand out
    for _ in range(rng.randint(2, 4)):
        steps = rng.randint(1, min(4, spare + 1))
        spare -= steps - 1
        cuts = sorted(rng.sample(range(1, total), steps - 1))
        links.append([b - a for a, b in zip([0] + cuts, cuts + [total])])
    count = 2 + sum(len(parts) - 1 for parts in links)
    labels = rng.sample(['n%03d' % i for i in range(count)], count)
    start, goal, inner = labels[0], labels[1], iter(labels[2:])
    edges = {}
    for parts in links:
        path = [start] + [next(inner) for _ in parts[:-1]] + [goal]
        for (a, b), tenths in zip(zip(path, path[1:]), parts):
            edges.setdefault(tuple(sorted((a, b))), (tenths / 10, frozenset({'c%d' % rng.randrange(2)})))
    for _ in range(rng.choice((0, 0, 1, 2, 3))):
        a, b = rng.sample(labels, 2)
        edges.setdefault(tuple(sorted((a, b))), (rng.randint(1, 30) / 10, frozenset({'c%d' % rng.randrange(2)})))
    adjacency = {p: [] for p in labels}
    for (a, b), (cost, contexts) in edges.items():
        adjacency[a].append((b, cost, contexts))
        adjacency[b].append((a, cost, contexts))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    terminal = {goal: rng.choice((0., .1, .2, .3))}
    if rng.random() < .3:
        terminal[rng.choice(labels[2:] or labels)] = rng.choice((0., .1, .2, .3, .7))
    if rng.random() < .1:
        terminal[start] = rng.choice((0., .3, 1.))
    pool = sorted(edges)
    blocked_edges = {rng.choice(pool)} if rng.random() < .2 else set()
    if rng.random() < .1:
        blocked_edges.add(tuple(sorted((SINK, goal))))
    blocked_nodes = frozenset({rng.choice(labels)}) if rng.random() < .15 else frozenset()
    excluded = frozenset({'c1'}) if rng.random() < .15 else frozenset()
    return Case(start, adjacency, edges, terminal, blocked_nodes, frozenset(blocked_edges), excluded)


def exact_routes(case):
    """Every simple usable route as (nodes, exact cost): the definition of the answer, enumerated with Fractions alone."""
    price = {pair: Fraction(cost) for pair, (cost, _) in case.edges.items()}
    found = []

    def visit(person, path, cost):
        if person in case.terminal and tuple(sorted((SINK, person))) not in case.blocked_edges:
            found.append((tuple(path), cost + Fraction(case.terminal[person])))
        for other, _, contexts in case.adjacency[person]:
            pair = tuple(sorted((person, other)))
            if other in path or other in case.blocked_nodes or contexts & case.excluded_contexts or pair in case.blocked_edges:
                continue
            visit(other, path + [other], cost + price[pair])
    visit(case.start, [case.start], Fraction(0))
    return found


def tie_answer(routes):
    """(least exact cost, steps, exact cost at those steps, the routes with both) under the tie rule."""
    least = min(cost for _, cost in routes)
    tied = [(path, cost) for path, cost in routes if cost - least <= least * TIE]
    steps = min(len(path) - 1 for path, _ in tied)
    cheapest = min(cost for path, cost in tied if len(path) - 1 == steps)
    return least, steps, cheapest, {path for path, cost in tied if len(path) - 1 == steps and cost == cheapest}


def exact_cost(case, path):
    """Exact cost of a returned [start, ..., staff, SINK]."""
    total = Fraction(0)
    for a, b in zip(path[:-2], path[1:-1]):
        total += Fraction(case.edges[tuple(sorted((a, b)))][0])
    return total + Fraction(case.terminal[path[-2]])


def usable_arcs(case):
    """{person: [(other, exact cost)]} over the edges a route may use; {staff: exact terminal cost} for the anchored staff."""
    entry = case.blocked_nodes - {case.start}
    arcs = {person: [(other, Fraction(cost)) for other, cost, contexts in rows
                     if other not in entry and not contexts & case.excluded_contexts
                     and tuple(sorted((person, other))) not in case.blocked_edges]
            for person, rows in case.adjacency.items()}
    cut = {b if a == SINK else a for a, b in case.blocked_edges if SINK in (a, b)}
    return arcs, {p: Fraction(t) for p, t in case.terminal.items() if p not in cut and p not in entry}


def spec_reference(case):
    """(steps, exact cost, least exact cost) of the answer, or None: Fractions over the whole graph, no candidate nodes.

    Exact Dijkstra for the least cost, then layers of walks by step count until one ends at a staff member within
    the tie bound; a layer keeps the cheapest walk to each node.
    """
    arcs, ends = usable_arcs(case)
    distance, heap, serial = {case.start: Fraction(0)}, [(Fraction(0), 0, case.start)], itertools.count(1)
    while heap:
        spent, _, person = heapq.heappop(heap)
        if spent != distance[person]:
            continue
        for other, cost in arcs[person]:
            if other not in distance or spent + cost < distance[other]:
                distance[other] = spent + cost
                heapq.heappush(heap, (spent + cost, next(serial), other))
    totals = [distance[p] + t for p, t in ends.items() if p in distance]
    if not totals:
        return None
    least = min(totals)
    bound = least + least * TIE
    layer = {case.start: Fraction(0)}
    for steps in range(len(arcs) + 2):
        here = [layer[p] + t for p, t in ends.items() if p in layer and layer[p] + t <= bound]
        if here:
            return steps, min(here), least
        following = {}
        for person, spent in layer.items():
            for other, cost in arcs[person]:
                if spent + cost <= bound and (other not in following or spent + cost < following[other]):
                    following[other] = spent + cost
        layer = following
    raise AssertionError('a route exists but no walk within the bound reached a staff member')


def tied_routes(case, cap=5000):
    """Every simple route within the tie bound of the cheapest, by depth-first search pruned with exact distances to the sink.

    Returns [] when there is no route and None when more than `cap` routes tie.
    """
    arcs, ends = usable_arcs(case)
    reverse = {p: [] for p in arcs}
    for person, rows in arcs.items():
        for other, cost in rows:
            reverse[other].append((person, cost))
    to_sink, serial = dict(ends), itertools.count()
    heap = [(cost, next(serial), p) for p, cost in ends.items()]
    heapq.heapify(heap)
    while heap:
        spent, _, person = heapq.heappop(heap)
        if spent != to_sink[person]:
            continue
        for before, cost in reverse[person]:
            if before not in to_sink or spent + cost < to_sink[before]:
                to_sink[before] = spent + cost
                heapq.heappush(heap, (spent + cost, next(serial), before))
    if case.start not in to_sink:
        return []
    bound = to_sink[case.start] + to_sink[case.start] * TIE
    found = []

    def visit(person, path, cost):
        if len(found) > cap:
            return
        if person in ends and cost + ends[person] <= bound:
            found.append(tuple(path))
        for other, hop in arcs[person]:
            if other not in path and other in to_sink and cost + hop + to_sink[other] <= bound:
                visit(other, path + [other], cost + hop)
    visit(case.start, [case.start], Fraction(0))
    return None if len(found) > cap else found


def search_with(case, advance=None):
    return _search_bidirectional(case.start, neighbors_of(case), case.terminal, case.blocked_nodes, case.blocked_edges,
                                 excluded_contexts=case.excluded_contexts, meter=_Meter(), advance=advance)


def frontiers_of(case, advance=None):
    """The first pass alone: (frontiers or None, tube or None, filters) for a case."""
    entry, blocked_at, seeds = _filters(case.start, case.terminal, case.blocked_nodes, case.blocked_edges)
    if not seeds:
        return None, None
    found = _grow_frontiers(case.start, neighbors_of(case), seeds, entry, blocked_at, case.excluded_contexts, _Meter(), advance)
    if found is None:
        return None, None
    return found, _tube(neighbors_of(case), entry, blocked_at, case.excluded_contexts, found)


TIE_SWAPS = {}


@pytest.mark.parametrize('mode', ['integer', 'dyadic', 'real'])
def test_bidirectional_equals_oracle_on_random_graphs(mode):
    """2,400 seeded graphs and queries: same status, cost and steps; tie swaps are counted and listed."""
    seen, swaps = Counter(), []
    for seed in range(800):
        case = make_case(seed + 100000 * ('integer', 'dyadic', 'real').index(mode), mode)
        oracle, new = run_search(_search_reference, case), run_search(_search_bidirectional, case)
        seen['cases'] += 1
        seen['no_targets'] += not case.terminal
        seen['origin_is_target'] += case.start in case.terminal
        seen['blocked_origin'] += case.start in case.blocked_nodes
        seen['blocked_terminal_arc'] += any(SINK in pair for pair in case.blocked_edges)
        seen['excluded_contexts'] += bool(case.excluded_contexts)
        seen['blocked_edges'] += bool(case.blocked_edges)
        if oracle is None:
            assert new is None, (mode, seed)
            seen['unreachable'] += 1
            seen['unreachable_with_targets'] += bool(case.terminal)
            continue
        assert new is not None, (mode, seed)
        steps = validate(case, *oracle)
        assert validate(case, *new) == steps, (mode, seed)
        assert abs(new[1] - oracle[1]) <= 1e-9, (mode, seed)
        seen['found'] += 1
        if new[0] != oracle[0]:
            swaps.append((mode, seed, oracle[0], new[0]))
    TIE_SWAPS[mode] = swaps
    print('%s: %d cases, %d found, %d unreachable, %d tie swaps' % (mode, seen['cases'], seen['found'], seen['unreachable'], len(swaps)))
    for swap in swaps[:5]:
        print('  tie swap', swap)
    # The generator must keep exercising every special case it was written for.
    for feature in ('no_targets', 'origin_is_target', 'blocked_origin', 'blocked_terminal_arc', 'excluded_contexts',
                    'blocked_edges', 'unreachable_with_targets'):
        assert seen[feature] >= 20, (mode, feature, seen[feature])
    assert seen['found'] >= 300
    if mode == 'real':
        assert not swaps, 'distinct real costs have one optimum, so no swap can occur'
    else:
        assert swaps, 'integer and dyadic costs must produce real ties for the tie rule to be exercised'


def test_both_searches_equal_exhaustive_simple_paths_on_small_graphs():
    checked = 0
    for mode in ('integer', 'dyadic'):
        for seed in range(300):
            case = make_case(seed + 7000, mode, small=True)
            best = exhaustive(case)
            for search in (_search_reference, _search_bidirectional):
                found = run_search(search, case)
                if best is None:
                    assert found is None, (mode, seed, search.__name__)
                else:
                    assert found is not None and (found[1], validate(case, *found)) == best, (mode, seed, search.__name__)
            checked += 1
    assert checked == 600


# --------------------------------------------------------------------------- the tie rule
# Exact cost = the real sum of the float costs; C = the least exact cost; a route ties when its exact cost is at most
# C + C / 10**12; the answer is the tied route with the fewest steps, then the least exact cost, then (a fixed choice)
# the one whose node ids read from the sink end are smallest.
BRUTE_MODES = ('integer', 'dyadic', 'decimal', 'log')
SIDES = {'balanced': None, 'forward only': lambda work, f, b: 0, 'backward only': lambda work, f, b: 1,
         'alternate': lambda work, f, b: sum(work) % 2}


def test_search_equals_the_tie_rule_enumerated_exactly_on_small_graphs():
    """6,000 small seeded graphs: every simple route enumerated with Fractions, and the answer must be the one the rule names."""
    seen = Counter()
    cases = [('%s-%d' % (mode, seed), make_case(seed + 31000, mode, small=True)) for mode in BRUTE_MODES for seed in range(900)]
    cases += [('ladder-%d' % seed, make_ladder_case(seed)) for seed in range(2400)]
    for label, case in cases:
        routes = exact_routes(case)
        found, oracle, reference = run_search(_search_bidirectional, case), run_search(_search_reference, case), spec_reference(case)
        seen['cases'] += 1
        seen['no_targets'] += not case.terminal
        seen['origin_is_target'] += case.start in case.terminal
        seen['blocked_origin'] += case.start in case.blocked_nodes
        seen['blocked_terminal_arc'] += any(SINK in pair for pair in case.blocked_edges)
        seen['excluded_contexts'] += bool(case.excluded_contexts)
        seen['blocked_edges'] += bool(case.blocked_edges)
        if not routes:
            assert found is None and oracle is None and reference is None, label
            seen['unreachable'] += 1
            seen['unreachable_with_targets'] += bool(case.terminal)
            continue
        least, steps, cheapest, winners = tie_answer(routes)
        assert found is not None and oracle is not None, label
        validate(case, *found)
        nodes = tuple(found[0][:-1])
        assert (len(nodes) - 1, exact_cost(case, found[0])) == (steps, cheapest), label
        assert nodes in winners and nodes == min(winners, key=lambda path: path[::-1]), (label, 'not the fixed choice among equals')
        assert reference == (steps, cheapest, least), (label, 'the whole-graph reference disagrees with the enumeration')
        seen['found'] += 1
        tied = [path for path, cost in routes if cost - least <= least * TIE]
        seen['several_tied_routes'] += len(tied) > 1
        seen['tied_routes_of_different_lengths'] += len({len(path) for path in tied}) > 1
        # the oracle only ever differs in the ways the rule names, and its route always lies inside the tie
        assert exact_cost(case, oracle[0]) - least <= least * TIE and len(oracle[0]) - 2 >= steps, label
        seen['oracle_took_more_steps'] += len(oracle[0]) - 2 > steps
    print('small graphs: %s' % dict(seen))
    assert seen['cases'] >= 6000
    for feature in ('no_targets', 'origin_is_target', 'blocked_origin', 'blocked_terminal_arc', 'excluded_contexts',
                    'blocked_edges', 'unreachable_with_targets', 'several_tied_routes'):
        assert seen[feature] >= 50, feature
    assert seen['found'] >= 3000 and seen['tied_routes_of_different_lengths'] >= 300 and seen['oracle_took_more_steps'] >= 100


def two_routes(a_hops, b_hops, terminal=0.):
    """Origin 's' to staff 'g' by two disjoint routes of the given hop costs; returns (neighbors, terminal, route A, route B)."""
    adjacency = {'s': [], 'g': []}
    routes = []
    for tag, hops in (('a', a_hops), ('b', b_hops)):
        path = ['s'] + ['%s%d' % (tag, i) for i in range(len(hops) - 1)] + ['g']
        routes.append(path + [SINK])
        for (x, y), cost in zip(zip(path, path[1:]), hops):
            adjacency.setdefault(x, []).append((y, cost, frozenset()))
            adjacency.setdefault(y, []).append((x, cost, frozenset()))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    return (lambda p: adjacency[p]), {'g': terminal}, routes[0], routes[1]


def test_the_verifiers_minimal_example_two_steps_beat_three_that_cost_the_same_in_decimal():
    # 40,000 tie-heavy graphs gave 102 step mismatches between the one-directional and the first bidirectional search; this
    # six-node graph is the smallest. Two steps (hops 0.5, 1.2, terminal 0.1) and three (0.5, 0.9, 0.3, terminal 0.1) both cost 1.8.
    edges = {('n000', 'n001'): .5, ('n000', 'n003'): 2.6, ('n000', 'n004'): 1.1, ('n000', 'n005'): 2.2, ('n001', 'n002'): .9,
             ('n001', 'n004'): 1.1, ('n001', 'n005'): 1.2, ('n002', 'n004'): 1.7, ('n002', 'n005'): .3, ('n003', 'n005'): 1.4,
             ('n004', 'n005'): 1.0}
    adjacency = {'n00%d' % i: [] for i in range(6)}
    for (x, y), cost in edges.items():
        adjacency[x].append((y, cost, frozenset()))
        adjacency[y].append((x, cost, frozenset()))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    neighbors, terminal = (lambda p: adjacency[p]), {'n005': .1}
    short, long = ['n000', 'n001', 'n005', SINK], ['n000', 'n001', 'n002', 'n005', SINK]
    exact = lambda path: sum((Fraction(edges[tuple(sorted(pair))]) for pair in zip(path[:-2], path[1:-1])), Fraction(.1))
    assert abs(exact(short) - exact(long)) <= exact(long) * TIE, 'the premise: the two cost the same to twelve digits'
    assert (.5 + .9) + (.3 + .1) < .5 + (1.2 + .1), 'the premise: a forward-plus-backward sum ranks the longer route cheaper'
    assert _search_bidirectional('n000', neighbors, terminal) == (short, .5 + 1.2 + .1)
    assert _search_reference('n000', neighbors, terminal)[0] == short
    for name, advance in SIDES.items():
        assert _search_bidirectional('n000', neighbors, terminal, advance=advance)[0] == short, name


def test_the_tie_window_is_exactly_one_part_in_a_trillion():
    for scale in (1, 7):
        cheap = scale * 1e12
        window = float(scale)       # one part in 10**12 of the cheapest cost, exactly
        step = math.ulp(cheap)      # the next float up
        # A rival of one step costing exactly the window above two steps of exactly `cheap` in all: it ties and wins; one float
        # higher it does not, and the cheaper, longer route wins.
        neighbors, terminal, long, short = two_routes([cheap / 2, cheap / 2], [cheap + window])
        assert _search_bidirectional('s', neighbors, terminal)[0] == short, scale
        neighbors, terminal, long, short = two_routes([cheap / 2, cheap / 2], [cheap + window + step])
        assert _search_bidirectional('s', neighbors, terminal)[0] == long, scale
        # Every placement of the rival around the window, with either route holding the fewer steps, against the exact rule.
        for rival in (cheap + window, cheap + window - step, cheap + window + step, cheap + 10 * window, cheap + step, cheap,
                      cheap - step, cheap - window, cheap - window - step, cheap - 10 * window):
            for hops_a, hops_b in (([cheap / 2, cheap / 2], [rival]), ([rival], [cheap / 2, cheap / 2]),
                                   ([cheap], [rival / 2, rival / 2]), ([rival / 2, rival / 2], [cheap])):
                neighbors, terminal, a, b = two_routes(hops_a, hops_b)
                routes = [(tuple(path[:-1]), sum(map(Fraction, hops))) for path, hops in ((a, hops_a), (b, hops_b))]
                found = _search_bidirectional('s', neighbors, terminal)
                assert tuple(found[0][:-1]) in tie_answer(routes)[3], (scale, rival - cheap, hops_a, hops_b)


def test_exact_arithmetic_beats_float_absorption():
    # 2**53 + 1 is not a float, so adding 1.0 twice to 2**53 loses both: the three-step route looks cheaper by 2 in float.
    # Exactly the two routes cost the same, and the one-step route has fewer steps.
    huge = 2. ** 53
    neighbors, terminal, long, short = two_routes([huge, 1., 1.], [huge + 2.])
    assert _search_reference('s', neighbors, terminal)[0] == long
    assert _search_bidirectional('s', neighbors, terminal) == (short, huge + 2.)
    # Ten hops of 0.1 cost one part in 10**16 more than 1.0: a tie, and the one-step route wins.
    neighbors, terminal, long, short = two_routes([.1] * 10, [1.])
    assert _search_bidirectional('s', neighbors, terminal)[0] == short
    # Three parts in 10**12 is a real difference: the cheaper, longer route wins.
    neighbors, terminal, long, short = two_routes([.1] * 10, [1. + 3e-12])
    assert _search_bidirectional('s', neighbors, terminal)[0] == long


def test_a_route_of_a_hundred_thousand_hops_rounds_past_the_tie_window_yet_ties_exactly():
    # 0.7 a hundred thousand times against 1.4 (exactly twice 0.7) fifty thousand times: the exact costs are equal, but the
    # float sums differ by more than one part in 10**12, so only the allowance for rounding in the stopping rule keeps every
    # node of the shorter route inside the nodes the search settles. Fewest steps wins.
    hops = 100000
    neighbors, terminal, long, short = two_routes([.7] * hops, [1.4] * (hops // 2))
    assert hops * Fraction(.7) == (hops // 2) * Fraction(1.4)
    sums = []
    for parts in ([.7] * hops, [1.4] * (hops // 2)):
        total = 0.
        for part in parts:
            total += part
        sums.append(total)
    assert sums[1] - sums[0] > 1e-12 * sums[0], 'the premise: the route with fewer steps is the dearer in float, by more than the tie window'
    assert _search_bidirectional('s', neighbors, terminal) == (short, sums[1])


def test_the_answer_does_not_depend_on_which_side_the_search_grows():
    checked = Counter()
    for seed in range(600):
        case = make_case(seed + 52000, BRUTE_MODES[seed % 4], small=True)
        answers = {name: repr(search_with(case, advance)) for name, advance in SIDES.items()}
        assert len(set(answers.values())) == 1, (seed, answers)
        checked['small'] += 1
        checked['found'] += answers['balanced'] != 'None'
    for seed in range(300):
        case = make_float_case(seed, ('decimal', 'log')[seed % 2])
        rng = random.Random(seed)
        answers = {repr(search_with(case, advance)) for advance in (*SIDES.values(), lambda work, f, b: rng.randrange(2))}
        assert len(answers) == 1, seed
        checked['larger'] += 1
    assert checked['found'] >= 300 and checked['larger'] == 300


def test_every_tied_route_stays_inside_what_the_two_frontiers_settled_and_inside_the_tube():
    """The lemma, checked directly: under five ways to choose the side to grow, including never growing one of them."""
    seen = Counter()
    cases = [make_float_case(seed, mode) for mode in ('decimal', 'log') for seed in range(450)]
    cases += [make_ladder_case(seed) for seed in range(450)] + [make_case(seed, mode, small=True) for seed in range(250) for mode in BRUTE_MODES]
    for index, case in enumerate(cases):
        routes = tied_routes(case)
        if routes is None:
            seen['too many tied routes to list'] += 1
            continue
        rng = random.Random(index)
        for name, advance in (*SIDES.items(), ('random', lambda work, f, b: rng.randrange(2))):
            frontiers, tube = frontiers_of(case, advance)
            if frontiers is None:
                assert not routes, 'the frontiers never met although a route exists'
                continue
            (settled_f, settled_b), (labels_f, labels_b) = frontiers.settled, frontiers.labels
            seen[name] += 1
            seen['tied routes'] += len(routes)
            assert all(p in labels_f and p in labels_b for p in tube.heads) and tube.heads <= tube.nodes
            for path in routes:
                last = len(path) - 1
                # for all i <= j: x_i is settled forward or x_j is settled backward
                for i in range(last + 1):
                    for j in range(i, last + 1):
                        assert path[i] in settled_f or path[j] in settled_b, (name, path, i, j)
                # so a prefix is settled forward and the rest backward, and the junction is a head
                a = next((i for i, p in enumerate(path) if p not in settled_f), last + 1)
                assert all(p in settled_b for p in path[a:]), (name, path)
                assert path[min(a, last)] in tube.heads, (name, path)
                assert set(path) <= tube.nodes, (name, path)
    print('lemma: %s' % dict(seen))
    assert min(seen[name] for name in (*SIDES, 'random')) >= 1500 and seen['tied routes'] >= 6000


def test_the_exact_solver_is_exact_over_a_whole_graph_with_no_first_pass():
    """_fewest_steps_exact on the complete arcs of small graphs, against enumeration: it needs no candidate set to be right."""
    for seed in range(1500):
        case = make_case(seed + 77000, BRUTE_MODES[seed % 4], small=True)
        entry, blocked_at, seeds = _filters(case.start, case.terminal, case.blocked_nodes, case.blocked_edges)
        arcs = {p: [(o, c) for o, c, k in rows if o not in entry and o not in blocked_at.get(p, ()) and not k & case.excluded_contexts]
                for p, rows in case.adjacency.items()}
        found = _fewest_steps_exact(case.start, arcs, seeds)
        routes = exact_routes(case)
        if not routes:
            assert found is None
            continue
        least, steps, cheapest, winners = tie_answer(routes)
        assert tuple(found[0]) == min(winners, key=lambda path: path[::-1]), seed
        assert (len(found[0]) - 1, exact_cost(case, [*found[0], SINK])) == (steps, cheapest) and found[2] == float(least), seed


def grow(start, adjacency, goal):
    entry, blocked_at, seeds = _filters(start, {goal: 0.}, frozenset(), frozenset())
    meter = _Meter()
    return meter, _grow_frontiers(start, lambda p: adjacency[p], seeds, entry, blocked_at, frozenset(), meter)


def test_the_near_tie_pass_costs_almost_nothing_on_costs_that_are_not_equal(monkeypatch):
    """Real-valued costs: the extended stop adds no expansion, and the pass re-reads only rows the search already read."""
    names, adjacency = small_world(5)
    rng = random.Random(1)
    extra, rereads, lengths = 0, [], []
    for _ in range(20):
        start, goal = rng.sample(names, 2)
        reads = []

        def neighbors(person):
            reads.append(person)
            return adjacency[person]
        meter = _Meter()
        found = _search_bidirectional(start, neighbors, {goal: 0.}, meter=meter)
        searched, repeated = reads[:meter.expanded], reads[meter.expanded:]
        assert set(repeated) <= set(searched) and len(repeated) == len(set(repeated)), 'the second pass reads only rows the first read'
        rereads.append(len(repeated))
        lengths.append(len(found[0]) - 2)
        assert len(repeated) <= len(found[0]) + 1, (len(repeated), found[0])
        # the classic stop (the two open costs reach the best meeting) expands the same nodes: no tie, no extra work
        with monkeypatch.context() as patch:
            patch.setattr(closeness, 'STOP_SLACK', 1. - 1e-9)
            extra += meter.expanded - grow(start, adjacency, goal)[0].expanded
    print('small world: rows re-read per search %s for routes of %s steps; extra expansions %d' % (rereads, lengths, extra))
    assert extra == 0


def test_a_tube_that_lost_the_cheapest_route_raises_instead_of_answering(monkeypatch):
    edges = {('a', 's'): 1., ('a', 'g'): 1., ('b', 's'): 3., ('b', 'c'): 1., ('c', 'g'): 1.}      # cost 2 by a, 5 by b and c
    adjacency = {p: [] for p in 'sabcg'}
    for (x, y), cost in edges.items():
        adjacency[x].append((y, cost, frozenset()))
        adjacency[y].append((x, cost, frozenset()))
    neighbors = lambda p: adjacency[p]
    assert _search_bidirectional('s', neighbors, {'g': 0.}) == (['s', 'a', 'g', SINK], 2.)
    # A tube holding only the dearer route would still give an answer; the search compares it with its own cheapest meeting.
    dearer = {'s': [('b', 3.)], 'b': [('s', 3.), ('c', 1.)], 'c': [('b', 1.), ('g', 1.)], 'g': [('c', 1.)]}
    monkeypatch.setattr(closeness, '_tube', lambda *args: closeness._Tube(set(), set(dearer), dearer))
    with pytest.raises(RuntimeError, match='lost the cheapest route'):
        _search_bidirectional('s', neighbors, {'g': 0.})


def test_cancellation_reaches_the_near_tie_pass_and_the_exact_solver():
    names, adjacency = chain(60)
    event, reads = threading.Event(), []

    def neighbors(person):
        if person in reads:
            event.set()            # the first row the second pass re-reads cancels the job
        reads.append(person)
        return adjacency[person]
    meter = _Meter()
    with cancel_scope(event):
        with pytest.raises(RouteCancelled):
            _search_bidirectional('n000', neighbors, {'n059': 0.}, meter=meter)
    assert len(reads) - meter.expanded <= 1, 'cancelled by the next row'
    arcs = {p: [(o, c) for o, c, _ in rows] for p, rows in adjacency.items()}
    event = threading.Event()
    event.set()
    with cancel_scope(event):
        with pytest.raises(RouteCancelled):
            _fewest_steps_exact('n000', arcs, {'n059': 0.})
    assert _fewest_steps_exact('n000', arcs, {'n059': 0.})[1] == 59.      # unscoped, the same call just answers


def oracle_table(mode, count, first=0):
    """Oracle, new search and the exact reference on `count` of the verifier's tie-heavy graphs; counts by category.

    A category is how the new route relates to the oracle's. A violation is a status mismatch, a route that is not the exact
    answer, a cost more than the tie width from the oracle's or the least, or more steps than the oracle's.
    """
    table = Counter(cases=0, violations=0)
    for seed in range(first, first + count):
        case = make_float_case(seed, mode)
        oracle, found, want = run_search(_search_reference, case), run_search(_search_bidirectional, case), spec_reference(case)
        table['cases'] += 1
        if want is None:
            table['violations'] += not (oracle is None and found is None)
            table['unreachable'] += 1
            continue
        steps, cheapest, least = want
        width = least * TIE
        new_steps, old_steps = len(found[0]) - 2, len(oracle[0]) - 2
        new_cost, old_cost = exact_cost(case, found[0]), exact_cost(case, oracle[0])
        bad = (new_steps, new_cost) != (steps, cheapest) or abs(new_cost - old_cost) > width or old_cost - least > width
        if found[0] == oracle[0]:
            kind = 'same route'
        elif new_steps < old_steps:
            kind = 'fewer steps'
        elif new_steps > old_steps:
            kind, bad = 'MORE steps', True
        elif new_cost == old_cost:
            kind = 'equal-cost swap'
        elif new_cost < old_cost:
            kind = 'same steps, lower exact cost'
        else:
            kind, bad = 'WORSE exact cost', True
        table[kind] += 1
        table['violations'] += bad
    return table


def format_table(label, table):
    kinds = ('same route', 'equal-cost swap', 'same steps, lower exact cost', 'fewer steps', 'unreachable', 'MORE steps', 'WORSE exact cost')
    return '%s: %d graphs: %s; violations %d' % (label, table['cases'], ', '.join('%s %d' % (k, table[k]) for k in kinds), table['violations'])


@pytest.mark.parametrize('mode', ['decimal', 'log'])
def test_against_the_oracle_on_the_verifiers_tie_graphs(mode):
    """2,000 of the verifier's 40,000 tie-heavy graphs per mode (--ties N runs more); categories are counted and printed."""
    table = oracle_table(mode, 2000)
    print(format_table(mode, table))
    assert table['violations'] == 0 and table['cases'] == 2000
    assert table['fewer steps'] + table['same steps, lower exact cost'] >= 1, 'the rule must be exercised'


# --------------------------------------------------------------------------- unit tests
def chain(length):
    names = ['n%03d' % i for i in range(length)]
    adjacency = {p: [] for p in names}
    for a, b in zip(names, names[1:]):
        adjacency[a].append((b, 1., frozenset({'c'})))
        adjacency[b].append((a, 1., frozenset({'c'})))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    return names, adjacency


def test_no_anchored_target_is_unreachable_without_expanding_the_origin():
    names, adjacency = chain(100)
    neighbors = lambda p: adjacency[p]
    for terminal, blocked_nodes, blocked_edges in [
            ({}, frozenset(), frozenset()),
            ({'n099': 0.}, frozenset({'n099'}), frozenset()),
            ({'n099': 0.}, frozenset(), frozenset({(SINK, 'n099')}))]:
        meter = _Meter()
        assert _search_bidirectional('n000', neighbors, terminal, blocked_nodes, blocked_edges, meter=meter) is None
        assert meter.expanded == 0  # the contract allows the origin itself; this search does not even expand that
    # The oracle has to scan the whole component to learn the same thing.
    meter = _Meter()
    assert _search_reference('n000', neighbors, {}, meter=meter) is None
    assert meter.expanded == 100


def test_progress_reports_expanded_checks_and_elapsed_summed_over_both_sides():
    names, adjacency = chain(41)
    ticks = itertools.count()
    reports, expanded, rows = [], [], []
    meter = _Meter(reports.append, clock=lambda: float(next(ticks)))

    def neighbors(person):
        expanded.append(person)
        meter.checks += len(adjacency[person])
        rows.append(len(adjacency[person]))
        return adjacency[person]
    path, cost = _search_bidirectional('n000', neighbors, {'n040': .5}, meter=meter)
    assert path == names + [SINK] and cost == 40.5
    # Both ends were searched, and every expansion was reported by the one meter. The near-tie pass then re-reads the rows of
    # nodes already expanded, and reports nothing.
    searched = expanded[:meter.expanded]
    assert min(searched) == 'n000' and max(searched) == 'n040'
    assert sum(1 for p in searched if p < 'n020') > 3 and sum(1 for p in searched if p >= 'n020') > 3
    assert meter.expanded <= len(expanded) and set(expanded[meter.expanded:]) <= set(searched)
    assert meter.checks == sum(rows)
    assert reports and set(reports[0]) == {'expanded', 'neighbor_checks', 'elapsed_ms'}
    assert reports[-1] == {'expanded': meter.expanded, 'neighbor_checks': sum(rows[:meter.expanded]), 'elapsed_ms': reports[-1]['elapsed_ms']}
    assert all(isinstance(r['elapsed_ms'], int) for r in reports)
    assert [r['elapsed_ms'] for r in reports] == sorted(r['elapsed_ms'] for r in reports)
    assert [r['expanded'] for r in reports] == list(range(1, meter.expanded + 1))


@pytest.mark.parametrize('search', SEARCHES)
def test_cancelled_scope_stops_a_search_within_64_expansions(search):
    names, adjacency = chain(400)
    for count in (0, 10):
        event = threading.Event()
        calls = []
        def neighbors(person):
            calls.append(person)
            if len(calls) == count:
                event.set()
            return adjacency[person]
        if not count:
            event.set()
        meter = _Meter()
        with cancel_scope(event):
            with pytest.raises(RouteCancelled):
                search('n000', neighbors, {'n399': 0.}, meter=meter)
        assert count <= meter.expanded <= count + 64
        assert len(calls) <= count + 64
    # No scope, or a scope whose event is clear: the search is unchanged.
    meter = _Meter()
    with cancel_scope(threading.Event()):
        assert search('n000', lambda p: adjacency[p], {'n399': 0.}, meter=meter)[1] == 399.
    assert search('n000', lambda p: adjacency[p], {'n399': 0.}, meter=meter)[1] == 399.


def test_cancel_scopes_nest_restore_and_share_one_module():
    first, second = threading.Event(), threading.Event()
    second.set()
    cancel.check_cancelled()
    with cancel_scope(first):
        with cancel_scope(second):
            with pytest.raises(RouteCancelled):
                cancel.check_cancelled()
        cancel.check_cancelled()
    cancel.check_cancelled()
    # Another thread's scope never cancels this one.
    seen = []
    def other():
        with cancel_scope(second):
            seen.append('in')
            with pytest.raises(RouteCancelled):
                cancel.check_cancelled()
    with cancel_scope(first):
        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
        cancel.check_cancelled()
    assert seen == ['in']
    assert closeness.check_cancelled is cancel.check_cancelled


def test_oracle_keeps_the_former_tie_order():
    # The one-directional search takes equal keys in the order they were pushed,
    # which is sorted neighbour order. Pinning it keeps the oracle the old search.
    edges = {('a', 's'): 1., ('b', 's'): 1., ('c', 's'): 1., ('a', 't'): 1., ('b', 't'): 1., ('c', 't'): 1.}
    adjacency = {p: [] for p in 'abcst'}
    for (x, y), cost in edges.items():
        adjacency[x].append((y, cost, frozenset()))
        adjacency[y].append((x, cost, frozenset()))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    neighbors = lambda p: adjacency[p]
    assert _search_reference('s', neighbors, {'t': 0.}) == (['s', 'a', 't', SINK], 2.)
    assert _search_reference('s', neighbors, {'t': 0.}, frozenset({'a'})) == (['s', 'b', 't', SINK], 2.)
    assert _search_reference('s', neighbors, {'t': 0.}, frozenset({'a', 'b'})) == (['s', 'c', 't', SINK], 2.)
    # Any equal-score path is acceptable from the new search, but never a worse one.
    for blocked in (frozenset(), frozenset({'a'}), frozenset({'a', 'b'})):
        found = _search_bidirectional('s', neighbors, {'t': 0.}, blocked)
        assert found[1] == 2. and len(found[0]) == 4 and found[0][1] not in blocked


# Costs written as decimals, so that the tie can be checked. In each graph two routes cost the same on paper and, in exact
# arithmetic, within a few parts in 10**17 of each other; floating point separates them by rounding, differently for
# different orders of addition. The tie rule decides instead: costs within one part in 10**12 tie, and the tied route with
# fewer steps wins, whatever the float sums say.
NEAR_TIES = [
    # n0-n2-SINK costs .5 + .3 and n0-n2-n3-SINK costs .5 + .2 + .1: .8 both on paper. Added from the origin the two-step
    # route comes to .7999999999999999 and the one-step route to .8, so the former one-directional search took the two-step
    # route; the rule takes the one-step route (which here is also the cheaper in exact arithmetic).
    pytest.param({('n1', 'n3'): .25, ('n2', 'n3'): .2, ('n0', 'n2'): .5}, {'n2': .3, 'n3': .1},
                 (['n0', 'n2', 'n3', SINK], 0.7999999999999999), (['n0', 'n2', SINK], 0.8), id='fewer-steps-is-also-cheaper'),
    # n0-n3-SINK and n0-n1-n3-SINK both cost .6 on paper and both sum to 0.6000000000000001 from the origin. In exact
    # arithmetic the two-step route is the cheaper by 3e-17, far inside the tie window, so fewer steps still wins: the
    # one-step route. (The one-directional search already returned it; a bidirectional search that compared forward plus
    # backward float sums returned the two-step route here.)
    pytest.param({('n0', 'n3'): .4, ('n1', 'n3'): .25, ('n0', 'n1'): .15}, {'n3': .2},
                 (['n0', 'n3', SINK], 0.6000000000000001), (['n0', 'n3', SINK], 0.6000000000000001), id='fewer-steps-beats-lower-exact-cost'),
]


@pytest.mark.parametrize('edges,terminal,old,new', NEAR_TIES)
def test_decimal_near_ties_go_to_the_route_with_fewer_steps_inside_the_tie_window(edges, terminal, old, new):
    """The two decimal graphs that pinned the old divergence now pin the rule: both routes tie, the shorter one wins
    (before the lower exact cost), and it reports the float cost summed from the origin."""
    adjacency = {}
    for (x, y), cost in edges.items():
        adjacency.setdefault(x, []).append((y, cost, frozenset()))
        adjacency.setdefault(y, []).append((x, cost, frozenset()))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    case = Case('n0', adjacency, {pair: (cost, frozenset()) for pair, cost in edges.items()}, terminal,
                frozenset(), frozenset(), frozenset())
    routes = exact_routes(case)
    least, steps, cheapest, winners = tie_answer(routes)
    assert len(routes) == 2 and len({len(path) for path, _ in routes}) == 2, 'the premise: two routes of different lengths'
    assert all(cost - least <= least * TIE for _, cost in routes), 'the premise: both cost the same to twelve digits'
    shorter = min((path for path, _ in routes), key=len)
    assert winners == {shorter} and steps == len(shorter) - 1, 'the rule: the tied route with fewer steps'
    reference, found = run_search(_search_reference, case), run_search(_search_bidirectional, case)
    assert reference == old, 'what the one-directional search returned: the order of float addition decided'
    assert found == new and tuple(found[0][:-1]) == shorter
    validate(case, *found)  # the reported cost is the returned route's own, its hops summed in order from the origin
    assert abs(reference[1] - found[1]) < 1e-12, 'a cost that differs from the oracle at most at rounding level'
    for name, advance in SIDES.items():
        assert search_with(case, advance) == new, name


def small_world(seed, n=3000, half=4, rewire=.1):
    rng = random.Random(seed)
    names = ['n%05d' % i for i in range(n)]
    edges = {}
    for i in range(n):
        for k in range(1, half + 1):
            j = rng.randrange(n) if rng.random() < rewire else (i + k) % n
            if i != j:
                edges.setdefault(tuple(sorted((names[i], names[j]))), (rng.uniform(.5, 2.), frozenset()))
    adjacency = {p: [] for p in names}
    for (a, b), (cost, contexts) in edges.items():
        adjacency[a].append((b, cost, contexts))
        adjacency[b].append((a, cost, contexts))
    for rows in adjacency.values():
        rows.sort(key=lambda row: row[0])
    return names, adjacency


def test_bidirectional_expands_far_fewer_nodes_than_the_oracle_on_a_small_world():
    names, adjacency = small_world(5)
    neighbors = lambda p: adjacency[p]
    rng = random.Random(1)
    ratios, old_total, new_total = [], 0, 0
    for _ in range(20):
        start, goal = rng.sample(names, 2)
        old, new = _Meter(), _Meter()
        expected = _search_reference(start, neighbors, {goal: 0.}, meter=old)
        found = _search_bidirectional(start, neighbors, {goal: 0.}, meter=new)
        assert found[0] == expected[0] and found[1] == expected[1]    # real costs: one optimum
        ratios.append(old.expanded / new.expanded)
        old_total, new_total = old_total + old.expanded, new_total + new.expanded
    print('small world: median expansion ratio %.1fx, total %.1fx' % (statistics.median(ratios), old_total / new_total))
    assert statistics.median(ratios) >= 6 and old_total / new_total >= 6 and min(ratios) >= 2


# --------------------------------------------------------------------------- real index
def synthetic_router(seed, people=120, offices=12, tenures=(1, 4), span=(90, 1500), reach=2, jump=.15, quantize=0,
                     person_format='p%05d'):
    """A seeded small world of dated office tenures; offices ring-adjacent, a few jumps.

    person_format only names the people (the world is the same for any format that sorts the same way); the cold-path
    benchmark uses 'person:hill:%06d' so that the people index counts them.
    """
    rng = random.Random(seed)
    first, horizon = date(2008, 1, 1), (date(2026, 6, 1) - date(2008, 1, 1)).days
    nodes = {}
    for i in range(offices):
        nodes['office:%04d' % i] = {'kind': 'container', 'container_kind': 'member_office',
                                    'member_bioguide': 'M%04d' % i, 'label': 'Office %d' % i}
    ids = [person_format % i for i in range(people)]
    for i, p in enumerate(ids):
        nodes[p] = {'kind': 'person', 'label': 'Person %05d' % i, 'member_ids': [], 'committee_ids': [], 'policy_ids': []}
    episodes = []
    for p in ids:
        home = rng.randrange(offices)
        for _ in range(rng.randint(*tenures)):
            office = rng.randrange(offices) if rng.random() < jump else (home + rng.randint(-reach, reach)) % offices
            offset = rng.randrange(horizon)
            length = rng.randint(*span)
            if quantize:
                offset, length = quantize * (offset // quantize), quantize * max(1, length // quantize)
            start = first + timedelta(days=offset)
            end = min(start + timedelta(days=length), date(2026, 9, 11))
            if end > start:
                episodes.append({'person_id': p, 'employer_id': 'office:%04d' % office, 'start': start.isoformat(),
                                 'end_exclusive': end.isoformat(), 'employment_verified': True,
                                 'provenance': {'work_route_source': 'politico_profile_history'}})
    router = AdvocacyRouter(nodes, episodes, 'bidirectional-%d' % seed, AS_OF)
    members = ['M%04d' % i for i in range(offices)]
    router.member_context = MemberContext([(members[i], members[(i + 1) % offices], 1 + i % 3) for i in range(offices)]
                                          + [(members[i], members[(i * 7 + 3) % offices], 2) for i in range(0, offices, 2)])
    return router, ids, sorted(n for n, v in nodes.items() if v['kind'] == 'container')


def roster(router, office):
    return tuple(sorted({p for p, rows in router.by_person.items() if any(e['employer_id'] == office for e in rows)}))


def settled_options(router, **extra):
    return router._options({'objective': 'work_time', 'alternative_strategy': 'ranked', **extra})


def stripped(result):
    drop = {'expanded', 'neighbor_checks', 'pairs_scored', 'search_elapsed_ms', 'cache_hit'}
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items() if k not in drop}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return json.dumps(clean(result), sort_keys=True)


def random_queries(router, ids, offices, rng, people, office_count):
    queries = [(a, (b,), None) for a, b in (rng.sample(ids, 2) for _ in range(people))]
    for _ in range(office_count):
        office = rng.choice(offices)
        if roster(router, office):
            queries.append((rng.choice(ids), roster(router, office), office))
    return queries


def query_options(router, ids, offices, rng, origin, office):
    request = {'route_count': rng.choice((1, 3, 5)), 'uncertainty_weight': rng.choice((0., 1., 2.5))}
    if office:
        request['_target_office'] = office
    if rng.random() < .5:
        request['_excluded_nodes'] = tuple(sorted(p for p in rng.sample(ids, 3) if p != origin))
        request['_excluded_contexts'] = tuple(sorted(rng.sample(offices, 2))) if rng.random() < .5 else ()
    return settled_options(router, **request)


def test_route_payloads_for_identical_paths_are_byte_identical_to_the_oracle():
    identical = swaps = 0
    for seed, quantize in ((1, 0), (2, 0), (3, 90)):
        router, ids, offices = synthetic_router(seed, people=140 + 40 * seed, offices=14, quantize=quantize)
        new, oracle = ClosenessRoutes(router), ClosenessRoutes(router, search=_search_reference)
        rng = random.Random(seed)
        for origin, targets, office in random_queries(router, ids, offices, rng, 25, 12):
            options = query_options(router, ids, offices, rng, origin, office)
            got, expected = new.route(origin, targets, options), oracle.route(origin, targets, options)
            assert got['status'] == expected['status']
            assert got['algorithm'] == expected['algorithm'] and got['heuristic'] == 0
            if got['status'] != 'found':
                continue
            if got['node_ids'] == expected['node_ids'] and [r['node_ids'] for r in got.get('routes', [])] == [
                    r['node_ids'] for r in expected.get('routes', [])]:
                assert stripped(got) == stripped(expected)
                identical += 1
            else:
                # An equal-score alternative (same cost and steps), or a near tie the tie rule sends to fewer steps: nothing else.
                assert math.isclose(got['cost'], expected['cost'], rel_tol=2e-12, abs_tol=0)
                assert got['steps'] <= expected['steps']
                swaps += 1
    print('payload comparisons: %d byte-identical, %d tie swaps' % (identical, swaps))
    assert identical >= 40


def test_reported_costs_are_the_evidence_costs_of_the_returned_hops():
    checked = 0
    for seed, weight in ((1, 0.), (2, 1.), (3, 2.5)):
        router, ids, offices = synthetic_router(seed, people=150 + 20 * seed, offices=12)
        routes = ClosenessRoutes(router)
        rng = random.Random(seed)
        for origin, targets, office in random_queries(router, ids, offices, rng, 15, 8):
            extra = {'_target_office': office} if office else {}
            result = routes.route(origin, targets, settled_options(router, route_count=3, uncertainty_weight=weight, **extra))
            for route in result.get('routes', []):
                total = 0.
                for hop in route['hops']:
                    assert hop['cost'] == -math.log(HOP_FACTOR * hop['strength']) + weight * hop['uncertainty']['cost']
                    total += hop['cost']
                if office:
                    factor = route['office_anchor_strength'] * (1 + MEMBER_BETA * route['member_context']['value']) / (1 + MEMBER_BETA)
                    total += -math.log(factor) + weight * route['office_anchor_uncertainty']['cost']
                assert route['cost'] == total, 'the cost the search ranked is not the cost of the evidence shown'
                checked += 1
    assert checked >= 60


@pytest.mark.parametrize('weight', [0., 1., 2.5])
def test_rows_hold_exactly_the_costs_and_contexts_of_a_direct_computation(weight):
    router, ids, _ = synthetic_router(3, people=90, offices=9)
    routes = ClosenessRoutes(router)
    options = settled_options(router, uncertainty_weight=weight)
    index = routes.index(options)
    cache = routes.row_cache(index, options, None)
    meter = _Meter()
    neighbors = _Neighbors(cache, lambda p: router._person_allowed(p, options), router.as_of, weight, meter)
    order = list(ids)
    random.Random(4).shuffle(order)
    for person in order:  # shuffled, so most rows are derived from a neighbour's finished row
        list(neighbors(person))
    for person in ids:
        expected = []
        for other in sorted(index.neighbors(person)):
            raw = index.pair(*sorted((person, other)))
            risk = uncertainty(raw['workplaces'], router.as_of)
            if raw['strength'] > 0:
                expected.append((other, -math.log(HOP_FACTOR * raw['strength']) + weight * risk['cost'],
                                 frozenset(evidence_contexts({**raw, 'uncertainty': risk}))))
        assert list(zip(*cache.rows[person])) == expected
    total = sum(len(index.neighbors(p)) for p in ids)
    assert meter.checks == total
    assert meter.pairs == total // 2, 'each pair is priced once, whichever endpoint is expanded first'
    assert all(isinstance(row[1], array) and isinstance(row[0], tuple) and isinstance(row[2], tuple)
               for row in cache.rows.values())


def test_a_cached_row_costs_a_fraction_of_the_full_evidence_it_replaces():
    router, ids, _ = synthetic_router(3, people=160, offices=10)
    routes = ClosenessRoutes(router)
    options = settled_options(router)
    index = routes.index(options)
    cache = routes.row_cache(index, options, None)
    neighbors = _Neighbors(cache, lambda p: True, router.as_of, 1., _Meter())
    for person in ids:
        list(neighbors(person))
    size = sum(sys.getsizeof(row) + sum(sys.getsizeof(part) for part in row) for row in cache.rows.values())
    assert cache.edges > 1000
    assert size / cache.edges < 64
    pair = next(tuple(sorted((p, q))) for p in ids for q in sorted(index.neighbors(p)) if index.pair(*sorted((p, q)))['strength'] > 0)
    evidence = closeness._full_evidence(index, pair, router.as_of, 1.)
    assert len(json.dumps(evidence)) > 4 * 64, 'the evidence dict the search no longer keeps per pair'


def test_row_cache_is_a_bounded_lru(monkeypatch):
    monkeypatch.setattr(_RowCache, 'MAX_ROWS', 3)
    monkeypatch.setattr(_RowCache, 'MAX_EDGES', 10)
    cache = _RowCache(None)
    row = lambda n: (tuple('x%d' % i for i in range(n)), array('d', [1.] * n), (frozenset(),) * n)
    cache.put('a', row(4))
    cache.put('b', row(4))
    cache.put('c', row(1))
    assert list(cache.rows) == ['a', 'b', 'c'] and cache.edges == 9
    assert cache.get('a') is cache.rows['a']          # a is now the most recent
    cache.put('d', row(1))                              # four rows: the least recent (b) goes
    assert list(cache.rows) == ['c', 'a', 'd'] and cache.edges == 6
    cache.put('c', row(3))                              # replacing a row re-counts its edges
    assert cache.edges == 8 and list(cache.rows)[-1] == 'c'
    cache.put('e', row(9))                              # over the edge cap: evict until it fits
    assert list(cache.rows) == ['e'] and cache.edges == 9
    cache.put('f', row(50))                             # one oversized row is still kept, alone
    assert list(cache.rows) == ['f'] and cache.edges == 50
    assert cache.intern(frozenset({'x'})) is cache.intern(frozenset({'x'}))


def test_tiny_caps_change_the_work_but_never_the_routes(monkeypatch):
    router, ids, offices = synthetic_router(4, people=150, offices=15)
    rng = random.Random(9)
    queries = random_queries(router, ids, offices, rng, 15, 8)
    requests = [(origin, targets, query_options(router, ids, offices, rng, origin, office)) for origin, targets, office in queries]
    baseline = ClosenessRoutes(router)
    expected = [stripped(baseline.route(*request)) for request in requests]
    monkeypatch.setattr(_RowCache, 'MAX_ROWS', 7)
    monkeypatch.setattr(_RowCache, 'MAX_EDGES', 60)
    monkeypatch.setattr(ClosenessRoutes, 'MAX_ROW_CACHES', 1)
    capped = ClosenessRoutes(router)
    for request, answer in zip(requests, expected):
        assert stripped(capped.route(*request)) == answer
        assert len(capped.row_caches) <= 1
        for cache in capped.row_caches.values():
            assert len(cache.rows) <= 7 or len(cache.rows) == 1
            assert cache.edges <= 60 or len(cache.rows) == 1


def test_rows_serve_every_distinct_route_iteration_and_repeated_requests():
    router, ids, _ = synthetic_router(5, people=200, offices=16)
    request = {'objective': 'work_time', 'route_count': 3}
    first = router.route(ids[3], ids[150], request)
    routes = router.closeness_routes
    assert first['routes_returned'] >= 2
    assert len(routes.row_caches) == 1, 'exclusions must not fragment the cache'
    assert first['routes'][0]['neighbor_checks'] > 0
    routes.cache.clear()                                # force the searches to run again
    second = router.route(ids[3], ids[150], request)
    assert [r['node_ids'] for r in second['routes']] == [r['node_ids'] for r in first['routes']]
    assert [r['expanded'] for r in second['routes']] == [r['expanded'] for r in first['routes']], 'cold and warm runs search alike'
    assert [r['neighbor_checks'] for r in second['routes']] == [0] * len(second['routes'])
    assert len(routes.row_caches) == 1
    # A different cost configuration is a different cache, with its own work.
    third = router.route(ids[3], ids[150], {**request, 'uncertainty_weight': 2.})
    assert len(routes.row_caches) == 2 and third['routes'][0]['neighbor_checks'] > 0


def test_cache_key_covers_every_cost_and_neighbour_input_and_no_exclusion(monkeypatch):
    monkeypatch.setattr(ClosenessRoutes, 'MAX_ROW_CACHES', 50)
    router, ids, offices = synthetic_router(2, people=60, offices=6)
    routes = ClosenessRoutes(router)
    options = settled_options(router)
    index = routes.index(options)
    reference = routes.row_cache(index, options, None)
    assert routes.row_cache(index, dict(options), None) is reference
    for change in ({'uncertainty_weight': 2.}, {'include_unresolved': True}, {'include_review': False},
                   {'include_historical': False}, {'work_sources': 'directory'}):
        changed = {**options, **change}
        assert routes.row_cache(routes.index(changed), changed, None) is not reference, change
    excluding = {**options, '_excluded_nodes': (ids[0],), '_excluded_edges': ((ids[0], ids[1]),),
                 '_excluded_contexts': (offices[0],), 'route_count': 3, 'alternative_strategy': 'distinct'}
    assert routes.row_cache(index, excluding, None) is reference
    assert routes.row_cache(index, options, frozenset(ids[:5])) is not reference
    members = routes.row_cache(index, options, set(ids[:5]))
    assert routes.row_cache(index, options, set(ids[:5])) is members
    assert routes.row_cache(index, options, set(ids[:6])) is not members
    router.as_of = '2026-09-11'
    try:
        assert routes.row_cache(index, options, None) is not reference
    finally:
        router.as_of = AS_OF
    revision = router.revision
    router.revision = 'another-revision'
    try:
        assert routes.row_cache(index, options, None) is not reference
    finally:
        router.revision = revision
    router.person_facts = type('Facts', (), {'network_excluded': {ids[0]}})()
    assert routes.row_cache(index, options, None) is not reference
    del router.person_facts
    assert routes.row_cache(index, options, None) is reference
    # The identity and length of router.nodes are not part of the key: the service builds an equal mapping on every
    # poll, and that must not empty a warm cache. The node fields a hop depends on are covered by nodes_revision.
    router.nodes = dict(router.nodes)
    assert routes.row_cache(index, options, None) is reference
    router.nodes = {**router.nodes, 'extra-node': dict(next(iter(router.nodes.values())))}
    assert routes.row_cache(index, options, None) is reference
    router.nodes_revision = 1
    revised = routes.row_cache(index, options, None)
    assert revised is not reference
    router.nodes_revision = 1
    assert routes.row_cache(index, options, None) is revised
    router.nodes_revision = 2
    assert routes.row_cache(index, options, None) is not revised
    del router.nodes_revision
    assert routes.row_cache(index, options, None) is reference


class _Colliding(frozenset):
    """A frozenset whose hash is forced: two different sets of one length now hash alike."""
    def __hash__(self):
        return 42


def test_the_set_itself_is_in_the_key_so_a_length_and_hash_collision_cannot_share_a_cache(monkeypatch):
    monkeypatch.setattr(ClosenessRoutes, 'MAX_ROW_CACHES', 50)
    router, ids, offices = synthetic_router(2, people=60, offices=6)
    routes = ClosenessRoutes(router)
    options = settled_options(router)
    index = routes.index(options)
    first = routes.row_cache(index, options, _Colliding(ids[:5]))
    second = routes.row_cache(index, options, _Colliding(ids[5:10]))
    assert first is not second, 'sets of equal length and equal hash must not share a cache'
    assert routes.row_cache(index, options, _Colliding(ids[:5])) is first
    router.person_facts = type('Facts', (), {'network_excluded': _Colliding(ids[:3])})()
    excluded_one = routes.row_cache(index, options, None)
    router.person_facts = type('Facts', (), {'network_excluded': _Colliding(ids[3:6])})()
    assert routes.row_cache(index, options, None) is not excluded_one


def test_full_evidence_is_built_once_and_only_for_pairs_on_candidate_or_returned_routes(monkeypatch):
    built = []
    original = closeness._full_evidence
    monkeypatch.setattr(closeness, '_full_evidence', lambda *args: built.append(args[1]) or original(*args))
    router, ids, offices = synthetic_router(6, people=300, offices=20)
    result = ClosenessRoutes(router).route(ids[1], (ids[200],), settled_options(router, route_count=4))
    assert result['status'] == 'found' and result['routes_returned'] >= 2
    on_routes = {tuple(sorted(pair)) for r in result['routes'] for pair in zip(r['node_ids'], r['node_ids'][1:])}
    assert on_routes <= set(built) and len(built) == len(set(built))
    assert len(built) * 5 <= result['neighbor_checks'], (len(built), result['neighbor_checks'])


def test_search_labels_state_what_ran():
    router, ids, offices = synthetic_router(1)
    for options in (settled_options(router), settled_options(router, route_count=3)):
        result = ClosenessRoutes(router).route(ids[0], (ids[9],), options)
        assert 'Bidirectional Dijkstra' in result['algorithm'] and result['heuristic'] == 0
        for route in result.get('routes', []):
            assert 'Bidirectional Dijkstra' in route['algorithm'] and route['heuristic'] == 0
    assert 'Bidirectional Dijkstra' in router.route(ids[0], ids[9], {'objective': 'work_time'})['algorithm']


def test_office_with_no_anchored_staff_is_unreachable_without_any_search():
    router, ids, offices = synthetic_router(1, people=100, offices=10)
    members = set(roster(router, offices[0]))
    outsiders = tuple(p for p in ids if p not in members)[:12]
    result = ClosenessRoutes(router).route(ids[0], outsiders, settled_options(router, _target_office=offices[0]))
    assert result['status'] == 'unreachable' and result['optimal']
    assert (result['expanded'], result['neighbor_checks']) == (0, 0)


def test_concurrent_requests_share_one_cache_and_agree_with_serial_runs():
    router, ids, offices = synthetic_router(7, people=180, offices=15)
    rng = random.Random(3)
    requests = [(o, t, query_options(router, ids, offices, rng, o, off)) for o, t, off in random_queries(router, ids, offices, rng, 16, 8)]
    serial = ClosenessRoutes(router)
    expected = [stripped(serial.route(*r)) for r in requests]
    shared = ClosenessRoutes(router)
    for round_number in range(2):  # the second round runs on rows the first round left behind
        shared.cache.clear()
        with ThreadPoolExecutor(4) as pool:
            assert list(pool.map(lambda r: stripped(shared.route(*r)), requests)) == expected
    assert len(shared.row_caches) <= len({options['uncertainty_weight'] for _, _, options in requests})


@pytest.mark.parametrize('search', SEARCHES)
def test_blocking_origin_terminal_arcs_and_the_lexicographic_tie_rule(search):
    names, adjacency = chain(6)
    neighbors = lambda p: adjacency[p]
    whole = names + [SINK]
    # The origin is never entered, so blocking it removes nothing; blocking the
    # only way through, or the only terminal arc, removes the route.
    assert search('n000', neighbors, {'n005': 0.}, frozenset({'n000'})) == (whole, 5.)
    assert search('n000', neighbors, {'n005': 0.}, frozenset({'n003'})) is None
    assert search('n000', neighbors, {'n005': 0.}, frozenset(), frozenset({('n002', 'n003')})) is None
    assert search('n000', neighbors, {'n005': 0.}, frozenset(), frozenset({(SINK, 'n005')})) is None
    # An anchored origin routes to the sink at zero steps when that is cheapest.
    assert search('n000', neighbors, {'n000': 1., 'n005': 0.}) == (['n000', SINK], 1.)
    assert search('n000', neighbors, {'n000': 6., 'n005': 0.}) == (whole, 5.)
    assert search('n000', neighbors, {'n001': 3., 'n005': 0.}) == (['n000', 'n001', SINK], 4.)
    # Equal cost goes to fewer steps, whichever side finds it.
    assert search('n000', neighbors, {'n001': 4., 'n005': 0.}) == (['n000', 'n001', SINK], 5.)
    assert search('n000', neighbors, {'n003': 2., 'n005': 0.}) == (['n000', 'n001', 'n002', 'n003', SINK], 5.)
    # Edges are undirected and blocked as pairs; a blocked context removes the edge.
    assert search('n005', neighbors, {'n000': 0.}) == (list(reversed(names)) + [SINK], 5.)
    assert search('n005', neighbors, {'n000': 0.}, excluded_contexts=frozenset({'c'})) is None


# --------------------------------------------------------------------------- ranked alternatives follow the tie rule
# Yen's candidates used to be ranked by reported (float) cost, then steps. They are now ranked like the first route: among the
# pending candidates whose exact cost is within one part in 10**12 of the cheapest, the fewest steps, then the lower exact
# cost, then the smaller ids read from the sink end; candidates further apart are ranked by cost alone.
def pend(people, exact):
    """One pending alternative as ClosenessRoutes holds it: (exact cost, reported cost, path ending at the sink)."""
    exact = Fraction(exact)
    return exact, float(exact), [*people.split(), SINK]


def test_next_alternative_takes_the_fewest_steps_inside_the_tie_window_and_the_cheapest_outside_it():
    for scale in (1, 7, Fraction(1, 3)):
        cheap, window, tiny = Fraction(scale) * 10 ** 12, Fraction(scale), Fraction(1, 10 ** 6)  # window: one part in 10**12, exactly
        for extra, short_wins in ((0, True), (window - tiny, True), (window, True), (window + tiny, False)):
            for order in (1, -1):
                pending = [pend('s a b g', cheap), pend('s g', cheap + extra)][::order]    # the long route is the cheaper
                path, cost = _next_alternative(pending)
                assert path == (['s', 'g', SINK] if short_wins else ['s', 'a', 'b', 'g', SINK]), (scale, extra, order)
                assert cost == float(cheap + extra if short_wins else cheap) and len(pending) == 1
        # the other way round the shorter route is also the cheaper, so it is first at any distance
        assert _next_alternative([pend('s a b g', cheap + 5 * window), pend('s g', cheap)])[0] == ['s', 'g', SINK]


def test_next_alternative_orders_by_steps_then_exact_cost_then_ids_read_from_the_sink_end():
    base = Fraction(10 ** 6)
    window = base / 10 ** 12
    tenth = window / 10
    # inside the window the fewest steps wins, even over a lower exact cost
    assert _next_alternative([pend('s a b z g', base), pend('s g', base + 9 * tenth)])[0] == ['s', 'g', SINK]
    # with equal steps the lower exact cost wins, even over smaller ids
    assert _next_alternative([pend('s a x g', base + tenth), pend('s a y g', base)])[0] == ['s', 'a', 'y', 'g', SINK]
    # with equal steps and equal exact cost the smaller ids read from the sink end win (y < z, although b < c), in either order
    for order in (1, -1):
        assert _next_alternative([pend('s b z g', base), pend('s c y g', base)][::order])[0] == ['s', 'c', 'y', 'g', SINK]
    # outside the window cost alone decides, whatever the steps
    assert _next_alternative([pend('s g', base + 2 * window), pend('s a b c g', base)])[0] == ['s', 'a', 'b', 'c', 'g', SINK]


def test_next_alternative_ranks_a_whole_list_one_pick_at_a_time_against_the_cheapest_pending_one():
    base = Fraction(100)
    window = base / 10 ** 12
    pending = [pend('s a b c g', base), pend('s a g', base + window / 2), pend('s b g', base + window / 4),
               pend('s g', base + 3 * window), pend('s a c g', base)]
    order = []
    while pending:
        order.append(' '.join(_next_alternative(pending)[0][:-1]))
    # the window is measured from the cheapest candidate still pending: the one-step route sits outside it until last
    assert order == ['s b g', 's a g', 's a c g', 's a b c g', 's g']


def test_next_alternative_properties_on_random_pending_lists():
    rng = random.Random(8)
    picked = Counter()
    for _ in range(2000):
        base = Fraction(rng.choice((1, 7, 10 ** 6, 10 ** 12 + 1)))
        pending, seen = [], set()
        for _ in range(rng.randint(1, 8)):
            people = ['s', *rng.sample('abcdef', rng.randint(0, 4)), 'g']
            # tenths of a tie window above base
            exact = base + base * TIE * Fraction(rng.choice((0, 0, 1, 3, 5, 9, 10, 11, 15, 30, 100)), 10)
            if tuple(people) not in seen:
                seen.add(tuple(people))
                pending.append((exact, float(exact), [*people, SINK]))
        while pending:
            least = min(item[0] for item in pending)
            inside = [item for item in pending if item[0] <= least + least * TIE]
            before = list(pending)
            path, cost = _next_alternative(pending)
            chosen = next(item for item in before if item[2] == path)
            assert chosen in inside and chosen not in pending and len(pending) == len(before) - 1 and cost == chosen[1]
            assert all(len(other[2]) >= len(path) for other in inside), 'a tied candidate has fewer steps'
            same = [other for other in inside if len(other[2]) == len(path)]
            assert all(other[0] >= chosen[0] for other in same), 'a candidate of equal steps is cheaper'
            assert all(other[2][::-1] >= path[::-1] for other in same if other[0] == chosen[0]), 'equal cost, smaller ids'
            picked['picks'] += 1
            picked['several inside'] += len(inside) > 1
            picked['not the cheapest'] += chosen[0] > least
    assert picked['picks'] > 5000 and picked['several inside'] > 1500 and picked['not the cheapest'] > 300


class CostedIndex:
    """The real work index with each pair's hop cost fixed by the test.

    The cost rides in the pair's strength and the patched _hop_cost returns the strength as the cost, so the search, its
    alternatives and the evidence of the answer all run for real, on costs chosen to tie.
    """
    def __init__(self, index, costs):
        self.index, self.costs = index, costs

    def __getattr__(self, name):
        return getattr(self.index, name)

    def pair(self, a, b):
        return {**self.index.pair(a, b), 'strength': self.costs.get(tuple(sorted((a, b))), 0.)}

    def pair_row(self, a, b):
        """The row builder prices hops with pair_row, the evidence with pair: fix the cost in both."""
        return {**self.index.pair_row(a, b), 'strength': self.costs.get(tuple(sorted((a, b))), 0.)}


def costed_routes(monkeypatch, costs, **request):
    """A ClosenessRoutes over a real router in which each pair named in `costs` worked together for a year, and its options."""
    monkeypatch.setattr(closeness, '_hop_cost', lambda strength, risk, weight: strength)
    nodes = {p: {'kind': 'person', 'label': 'Person ' + p, 'member_ids': [], 'committee_ids': [], 'policy_ids': []}
             for pair in costs for p in pair}
    episodes = [{'person_id': p, 'employer_id': 'office:%s%s' % pair, 'start': '2020-01-01', 'end_exclusive': '2021-01-01',
                 'employment_verified': True, 'provenance': {'work_route_source': 'politico_profile_history'}}
                for pair in costs for p in pair]
    router = AdvocacyRouter(nodes, episodes, 'ranking-%d' % len(costs), AS_OF)
    routes = ClosenessRoutes(router)
    options = settled_options(router, uncertainty_weight=0., **request)
    routes.indexes[(options['include_review'], options['include_unresolved'], options['include_historical'],
                    options['work_sources'])] = CostedIndex(routes.index(options), costs)
    return routes, options


def ladder(last):
    """From a to d: a-m-d costs 1.0 in two steps, a-d costs 2.0 in one, and a-m-n-d costs 2.0 + `last` in three."""
    return {('a', 'm'): .5, ('d', 'm'): .5, ('a', 'd'): 2., ('m', 'n'): .75, ('d', 'n'): .75 + last}


SHORT, LONG = ['a', 'd'], ['a', 'm', 'n', 'd']


def exact_and_reported(costs, path):
    exact, reported = Fraction(0), 0.
    for pair in zip(path, path[1:]):
        exact, reported = exact + Fraction(costs[tuple(sorted(pair))]), reported + costs[tuple(sorted(pair))]
    return exact, reported


@pytest.mark.parametrize('last', [-1e-12, -2. ** -52, 0.])
def test_ranked_alternatives_inside_the_tie_window_list_the_route_with_fewer_steps_first(monkeypatch, last):
    costs = ladder(last)
    (short, short_cost), (long, long_cost) = exact_and_reported(costs, SHORT), exact_and_reported(costs, LONG)
    assert long <= short and short - long <= long * TIE, 'the premise: the longer route is the cheaper, and inside the window'
    routes, options = costed_routes(monkeypatch, costs, route_count=3)
    result = routes.route('a', ('d',), options)
    assert [r['node_ids'] for r in result['routes']] == [['a', 'm', 'd'], SHORT, LONG]
    assert [r['route_rank'] for r in result['routes']] == [1, 2, 3]
    # each route reports its own cost, summed from the origin; inside the window that can put the costs out of order by rounding
    assert [r['cost'] for r in result['routes']] == [1., short_cost, long_cost]
    assert (long_cost < short_cost) == (last < 0)


@pytest.mark.parametrize('last,first', [(-2e-9, LONG), (-1e-11, LONG), (-4e-12, LONG), (4e-12, SHORT), (1e-9, SHORT)])
def test_ranked_alternatives_further_apart_than_the_tie_window_rank_by_cost_as_before(monkeypatch, last, first):
    costs = ladder(last)
    (short, _), (long, _) = exact_and_reported(costs, SHORT), exact_and_reported(costs, LONG)
    assert abs(short - long) > min(short, long) * TIE, 'the premise: further apart than the window'
    routes, options = costed_routes(monkeypatch, costs, route_count=3)
    result = routes.route('a', ('d',), options)
    assert [r['node_ids'] for r in result['routes']] == [['a', 'm', 'd'], first, LONG if first == SHORT else SHORT]
    assert [r['cost'] for r in result['routes']] == sorted(r['cost'] for r in result['routes'])


def reported_cost_rule(pending):
    """The ranking before the tie rule: least reported cost, then fewest steps, then the order of discovery."""
    index = min(range(len(pending)), key=lambda i: (pending[i][1], len(pending[i][2]), i))
    _, cost, path = pending.pop(index)
    return path, cost


def test_ranked_alternatives_on_real_costs_come_in_the_order_the_reported_costs_gave(monkeypatch):
    """Real exposure costs have no near ties, so the tie rule changes no ranking and no payload."""
    compared = 0
    for seed in (1, 2):
        router, ids, offices = synthetic_router(seed, people=140 + 40 * seed, offices=14)
        rng = random.Random(seed)
        for origin, targets, office in random_queries(router, ids, offices, rng, 25, 12):
            options = query_options(router, ids, offices, rng, origin, office)
            got = ClosenessRoutes(router).route(origin, targets, options)
            with monkeypatch.context() as patch:
                patch.setattr(closeness, '_next_alternative', reported_cost_rule)
                expected = ClosenessRoutes(router).route(origin, targets, options)
            assert stripped(got) == stripped(expected), (seed, origin, targets)
            compared += len(got.get('routes', ())) > 1
    assert compared >= 20


# --------------------------------------------------------------------------- benchmark
def benchmark(graphs=3, people=20000, offices=1000, queries=30, seed0=11, out=print):
    """Oracle versus bidirectional on seeded small worlds; every run starts with an empty row cache.

    Returns {'person': [...], 'office': [...]}: per query, the work each search did
    and whether the two agreed (identical path, equal-score swap, near-tie resolved
    to fewer steps, or MISMATCH).
    """
    try:
        import psutil
        rss = lambda: psutil.Process().memory_info().rss / 2 ** 20
    except ImportError:
        rss = lambda: float('nan')
    table = {'person': [], 'office': []}
    for g in range(graphs):
        started = time.perf_counter()
        router, ids, office_ids = synthetic_router(seed0 + g, people=people, offices=offices, tenures=(2, 4),
                                                   span=(365, 1800), reach=4, jump=.1)
        routes = ClosenessRoutes(router)
        index = routes.index(settled_options(router, route_count=1))
        out('graph %d: %d people, %d offices, index in %.1fs, median all-time neighbours %d, rss %.0f MB'
            % (seed0 + g, people, offices, time.perf_counter() - started,
               statistics.median(len(index.neighbors(p)) for p in ids[:200]), rss()))
        calls = [0]
        def counting(original):
            def call(*args):
                calls[0] += 1
                return original(*args)
            return call
        for name in ('pair', 'pair_row'):  # pair arithmetic: the hop rows' pair_row and the evidence's pair
            setattr(index, name, counting(getattr(index, name)))
        rng = random.Random(100000 + seed0 + g)
        batches = {'person': [(a, (b,), None) for a, b in (rng.sample(ids, 2) for _ in range(queries))], 'office': []}
        while len(batches['office']) < queries:
            office = rng.choice(office_ids)
            if roster(router, office):
                batches['office'].append((rng.choice(ids), roster(router, office), office))
        for kind, batch in batches.items():
            for origin, targets, office in batch:
                request = settled_options(router, route_count=1, **({'_target_office': office} if office else {}))
                row, results = {}, {}
                for name, search in (('oracle', _search_reference), ('bidirectional', _search_bidirectional)):
                    routes.search = search
                    routes.row_caches.clear()
                    routes.cache.clear()
                    calls[0] = 0
                    began = time.perf_counter()
                    results[name] = routes.route(origin, targets, request)
                    row[name] = {'expanded': results[name]['expanded'], 'checks': results[name]['neighbor_checks'],
                                 'pairs': calls[0], 'ms': (time.perf_counter() - began) * 1000,
                                 'rows': sum(len(c.rows) for c in routes.row_caches.values()),
                                 'edges': sum(c.edges for c in routes.row_caches.values())}
                a, b = results['oracle'], results['bidirectional']
                row['found'], row['steps'] = a['status'] == 'found', a.get('steps')
                row['agreement'] = ('MISMATCH' if a['status'] != b['status'] or (a['status'] == 'found' and (
                    abs(a['cost'] - b['cost']) > 1e-9 or b['steps'] > a['steps']))
                    else 'near-tie' if a.get('steps') != b.get('steps')
                    else 'swap' if a.get('node_ids') != b.get('node_ids') else 'identical')
                table[kind].append(row)
        out('graph %d done in %.0fs, rss %.0f MB' % (seed0 + g, time.perf_counter() - started, rss()))
    return table


def summarize(table, out=print):
    summary = {}
    for kind, rows in table.items():
        summary[kind] = {}
        found = [r['steps'] for r in rows if r['found']]
        out('%s queries: %d, %d found, median %s steps' % (kind, len(rows), len(found), statistics.median(found) if found else '-'))
        for metric in ('expanded', 'checks', 'pairs', 'ms', 'rows', 'edges'):
            old = statistics.median(r['oracle'][metric] for r in rows)
            new = statistics.median(r['bidirectional'][metric] for r in rows)
            ratios = [r['oracle'][metric] / r['bidirectional'][metric] for r in rows if r['bidirectional'][metric]]
            summary[kind][metric] = (old, new, old / new if new else float('inf'), statistics.median(ratios) if ratios else float('inf'))
            out('  %-9s oracle %12.1f   bidirectional %12.1f   ratio of medians %7.1fx   median per-query ratio %7.1fx'
                % ((metric,) + summary[kind][metric]))
    counts = Counter(r['agreement'] for rows in table.values() for r in rows)
    out('agreement: %d identical paths, %d equal-score swaps, %d near ties resolved to fewer steps, %d cost-or-steps MISMATCHES'
        % (counts['identical'], counts['swap'], counts['near-tie'], counts['MISMATCH']))
    return summary, counts


def forget_routes(router):
    """Everything the router built for routes (work indexes, row caches, results, the contact index), so the next
    request meets a router nobody has asked yet."""
    router.closeness_routes = ClosenessRoutes(router)
    router.__dict__.pop('_contact_groups', None)
    return router.closeness_routes


def work_done(router, origin, target, count):
    """Candidate checks, pair arithmetic and overlap tests one request does from nothing, counted on the real calls."""
    calls, restore = Counter(), {}
    for name in ('pair', 'pair_row', 'overlaps'):
        original = getattr(ExposureIndex, name, None)
        if original is not None:
            def counted(self, *args, original=original, name=name):
                calls[name] += 1
                return original(self, *args)
            restore[name] = original
            setattr(ExposureIndex, name, counted)
    evidence = closeness._full_evidence

    def counted_evidence(*args):
        calls['evidence'] += 1
        return evidence(*args)
    closeness._full_evidence = counted_evidence
    try:
        forget_routes(router)
        result = router.route(origin, target, {'objective': 'work_time', 'route_count': count})
    finally:
        closeness._full_evidence = evidence
        for name, original in restore.items():
            setattr(ExposureIndex, name, original)
    runs = result.get('routes') or [result]
    return {'found': result['status'] == 'found', 'neighbor_checks': sum(r['neighbor_checks'] for r in runs),
            'pairs_scored': calls['pair'] - calls['evidence'] + calls['pair_row'],      # row building only, before and after the skip
            'overlap_tests': calls['overlaps'], 'evidence_pairs': calls['evidence'],
            'pairs_scored_reported': sum(r['pairs_scored'] for r in runs) if 'pairs_scored' in runs[0] else None}


def prewarm_stages(router):
    """The people index built lazily on this world, and the service's prewarm of both indexes when the service has one."""
    from types import SimpleNamespace
    from fec.surfaces.staffer_live_service import LiveService

    def service():
        built = LiveService('coldpath', SimpleNamespace(nodes=router.nodes))
        built.router, built.revision, built.ready = router, 'coldpath', True
        return built
    lazy = service()
    began = time.perf_counter()
    lazy.network_index(compressed=False)
    result = {'people_index_lazy_ms': round((time.perf_counter() - began) * 1000, 1)}
    if hasattr(LiveService, '_start_prewarm'):
        forget_routes(router)
        warm = service()
        warm.prewarm_enabled = True
        warm._start_prewarm()
        for thread in [t for t in threading.enumerate() if t.name == 'staffer-prewarm']:
            thread.join()
        state = warm.metrics['prewarm']
        result.update(prewarm=state['status'], work_index_ms=state['stages']['work_index']['ms'],
                      people_index_ms=state['stages']['network_index']['ms'], prewarm_total_ms=state['total_ms'])
    return result


def coldpath(worlds=2, people=20000, offices=1000, seed0=11, repeats=3, out=print):
    """What a person waits for on a router nobody has asked yet, and the work behind it.

    Each world is built once from its seed. Each repeat forgets what the router built for routes and times, in this order:
    the first route (route_count 1) and the page's default request (route_count 3, distinct alternatives), each from
    nothing, so the work index build is inside them; the work index build alone (the prewarm's first stage); then both
    requests again on the built index from empty row caches. A counting pass on a fresh router adds the work done, and
    the prewarm (when the service has one) runs on the same world. Returns one dict per world.
    """
    try:
        import psutil
        process = psutil.Process()
    except ImportError:
        process = None
    peak, stop = [0.], threading.Event()

    def watch():
        while not stop.wait(.05):
            peak[0] = max(peak[0], process.memory_info().rss / 2 ** 20)
    if process is not None:
        threading.Thread(target=watch, daemon=True, name='rss-watch').start()

    def timed(fn):
        """(wall seconds, this thread's CPU seconds): a busy host stretches the first, hardly the second."""
        gc.collect()
        began, cpu = time.perf_counter(), time.thread_time()
        fn()
        return time.perf_counter() - began, time.thread_time() - cpu
    table = []
    for g in range(worlds):
        seed, started = seed0 + g, time.perf_counter()
        router, ids, _ = synthetic_router(seed, people=people, offices=offices, tenures=(2, 4), span=(365, 1800), reach=4, jump=.1,
                                          person_format='person:hill:%06d')
        built = time.perf_counter() - started
        rng, queries = random.Random(100000 + seed), []
        while len(queries) < 3:
            origin, target = rng.sample(ids, 2)
            if router.route(origin, target, {'objective': 'work_time'})['status'] == 'found':
                queries.append((origin, target))
        origin, target = queries[0]
        options = router._options({'objective': 'work_time'})
        request = lambda count: {'objective': 'work_time', 'route_count': count}
        runs = {name: [] for name in ('first_route', 'route_3', 'index_alone', 'first_route_after_index', 'route_3_after_index')}
        for _ in range(repeats):
            forget_routes(router)
            runs['first_route'].append(timed(lambda: router.route(origin, target, request(1))))
            forget_routes(router)
            runs['route_3'].append(timed(lambda: router.route(origin, target, request(3))))
            routes = forget_routes(router)
            runs['index_alone'].append(timed(lambda: routes.index(options)))
            for name, count in (('first_route_after_index', 1), ('route_3_after_index', 3)):
                routes.row_caches.clear()
                routes.cache.clear()
                router.__dict__.pop('_contact_groups', None)
                runs[name].append(timed(lambda: router.route(origin, target, request(count))))
        peak_requests = round(peak[0])
        row = {'seed': seed, 'people': people, 'offices': offices, 'world_build_s': round(built, 2), 'repeats': repeats,
               'seconds': {name: {'best': round(min(w for w, _ in v), 3), 'median': round(statistics.median(w for w, _ in v), 3),
                                  'cpu_best': round(min(c for _, c in v), 3), 'all': [round(w, 3) for w, _ in v]}
                           for name, v in runs.items()},
               'work': {count: work_done(router, origin, target, count) for count in (1, 3)}, 'prewarm': prewarm_stages(router),
               'peak_rss_requests_mb': peak_requests, 'peak_rss_mb': round(peak[0]),
               'rss_mb_now': round(process.memory_info().rss / 2 ** 20) if process else None}
        table.append(row)
        out('world %d: %d people, %d offices, built in %.1fs' % (seed, people, offices, built))
        for name, value in row['seconds'].items():
            out('  %-24s best %8.3fs   median %8.3fs   best cpu %8.3fs' % (name, value['best'], value['median'], value['cpu_best']))
        for count, work in row['work'].items():
            out('  route_count %d work: %s' % (count, ', '.join('%s %s' % item for item in work.items())))
        out('  prewarm: %s' % ', '.join('%s %s' % item for item in row['prewarm'].items()))
        out('  peak RSS %s MB after the timed requests, %s MB after the prewarm step (which builds a second copy of both indexes)'
            % (row['peak_rss_requests_mb'], row['peak_rss_mb']))
    stop.set()
    return table


def test_coldpath_harness_runs_end_to_end_on_a_tiny_world():
    lines = []
    row, = coldpath(worlds=1, people=300, offices=15, repeats=1, out=lines.append)
    assert set(row['seconds']) == {'first_route', 'route_3', 'index_alone', 'first_route_after_index', 'route_3_after_index'}
    assert all(v['best'] > 0 for v in row['seconds'].values())
    one, three = row['work'][1], row['work'][3]
    assert one['found'] and 0 < one['neighbor_checks'] <= three['neighbor_checks']
    assert 0 < one['pairs_scored'] <= one['neighbor_checks'] and one['overlap_tests'] >= one['pairs_scored']
    assert one['pairs_scored_reported'] == one['pairs_scored']
    assert row['prewarm']['people_index_lazy_ms'] > 0 and lines


def test_benchmark_harness_runs_end_to_end_on_a_tiny_world():
    lines = []
    table = benchmark(graphs=1, people=300, offices=15, queries=4, out=lines.append)
    summary, counts = summarize(table, out=lines.append)
    assert counts['MISMATCH'] == 0 and len(table['person']) == len(table['office']) == 4
    assert summary['person']['expanded'][0] > 0 and lines


if __name__ == '__main__':
    # --benchmark [graphs people offices queries seed0] [--save file.json]; --summarize file.json...
    arguments = [a for a in sys.argv[1:] if not a.startswith('--')]
    if '--ties' in sys.argv:
        for mode in ('decimal', 'log'):
            print(format_table(mode, oracle_table(mode, int(arguments[0]) if arguments else 20000)), flush=True)
    elif '--summarize' in sys.argv:
        merged = {'person': [], 'office': []}
        for name in arguments:
            for kind, rows in json.load(open(name)).items():
                merged[kind].extend(rows)
        summarize(merged)
    elif '--coldpath' in sys.argv:
        saved = sys.argv[sys.argv.index('--save') + 1] if '--save' in sys.argv else None
        arguments = [a for a in arguments if a != saved]
        outcome = coldpath(**dict(zip(('worlds', 'people', 'offices', 'seed0', 'repeats'), map(int, arguments))))
        if saved:
            json.dump(outcome, open(saved, 'w'), indent=1)
    elif '--benchmark' in sys.argv:
        saved = sys.argv[sys.argv.index('--save') + 1] if '--save' in sys.argv else None
        arguments = [a for a in arguments if a != saved]
        outcome = benchmark(**dict(zip(('graphs', 'people', 'offices', 'queries', 'seed0'), map(int, arguments))))
        summarize(outcome)
        if saved:
            json.dump(outcome, open(saved, 'w'))
    else:
        raise SystemExit(pytest.main([__file__, '-q', '-s']))
