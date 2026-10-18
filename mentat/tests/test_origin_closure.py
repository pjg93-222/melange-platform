"""Inert controls for the shared origin-closure helper (D073). Pure in-memory graphs plus one throwaway SQLite file; no provider, key or product file."""
import random
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import origin_closure as oc
from origin_closure import Closure

A, B, C, D, E = ('C0000000%d' % n for n in range(1, 6))


def units(*pairs):
    return [(c, y) for c in pairs for y in oc.CYCLES]


def naive(unit_set, edges, seeds, later):
    """Independent oracle: per origin, a plain flood fill from its seed units (a different algorithm from the helper's SCC + bitsets)."""
    forward = {}
    for c, y, d, dy in edges:
        if dy >= y:
            forward.setdefault((c, y), set()).add((d, dy))
    if later:
        for c, y in unit_set:
            for later_y in oc.CYCLES:
                if later_y > y:
                    forward.setdefault((c, y), set()).add((c, later_y))
    reached = {}
    for ref in {r for _, _, r in seeds}:
        todo = [(c, y) for c, y, r in seeds if r == ref]
        seen = set(todo)
        while todo:
            for nxt in forward.get(todo.pop(), ()):
                if nxt not in seen:
                    seen.add(nxt)
                    todo.append(nxt)
        reached[ref] = seen
    return reached


class Shape(unittest.TestCase):
    def test_chain_and_branch_reach_downstream_only(self):
        u = [(c, 2026) for c in (A, B, C, D)]
        k = Closure(u, [(A, 2026, B, 2026), (B, 2026, C, 2026), (A, 2026, D, 2026)], [(A, 2026, 1), (C, 2026, 2)], False)
        self.assertEqual([k.origins((n, 2026)) for n in (A, B, C, D)], [[1], [1], [1, 2], [1]])
        self.assertEqual(k.total, 5)
        self.assertEqual(k.cyclic_components(), [])

    def test_payment_cycle_is_one_component_and_both_members_share_the_set(self):
        u = [(c, 2026) for c in (A, B, C, D)]
        edges = [(A, 2026, B, 2026), (B, 2026, A, 2026), (B, 2026, C, 2026), (D, 2026, A, 2026)]
        k = Closure(u, edges, [(A, 2026, 1), (D, 2026, 2)], False)
        self.assertEqual(k.cyclic_components(), [[(A, 2026), (B, 2026)]])
        self.assertEqual([k.origins((n, 2026)) for n in (A, B, C, D)], [[1, 2], [1, 2], [1, 2], [2]])
        # the seed on D (upstream of the cycle) reaches the whole cycle and C below it; the seed on A never reaches D
        self.assertEqual(k.origins((D, 2026)), [2])

    def test_self_payment_and_backward_cycle_edges_add_nothing(self):
        u = units(A, B)
        k = Closure(u, [(A, 2024, A, 2024), (A, 2026, B, 2024)], [(A, 2026, 1)], False)
        self.assertEqual(k.origins((A, 2026)), [1])
        self.assertEqual(k.origins((B, 2024)), [], 'an edge to an earlier cycle is not followed')
        self.assertEqual(k.total, 1)

    def test_forward_cycles_follow_the_policy_and_only_forward(self):
        u = units(A, B)
        edges = [(A, 2022, B, 2022)]
        on, off = Closure(u, edges, [(A, 2022, 1)], True), Closure(u, edges, [(A, 2022, 1)], False)
        self.assertEqual([y for y in oc.CYCLES if on.has((A, y), 1)], [2022, 2024, 2026])
        self.assertEqual([y for y in oc.CYCLES if on.has((B, y), 1)], [2022, 2024, 2026], 'the edge destination inherits the later cycles too')
        self.assertEqual([y for y in oc.CYCLES if off.has((A, y), 1)], [2022])
        self.assertEqual([y for y in oc.CYCLES if off.has((B, y), 1)], [2022])
        later_seed = Closure(u, [], [(A, 2026, 1)], True)
        self.assertEqual([y for y in oc.CYCLES if later_seed.has((A, y), 1)], [2026], 'cycles never propagate backwards')

    def test_forward_cycle_then_edge_composes(self):
        # seed 2022 -> forward link -> 2024 edge -> destination at 2024 (and its 2026)
        u = units(A, B)
        k = Closure(u, [(A, 2024, B, 2024)], [(A, 2022, 7)], True)
        self.assertEqual([y for y in oc.CYCLES if k.has((B, y), 7)], [2024, 2026])
        self.assertFalse(k.has((B, 2022), 7))

    def test_missing_forward_unit_and_undeclared_endpoint_are_reported_not_hidden(self):
        k = Closure([(A, 2022)], [(A, 2022, B, 2022)], [(A, 2022, 1)], True)
        self.assertEqual(sorted(k.undeclared), [(B, 2022)])
        self.assertEqual(k.missing_forward, [((A, 2022), (A, 2024)), ((A, 2022), (A, 2026))])
        self.assertEqual(k.total, 1, 'only declared units are counted as assignments')

    def test_earliest_cycles_is_smallest_carrying_cycle(self):
        u = units(A, B)
        k = Closure(u, [(A, 2024, B, 2024)], [(A, 2024, 1), (B, 2026, 2), (A, 2022, 3)], True)
        self.assertEqual(k.earliest_cycles(), {1: 2024, 2: 2026, 3: 2022})


