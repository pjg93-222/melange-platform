"""Complete loopless work routes using absolute size-adjusted exposure.

Search. A hop between two people is an undirected edge with a positive cost and
one step. An office route ends at a virtual sink: every anchored staff member
has a terminal arc to it with a nonnegative cost and no step. The best route is
the cheapest, and costs equal to 12 significant digits are a tie: among the
routes whose exact costs lie within one part in 10**12 of the cheapest, the one
with the fewest steps wins, then the one of least exact cost. Exact means the
real sum of the float costs, without rounding. The reported cost is still the
float sum of the route's costs added in order from the origin.

The search is bidirectional Dijkstra with heuristic 0: one frontier grows from
the origin, the other from the sink (its first expansion is every anchored staff
member at the terminal cost). Every relaxation that reaches a node the other
side has labelled records a candidate meeting. The frontiers keep growing until
the smallest open forward cost plus the smallest open backward cost exceeds the
best meeting by a margin that covers float rounding and the tie width. Every
route that can tie for the cheapest then runs through nodes the frontiers have
settled, and a second pass solves those nodes in exact arithmetic. Which side
advances never affects the answer, only how much work is spent. Nothing here
caps time or work; a route is proven, or shown absent.

Alternatives. Yen's loopless routes (alternative_strategy=ranked) follow the same
rule: the next route accepted is, among the pending candidates whose exact cost is
within one part in 10**12 of the cheapest pending one, the one with the fewest
steps, then the one of least exact cost, then the one whose node ids read from the
sink end are smallest (_next_alternative).

No route. A search that finds none is unreachable and says why (unreachable_reason, message). That result and
insufficient_work_coverage both name the Ecosystem objective (suggested_objective), the network the page offers instead.
The reason is read from what the search used; nothing is searched again (_explain_unreachable).

Coverage. A person is covered when the model's authenticated staffer coverage holds for them: an operational staffer, or someone
with a payroll record (everyone, when the router names no operational staffers). A person route needs both ends covered, or it is
insufficient_work_coverage. An office route (options['_target_office']) drops the current staff who are not covered and searches
to the rest, in every search it runs: the spur searches of the ranked alternatives, and each pass of distinct and independent
routes (staffer_distinct_routes keeps the dropped staff out of the later passes). Every result of an office route, found,
unreachable or coverage, says who was left out (uncovered_staff_count, uncovered_staff_ids, sorted; target_count still counts the
staff the request named) and which coverage they lack (uncovered_basis: payroll_or_roster, or roster alone when work_sources is
directory, which reads no payroll evidence, so none can cover anyone), and an unreachable reason is judged on the covered staff. It is
insufficient_work_coverage only when its origin is uncovered or none of the office's staff is covered. An office none of whose usable
staff has dated work at it says so of "the staff this search could use" when some staff were left out (not covered) or kept out
(avoided), and of "this office's current staff" only when nobody was (SEARCHED_STAFF_MESSAGES).

One index per request. A request that runs several passes (distinct and independent alternatives call ClosenessRoutes.route once per
pass) takes its work index at its first pass and keeps it to its last (request_work, opened by AdvocacyRouter.route_many), so a router
the service releases between two passes does not build the whole index again for the next. A released router stores no index and no
hop-row cache it builds, and still answers every request that reaches it.
"""
from array import array
from bisect import bisect_left
from collections import OrderedDict, namedtuple
import contextlib
import contextvars
from copy import deepcopy
from fractions import Fraction
import heapq
import itertools
import math
import threading
import time

try:
    from .staffer_route_cancel import check_cancelled
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_route_cancel import check_cancelled

from fec.staffer_network.closeness_math import ordinal, VERSION, HOP_FACTOR, MEMBER_BETA, HEADCOUNT_SEMANTICS
from fec.staffer_network.member_context import MemberContext
from fec.staffer_network.relationship_uncertainty import uncertainty, specification
from fec.surfaces.staffer_distinct_routes import evidence_contexts

SINK = '\0closeness-destination'
ALGORITHM = 'Bidirectional Dijkstra with terminal costs (heuristic 0); Yen loopless alternatives'
CANCEL_INTERVAL = 64
# Routes whose exact costs are within one part in TIE_PARTS of the cheapest tie. FLOAT_SLACK is a relative
# allowance for the rounding in the float sums the search compares (see _search_bidirectional).
TIE_PARTS = 10 ** 12
FLOAT_SLACK = 1e-10
STOP_SLACK = (1 + 1 / TIE_PARTS) * (1 + FLOAT_SLACK)
TIGHT_SLACK = 2 * (1 / TIE_PARTS + FLOAT_SLACK)


class _Meter:
    """Work counters, throttled progress and cancellation checks for one route request.

    checks counts the candidate neighbours considered (reported as neighbor_checks), pairs those of them this request
    priced itself, and pairs_scored the ones whose pair arithmetic ran: the others had no day in common.
    """
    def __init__(self, progress=None, clock=time.monotonic, started=None):
        self.progress, self.clock = progress, clock
        self.expanded = self.checks = self.pairs = self.pairs_scored = 0
        self.started = clock() if started is None else started
        self.last_progress = self.started

    def expansion(self):
        self.expanded += 1
        if not self.expanded % CANCEL_INTERVAL:
            check_cancelled()

    def report(self):
        if self.progress:
            now = self.clock()
            if now - self.last_progress > .2:
                self.progress({'expanded': self.expanded, 'neighbor_checks': self.checks,
                               'elapsed_ms': round((now - self.started) * 1000)})
                self.last_progress = now


def _hop_cost(strength, uncertainty_cost, weight):
    return -math.log(HOP_FACTOR * strength) + weight * uncertainty_cost


def _full_evidence(index, pair, as_of, weight):
    """Complete evidence for one pair (sorted ids); built only for pairs on candidate or returned routes."""
    raw = index.pair(*pair)
    risk = uncertainty(raw['workplaces'], as_of)
    return {**raw, 'uncertainty': risk, 'adjusted_strength': raw['strength'] * math.exp(-weight * risk['cost'])}


def _frozen(items):
    """The set itself, frozen, so two different sets can never share a cache key (a (len, hash) pair could collide)."""
    if items is None:
        return None
    return items if isinstance(items, frozenset) else frozenset(items)


INDEX_WAIT_SECONDS = .25


