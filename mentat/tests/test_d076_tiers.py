"""Inert controls for the D076 bulk activity index in the downstream collector (--bulk-activity-index): strict loader, persisted endpoint tiers, request ordering, and the
invariants (a tier is an ordering hint ONLY: it never completes, skips, zeroes or creates anything, and without the flags nothing changes).

No credentials, provider, network or Mentat state: every ledger, index and archive is in a temporary directory, credential loading and sockets are patched to fail, and the
transport is a scripted fake. The no-index parity control compares against the production collector bytes at the base commit when the overlay provides them
(`collect_committee_activity.base.py` beside the staged collector) and skips otherwise.
"""
from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock

import test_campaign_spending as cs
import test_collect_committee_activity_concurrency as conc
import test_collect_committee_activity_endpoints as ep

driver, accounted = ep.driver, ep.accounted
C1, C2, C3, C4, C5, C9 = cs.C1, cs.C2, cs.C3, cs.C4, cs.C5, cs.C9
KINDS = ('B', 'E_regular', 'E_notice')
SOURCES = [{'id': 'oppexp2024', 'kind': 'oppexp', 'cycle': 2024, 'sha256': 'a' * 64, 'vintage_asof': '2026-10-12T17:36:09Z'},
           {'id': 'pas22024', 'kind': 'pas2', 'cycle': 2024, 'sha256': 'b' * 64, 'vintage_asof': '2026-09-06T15:55:17Z'},
           {'id': 'ie2024', 'kind': 'ie', 'cycle': 2024, 'sha256': 'c' * 64, 'vintage_asof': '2026-10-17T10:14:43Z'}]
SOURCE_FOR = {'B': 'oppexp2024', 'E_regular': 'pas22024', 'E_notice': 'ie2024'}
TABLES = ('units', 'origin_catalog', 'origin_seeds', 'ledger_policy', 'endpoints', 'pages', 'observations', 'edges', 'counterparty_conflicts', 'candidate_resolutions',
          'candidate_references', 'leaf_coverage', 'failures')


def document(rows=(), complete=None, sources=None):
    """rows: [(committee, cycle, kind, count)]; complete: {(kind, cycle): bool}, default every kind and cycle complete."""
    complete = complete or {}
    coverage = {kind: {str(cycle): {'complete': complete.get((kind, cycle), True), 'ingested': [SOURCE_FOR[kind]], 'missing_source_kinds': []} for cycle in (2022, 2024, 2026)}
                for kind in KINDS}
    return {'schema_version': 1, 'kind': 'mentat-bulk-activity-index',
            'provenance': {'statement': 'inert', 'built_utc': '2026-10-17T00:00:00Z', 'builder': {'file': 'inert', 'sha256': 'd' * 64},
                           'vintage': {'oldest_asof': '2026-09-06T15:55:17Z', 'newest_asof': '2026-10-17T10:14:43Z'}, 'sources': sources or SOURCES},
            'coverage': coverage, 'rows': [[c, y, k, n, {SOURCE_FOR[k]: n}] for c, y, k, n in rows], 'stats': {}}


def endpoint_of(call):
    endpoint, query = call
    if endpoint == ep.SCHEDULE_B:
        return query['committee_id'], 'B'
    return query['committee_id'], 'E_notice' if str(query.get('is_notice')) == 'true' else 'E_regular'


def sequence(transport):
    return [endpoint_of(call) for call in transport.calls]


