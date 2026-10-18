"""D073 schema-3 controls: catalog+seeds instead of per-unit origins, derived closure, migration, and negative controls.

Inert only: temporary SQLite files and scripted fakes; no credentials, provider, network or product state. The differential tests run a FROZEN
copy of the schema-2 origin cascade (collect_committee_activity.py a7790c96, `_origin`/`seed`/`_store_edge`) as the oracle, so "the closure is lossless"
is checked against the code that produced the accepted batches, not against the new code's own idea of it.
"""
import hashlib
import json
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
import collect_committee_activity as driver
import origin_closure as oc
from test_collect_committee_activity import Transport, result, empty, C1, C2, C3, C4

CYCLES = (2022, 2024, 2026)
NAMES = ['C%08d' % n for n in range(1, 7)]


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def sql(path, statement, args=()):
    with closing(sqlite3.connect(path)) as db:
        db.execute(statement, args)
        db.commit()


def derived(frontier_or_db, later=None):
    """(committee, cycle, origin_id, metadata_json) rows the closure implies; same shape as the schema-2 `origins` table."""
    db = getattr(frontier_or_db, 'db', frontier_or_db)
    closure = oc.from_db(db, later)
    catalog = oc.read_catalog(db)
    return [(u[0], u[1], catalog[r][0], oc.metadata_json(catalog[r][1], catalog[r][2], u[1])) for u in sorted(closure.units) for r in closure.origins(u)]


class Schema2Reference:
    """Frozen schema-2 collector origin cascade on a plain SQLite file (test oracle only; never imported by product code)."""
    def __init__(self, path, later):
        self.path, self.later = Path(path), later
        self.db = sqlite3.connect(self.path, isolation_level=None)
        self.db.execute('PRAGMA synchronous=OFF')   # inert scratch oracle: durability is not under test
        self.db.execute('PRAGMA journal_mode=MEMORY')
        self.db.executescript(driver.SCHEMA2)
        self.db.execute('PRAGMA user_version=2')
        self.n = 0

    def _unit(self, committee, cycle):
        self.db.execute('INSERT OR IGNORE INTO units VALUES(?,?,?)', (committee, cycle, 'inert-created'))
        for kind in driver.KINDS:
            state = 'pending' if kind in driver.SUPPORTED else 'unsupported'
            self.db.execute('INSERT OR IGNORE INTO endpoints(committee_id,cycle,kind,status,last_error) VALUES(?,?,?,?,?)', (committee, cycle, kind, state, None if state == 'pending' else 'inert'))

    def _origin(self, committee, cycle, origin, metadata):   # verbatim cascade of a7790c96 :101-120
        todo = [(committee, cycle)]
        seen = set()
        while todo:
            key = todo.pop()
            if key in seen:
                continue
            seen.add(key)
            self._unit(*key)
            first = metadata.get('first_reached_cycle', cycle)
            local = {**metadata, 'first_reached_cycle': first, 'activity_cycle': key[1], 'later_cycle_context': key[1] > first}
            existing = self.db.execute('SELECT metadata_json FROM origins WHERE committee_id=? AND cycle=? AND origin_id=?', (*key, origin)).fetchone()
            changed = False
            if existing:
                prior = json.loads(existing[0])
                previous = prior.get('first_reached_cycle', key[1])
                if first < previous:
                    local = {**prior, 'first_reached_cycle': first, 'activity_cycle': key[1], 'later_cycle_context': key[1] > first}
                    self.db.execute('UPDATE origins SET metadata_json=? WHERE committee_id=? AND cycle=? AND origin_id=?', (driver.encoded(local), *key, origin))
                    changed = True
            else:
                self.db.execute('INSERT INTO origins VALUES(?,?,?,?)', (*key, origin, driver.encoded(local)))
                changed = True
            if changed:
                if self.later:
                    todo.extend((key[0], yr) for yr in CYCLES if yr > key[1])
                todo.extend((r[0], r[1]) for r in self.db.execute('SELECT DISTINCT destination_committee_id,destination_cycle FROM edges WHERE committee_id=? AND cycle=?', key) if r[1] >= key[1])

    def seed(self, rows):   # verbatim a7790c96 :121-131
        for row in rows:
            committee = row['committee_id']
            cycle = row['cycle']
            self._unit(committee, cycle)
            origins = row.get('origins') or row.get('industries') or [row.get('origin_id') or 'explicit-seed']
            for origin in origins:
                first = row.get('first_reached_cycle', (row.get('origin_metadata') or {}).get('first_reached_cycle', cycle))
                metadata = {**(row.get('origin_metadata') or {'source': 'explicit seed', 'scope': str(origin)}), 'first_reached_cycle': first}
                for activity_cycle in (yr for yr in CYCLES if yr >= cycle and (self.later or yr == cycle)):
                    self._origin(committee, activity_cycle, str(origin), {**metadata, 'activity_cycle': activity_cycle, 'later_cycle_context': activity_cycle > metadata['first_reached_cycle']})

    def edge(self, unit, destination):   # a7790c96 :158-163
        self.n += 1
        self._unit(destination, unit[1])
        self.db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?,?,?,?,?,?)', (*unit, 'B', 'occ%d' % self.n, 'sid%d' % self.n, destination, unit[1], '1.00', 'hash', 'verified_counterparty_field', '{}'))
        for origin in self.db.execute('SELECT origin_id,metadata_json FROM origins WHERE committee_id=? AND cycle=?', unit).fetchall():
            metadata = json.loads(origin[1])
            metadata.setdefault('first_reached_cycle', unit[1])
            for activity_cycle in (yr for yr in CYCLES if yr >= unit[1] and (self.later or yr == unit[1])):
                self._origin(destination, activity_cycle, origin[0], {**metadata, 'activity_cycle': activity_cycle, 'later_cycle_context': activity_cycle > metadata['first_reached_cycle']})

    def rows(self):
        return {tuple(r) for r in self.db.execute('SELECT committee_id,cycle,origin_id,metadata_json FROM origins')}

    def units(self):
        return {tuple(r) for r in self.db.execute('SELECT committee_id,cycle FROM units')}