class _Flight:
    """One work-index build in progress. The caller that started it builds; every other caller of the same key waits
    here for that build, so the index is built once however many requests arrive while it is being built."""
    __slots__ = ('done', 'value', 'error')

    def __init__(self):
        self.done = threading.Event()
        self.value = self.error = None

    def finish(self, value, error):
        self.value, self.error = value, error
        self.done.set()

    def wait(self):
        # A waiting search stays cancellable: its job's cancel event is checked between waits.
        while not self.done.wait(INDEX_WAIT_SECONDS):
            check_cancelled()
        if self.error is not None:
            raise self.error
        return self.value


def _payroll_people(index):
    """The people with payroll evidence, kept on the work index: it only changes when the index is rebuilt.

    build_index sets it; an index made some other way (a test double) gets it computed once, here.
    """
    people = getattr(index, 'payroll_people', None)
    if people is None:
        from fec.surfaces.staffer_work_sources import payroll_people
        people = index.payroll_people = payroll_people(index)
    return people


class _RowCache:
    """Compact hop rows for one cost configuration, shared by every request that uses it.

    A row holds one person's usable hops as parallel sequences: neighbour ids in
    sorted order, hop costs, and the context groups of the dated evidence. Full
    evidence is never kept here. Exclusions are applied when the search relaxes
    an edge, so they never fragment the cache. Bounded LRU: eviction only costs
    a recomputation, never a route.
    """
    MAX_ROWS = 200000
    MAX_EDGES = 2000000
    MAX_CONTEXTS = 50000

    def __init__(self, index):
        self.index = index
        self.rows = OrderedDict()
        self.edges = 0
        self.contexts = {}
        self.lock = threading.Lock()

    def get(self, person):
        with self.lock:
            row = self.rows.get(person)
            if row is not None:
                self.rows.move_to_end(person)
            return row

    def put(self, person, row):
        with self.lock:
            old = self.rows.pop(person, None)
            if old is not None:
                self.edges -= len(old[0])
            self.rows[person] = row
            self.edges += len(row[0])
            while len(self.rows) > 1 and (len(self.rows) > self.MAX_ROWS or self.edges > self.MAX_EDGES):
                self.edges -= len(self.rows.popitem(last=False)[1][0])

    def intern(self, contexts):
        known = self.contexts.get(contexts)
        if known is not None:
            return known
        if len(self.contexts) < self.MAX_CONTEXTS:
            self.contexts[contexts] = contexts
        return contexts


class _Neighbors:
    """Hop rows for one request: from the shared cache, or computed once and stored there."""
    def __init__(self, cache, allowed, as_of, weight, meter):
        self.cache, self.allowed, self.as_of, self.weight, self.meter = cache, allowed, as_of, weight, meter

    def __call__(self, person):
        row = self.cache.get(person)
        if row is None:
            row = self.build(person)
            self.cache.put(person, row)
        return zip(*row)

    def build(self, person):
        cache, allowed, meter = self.cache, self.allowed, self.meter
        index, rows = cache.index, cache.rows
        ids, costs, contexts = [], [], []
        # A neighbour whose row exists has already priced this pair; its row is
        # complete for every allowed neighbour, so absence means no usable hop.
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
            # Strength is positive only where the two stood in one workplace together for a day or more, so a pair
            # with no such overlap has no hop and never reaches pair arithmetic (see ExposureIndex.overlaps).
            if not index.overlaps(person, other):
                continue
            meter.pairs_scored += 1
            raw = index.pair_row(*((person, other) if person < other else (other, person)))
            if not raw['strength'] > 0:
                continue
            risk = uncertainty(raw['workplaces'], self.as_of)
            ids.append(other)
            costs.append(_hop_cost(raw['strength'], risk['cost'], self.weight))
            contexts.append(cache.intern(frozenset(evidence_contexts(raw))))
        return tuple(ids), array('d', costs), tuple(contexts)


def _search_reference(start, neighbors, terminal, blocked_nodes=frozenset(), blocked_edges=frozenset(),
                      progress=None, excluded_contexts=frozenset(), meter=None):
    """One-directional Dijkstra from `start` to the virtual sink (the former search).

    Kept only as a test oracle for _search_bidirectional: nothing in the server,
    and no request parameter, can select it. `neighbors(p)` yields
    (other, cost, contexts) in sorted-id order, `terminal` maps anchored staff to
    their terminal cost. Returns ([start, ..., staff, SINK], cost) or None.
    """
    meter = meter or _Meter(progress)
    check_cancelled()
    serial = itertools.count()
    queue = [(0., 0, next(serial), start)]
    distances, previous = {start: (0., 0)}, {}
    while queue:
        cost, steps, _, p = heapq.heappop(queue)
        if (cost, steps) != distances.get(p):
            continue
        if p == SINK:
            path = [p]
            while path[-1] != start:
                path.append(previous[path[-1]])
            return list(reversed(path)), cost
        meter.expansion()
        arcs = [(other, edge_cost) for other, edge_cost, contexts in neighbors(p)
                if not excluded_contexts or contexts.isdisjoint(excluded_contexts)]
        if p in terminal:
            arcs = [*arcs, (SINK, terminal[p])]
        for other, edge_cost in arcs:
            if other in blocked_nodes or tuple(sorted((p, other))) in blocked_edges:
                continue
            candidate = (cost + edge_cost, steps + (other != SINK))
            if candidate < distances.get(other, (math.inf, math.inf)):
                distances[other], previous[other] = candidate, p
                heapq.heappush(queue, (*candidate, next(serial), other))
        meter.report()
    return None


_Frontiers = namedtuple('_Frontiers', 'labels settled near theta best')
_Tube = namedtuple('_Tube', 'heads nodes arcs')


def _filters(start, terminal, blocked_nodes, blocked_edges):
    """One request's filters: (nodes never entered, blocked edges by endpoint, anchored staff the sink can still be reached from)."""
    sink_blocked, blocked_at = set(), {}
    for a, b in blocked_edges:
        if SINK in (a, b):
            sink_blocked.add(b if a == SINK else a)
        else:
            blocked_at.setdefault(a, set()).add(b)
            blocked_at.setdefault(b, set()).add(a)
    entry_blocked = frozenset(blocked_nodes) - {start}
    seeds = {p: cost for p, cost in terminal.items() if p not in sink_blocked and p not in entry_blocked}
    return entry_blocked, blocked_at, seeds