class Base(cs.Base):
    def write_index(self, doc, name='index.json', raw=None):
        path = self.root / name
        path.write_bytes(raw if raw is not None else json.dumps(doc, sort_keys=True, separators=(',', ':')).encode())
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def index(self, rows=(), complete=None, name='index.json'):
        path, sha = self.write_index(document(rows, complete), name)
        return driver.load_bulk_activity_index(path, sha)

    def tiers(self, frontier):
        return {(c, y, k): (t, n) for c, y, k, t, n in frontier.db.execute('SELECT committee_id,cycle,kind,tier,bulk_rows FROM endpoint_bulk_tier')}

    def base_module(self):
        path = Path(driver.__file__).resolve().with_name('collect_committee_activity.base.py')
        if not path.exists():
            self.skipTest('the overlay supplies the production collector bytes as collect_committee_activity.base.py')
        spec = importlib.util.spec_from_file_location('collect_committee_activity_base', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def snapshot(self, frontier):
        return {t: sorted(tuple(r) for r in frontier.db.execute('SELECT * FROM ' + t)) for t in TABLES}


# ---------------------------------------------------------------- strict loader and the tier rule
class Loader(Base):
    def refused(self, text, **kw):
        raw = kw.pop('raw', None)
        path, sha = self.write_index(kw.pop('doc', None), raw=raw)
        with self.assertRaises(ValueError) as caught:
            driver.load_bulk_activity_index(path, kw.pop('expect', sha))
        self.assertIn(text, str(caught.exception))

    def test_a_valid_index_loads_and_its_receipt_names_the_file_the_sources_and_the_vintage(self):
        path, sha = self.write_index(document([(C1, 2024, 'B', 5)]))
        loaded = driver.load_bulk_activity_index(path, sha)
        receipt = loaded.receipt
        self.assertEqual((receipt['sha256'], receipt['bytes'], receipt['rows'], receipt['schema_version']), (sha, path.stat().st_size, 1, 1))
        self.assertEqual(receipt['vintage'], {'oldest_asof': '2026-09-06T15:55:17Z', 'newest_asof': '2026-10-17T10:14:43Z'})
        self.assertEqual([s['id'] for s in receipt['sources']], ['oppexp2024', 'pas22024', 'ie2024'])
        self.assertEqual(receipt['builder_sha256'], 'd' * 64)
        self.assertTrue(receipt['coverage_complete']['B']['2024'])

    def test_a_count_just_below_the_limit_still_loads(self):
        doc = document()
        doc['rows'] = [[C1, 2024, 'B', 2 ** 62 - 1, {'oppexp2024': 2 ** 62 - 1}]]
        path, sha = self.write_index(doc)
        self.assertEqual(driver.load_bulk_activity_index(path, sha).tier(C1, 2024, 'B'), (1, 2 ** 62 - 1))

    def test_the_tier_rule_is_one_observed_two_unknown_three_covered_and_none_observed(self):
        loaded = self.index([(C1, 2024, 'B', 5)], complete={('E_notice', 2024): False, ('E_regular', 2026): False})
        self.assertEqual(loaded.tier(C1, 2024, 'B'), (1, 5))
        self.assertEqual(loaded.tier(C2, 2024, 'B'), (3, None), 'covered and none observed')
        self.assertEqual(loaded.tier(C1, 2024, 'E_notice'), (2, None), 'the index does not cover E_notice 2024: unknown, not "none"')
        self.assertEqual(loaded.tier(C1, 2026, 'E_regular'), (2, None))
        self.assertEqual(loaded.tier(C1, 2022, 'E_regular'), (3, None))

    def test_positive_evidence_stands_even_when_coverage_is_incomplete(self):
        loaded = self.index([(C1, 2024, 'E_notice', 2)], complete={('E_notice', 2024): False})
        self.assertEqual(loaded.tier(C1, 2024, 'E_notice'), (1, 2))

    def test_the_pin_is_checked_before_a_byte_is_trusted(self):
        path, sha = self.write_index(document([(C1, 2024, 'B', 5)]))
        for bad in ('0' * 64, sha[:-1], sha.upper(), '', None):
            with self.subTest(pin=bad), self.assertRaises(ValueError):
                driver.load_bulk_activity_index(path, bad)

    def test_each_defect_refuses_the_whole_index(self):
        good = lambda: document([(C1, 2024, 'B', 5), (C2, 2024, 'E_notice', 3)])
        row = lambda *cells: dict(doc={**good(), 'rows': [list(cells)]})
        mutations = [
            ('not strict JSON', 'not strict JSON', dict(raw=b'{"schema_version":1,')),
            ('duplicate key', 'Duplicate key', dict(raw=b'{"a":1,"a":2}')),
            ('schema version', 'Not a schema-1', dict(doc={**good(), 'schema_version': 2})),
            ('kind', 'Not a schema-1', dict(doc={**good(), 'kind': 'other'})),
            ('no sources', 'no source provenance', dict(doc={**good(), 'provenance': {'sources': []}})),
            ('source entry', 'source entry', dict(doc={**good(), 'provenance': {**good()['provenance'], 'sources': [{'id': 'x'}]}})),
            ('coverage cell missing', 'coverage for B 2022', dict(doc={**good(), 'coverage': {**good()['coverage'], 'B': {'2024': good()['coverage']['B']['2024']}}})),
            ('rows not a list', 'no rows list', dict(doc={**good(), 'rows': {}})),
            ('row too short', 'not [committee', row(C1, 2024, 'B', 5)),
            ('count zero', 'is malformed', row(C1, 2024, 'B', 0, {'oppexp2024': 0})),
            ('kind outside B/E', 'is malformed', row(C1, 2024, 'F', 1, {'oppexp2024': 1})),
            ('bad committee id', 'is malformed', row('C1', 2024, 'B', 1, {'oppexp2024': 1})),
            ('cycle outside 2022/24/26', 'is malformed', row(C1, 2020, 'B', 1, {'oppexp2024': 1})),
            ('per-source sum differs', 'is malformed', row(C1, 2024, 'B', 2, {'oppexp2024': 1})),
            ('unknown source id', 'is malformed', row(C1, 2024, 'B', 1, {'nope': 1})),
            ('bool count', 'is malformed', row(C1, 2024, 'B', True, {'oppexp2024': True})),
            ('zero count with no sources', 'is malformed', row(C1, 2024, 'B', 0, {})),
            ('count at 2**62', 'is malformed', row(C1, 2024, 'B', 2 ** 62, {'oppexp2024': 2 ** 62})),
            ('count beyond SQLite integers', 'is malformed', row(C1, 2024, 'B', 10 ** 30, {'oppexp2024': 10 ** 30})),
            ('count at 2**62 made of two sources that are each below it', 'is malformed', row(C1, 2024, 'B', 2 ** 62, {'oppexp2024': 2 ** 62 - 1, 'pas22024': 1})),
            ('provenance build time is not short text', 'not short text', dict(doc={**good(), 'provenance': {**good()['provenance'], 'built_utc': 'x' * 65}})),
            ('provenance build time is a number', 'not short text', dict(doc={**good(), 'provenance': {**good()['provenance'], 'built_utc': 20261017}})),
            ('builder hash is a list', 'not short text', dict(doc={**good(), 'provenance': {**good()['provenance'], 'builder': {'sha256': ['x']}}})),
            ('vintage value is an object', 'not short text', dict(doc={**good(), 'provenance': {**good()['provenance'], 'vintage': {'oldest_asof': {'a': 1}, 'newest_asof': 'x'}}})),
            ('source kind is a number', 'not short text', dict(doc={**good(), 'provenance': {**good()['provenance'], 'sources': [{**SOURCES[0], 'kind': 7}, *SOURCES[1:]]}})),
            ('source id is long', 'not short text', dict(doc={**good(), 'provenance': {**good()['provenance'], 'sources': [*SOURCES, {**SOURCES[0], 'id': 'i' * 65}]}})),
            ('rows is a nested object', 'is not [committee', dict(doc={**good(), 'rows': [{}]})),
            ('coverage is a list', 'coverage for B 2022', dict(doc={**good(), 'coverage': []})),
            ('JSON nested far too deep', 'malformed', dict(raw=b'[' * 200000)),
            ('repeated row', 'repeats', dict(doc={**good(), 'rows': [[C1, 2024, 'B', 1, {'oppexp2024': 1}], [C1, 2024, 'B', 1, {'oppexp2024': 1}]]})),
        ]
        for label, fragment, kw in mutations:
            with self.subTest(defect=label):
                self.refused(fragment, **dict(kw))

    def test_a_bad_pinned_index_is_a_clean_refusal_never_a_traceback_and_nothing_is_written(self):
        huge = document([(C1, 2024, 'B', 1)])
        huge['rows'] = [[C1, 2024, 'B', 10 ** 30, {'oppexp2024': 10 ** 30}]]
        path, sha = self.write_index(huge)
        seeds = self.root / 'seeds.json'
        seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': C1, 'cycle': 2024}]}))
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('no transport')):
            with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.root / 's'), '--seeds', str(seeds), '--max-requests', '1', '--live', '--init-new-ledger',
                                                '--bulk-activity-index', str(path), '--expect-bulk-index-sha256', sha]):
                with self.assertRaises(SystemExit) as caught:
                    driver.main()
        self.assertIn('Bulk activity index refused', str(caught.exception))
        self.assertIn('Nothing was written', str(caught.exception))
        self.assertFalse((self.root / 's').exists())

    def test_the_real_builders_output_is_accepted_by_the_independent_loader(self):
        sys.path.insert(0, str(Path(driver.__file__).resolve().parent))
        import build_bulk_activity_index as bx
        files = self.root / 'bulk'
        files.mkdir()
        pas2 = b'\n'.join(['|'.join([C1, 'N', 'Q1', 'G2024', '2024', kind, 'ORG', 'N', 'C', 'ST', '0', '', '', '01012024', '1', '', '', 'T', '1', '', '', '9']).encode() for kind in ('24K', '24E')]) + b'\n'
        (files / 'pas2.txt').write_bytes(pas2)
        entry = {'id': 'pas22024', 'kind': 'pas2', 'cycle': 2024, 'path': str(files / 'pas2.txt'), 'sha256': hashlib.sha256(pas2).hexdigest(), 'rows': 2, 'vintage_asof': '2026-09-06T00:00:00Z'}
        built = bx.build([entry], '2026-10-17T00:00:00Z')
        path, sha = self.write_index(built)
        loaded = driver.load_bulk_activity_index(path, sha)
        self.assertEqual((loaded.tier(C1, 2024, 'B'), loaded.tier(C1, 2024, 'E_regular'), loaded.tier(C1, 2024, 'E_notice')), ((1, 1), (1, 1), (2, None)), 'no IE file ingested: E_notice stays unknown')


