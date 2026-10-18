"""Inert controls for the D072 campaign-spending additions to the downstream collector, rebased on ledger schema 3 (D073) and the D071 collector.

Totals family (never a payment), candidate-to-principal-committee mapping (local ccl plus the optional API lookup),
persisted campaign priority with fair-turn ordering, and the additive ledger migration. No credentials, provider or
network: every ledger and archive is in a temporary directory, credential loading and sockets are patched to fail, and the
real accounted transport only receives a scripted fake opener.

Tests marked REAL read the accepted batch-02 snapshot (schema 2, opened mode=ro&immutable=1) from MENTAT_REAL_COMMITTEE_STATE or the repo's own
state/committee-activity; they skip when it is absent. The snapshot is never written: it is migrated to a NEW schema-3 scratch file first.
"""
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qsl, urlsplit

import test_collect_committee_activity_endpoints as ep

driver, contracts, accounted = ep.driver, ep.contracts, ep.accounted
C1, C2, C3, C9 = ep.C1, ep.C2, ep.C3, ep.C9
C4, C5, C7, C8 = 'C00000004', 'C00000005', 'C00000007', 'C00000008'
TOTALS_ID = contracts.binding('totals')['contract_id']
LOOKUP_ID = contracts.binding('candidate_committees')['contract_id']
F_ID, H4_ID = ep.F_ID, ep.H4_ID
REAL_STATE = Path(os.environ.get('MENTAT_REAL_COMMITTEE_STATE') or Path(__file__).resolve().parents[1] / 'state' / 'committee-activity')
REAL_SNAPSHOT = REAL_STATE / 'accepted-batches' / 'batch-02' / 'frontier.sqlite'
BASE_TABLES = ('units', 'origin_catalog', 'origin_seeds', 'ledger_policy', 'endpoints', 'pages', 'observations', 'edges', 'counterparty_conflicts',
               'candidate_resolutions', 'candidate_references', 'leaf_coverage', 'failures')
EXTENSION_TABLES = ('candidate_committee_map', 'candidate_committee_lookups', 'campaign_priority', 'endpoint_retrievals')


def totals_response(committee, *, page=1, pages=1, rows=1):
    results = [{'committee_id': committee, 'committee_name': 'INERT COMMITTEE', 'cycle': 2024, 'receipts': 1000.5 + n,
                'disbursements': 900.25, 'recipient_committee_id': C3, 'candidate_id': 'H0AA00001', 'subordinate_committee_id': C9}
               for n in range(rows)]
    return {'results': results, 'pagination': {'count': rows * pages, 'page': page, 'pages': pages, 'per_page': 100}}


def candidate_response(rows, *, page=1, pages=1):
    return {'results': rows, 'pagination': {'count': len(rows), 'page': page, 'pages': pages, 'per_page': 100}}


def provider(*, totals=None, candidate=None, b=None):
    """Scripted provider: totals/candidate lookups by endpoint, optional per-committee Schedule B rows (one per page)."""
    def other(endpoint, query):
        if endpoint.startswith('committee/') and endpoint.endswith('/totals/'):
            return (totals or (lambda committee, q: totals_response(committee)))(endpoint.split('/')[1], query)
        if endpoint.startswith('candidate/'):
            return candidate(endpoint.split('/')[1], query)
        if endpoint == ep.SCHEDULE_B and b is not None and query['committee_id'] in b:
            rows = b[query['committee_id']]
            start = 0 if 'last_index' not in query else [row['sub_id'] for row in rows].index(str(query['last_index'])) + 1
            chunk = rows[start:start + 1]
            cursor = {'last_index': chunk[-1]['sub_id'], 'last_disbursement_date': '2024-01-02'} if chunk and start + 1 < len(rows) else None
            return ep.legacy_response(chunk, count=len(rows), cursor=cursor)
        return None
    return ep.serve(other=other)


def b_rows(count, start=0):
    return [{'sub_id': ep.sid(start + n), 'disbursement_amount': ep.Decimal('10.25'), 'committee_id': C2} for n in range(count)]