def _balanced(work, forward, backward):
    """Advance the side that has done less work; ties go to the smaller open key, then to the origin side."""
    return 0 if (work[0], forward[0], forward[1]) <= (work[1], backward[0], backward[1]) else 1


def _grow_frontiers(start, neighbors, seeds, entry_blocked, blocked_at, excluded_contexts, meter, advance=None):
    """Float bidirectional Dijkstra: the first pass of _search_bidirectional (see there).

    Returns _Frontiers(labels, settled, near, theta, best), or None when the two
    frontiers never meet (no usable route). `near` holds every node whose two
    labels summed to at most the then-current theta when they were last compared,
    so every node whose final labels sum to at most the final theta; `theta` is
    the final stopping threshold and `best` the cost of the cheapest meeting.
    `advance` only exists for tests: it picks the side to grow and must not
    change the answer.
    """
    serial = itertools.count()
    labels = ({start: (0., 0)}, {p: (cost, 0) for p, cost in seeds.items()})
    queues = ([(0., 0, next(serial), start)], [(cost, 0, next(serial), p) for p, cost in seeds.items()])
    heapq.heapify(queues[1])
    settled = (set(), set())
    work = [0, 0]
    best, theta, near = math.inf, math.inf, set()
    if start in seeds:
        best = labels[1][start][0]
        theta = best * STOP_SLACK
        near.add(start)
    choose = advance or _balanced
    while True:
        for side in (0, 1):
            queue, label, done = queues[side], labels[side], settled[side]
            while queue:
                head = queue[0]
                known = label[head[3]]
                if head[0] == known[0] and head[1] == known[1] and head[3] not in done:
                    break
                heapq.heappop(queue)
        if not queues[0] or not queues[1]:
            break
        forward, backward = queues[0][0], queues[1][0]
        if forward[0] + backward[0] > theta:
            break
        side = choose(work, forward, backward)
        cost, steps, _, person = heapq.heappop(queues[side])
        settled[side].add(person)
        meter.expansion()
        arcs = tuple(neighbors(person))
        work[side] += 1 + len(arcs)
        blocked_here = blocked_at.get(person, ())
        label, opposite, queue = labels[side], labels[1 - side], queues[side]
        for other, edge_cost, contexts in arcs:
            if other in entry_blocked or other in blocked_here:
                continue
            if excluded_contexts and not contexts.isdisjoint(excluded_contexts):
                continue
            key = (cost + edge_cost, steps + 1)
            known = label.get(other)
            if known is None or key < known:
                label[other] = key
                heapq.heappush(queue, (key[0], key[1], next(serial), other))
                joined = opposite.get(other)
                if joined is not None:
                    total = key[0] + joined[0]
                    if total < best:
                        best, theta = total, total * STOP_SLACK
                    if total <= theta:
                        near.add(other)
        meter.report()
    return None if best == math.inf else _Frontiers(labels, settled, near, theta, best)


def _tube(neighbors, entry_blocked, blocked_at, excluded_contexts, frontiers):
    """The nodes every route that ties for the cheapest must stay inside, with their usable arcs.

    heads: nodes settled by at least one side whose two final labels sum to at
    most theta (a route's first node that the forward side did not settle, or its
    last node when it did settle them all). From the heads the tube follows, on
    each side, only edges that are tight on that side's labels within
    TIGHT_SLACK of theta: toward the origin through nodes settled forward, toward
    the sink through nodes settled backward. In ordinary data that is the nodes
    of one route; exact ties widen it, and only by the tied routes' nodes.
    """
    (labels_f, labels_b), (settled_f, settled_b) = frontiers.labels, frontiers.settled
    slack, rows = frontiers.theta * TIGHT_SLACK, {}

    def usable(person):
        row = rows.get(person)
        if row is None:
            check_cancelled()
            blocked_here = blocked_at.get(person, ())
            row = rows[person] = sorted(
                (other, cost) for other, cost, contexts in neighbors(person)
                if other not in entry_blocked and other not in blocked_here
                and not (excluded_contexts and not contexts.isdisjoint(excluded_contexts)))
        return row

    def follow(heads, label, settled):
        reached, stack = set(heads), list(heads)
        while stack:
            person = stack.pop()
            limit = label[person][0] + slack
            for other, cost in usable(person):
                if other not in reached and other in settled and label[other][0] + cost <= limit:
                    reached.add(other)
                    stack.append(other)
        return reached

    heads = {p for p in frontiers.near if (p in settled_f or p in settled_b) and labels_f[p][0] + labels_b[p][0] <= frontiers.theta}
    nodes = follow(heads, labels_f, settled_f) | follow(heads, labels_b, settled_b)
    return _Tube(heads, nodes, {p: [(q, cost) for q, cost in usable(p) if q in nodes] for p in nodes})


def _fewest_steps_exact(start, arcs, terminal):
    """The result of _search_bidirectional, computed in exact arithmetic over `arcs` only.

    arcs maps each node to its usable (neighbour, float cost) pairs, neighbours
    inside the mapping; terminal maps anchored staff to float terminal costs. Float
    costs are dyadic rationals, so scaling them all by one power of two turns every
    route cost into a Python int: sums and comparisons are then exact. Dijkstra from
    the sink gives, for every node, the least exact cost of finishing from it, and so
    the least exact cost C of a route. Then a layered search over step counts, keeping
    only walks that can still finish within C + C // 10**12 (so tied means within one
    part in 10**12), finds the least k for which a walk of k steps ends at a staff
    member inside that bound, and the cheapest such walk at that k. A walk with a
    repeated node could be shortened to fewer steps at lower cost, so the walk at the
    least k is a route. Ties in exact cost go to the smallest node ids, read from the
    sink end, so the answer does not depend on how the arcs were found. Returns
    (nodes, float cost, C as a float) or None.
    """
    ends = sorted(p for p in terminal if p in arcs)
    values = [float(cost) for row in arcs.values() for _, cost in row] + [float(terminal[p]) for p in ends]
    shift = max((denominator.bit_length() - 1 for _, denominator in map(float.as_integer_ratio, values)), default=0)

    def units(cost):
        numerator, denominator = float(cost).as_integer_ratio()
        return numerator << (shift - denominator.bit_length() + 1)
    hops = {p: sorted((q, units(cost)) for q, cost in row) for p, row in arcs.items()}
    close = {p: units(terminal[p]) for p in ends}
    before = {}
    for person, row in hops.items():
        for other, cost in row:
            before.setdefault(other, []).append((person, cost))
    to_sink = dict(close)
    heap = [(cost, number, p) for number, (p, cost) in enumerate(close.items())]
    heapq.heapify(heap)
    serial = itertools.count(len(heap))
    while heap:
        spent, _, person = heapq.heappop(heap)
        if spent != to_sink[person]:
            continue
        check_cancelled()
        for earlier, cost in before.get(person, ()):
            known = to_sink.get(earlier)
            if known is None or spent + cost < known:
                to_sink[earlier] = spent + cost
                heapq.heappush(heap, (spent + cost, next(serial), earlier))
    if start not in to_sink:
        return None
    optimum = to_sink[start]
    bound = optimum + optimum // TIE_PARTS
    layer, parents = {start: 0}, []
    while True:
        check_cancelled()
        pick = None
        for p in ends:
            if p in layer and layer[p] + close[p] <= bound and (pick is None or layer[p] + close[p] < pick[0]):
                pick = (layer[p] + close[p], p)
        if pick:
            break
        following, parent = {}, {}
        for person in sorted(layer):
            for other, cost in hops.get(person, ()):
                spent = layer[person] + cost
                if other in to_sink and spent + to_sink[other] <= bound and (other not in following or spent < following[other]):
                    following[other], parent[other] = spent, person
        if not following or len(parents) > len(arcs):
            return None
        layer = following
        parents.append(parent)
    route = [pick[1]]
    for parent in reversed(parents):
        route.append(parent[route[-1]])
    route.reverse()
    price = {(p, q): cost for p, row in arcs.items() for q, cost in row}
    total = 0.
    for p, q in zip(route, route[1:]):
        total += price[p, q]
    return route, total + terminal[route[-1]], optimum / 2 ** shift