# ---------------------------------------------------------------- persisted tiers
class Persistence(Base):
    def test_set_bulk_index_records_every_existing_endpoint_with_its_source_and_never_stores_a_zero(self):
        frontier = self.seeded()
        loaded = self.index([(C2, 2024, 'B', 7), (C4, 2024, 'E_notice', 3)], complete={('E_regular', 2024): False})
        summary = frontier.set_bulk_index(loaded)
        tiers = self.tiers(frontier)
        self.assertEqual(len(tiers), 15, '5 units x B / E_regular / E_notice')
        self.assertEqual(tiers[(C2, 2024, 'B')], (1, 7))
        self.assertEqual(tiers[(C4, 2024, 'E_notice')], (1, 3))
        self.assertEqual({v for k, v in tiers.items() if k[2] == 'E_regular'}, {(2, None)})
        self.assertEqual(tiers[(C1, 2024, 'B')], (3, None))
        self.assertEqual({v[1] for v in tiers.values()}, {7, 3, None}, 'bulk_rows is NULL unless rows were observed')
        sources = {r[0] for r in frontier.db.execute('SELECT DISTINCT source FROM endpoint_bulk_tier')}
        shas = {r[0] for r in frontier.db.execute('SELECT DISTINCT index_sha256 FROM endpoint_bulk_tier')}
        self.assertEqual((sources, shas), ({'bulk_activity_index'}, {loaded.receipt['sha256']}))
        self.assertEqual((summary['tiered_endpoints'], summary['by_tier']), (15, {'1': 2, '2': 5, '3': 8}))
        with self.assertRaises(sqlite3.IntegrityError):
            frontier.db.execute("INSERT INTO endpoint_bulk_tier VALUES('C00000001',2024,'B',4,NULL,'x','bulk_activity_index','t')")

    def test_the_index_changes_no_endpoint_unit_edge_page_or_observation(self):
        frontier = self.seeded()
        before = self.snapshot(frontier)
        frontier.set_bulk_index(self.index([(C2, 2024, 'B', 7), ('C00000777', 2024, 'B', 4), ('C00000888', 2026, 'E_notice', 1)]))
        self.assertEqual(self.snapshot(frontier), before)
        self.assertEqual(set(self.tiers(frontier)), {(c, y, k) for c, y, k in frontier.db.execute("SELECT committee_id,cycle,kind FROM endpoints WHERE kind IN('B','E_regular','E_notice')")},
                         'tiers exist only for endpoints that already existed; an index row for a committee the ledger does not hold creates nothing')

    def test_the_tier_table_is_not_an_extension_table_so_the_d072_paths_stay_as_they_were(self):
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([(C2, 2024, 'B', 7)]))
        self.assertFalse(frontier.extensions_ready)
        self.assertNotIn('endpoint_bulk_tier', driver.EXTENSION_NAMES)

    def test_reapplying_the_same_index_rewrites_nothing_and_another_index_rewrites_its_tiers(self):
        frontier = self.seeded()
        first = self.index([(C2, 2024, 'B', 7)], name='one.json')
        frontier.set_bulk_index(first)
        stamp = frontier.db.execute("SELECT recorded FROM endpoint_bulk_tier WHERE committee_id=? AND kind='B'", (C2,)).fetchone()[0]
        frontier.set_bulk_index(first)
        self.assertEqual(frontier.db.execute("SELECT recorded FROM endpoint_bulk_tier WHERE committee_id=? AND kind='B'", (C2,)).fetchone()[0], stamp)
        second = self.index([(C3, 2024, 'B', 2)], name='two.json')
        frontier.set_bulk_index(second)
        tiers = self.tiers(frontier)
        self.assertEqual((tiers[(C2, 2024, 'B')], tiers[(C3, 2024, 'B')]), ((3, None), (1, 2)))
        self.assertEqual({r[0] for r in frontier.db.execute('SELECT DISTINCT index_sha256 FROM endpoint_bulk_tier')}, {second.receipt['sha256']})

    def test_units_created_during_a_run_are_tiered_when_they_are_created(self):
        row = {'sub_id': ep.sid(0), 'disbursement_amount': ep.Decimal('10.25'), 'committee_id': C1, 'recipient_committee_id': C9}
        handler = ep.serve(other=lambda endpoint, query: ep.legacy_response([row], count=1) if endpoint == ep.SCHEDULE_B and query['committee_id'] == C1 else None)
        frontier = self.seeded(committees=(C1,))
        frontier.set_bulk_index(self.index([(C9, 2024, 'B', 4)]))
        self.assertNotIn((C9, 2024, 'B'), self.tiers(frontier))
        frontier.run(self.transport(handler))
        tiers = self.tiers(frontier)
        self.assertEqual((tiers[(C9, 2024, 'B')], tiers[(C9, 2024, 'E_notice')]), ((1, 4), (3, None)))
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM endpoints WHERE committee_id=? AND status='complete'", C9), 3, 'the new unit is collected like any other')

    def test_a_ledger_holding_the_tier_table_reopens_and_a_wrongly_shaped_one_is_refused(self):
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([(C2, 2024, 'B', 7)]))
        frontier.close()
        reopened = driver.Frontier(self.root / 'frontier.sqlite')
        self.addCleanup(reopened.close)
        self.assertEqual(self.tiers(reopened)[(C2, 2024, 'B')], (1, 7))
        reopened.close()
        with closing(sqlite3.connect(self.root / 'frontier.sqlite', isolation_level=None)) as db:
            db.execute('ALTER TABLE endpoint_bulk_tier ADD COLUMN stray TEXT')
        with self.assertRaises(ValueError) as caught:
            driver.Frontier(self.root / 'frontier.sqlite')
        self.assertIn('Frontier schema differs', str(caught.exception))

    def test_the_report_names_the_tiers_only_in_a_ledger_that_holds_them(self):
        plain = self.seeded(name='plain.sqlite')
        self.assertNotIn('bulk_activity_tiers', plain.report())
        tiered = self.seeded()
        tiered.set_bulk_index(self.index([(C2, 2024, 'B', 7)]))
        block = tiered.report()['bulk_activity_tiers']
        self.assertEqual((block['source'], block['tiered_endpoints'], block['tier_is_an_ordering_hint_only']), ('bulk_activity_index', 15, True))
        self.assertEqual(block['by_kind_tier']['B'], {'1': 1, '3': 4})


