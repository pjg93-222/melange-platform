"""Exact positive-cost routes through ordered, additive person overlap.

An inverted feature index avoids materializing office/party/school cliques.
Only a mathematically sufficient strength bound may prune a candidate edge.
"""
from collections import defaultdict, OrderedDict
from copy import deepcopy
import heapq
import itertools
import math
import time

try:
    from .staffer_connection_facts import DIMENSIONS
    from .staffer_route_cancel import check_cancelled
except ImportError:  # direct script execution: no package context
    import os as _bootstrap_os, sys as _bootstrap_sys
    _bootstrap_root = _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(
        _bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))
    if _bootstrap_root not in _bootstrap_sys.path:
        _bootstrap_sys.path.insert(0, _bootstrap_root)
    from fec.surfaces.staffer_connection_facts import DIMENSIONS
    from fec.surfaces.staffer_route_cancel import check_cancelled

VERSION = 'ordered-person-overlap-v5-dated-work'
WORK_TIME_VERSION = 'exact-workplace-overlap-time-v1'
DISTANCE_SCALE = 1_000_000
# One additional introduction has the same balanced cost as a unit of
# connection distance (a tie supported by one current-employment overlap).
BALANCED_STEP_UNITS = DISTANCE_SCALE


def distance_units(strength):
    return max(1, math.ceil(DISTANCE_SCALE * 128.0 / strength))


def work_time_strength(days, proximity=1.0):
    """Exact workplace is proximity 1; time grows logarithmically by 30-day units."""
    return proximity * math.log2(1 + days / 30.0)


def authenticated_work_evidence(evidence):
    if not evidence or not evidence.get('temporal_overlap_established') or evidence.get('modeled_overlap'):
        return False
    days = evidence.get('overlap_duration_days')
    if not isinstance(days, int) or days <= 0 or not evidence.get('employer_id'):
        return False
    proofs = evidence.get('provenance') if isinstance(evidence.get('provenance'), list) else [evidence.get('provenance', {})]
    # Both sides must come from reconciled Politico career history. Payroll
    # remains targeted identity corroboration; it cannot populate work ties.
    return len(proofs) == 2 and all(isinstance(p, dict) and p.get('work_route_source') == 'politico_profile_history'
                                    for p in proofs)