def _search_bidirectional(start, neighbors, terminal, blocked_nodes=frozenset(), blocked_edges=frozenset(),
                          progress=None, excluded_contexts=frozenset(), meter=None, advance=None):
    """Bidirectional Dijkstra for the cheapest routes, then an exact choice among those that tie.

    Same arguments and return value as _search_reference. Let the exact cost of a
    route be the exact real sum of its float hop costs and its terminal cost, and
    C the least exact cost over all usable routes. Routes costing at most
    C + C / 10**12 tie: costs equal to 12 significant digits are one cost. The
    result is the tied route with the fewest steps; among those, the one of least
    exact cost; among those, a fixed one (the smallest node ids read from the sink
    end). The reported cost is the float sum of the route's costs added in order
    from the origin, exactly as the one-directional search accumulated it. Where
    two routes tie in real arithmetic, rounding therefore never picks the winner.

    Weights are (cost, steps) pairs added componentwise and ordered
    lexicographically; every person-to-person hop has positive cost and one step, a
    terminal arc has a nonnegative cost and no step.

    First pass (_grow_frontiers). Side 0 grows from the origin and side 1 from the
    sink (its first expansion is every anchored staff member at its terminal cost),
    each by Dijkstra on float weights. Every label change is compared with the other
    side's current label for that node, so each meeting is priced, and `best` is the
    cheapest meeting seen. The loop ends when a frontier is empty, or when the
    smallest open forward cost plus the smallest open backward cost is greater than
    theta = best * (1 + 10**-12) * (1 + 10**-10). That is later than the classic stop
    (the two open costs reach best) only when some sum of open costs lies within about
    10**-10 of best, which with costs that are not equal does not happen.

    Lemma. Let P = x0..xm be a usable route (xm anchored) whose exact cost is at
    most (1 + 10**-12) C. When the loop ends, for every i <= j, xi is settled forward
    or xj is settled backward. Hence some index a has x0..x(a-1) settled forward and
    xa..xm settled backward, and every node of P is settled by at least one side.
    Proof. Take a side and a usable path from its origin with float prefix sums
    f0 <= f1 <= ... (each the previous one plus an edge, rounded). If node yi of the
    path is not settled, the smallest open cost of that side is at most fi: let yk be
    the first unsettled node on the path; its predecessor is settled with a label at
    most f(k-1) (by induction, rounding being monotone), so yk holds an open label at
    most fk <= fi, and the smallest open key is at most that label. On the backward
    side the path starts at the sink, so the label of xm is at most its terminal cost.
    Now suppose xi is not settled forward and xj is not settled backward, with i <= j.
    The two smallest open costs add to at most F + B, where F is the float sum of P's
    first i edges and B the float sum of its terminal cost and edges j to m-1. These
    parts share no edge, so their exact costs add to at most the exact cost of P, and
    a float sum of n positive terms is within a relative n * 2**-53 of the exact sum.
    So the open costs add to at most (1 + g) (1 + 10**-12) C, while the best meeting
    costs at least (1 - g') C in float, where g and g' are about (steps + 3) * 2**-53
    for P and for the best meeting. theta exceeds the former whenever g + g' <
    10**-10, which holds while the two routes have under 9 * 10**5 steps in all; a
    route has fewer steps than there are people. That contradicts the stop rule. An
    empty frontier has settled every node reachable from its origin, all of P. QED

    Second pass (_tube, _fewest_steps_exact). The heads are the nodes settled by at
    least one side whose two labels sum to at most theta; by the lemma xa (or xm, when
    every node of P is settled forward) is one. From the heads, follow edges that are
    tight on a side's labels, label(next) + cost <= label(here) + slack with slack =
    2 * (10**-12 + 10**-10) * theta: toward the origin through nodes settled forward,
    toward the sink through nodes settled backward. P's prefix to xa and its suffix
    from xa are such chains: a prefix of a route within 10**-12 C of cheapest is
    within 10**-12 C of the cheapest way to reach each of its nodes, and float labels
    are within the allowance above of their exact values. Every node of every tied
    route is therefore in the tube, so the exact solver, which sees the tube alone,
    finds the same least cost and the same tied routes as it would on the whole graph.
    With costs that are not equal the tube is the nodes of one route: the pass reads
    a few rows, all of rows the first pass read, and counts no expansion.

    Filters match _search_reference exactly: a node other than `start` that is in
    blocked_nodes is never entered from either side (the origin is never entered,
    so it may itself be blocked), an edge in blocked_edges is never used, a
    terminal arc blocked as the pair (SINK, staff) is never used, and an edge
    whose contexts meet excluded_contexts is never used.

    Side choice. The side that has done less work advances, where expanding a
    node costs one plus its usable edges: a stand-in for its neighbour checks
    that does not depend on what the cache already holds, so the same request
    expands the same nodes in the same order, cold or warm. This only balances the
    two frontiers; the answer does not depend on it.
    """
    meter = meter or _Meter(progress)
    check_cancelled()
    entry_blocked, blocked_at, seeds = _filters(start, terminal, blocked_nodes, blocked_edges)
    if not seeds:
        return None  # No staff can reach the sink, so the origin is never expanded.
    frontiers = _grow_frontiers(start, neighbors, seeds, entry_blocked, blocked_at, excluded_contexts, meter, advance)
    if frontiers is None:
        return None
    tube = _tube(neighbors, entry_blocked, blocked_at, excluded_contexts, frontiers)
    found = _fewest_steps_exact(start, tube.arcs, seeds)
    if found is None or abs(found[2] - frontiers.best) > frontiers.best * FLOAT_SLACK:
        raise RuntimeError('the near-tie pass lost the cheapest route: exact %r, search %r' % (found and found[2], frontiers.best))
    return [*found[0], SINK], found[1]