# ---------------------------------------------------------------- ordering
class Ordering(Base):
    def order(self, frontier, handler=None, **options):
        transport = self.transport(handler or ep.serve(), **options)
        frontier.run(transport)
        return sequence(transport)

    def test_tier_one_first_then_two_then_three_each_in_the_existing_fair_turn_order(self):
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([(C4, 2024, 'B', 9), (C4, 2024, 'E_notice', 2), (C2, 2024, 'E_regular', 1)], complete={('E_notice', 2024): False}))
        order = self.order(frontier)
        tier = lambda key: {1: 1, 2: 2, 3: 3}[self.tiers(frontier)[(key[0], 2024, key[1])][0]]
        self.assertEqual(order[:3], [(C2, 'E_regular'), (C4, 'B'), (C4, 'E_notice')], 'tier 1 in (cycle, committee, kind) order')
        self.assertEqual([tier(k) for k in order], sorted(tier(k) for k in order), 'tiers never interleave')
        group = lambda wanted: [k for k in order if tier(k) == wanted]
        for wanted in (1, 2, 3):
            self.assertEqual(group(wanted), sorted(group(wanted), key=lambda k: (k[0], k[1])), 'inside a tier the committee/kind order is the original fair-turn order')
        self.assertEqual([k for k in order if tier(k) == 2], [(c, 'E_notice') for c in (C1, C2, C3, C5)], 'unknown (E_notice not covered) comes before covered-and-none')
        self.assertEqual(len(order), 15)

    def test_without_an_index_the_order_is_the_original_one(self):
        frontier = self.seeded()
        order = self.order(frontier)
        self.assertEqual(order[:6], [(C1, 'B'), (C1, 'E_notice'), (C1, 'E_regular'), (C2, 'B'), (C2, 'E_notice'), (C2, 'E_regular')])

    def test_campaign_priority_comes_first_then_tier_then_fair_turns(self):
        frontier = self.seeded()
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl={2024: {'path': self.ccl(cs.Priority.LINES), 'vintage': 'v1'}})   # C2 rank 0, C3 rank 1
        frontier.set_bulk_index(self.index([(C3, 2024, 'B', 4), (C4, 2024, 'B', 9)]))
        order = self.order(frontier)
        self.assertEqual(order[:3], [(C3, 'B'), (C2, 'B'), (C4, 'B')], 'priority group by tier (C3 tier 1 before C2 tier 3 despite its later rank), then the best tier outside it')
        self.assertEqual(len(order), 15)

    def test_a_tier_one_endpoint_keeps_its_turn_across_pages_before_any_tier_three_first_page(self):
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([(C4, 2024, 'B', 3)]))
        order = self.order(frontier, cs.provider(b={C4: cs.b_rows(3)}))
        self.assertEqual(order[:3], [(C4, 'B')] * 3, 'the tier outranks the pages-stored fair-turn counter')
        self.assertEqual(len(order), 17, '15 endpoints plus the two extra pages')

    def test_the_same_holds_inside_the_campaign_priority_group(self):
        frontier = self.seeded()
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl={2024: {'path': self.ccl(cs.Priority.LINES), 'vintage': 'v1'}})   # C2 rank 0 (tier 3), C3 rank 1
        frontier.set_bulk_index(self.index([(C3, 2024, 'B', 3)]))
        order = self.order(frontier, cs.provider(b={C3: cs.b_rows(3)}))
        self.assertEqual(order[:4], [(C3, 'B')] * 3 + [(C2, 'B')], 'C3 (tier 1) takes its three pages before C2 (tier 3) takes its first')

    def test_persisted_tiers_order_a_restart_that_passes_no_flag(self):
        handler = ep.serve()
        whole = self.seeded(name='whole.sqlite')
        whole.set_bulk_index(self.index([(C4, 2024, 'B', 9), (C5, 2024, 'E_regular', 2)]))
        wanted = self.order(whole, handler)
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([(C4, 2024, 'B', 9), (C5, 2024, 'E_regular', 2)], name='same.json'))
        first = self.transport(handler, cap=1)
        frontier.run(first)
        frontier.close()
        reopened = driver.Frontier(self.root / 'frontier.sqlite')
        self.addCleanup(reopened.close)
        second = self.transport(handler)
        reopened.run(second)
        self.assertEqual(sequence(first) + sequence(second), wanted)
        self.assertEqual(sequence(first), [(C4, 'B')])
        self.assertEqual(sequence(second)[0], (C5, 'E_regular'), 'the second tier-1 endpoint is served by the restart before any tier-3 endpoint')
        self.assertIn('bulk_activity_tiers', reopened.report())

    def poisoned(self, module, name):
        """The reviewer's probe: 10 units (30 B/E endpoints); the B endpoints of the first 8 are tier 1 and have each failed 11 times with no page stored; the other 22 endpoints are fresh tier 3."""
        committees = tuple('C%08d' % n for n in range(1, 11))
        frontier = module.Frontier(self.root / name)
        self.addCleanup(frontier.close)
        frontier.seed([{'committee_id': c, 'cycle': 2024, 'origins': ['industryA']} for c in committees])
        if module is driver:
            frontier.set_bulk_index(self.index([(c, 2024, 'B', 5) for c in committees[:8]], name=name + '.json'))
        frontier.db.execute("UPDATE endpoints SET attempt_count=11 WHERE kind='B' AND committee_id IN (%s)" % ','.join("'%s'" % c for c in committees[:8]))
        return frontier, committees

    def test_eight_repeatedly_failing_tier_one_endpoints_do_not_starve_tier_three_and_the_order_equals_production(self):
        staged, committees = self.poisoned(driver, 'staged.sqlite')
        picks = lambda frontier, n: [(r['committee_id'], r['kind']) for r in frontier._next_rows(n, set())]
        failing = [(c, 'B') for c in committees[:8]]
        got = picks(staged, 30)
        self.assertNotIn(got[0], failing)
        self.assertTrue(set(got[:22]).isdisjoint(failing), 'all 22 fresh tier-3 endpoints get their turns first')
        self.assertEqual(got[22:], failing, 'the failing endpoints come last, still in the old fair-turn order')
        self.assertTrue(all(r[0] == 'pending' for r in staged.db.execute("SELECT status FROM endpoints WHERE kind='B' AND attempt_count=11")), 'they stay pending: demoted, not skipped')
        self.assertTrue(all(r[0] in (1, 3) for r in staged.db.execute('SELECT tier FROM endpoint_bulk_tier')), 'the demotion is a sort position, never a stored tier')
        production, _ = self.poisoned(self.base_module(), 'base.sqlite')
        self.assertEqual(got, picks(production, 30), 'the production query gives exactly this order here')
        self.assertEqual(picks(staged, 4), picks(production, 4))

    def test_the_failing_endpoints_are_still_fetched_after_the_fresh_work_and_complete_only_by_their_own_pages(self):
        staged, committees = self.poisoned(driver, 'staged.sqlite')
        transport = self.transport(ep.serve())
        staged.run(transport)
        order = sequence(transport)
        self.assertEqual(len(order), 30)
        self.assertEqual(order[22:], [(c, 'B') for c in committees[:8]])
        self.assertEqual(self.count(staged, "SELECT COUNT(*) FROM endpoints WHERE kind IN('B','E_regular','E_notice') AND status='complete' AND pages_stored>=1"), 30)

    def test_the_demotion_needs_three_failures_beyond_the_stored_pages_and_spares_healthy_multi_page_endpoints(self):
        frontier = self.seeded(committees=(C1, C2, C3, C4))
        frontier.set_bulk_index(self.index([(c, 2024, 'B', 9) for c in (C1, C2, C3, C4)]))
        frontier.db.execute("UPDATE endpoints SET attempt_count=2 WHERE committee_id=? AND kind='B'", (C1,))                                  # two failures: not demoted
        frontier.db.execute("UPDATE endpoints SET attempt_count=5,pages_stored=5,rows_stored=500 WHERE committee_id=? AND kind='B'", (C2,))    # five healthy pages: not demoted
        frontier.db.execute("UPDATE endpoints SET attempt_count=3 WHERE committee_id=? AND kind='B'", (C3,))                                  # three failures: demoted
        frontier.db.execute("UPDATE endpoints SET attempt_count=8,pages_stored=5,rows_stored=500 WHERE committee_id=? AND kind='B'", (C4,))    # five pages and three failures: demoted
        picks = [(r['committee_id'], r['kind']) for r in frontier._next_rows(12, set())]
        tier_one = [(C1, 'B'), (C2, 'B')]
        self.assertEqual(picks[:2], sorted(tier_one, key=lambda k: (frontier.db.execute('SELECT attempt_count,pages_stored FROM endpoints WHERE committee_id=? AND kind=?', (k[0], k[1])).fetchone()[0:2], k)))
        self.assertEqual(set(picks[:2]), set(tier_one))
        self.assertEqual(set(picks[-2:]), {(C3, 'B'), (C4, 'B')}, 'only the two with 3 or more failures sort after the fresh tier-3 work')

    def test_inside_the_priority_group_a_failing_endpoint_sorts_after_a_tier_three_one_and_before_everything_outside_it(self):
        frontier = self.seeded()
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl={2024: {'path': self.ccl(cs.Priority.LINES), 'vintage': 'v1'}})   # C2 rank 0, C3 rank 1
        frontier.set_bulk_index(self.index([(C2, 2024, 'B', 4), (C4, 2024, 'B', 9)]))
        frontier.db.execute("UPDATE endpoints SET attempt_count=9 WHERE committee_id=? AND kind='B'", (C2,))
        picks = [(r['committee_id'], r['kind']) for r in frontier._next_rows(15, set())]
        self.assertEqual(picks[:3], [(C3, 'B'), (C2, 'B'), (C4, 'B')], 'priority group (C3 tier 3 first, the failing tier-1 C2 after it), then the rest')

    def test_a_blocked_or_benched_tier_one_endpoint_does_not_hold_back_the_rest(self):
        frontier = self.seeded(committees=(C1, C2))
        frontier.set_bulk_index(self.index([(C1, 2024, 'B', 9)]))
        frontier.db.execute("UPDATE endpoints SET status='blocked',retry_after=? WHERE committee_id=? AND kind='B'", (4102444800.0, C1))
        order = self.order(frontier)
        self.assertNotIn((C1, 'B'), order)
        self.assertEqual(len(order), 5, 'the five other endpoints are still served')


