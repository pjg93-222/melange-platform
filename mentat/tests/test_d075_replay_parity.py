"""D075 replay parity (design gate R2): re-run accepted batch 02 from its 300 ARCHIVED responses through the evidence broker v2 and the collector, in-process, and require the result to
equal the schema-3 migration of accepted batch 02.

Inputs are read-only: the accepted batch-01 snapshot (the base batch 02 started from), its captured seed file, the batch-02 archive folder (300 original response pairs, including its HTTP 504s)
and the real batch-01/02 receipts. Everything that is written lives in the overlay scratch: a schema-3 migration of batch 01 (the starting ledger), a schema-3 migration of batch 02 (the
expected ledger), a cassette over the archive, the broker's own host folder, and the state the collector writes. The run uses the REAL run id, so the broker records the very same archive
paths as batch 02 (the production Windows spelling, mapped to the scratch folder), and the comparison needs no folder-name exception. No provider, credential or network.

Compared table by table, all rows both ways (a digest of the sorted rows and a symmetric difference when they differ): units, origin_catalog, origin_seeds, ledger_policy, endpoints, pages,
observations, edges, counterparty_conflicts, candidate_resolutions, candidate_references, leaf_coverage, failures. Excluded, and only these: units.created, endpoints.last_attempt_at,
failures.at and failures.id (clock and row ids), endpoints.retry_after (a clock value; only whether it is set is compared) and captured_utc inside the archived metadata records.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import time
import types
import unittest
from unittest import mock

import d075_support as s
import test_collect_committee_activity_concurrency as cc
import test_r1b_cross as cross
import archive_cassette

driver = s.driver
STATE = cross.REAL_STATE
BATCH1, BATCH2 = STATE / 'accepted-batches' / 'batch-01', STATE / 'accepted-batches' / 'batch-02'
RUN = 'b36c4ba7c6994abeb07943ae9a54ff18'
ARCHIVE = STATE / ('responses-' + RUN)
RECEIPT2, RECEIPT1 = STATE / ('RECEIPT-' + RUN + '.json'), STATE / 'RECEIPT-92fae84f338b45c1bb0b4edbc8d1760b.json'
SEEDS = sorted(STATE.glob('seeds-*.json'))
PRESENT = all(path.is_file() for path in (BATCH1 / 'frontier.sqlite', BATCH1 / 'SNAPSHOT.json', BATCH2 / 'frontier.sqlite', BATCH2 / 'SNAPSHOT.json', RECEIPT1, RECEIPT2)) and ARCHIVE.is_dir() and bool(SEEDS)

SPECS = {
    'units': ('committee_id,cycle', None),
    'origin_catalog': ('origin_ref,origin_id,base_metadata_json,first_reached_cycle', None),
    'origin_seeds': ('committee_id,cycle,origin_ref', None),
    'ledger_policy': ('name,value', None),
    'endpoints': ('committee_id,cycle,kind,status,request_cursor_json,expected_count,rows_stored,pages_stored,last_error,coverage_issues_json,attempt_count,retry_after IS NOT NULL', None),
    'pages': ('committee_id,cycle,kind,page_no,request_query_json,response_pagination_json,next_cursor_json,archive_path,archive_sha256,archive_bytes,records_count', None),
    'observations': ('committee_id,cycle,kind,observation_id,page_no,row_ordinal,source_id,raw_json,raw_row_sha256,amount_text,amount_field,amount_status', None),
    'edges': ('committee_id,cycle,kind,observation_id,source_id,destination_committee_id,destination_cycle,amount_text,raw_row_sha256,identity_status,counterparty_evidence_json', None),
    'counterparty_conflicts': ('committee_id,cycle,kind,observation_id,source_id,flat_committee_id,nested_committee_id,status', None),
    'candidate_resolutions': ('candidate_id,cycle,committee_id,kind,source_id,status', None),
    'candidate_references': ('candidate_id,cycle,committee_id,kind,observation_id,source_id,field,role', None),
    'leaf_coverage': ('committee_id,cycle,kind,observation_id,source_id,reason,raw_row_sha256', None),
    'failures': ('committee_id,cycle,kind,error_type,detail,request_query_json,archive_record_json', 'record'),
}


def normal(row, how):
    if how == 'record' and row[-1] is not None:
        record = json.loads(row[-1])
        record.pop('captured_utc', None)                                  # a clock value of the fetch, nothing else
        return row[:-1] + (json.dumps(record, sort_keys=True),)
    return row


def rows_of(path, table):
    columns, how = SPECS[table]
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)) as db:
        return sorted(json.dumps(normal(tuple(row), how), default=str) for row in db.execute('SELECT %s FROM %s' % (columns, table)))


def digest(rows):
    return hashlib.sha256('\n'.join(rows).encode('utf-8')).hexdigest()


def compare(left, right):
    """{table: {'rows': (n, m), 'only_left': n, 'only_right': m}} for every table; equal tables report zero both ways."""
    report = {}
    for table in SPECS:
        a, b = rows_of(left, table), rows_of(right, table)
        same = len(a) == len(b) and digest(a) == digest(b)
        report[table] = {'rows': (len(a), len(b)), 'equal': same}
        if not same:
            report[table].update(only_left=len(set(a) - set(b)), only_right=len(set(b) - set(a)))
    return report


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(folder):
    return hashlib.sha256(json.dumps(sorted((p.name, sha_file(p)) for p in Path(folder).iterdir())).encode()).hexdigest()


@unittest.skipUnless(PRESENT, 'REAL: the accepted batch-01/02 snapshots, their receipts, the captured seed file and the batch-02 archive are not present')
class ReplayParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        scratch = Path(os.environ.get('D075_SCRATCH') or __import__('tempfile').gettempdir())
        cls.root = scratch / ('parity-' + str(os.getpid()))
        shutil.rmtree(cls.root, ignore_errors=True)
        cls.root.mkdir(parents=True)
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.before = {'b1': sha_file(BATCH1 / 'frontier.sqlite'), 'b2': sha_file(BATCH2 / 'frontier.sqlite'), 'r1': sha_file(RECEIPT1), 'r2': sha_file(RECEIPT2), 'seeds': sha_file(SEEDS[0]),
                      'archive': tree_digest(ARCHIVE)}
        cls.real_receipt = json.loads(RECEIPT2.read_bytes())
        cls.stored_root = cls.real_receipt['ledger'].rsplit('\\', 1)[0]
        # the starting ledger (batch 01, schema 3) and the expected ledger (batch 02, schema 3): lossless migrations of the accepted snapshots, written to scratch only
        (cls.root / 'base').mkdir()
        (cls.root / 'expected').mkdir()
        started = time.monotonic()
        cls.base_proof = driver.migrate_to_schema3(BATCH1 / 'frontier.sqlite', cls.root / 'base' / 'frontier.sqlite', later_cycle_context=True, seed_file=SEEDS[0], snapshot_proof=BATCH1 / 'SNAPSHOT.json')
        cls.expected_proof = driver.migrate_to_schema3(BATCH2 / 'frontier.sqlite', cls.root / 'expected' / 'frontier.sqlite', later_cycle_context=True, seed_file=SEEDS[0], snapshot_proof=BATCH2 / 'SNAPSHOT.json')
        cls.migrate_seconds = round(time.monotonic() - started, 1)
        cls.run_replay()

    @classmethod
    def run_replay(cls, *, drop=None, name='run'):
        """Collector main() in broker mode over a broker whose provider is the cassette; returns (printed, broker, cassette, state)."""
        work = cls.root / name
        state, host = work / 'state', work / 'host'
        state.mkdir(parents=True)
        shutil.copyfile(cls.root / 'base' / 'frontier.sqlite', state / 'frontier.sqlite')
        cassette = archive_cassette.Cassette.from_archive([ARCHIVE])
        if drop is not None:
            cassette.entries.pop(drop)
            cassette.queue = {}
            for entry in cassette.entries:
                cassette.queue.setdefault(entry['key'], []).append(entry)
        broker = s.host.EvidenceBroker(keys=list(cc.KEYS), ledger=work / 'quota.sqlite', archive_root=host, run_id=RUN, families=['B', 'E'], max_attempts=300,
                                       opener=archive_cassette.CassetteOpener(cassette), cap=10 ** 9, stored_root=cls.stored_root)
        rig = types.SimpleNamespace(root=work, seeds=SEEDS[0])
        mapping = '%s=%s;%s=%s' % (host, cls.stored_root, state, cls.stored_root)
        started = time.monotonic()
        with mock.patch.dict(os.environ, {s.driver.stored_paths.VARIABLE: mapping}), mock.patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
            printed = s.invoke_broker(rig, broker, ['--include-later-cycle-context', '--max-requests', '300', '--max-failed-attempts', '8'], state=state, archive_root=host, run_id=RUN, seeds=SEEDS[0])
        seconds = round(time.monotonic() - started, 1)
        broker.shutdown()
        result = types.SimpleNamespace(printed=printed, broker=broker, cassette=cassette, state=state, host=host, seconds=seconds, mapping=mapping)
        if name == 'run':
            cls.result = result
        return result

    # -------------------------------------------------------------------------------------------- the gate
    def test_the_replay_equals_the_schema3_migration_of_accepted_batch_02(self):
        report = compare(self.result.state / 'frontier.sqlite', self.root / 'expected' / 'frontier.sqlite')
        self.assertEqual({table: item for table, item in report.items() if not item['equal']}, {}, 'every table equal both ways, row for row')
        counts = {table: item['rows'][0] for table, item in report.items()}
        self.assertEqual((counts['units'], counts['pages'], counts['failures']), (13141, 318, 4))
        self.assertGreater(counts['observations'], 10000)

    def test_the_cassette_was_used_in_full_with_no_miss_and_the_run_stopped_at_the_cap(self):
        report = self.result.cassette.report()
        self.assertEqual((report['entries'], report['served'], report['misses'], report['unused']), (300, 300, [], 0))
        receipt = json.loads(Path(self.result.printed['receipt']).read_bytes())
        self.assertEqual(receipt['stop'], self.real_receipt['stop'])
        self.assertEqual(receipt['stop']['error_type'], 'FECAdmissionRefused')
        self.assertIn('Physical HTTP request cap reached', receipt['stop']['detail'])

    def test_the_receipt_counts_equal_the_real_batch_02_receipt(self):
        receipt = json.loads(Path(self.result.printed['receipt']).read_bytes())
        names = ('http_attempts', 'physical_request_cap', 'failed_attempt_allowance', 'new_pages_this_invocation', 'pages', 'units', 'observations', 'edges', 'candidate_resolutions_required',
                 'candidate_beneficiary_or_unverified_references', 'counterparty_identity_conflicts', 'leaf_disclosure_boundaries', 'endpoint_status_counts', 'unknown_or_subcent_amount_occurrences',
                 'later_cycle_context_explicitly_enabled', 'later_context_missing_when_disabled', 'supported_outgoing_endpoints_complete', 'exhaustive_disclosed_spending_complete',
                 'required_unsupported_endpoints', 'supported_outgoing_endpoint_kinds')
        for name in names:
            self.assertEqual(receipt[name], self.real_receipt[name], name)
        self.assertEqual((receipt['http_attempts'], receipt['new_pages_this_invocation'], receipt['pages']), (300, 298, 318))
        self.assertEqual(receipt['seed_receipt']['sha256'], self.real_receipt['seed_receipt']['sha256'])
        self.assertEqual(receipt['workers'], 1)

    def test_the_recorded_spellings_are_the_production_ones_byte_for_byte(self):
        receipt = json.loads(Path(self.result.printed['receipt']).read_bytes())
        for name in ('ledger', 'raw_archive_directory'):
            self.assertEqual(receipt[name], self.real_receipt[name], name)
        self.assertEqual(receipt['seed_receipt']['captured_copy'], self.real_receipt['seed_receipt']['captured_copy'])
        self.assertEqual(receipt['ledger_schema_version'], 3)
        with closing(sqlite3.connect(self.result.state / 'frontier.sqlite')) as db, closing(sqlite3.connect(self.root / 'expected' / 'frontier.sqlite')) as expected:
            mine = sorted(row[0] for row in db.execute('SELECT archive_path FROM pages'))
            theirs = sorted(row[0] for row in expected.execute('SELECT archive_path FROM pages'))
        self.assertEqual(mine, theirs)
        prefix = self.real_receipt['raw_archive_directory'] + '\\attempt-'
        self.assertEqual(len([path for path in mine if path.startswith(prefix)]), 298, 'the 298 pages this run committed are named under the batch-02 folder, byte for byte')
        self.assertTrue(all(path.startswith(prefix) or '\\responses-92fae84f338b45c1bb0b4edbc8d1760b\\attempt-' in path for path in mine), 'and the 20 batch-01 pages under the batch-01 folder')

    def test_the_archive_the_broker_wrote_on_the_host_has_the_original_file_names_and_bytes(self):
        written = self.result.host / ('responses-' + RUN)
        self.assertEqual(sorted(p.name for p in written.iterdir()), sorted(p.name for p in ARCHIVE.iterdir()), 'same attempt numbers and hashes, 600 files')
        for name in sorted(p.name for p in ARCHIVE.glob('*.body.bin')):
            self.assertEqual((written / name).read_bytes(), (ARCHIVE / name).read_bytes(), name)
        journal = [line for line in s.journal(self.result.broker) if line['event'] == 'attempt']
        receipt = json.loads(Path(self.result.printed['receipt']).read_bytes())
        self.assertEqual((len(journal), receipt['http_attempts'], receipt['endpoint_attempts_recorded'], receipt['candidate_committee_lookup_http_attempts']), (300, 300, 300, 0))
        self.assertEqual(sorted(json.loads(self.result.broker.receipt_path.read_text())['by_status']), ['200', '504'])

    def test_the_migrations_are_lossless_and_nothing_accepted_was_touched(self):
        for proof in (self.base_proof, self.expected_proof):
            self.assertEqual(proof['result'], 'lossless')
            self.assertEqual(proof['equality']['mismatches_total'], 0)
        after = {'b1': sha_file(BATCH1 / 'frontier.sqlite'), 'b2': sha_file(BATCH2 / 'frontier.sqlite'), 'r1': sha_file(RECEIPT1), 'r2': sha_file(RECEIPT2), 'seeds': sha_file(SEEDS[0]),
                 'archive': tree_digest(ARCHIVE)}
        self.assertEqual(after, self.before)
        self.assertFalse(list(STATE.glob('*.partial*')))

    # -------------------------------------------------------------------------------------------- the comparison has teeth
    def test_a_cassette_missing_one_response_is_a_miss_a_broker_stop_and_a_different_ledger(self):
        broken = self.run_replay(drop=150, name='dropped')
        self.assertEqual(len(broken.cassette.misses), 1)
        receipt = json.loads(Path(broken.printed['receipt']).read_bytes())
        self.assertEqual(receipt['stop']['error_type'], 'BrokerUnavailable')
        self.assertLess(receipt['http_attempts'], 300)
        report = compare(broken.state / 'frontier.sqlite', self.root / 'expected' / 'frontier.sqlite')
        self.assertTrue([table for table, item in report.items() if not item['equal']], 'a replay that lost a response must NOT compare equal')


if __name__ == '__main__':
    unittest.main()
