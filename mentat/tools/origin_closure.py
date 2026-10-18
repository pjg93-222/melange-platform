"""Shared, pure derivation of the frontier ledger's origin closure (ledger schema 3). Standard library only.

Schema 3 stores each origin once (`origin_catalog`) and each seeded unit/origin once (`origin_seeds`). Which units an
origin reaches is not stored: it is a deterministic function of the seeds, the archive-validated `edges` and, when the
ledger policy enables later-cycle context, the forward-cycle links. This module is that function. It opens no file and
makes no call; `from_db` only runs SELECTs on a connection the caller owns.

Graph: a node is a unit (committee_id, cycle). Edge u -> v for every stored edge row whose destination cycle is not
earlier than its source cycle, plus (policy on) u -> (committee, later cycle) for each later cycle. Strongly connected
units are condensed (iterative Tarjan), origin bitsets (Python ints, one bit per origin_ref) are propagated once in
topological order, and each unit reads its component's bitset. Memory is at most units x origins / 8 bytes.

The result is a LOWER BOUND: "origins observed to reach this unit through collected disclosures". A missing origin means
not observed, never "did not reach" (`disclosure()`; `lineage_disclosure()` adds what is still uncollected).
"""
import json
from collections import defaultdict, deque

CYCLES = (2022, 2024, 2026)
CYCLE_FIELDS = ('first_reached_cycle', 'activity_cycle', 'later_cycle_context')
CLOSURE_BASIS = 'collected_edges_only'


def disclosure():
    """Fields every receipt or consumer output built on a closure must carry."""
    return {'closure_basis': CLOSURE_BASIS, 'lower_bound': True}


def base_of(metadata):
    """An origin's base metadata: the seed metadata without the three per-unit cycle fields."""
    return {key: value for key, value in metadata.items() if key not in CYCLE_FIELDS}


def metadata_for(base, first, cycle):
    """The per-unit metadata a schema-2 `origins` row stored, rebuilt from the catalog (byte-equal once encoded)."""
    return {**base, 'first_reached_cycle': first, 'activity_cycle': cycle, 'later_cycle_context': cycle > first}


def metadata_json(base, first, cycle):
    return json.dumps(metadata_for(base, first, cycle), sort_keys=True, separators=(',', ':'), allow_nan=False)