# ---------------------------------------------------------------- tier 3 is last, never skipped, never complete without pages
class TierThree(Base):
    def all_three(self):
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([]))    # coverage complete and nothing observed anywhere: every B / E endpoint is tier 3
        self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM endpoint_bulk_tier WHERE tier=3'), 15)
        return frontier

    def audit(self, frontier):
        for kind, status, pages_stored, pages in frontier.db.execute("SELECT e.kind,e.status,e.pages_stored,(SELECT COUNT(*) FROM pages p WHERE p.committee_id=e.committee_id AND p.cycle=e.cycle AND p.kind=e.kind) "
                                                                    "FROM endpoints e WHERE e.kind IN('B','E_regular','E_notice')"):
            if status == 'complete':
                self.assertTrue(pages_stored >= 1 and pages == pages_stored, 'a complete endpoint has fetched pages')

    def test_tier_three_is_still_fetched_and_only_a_fetched_page_completes_it(self):
        frontier = self.all_three()
        transport = self.transport(ep.serve(), cap=4)
        report = frontier.run(transport)
        self.assertEqual(len(transport.calls), 4)
        self.audit(frontier)
        done = frontier.db.execute("SELECT COUNT(*) FROM endpoints WHERE kind IN('B','E_regular','E_notice') AND status='complete'").fetchone()[0]
        pending = frontier.db.execute("SELECT status,pages_stored,rows_stored,last_error FROM endpoints WHERE kind IN('B','E_regular','E_notice') AND status!='complete'").fetchall()
        self.assertEqual((done, len(pending)), (4, 11))
        self.assertEqual(sorted((tuple(r) for r in pending), key=repr), sorted([('pending', 0, 0, None)] * 10 + [('pending', 0, 0, 'inert physical request cap')], key=repr), 'what the cap did not reach is pending: not complete, not skipped, not zero-filled')
        self.assertFalse(report['supported_outgoing_endpoints_complete'])
        rest = self.transport(ep.serve())
        frontier.run(rest)
        self.assertEqual(len(rest.calls), 11)
        self.audit(frontier)
        self.assertTrue(frontier.report()['supported_outgoing_endpoints_complete'])

    def test_when_the_api_has_rows_for_a_tier_three_endpoint_they_are_collected_as_usual(self):
        frontier = self.all_three()
        handler = cs.provider(b={C2: cs.b_rows(2)})
        frontier.run(self.transport(handler))
        endpoint = frontier.db.execute("SELECT status,rows_stored,pages_stored,expected_count FROM endpoints WHERE committee_id=? AND kind='B'", (C2,)).fetchone()
        self.assertEqual(tuple(endpoint), ('complete', 2, 2, 2), 'the bulk "none observed" is an order, never an answer: the API wins')
        self.assertEqual(self.tiers(frontier)[(C2, 2024, 'B')], (3, None), 'and the tier is not rewritten by what the API returned')

    def test_a_tier_three_page_that_is_not_an_exact_certificate_stays_not_complete(self):
        frontier = self.all_three()
        inexact = lambda endpoint, query: ep.legacy_response([], count=0, exact=False) if endpoint == ep.SCHEDULE_B and query['committee_id'] == C1 else None
        frontier.run(self.transport(ep.serve(other=inexact)))
        status, issues = frontier.db.execute("SELECT status,coverage_issues_json FROM endpoints WHERE committee_id=? AND kind='B'", (C1,)).fetchone()
        self.assertNotEqual(status, 'complete')
        self.assertIn('count_exactness_unknown', json.loads(issues))

    def test_the_priority_and_tier_order_never_leaves_a_pending_endpoint_unreached(self):
        frontier = self.seeded()
        frontier.set_bulk_index(self.index([(C1, 2024, 'B', 1)]))
        transport = self.transport(ep.serve())
        frontier.run(transport)
        self.assertEqual(len(transport.calls), 15)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM endpoints WHERE status='pending'"), 0)