class Base(ep.Base):
    def ccl(self, lines, cycle=2024, receipt=True):
        folder = self.root / ('ccl%d' % (cycle % 100))
        folder.mkdir(exist_ok=True)
        path = folder / 'ccl.txt'
        path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        if receipt:
            (folder / 'source-receipt.json').write_text(json.dumps({'last_modified': 'Mon, 07 Sep 2026 05:51:23 GMT'}), encoding='utf-8')
        return path

    def seeded(self, committees=(C1, C2, C3, C4, C5), name='frontier.sqlite', **options):
        frontier = self.frontier(name, **options)
        frontier.seed([{'committee_id': c, 'cycle': 2024, 'origins': ['industryA']} for c in committees])
        return frontier

    def dump_all(self, frontier):
        names = [r[0] for r in frontier.db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {n: sorted((tuple(r) for r in frontier.db.execute('SELECT * FROM ' + n)), key=repr) for n in names}

    def calls_b(self, transport):
        return [(q['committee_id'], q.get('last_index')) for endpoint, q in transport.calls if endpoint == ep.SCHEDULE_B]

    def adapter(self, handler, *, maximum=100, cap=1000, overrides=None):
        self.sequence += 1
        opener = ep.Opener(handler, overrides)
        transport = accounted.AccountedTransport([ep.KEY], self.root / 'quota.sqlite', self.root / ('archive-%d' % self.sequence),
                                                 maximum, opener=opener, cap=cap)
        self.addCleanup(transport.close)
        return transport, opener

    def used(self):
        with closing(sqlite3.connect(self.root / 'quota.sqlite')) as db:
            row = db.execute('SELECT requests_used FROM api_request_budget WHERE budget_id=?', (ep.budget_id(ep.KEY),)).fetchone()
        return 0 if row is None else row[0]


# ---------------------------------------------------------------- pure contract rules
class ContractRules(unittest.TestCase):
    def test_totals_query_has_no_committee_selector_and_cannot_be_steered(self):
        self.assertEqual(contracts.query('totals', C1, 2024), {'cycle': 2024, 'per_page': 100, 'page': 1})
        self.assertEqual(contracts.query('totals', C1, 2026, cursor={'page': 3})['page'], 3)
        for cursor in ({'page': 0}, {'page': True}, {'last_index': '1'}, {'page': 2, 'committee_id': C9}, {'cycle': 2022}, {'sort': 'x'}):
            with self.subTest(cursor=cursor), self.assertRaises(ValueError):
                contracts.query('totals', C1, 2024, cursor=cursor)
        for committee, cycle in (('C1', 2024), (None, 2024), (C1, 2023), (C1, '2024')):
            with self.subTest(committee=committee, cycle=cycle), self.assertRaises(ValueError):
                contracts.query('totals', committee, cycle)
        with self.assertRaises(ValueError):
            contracts.query('candidate_committees', C1, 2024)

    def test_candidate_committees_query_always_asks_for_the_principal_designation(self):
        self.assertEqual(contracts.candidate_committees_query('H0KY06104', 2024), {'designation': 'P', 'cycle': 2024, 'per_page': 100, 'page': 1})
        self.assertEqual(contracts.candidate_committees_query('S6KY00286', 2026, cursor={'page': 2})['page'], 2)
        for args, cursor in ((('H0KY0610', 2024), None), (('C00000001', 2024), None), (('H0KY06104', 2021), None),
                             (('H0KY06104', 2024), {'designation': 'A'}), (('H0KY06104', 2024), {'page': 0})):
            with self.subTest(args=args, cursor=cursor), self.assertRaises(ValueError):
                contracts.candidate_committees_query(*args, cursor=cursor)

    def test_totals_pages_follow_only_provider_reported_pages(self):
        query = contracts.query('totals', C1, 2024)
        single = contracts.inspect_page('totals', query, totals_response(C1))
        self.assertEqual((single['issues'], single['next_cursor'], single['page_end_matches_count']), ([], {}, True))
        bare = contracts.inspect_page('totals', query, {'results': [{'committee_id': C1}]})
        self.assertEqual((bare['issues'], bare['next_cursor'], bare['page_end_matches_count']), ([], {}, True), 'no pagination object: single page')
        first = contracts.inspect_page('totals', query, totals_response(C1, pages=3))
        self.assertEqual((first['issues'], first['next_cursor']), ([], {'page': 2}))
        last = contracts.inspect_page('totals', contracts.query('totals', C1, 2024, cursor={'page': 3}), totals_response(C1, page=3, pages=3))
        self.assertEqual((last['issues'], last['next_cursor'], last['page_end_matches_count']), ([], {}, True))
        for payload, issue in ((totals_response(C1, page=2, pages=3), 'wrong_numbered_page'),
                               ({'results': [], 'pagination': {'page': 1, 'pages': 3}}, 'empty_before_final_page'),
                               ({'results': [], 'pagination': {'page': 1, 'pages': 'x'}}, 'invalid_numbered_pages'),
                               ({'results': ['text'], 'pagination': {}}, 'result_row_not_object'),
                               ({'pagination': {}}, 'results_not_array'), ([], 'response_not_object')):
            result = contracts.inspect_page('totals', query, payload)
            with self.subTest(issue=issue):
                self.assertIn(issue, result['issues'])
                self.assertEqual((result['next_cursor'], result['page_end_matches_count']), ({}, False))

    def test_candidate_lookup_keeps_only_exact_principal_committee_ids(self):
        query = contracts.candidate_committees_query('H0AA00001', 2024)
        rows = [{'committee_id': C2, 'designation': 'P'}, {'committee_id': C2, 'designation': 'P'}, {'committee_id': C3, 'designation': 'A'},
                {'committee_id': 'bad', 'designation': 'P'}, {'designation': 'P'}]
        result = contracts.inspect_candidate_committees_page(query, candidate_response(rows))
        self.assertEqual((result['committee_ids'], result['ignored_non_principal_rows']), ([C2], 1))
        self.assertEqual(result['issues'], ['missing_or_invalid_committee_id'])

    def test_new_contracts_are_labeled_unpinned_and_fh4_binding_hashes_are_unchanged(self):
        for kind in ('totals', 'candidate_committees'):
            proof = contracts.binding(kind)
            self.assertIsNone(proof['official_commit'])
            self.assertTrue(proof['pin_status'].startswith('UNPINNED'))
            self.assertIn('unpinned', proof['contract_id'])
        self.assertEqual(contracts.binding('F')['contract_metadata_sha256'], '91c1a55f9b3734b79b757959bca66777fd61a41f74bad21120a0cbf2c781d1de')
        self.assertEqual(contracts.binding('H4')['contract_metadata_sha256'], 'b58e9335af8ed00617b664c4507a9d28120ff1d16b5315f88a58210e3b55ecd1')
        self.assertEqual(contracts.binding('F')['official_commit'], contracts.OFFICIAL_COMMIT)
        self.assertEqual(contracts.PER_UNIT_KINDS, ('F', 'H4', 'totals'))


# ---------------------------------------------------------------- totals family
class TotalsFamily(Base):
    def test_enabling_totals_uses_the_same_intent_and_adds_pending_rows_without_touching_existing_ones(self):
        path = self.legacy(units=((C1, 2024), (C2, 2024)))
        before = self.dump(path)
        frontier = self.frontier(enabled_endpoint_contracts=[TOTALS_ID])
        intent = json.loads(Path(path).with_suffix('.endpoint-contracts.json').read_bytes())
        self.assertEqual(intent['enabled_contract_ids'], [TOTALS_ID])
        self.assertEqual(intent['module_sha256'], hashlib.sha256(Path(contracts.__file__).read_bytes()).hexdigest())
        receipt = frontier.report()['endpoint_contract_enablement']
        self.assertEqual((receipt['created_existing_endpoints'], receipt['activated_existing_endpoints']), (2, 0))
        after = self.dump(path)
        self.assertEqual([row for row in before if row not in after], [], 'no existing endpoint row changed or vanished')
        self.assertEqual({(row[0], row[2], row[3]) for row in after if row not in before}, {(C1, 'totals', 'pending'), (C2, 'totals', 'pending')})
        frontier.close()
        again = self.frontier(enabled_endpoint_contracts=[TOTALS_ID])
        self.assertEqual(again.report()['endpoint_contract_enablement']['created_existing_endpoints'], 0)
        self.assertEqual(self.dump(path), after)
        # Units created after enablement carry a totals row; without enablement no unit ever gets one.
        again.seed([{'committee_id': C3, 'cycle': 2024}])
        self.assertEqual(self.count(again, "SELECT COUNT(*) FROM endpoints WHERE committee_id=? AND kind='totals'", C3), 1)
        plain = self.legacy(name='plain.sqlite', units=((C1, 2024),))
        self.assertEqual(self.count(self.frontier('plain.sqlite'), "SELECT COUNT(*) FROM endpoints WHERE kind='totals'"), 0)
        self.assertEqual(self.count(self.frontier('plain.sqlite'), 'SELECT COUNT(*) FROM endpoints WHERE committee_id=?', C1), 11)
        self.assertFalse(Path(plain).with_suffix('.endpoint-contracts.json').exists())

    def test_intent_is_one_exact_set_so_adding_f_h4_later_fails_closed_until_a_rebind_exists(self):
        path = self.legacy()
        self.frontier(enabled_endpoint_contracts=[TOTALS_ID]).close()
        for ids in ([TOTALS_ID, F_ID, H4_ID], [], [F_ID]):
            with self.subTest(ids=ids), self.assertRaises(driver.EndpointContractRefused):
                driver.Frontier(path, enabled_endpoint_contracts=ids)
        driver.Frontier(path, enabled_endpoint_contracts=[TOTALS_ID]).close()

    def test_totals_page_is_retrieved_never_a_payment_and_recorded_with_its_time(self):
        frontier = self.frontier(enabled_endpoint_contracts=[TOTALS_ID])
        frontier.seed([{'committee_id': C1, 'cycle': 2024, 'origins': ['industryA']}])
        transport = self.transport(provider())
        report = frontier.run(transport)
        totals_calls = [(e, q) for e, q in transport.calls if e.endswith('/totals/')]
        self.assertEqual(totals_calls, [('committee/%s/totals/' % C1, {'cycle': 2024, 'per_page': 100, 'page': 1})])
        row = self.endpoint(frontier, 'totals')
        self.assertEqual((row['status'], row['issues'], row['rows'], row['pages'], row['attempts']), ('complete', set(), 1, 1, 1))
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM edges"), 0)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM candidate_references"), 0)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM leaf_coverage WHERE kind='totals'"), 0)
        self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM units'), 1, 'recipient/subordinate fields in a totals object never create a unit')
        observation = frontier.db.execute("SELECT source_id,amount_text,amount_field,amount_status FROM observations WHERE kind='totals'").fetchone()
        self.assertEqual(tuple(observation), (None, None, 'totals_not_a_payment', 'not_a_payment_totals_row'))
        stored = json.loads(frontier.db.execute("SELECT raw_json FROM observations WHERE kind='totals'").fetchone()[0])
        self.assertEqual(stored['receipts'], 1000.5)
        retrieval = frontier.db.execute("SELECT retrieved_at,archive_sha256 FROM endpoint_retrievals WHERE kind='totals'").fetchone()
        self.assertIsNotNone(retrieval[0])
        self.assertEqual(retrieval[1], frontier.db.execute("SELECT archive_sha256 FROM pages WHERE kind='totals'").fetchone()[0])
        self.assertEqual(report['unknown_or_subcent_amount_occurrences'], 0)
        self.assertNotIn('totals', report['supported_outgoing_endpoint_kinds'])
        self.assertTrue(report['supported_outgoing_endpoints_complete'])
        self.assertEqual(report['endpoint_status_counts']['totals:complete'], 1)

    def test_numbered_totals_pages_complete_only_after_the_provider_reported_last_page(self):
        frontier = self.frontier(enabled_endpoint_contracts=[TOTALS_ID])
        frontier.seed([{'committee_id': C1, 'cycle': 2024}])
        transport = self.transport(provider(totals=lambda committee, q: totals_response(committee, page=int(q['page']), pages=3)))
        frontier.run(transport)
        self.assertEqual([q['page'] for e, q in transport.calls if e.endswith('/totals/')], [1, 2, 3])
        self.assertEqual((self.endpoint(frontier, 'totals')['status'], self.endpoint(frontier, 'totals')['rows']), ('complete', 3))

    def test_malformed_or_looping_totals_never_complete(self):
        for label, response in (('wrong page', lambda committee, q: totals_response(committee, page=2, pages=3)),
                                ('no rows before last page', lambda committee, q: {'results': [], 'pagination': {'page': int(q['page']), 'pages': 5}})):
            with self.subTest(label=label):
                frontier = self.frontier('t-%s.sqlite' % label.replace(' ', '-'), enabled_endpoint_contracts=[TOTALS_ID])
                frontier.seed([{'committee_id': C1, 'cycle': 2024}])
                frontier.run(self.transport(provider(totals=response)))
                row = self.endpoint(frontier, 'totals')
                self.assertEqual(row['status'], 'closed_unknown')
                self.assertTrue(row['issues'])
        frontier = self.frontier('t-not-object.sqlite', enabled_endpoint_contracts=[TOTALS_ID])
        frontier.seed([{'committee_id': C1, 'cycle': 2024}])
        frontier.run(self.transport(provider(totals=lambda committee, q: ['not', 'an', 'object'])))
        self.assertEqual(self.endpoint(frontier, 'totals')['status'], 'inconsistent')

    def test_totals_through_the_actual_adapter_is_charged_archived_and_bound(self):
        frontier = self.frontier(enabled_endpoint_contracts=[TOTALS_ID])
        frontier.seed([{'committee_id': C1, 'cycle': 2024}])
        transport, opener = self.adapter(provider())
        report = frontier.run(transport)
        self.assertEqual((transport.http_attempts, len(opener.calls), self.used()), (4, 4, 4))
        self.assertIn('committee/%s/totals/' % C1, opener.endpoints())
        record = next(r for r in transport.records if r['endpoint'].endswith('/totals/'))
        body = Path(record['path']).read_bytes()
        self.assertEqual(hashlib.sha256(body).hexdigest(), record['sha256'])
        self.assertEqual(frontier.db.execute("SELECT archive_sha256 FROM pages WHERE kind='totals'").fetchone()[0], record['sha256'])
        self.assertEqual(frontier.db.execute("SELECT retrieved_at FROM endpoint_retrievals WHERE kind='totals'").fetchone()[0], record['captured_utc'])
        self.assertTrue(report['supported_outgoing_endpoints_complete'])