class OverlapRoutes:
    def __init__(self, router, facts):
        self.router, self.facts = router, facts
        self.postings = {key: defaultdict(set) for key, _, _ in DIMENSIONS}
        self.keys = {}
        for person, features in facts.features.items():
            self.keys[person] = {key: frozenset(features[key]) for key, _, _ in DIMENSIONS}
            for dimension, values in features.items():
                for value in values:
                    self.postings[dimension][value].add(person)
        self.cache = OrderedDict()

    @staticmethod
    def strength(counts):
        return sum(weight * math.log2(1 + counts.get(key, 0)) for key, _, weight in DIMENSIONS)

    def active_keys(self, person, options):
        if not options['include_review']:
            result = {key: frozenset() for key, _, _ in DIMENSIONS}
        else:
            result = dict(self.keys.get(person, {}))
        # Source and user evidence stay independently selectable.
        values = self.facts.features.get(person, {}).get('current_employment', {})
        result['current_employment'] = frozenset(key for key, item in values.items() if any(
            options['include_user_asserted'] if p.get('user_asserted') else options['include_review']
            for p in item['sources']))
        if not options['include_historical']:
            result['past_employment'] = frozenset()
        if not options['include_policy']:
            result['subject'] = frozenset()
        return result

    def shared(self, a, b, keys):
        return {key: keys[a].get(key, frozenset()) & keys[b].get(key, frozenset()) for key, _, _ in DIMENSIONS}

    def route(self, origin, targets, options, progress=None):
        started = time.monotonic()
        router = self.router
        targets = tuple(sorted(set(targets)))
        excluded_nodes = set(options.get('_excluded_nodes', ()))
        excluded_edges = set(options.get('_excluded_edges', ()))
        work_time = options['objective'] == 'work_time'
        result = {**router._base(), 'origin': origin, 'target': None, 'target_count': len(targets),
            'objective': options['objective'], 'strength_model': VERSION, 'cost': None, 'steps': None,
            'score_semantics': ('Exact workplace proximity multiplied by documented overlap time; personal familiarity is not established.' if work_time else
                                'heuristic shared-background score; not measured personal closeness or introduction probability'),
            'personal_familiarity_established': False,
            'node_ids': [], 'hops': [], 'edges': [], 'graph': {'nodes': [], 'edges': []},
            'optimal': False, 'complete_search': True, 'timeout_ms': None, 'search_limited': False,
            'cache_hit': False, 'prefer_current': 'ordered_weights',
            'distance_scale': DISTANCE_SCALE,
            'weight_order': ([{'key': 'work_proximity', 'label': 'Exact workplace', 'weight': 1.0},
                              {'key': 'overlap_time', 'label': 'Documented overlap days', 'transform': 'log2(1 + days/30)'}] if work_time else
                             [{'key': key, 'label': label, 'weight': weight} for key, label, weight in DIMENSIONS])}
        operational = getattr(router, 'operational_staffer_ids', None)
        if not targets or any(not router._person_allowed(p, options) or (work_time and operational is not None and p not in operational)
                              for p in (origin, *targets)):
            return {**result, 'status': 'unknown_person'}
        cache_key = (origin, targets, tuple(sorted(options.items())))
        if cache_key in self.cache:
            cached = deepcopy(self.cache[cache_key])
            return {**cached, 'cache_hit': True, 'original_search_elapsed_ms': cached['search_elapsed_ms'],
                    'search_elapsed_ms': round((time.monotonic()-started)*1000, 2)}
        fewest = options['objective'] == 'overlap_fewest_steps'
        balanced = options['objective'] in ('overlap_balanced', 'work_time')
        step_units = BALANCED_STEP_UNITS if balanced else 0
        weight = lambda cost: (1, cost) if fewest else (cost + step_units, 1)
        # Every edge has at least one integer distance unit. This conservative
        # bound remains valid for any strength, filters or future evidence.
        min_hop = weight(1)
        if balanced:
            result['balance_step_penalty'] = BALANCED_STEP_UNITS / DISTANCE_SCALE
        add = lambda a, b: (a[0] + b[0], a[1] + b[1])
        infinity, zero = (math.inf, math.inf), (0, 0)
        serial = itertools.count()
        queues = [[(zero, next(serial), origin)], [(zero, next(serial), p) for p in targets]]
        dist = [{origin: zero}, dict.fromkeys(targets, zero)]
        prev, settled = [{}, {}], [set(), set()]
        best, meeting = (zero, origin) if origin in targets else (infinity, None)
        keys = {}
        base_cache = {}
        def person_keys(p):
            if p not in keys:
                keys[p] = self.active_keys(p, options)
            return keys[p]
        budget = {'neighbor_checks': 0, 'deadline': math.inf}
        full_options = {**options, 'max_neighbor_checks': math.inf, 'max_expansions': math.inf,
                        'work_time_only': work_time}
        expanded, pruned, skipped_expansions, last_progress = 0, 0, 0, started
        def trim(side):
            while queues[side] and (queues[side][0][2] in settled[side] or queues[side][0][0] != dist[side].get(queues[side][0][2])):
                heapq.heappop(queues[side])
            return queues[side][0][0] if queues[side] else infinity
        while queues[0] and queues[1]:
            lower = [trim(0), trim(1)]
            if meeting is not None and add(*lower) >= best:
                break
            if not queues[0] or not queues[1]:
                break
            # In a fewest-step office search, finish the shallower layer (all
            # zero-step office endpoints) before exploring extra intermediaries.
            # Either side may advance in bidirectional Dijkstra; this schedule
            # strengthens its lower bound earlier without changing the answer.
            side = min((0, 1), key=lambda i: (lower[i][0], len(queues[i]), lower[i][1], i) if fewest else (lower[i], len(queues[i]), i))
            distance, _, person = heapq.heappop(queues[side])
            settled[side].add(person)
            # Edges from an opposite settled node were already relaxed, and
            # meeting costs were checked when this node was discovered. Every
            # other improving continuation needs an edge plus at least the
            # opposite frontier's lower bound. Skip only when that bound proves
            # no improvement is possible; never apply a search-effort cutoff.
            if meeting is not None and (person in settled[1-side] or
                    add(add(distance, min_hop), lower[1-side]) >= best):
                skipped_expansions += 1
                continue
            expanded += 1
            if not expanded % 64:
                check_cancelled()
            own = person_keys(person)
            # Only dated work can supply employment points. Shared lobbying
            # reports remain available in evidence-cost routes and the activity
            # explorer; they do not establish employment or add affinity points.
            if person not in base_cache:
                base_cache[person] = router._neighbors(person, full_options, budget, policy=False, activity=False)
            base = base_cache[person]
            seen = {person}
            def candidates():
                # Strong classes first produce a good upper bound early. Every
                # possible social edge is covered by at least one posting.
                yield from base
                if work_time:
                    return
                for index, (dimension, _, _) in enumerate(DIMENSIONS):
                    maximum = self.strength({key: len(own.get(key, ())) for key, _, _ in DIMENSIONS[index:]})
                    if not maximum or add(add(distance, weight(distance_units(maximum))), lower[1-side]) >= best:
                        return
                    for key in sorted(own.get(dimension, ())):
                        yield from self.postings[dimension][key]
            for other in candidates():
                if other in excluded_nodes or tuple(sorted((person, other))) in excluded_edges:
                    continue
                if other in seen:
                    continue
                seen.add(other)
                if other in settled[side] or other in settled[1-side]:
                    continue
                budget['neighbor_checks'] += 1
                if not router._person_allowed(other, options) or (work_time and operational is not None and other not in operational):
                    continue
                evidence = base.get(other)
                if work_time:
                    if not authenticated_work_evidence(evidence):
                        continue
                    shared = {key: frozenset() for key, _, _ in DIMENSIONS}
                    extra = None
                    strength = work_time_strength(evidence['overlap_duration_days'])
                    cost = max(1, math.ceil(DISTANCE_SCALE / strength))
                    candidate = add(distance, weight(cost))
                    if add(candidate, lower[1-side]) >= best:
                        pruned += 1
                        continue
                    if candidate < dist[side].get(other, infinity):
                        dist[side][other] = candidate
                        prev[side][other] = (person, other, cost, strength, shared, evidence, extra)
                        heapq.heappush(queues[side], (candidate, next(serial), other))
                    if other in dist[1-side]:
                        joined = add(dist[side][other], dist[1-side][other])
                        if joined < best:
                            best, meeting = joined, other
                    continue
                person_keys(other)
                shared = self.shared(person, other, keys)
                counts = {key: len(values) for key, values in shared.items()}
                # A dated work edge not represented in profile features
                # retains a work-evidence contribution. Never count it twice.
                extra = None
                if evidence and not (counts['current_employment'] or counts['past_employment']):
                    extra = 'current_employment' if evidence.get('temporal_status') == 'current' else 'past_employment'
                    counts[extra] += 1
                strength = self.strength(counts)
                if not strength:
                    continue
                cost = distance_units(strength)
                candidate = add(distance, weight(cost))
                # Opposite settled neighbors were already accounted for and
                # excluded above. Others need at least that frontier's lower
                # bound. This is proof pruning, never a work limit.
                if add(candidate, lower[1-side]) >= best:
                    pruned += 1
                    continue
                if candidate < dist[side].get(other, infinity):
                    dist[side][other] = candidate
                    prev[side][other] = (person, other, cost, strength, shared, evidence, extra)
                    heapq.heappush(queues[side], (candidate, next(serial), other))
                if other in dist[1-side]:
                    joined = add(dist[side][other], dist[1-side][other])
                    if joined < best:
                        best, meeting = joined, other
            now = time.monotonic()
            if progress and now - last_progress >= .25:
                progress({'expanded': expanded, 'neighbor_checks': budget['neighbor_checks'],
                    'elapsed_ms': round((now-started)*1000),
                    'best_steps_so_far': (best[0] if fewest else best[1]) if meeting else None})
                last_progress = now
        result.update(status='found' if meeting is not None else 'unreachable', optimal=True,
            expanded=expanded, neighbor_checks=budget['neighbor_checks'], pruned=pruned,
            proof_pruned_expansions=skipped_expansions,
            search_elapsed_ms=round((time.monotonic()-started)*1000, 2))
        if meeting is not None:
            path, person = [], meeting
            while person != origin:
                entry = prev[0][person]
                path.append(entry)
                person = entry[0]
            path.reverse()
            person = meeting
            while person not in targets:
                entry = prev[1][person]
                path.append((entry[1], entry[0], *entry[2:]))
                person = entry[0]
            hops = [self.hop(entry, options) for entry in path]
            ids = [origin] + [hop['target'] for hop in hops]
            result.update(target=ids[-1], node_ids=ids, steps=len(hops), cost=sum(h['distance_units'] for h in hops)/DISTANCE_SCALE,
                hops=hops, edges=hops,
                graph={'nodes': [router.nodes[p] for p in ids], 'edges': hops})
            if balanced:
                result['balanced_score'] = (sum(h['distance_units'] for h in hops) + BALANCED_STEP_UNITS * len(hops)) / DISTANCE_SCALE
        self.cache[cache_key] = deepcopy(result)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)
        return result

    def hop(self, entry, options):
        a, b, cost, strength, shared, evidence, extra = entry
        if evidence and evidence['source'] != a:
            evidence = self.router._reverse_hop(evidence)
        if options['objective'] == 'work_time':
            days = evidence['overlap_duration_days']
            return {'source': a, 'target': b, 'relationship': 'work_time_overlap',
                'relationship_label': 'Same exact workplace with documented overlap',
                'cost': cost / DISTANCE_SCALE, 'distance_units': cost, 'strength': strength,
                'work_proximity': 1.0, 'overlap_duration_days': days,
                'start': evidence.get('start'), 'end_exclusive': evidence.get('end_exclusive'),
                'employer_id': evidence.get('employer_id'), 'employer_label': evidence.get('employer_label'),
                'evidence_class': 'dated_work_overlap', 'temporal_overlap_established': True,
                'modeled_overlap': False, 'personal_familiarity_established': False,
                'score_semantics': 'work proximity × documented overlap time',
                'provenance': {'model': WORK_TIME_VERSION,
                    'formula': 'strength = 1.0 exact-workplace proximity × log2(1 + documented overlap days / 30); route minimizes inverse strength plus one unit per introduction',
                    'work_evidence': evidence}}
        overlaps = []
        user_asserted = bool(evidence and evidence.get('user_asserted'))
        for key, label, weight in DIMENSIONS:
            values = []
            for value in sorted(shared[key]):
                left, right = self.facts.features[a][key][value], self.facts.features[b][key][value]
                sources = {person: [p for p in item['sources'] if not (key == 'current_employment' and
                    not (options['include_user_asserted'] if p.get('user_asserted') else options['include_review']))]
                    for person, item in ((a, left), (b, right))}
                user_asserted |= any(p.get('user_asserted') for items in sources.values() for p in items)
                values.append({'label': left['label'], 'key': value,
                    'sources': sources})
            if key == extra:
                values.append({'label': evidence.get('employer_label') or 'Shared reported work', 'work_evidence': evidence})
            if values:
                display_label = 'Same employer in latest records' if key == 'current_employment' else label
                overlaps.append({'key': key, 'label': display_label, 'count': len(values),
                    'contribution': weight * math.log2(1+len(values)), 'matches': values})
        return {'source': a, 'target': b, 'relationship': 'shared_overlap', 'cost': cost/DISTANCE_SCALE, 'distance_units': cost,
            'strength': strength, 'overlaps': overlaps, 'work_evidence': evidence,
            'evidence_class': evidence.get('evidence_class', 'dated_work_overlap') if evidence else 'shared_background_only',
            'temporal_overlap_established': bool(evidence and evidence.get('temporal_overlap_established')),
            'modeled_overlap': bool(evidence and evidence.get('modeled_overlap')),
            'personal_familiarity_established': False,
            'score_semantics': 'heuristic shared-background score; not measured personal closeness or introduction probability',
            'temporal_status': evidence['temporal_status'] if evidence else 'overlap_unconfirmed',
            'provenance': {'model': VERSION, 'formula': 'strength = sum(weight * log2(1 + distinct shared facts)); integer distance = ceil(1000000 * 128 / strength)'},
            'user_asserted': user_asserted}