# ---------------------------------------------------------------- accounting does not depend on the tiers
class Accounting(Base):
    def play(self, root, workers=1, index=None):
        """conc.run_world with the index applied right after seeding (the same place main() applies it)."""
        original = driver.Frontier.seed

        def seed_then_tier(frontier, rows):
            original(frontier, rows)
            if index is not None:
                frontier.set_bulk_index(index)
        with mock.patch.object(driver.Frontier, 'seed', seed_then_tier):
            frontier, transport, _, report = conc.run_world(root, workers=workers)
        return conc.digest_of({'tables': conc.canonical(frontier.path), 'report': {name: report[name] for name in conc.COUNT_FIELDS}, 'http_attempts': transport.http_attempts}), report

    def indexes(self):
        """A truthful index (rows exactly where the synthetic provider has data), the inverse (rows only where it has none) and an empty one (everything tier 3)."""
        world = conc.World()
        truthful = [(conc.cid(n), cycle, kind, len(world.rows(n, cycle, kind))) for n in range(1, conc.NC + 1) for cycle in conc.CYCLES for kind in KINDS if world.rows(n, cycle, kind)]
        inverted = [(conc.cid(n), cycle, kind, 1) for n in range(1, conc.NC + 1) for cycle in conc.CYCLES for kind in KINDS if not world.rows(n, cycle, kind)]
        return {name: document(selection) for name, selection in (('truthful', truthful), ('inverted', inverted), ('nothing', []))}

    def test_final_accounting_is_identical_without_an_index_with_a_truthful_one_and_with_a_wrong_one(self):
        results = {}
        baseline, _ = self.play(self.root / 'none')
        for name, doc in self.indexes().items():
            path, sha = self.write_index(doc, name + '.json')
            results[name], report = self.play(self.root / name, index=driver.load_bulk_activity_index(path, sha))
            self.assertEqual(results[name], baseline, name + ' index changed the accounting')
            self.assertIn('bulk_activity_tiers', report)

    def test_worker_count_does_not_change_the_accounting_of_an_indexed_run(self):
        path, sha = self.write_index(self.indexes()['truthful'])
        index = driver.load_bulk_activity_index(path, sha)
        serial, _ = self.play(self.root / 'one', workers=1, index=index)
        parallel, _ = self.play(self.root / 'four', workers=4, index=index)
        self.assertEqual(serial, parallel)