class Witness(unittest.TestCase):
    def test_witness_path_is_a_real_seed_to_unit_walk(self):
        u = units(A, B, C)
        edges = [(A, 2024, B, 2024), (B, 2024, C, 2024)]
        k = Closure(u, edges, [(A, 2022, 5)], True)
        path = k.witness((C, 2026), 5)
        self.assertEqual(path[0], {'unit': (A, 2022), 'via': 'seed'})
        self.assertEqual(path[-1]['unit'], (C, 2026))
        # every hop is a stored edge or a forward-cycle link, joined end to start
        previous = path[0]['unit']
        for hop in path[1:]:
            self.assertEqual(hop['from'], previous)
            if hop['via'] == 'edge':
                self.assertIn((hop['from'][0], hop['from'][1], hop['unit'][0], hop['unit'][1]), edges)
            else:
                self.assertEqual((hop['via'], hop['unit'][0], hop['unit'][1] > hop['from'][1]), ('forward_cycle', hop['from'][0], True))
            previous = hop['unit']
        self.assertEqual([h['via'] for h in path], ['seed', 'forward_cycle', 'edge', 'edge', 'forward_cycle'])

    def test_witness_is_shortest_and_survives_a_cycle(self):
        u = [(c, 2026) for c in (A, B, C, D, E)]
        edges = [(A, 2026, B, 2026), (B, 2026, A, 2026), (B, 2026, C, 2026), (A, 2026, D, 2026), (D, 2026, C, 2026), (C, 2026, E, 2026)]
        k = Closure(u, edges, [(A, 2026, 1)], False)
        path = k.witness((E, 2026), 1)
        self.assertEqual((path[0]['unit'][0], path[-1]['unit'][0], len(path)), (A, E, 4), 'two three-hop routes tie; the walk is a shortest one')
        self.assertIn(path[1]['unit'][0], (B, D))
        self.assertEqual(k.witness((A, 2026), 1), [{'unit': (A, 2026), 'via': 'seed'}], 'a seeded unit is its own witness')

    def test_no_witness_means_not_observed_never_a_negative_claim(self):
        k = Closure([(A, 2026), (B, 2026)], [], [(A, 2026, 1)], False)
        self.assertIsNone(k.witness((B, 2026), 1))
        self.assertFalse(k.has((B, 2026), 1))
        self.assertEqual(oc.disclosure(), {'closure_basis': 'collected_edges_only', 'lower_bound': True})

    def test_ancestors_and_disclosure_count_what_is_still_uncollected(self):
        u = [(c, 2026) for c in (A, B, C, D)]
        k = Closure(u, [(A, 2026, B, 2026), (B, 2026, C, 2026)], [(A, 2026, 1)], False)
        self.assertEqual(k.ancestors((C, 2026)), {(A, 2026), (B, 2026)})
        info = oc.lineage_disclosure(k, (C, 2026), {(A, 2026): 1, (B, 2026): 2, (D, 2026): 4})
        self.assertEqual((info['closure_basis'], info['lower_bound']), ('collected_edges_only', True))
        self.assertEqual((info['ancestor_units'], info['noncomplete_endpoints_among_ancestors'], info['noncomplete_endpoints_ledger_wide']), (2, 3, 7))
        self.assertIn('never evidence', info['absence_means'])


class Oracle(unittest.TestCase):
    def test_random_graphs_with_cycles_match_a_flood_fill_oracle(self):
        rng = random.Random(73)
        for trial in range(60):
            committees = ['C%08d' % n for n in range(1, rng.randint(3, 14))]
            unit_set = {(c, y) for c in committees for y in oc.CYCLES}
            edges = [(rng.choice(committees), y, rng.choice(committees), rng.choice([y, y, y, 2022, 2026])) for y in rng.choices(oc.CYCLES, k=rng.randint(0, 40))]
            seeds = [(rng.choice(committees), rng.choice(oc.CYCLES), rng.randint(1, 9)) for _ in range(rng.randint(0, 12))]
            for later in (False, True):
                k = Closure(unit_set, edges, seeds, later)
                oracle = naive(unit_set, edges, seeds, later)
                for unit in unit_set:
                    expected = sorted(ref for ref, reached in oracle.items() if unit in reached)
                    self.assertEqual(k.origins(unit), expected, (trial, later, unit))
                self.assertEqual(k.total, sum(1 for reached in oracle.values() for unit in reached if unit in unit_set))
                for unit in list(unit_set)[:6]:
                    for ref in k.origins(unit)[:2]:
                        path = k.witness(unit, ref)
                        self.assertEqual(path[-1]['unit'], unit)
                        self.assertTrue(any(s[:2] == path[0]['unit'] and s[2] == ref for s in seeds))

    def test_long_chain_and_long_cycle_do_not_recurse(self):
        n = 6000
        names = ['C%08d' % i for i in range(n)]
        chain = Closure([(c, 2026) for c in names], [(names[i], 2026, names[i + 1], 2026) for i in range(n - 1)], [(names[0], 2026, 1)], False)
        self.assertEqual(chain.total, n)
        ring = Closure([(c, 2026) for c in names], [(names[i], 2026, names[(i + 1) % n], 2026) for i in range(n)], [(names[7], 2026, 1)], False)
        self.assertEqual((ring.total, len(ring.cyclic_components()), len(ring.cyclic_components()[0])), (n, 1, n))
        self.assertEqual(len(chain.witness((names[-1], 2026), 1)), n)

    def test_iter_bits_and_bitsets_scale_to_the_real_origin_count(self):
        refs = [1, 2, 63, 64, 65, 46521, 46522]
        value = sum(1 << r for r in refs)
        self.assertEqual(list(oc.iter_bits(value)), refs)
        self.assertEqual(list(oc.iter_bits(0)), [])
        k = Closure([(A, 2026)], [], [(A, 2026, r) for r in refs], False)
        self.assertEqual((k.origins((A, 2026)), k.total), (refs, len(refs)))