def new_edge(frontier, unit, destination, n):
    with frontier.transaction():
        frontier._store_edge(unit, 'B', 'sid%d' % n, 'occ%d' % n, {'recipient_committee_id': destination}, '1.00', 'hash')


def seed_rows(rng, with_metadata, pinned):
    """One metadata mode per scenario: an origin keeps one base metadata (schema 3 stores one), whichever seed row names it.
    `pinned` maps each origin to its one cycle (the common real shape); None lets any origin be seeded at any cycle, which is
    usually a state the validator's R2 already refuses, so such scenarios are dropped at the first unclean step."""
    rows = []
    for _ in range(rng.randint(1, 3)):
        origins = rng.sample(['o1', 'o2', 'o3', 'o4'], rng.randint(1, 2))
        cycle = pinned[origins[0]] if pinned else rng.choice(CYCLES)
        row = {'committee_id': rng.choice(NAMES[:4]), 'cycle': cycle, 'origins': origins}
        if pinned and len({pinned[o] for o in origins}) > 1:
            row['origins'] = [o for o in origins if pinned[o] == cycle]
        if with_metadata:
            row['origin_metadata'] = {'company': 'inert', 'path': 'C:\\inert'}
        rows.append(row)
    return rows


def r2_clean(rows):
    """True when every origin has one base metadata and one first cycle equal to the earliest cycle carrying it: the state the validator's R2 accepts."""
    firsts, earliest, bases = {}, {}, {}
    for _c, cycle, origin, text in rows:
        metadata = json.loads(text)
        firsts.setdefault(origin, set()).add(metadata['first_reached_cycle'])
        earliest[origin] = min(earliest.get(origin, cycle), cycle)
        bases.setdefault(origin, set()).add(driver.encoded(oc.base_of(metadata)))
    return all(len(v) == 1 and next(iter(v)) == earliest[o] for o, v in firsts.items()) and all(len(v) == 1 for v in bases.values())


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='D073-inert-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.n = 0

    def frontier(self, name='frontier.sqlite', **options):
        value = driver.Frontier(self.root / name, **options)
        self.addCleanup(value.close)
        return value