# ---------------------------------------------------------------- additive migration
class Migration(Base):
    def test_unextended_open_and_run_leave_the_reviewed_eleven_table_ledger_untouched(self):
        path = self.legacy(units=((C1, 2024),))
        before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        driver.Frontier(path).close()
        self.assertEqual(hashlib.sha256(Path(path).read_bytes()).hexdigest(), before, 'an unextended open writes nothing')
        frontier = self.frontier()
        frontier.run(self.transport(ep.serve()))
        names = {r[0] for r in frontier.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(names, set(BASE_TABLES))
        self.assertIsNone(frontier.report()['campaign_priority'])

    def test_priority_migration_is_additive_and_a_second_pass_changes_nothing(self):
        path = self.legacy(units=((C1, 2024), (C2, 2024)))
        before = {name: rows for name, rows in self.dump_all(self.frontier()).items()}
        frontier = self.frontier()
        ccl = self.ccl(['H0AA00001|2024|2024|%s|H|P|1' % C2])
        spec = {2024: {'path': ccl, 'vintage': 'vintage-text'}}
        frontier.set_priority([('H0AA00001', 2024)], ccl=spec)
        first = self.dump_all(frontier)
        self.assertEqual(set(first) - set(before), set(EXTENSION_TABLES))
        for name in BASE_TABLES:
            self.assertEqual(first[name], before[name], name + ' unchanged')
        frontier.set_priority([('H0AA00001', 2024)], ccl=spec)
        self.assertEqual(self.dump_all(frontier)['candidate_committee_map'], first['candidate_committee_map'])
        self.assertEqual({k: v for k, v in self.dump_all(frontier).items() if k != 'campaign_priority' and k != 'candidate_committee_lookups'},
                         {k: v for k, v in first.items() if k != 'campaign_priority' and k != 'candidate_committee_lookups'})
        frontier.close()
        reopened = driver.Frontier(path)
        self.addCleanup(reopened.close)
        self.assertTrue(reopened.extensions_ready)
        self.assertEqual(self.dump_all(reopened)['candidate_committee_map'], first['candidate_committee_map'])

    def test_schema_gate_still_refuses_unknown_or_altered_tables(self):
        for label, statement in (('unknown table', 'CREATE TABLE surprise(x)'),
                                 ('altered extension table', 'CREATE TABLE campaign_priority(candidate_id TEXT,cycle INTEGER)'),
                                 ('altered reviewed table', None)):
            with self.subTest(label=label):
                path = self.legacy(name='gate-%s.sqlite' % label.replace(' ', '-'), units=((C1, 2024),))
                with closing(sqlite3.connect(path)) as db, db:
                    if statement:
                        db.execute(statement)
                    else:
                        db.execute('ALTER TABLE units ADD COLUMN extra TEXT')
                with self.assertRaises(ValueError):
                    driver.Frontier(path)
        path = self.legacy(name='gate-missing.sqlite', units=((C1, 2024),))
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('DROP TABLE failures')
        with self.assertRaises(ValueError):
            driver.Frontier(path)

    @unittest.skipUnless(REAL_SNAPSHOT.is_file(), 'REAL: accepted batch-02 snapshot not present')
    def test_REAL_batch02_scratch_copy_migration_changes_no_existing_row(self):
        copy, baseline = self.root / 'frontier.sqlite', self.root / 'baseline-schema3.sqlite'
        original_sha = hashlib.sha256(REAL_SNAPSHOT.read_bytes()).hexdigest()
        driver.migrate_to_schema3(REAL_SNAPSHOT, baseline, later_cycle_context=True)   # reads the accepted snapshot immutable, writes only the NEW file
        shutil.copyfile(baseline, copy)
        self.assertEqual(hashlib.sha256(REAL_SNAPSHOT.read_bytes()).hexdigest(), original_sha)
        ccl = self.ccl(['H0KY06104|2024|2024|C00467571|H|P|246479', 'H9ZZ99999|2024|2024|C99999999|H|P|9'])
        spec = {2024: {'path': ccl, 'vintage': 'inert'}}

        def compare():
            with closing(sqlite3.connect(':memory:', uri=True)) as db:
                db.execute("ATTACH DATABASE ? AS orig", (baseline.as_uri() + '?mode=ro&immutable=1',))
                db.execute("ATTACH DATABASE ? AS mig", (copy.as_uri() + '?mode=ro',))
                missing, extra = {}, {}
                for table in BASE_TABLES:
                    missing[table] = db.execute('SELECT COUNT(*) FROM (SELECT * FROM orig.%s EXCEPT SELECT * FROM mig.%s)' % (table, table)).fetchone()[0]
                    extra[table] = db.execute('SELECT COUNT(*) FROM (SELECT * FROM mig.%s EXCEPT SELECT * FROM orig.%s)' % (table, table)).fetchone()[0]
                extra['endpoints_not_totals'] = db.execute("SELECT COUNT(*) FROM (SELECT * FROM mig.endpoints EXCEPT SELECT * FROM orig.endpoints) WHERE kind<>'totals'").fetchone()[0]
                return missing, extra

        frontier = driver.Frontier(copy, include_later_cycle_context=True, enabled_endpoint_contracts=[TOTALS_ID, LOOKUP_ID])
        self.assertEqual(frontier.report()['endpoint_contract_enablement']['created_existing_endpoints'], 13141)
        self.assertEqual(frontier.set_priority([('H0KY06104', 2024)], ccl=spec)['units_seeded_now'], 0)
        frontier.close()
        missing, extra = compare()
        self.assertEqual(sum(missing.values()), 0, 'no original row changed or removed: ' + str(missing))
        self.assertEqual(sum(n for t, n in extra.items() if t != 'endpoints'), 0, 'Barr is already a unit: nothing but totals rows was added')
        self.assertEqual(extra['endpoints'], 13141)
        # A candidate whose committee is absent from the snapshot is seeded as a discovery-tagged unit; the second pass is a no-op.
        frontier = driver.Frontier(copy, include_later_cycle_context=True, enabled_endpoint_contracts=[TOTALS_ID, LOOKUP_ID])   # schema 3 records the policy: one policy per ledger
        self.assertEqual(frontier.report()['endpoint_contract_enablement']['created_existing_endpoints'], 0)
        summary = frontier.set_priority([('H0KY06104', 2024), ('H9ZZ99999', 2024)], ccl=spec)
        self.assertEqual(summary['units_seeded_now'], 1)
        settled = self.dump_all(frontier)
        frontier.set_priority([('H0KY06104', 2024), ('H9ZZ99999', 2024)], ccl=spec)
        self.assertEqual(self.dump_all(frontier)['units'], settled['units'])
        self.assertEqual(self.dump_all(frontier)['endpoints'], settled['endpoints'])
        frontier.close()
        missing, extra = compare()
        self.assertEqual(sum(missing.values()), 0, str(missing))
        # the absent committee C99999999 enters in cycle 2024 and, under the ledger's later-cycle policy, 2026: 2 units x 12 endpoint rows, one catalog entry, one seed
        self.assertEqual((extra['units'], extra['origin_catalog'], extra['origin_seeds'], extra['endpoints']), (2, 1, 1, 13141 + 24))
        quiet = ('units', 'origin_catalog', 'origin_seeds', 'endpoints', 'endpoints_not_totals')
        self.assertEqual({t: n for t, n in extra.items() if t not in quiet}, {t: 0 for t in extra if t not in quiet})
        self.assertEqual(extra['endpoints_not_totals'], 22)
        with closing(sqlite3.connect(copy)) as db:
            origin = json.loads(db.execute("SELECT base_metadata_json FROM origin_catalog WHERE origin_id='campaign-priority-H9ZZ99999-2024'").fetchone()[0])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 3)
        self.assertEqual((origin['discovery_source'], origin['giving_or_payment_evidence'], origin['hint_not_coverage']), ('campaign_priority', False, True))
        self.assertEqual(hashlib.sha256(REAL_SNAPSHOT.read_bytes()).hexdigest(), original_sha, 'the accepted snapshot itself is never written')