def _next_alternative(pending):
    """Remove and return (path, reported cost) of the next alternative to accept from `pending`.

    `pending` is a list of (exact cost, reported cost, path) for Yen's candidates. The tie rule of the search, applied to
    whole routes: among the candidates whose exact cost is at most the least pending exact cost times (1 + 10**-12), the
    one with the fewest steps; then the one of least exact cost; then the one whose node ids, read from the sink end, are
    smallest (the fixed choice _fewest_steps_exact makes). Candidates further apart than the tie width are ranked by cost
    alone, so ordinary data ranks as it always did; inside the width, rounding in the reported costs never picks the
    winner, and alternatives can come in an order that differs from their reported costs by rounding.
    """
    least = min(item[0] for item in pending)
    bound = least + least / TIE_PARTS
    index = min((i for i, item in enumerate(pending) if item[0] <= bound),
                key=lambda i: (len(pending[i][2]), pending[i][0], pending[i][2][::-1]))
    _, cost, path = pending.pop(index)
    return path, cost


# What the page says when a Staffer search finds no route, and what it offers instead. The Ecosystem network's objective is
# the value of the page's route-model selector (staffer_live/index.html). A connection is usable when the search itself
# could take it: the model's own record filters and the request's exclusions have both left it standing. Each sentence is
# true of the search it explains, so those that speak of what the search "can use" stay true when the people a reader
# excluded are what left no connection.
SUGGESTED_OBJECTIVE = 'evidence_cost'
UNREACHABLE_MESSAGES = {
    'office_without_work_records':
        "No Staffer route: none of this office's current staff have dated employment at this office in the records this model uses.",
    'origin_without_work_connections':
        'No Staffer route: the person you start with has no workplace connections this search can use.',
    'target_without_work_connections':
        'No Staffer route: the person you want to reach has no workplace connections this search can use.',
    'no_path_in_work_records':
        'No Staffer route between these people in the work records this model uses.',
}
# The two reasons that name the destination read differently when it is an office, which is a set of staff.
OFFICE_UNREACHABLE_MESSAGES = {
    'target_without_work_connections':
        "No Staffer route: none of this office's current staff with dated employment there has a workplace connection this search can use.",
    'no_path_in_work_records':
        "No Staffer route between this person and this office's current staff in the work records this model uses.",
}
# A search that left people out cannot tell the reader whether a route exists with them, so the last reason says so.
EXCLUSION_CLAUSE = ', leaving out the people you excluded'
# An office is judged on the staff the search could use: the covered staff the request did not exclude. When some of its current
# staff were left out (no coverage) or excluded (avoided), "none of this office's current staff" would say more than the search
# saw, because those staff may have dated employment at it, so the sentence speaks of the staff the search could use.
SEARCHED_STAFF_MESSAGES = {
    'office_without_work_records':
        'No Staffer route: none of the staff this search could use have dated employment at this office in the records this model uses.',
}


def _has_usable_hop(neighbors, person, entry_blocked, blocked_at, excluded_contexts):
    """Whether `person` has a hop the search could take: their hop row, filtered as _grow_frontiers filters it."""
    blocked_here = blocked_at.get(person, ())
    return any(other not in entry_blocked and other not in blocked_here
               and not (excluded_contexts and not contexts.isdisjoint(excluded_contexts))
               for other, _, contexts in neighbors(person))


def _explain_unreachable(neighbors, start, terminal, office, blocked_nodes, blocked_edges, excluded_contexts, staff_left_out=False):
    """{unreachable_reason, message, suggested_objective} for a search that found no route.

    The reason is the first of these that holds, read from what the search used:
    office_without_work_records, the destination is an office and none of the staff the search could use has a work record at
    it, so nothing can end a route (the bidirectional search ends before it expands anyone); origin_without_work_connections,
    the origin has no usable hop; target_without_work_connections, no person a route could end at has one; otherwise
    no_path_in_work_records. `staff_left_out` says that some of the office's current staff were left out of the search (not
    covered) or kept out of it (avoided): the first reason then says "the staff this search could use". `neighbors` reads the
    shared row cache, so the rows the search built cost nothing again and a row it never reached is built once; no search runs
    here, and the caller's meter does not count this work.
    """
    if office and not terminal:
        reason, excluded = 'office_without_work_records', False
    else:
        entry_blocked, blocked_at, seeds = _filters(start, terminal, blocked_nodes, blocked_edges)
        excluded = bool(entry_blocked)

        def usable(person):
            return _has_usable_hop(neighbors, person, entry_blocked, blocked_at, excluded_contexts)
        reason = ('origin_without_work_connections' if not usable(start) else
                  'target_without_work_connections' if not any(usable(person) for person in seeds) else
                  'no_path_in_work_records')
    message = (OFFICE_UNREACHABLE_MESSAGES if office else {}).get(reason) or UNREACHABLE_MESSAGES[reason]
    if staff_left_out:
        message = SEARCHED_STAFF_MESSAGES.get(reason, message)
    if reason == 'no_path_in_work_records' and excluded:
        message = message[:-1] + EXCLUSION_CLAUSE + '.'
    return {'unreachable_reason': reason, 'message': message, 'suggested_objective': SUGGESTED_OBJECTIVE}