def iter_bits(value):
    """Set bit positions of a non-negative int, ascending."""
    data = value.to_bytes((value.bit_length() + 7) // 8, 'little')
    for index, byte in enumerate(data):
        if byte:
            for bit in range(8):
                if byte >> bit & 1:
                    yield index * 8 + bit


def _components(nodes, successors):
    """Iterative Tarjan. Components come out sinks first: every component reachable from X is emitted before X."""
    index, low, on_stack, stack, found = {}, {}, set(), [], []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work = [(root, iter(successors.get(root, ())))]
        while work:
            node, pending = work[-1]
            descended = False
            for follower in pending:
                if follower not in index:
                    index[follower] = low[follower] = counter
                    counter += 1
                    stack.append(follower)
                    on_stack.add(follower)
                    work.append((follower, iter(successors.get(follower, ()))))
                    descended = True
                    break
                if follower in on_stack:
                    low[node] = min(low[node], index[follower])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index[node]:
                members = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    members.append(member)
                    if member == node:
                        break
                found.append(members)
    return found


class Closure:
    """units: (committee_id, cycle); edges: (committee_id, cycle, destination_committee_id, destination_cycle);
    seeds: (committee_id, cycle, origin_ref); later_context: the ledger policy (forward-cycle links on or off)."""

    def __init__(self, units, edges, seeds, later_context):
        self.later_context = bool(later_context)
        self.units = {tuple(unit) for unit in units}
        self.seed_bits = defaultdict(int)
        for committee, cycle, ref in seeds:
            self.seed_bits[(committee, cycle)] |= 1 << ref
        successors = defaultdict(dict)
        nodes = set(self.units) | set(self.seed_bits)
        for committee, cycle, destination, destination_cycle in edges:
            source, target = (committee, cycle), (destination, destination_cycle)
            nodes.add(source)
            nodes.add(target)
            if destination_cycle >= cycle and source != target:
                successors[source][target] = 'edge'
        self.undeclared = nodes - self.units
        self.missing_forward = []
        if self.later_context:
            for committee, cycle in sorted(nodes):
                for later in CYCLES:
                    if later > cycle:
                        if (committee, later) in nodes:
                            successors[(committee, cycle)].setdefault((committee, later), 'forward_cycle')
                        elif (committee, cycle) in self.units:
                            self.missing_forward.append(((committee, cycle), (committee, later)))
        self._successors = successors
        self._components = _components(sorted(nodes), successors)
        self._component_of = {node: number for number, members in enumerate(self._components) for node in members}
        bits = [0] * len(self._components)
        for node, value in self.seed_bits.items():
            bits[self._component_of[node]] |= value
        for number in range(len(bits) - 1, -1, -1):   # sources first: every predecessor component has a higher number
            value = bits[number]
            if value:
                for node in self._components[number]:
                    for follower in successors.get(node, ()):
                        target = self._component_of[follower]
                        if target != number:
                            bits[target] |= value
        self._bits = bits
        self._predecessors = None
        self.total = sum(self.bitset(unit).bit_count() for unit in self.units)

    def bitset(self, unit):
        number = self._component_of.get(tuple(unit))
        return 0 if number is None else self._bits[number]

    def origins(self, unit):
        """Sorted origin_refs observed to reach `unit`."""
        return list(iter_bits(self.bitset(unit)))

    def has(self, unit, ref):
        return bool(self.bitset(unit) >> ref & 1)

    def count(self, unit):
        return self.bitset(unit).bit_count()

    def cyclic_components(self):
        """Strongly connected groups of more than one unit (payment loops, mutual payers)."""
        return [sorted(members) for members in self._components if len(members) > 1]

    def earliest_cycles(self):
        """{origin_ref: smallest cycle among declared units carrying it}. Propagation never lowers a cycle, so this is
        also the smallest seed cycle; the catalog's first_reached_cycle is checked against it."""
        by_cycle = defaultdict(int)
        for unit in self.units:
            by_cycle[unit[1]] |= self.bitset(unit)
        earliest, seen = {}, 0
        for cycle in sorted(by_cycle):
            for ref in iter_bits(by_cycle[cycle] & ~seen):
                earliest[ref] = cycle
            seen |= by_cycle[cycle]
        return earliest

    def _reverse(self):
        if self._predecessors is None:
            reverse = defaultdict(list)
            for source, targets in self._successors.items():
                for target, via in targets.items():
                    reverse[target].append((source, via))
            self._predecessors = reverse
        return self._predecessors

    def ancestors(self, unit):
        """Units with a path to `unit` (collected edges and forward-cycle links), `unit` itself only if it sits on a cycle."""
        reverse, seen, queue = self._reverse(), set(), deque([tuple(unit)])
        while queue:
            for source, _via in reverse.get(queue.popleft(), ()):
                if source not in seen:
                    seen.add(source)
                    queue.append(source)
        return seen

    def witness(self, unit, ref):
        """Shortest seed-to-unit path for one origin, or None when the origin is not observed to reach the unit.
        Each hop: {'unit', 'via': 'seed' | 'edge' | 'forward_cycle', 'from'}; join `edges` on (from, unit) for evidence."""
        unit = tuple(unit)
        if not self.has(unit, ref):
            return None
        reverse, parent, queue = self._reverse(), {unit: None}, deque([unit])
        while queue:
            node = queue.popleft()
            if self.seed_bits.get(node, 0) >> ref & 1:
                hops = [{'unit': node, 'via': 'seed'}]
                while parent[node] is not None:
                    following, via = parent[node]
                    hops.append({'unit': following, 'via': via, 'from': node})
                    node = following
                return hops
            for source, via in reverse.get(node, ()):
                if source not in parent and self.has(source, ref):
                    parent[source] = (node, via)
                    queue.append(source)
        raise AssertionError('a unit carrying an origin always has a seed ancestor')


def read_policy(db, schema='main'):
    """The ledger's recorded later-cycle context policy; a missing or malformed record is an error, never a default."""
    row = db.execute('SELECT value FROM ' + schema + ".ledger_policy WHERE name='later_cycle_context'").fetchone()
    if row is None or row[0] not in ('true', 'false'):
        raise ValueError('Ledger records no valid later_cycle_context policy.')
    return row[0] == 'true'


def read_catalog(db, schema='main'):
    """{origin_ref: (origin_id, base_metadata_dict, first_reached_cycle)}."""
    return {r[0]: (r[1], json.loads(r[2]), r[3])
            for r in db.execute('SELECT origin_ref,origin_id,base_metadata_json,first_reached_cycle FROM ' + schema + '.origin_catalog')}


def from_db(db, later_context=None, schema='main'):
    """Closure of a schema-3 ledger. later_context=None reads the ledger's own recorded policy (what a consumer wants)."""
    if later_context is None:
        later_context = read_policy(db, schema)
    units = [(r[0], r[1]) for r in db.execute('SELECT committee_id,cycle FROM ' + schema + '.units')]
    edges = [tuple(r) for r in db.execute('SELECT DISTINCT committee_id,cycle,destination_committee_id,destination_cycle FROM ' + schema + '.edges')]
    seeds = [tuple(r) for r in db.execute('SELECT committee_id,cycle,origin_ref FROM ' + schema + '.origin_seeds')]
    return Closure(units, edges, seeds, later_context)


def incomplete_by_unit(db, schema='main', kinds=None):
    """{unit: endpoints that are not 'complete'} (incoming-receipt family A excluded; kinds narrows further).
    Unsupported families count: a coverage gap is a gap whatever the reason."""
    counts = defaultdict(int)
    for committee, cycle, kind, count in db.execute("SELECT committee_id,cycle,kind,COUNT(*) FROM " + schema + ".endpoints WHERE kind!='A' AND status!='complete' GROUP BY committee_id,cycle,kind"):
        if kinds is None or kind in kinds:
            counts[(committee, cycle)] += count
    return counts


def lineage_disclosure(closure, unit, incomplete):
    """What a consumer must print next to a unit's origin list: the basis, the lower-bound flag and what is still
    uncollected. An origin that is absent is 'not observed', never 'did not reach'. The ledger-wide count is given because an
    edge not yet collected from ANY unit with an incomplete endpoint could reach this unit's ancestry."""
    ancestors = closure.ancestors(unit)
    return {**disclosure(), 'ancestor_units': len(ancestors),
            'noncomplete_endpoints_among_ancestors': sum(incomplete.get(a, 0) for a in ancestors),
            'noncomplete_endpoints_ledger_wide': sum(incomplete.values()),
            'absence_means': 'not observed through collected edges; never evidence that the origin did not reach the unit'}