# ---------------------------------------------------------------- priority list, mapping and ordering
class PriorityFile(unittest.TestCase):
    def write(self, value):
        folder = tempfile.TemporaryDirectory(prefix='D072-inert-')
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / 'priority.json'
        path.write_text(json.dumps(value), encoding='utf-8')
        return path

    def test_file_validation_is_strict_and_order_is_rank(self):
        entries, receipt = driver.load_priority_candidates(self.write([{'candidate_id': 'H0KY06104', 'cycle': 2024}, {'candidate_id': 'S6KY00286', 'cycle': 2026}]))
        self.assertEqual(entries, [('H0KY06104', 2024), ('S6KY00286', 2026)])
        self.assertEqual((receipt['entries'], len(receipt['sha256'])), (2, 64))
        for bad in ([], {}, [{'candidate_id': 'H0KY06104'}], [{'candidate_id': 'H0KY06104', 'cycle': 2020}],
                    [{'candidate_id': 'H0KY06104', 'cycle': '2024'}], [{'candidate_id': 'h0ky06104', 'cycle': 2024}],
                    [{'candidate_id': 'H0KY06104', 'cycle': 2024, 'note': 'x'}], [{'candidate_id': 'C00000001', 'cycle': 2024}],
                    [{'candidate_id': 'H0KY06104', 'cycle': 2024}, {'candidate_id': 'H0KY06104', 'cycle': 2024}], ['H0KY06104'],
                    [{'candidate_id': 'H0KY06104', 'cycle': True}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                driver.load_priority_candidates(self.write(bad))


class Priority(Base):
    LINES = ['H0AA00001|2024|2024|%s|H|P|1' % C2, 'H0AA00001|2024|2024|%s|H|A|2' % C5, 'H0AA00002|2022|2024|%s|H|P|3' % C3,
             'H0AA00001|2022|2022|%s|H|P|4' % C4, 'H0AA00003|2024|2024|%s|H|P|5' % C9, 'H0AA00004|2024|2024|C00000010|H|A|6']

    def spec(self):
        return {2024: {'path': self.ccl(self.LINES), 'vintage': 'v1'}}

    def test_ccl_mapping_takes_principal_committees_for_the_fec_cycle_with_file_hash_and_vintage(self):
        frontier = self.seeded()
        spec = self.spec()
        summary = frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024), ('H0AA00004', 2024), ('H0AA00005', 2024)], ccl=spec)
        rows = frontier.db.execute('SELECT candidate_id,cycle,committee_id,designation,source,source_sha256,vintage_or_retrieved_at,detail_json '
                                   'FROM candidate_committee_map ORDER BY candidate_id,committee_id').fetchall()
        sha = hashlib.sha256(Path(spec[2024]['path']).read_bytes()).hexdigest()
        self.assertEqual([tuple(r[:5]) for r in rows], [('H0AA00001', 2024, C2, 'P', 'local_ccl'), ('H0AA00002', 2024, C3, 'P', 'local_ccl')])
        self.assertTrue(all(r[5] == sha and r[6] == 'v1' for r in rows))
        self.assertEqual(json.loads(rows[1][7])['cand_election_yr'], '2022', 'cycle is the FEC election year, not the candidate election year')
        lookups = {(r[0], r[1]): r[2] for r in frontier.db.execute('SELECT candidate_id,source,status FROM candidate_committee_lookups')}
        self.assertEqual(lookups[('H0AA00004', 'local_ccl')], 'none_found')
        self.assertEqual(lookups[('H0AA00005', 'local_ccl')], 'none_found')
        self.assertEqual((summary['priority_entries'], summary['entries_with_principal_committee_mapping'], summary['priority_committee_units']), (4, 2, 2))
        self.assertEqual(driver.ccl_vintage(spec[2024]['path']), 'Mon, 07 Sep 2026 05:51:23 GMT')
        bare = self.ccl(self.LINES, cycle=2026, receipt=False)
        self.assertTrue(driver.ccl_vintage(bare).startswith('file_mtime_utc:'))

    def test_absent_committee_units_are_seeded_as_discovery_tagged_origins_and_existing_units_are_left_alone(self):
        frontier = self.seeded(committees=(C1, C2))
        before = self.dump_all(frontier)
        summary = frontier.set_priority([('H0AA00001', 2024), ('H0AA00003', 2024)], ccl=self.spec())
        self.assertEqual(summary['units_seeded_now'], 1)
        after = self.dump_all(frontier)
        self.assertEqual([row for row in before['origin_seeds'] if row not in after['origin_seeds']], [])
        added = [row for row in after['origin_seeds'] if row not in before['origin_seeds']]
        self.assertEqual([(row[0], row[1]) for row in added], [(C9, 2024)])
        entry = [row for row in after['origin_catalog'] if row[0] == added[0][2]][0]
        self.assertEqual(entry[1], 'campaign-priority-H0AA00003-2024')
        metadata = json.loads(entry[2])
        self.assertEqual((metadata['discovery_source'], metadata['giving_or_payment_evidence'], metadata['hint_not_coverage']), ('campaign_priority', False, True))
        self.assertEqual(entry[3], 2024)
        self.assertEqual(driver.origin_closure.metadata_for(metadata, entry[3], 2024)['later_cycle_context'], False)
        self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM endpoints WHERE committee_id=?', C9), 11)

    def test_priority_committees_get_their_b_pages_first_with_fair_turns_and_everything_else_keeps_the_original_order(self):
        handler = provider(b={C2: b_rows(2), C3: b_rows(1, start=10)})
        plain = self.seeded(name='plain.sqlite')
        baseline = self.transport(handler)
        plain.run(baseline)
        self.assertEqual(baseline.calls[0][1]['committee_id'], C1, 'without priority the first turn goes to the first committee')
        frontier = self.seeded()
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl=self.spec())
        transport = self.transport(handler)
        frontier.run(transport)
        self.assertEqual(self.calls_b(transport)[:3], [(C2, None), (C3, None), (C2, ep.sid(0))], 'rank order, then fair turns inside the priority group')
        first_non_priority = next(i for i, (e, q) in enumerate(transport.calls) if q['committee_id'] not in (C2, C3) or (e != ep.SCHEDULE_B))
        self.assertEqual(first_non_priority, 3)
        self.assertEqual([(e, q['committee_id']) for e, q in transport.calls[3:6]], [(ep.SCHEDULE_B, C1), (ep.SCHEDULE_E, C1), (ep.SCHEDULE_E, C1)])
        self.assertEqual(len(transport.calls), len(baseline.calls), 'same total work: only the order differs')

    def test_priority_survives_restart_without_any_flag(self):
        handler = provider(b={C2: b_rows(3), C3: b_rows(1, start=10)})
        frontier = self.seeded()
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl=self.spec())
        stopped = self.transport(handler, cap=2)
        report = frontier.run(stopped)
        self.assertEqual(self.calls_b(stopped), [(C2, None), (C3, None)])
        self.assertEqual(report['stop']['error_type'], 'FECAdmissionRefused')
        frontier.close()
        reopened = driver.Frontier(self.root / 'frontier.sqlite')
        self.addCleanup(reopened.close)
        resumed = self.transport(handler, cap=2)
        reopened.run(resumed)
        self.assertEqual(self.calls_b(resumed), [(C2, ep.sid(0)), (C2, ep.sid(1))], 'the stored cursor of the priority endpoint continues before any other unit')
        self.assertEqual(reopened.report()['campaign_priority']['priority_entries'], 2)

    def test_a_later_list_upserts_ranks_and_keeps_earlier_entries(self):
        frontier = self.seeded()
        spec = self.spec()
        frontier.set_priority([('H0AA00001', 2024), ('H0AA00002', 2024)], ccl=spec)
        frontier.set_priority([('H0AA00002', 2024), ('H0AA00003', 2024)], ccl=spec)
        ranks = {tuple(r[:2]): r[2] for r in frontier.db.execute('SELECT candidate_id,cycle,priority_rank FROM campaign_priority')}
        self.assertEqual(ranks, {('H0AA00001', 2024): 0, ('H0AA00002', 2024): 0, ('H0AA00003', 2024): 1})

    def test_totals_endpoints_share_the_priority_group_when_enabled(self):
        frontier = self.seeded(enabled_endpoint_contracts=[TOTALS_ID])
        frontier.set_priority([('H0AA00002', 2024)], ccl=self.spec())
        transport = self.transport(provider())
        frontier.run(transport)
        self.assertEqual([(e.split('/')[-2] if e.endswith('/totals/') else 'B', q.get('committee_id') or e.split('/')[1]) for e, q in transport.calls[:2]],
                         [('B', C3), ('totals', C3)])

    def test_unmapped_priority_entries_change_nothing_about_ordering(self):
        frontier = self.seeded()
        frontier.set_priority([('H0AA00004', 2024)], ccl=self.spec())
        transport = self.transport(ep.serve())
        frontier.run(transport)
        self.assertEqual(transport.calls[0][1]['committee_id'], C1)