# ---------------------------------------------------------------- no index: the production bytes' behaviour
class NoIndexParity(Base):
    def scenario(self, module, root):
        root.mkdir()
        frontier = module.Frontier(root / 'frontier.sqlite')
        frontier.seed([{'committee_id': c, 'cycle': 2024, 'origins': ['industryA']} for c in (C1, C2, C3, C4, C5)])
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl={2024: {'path': self.ccl(cs.Priority.LINES), 'vintage': 'v1'}})
        transport = ep.Transport(root / 'raw', cs.provider(b={C2: cs.b_rows(3), C3: cs.b_rows(1, start=10), C4: cs.b_rows(2, start=20)}), 100)
        report = frontier.run(transport)
        tables = conc.canonical(frontier.path)
        priority = sorted(tuple(r) for r in frontier.db.execute('SELECT candidate_id,cycle,priority_rank FROM campaign_priority'))
        mapping = sorted(tuple(r) for r in frontier.db.execute('SELECT candidate_id,cycle,committee_id,designation,source,source_sha256 FROM candidate_committee_map'))
        names = sorted(r[0] for r in frontier.db.execute("SELECT name FROM sqlite_master WHERE type IN('table','view') ORDER BY name"))
        frontier.close()
        return [(e, q['committee_id'], q.get('last_index')) for e, q in transport.calls], tables, names, report, (priority, mapping)

    def test_a_run_without_the_flags_equals_the_production_collector_call_for_call_and_row_for_row(self):
        base = self.base_module()
        with mock.patch.object(base, 'now', driver.now):
            expected = self.scenario(base, self.root / 'base')
        got = self.scenario(driver, self.root / 'staged')
        self.assertEqual(got[0], expected[0], 'identical request sequence')
        self.assertEqual(got[1], expected[1], 'identical canonical ledger tables')
        self.assertEqual(got[2], expected[2], 'identical table set: no endpoint_bulk_tier appears')
        self.assertEqual(sorted(got[3]), sorted(expected[3]), 'identical receipt field set')
        volatile = {'generated_utc', 'ledger', 'run_seconds', 'achieved_requests_per_hour', 'rolling_60s_requests_per_hour_peak', 'rolling_10min_requests_per_hour_last'}
        self.assertEqual({k: v for k, v in got[3].items() if k not in volatile}, {k: v for k, v in expected[3].items() if k not in volatile})
        self.assertEqual(got[4], expected[4], 'identical campaign priority and mapping rows')
        self.assertNotIn('endpoint_bulk_tier', got[2])
        self.assertNotIn('bulk_activity_tiers', got[3])

    def test_the_default_ledger_never_acquires_the_tier_table(self):
        frontier = self.seeded()
        frontier.run(self.transport(ep.serve()))
        names = {r[0] for r in frontier.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertNotIn('endpoint_bulk_tier', names)


# ---------------------------------------------------------------- the off switch (review round 2, L5)
class OffSwitch(Base):
    """The in-band way back to the production behaviour is `DROP TABLE endpoint_bulk_tier` on the closed ledger between batches (D076_NOTES.md, 9.5). Afterwards the ledger is what a
    never-indexed one is: the production collector opens it, and either collector, run without flags, plays it call for call and row for row like production on a plain ledger.
    Needs the production bytes (`collect_committee_activity.base.py`, supplied by the overlay); skips otherwise."""
    def indexed(self, name):
        frontier = self.seeded(name=name)
        frontier.set_bulk_index(self.index([(C4, 2024, 'B', 9), (C3, 2024, 'E_notice', 2)], name=name + '.index.json'))
        frontier.close()
        return self.root / name

    def drop(self, path):
        with closing(sqlite3.connect(path, isolation_level=None)) as db:
            db.execute('DROP TABLE endpoint_bulk_tier')
            self.assertEqual(db.execute('PRAGMA quick_check').fetchone()[0], 'ok')
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master")}
        self.assertNotIn('endpoint_bulk_tier', names)

    def play(self, module, path, label):
        frontier = module.Frontier(path)
        transport = ep.Transport(self.root / ('raw-' + label), cs.provider(b={C2: cs.b_rows(3), C3: cs.b_rows(1, start=10), C4: cs.b_rows(2, start=20)}), 100)
        frontier.run(transport)
        names = sorted(row[0] for row in frontier.db.execute("SELECT name FROM sqlite_master WHERE type='table'"))
        frontier.close()
        return [(e, q['committee_id'], q.get('last_index')) for e, q in transport.calls], conc.canonical(path), names

    def test_the_production_collector_refuses_a_ledger_holding_the_table_and_opens_it_after_the_drop(self):
        base = self.base_module()
        path = self.indexed('indexed.sqlite')
        with self.assertRaises(ValueError) as caught:
            base.Frontier(path)
        self.assertIn('Frontier schema differs', str(caught.exception))
        self.drop(path)
        base.Frontier(path).close()

    def test_after_the_drop_both_collectors_play_the_ledger_like_production_plays_a_never_indexed_one(self):
        base = self.base_module()
        self.seeded(name='plain.sqlite').close()
        with mock.patch.object(base, 'now', driver.now):
            expected = self.play(base, self.root / 'plain.sqlite', 'plain')
            for module, label in ((base, 'base'), (driver, 'staged')):
                path = self.indexed(label + '.sqlite')
                self.drop(path)
                got = self.play(module, path, label)
                with self.subTest(collector=label):
                    self.assertEqual(got[0], expected[0], 'identical request sequence')
                    self.assertEqual(got[1], expected[1], 'identical canonical ledger tables')
                    self.assertEqual(got[2], expected[2], 'the table does not come back on its own')
                    self.assertNotIn('endpoint_bulk_tier', got[2])

    def test_a_neutral_index_with_no_rows_and_no_complete_coverage_is_an_off_switch_inside_the_chain_tools(self):
        """The drop above leaves the validator and the observer behind (a later ledger without a table the earlier one held is refused; the live ledger must equal the newest accepted
        snapshot). Re-indexing with a neutral index keeps the ledger in the chain: every endpoint becomes tier 2 (unknown), so the order is the production fair-turn order."""
        base = self.base_module()
        self.seeded(name='plain.sqlite').close()
        neutral = document((), complete={(kind, cycle): False for kind in KINDS for cycle in (2022, 2024, 2026)})
        path, sha = self.write_index(neutral, 'neutral.json')
        frontier = self.seeded(name='indexed.sqlite')
        frontier.set_bulk_index(self.index([(C4, 2024, 'B', 9), (C3, 2024, 'E_notice', 2)], name='real.json'))
        self.assertIn((1, 9), set(self.tiers(frontier).values()))
        frontier.close()
        reopened = driver.Frontier(self.root / 'indexed.sqlite')
        reopened.set_bulk_index(driver.load_bulk_activity_index(path, sha))
        self.assertEqual({value for value in self.tiers(reopened).values()}, {(2, None)}, 'every endpoint is unknown again; nothing is a zero')
        self.assertEqual(self.count(reopened, 'SELECT COUNT(*) FROM endpoint_bulk_tier WHERE index_sha256<>?', sha), 0)
        reopened.close()
        with mock.patch.object(base, 'now', driver.now):
            expected = self.play(base, self.root / 'plain.sqlite', 'plain')
            got = self.play(driver, self.root / 'indexed.sqlite', 'staged')
        self.assertEqual(got[0], expected[0], 'identical request sequence')
        self.assertEqual(got[1], expected[1], 'identical canonical ledger tables')


# ---------------------------------------------------------------- command line
class CommandLine(Base):
    run_cli = cs.CommandLine.run_cli

    def flags(self, doc=None, raw=None, name='index.json'):
        path, sha = self.write_index(doc if doc is not None else document([(C1, 2024, 'B', 5), (C2, 2024, 'E_notice', 2)]), name, raw=raw)
        return ['--bulk-activity-index', str(path), '--expect-bulk-index-sha256', sha], sha

    def start(self):
        files = sorted((self.root / 'state').glob('RUN-*.START.json'), key=lambda p: p.stat().st_mtime_ns)
        return json.loads(files[-1].read_text())

    def test_the_flags_order_the_run_and_are_recorded_in_the_start_record_and_the_receipt(self):
        flags, sha = self.flags()
        receipt, transport = self.run_cli(flags)
        self.assertEqual(sequence(transport)[0], (C1, 'B'), 'tier 1 first')
        block = receipt['bulk_activity_index']
        self.assertEqual((block['sha256'], block['rows'], block['vintage']['newest_asof']), (sha, 2, '2026-10-17T10:14:43Z'))
        self.assertEqual(self.start()['bulk_activity_index'], block)
        self.assertEqual(receipt['bulk_activity_tiers']['index_sha256s'], [sha])
        self.assertEqual((receipt['bulk_activity_tiers']['tiered_endpoints'], receipt['bulk_activity_tiers']['by_tier']['1']), (6, 2))

    def test_a_restart_without_the_flags_keeps_ordering_by_the_persisted_tiers_and_says_so(self):
        flags, sha = self.flags()
        self.run_cli(flags)
        again, transport = self.run_cli([])
        self.assertNotIn('bulk_activity_index', again)
        self.assertNotIn('bulk_activity_index', self.start())
        self.assertEqual(again['bulk_activity_tiers']['index_sha256s'], [sha])

    def test_a_default_run_has_neither_receipt_field_nor_table(self):
        receipt, _ = self.run_cli([])
        self.assertNotIn('bulk_activity_index', receipt)
        self.assertNotIn('bulk_activity_tiers', receipt)
        self.assertNotIn('bulk_activity_index', self.start())
        with closing(sqlite3.connect(self.root / 'state' / 'frontier.sqlite')) as db:
            self.assertNotIn('endpoint_bulk_tier', {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")})

    def test_bad_flag_combinations_are_refused_before_any_transport_or_write(self):
        flags, sha = self.flags()
        path = flags[1]
        cases = {'index without pin': (flags[:2], 'given together or not at all'), 'pin without index': (flags[2:], 'given together or not at all'),
                 'pin not hex': ([flags[0], path, '--expect-bulk-index-sha256', 'xyz'], 'must be 64 lowercase hex digits'),
                 'pin upper case': ([flags[0], path, '--expect-bulk-index-sha256', sha.upper()], 'must be 64 lowercase hex digits')}
        for name, (extra, message) in cases.items():
            with self.subTest(case=name), mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('no transport')):
                with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.root / 's'), '--seeds', 'x', '--max-requests', '1', '--live'] + extra):
                    with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()) as err:
                        driver.main()
                self.assertIn(message, err.getvalue())
                self.assertFalse((self.root / 's').exists())

    def test_a_wrong_pin_or_a_malformed_index_stops_with_nothing_written(self):
        flags, sha = self.flags()
        seeds = self.root / 'seeds.json'
        seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': C1, 'cycle': 2024}]}))
        broken, _ = self.flags(raw=b'{"schema_version":1}', name='broken.json')
        for name, argv in (('wrong pin', [flags[0], flags[1], flags[2], '0' * 64]), ('malformed index', broken)):
            with self.subTest(case=name), mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('no transport')):
                with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.root / 's'), '--seeds', str(seeds), '--max-requests', '1', '--live', '--init-new-ledger'] + argv):
                    with self.assertRaises(SystemExit) as caught:
                        driver.main()
                self.assertIn('Nothing was written', str(caught.exception))
                self.assertFalse((self.root / 's').exists(), 'not even the state folder')

    def test_offline_subcommands_take_no_index(self):
        flags, _ = self.flags()
        state = self.root / 'state'
        state.mkdir()
        (state / 'frontier.sqlite').write_bytes(b'')
        for sub in (['--recover-crashed-runs'], ['--rebind-endpoint-contracts', '--enable-endpoint-contract', ep.F_ID]):
            with self.subTest(sub=sub), mock.patch.object(sys, 'argv', ['collect', '--state', str(state)] + sub + flags):
                with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()) as err:
                    driver.main()
                self.assertIn('--bulk-activity-index', err.getvalue())


if __name__ == '__main__':
    unittest.main()
