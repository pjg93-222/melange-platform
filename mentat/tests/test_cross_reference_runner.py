"""Independent offline tests of the real runner and its emitted ledgers."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from cross_reference_store import canonical, digest
from run_cross_reference import coverage_presentation, partisan_breakdowns, presentation, run, write_ledger
from cross_reference_engine import analyze


def receipt(sid, amount=100, **updates):
    value=dict(sub_id=sid, industry='Fixture', company='Acme', committee_id='C00000001',
               contributor_name='PERSON, ONE', contribution_receipt_date='2024-01-01',
               contribution_receipt_amount=amount, two_year_transaction_period=2024, cycle=2024,
               entity_type='IND', company_match_status='accepted', excluded_reason='',
               filing_form='F3X', line_number='11AI', transaction_id=sid, file_num=10,
               contributor_zip='12345', memo_code='', duplicate_memo=0)
    value.update(updates)
    return value


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.database=self.root/'source.db'
        self.states=self.root/'cross-reference'
        self.baselines=self.root/'baselines'
        self.columns={
            'sub_id':'TEXT','industry':'TEXT','company':'TEXT','committee_id':'TEXT','contributor_name':'TEXT',
            'contribution_receipt_date':'TEXT','contribution_receipt_amount':'REAL',
            'two_year_transaction_period':'INTEGER','cycle':'INTEGER','entity_type':'TEXT',
            'company_match_status':'TEXT','excluded_reason':'TEXT','filing_form':'TEXT','line_number':'TEXT',
            'transaction_id':'TEXT','file_num':'INTEGER','contributor_zip':'TEXT','memo_code':'TEXT',
            'duplicate_memo':'INTEGER','contributor_id':'TEXT','memo_text':'TEXT',
            'back_reference_transaction_id':'TEXT','adjustment_type':'TEXT','refund_of_sub_id':'TEXT',
            'adjustment_evidence_id':'TEXT'}
        with closing(sqlite3.connect(self.database)) as db, db:
            for table in ('contributions','corporate_giving'):
                db.execute(f'CREATE TABLE {table}('+','.join(f'{key} {kind}' for key,kind in self.columns.items())+')')

    def insert(self, rows, table='contributions', archive=False):
        with closing(sqlite3.connect(self.database)) as db, db:
            for row in rows:
                fields=list(row)
                db.execute(f'INSERT INTO {table}('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',
                           [row[key] for key in fields])
            if archive:
                db.execute('CREATE TABLE IF NOT EXISTS fec_response_archive(archive_sha256 TEXT,payload_json TEXT,payload_kind TEXT,created_at TEXT)')
                payload=canonical({'results':[{**row,'file_number':row['file_num']} for row in rows]})
                db.execute('INSERT INTO fec_response_archive VALUES(?,?,?,?)',(digest(payload),payload,'api_response','2026-09-10T12:00:00+00:00'))

    def pin(self, rows, counts=None):
        path=self.baselines/'fixture'/'2024'
        path.mkdir(parents=True)
        snapshot=path/'snapshot.sqlite'
        with closing(sqlite3.connect(snapshot)) as db, db:
            db.execute('CREATE TABLE facts(id TEXT PRIMARY KEY,raw TEXT,analysis TEXT)')
            for row in rows:
                year=int(row['contribution_receipt_date'][:4]);cycle=year+year%2
                raw={key:row[key] for key in ('sub_id','committee_id','contributor_name')}
                raw.update(date=row['contribution_receipt_date'][:10],
                           amount_cents=round(row['contribution_receipt_amount']*100),cycle=cycle)
                count=(counts or {}).get(row['sub_id'],raw['amount_cents'])
                analysis=dict(company=row['company'],counted_cents=count,party='D',recipient_category='Candidate')
                db.execute('INSERT INTO facts VALUES(?,?,?)',(row['sub_id'],canonical(raw),canonical(analysis)))
        (path/'current.json').write_text(json.dumps({'industry':'Fixture','cycles':[2024],
            'snapshot':str(snapshot),'snapshot_sha256':fingerprint(snapshot)}),encoding='utf-8')
        return snapshot

    def execute(self, **options):
        with patch('run_cross_reference.fetch_exact_evidence',side_effect=AssertionError('Offline test attempted FEC request')):
            return run(self.database,'Fixture',cycles=[2024],state_root=self.states,
                       baseline_root=self.baselines,fetch=False,**options)

    def directory(self, report=None):
        run_id=report['run_id'] if report else json.loads((self.states/'latest.json').read_text())['run_id']
        return self.states/run_id

    def ledger(self, report, table):
        with closing(sqlite3.connect(self.directory(report)/'ledger.sqlite')) as db:
            return [json.loads(row[0]) for row in db.execute(f'SELECT payload_json FROM {table} ORDER BY id')]

    def test_real_run_preserves_source_and_repeats_identical_decision_ledger(self):
        rows=[receipt('101',1.25),receipt('102',1.25,memo_code='X',back_reference_transaction_id='101')]
        self.insert(rows,archive=True)
        before=fingerprint(self.database)
        first=self.execute();second=self.execute()
        self.assertEqual(fingerprint(self.database),before)
        self.assertEqual(first['summary'],second['summary'])
        for table in ('decisions','links','changes','review_queue','coverage_queue'):
            self.assertEqual(self.ledger(first,table),self.ledger(second,table))
        self.assertEqual(first['ledger_sha256'],second['ledger_sha256'])
        self.assertEqual(first['summary']['observed_original_giving'],1.25)
        self.assertEqual(first['summary']['amount_change'],-1.25)
        self.assertFalse(first['summary']['verified_unique'])
        self.assertFalse(first['checks']['collection_completeness_established'])
        self.assertTrue((self.directory(first)/'changes.csv').exists())

    def test_plain_receipt_resolves_automatically_without_complete_filing(self):
        self.insert([receipt('101')], archive=True)
        report = self.execute()
        self.assertEqual(report['summary']['automatic_decisions'], 1)
        self.assertEqual(report['summary']['source_verified_records'], 1)
        self.assertEqual(report['summary']['unresolved_records'], 0)
        self.assertEqual(report['summary']['coverage_records'], 1)
        self.assertEqual(report['summary']['coverage_only_records'], 1)
        self.assertEqual(report['summary']['coverage_amount'], 100)
        self.assertFalse(report['review_queue'])
        self.assertEqual(report['coverage_reasons'][0]['records'], 1)
        self.assertEqual(len(self.ledger(report, 'coverage_queue')), 1)
        self.assertFalse(self.ledger(report, 'review_queue'))
        self.assertTrue((self.directory(report) / 'coverage.csv').exists())
        self.assertFalse(report['summary']['verified_unique'])
        self.assertEqual(report['summary']['supported_original_giving'], 0)

    def test_rejected_search_retains_ambiguity_but_obvious_interest_is_excluded(self):
        self.insert([receipt('101', 500, company_match_status='review', entity_type='ORG',
                             line_number='17', memo_text='INTEREST INCOME'),
                     receipt('102', 50, company_match_status='rejected')], archive=True)
        report = self.execute()
        self.assertEqual(report['summary']['automatic_decisions'], 1)
        self.assertEqual(report['summary']['unresolved_records'], 1)
        self.assertEqual(report['summary']['observed_original_giving'], 50)
        self.assertEqual(report['summary']['corporate_giving'], 0)
        self.assertEqual(report['summary']['coverage_records'], 1)

    def test_coverage_totals_deduplicate_overlapping_tasks_and_keep_actual_review(self):
        rows = [receipt('101', 100), receipt('102', -25)]
        result = analyze(rows)
        # Different coverage questions can concern the same physical receipt.
        result['coverage_queue'] = [
            {'reason': 'one', 'source_ids': ['101', '102'], 'action': 'Fetch filing'},
            {'reason': 'two', 'source_ids': ['101'], 'action': 'Check inventory'},
        ]
        summary, reasons, review, changes = presentation(result, rows)
        self.assertEqual(summary['coverage_records'], 2)
        self.assertEqual(summary['coverage_amount'], 125)
        self.assertEqual(summary['coverage_only_records'], 1)
        self.assertEqual(summary['unresolved_records'], 1)
        self.assertEqual(summary['unresolved_amount'], 25)
        self.assertEqual(summary['automatic_decisions'] + summary['unresolved_records'], 2)
        groups, tasks = coverage_presentation(result, rows)
        self.assertEqual([(r['reason'], r['amount']) for r in groups], [('one', 125), ('two', 100)])
        self.assertEqual(len(tasks), 2)

    def test_added_source_record_is_a_positive_change_against_pinned_baseline(self):
        original=receipt('101',100);added=receipt('102',20.25)
        self.pin([original]);self.insert([original,added])
        report=self.execute()
        self.assertEqual(report['summary']['baseline_amount'],100)
        self.assertEqual(report['summary']['observed_original_giving'],120.25)
        self.assertEqual(report['summary']['amount_change'],20.25)
        self.assertEqual(report['scope']['source_change_counts'],{'added':1})
        changes=self.ledger(report,'changes')
        self.assertEqual([(c['source_id'],c['baseline_cents'],c['delta_cents']) for c in changes],[('102',0,2025)])

    def test_missing_baseline_record_blocks_and_preserves_loss_evidence(self):
        original=receipt('101',100);lost=receipt('102',1000000)
        snapshot=self.pin([original,lost]);self.insert([original])
        before=fingerprint(snapshot)
        with self.assertRaisesRegex(ValueError,'missing or financially changed'):
            self.execute()
        directory=self.directory()
        self.assertFalse((directory/'report.json').exists())
        comparison=json.loads((directory/'source-comparison.json').read_text())
        self.assertEqual(comparison['changes'][0]['kind'],'missing')
        self.assertEqual(comparison['changes'][0]['before']['amount_cents'],100000000)
        self.assertEqual(fingerprint(snapshot),before)

    def test_complete_source_loss_still_produces_a_baseline_loss_ledger(self):
        self.pin([receipt('101',100)])
        with self.assertRaisesRegex(ValueError,'missing or financially changed'):
            self.execute()
        comparison=json.loads((self.directory()/'source-comparison.json').read_text())
        self.assertEqual(comparison['changes'][0]['kind'],'missing')
        self.assertFalse((self.directory()/'report.json').exists())

    def test_offsetting_amount_mutations_cannot_cancel(self):
        self.pin([receipt('101',100),receipt('102',100)])
        self.insert([receipt('101',90),receipt('102',110)])
        with self.assertRaisesRegex(ValueError,'2 accepted source records'):
            self.execute()
        comparison=json.loads((self.directory()/'source-comparison.json').read_text())
        self.assertEqual([c['kind'] for c in comparison['changes']],['changed','changed'])
        self.assertEqual(sum(c['after']['amount_cents']-c['before']['amount_cents'] for c in comparison['changes']),0)

    def test_receipt_date_defines_cycle_and_query_cycle_is_preserved(self):
        original=receipt('101',100,two_year_transaction_period=2026,cycle=2026)
        outside=receipt('102',999,contribution_receipt_date='2026-01-01',two_year_transaction_period=2024)
        self.pin([original]);self.insert([original,outside])
        report=self.execute()
        rows=self.ledger(report,'source_records')
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['cycle'],2024)
        self.assertEqual(rows[0]['source_query_cycle'],2026)
        self.assertEqual(report['summary']['observed_original_giving'],100)
        self.assertEqual(report['scope']['source_change_counts'],{})

    def test_cent_summary_maps_each_money_bucket_without_pac_double_count(self):
        employee=receipt('101',1.23)
        refund=receipt('105',-.23,adjustment_type='refund',refund_of_sub_id='101',adjustment_evidence_id='refund-1')
        self.insert([employee,refund])
        self.insert([receipt('102',2.34,entity_type='ORG',contributor_name='ACME INC'),
                     receipt('103',3.45,entity_type='ORG',contributor_name='UNVERIFIED ENTITY'),
                     receipt('104',4.56,entity_type='PAC',contributor_name='ACME PAC',contributor_id='C00000009')],table='corporate_giving')
        affiliations=self.root/'affiliations.json'
        affiliations.write_text(json.dumps({'records':[dict(company='Acme',entity_name='ACME INC',relationship='treasury_entity',
                valid_from='2023-01-01',source_url='https://www.fec.gov/fixture',evidence_id='ownership-1')]}))
        report=self.execute(affiliations_path=affiliations)
        summary=report['summary']
        self.assertEqual(summary['employee_giving'],1.23)
        self.assertEqual(summary['corporate_giving'],2.34)
        self.assertEqual(summary['unverified_funding'],3.45)
        self.assertEqual(summary['linked_refund_adjustments'],-.23)
        self.assertEqual(summary['pac_outgoing'],4.56)
        self.assertEqual(summary['observed_original_giving'],6.79)
        self.assertEqual(summary['baseline_amount'],11.35)
        self.assertEqual(summary['amount_change'],-4.56)
        self.assertEqual(summary['supported_original_giving'],0)
        with closing(sqlite3.connect(self.directory(report)/'ledger.sqlite')) as db:
            totals=json.loads(db.execute("SELECT payload_json FROM run_metadata WHERE name='totals'").fetchone()[0])
        self.assertEqual(totals['provisional_original_giving_cents'],679)
        self.assertEqual(totals['observed_linked_refund_cents'],-23)

    def test_source_mutation_during_run_blocks_final_report(self):
        self.insert([receipt('101')])
        changed=False
        def progress(value):
            nonlocal changed
            if not changed and any(s['id']=='identify' and s['status']=='running' for s in value['steps']):
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute('UPDATE contributions SET contribution_receipt_amount=101')
                changed=True
        with self.assertRaisesRegex(ValueError,'Source changed during cross-referencing'):
            self.execute(progress_callback=progress)
        self.assertFalse((self.directory()/'report.json').exists())

    def test_explicit_exclusion_is_not_restored_end_to_end(self):
        excluded=receipt('101',100,excluded_reason='Adjudicated outside scope')
        self.pin([excluded],counts={'101':0});self.insert([excluded])
        report=self.execute()
        self.assertEqual(report['summary']['observed_original_giving'],0)
        self.assertEqual(report['summary']['amount_change'],0)
        self.assertEqual(self.ledger(report,'decisions')[0]['bucket'],'excluded_source_policy')

    def test_identical_source_across_queries_counts_once_and_preserves_both_observations(self):
        employee=receipt('101',100)
        rejected=receipt('101',100,company_match_status='rejected',excluded_reason='corporate_query_individual_entity')
        self.insert([employee]);self.insert([rejected],table='corporate_giving')
        report=self.execute()
        self.assertEqual(report['summary']['observed_original_giving'],100)
        self.assertEqual(report['summary']['baseline_amount'],100)
        self.assertEqual(len(self.ledger(report,'source_records')),2)
        decisions=self.ledger(report,'decisions')
        self.assertEqual(len(decisions),1)
        self.assertEqual(decisions[0]['bucket'],'employee_original')
        self.assertEqual(report['scope']['source_observations'],2)
        self.assertEqual(report['scope']['distinct_source_ids'],1)

    def test_source_overlap_with_conflicting_amount_never_selects_a_winner(self):
        self.insert([receipt('101',100)])
        self.insert([receipt('101',101,company_match_status='rejected')],table='corporate_giving')
        with self.assertRaisesRegex(ValueError,'Conflicting.*(?:source|financial|FEC)'):
            self.execute()
        self.assertFalse((self.directory()/'report.json').exists())

    def test_source_overlap_with_conflicting_accounting_metadata_is_quarantined(self):
        self.insert([receipt('101',100,line_number='11AI')])
        self.insert([receipt('101',100,line_number='17',memo_text='INTEREST')],table='corporate_giving')
        report=self.execute()
        decision=self.ledger(report,'decisions')[0]
        self.assertEqual(decision['bucket'],'conflicting_source_evidence')
        self.assertEqual(decision['attribution_status'],'unverified')
        self.assertEqual(decision['supported_unique_cents'],0)
        self.assertEqual(report['summary']['observed_original_giving'],100)

    def test_competing_accepted_company_claims_remain_unresolved(self):
        self.insert([receipt('101',100,company='Acme')])
        self.insert([receipt('101',100,company='Other Company')],table='corporate_giving')
        report=self.execute()
        decision=self.ledger(report,'decisions')[0]
        self.assertEqual(decision['bucket'],'conflicting_source_evidence')
        self.assertEqual(decision['attribution_status'],'unverified')
        self.assertEqual(report['summary']['observed_original_giving'],100)

    def test_merged_fec_document_provenance_survives_into_decision_ledger(self):
        a=receipt('101',100)
        self.insert([a])
        old={**a,'file_number':10}
        new={**old,'line_number':None}
        documents=[]
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE fec_response_archive(archive_sha256 TEXT,payload_json TEXT,payload_kind TEXT,created_at TEXT)')
            for value,observed in [(old,'2026-09-09T12:00:00+00:00'),(new,'2026-09-10T12:00:00+00:00')]:
                payload=canonical({'results':[value]});documents.append(digest(payload))
                db.execute('INSERT INTO fec_response_archive VALUES(?,?,?,?)',(documents[-1],payload,'api_response',observed))
        report=self.execute()
        decision=self.ledger(report,'decisions')[0]
        self.assertTrue(set(documents) <= set(decision['evidence_ids']))
        self.assertEqual(decision['field_evidence_ids']['line_number'],[documents[0]])

    def test_ledger_bytes_ignore_header_and_evidence_dictionary_insertion_order(self):
        rows=[receipt('101',100,source_table='contributions'),
              receipt('102',200,source_table='contributions',file_num=11)]
        evidence={row['sub_id']:{**row,'file_number':row['file_num']} for row in rows}
        headers={str(number):dict(file_num=number,committee_id='C00000001',filing_form='F3X',original_file_num=None)
                 for number in (10,11)}
        result=analyze(rows,evidence=evidence,headers=headers)
        first=self.root/'ledger-order-first';first.mkdir()
        second=self.root/'ledger-order-second';second.mkdir()
        path_one=write_ledger(first,rows,result,evidence,headers,{'scope':'fixture'},[])
        path_two=write_ledger(second,rows,result,dict(reversed(list(evidence.items()))),
                              dict(reversed(list(headers.items()))),{'scope':'fixture'},[])
        self.assertEqual(path_one.read_bytes(),path_two.read_bytes())
        with closing(sqlite3.connect(path_one)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM filing_headers').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0],2)


class PartisanBreakdownTests(unittest.TestCase):
    def calculate(self, amounts):
        rows=[];decisions=[]
        for index,(party,original,pac) in enumerate(amounts):
            sid=str(index)
            rows.append({'sub_id':sid,'baseline_party':party})
            decisions.append({'source_id':sid,'counted_cents':original,'pac_activity_cents':pac})
        return {r['view']:r for r in partisan_breakdowns({'decisions':decisions},rows)}

    def test_unknown_dollars_stay_outside_party_denominator_but_inside_coverage(self):
        result=self.calculate([('D',10000,0),('R',5000,0),('',2500,0)])['original_giving']
        self.assertEqual(result['denominator_dr'],150)
        self.assertEqual(result['other_unknown'],25)
        self.assertAlmostEqual(result['democratic_percent'],100*100/150)
        self.assertAlmostEqual(result['classified_gross_percent'],100*150/175)

    def test_negative_party_or_nonpositive_denominator_does_not_emit_misleading_percentage(self):
        for democratic,republican in [(-10000,5000),(-5000,5000),(10000,-5000)]:
            with self.subTest(democratic=democratic,republican=republican):
                result=self.calculate([('D',democratic,0),('R',republican,0),('',5000,0)])['original_giving']
                self.assertIsNone(result['democratic_percent'])
                self.assertIsNone(result['republican_percent'])
                self.assertAlmostEqual(result['classified_gross_percent'],
                    100*(abs(democratic)+abs(republican))/(abs(democratic)+abs(republican)+5000))

    def test_original_and_pac_party_percentages_use_separate_money_views(self):
        result=self.calculate([('D',10000,0),('R',0,6000)])
        self.assertEqual(result['original_giving']['denominator_dr'],100)
        self.assertEqual(result['original_giving']['democratic_percent'],100)
        self.assertEqual(result['pac_activity']['denominator_dr'],60)
        self.assertEqual(result['pac_activity']['republican_percent'],100)


if __name__=='__main__':unittest.main()