class Reading(unittest.TestCase):
    def ledger(self, policy='true'):
        db = sqlite3.connect(':memory:')
        db.executescript('''
            CREATE TABLE units(committee_id TEXT,cycle INTEGER);
            CREATE TABLE edges(committee_id TEXT,cycle INTEGER,kind TEXT,observation_id TEXT,destination_committee_id TEXT,destination_cycle INTEGER);
            CREATE TABLE origin_seeds(committee_id TEXT,cycle INTEGER,origin_ref INTEGER);
            CREATE TABLE origin_catalog(origin_ref INTEGER PRIMARY KEY,origin_id TEXT,base_metadata_json TEXT,first_reached_cycle INTEGER);
            CREATE TABLE ledger_policy(name TEXT,value TEXT);
            CREATE TABLE endpoints(committee_id TEXT,cycle INTEGER,kind TEXT,status TEXT);''')
        db.executemany('INSERT INTO units VALUES(?,?)', units(A, B))
        db.executemany('INSERT INTO edges VALUES(?,?,?,?,?,?)', [(A, 2022, 'B', 'o1', B, 2022), (A, 2022, 'B', 'o2', B, 2022)])
        db.execute('INSERT INTO origin_seeds VALUES(?,?,?)', (A, 2022, 1))
        db.execute('INSERT INTO origin_catalog VALUES(1,?,?,2022)', ('org-1', '{"company":"inert"}'))
        if policy:
            db.execute("INSERT INTO ledger_policy VALUES('later_cycle_context',?)", (policy,))
        db.executemany('INSERT INTO endpoints VALUES(?,?,?,?)', [(A, 2022, 'B', 'pending'), (A, 2022, 'A', 'pending'), (B, 2022, 'B', 'complete'), (B, 2022, 'F', 'unsupported')])
        return db

    def test_from_db_reads_the_recorded_policy_and_dedupes_parallel_edge_rows(self):
        k = oc.from_db(self.ledger('true'))
        self.assertEqual((k.later_context, k.total), (True, 6))
        self.assertEqual(oc.from_db(self.ledger('false')).total, 2)
        self.assertEqual(oc.from_db(self.ledger('true'), later_context=False).total, 2, 'an explicit argument overrides, e.g. to prove a policy mismatch')

    def test_missing_or_malformed_policy_is_an_error_never_a_default(self):
        for bad in (None, 'yes', ''):
            with self.assertRaisesRegex(ValueError, 'no valid later_cycle_context'):
                oc.from_db(self.ledger(bad))

    def test_catalog_and_incomplete_endpoint_counts(self):
        db = self.ledger()
        self.assertEqual(oc.read_catalog(db), {1: ('org-1', {'company': 'inert'}, 2022)})
        self.assertEqual(dict(oc.incomplete_by_unit(db)), {(A, 2022): 1, (B, 2022): 1}, 'family A is excluded; an unsupported family is a gap, a complete one is not')
        self.assertEqual(dict(oc.incomplete_by_unit(db, kinds=('B',))), {(A, 2022): 1})

    def test_metadata_reconstruction_matches_the_schema2_row_exactly(self):
        base = {'company': 'Zeta é', 'root_basis': 'x', 'n': [1, 2.5]}
        text = oc.metadata_json(base, 2022, 2024)
        self.assertEqual(text, '{"activity_cycle":2024,"company":"Zeta \\u00e9","first_reached_cycle":2022,"later_cycle_context":true,"n":[1,2.5],"root_basis":"x"}')
        self.assertEqual(oc.base_of(oc.metadata_for(base, 2022, 2024)), base)
        self.assertEqual(oc.metadata_for(base, 2024, 2024)['later_cycle_context'], False)


if __name__ == '__main__':
    unittest.main()