class Contract(Base):
    def test_schema3_tables_have_no_origins_and_record_the_policy(self):
        f = self.frontier(include_later_cycle_context=True)
        tables = {r[0] for r in f.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({'origin_catalog', 'origin_seeds', 'ledger_policy'} <= tables)
        self.assertNotIn('origins', tables)
        self.assertEqual(f.db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual(f.db.execute("SELECT value FROM ledger_policy WHERE name='later_cycle_context'").fetchone()[0], 'true')
        self.assertEqual([r[1] for r in f.db.execute('PRAGMA table_info(origin_seeds)')], ['committee_id', 'cycle', 'origin_ref'])

    def test_schema2_ledger_is_refused_with_the_migration_pointer_and_left_untouched(self):
        path = self.root / 'old.sqlite'
        Schema2Reference(path, False).db.close()
        before = file_sha(path)
        with self.assertRaisesRegex(ValueError, r'schema 2.*--migrate-to-schema3'):
            driver.Frontier(path)
        self.assertEqual(file_sha(path), before, 'refusing must not rewrite, migrate or add a table')
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")} & {'origin_catalog', 'origin_seeds', 'ledger_policy'}, set())

    def test_mixed_policy_is_refused_both_ways_and_a_missing_or_bad_record_never_defaults(self):
        for created in (True, False):
            name = 'p%s.sqlite' % created
            self.frontier(name, include_later_cycle_context=created).close()
            with self.assertRaisesRegex(ValueError, 'differs from this invocation'):
                driver.Frontier(self.root / name, include_later_cycle_context=not created)
            driver.Frontier(self.root / name, include_later_cycle_context=created).close()
        for edit in ("UPDATE ledger_policy SET value='maybe'", 'DELETE FROM ledger_policy'):
            name = 'bad%d.sqlite' % self.n
            self.n += 1
            self.frontier(name).close()
            sql(self.root / name, edit)
            with self.assertRaisesRegex(ValueError, 'recorded later-cycle context policy'):
                driver.Frontier(self.root / name)

    def test_unit_layout_drift_is_still_refused(self):
        self.frontier('drift.sqlite').close()
        sql(self.root / 'drift.sqlite', 'DROP TABLE origin_seeds')
        sql(self.root / 'drift.sqlite', 'CREATE TABLE origin_seeds(committee_id TEXT,cycle INTEGER,origin_ref INTEGER,extra TEXT)')
        with self.assertRaisesRegex(ValueError, 'differs from reviewed contract'):
            driver.Frontier(self.root / 'drift.sqlite')

    def test_origin_encoding_is_byte_identical_to_the_collector_encoding(self):
        base = {'company': 'Z\u00e9ta', 'n': [1, 2.5], 'nested': {'b': 1, 'a': None}}
        for first, cycle in ((2022, 2022), (2022, 2026), (2024, 2024)):
            self.assertEqual(oc.metadata_json(base, first, cycle), driver.encoded({**base, 'first_reached_cycle': first, 'activity_cycle': cycle, 'later_cycle_context': cycle > first}))


class Collector(Base):
    def run_pair(self, later):
        f = self.frontier(include_later_cycle_context=later)
        f.seed([{'committee_id': C1, 'cycle': 2022, 'origins': ['a', 'b'], 'origin_metadata': {'company': 'inert'}}])
        new_edge(f, (C1, 2022), C2, 1)
        return f

    def test_forward_units_are_admitted_for_each_new_destination_so_the_unit_set_is_unchanged(self):
        on = self.run_pair(True)
        self.assertEqual({tuple(r) for r in on.db.execute('SELECT committee_id,cycle FROM units')}, {(c, y) for c in (C1, C2) for y in CYCLES})
        off = self.frontier('off.sqlite')
        off.seed([{'committee_id': C1, 'cycle': 2022, 'origins': ['a']}])
        new_edge(off, (C1, 2022), C2, 1)
        self.assertEqual({tuple(r) for r in off.db.execute('SELECT committee_id,cycle FROM units')}, {(C1, 2022), (C2, 2022)})
        # every unit has its eleven endpoint rows: forward units are collectable, not just listed
        self.assertEqual(on.db.execute('SELECT COUNT(*) FROM endpoints').fetchone()[0], 6 * len(driver.KINDS))

    def test_edges_write_no_origin_rows_only_the_edge_and_forward_units(self):
        f = self.frontier(include_later_cycle_context=True)
        f.seed([{'committee_id': C1, 'cycle': 2022, 'origins': ['a', 'b', 'c']}])
        seeds, catalog = f.report()['origin_seed_assignments'], f.report()['origin_catalog_entries']
        for index, dest in enumerate((C2, C3, C4, C2), 1):
            new_edge(f, (C1, 2022), dest, index)
        report = f.report()
        self.assertEqual((report['origin_seed_assignments'], report['origin_catalog_entries']), (seeds, catalog))
        self.assertEqual((seeds, catalog), (3, 3))
        self.assertEqual(report['origin_unit_assignments'], 3 * 12, 'four committees x three cycles x three origins, derived not stored')
        self.assertEqual(report['edges'], 4)

    def test_catalog_first_only_falls_and_the_base_is_frozen(self):
        f = self.frontier(include_later_cycle_context=True)
        f.seed([{'committee_id': C1, 'cycle': 2024, 'origins': ['x'], 'origin_metadata': {'company': 'inert'}}])
        row = lambda: tuple(f.db.execute("SELECT origin_ref,base_metadata_json,first_reached_cycle FROM origin_catalog WHERE origin_id='x'").fetchone())
        self.assertEqual(row()[2], 2024)
        f.seed([{'committee_id': C2, 'cycle': 2022, 'origins': ['x'], 'origin_metadata': {'company': 'inert'}}])
        self.assertEqual(row()[2], 2022)
        f.seed([{'committee_id': C3, 'cycle': 2026, 'origins': ['x'], 'origin_metadata': {'company': 'inert'}}])
        self.assertEqual(row()[2], 2022, 'a later seed can never raise first_reached_cycle')
        before = (row(), f.db.execute('SELECT COUNT(*) FROM origin_seeds').fetchone()[0], f.db.execute('SELECT COUNT(*) FROM units').fetchone()[0])
        with self.assertRaisesRegex(ValueError, 'conflicts with its catalog entry'):
            f.seed([{'committee_id': C4, 'cycle': 2022, 'origins': ['y']}, {'committee_id': C4, 'cycle': 2022, 'origins': ['x'], 'origin_metadata': {'company': 'other'}}])
        self.assertEqual((row(), f.db.execute('SELECT COUNT(*) FROM origin_seeds').fetchone()[0], f.db.execute('SELECT COUNT(*) FROM units').fetchone()[0]), before, 'the refused batch rolled back whole')

    def test_reseeding_is_idempotent_and_order_independent(self):
        rows = [{'committee_id': C1, 'cycle': y, 'origins': ['k']} for y in CYCLES]
        first, second = self.frontier('one.sqlite', include_later_cycle_context=True), self.frontier('two.sqlite', include_later_cycle_context=True)
        first.seed(rows)
        first.seed(rows)
        second.seed(list(reversed(rows)))
        dump = lambda f: ([tuple(r) for r in f.db.execute('SELECT * FROM origin_seeds ORDER BY 1,2,3')], [tuple(r) for r in f.db.execute('SELECT origin_id,base_metadata_json,first_reached_cycle FROM origin_catalog')])
        self.assertEqual(dump(first), dump(second))
        self.assertEqual(len(dump(first)[0]), 3)

    def test_receipt_replaces_the_origins_count_and_discloses_the_lower_bound(self):
        f = self.frontier(include_later_cycle_context=True)
        f.seed([{'committee_id': C1, 'cycle': 2026, 'origins': ['a', 'b']}])

        def handler(endpoint, query):
            if endpoint.endswith('schedule_b/') and query['committee_id'] == C1:
                return result([{'sub_id': '1', 'recipient_committee_id': C2, 'disbursement_amount': 1}])
            return result()
        report = f.run(Transport(self.root / 'raw', handler))
        self.assertNotIn('origins', report)
        self.assertEqual((report['origin_seed_assignments'], report['origin_catalog_entries'], report['origin_unit_assignments']), (2, 2, 4))
        self.assertEqual((report['closure_basis'], report['lower_bound'], report['ledger_schema_version']), ('collected_edges_only', True, 3))
        self.assertIn('lower bound', report['origin_unit_assignments_note'])
        for name in ('units', 'pages', 'observations', 'edges', 'candidate_beneficiary_or_unverified_references', 'counterparty_identity_conflicts', 'leaf_disclosure_boundaries'):
            self.assertIsInstance(report[name], int, name)
        self.assertFalse(report['exhaustive_disclosed_spending_complete'])

    def test_lineage_witness_path_comes_from_collected_edges_only(self):
        f = self.frontier(include_later_cycle_context=True)
        f.seed([{'committee_id': C1, 'cycle': 2022, 'origins': ['a']}])
        new_edge(f, (C1, 2024), C2, 1)
        closure = f.closure()
        ref = f.db.execute("SELECT origin_ref FROM origin_catalog WHERE origin_id='a'").fetchone()[0]
        self.assertEqual([h['via'] for h in closure.witness((C2, 2026), ref)], ['seed', 'forward_cycle', 'edge', 'forward_cycle'])
        self.assertIsNone(closure.witness((C3, 2026), ref), 'an unknown unit has no witness')
        self.assertFalse(closure.has((C2, 2022), ref), 'not observed at 2022: the edge was collected for 2024 only')


class Differential(Base):
    def test_closure_reproduces_the_frozen_schema2_cascade_row_for_row(self):
        compared = forward = cyclic = 0
        for trial in range(80):
            rng = random.Random(1000 + trial)
            later = rng.random() < .6
            ref = Schema2Reference(self.root / ('ref%d.sqlite' % trial), later)
            new = self.frontier('new%d.sqlite' % trial, include_later_cycle_context=later)
            with_metadata = rng.random() < .5
            pinned = {o: rng.choice(CYCLES) for o in ('o1', 'o2', 'o3', 'o4')} if rng.random() < .7 else None
            new.db.execute('PRAGMA synchronous=OFF')   # inert scratch ledger: durability is not under test here
            try:
                for step in range(rng.randint(2, 7)):
                    if rng.random() < .5 or not ref.units():
                        rows = seed_rows(rng, with_metadata, pinned)
                        ref.seed(rows)
                        new.seed(rows)
                    else:
                        unit = rng.choice(sorted(ref.units()))
                        destination = rng.choice(NAMES)
                        ref.edge(unit, destination)
                        new_edge(new, unit, destination, ref.n)
                    if not r2_clean(ref.rows()):
                        break
                    self.assertEqual(ref.rows(), set(derived(new)), (trial, step))
                    self.assertEqual(ref.units(), {tuple(r) for r in new.db.execute('SELECT committee_id,cycle FROM units')}, 'the unit set must be identical')
                else:
                    compared += 1
                    forward += later
                    cyclic += bool(new.closure().cyclic_components())
            finally:
                ref.db.close()
        self.assertGreaterEqual(compared, 25)
        self.assertGreaterEqual(forward, 10)
        self.assertGreaterEqual(cyclic, 1, 'the sweep must include payment cycles')


class Migration(Base):
    def fixture(self, name='old', later=True, cyclic=False):
        """A populated schema-2 ledger (frozen cascade) with multi-hop, forward cycles and optionally a payment cycle, plus other-table rows."""
        folder = self.root / name
        folder.mkdir()
        ref = Schema2Reference(folder / 'frontier.sqlite', later)
        rows = [{'committee_id': C1, 'cycle': 2022, 'origins': ['a', 'b'], 'origin_metadata': {'company': 'inert \u00e9', 'path': 'C:\\inert'}}, {'committee_id': C3, 'cycle': 2024, 'origins': ['c']}]
        ref.seed(rows)
        ref.edge((C1, 2022), C2)
        ref.edge((C2, 2022), C4)
        ref.edge((C3, 2024), C4)
        if cyclic:
            ref.edge((C2, 2022), C1)
        ref.db.execute("INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?,?,?)", (C1, 2022, 'B', 1, '{}', '{}', '{}', 'inert/archive.body.bin', 'ab' * 32, 10, 1))
        ref.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (C1, 2022, 'B', 'occ', 1, 0, 'sid', '{"sub_id":1}', 'cd' * 32, '1.00', 'disbursement_amount', 'valid_decimal'))
        ref.db.execute("INSERT INTO failures(committee_id,cycle,kind,at,error_type,detail) VALUES(?,?,?,?,?,?)", (C1, 2022, 'B', 'inert', 'OSError', 'inert'))
        ref.db.execute("INSERT INTO candidate_references VALUES('H0AA00001',2022,?, 'B','occ','sid','candidate_id','associated_candidate_payment_role_unverified')", (C1,))
        ref.db.close()
        seeds = folder / 'seeds.json'
        seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': rows}))
        return folder / 'frontier.sqlite', seeds, ref

    def test_derived_seed_migration_is_lossless_new_file_only_and_leaves_the_source_alone(self):
        source, _seeds, _ref = self.fixture()
        before = file_sha(source)
        out = self.root / 'new'
        out.mkdir()
        proof = driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True)
        self.assertEqual(file_sha(source), before)
        self.assertEqual(sorted(p.name for p in out.iterdir()), ['MIGRATION.json', 'frontier.sqlite'])
        self.assertEqual(proof['result'], 'lossless')
        self.assertEqual(proof['equality']['mismatches_total'], 0)
        self.assertEqual(proof['equality']['metadata_mismatches'], 0)
        self.assertEqual(proof['equality']['metadata_byte_equal_rows'], proof['counts']['origins_rows_stored_schema2'])
        self.assertEqual(proof['counts']['closure_rows_derived'], proof['counts']['origins_rows_stored_schema2'])
        self.assertEqual(proof['seed_basis'], 'derived_unexplained_rows')
        self.assertEqual(proof['counts']['origin_seed_rows'], 3, 'a, b at C1/2022 and c at C3/2024; everything else is explained by an edge or an earlier cycle')
        with closing(sqlite3.connect(source)) as db:
            stored = {tuple(r) for r in db.execute('SELECT * FROM origins')}
        migrated = driver.Frontier(out / 'frontier.sqlite', include_later_cycle_context=True)
        self.addCleanup(migrated.close)
        self.assertEqual(set(derived(migrated)), stored)
        for table in driver.COPIED_TABLES:
            self.assertTrue(proof['copied_tables_row_for_row_identical'][table]['identical'], table)
        self.assertEqual(json.loads((out / 'MIGRATION.json').read_text())['destination']['sha256'], file_sha(out / 'frontier.sqlite'))
        self.assertEqual(json.loads((out / 'MIGRATION.json').read_text())['source']['sha256'], before)
        with self.assertRaisesRegex(ValueError, 'differs from this invocation'):
            driver.Frontier(out / 'frontier.sqlite', include_later_cycle_context=False)

    def test_seed_file_migration_binds_the_seed_bytes_and_equals_the_collectors_own_seed(self):
        source, seeds, _ref = self.fixture()
        out = self.root / 'seeded'
        out.mkdir()
        proof = driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True, seed_file=seeds)
        self.assertEqual((proof['seed_basis'], proof['seed_file']['sha256'], proof['seed_cross_check']['seed_file_equals_derived']), ('captured_seed_file', file_sha(seeds), True))
        fresh = self.frontier('fresh.sqlite', include_later_cycle_context=True)
        fresh.seed(json.loads(seeds.read_text())['seed_units'])
        dump = lambda db: ([tuple(r) for r in db.execute('SELECT o.origin_id,o.base_metadata_json,o.first_reached_cycle,s.committee_id,s.cycle FROM origin_seeds s JOIN origin_catalog o USING(origin_ref) ORDER BY 1,4,5')])
        with closing(sqlite3.connect(out / 'frontier.sqlite')) as migrated:
            self.assertEqual(dump(migrated), dump(fresh.db), 'migration seeds are exactly what seed() stores on a fresh ledger')
            self.assertEqual(sorted(r[0] for r in migrated.execute('SELECT origin_ref FROM origin_catalog')), [1, 2, 3])

    def test_payment_cycle_needs_the_seed_file_and_derived_mode_refuses_rather_than_guess(self):
        source, seeds, _ref = self.fixture('cycle', cyclic=True)
        out = self.root / 'cyc'
        out.mkdir()
        with self.assertRaisesRegex(ValueError, 'Migration refused'):
            driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True)
        self.assertEqual(list(out.iterdir()), [], 'a refusal leaves no partial, ledger or proof')
        proof = driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True, seed_file=seeds)
        self.assertEqual((proof['result'], proof['counts']['scc_groups_with_cycles']), ('lossless', 1))

    def test_every_tamper_of_the_source_is_refused_without_leaving_files(self):
        tampers = {
            'metadata field': lambda db: db.execute("UPDATE origins SET metadata_json=replace(metadata_json,'inert','forged') WHERE rowid=(SELECT MIN(rowid) FROM origins)"),
            'first cycle': lambda db: db.execute("UPDATE origins SET metadata_json=replace(metadata_json,'\"first_reached_cycle\":2022','\"first_reached_cycle\":2024') WHERE committee_id=? AND cycle=2024 AND origin_id='a'", (C2,)),
            'dropped row': lambda db: db.execute("DELETE FROM origins WHERE committee_id=? AND cycle=2026 AND origin_id='a'", (C4,)),
            'row for an unknown unit': lambda db: db.execute("INSERT INTO origins VALUES('C00000009',2022,'a','{}')"),
        }
        for label, tamper in tampers.items():
            with self.subTest(label):
                source, seeds, _ref = self.fixture('t%d' % self.n)
                self.n += 1
                with closing(sqlite3.connect(source)) as db:
                    tamper(db)
                    db.commit()
                out = self.root / ('o%d' % self.n)
                out.mkdir()
                with self.assertRaises(ValueError):
                    driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True, seed_file=seeds)
                self.assertEqual(list(out.iterdir()), [])

    def test_an_extra_stored_row_is_refused_with_the_seed_file_but_is_only_a_seed_without_it(self):
        source, seeds, _ref = self.fixture()
        sql(source, "INSERT INTO origins VALUES(?,2026,'zz',?)", (C2, driver.encoded({'first_reached_cycle': 2026, 'activity_cycle': 2026, 'later_cycle_context': False, 'source': 'explicit seed'})))
        out = self.root / 'x'
        out.mkdir()
        with self.assertRaises(ValueError):
            driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True, seed_file=seeds)
        proof = driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True)
        self.assertEqual(proof['counts']['origin_seed_rows'], 4)

    def test_policy_guards_stated_flag_forward_units_snapshot_binding_and_new_files_only(self):
        source, seeds, _ref = self.fixture()
        out = self.root / 'g'
        out.mkdir()
        target = out / 'frontier.sqlite'
        with self.assertRaisesRegex(ValueError, 'explicitly'):
            driver.migrate_to_schema3(source, target, later_cycle_context=None)
        sql(source, "DELETE FROM units WHERE committee_id=? AND cycle=2026", (C4,))   # a ledger that lacks a later-cycle unit cannot be a policy-true ledger
        with self.assertRaisesRegex(ValueError, 'lacks a later-cycle unit'):
            driver.migrate_to_schema3(source, target, later_cycle_context=True)
        source, seeds, _ref = self.fixture('b')
        proof = self.root / 'SNAPSHOT.json'
        proof.write_text(json.dumps({'snapshot_sha256': file_sha(source), 'later_cycle_context_explicitly_enabled': True}))
        with self.assertRaisesRegex(ValueError, 'Snapshot proof does not bind'):
            driver.migrate_to_schema3(source, target, later_cycle_context=False, snapshot_proof=proof)
        proof.write_text(json.dumps({'snapshot_sha256': '00' * 32, 'later_cycle_context_explicitly_enabled': True}))
        with self.assertRaisesRegex(ValueError, 'Snapshot proof does not bind'):
            driver.migrate_to_schema3(source, target, later_cycle_context=True, snapshot_proof=proof)
        proof.write_text(json.dumps({'snapshot_sha256': file_sha(source), 'later_cycle_context_explicitly_enabled': True}))
        done = driver.migrate_to_schema3(source, target, later_cycle_context=True, snapshot_proof=proof)
        self.assertEqual(done['snapshot_proof']['sha256'], file_sha(proof))
        before = file_sha(target)
        with self.assertRaises(FileExistsError):
            driver.migrate_to_schema3(source, target, later_cycle_context=True)
        with self.assertRaises(FileExistsError):
            driver.migrate_to_schema3(source, out / 'second.sqlite', later_cycle_context=True)   # MIGRATION.json already there
        self.assertEqual(file_sha(target), before)

    def test_sources_that_are_not_quiescent_schema2_or_that_will_not_fit_are_refused(self):
        source, _seeds, _ref = self.fixture()
        out = self.root / 'q'
        out.mkdir()
        Path(str(source) + '-journal').write_bytes(b'hot')
        with self.assertRaisesRegex(ValueError, 'Quiescent source'):
            driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True)
        Path(str(source) + '-journal').unlink()
        with mock.patch.object(driver.shutil, 'disk_usage', return_value=mock.Mock(free=1000)):
            with self.assertRaisesRegex(OSError, 'free space'):
                driver.migrate_to_schema3(source, out / 'frontier.sqlite', later_cycle_context=True)
        three = self.root / 'three.sqlite'
        driver.Frontier(three).close()
        with self.assertRaisesRegex(ValueError, 'schema-2'):
            driver.migrate_to_schema3(three, out / 'frontier.sqlite', later_cycle_context=False)
        self.assertEqual(list(out.iterdir()), [])

    def test_command_line_entry_migrates_and_prints_the_result(self):
        source, seeds, _ref = self.fixture()
        out = self.root / 'cli'
        out.mkdir()
        done = subprocess.run([sys.executable, '-B', str(TOOLS / 'collect_committee_activity.py'), '--migrate-to-schema3', str(source), str(out / 'frontier.sqlite'),
                               '--later-cycle-context', 'true', '--seed-file', str(seeds)], capture_output=True, cwd=TOOLS, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr.decode())
        self.assertEqual(json.loads(done.stdout)['result'], 'lossless')
        missing = subprocess.run([sys.executable, '-B', str(TOOLS / 'collect_committee_activity.py'), '--migrate-to-schema3', str(source), str(out / 'x.sqlite')], capture_output=True, cwd=TOOLS, timeout=120)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn('--later-cycle-context', missing.stderr.decode())


if __name__ == '__main__':
    unittest.main()