# One search request, one work index. A request with several passes (distinct and independent alternatives call ClosenessRoutes.route once
# per pass) takes the index at its first pass and keeps it to its last, whatever the router's table holds in between: a publish that
# releases the router between two passes must not make the next pass build the whole index again (tens of seconds on the live records).
# A contextvar, so a request on another thread has its own and nothing has to be passed through route_many's signature.
_REQUEST_WORK = contextvars.ContextVar('staffer_request_work', default=None)


class _RequestWork:
    """What one request has taken from the ClosenessRoutes it searches: its work indexes by key and, for a released router, the hop-row
    caches that router no longer keeps."""
    __slots__ = ('indexes', 'row_caches')

    def __init__(self):
        self.indexes, self.row_caches = {}, {}


@contextlib.contextmanager
def request_work():
    """Keep one work index (and, on a released router, one hop-row cache) for every pass of the request that runs inside.

    AdvocacyRouter.route_many opens it around distinct_routes and the Yen loop. A scope that is already open is the request's own, so a
    nested one changes nothing. It closes, and what it held is let go, when the request ends, however it ends.
    """
    if _REQUEST_WORK.get() is not None:
        yield
        return
    token = _REQUEST_WORK.set(_RequestWork())
    try:
        yield
    finally:
        _REQUEST_WORK.reset(token)


