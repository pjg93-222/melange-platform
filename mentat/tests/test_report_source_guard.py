from pathlib import Path
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing, contextmanager

@contextmanager
def db(path):
    with closing(sqlite3.connect(path)) as connection, connection:
        yield connection

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from report_source_guard import prepare, check_payload, accept, sha


class SourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'source.db'
        with db(self.database) as c:
            for table in ('contributions', 'corporate_giving'):
                c.execute(f'CREATE TABLE {table}(sub_id TEXT, industry TEXT, company TEXT, committee_id TEXT, contributor_name TEXT, contribution_receipt_date TEXT, contribution_receipt_amount REAL)')
            c.execute("INSERT INTO contributions VALUES('1','Fixture','Acme','C1','ONE','2024-01-01',100)")
        self.receipt = self.root / 'build.json'
        self.payload = self.root / 'payload.json'
        self.row = dict(source_id='1', cycle=2024, amount=100, company='Acme',
                        committee_id='C1', contributor='ONE', date='2024-01-01',
                        include_in_analysis='Yes', inclusion_reason='Included', receipt_role='contribution',
                        filing_form='F3X', line_number='11AI')

    def start(self):
        return prepare(self.database, 'Fixture', (2024,), self.root / 'states', self.receipt)

    def check(self, rows=None):
        self.payload.write_text(json.dumps({'metadata': {'industry': 'Fixture'}, 'raw_rows': [self.row] if rows is None else rows}))
        return check_payload(self.receipt, self.payload)

    def seed(self):
        self.start(); self.check(); accept(self.receipt)

    def test_older_source_is_rejected_and_baseline_preserved(self):
        self.seed()
        pointer = Path(json.loads(self.receipt.read_text())['directory']) / 'current.json'
        baseline = sha(pointer)
        with db(self.database) as c:
            c.execute('DELETE FROM contributions')
        with self.assertRaisesRegex(ValueError, 'Source regression'):
            self.start()
        self.assertEqual(sha(pointer), baseline)
        state = json.loads(self.receipt.read_text())
        self.assertEqual(state['counts']['missing'], 1)
        self.assertEqual(json.loads(Path(state['source_ledger']).read_text())['before']['amount_cents'], 10000)

    def test_correct_exclusion_has_dollar_ledger_and_keeps_raw(self):
        self.seed(); self.start()
        result = self.check([{**self.row, 'include_in_analysis': 'No', 'inclusion_reason': 'Receipt role: other_receipt', 'receipt_role': 'other_receipt'}])
        self.assertEqual(result['delta_cents'], -10000)
        ledger = json.loads(Path(result['dollar_ledger']).read_text())
        self.assertEqual(ledger['before']['counted_cents'], 10000)
        self.assertEqual(ledger['after']['counted_cents'], 0)
        with db(result['snapshot']) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM facts').fetchone()[0], 1)

    def test_historical_baseline_without_reason_is_recorded_as_new_source(self):
        self.start()
        result=self.check([{**self.row, 'inclusion_reason':''}])
        self.assertEqual(result['status'],'verified')
        self.assertEqual(json.loads(Path(result['dollar_ledger']).read_text())['explanation'],'New source record')

    def test_unexplained_reduction_blocks(self):
        self.seed(); self.start()
        with self.assertRaisesRegex(ValueError, 'Unexplained'):
            self.check([{**self.row, 'include_in_analysis': 'No', 'inclusion_reason': ''}])

    def test_omitting_raw_evidence_or_changing_amount_blocks(self):
        self.start()
        with self.assertRaisesRegex(ValueError, 'omitted'):
            self.check([])
        with self.assertRaisesRegex(ValueError, 'amount or ID'):
            self.check([{**self.row, 'amount': 90}])

    def test_source_change_after_prepare_blocks(self):
        self.start()
        with db(self.database) as c:
            c.execute("UPDATE contributions SET contribution_receipt_amount=99")
        with self.assertRaisesRegex(ValueError, 'database changed'):
            self.check()

    def test_source_write_ahead_log_after_prepare_blocks(self):
        self.start()
        Path(str(self.database)+'-wal').write_bytes(b'active writer')
        with self.assertRaisesRegex(ValueError, 'quiescent'):
            self.check()

    def test_source_change_after_check_blocks_acceptance(self):
        self.start(); self.check()
        with db(self.database) as c:
            c.execute('UPDATE contributions SET contribution_receipt_amount=99')
        with self.assertRaisesRegex(ValueError, 'changed before acceptance'):
            accept(self.receipt)

    def test_first_cycle_build_cannot_bypass_full_baseline(self):
        prepare(self.database, 'Fixture', (2022,2024,2026), self.root/'states', self.receipt)
        self.check(); accept(self.receipt)
        with db(self.database) as c:
            c.execute('DELETE FROM contributions')
        with self.assertRaisesRegex(ValueError, 'Source regression'):
            self.start()

    def test_nonaccepted_warehouse_rows_cannot_be_silently_zeroed_when_omitted(self):
        with db(self.database) as c:
            c.execute('ALTER TABLE contributions ADD company_match_status TEXT')
            c.execute("UPDATE contributions SET company_match_status='accepted'")
            c.execute("INSERT INTO contributions VALUES('2','Fixture','Acme','C1','OTHER','2024-01-01',200,'rejected')")
        self.start()
        with self.assertRaisesRegex(ValueError, 'omitted scoped raw source'):
            self.check()

    def test_duplicate_exclusion_requires_counted_parent(self):
        self.start()
        with self.assertRaisesRegex(ValueError, 'no counted counterpart'):
            self.check([{**self.row, 'include_in_analysis':'No', 'duplicate_of_sub_id':'missing',
                         'duplicate_reason':'fec_structured_back_reference', 'inclusion_reason':'Memo duplicate'}])

    def test_saved_payload_cannot_publish_unresolved_or_noncontribution_roles(self):
        self.start()
        for role in ('other_receipt', 'asset_liquidation', 'disclosure_memo', 'unverified_contradicted_receipt'):
            with self.subTest(role=role), self.assertRaisesRegex(ValueError, 'Receipt evidence'):
                self.check([{**self.row, 'receipt_role': role}])

    def test_missing_metadata_and_unknown_roles_retain_counted_amounts(self):
        self.start()
        for field in ('filing_form', 'line_number'):
            with self.subTest(field=field):
                self.check([{**self.row, field: ''}])
        for role in ('', None, 'unverified_receipt_role', 'unverified_disclosure_memo', 'future_unknown_role', 'in_kind_contribution'):
            with self.subTest(role=role):
                self.check([{**self.row, 'receipt_role':role, 'receipt_role_unverified':1}])

    def test_cycle_cannot_change_while_amount_and_id_stay_equal(self):
        prepare(self.database, 'Fixture', (2022,2024,2026), self.root/'states', self.receipt)
        with self.assertRaisesRegex(ValueError, 'cycle disagrees'):
            self.check([{**self.row, 'cycle': 2026}])

    def test_inclusion_typo_cannot_silently_zero_a_receipt(self):
        self.start()
        for decision in ('', None, 'yes', 'Maybe'):
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, 'inclusion decision'):
                self.check([{**self.row, 'include_in_analysis': decision}])

    def test_memo_exclusion_cannot_omit_its_link_or_count_itself(self):
        self.start()
        for changes in (
            dict(include_in_analysis='No', duplicate_memo=1, inclusion_reason='Memo duplicate'),
            dict(include_in_analysis='No', duplicate_reason='fec_structured_back_reference', inclusion_reason='Memo duplicate'),
            dict(duplicate_of_sub_id='1', duplicate_reason='exact_same_recipient_memo'),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'Memo exclusion'):
                self.check([{**self.row, **changes}])

    def test_transfer_receipts_remain_eligible_under_declared_receipt_stage_policy(self):
        self.start()
        result = self.check([{**self.row, 'receipt_role':'transfer_receipt', 'line_number':'12'}])
        self.assertEqual(result['delta_cents'], 10000)

    def test_linked_memo_preserves_parent_and_reconciles_dollars(self):
        with db(self.database) as c:
            c.execute("INSERT INTO contributions VALUES('2','Fixture','Acme','C1','ONE','2024-01-01',100)")
        self.start()
        result = self.check([self.row, {**self.row, 'source_id':'2', 'include_in_analysis':'No',
                            'duplicate_memo':1, 'duplicate_of_sub_id':'1',
                            'duplicate_reason':'fec_structured_back_reference', 'inclusion_reason':'Memo duplicate'}])
        self.assertEqual(result['delta_cents'], 10000)
        with db(result['snapshot']) as c:
            self.assertEqual(c.execute('SELECT count(*) FROM facts').fetchone()[0], 2)

    def test_concurrent_accepted_build_cannot_be_overwritten(self):
        self.seed(); self.start()
        pointer = Path(json.loads(self.receipt.read_text())['directory']) / 'current.json'
        state = json.loads(pointer.read_text()); state['another_run'] = True
        pointer.write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'changed during'):
            self.check()

    def test_financial_identity_and_missing_fields_cannot_pass_balanced_totals(self):
        self.seed(); self.start()
        for field, wrong in [('committee_id','C999'), ('contributor','SOMEONE ELSE'), ('date','2024-12-31')]:
            for row in ({**self.row, field:wrong}, {k:v for k,v in self.row.items() if k != field}):
                with self.subTest(field=field,row=row), self.assertRaisesRegex(ValueError,'financial identity'):
                    self.check([row])
        self.assertEqual(self.check()['delta_cents'],0)

    def test_wider_scope_preserves_accepted_cycle_for_guard_and_runner(self):
        from run_cross_reference import baseline_rows
        self.seed()
        with db(self.database) as c:c.execute('DELETE FROM contributions')
        baseline, info=baseline_rows(self.root/'states','Fixture',(2022,2024,2026))
        self.assertEqual(baseline['1']['raw']['amount_cents'],10000)
        self.assertIsNotNone(info)
        with self.assertRaisesRegex(ValueError,'Source regression'):
            prepare(self.database,'Fixture',(2022,2024,2026),self.root/'states',self.receipt)
        self.assertEqual(json.loads(self.receipt.read_text())['counts']['missing'],1)

    def test_expansion_combines_disjoint_baselines_and_tracks_each_pointer(self):
        self.seed()
        with db(self.database) as c:
            c.execute("INSERT INTO contributions VALUES('2','Fixture','Acme','C2','TWO','2026-01-01',25)")
        prepare(self.database,'Fixture',(2026,),self.root/'states',self.receipt)
        other={**self.row,'source_id':'2','cycle':2026,'date':'2026-01-01','contributor':'TWO','committee_id':'C2','amount':25}
        self.check([other]);accept(self.receipt)
        state=prepare(self.database,'Fixture',(2022,2024,2026),self.root/'states',self.receipt)
        self.assertEqual(state['counts']['unchanged'],2)
        self.assertEqual(len(state['baseline_scopes']),2)
        self.assertEqual(self.check([self.row,other])['delta_cents'],0)
        path=self.root/'states/fixture/2024/current.json'
        saved=json.loads(path.read_text());saved['concurrent_change']=True
        path.write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError,'baseline changed'):accept(self.receipt)

    def test_new_overlapping_baseline_after_prepare_blocks(self):
        self.seed()
        prepare(self.database,'Fixture',(2022,2024,2026),self.root/'states',self.receipt)
        other=self.root/'parallel.json'
        prepare(self.database,'Fixture',(2022,2024),self.root/'states',other)
        self.payload.write_text(json.dumps({'metadata':{'industry':'Fixture'},'raw_rows':[self.row]}))
        check_payload(other,self.payload);accept(other)
        with self.assertRaisesRegex(ValueError,'baseline changed'):self.check()


if __name__ == '__main__':
    unittest.main()