# ---------------------------------------------------------------- optional API lookup
class CandidateLookup(Base):
    def api(self, rows=None, name='frontier.sqlite', **options):
        frontier = self.seeded(committees=(C1,), name=name, enabled_endpoint_contracts=[LOOKUP_ID])
        frontier.set_priority([('H0AA00005', 2024)])
        handler = provider(candidate=lambda candidate, q: candidate_response(rows if rows is not None else [{'committee_id': C7, 'designation': 'P'}, {'committee_id': C8, 'designation': 'A'}]))
        transport, opener = self.adapter(handler, **options)
        return frontier, transport, opener

    def test_found_lookup_is_archived_charged_mapped_and_seeded(self):
        frontier, transport, opener = self.api()
        outcome = frontier.fill_priority_via_api(transport)
        self.assertEqual((outcome['lookups'], outcome['found'], outcome['http_attempts'], self.used()), (1, 1, 1, 1))
        self.assertEqual(opener.endpoints(), ['candidate/H0AA00005/committees/'])
        self.assertEqual(dict(parse_qsl(urlsplit(opener.calls[0][0]).query)), {'designation': 'P', 'cycle': '2024', 'per_page': '100', 'page': '1'})
        mapped = frontier.db.execute('SELECT committee_id,designation,source,source_sha256,vintage_or_retrieved_at FROM candidate_committee_map').fetchall()
        self.assertEqual([tuple(r[:3]) for r in mapped], [(C7, 'P', 'api_candidate_committees')])
        record = transport.records[0]
        self.assertEqual((mapped[0][3], mapped[0][4]), (record['sha256'], record['captured_utc']))
        self.assertEqual(hashlib.sha256(Path(record['path']).read_bytes()).hexdigest(), record['sha256'])
        detail = json.loads(frontier.db.execute('SELECT detail_json FROM candidate_committee_lookups').fetchone()[0])
        self.assertEqual((detail['ignored_non_principal_rows'], detail['archive_pages'][0]['sha256']), (1, record['sha256']))
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM units WHERE committee_id=?", C7), 1, 'the principal committee unit is seeded')
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM origin_seeds s JOIN origin_catalog c ON c.origin_ref=s.origin_ref WHERE s.committee_id=? AND c.origin_id LIKE 'campaign-priority-%'", C7), 1)
        self.assertEqual(self.count(frontier, "SELECT COUNT(*) FROM units WHERE committee_id=?", C8), 0)
        again = frontier.fill_priority_via_api(transport)
        self.assertEqual((again['lookups'], transport.http_attempts), (0, 1), 'a stored mapping is never looked up again')

    def test_lookup_requires_the_enabled_contract_and_makes_no_call_without_it(self):
        frontier = self.seeded(committees=(C1,))
        frontier.set_priority([('H0AA00005', 2024)])
        transport, opener = self.adapter(provider())
        skipped = frontier.fill_priority_via_api(transport)
        self.assertEqual((skipped['skipped'], skipped['lookups'], skipped['http_attempts']), ('candidate_committees contract not enabled', 0, 0))
        self.assertEqual((transport.http_attempts, len(opener.calls)), (0, 0))

    def test_none_found_is_remembered_but_a_failure_is_retried(self):
        frontier, transport, _ = self.api(rows=[{'committee_id': C8, 'designation': 'A'}])
        self.assertEqual(frontier.fill_priority_via_api(transport)['none_found'], 1)
        self.assertEqual(frontier.fill_priority_via_api(transport)['lookups'], 0)
        self.assertEqual(self.count(frontier, 'SELECT COUNT(*) FROM candidate_committee_map'), 0)
        failing, transport, _ = self.api(name='second.sqlite', overrides={1: (b'unavailable', 503)})
        outcome = failing.fill_priority_via_api(transport)
        self.assertEqual((outcome['failed'], outcome['stopped'], outcome['http_attempts']), (1, None, 1))
        self.assertEqual(failing.db.execute('SELECT status FROM candidate_committee_lookups').fetchone()[0], 'failed')
        self.assertEqual(self.count(failing, 'SELECT COUNT(*) FROM candidate_committee_map'), 0)
        retry = failing.fill_priority_via_api(transport)
        self.assertEqual((retry['lookups'], retry['found']), (1, 1))

    def test_cap_or_quota_refusal_stops_the_lookup_pass_and_is_recorded(self):
        frontier, transport, _ = self.api(maximum=0)
        outcome = frontier.fill_priority_via_api(transport)
        self.assertEqual(outcome['stopped']['error_type'], 'FECAdmissionRefused')
        self.assertEqual((transport.http_attempts, self.used()), (0, 0))
        self.assertEqual(frontier.db.execute('SELECT status FROM candidate_committee_lookups').fetchone()[0], 'failed')

    def test_malformed_lookup_response_is_not_mapped(self):
        frontier, transport, _ = self.api(rows=[{'committee_id': 'bad', 'designation': 'P'}])
        outcome = frontier.fill_priority_via_api(transport)
        self.assertEqual((outcome['failed'], self.count(frontier, 'SELECT COUNT(*) FROM candidate_committee_map')), (1, 0))