class ClosenessRoutes:
    """Work-time routes for one router: the closeness index, the shared hop-row caches and the search over them.

    Near ties are decided by the tie rule in the module docstring: routes whose exact costs lie within one part in
    10**12 of the cheapest tie, the tied route with the fewest steps wins, and the reported cost is the float sum of
    the returned route's costs from the origin. The ranked alternatives follow the same rule (_next_alternative), so
    two alternatives inside that width can be listed in an order that differs from their reported costs by rounding.
    Each spur search of the ranked loop judges that width against its own spur cost, not the whole route's, so two
    alternatives that tie within the width over the whole route can occasionally be listed out of step order.
    The rule is documented in staffer_live/README.md as well.
    """
    MAX_ROW_CACHES = 3

    def __init__(self, router, search=None):
        self.router = router
        self.indexes = OrderedDict()
        self.index_lock = threading.Lock()
        self.index_flights = {}
        self.cache = OrderedDict()
        self.row_caches = OrderedDict()
        self.row_lock = threading.Lock()
        # Set by release(), for good: the service has replaced this router. It stores no index and no hop-row cache from then on.
        self.released = False
        # Tests pass _search_reference here; the server never does.
        self.search = search or _search_bidirectional

    def index(self, options):
        """The work index for the options that shape it, built once: concurrent callers wait for the one build.

        A request that holds a scope (request_work) is given the index it took first for every later pass, whatever the table holds by then.
        """
        key = (options['include_review'], options['include_unresolved'], options['include_historical'], options['work_sources'])
        scope = _REQUEST_WORK.get()
        if scope is not None:
            held = scope.indexes.get((self, key))
            if held is not None:
                return held
        index = self._shared_index(key, options)
        if scope is not None:
            scope.indexes[(self, key)] = index
        return index

    def _shared_index(self, key, options):
        """The table's index for `key` (built with `options` if it holds none): the part of index() every request shares.

        The lock only guards the two small tables and is never held while building, which takes tens of seconds on
        the live records. A build that fails raises in every caller then waiting on it and is retried by the next. A released router
        keeps nothing it builds: the callers waiting on the build still get its index, and the table stays empty.
        """
        with self.index_lock:
            index = self.indexes.get(key)
            if index is not None:
                return index
            flight = self.index_flights.get(key)
            builder = flight is None
            if builder:
                flight = self.index_flights[key] = _Flight()
        if not builder:
            return flight.wait()
        index = error = None
        try:
            from fec.surfaces.staffer_work_sources import build_index
            index = build_index(self.router, options)
            return index
        except BaseException as exc:
            error = exc
            raise
        finally:
            with self.index_lock:
                if error is None and not self.released:
                    self.indexes[key] = index
                    while len(self.indexes) > 4:
                        self.indexes.popitem(last=False)
                del self.index_flights[key]
            flight.finish(index, error)

    def row_cache(self, index, options, operational):
        """The shared hop-row cache for everything that changes a cost or a neighbour set, and nothing else.

        The node fields a hop depends on (label, kind, name status: see
        AdvocacyRouter._person_allowed) are covered by router.nodes_revision, which
        the service bumps when it installs different node contents and leaves
        alone when a refresh yields identical contents. The identity or length of
        the nodes mapping is deliberately not part of the key: the service builds
        a new, equal mapping every poll, and that must not empty a warm cache.
        """
        router = self.router
        excluded = getattr(getattr(router, 'person_facts', None), 'network_excluded', ())
        key = (getattr(router, 'revision', None), getattr(getattr(router, 'activity_provider', None), 'revision', None),
               router.as_of, options['work_sources'], options['uncertainty_weight'], options['include_review'],
               options['include_unresolved'], options['include_historical'],
               _frozen(operational), _frozen(excluded), getattr(router, 'nodes_revision', None))
        with self.row_lock:
            if self.released:
                # The router has been replaced: nothing is stored on it. A request with a scope shares one cache among its passes.
                return self._unstored_row_cache(key, index)
            cache = self.row_caches.get(key)
            if cache is None or cache.index is not index:
                cache = self.row_caches[key] = _RowCache(index)
            self.row_caches.move_to_end(key)
            while len(self.row_caches) > self.MAX_ROW_CACHES:
                self.row_caches.popitem(last=False)
            return cache

    def _unstored_row_cache(self, key, index):
        scope = _REQUEST_WORK.get()
        if scope is None:
            return _RowCache(index)
        cache = scope.row_caches.get((self, key))
        if cache is None or cache.index is not index:
            cache = scope.row_caches[(self, key)] = _RowCache(index)
        return cache

    def release(self):
        """Let go of the work indexes and hop-row caches, for a router the service has replaced (AdvocacyRouter.release_search_state).

        A search that is running keeps the index and the caches it already holds (a request keeps its one index for all its passes,
        request_work); one that asks afterwards builds its own and answers exactly as before, so nothing here changes an answer. From
        here on the router stores no index and no hop-row cache it builds, and an index whose build was still running is not put back
        into the emptied table. The finished results (self.cache) are small and stay, as does the router: the two refer to each other, so
        the router itself is freed only by a garbage collection.
        """
        self.released = True          # first: whatever stores after this sees it, and whatever stored before is cleared below
        with self.index_lock:
            self.indexes.clear()
        with self.row_lock:
            self.row_caches.clear()

    def route(self, origin, targets, options, progress=None):
        started = time.monotonic()
        targets = tuple(sorted(set(targets)))
        router = self.router
        uncertainty_weight = options['uncertainty_weight']
        base = {**router._base(), 'origin': origin, 'target': None, 'target_count': len(targets),
                'algorithm': ALGORITHM, 'heuristic': 0,
                'objective': 'work_time', 'strength_model': VERSION,
                'cost': None, 'steps': None, 'node_ids': [], 'hops': [], 'edges': [],
                'graph': {'nodes': [], 'edges': []}, 'optimal': False, 'complete_search': True,
                'search_limited': False, 'cache_hit': False, 'timeout_ms': None,
                'personal_familiarity_established': False,
                'score_semantics': 'Best loopless path under explicit exposure and uncertainty assumptions; not a probability of access',
                'uncertainty_model': specification(router.as_of), 'uncertainty_weight': uncertainty_weight,
                'headcount_semantics': HEADCOUNT_SEMANTICS}
        if options['route_count'] > 1:
            base.update(routes=[], routes_requested=options['route_count'], routes_returned=0, alternatives_complete=True)
        index = self.index(options)
        base['work_source_coverage'] = index.source_coverage
        base['work_sources'] = options['work_sources']
        payroll_people = _payroll_people(index)
        operational = getattr(router, 'operational_staffer_ids', None)
        def allowed(p):
            return router._person_allowed(p, options) and (operational is None or p in operational or p in payroll_people)
        if not targets or any(not router._person_allowed(p, options) for p in (origin, *targets)):
            return {**base, 'status': 'unknown_person'}
        unavailable = [p for p in (origin, *targets) if not allowed(p)]
        office = options.get('_target_office')
        uncovered = []
        if office:
            # An office is a set of staff, so one without coverage must not fail the request (D-086): drop the uncovered
            # staff, search to the covered ones, and report who was left out on every result. The request fails only when
            # nothing can be searched: the origin is uncovered, or no member of the office is covered.
            uncovered = [p for p in targets if not allowed(p)]
            # What "uncovered" means in this search: no payroll evidence and not on the staffer roster, or, where payroll evidence is not read
            # at all (a directory-only comparison), not on the roster.
            base.update(uncovered_staff_count=len(uncovered), uncovered_staff_ids=uncovered,
                        uncovered_basis='roster' if options['work_sources'] == 'directory' else 'payroll_or_roster')
            if allowed(origin) and len(uncovered) < len(targets):
                targets, unavailable = tuple(p for p in targets if allowed(p)), []
        if unavailable:
            return {**base, 'status': 'insufficient_work_coverage',
                    'unavailable_person_ids': unavailable,
                    'message': "These people are known, but their records do not meet this model's authenticated staffer coverage requirements. Try Ecosystem for documented associations.",
                    'suggested_objective': SUGGESTED_OBJECTIVE}
        # The staff left out are part of the answer, so they are part of the key.
        key = (origin, targets, tuple(uncovered), tuple(sorted(options.items())))
        if key in self.cache:
            return {**deepcopy(self.cache[key]), 'cache_hit': True}
        # The index this request took at its start is the one it uses throughout. Asking for it again here would return that same
        # index, or build another if the table had lost it in between (release() empties the table).
        context = getattr(router, 'member_context', None) or MemberContext()
        excluded_contexts = frozenset(options['_excluded_contexts'])
        member = router.nodes.get(office, {}).get('member_bioguide') if office else None
        terminal = {}
        for p in targets:
            anchor = index.office_anchors(p).get(office, 0.) if office else 1.
            info = context.score(index.member_anchors(p), {member: 1.}) if member else {'value': 0., 'coverage': 0., 'status': 'no_member_target'}
            modifier = 1 + MEMBER_BETA * info['value']
            anchor_uncertainty = index.office_uncertainty(p, office, router.as_of) if office else {'cost': 0., 'factor': 1.}
            if anchor > 0:
                # Common normalization preserves ranking and keeps Dijkstra
                # terminal costs nonnegative, even with a prestige bonus.
                factor = anchor * modifier / (1 + MEMBER_BETA) if office else 1.
                terminal[p] = (-math.log(factor) + uncertainty_weight * anchor_uncertainty['cost'],
                               anchor, info, modifier, anchor_uncertainty)
        terminal_costs = {p: row[0] for p, row in terminal.items()}
        meter = _Meter(progress, started=started)
        neighbors = _Neighbors(self.row_cache(index, options, operational), allowed, router.as_of, uncertainty_weight, meter)
        evidence = {}
        def pair_evidence(a, b):
            pair = (a, b) if a < b else (b, a)
            if pair not in evidence:
                evidence[pair] = _full_evidence(index, pair, router.as_of, uncertainty_weight)
            return evidence[pair]
        def search(start, blocked_nodes=frozenset(), blocked_edges=frozenset()):
            return self.search(start, neighbors, terminal_costs, blocked_nodes, blocked_edges,
                               excluded_contexts=excluded_contexts, meter=meter)
        def path_costs(path):
            """(reported cost, exact cost): the float sum of the path's costs added in order from the origin, and their real sum."""
            total, exact = 0., Fraction(0)
            for a, b in zip(path, path[1:]):
                if b == SINK:
                    cost = terminal_costs[a]
                else:
                    evidence_ab = pair_evidence(a, b)
                    cost = _hop_cost(evidence_ab['strength'], evidence_ab['uncertainty']['cost'], uncertainty_weight)
                total += cost
                exact += Fraction(cost)
            return total, exact
        # Yen with the terminal sink included: alternatives may end at different
        # staff, and even a zero-staff-edge office route can have alternatives.
        # Candidates are ranked by the tie rule (_next_alternative), not by float cost.
        blocked_nodes = frozenset(options.get('_excluded_nodes', ()))
        blocked_edges = frozenset(options.get('_excluded_edges', ()))
        first = search(origin, blocked_nodes, blocked_edges)
        accepted, pending, seen = ([first] if first else []), [], set()
        if first:
            seen.add(tuple(first[0]))
        while accepted and len(accepted) < options['route_count']:
            previous = accepted[-1][0]
            for k in range(len(previous) - 1):
                root = previous[:k+1]
                excluded = {tuple(sorted((p[k], p[k+1]))) for p, _ in accepted if len(p) > k+1 and p[:k+1] == root}
                spur = search(root[-1], blocked_nodes | set(root[:-1]), blocked_edges | excluded)
                if not spur:
                    continue
                candidate = root[:-1] + spur[0]
                signature = tuple(candidate)
                if signature in seen:
                    continue
                seen.add(signature)
                cost, exact = path_costs(candidate)
                pending.append((exact, cost, candidate))
            if not pending:
                break
            accepted.append(_next_alternative(pending))
        results = []
        for rank, (path, cost) in enumerate(accepted, 1):
            ids, hops = path[:-1], []
            for a, b in zip(ids, ids[1:]):
                evidence_ab = pair_evidence(a, b)
                hop_cost = _hop_cost(evidence_ab['strength'], evidence_ab['uncertainty']['cost'], uncertainty_weight)
                workplaces = [{**w, 'employer_label': router.nodes.get(w['employer_id'], {}).get('label', w['employer_id'])}
                              for w in evidence_ab['workplaces']]
                selected_sources = {p: [{k:v for k,v in e.items() if not k.startswith('_')}
                    for e in index.source_episodes.get(p, [])
                    if any(e['employer_id'] == w['employer_id'] and
                           any(ordinal(e['start']) < end and ordinal(e['end_exclusive']) > start
                               for start,end in w['overlap_intervals']) for w in workplaces)] for p in (a,b)}
                modeled = any(e['modeled_payroll_interval'] for rows in selected_sources.values() for e in rows)
                hops.append({**evidence_ab, 'workplaces': workplaces, 'source': a, 'target': b,
                    'work_sources': sorted({e['work_source'] for rows in selected_sources.values() for e in rows}),
                    'modeled_exposure': modeled,
                    'overlap_duration_days': None if modeled else evidence_ab['overlap_duration_days'],
                    'model_interval_overlap_days': evidence_ab['overlap_duration_days'] if modeled else None,
                    'date_semantics': 'Exposure may include modeled payroll report intervals; see source bounds and role scope' if modeled else 'Exposure from supplied employment intervals; payroll trajectory titles are stint summaries',
                    'relationship': 'work_time_overlap', 'relationship_label': 'Dated workplace overlap, adjusted for staff size',
                    'cost': hop_cost, 'work_proximity': evidence_ab['effective_exposure_days']/evidence_ab['overlap_duration_days'],
                    'employer_id': workplaces[0]['employer_id'] if len(workplaces) == 1 else None,
                    'employer_label': workplaces[0]['employer_label'] if len(workplaces) == 1 else 'Multiple documented workplaces',
                    'evidence_class': 'modeled_payroll_exposure' if modeled else 'dated_work_overlap', 'temporal_overlap_established': not modeled,
                    'modeled_overlap': modeled, 'personal_familiarity_established': False,
                    'provenance': {'model': VERSION, 'allocation_model_is_assumption': True,
                        'formula': 'route cost=sum(-log(0.8*W)+weight*u) plus uncertainty-adjusted terminal office anchor; u is not a probability',
                        'source_evidence': selected_sources}})
            end = ids[-1]
            score = math.exp(-cost) * ((1+MEMBER_BETA) if office else 1)
            uncertainty_cost = sum(h['uncertainty']['cost'] for h in hops) + terminal[end][4]['cost']
            unpenalized = math.prod(HOP_FACTOR * h['strength'] for h in hops)
            if office:
                unpenalized *= terminal[end][1] * terminal[end][3]
            results.append({**base, 'status': 'found', 'optimal': True, 'target': end,
                'node_ids': ids, 'steps': len(hops), 'cost': cost, 'ranking_cost': cost,
                'connection_score': score, 'route_strength': score/terminal[end][3] if office else score,
                'unpenalized_connection_score': unpenalized, 'uncertainty_cost': uncertainty_cost,
                'uncertainty_factor': math.exp(-uncertainty_weight * uncertainty_cost),
                'office_anchor_uncertainty': terminal[end][4] if office else None,
                'member_context': terminal[end][2], 'member_context_source': context.provenance,
                'member_context_max_bonus': MEMBER_BETA, 'office_anchor_strength': terminal[end][1] if office else None,
                'hops': hops, 'edges': hops, 'route_rank': rank,
                'graph': {'nodes': [router.nodes[p] for p in ids], 'edges': hops}})
        # Describe dependence among the returned alternatives without pretending
        # that k-best paths are independent trials or a calibrated success rate.
        if results:
            first_pairs = {tuple(sorted((a,b))) for a,b in zip(results[0]['node_ids'],results[0]['node_ids'][1:])}
            first_people = set(results[0]['node_ids'][1:-1])
            first_contexts = {w['context_group'] for h in results[0]['hops'] for w in h['workplaces']}
            for route in results:
                pairs = {tuple(sorted((a,b))) for a,b in zip(route['node_ids'],route['node_ids'][1:])}
                contexts = {w['context_group'] for h in route['hops'] for w in h['workplaces']}
                route['dependence_on_best_route'] = {
                    'shared_staff_edges': len(first_pairs & pairs),
                    'shared_intermediaries': len(first_people & set(route['node_ids'][1:-1])),
                    'shared_workplace_contexts': len(first_contexts & contexts),
                    'same_terminal_contact': route['target'] == results[0]['target'],
                    'scope': 'returned alternatives only; no independence or failure-probability claim'}
        if results:
            result = results[0]
        else:
            # Its own meter: the counters below describe the search, not the reading of its rows that explains the result.
            explainer = _Neighbors(neighbors.cache, allowed, router.as_of, uncertainty_weight, _Meter())
            # The staff left out (not covered) and the staff kept out (avoided) are not the staff the search could use.
            staff_left_out = bool(uncovered or options.get('_avoided_targets'))
            result = {**base, 'status': 'unreachable', 'optimal': True,
                      **_explain_unreachable(explainer, origin, terminal_costs, office, blocked_nodes, blocked_edges, excluded_contexts,
                                             staff_left_out)}
        result = {**result, 'expanded': meter.expanded, 'neighbor_checks': meter.checks,
                  'pairs_scored': meter.pairs_scored,
                  'search_elapsed_ms': round((time.monotonic()-started)*1000, 2)}
        if options['route_count'] > 1:
            result.update(routes=results, routes_requested=options['route_count'], routes_returned=len(results), alternatives_complete=True)
        self.cache[key] = deepcopy(result)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return result