# ---------------------------------------------------------------- command line
class CommandLine(Base):
    def run_cli(self, argv, handler=None):
        state = self.root / 'state'
        seeds = self.root / 'seeds.json'
        if not seeds.exists():
            seeds.write_text(json.dumps({'schema_version': 1, 'seed_units': [{'committee_id': c, 'cycle': 2024} for c in (C1, C2)]}))
        transports = []

        def live(archive, maximum, **options):
            transport = self.transport(handler or provider(), cap=maximum)
            transport.maximum = maximum
            transports.append(transport)

            class Context:
                def __enter__(self_inner):
                    return transport

                def __exit__(self_inner, *exc):
                    return False
            return Context()
        base = ['collect', '--state', str(state), '--seeds', str(seeds), '--max-requests', '3', '--live']
        if not (state / 'frontier.sqlite').exists():
            base = base + ['--init-new-ledger']   # R1b: a missing ledger is refused unless the caller says so
        with mock.patch.object(accounted.AccountedTransport, 'live', side_effect=live), redirect_stdout(io.StringIO()):
            with mock.patch.object(sys, 'argv', base + argv):
                driver.main()
        receipt = json.loads(sorted(state.glob('RECEIPT-*.json'), key=lambda p: p.stat().st_mtime_ns)[-1].read_text())
        return receipt, transports[0]

    def test_flags_map_seed_prioritize_and_record_the_inputs_in_the_receipt(self):
        priority = self.root / 'priority.json'
        priority.write_text(json.dumps([{'candidate_id': 'H0AA00003', 'cycle': 2024}, {'candidate_id': 'H0AA00001', 'cycle': 2024}]))
        ccl = self.ccl(Priority.LINES)
        receipt, transport = self.run_cli(['--priority-candidates', str(priority), '--ccl', '2024=' + str(ccl), '--ccl-vintage', '2024=v-cli'])
        self.assertEqual(self.calls_b(transport)[:2], [(C9, None), (C2, None)], 'list order; C9 was not a seeded unit')
        self.assertEqual(receipt['priority_candidates_file']['sha256'], hashlib.sha256(priority.read_bytes()).hexdigest())
        self.assertEqual((receipt['campaign_priority']['priority_entries'], receipt['campaign_priority']['priority_committee_units']), (2, 2))
        self.assertEqual({k: receipt['candidate_committee_api_lookup'][k] for k in ('skipped', 'lookups', 'http_attempts')}, {'skipped': 'candidate_committees contract not enabled', 'lookups': 0, 'http_attempts': 0})
        self.assertEqual((receipt['http_attempts'], receipt['endpoint_attempts_recorded'], receipt['candidate_committee_lookup_http_attempts']), (3, 3, 0))
        with closing(sqlite3.connect(self.root / 'state' / 'frontier.sqlite')) as db:
            self.assertEqual(db.execute('SELECT vintage_or_retrieved_at FROM candidate_committee_map LIMIT 1').fetchone()[0], 'v-cli')
            self.assertEqual(db.execute("SELECT COUNT(*) FROM origin_catalog WHERE origin_id='campaign-priority-H0AA00003-2024'").fetchone()[0], 1)
        # A restart without any priority flag keeps the persisted priority (the receipt reports it).
        again, transport = self.run_cli([])
        self.assertEqual(again['campaign_priority']['priority_entries'], 2)
        self.assertNotIn('priority_candidates_file', again)

    def test_bad_flag_combinations_are_refused_before_any_transport(self):
        for flags in (['--ccl', '2024=x.txt'], ['--ccl-vintage', '2024=v'], ['--priority-candidates', 'p.json', '--ccl', 'x.txt'],
                      ['--priority-candidates', 'p.json', '--ccl', '2020=x.txt'], ['--priority-candidates', 'p.json', '--ccl-vintage', '2024=v']):
            with self.subTest(flags=flags), mock.patch.object(accounted.AccountedTransport, 'live', side_effect=AssertionError('no transport')):
                with mock.patch.object(sys, 'argv', ['collect', '--state', str(self.root / 's'), '--seeds', 'x', '--max-requests', '1', '--live'] + flags):
                    with self.assertRaises(SystemExit), mock.patch('sys.stderr', new=io.StringIO()):
                        driver.main()
        ep.fec_key_store.load_keys.assert_not_called()


if __name__ == '__main__':
    unittest.main()
