"""The anchor snapshot builder (mentat/forecast/anchors): built against synthetic Mentat data with hand-computed expectations, asserted
against the shared contract (schema, invariants, negative controls), plus read-only and refusal guarantees. A run against the real
Mentat tree (ADDENDUM section F numbers, less the non_giving receipts left out of the forecast by the 2026-10-01 ruling) is opt-in:
MENTAT_ANCHORS_REAL=1 (credit unions) or =all (banks and property & casualty too)."""
import copy
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'forecast' / 'anchors'))
sys.path.insert(0, str(ROOT / 'forecast' / 'contracts'))
import build_giving_anchors as bga  # noqa: E402
import contract  # noqa: E402
import coverage  # noqa: E402
import cpi as cpi_mod  # noqa: E402
import d049  # noqa: E402
import fec_pas2  # noqa: E402
import lda as lda_mod  # noqa: E402
import mentat_sources as ms  # noqa: E402
import openpyxl  # noqa: E402

PAS2_HEAD = ['CMTE_ID', 'AMNDT_IND', 'RPT_TP', 'TRANSACTION_PGI', 'IMAGE_NUM', 'TRANSACTION_TP', 'ENTITY_TP', 'NAME', 'CITY', 'STATE', 'ZIP_CODE',
             'EMPLOYER', 'OCCUPATION', 'TRANSACTION_DT', 'TRANSACTION_AMT', 'OTHER_ID', 'CAND_ID', 'TRAN_ID', 'FILE_NUM', 'MEMO_CD', 'MEMO_TEXT', 'SUB_ID']


AFTER_EVERY_DEADLINE = date(2030, 1, 1)          # the as_of of the direct lda calls below: past every LD-2 deadline of their fixtures (latest quarter: 2026 Q4, due 2027-01-20)


def pas2_line(cmte, tp, amount, cand, dt, tran, file_num, sub, amndt='N', memo=''):
    row = dict.fromkeys(PAS2_HEAD, '')
    row.update(CMTE_ID=cmte, AMNDT_IND=amndt, RPT_TP='Q1', TRANSACTION_TP=tp, TRANSACTION_DT=dt, TRANSACTION_AMT=str(amount), CAND_ID=cand,
               TRAN_ID=tran, FILE_NUM=str(file_num), SUB_ID=str(sub), MEMO_CD=memo)
    return '|'.join(row[k] for k in PAS2_HEAD)


def write_text(path, lines):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='latin-1')


class World:
    """A synthetic Mentat project + report directory + FEC bulk files + LDA file + CPI workbook, all in one temp folder."""

    SCRAPED = '2026-07-28T03:06:03'

    def __init__(self, base, workbook_bytes=b'published workbook v1', cut_back=None, roster=('C00000001', 'C00000002')):
        self.base = Path(base)
        self.project = self.base / 'mentat'
        self.report = self.base / 'reports'
        self.fec = self.base / 'fec'
        self.report.mkdir(parents=True)
        self.workbook = self.report / 'Banking Industry Spending Overview.xlsx'
        self.workbook.write_bytes(workbook_bytes)
        self.cut_back, self.roster = cut_back, roster
        self._source_db()
        self._index()
        self._fec()
        self._lda()
        self._cpi()
        self.registry = {'schema_version': 1, 'thin_base_threshold_usd': 500, 'industries': [{
            'id': 'commercial_banks', 'label': 'Commercial banks (test)', 'forecast_industry': 'commercial_banks', 'mentat_dataset': 'Banking',
            'workbook': self.workbook.name, 'source_db_candidates': ['.cache/report-repairs/banking-*/data/mentat.db'], 'lda_sector': 'bank',
            'coverage_cut_back': cut_back, 'pac_roster': [{'id': c, 'company': 'Bank ' + c[-1]} for c in roster]}, {
            'id': 'property_casualty', 'label': 'P&C (test)', 'forecast_industry': 'property_casualty', 'mentat_dataset': 'Property & Casualty Insurance',
            'workbook': 'Property & Casualty Insurance Industry Spending Overview.xlsx', 'source_db_candidates': ['.cache/none/mentat.db'], 'lda_sector': None,
            'coverage_cut_back': None, 'pac_roster': []}]}
        self.options = dict(report_dir=self.report, fec_root=self.fec, lda_csv=self.base / 'sector_spend.csv', controls=self.base / '00_controls.xlsx', only=None, run=None,
                            lda_receipts=self.base / 'fullscope_receipts.json')          # base IS the temp root, so the data-root default (csv folder's parent) would be the shared temp folder

    # (sub_id, cycle label, date, party, funding source, receipt cents, committee type)
    FACTS = [
        (1, '2023-2024', '2023-05-10', 'D', 'employee_original', 100000, 'H'),
        (2, '2023-2024', '2024-02-01', 'R', 'employee_original', 200000, 'H'),
        (3, '2023-2024', '2024-03-01', 'D', 'employee_original', 50000, 'S'),
        (4, '2023-2024', '2024-04-01', 'R', 'employee_original', 150000, 'S'),
        (5, '2023-2024', '2024-05-01', 'R', 'employee_original', 999900, 'P'),
        (6, '2023-2024', '2024-06-01', 'R', 'employee_original', 10000, 'Q'),
        (7, '2023-2024', '2024-07-01', 'D', 'employee_original', -5000, 'H'),
        (8, '2023-2024', '2024-08-01', 'I', 'non_giving', 2500, 'H'),
        (9, '2023-2024', '2024-09-01', 'D', 'employee_original', 0, 'H'),
        (10, '2021-2022', '2022-06-01', 'R', 'employee_original', 70000, 'S'),
        (11, '2025-2026', '2025-03-10', 'D', 'employee_original', 30000, 'H'),
        (12, '2025-2026', '2025-09-15', 'R', 'employee_original', 40000, 'H'),
        (13, '2025-2026', '2026-01-20', 'R', 'employee_original', 60000, 'S'),
        (14, '2025-2026', '2026-06-20', 'D', 'employee_original', 70000, 'H'),
        (15, '2025-2026', '2026-07-20', 'D', 'employee_original', 80000, 'S'),
    ]

    NO_SOURCE = frozenset()          # sub_ids of FACTS that have no row in the pinned source database (unmatched in the chamber join)

    def _source_db(self):
        path = self.project / '.cache' / 'report-repairs' / 'banking-20260910' / 'data' / 'mentat.db'
        path.parent.mkdir(parents=True)
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE contributions(sub_id INTEGER, recipient_committee_type TEXT, scraped_at TEXT, industry TEXT)')
        db.execute('CREATE TABLE corporate_giving(sub_id INTEGER, recipient_committee_type TEXT, scraped_at TEXT, industry TEXT)')
        db.execute('CREATE TABLE api_work_progress(cycle INTEGER, fetched_complete INTEGER, stored_complete INTEGER)')
        for sub, _c, _d, _p, _s, _v, t in self.FACTS:
            if sub not in self.NO_SOURCE:
                db.execute('INSERT INTO contributions VALUES (?,?,?,?)', (sub, t, '2026-07-01T00:00:00', 'Banking'))
        db.execute('INSERT INTO corporate_giving VALUES (8, "H", ?, "Banking")', (self.SCRAPED,))
        for fetched, stored in ((1, 1), (1, 0), (1, 0)):
            db.execute('INSERT INTO api_work_progress VALUES (2026, ?, ?)', (fetched, stored))
        db.commit()
        db.close()
        self.source_db = path
        self.db_sha = hashlib.sha256(path.read_bytes()).hexdigest()

    def _index(self, built_at=1790000000.0, token='t' * 64):
        folder = self.project / 'state' / 'contribution-analytics'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (token + '.sqlite')
        db = sqlite3.connect(path)
        db.execute('CREATE TABLE facts(source_id TEXT PRIMARY KEY, cycle TEXT, date TEXT, party TEXT, recipient_category TEXT, funding_source TEXT, receipt_cents INTEGER)')
        totals = {}
        for sub, cycle, day, party, source, cents, _t in self.FACTS:
            db.execute('INSERT INTO facts VALUES (?,?,?,?,?,?,?)', (str(sub), cycle, day, party, 'Candidate', source, cents))
            totals[cycle] = totals.get(cycle, 0) + cents
        manifest = {'token': token, 'built_at': built_at, 'receipt_cycles': totals,
                    'provenance': {'workbook': self.workbook.name, 'workbook_sha256': hashlib.sha256(self.workbook.read_bytes()).hexdigest(),
                                   'database_sha256': self.db_sha, 'payload_sha256': 'b' * 64}}
        db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('INSERT INTO metadata VALUES (?,?)', ('manifest', json.dumps(manifest)))
        db.commit()
        db.close()
        self.index = path

    def _fec(self):
        cn = {'22': ['H2AA00001|A|DEM'], '24': ['H2AA00001|A|DEM', 'S2BB00002|B|REP', 'H2CC00003|C|IND', 'P80000001|P|REP'],
              '26': ['H2AA00001|A|DEM', 'S2BB00002|B|REP', 'H2CC00003|C|IND']}
        for yy, rows in cn.items():
            write_text(self.fec / 'fec_beneficiary' / ('cn' + yy) / 'cn.txt', [r.split('|')[0] + '|' + r.split('|')[1] + '|' + r.split('|')[2] + '|||||||||||' for r in rows])
            write_text(self.fec / 'fec_committee_master' / ('cm' + yy) / 'cm.txt', [c + '|NAME|||||||||Q||||' for c in ('C00000001', 'C00000002')])
        H, S, P = 'H2AA00001', 'S2BB00002', 'P80000001'
        pas = {
            '22': [pas2_line('C00000001', '24K', 1000, H, '03152022', 'X1', 50, 5001)],
            '24': [pas2_line('C00000001', '24K', 2000, H, '03152024', 'A1', 100, 1001),
                   pas2_line('C00000001', '24K', 2000, H, '03152024', 'A1', 100, 1001),       # exact duplicate sub_id: counted once
                   pas2_line('C00000001', '24K', 1000, S, '06202024', 'A2', 100, 1002),
                   pas2_line('C00000002', '24Z', 500, 'H2CC00003', '07012024', 'A4', 100, 1004),  # in-kind, other party
                   pas2_line('C00000001', '24A', 7000, H, '07012024', 'A5', 100, 1005),         # independent expenditure: excluded
                   pas2_line('C00000001', '24E', 6000, H, '07012024', 'A6', 100, 1006),
                   pas2_line('C00000001', '24K', -300, H, '09012024', 'A3', 101, 1003),         # refund nets out
                   pas2_line('C00000001', '24K', 100, P, '08012024', 'B1', 100, 1007),          # presidential
                   pas2_line('C00000002', '24K', 50, '', '08022024', 'B2', 100, 1008),          # no candidate id
                   pas2_line('C00000999', '24K', 5000, H, '03152024', 'Z1', 100, 1009)],        # not a roster committee
            '26': [pas2_line('C00000001', '24K', 400, H, '04102025', 'C1', 200, 2001),
                   pas2_line('C00000002', '24K', 600, S, '02102026', 'C2', 200, 2002),
                   pas2_line('C00000001', '24K', 800, H, '05202026', 'C3', 200, 2003),
                   pas2_line('C00000001', '24K', 900, S, '06152026', 'C4', 200, 2004),
                   pas2_line('C00000002', '24K', 1000, H, '07102026', 'C5', 200, 2005)]}
        for yy, lines in pas.items():
            folder = self.fec / 'fec_beneficiary' / ('pas2' + yy)
            write_text(folder / 'itpas2.txt', lines)
            sha = hashlib.sha256((folder / 'itpas2.txt').read_bytes()).hexdigest()
            (folder / 'source-receipt.json').write_text(json.dumps({'sha256': sha, 'last_modified': 'Sun, 06 Sep 2026 15:55:17 GMT'}))

    def _lda(self):
        head = 'sector,year,period,is_partial_year,distinct_organizations,filings,income_total,expenses_total,distinct_named_lobbyists'
        rows = [head]
        for year, inc, exp, lobbyists in ((2024, 400, 900, 40), (2025, 500, 1000, 41)):
            rows.append(f'bank,{year},annual,false,9,9,{inc},{exp},{lobbyists}')
            for q in (1, 2, 3, 4):
                rows.append(f'bank,{year},Q{q},false,9,9,{inc / 4},{exp / 4},{lobbyists - 10}')
        rows += ['bank,2026,annual,true,9,9,300,600,33', 'bank,2026,Q1,true,9,9,100,200,20', 'bank,2026,Q2,true,9,9,200,400,25', 'bank,2026,Q3,true,0,0,0,0,0', 'bank,2026,Q4,true,0,0,0,0,0']
        (self.base / 'sector_spend.csv').write_text('\n'.join(rows) + '\n')
        (self.base / 'fullscope_receipts.json').write_text(json.dumps({'scope_sweeps': {'2026': {'sweep_through_dt_posted': '2026-08-01T11:57:13-04:00'}}}))

    def _cpi(self):
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'data'
        sheet.append(['year', 'cpi_annual_average'])
        for year, value in ((2021, 270.0), (2022, 290.0), (2023, 300.0), (2024, 310.0), (2025, 320.0), (2026, None)):
            sheet.append([year, value])
        book.save(self.base / '00_controls.xlsx')

    def build(self, **kw):
        return bga.build_snapshot(self.project, self.registry, {**self.options, **kw})


class BuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = World(self.tmp.name)

    # --- numbers ----------------------------------------------------------------------------------------------------------------
    def test_general_giving_is_the_included_receipt_amount_net_of_adjustments_without_non_giving(self):
        snap, _ = self.world.build()
        g = snap['industries']['commercial_banks']['general_giving']
        # 2023-24: 1,000 + 2,000 + 500 + 1,500 + 9,999 + 100 - 50 (the zero-receipt fact is not an included receipt; the $25 non_giving receipt is left out
        # of the forecast by the 2026-10-01 ruling but stays in the split of the Analysis Amount)
        self.assertEqual(g['cycles'], {'2022': 700.0, '2024': 15049.0, '2026': 2800.0})
        self.assertEqual(g['by_funding_source']['2024'], {'employee_original': 15049.0, 'non_giving': 25.0})
        self.assertEqual(g['excluded_non_giving'], {'2022': 0.0, '2024': 25.0, '2026': 0.0})
        self.assertEqual(g['excluded_funding_sources'], ['non_giving'])
        self.assertEqual(g['cycles']['2024'] + g['excluded_non_giving']['2024'], sum(g['by_funding_source']['2024'].values()))

    def test_observed_cuts_are_cumulative_month_ends_and_stop_at_the_coverage_end(self):
        snap, _ = self.world.build()
        ind = snap['industries']['commercial_banks']
        cuts = ind['general_giving']['observed']
        self.assertEqual(cuts[0], {'through': '2025-01-31', 'amount': 0.0})
        self.assertEqual(cuts[-1]['through'], '2026-06-30')
        self.assertEqual(len(cuts), 18)
        by = {c['through']: c['amount'] for c in cuts}
        self.assertEqual(by['2025-03-31'], 300.0)
        self.assertEqual(by['2025-09-30'], 700.0)
        self.assertEqual(by['2026-01-31'], 1300.0)
        self.assertEqual(by['2026-06-30'], 2000.0)           # the 2026-07-20 row is after the coverage end
        self.assertEqual(ind['general_giving']['cycles']['2026'], 2800.0)   # the open cycle holds every row to date
        self.assertTrue(any('dated after the coverage end ($800.00)' in f for f in ind['coverage']['flags']))

    def test_cut_back_moves_the_coverage_end_and_leaves_later_rows_unobserved(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        world = World(self.tmp.name, cut_back={'when_rule_coverage_end': '2026-06-30', 'coverage_end': '2026-05-31', 'reason': 'June incomplete'})
        snap, _ = world.build()
        ind = snap['industries']['commercial_banks']
        self.assertEqual(ind['coverage']['rule_coverage_end'], '2026-06-30')
        self.assertEqual(ind['coverage']['coverage_end'], '2026-05-31')
        self.assertEqual(ind['coverage']['reason'], 'June incomplete')
        self.assertEqual(ind['general_giving']['observed'][-1], {'through': '2026-05-31', 'amount': 1300.0})   # the 2026-06-20 row is not observed
        self.assertEqual(ind['pac_to_candidates']['observed'][-1]['through'], '2026-05-31')
        self.assertEqual(contract.check_anchor_invariants(snap), [])

    def test_candidate_receipts_split_by_committee_type_and_party(self):
        snap, _ = self.world.build()
        c = snap['industries']['commercial_banks']['candidate_receipts']
        self.assertEqual(c['cycles']['2024']['House'], {'D': 1000.0 - 50.0, 'R': 2000.0, 'other': 0.0})      # adjustment nets; the $25 other-party receipt is non_giving: left out
        self.assertEqual(c['excluded_non_giving']['2024']['House'], {'D': 0.0, 'R': 0.0, 'other': 25.0})     # ... and reported
        self.assertEqual(c['cycles']['2024']['Senate'], {'D': 500.0, 'R': 1500.0, 'other': 0.0})
        self.assertEqual(c['cycles']['2022']['Senate'], {'D': 0.0, 'R': 700.0, 'other': 0.0})             # no House row in 2022: only the chambers with rows exist
        last = c['observed'][-1]
        self.assertEqual(last['House'], {'D': 1000.0, 'R': 400.0, 'other': 0.0})
        self.assertEqual(last['Senate'], {'D': 0.0, 'R': 600.0, 'other': 0.0})

    def test_thin_base_flags_follow_the_threshold_per_chamber(self):
        snap, _ = self.world.build()
        thin = snap['industries']['commercial_banks']['candidate_receipts']['thin_base']
        self.assertEqual(thin['2024'], {'House': False, 'Senate': False})       # 2950 and 2000 against a $500 test threshold
        self.assertEqual(thin['2022'], {'House': True, 'Senate': False})

    def test_pac_to_candidates_follows_the_d049_rules(self):
        snap, _ = self.world.build()
        p = snap['industries']['commercial_banks']['pac_to_candidates']
        y24 = p['cycles']['2024']
        self.assertEqual(y24['House'], {'D': 2000.0 - 300.0, 'R': 0.0, 'other': 500.0})       # duplicate counted once; refund nets out; 24A/24E excluded; in-kind counted
        self.assertEqual(y24['Senate'], {'D': 0.0, 'R': 1000.0, 'other': 0.0})
        self.assertEqual(y24['presidential'], 100.0)
        self.assertEqual(y24['no_candidate'], 50.0)
        self.assertEqual(y24['to_house_senate'], 3200.0)
        self.assertEqual(y24['all_recipients'], 3350.0)
        self.assertEqual(p['cycles']['2022']['to_house_senate'], 1000.0)
        self.assertEqual(p['committee_count'], 2)
        self.assertFalse(p['single_pac'])

    def test_pac_observed_cuts_run_to_the_coverage_end_and_the_file_total_is_separate(self):
        snap, _ = self.world.build()
        p = snap['industries']['commercial_banks']['pac_to_candidates']
        last = p['observed'][-1]
        self.assertEqual(last['through'], '2026-06-30')
        self.assertEqual(last['House'], {'D': 1200.0, 'R': 0.0, 'other': 0.0})       # 2025-04-10 and 2026-05-20; the 2026-07-10 row is after the coverage end
        self.assertEqual(last['Senate'], {'D': 0.0, 'R': 1500.0, 'other': 0.0})
        self.assertEqual(last['to_house_senate'], 2700.0)
        by = {c['through']: c['to_house_senate'] for c in p['observed']}
        self.assertEqual(by['2025-04-30'], 400.0)
        self.assertEqual(by['2026-02-28'], 1000.0)
        self.assertEqual(p['cycles']['2026']['to_house_senate'], 3700.0)             # every row in the file, including July
        self.assertEqual(p['file_through']['2026'], '2026-07-10')
        self.assertEqual({i['role'] for i in p['inputs']}, {f'{f}:{c}' for f in ('pas2', 'cn', 'cm') for c in (2022, 2024, 2026)})

    def test_single_committee_roster_is_flagged_single_pac(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        world = World(self.tmp.name, roster=('C00000001',))
        snap, _ = world.build()
        ind = snap['industries']['commercial_banks']
        self.assertTrue(ind['pac_to_candidates']['single_pac'])
        self.assertTrue(any('one committee' in f for f in ind['flags']))

    def test_lda_levels_keep_both_series_and_the_partial_year(self):
        snap, _ = self.world.build()
        lda = snap['industries']['commercial_banks']['lda']
        self.assertEqual(lda['status'], 'ready')
        self.assertEqual(lda['series']['income_total']['annual'], {'2024': 400.0, '2025': 500.0})
        self.assertEqual(lda['series']['expenses_total']['annual'], {'2024': 900.0, '2025': 1000.0})
        self.assertEqual(lda['series']['income_total']['partial'], {'year': 2026, 'through_quarter': 'Q2', 'quarters': {'Q1': 100.0, 'Q2': 200.0}, 'total': 300.0,
                                                                    'nowcast': {'year': 2026, 'through_quarter': 'Q2', 'observed': 300.0, 'prior_total': 500.0, 'share': 0.5,
                                                                                'share_years': [2024, 2025], 'shares': [0.5, 0.5], 'point': 550.0}})
        self.assertEqual(lda['series']['expenses_total']['partial']['total'], 600.0)
        self.assertEqual(lda['through'], '2026-06-30')

    def test_lda_counts_are_the_annual_rows_organizations_and_lobbyists_partial_year_included(self):
        snap, _ = self.world.build()
        lda = snap['industries']['commercial_banks']['lda']
        self.assertEqual(lda['counts'], {'2024': {'organizations': 9, 'lobbyists': 40}, '2025': {'organizations': 9, 'lobbyists': 41}, '2026': {'organizations': 9, 'lobbyists': 33}})
        # a file without the lobbyist column (an older Melange build) carries no counts instead of inventing them
        head = 'sector,year,period,is_partial_year,distinct_organizations,filings,income_total,expenses_total'
        rows = [dict(zip(head.split(','), line.split(','))) for line in ('x,2025,annual,false,1,4,100,900', 'x,2026,annual,true,1,2,40,80', 'x,2026,Q1,true,1,1,40,80')]
        self.assertEqual(lda_mod.sector_levels(rows, 'x', AFTER_EVERY_DEADLINE)['counts'], {})

    def test_cpi_multiplier_exists_only_for_cycles_with_a_published_cpi(self):
        snap, _ = self.world.build()
        self.assertEqual(snap['cpi']['multipliers'], {'2022': 320.0 / 280.0, '2024': 320.0 / 305.0})
        self.assertNotIn('2026', snap['cpi']['multipliers'])
        self.assertTrue(any('No 2026 CPI exists yet' in f for f in snap['industries']['commercial_banks']['coverage']['flags']))

    def test_work_unit_progress_flags_an_incomplete_collection(self):
        snap, _ = self.world.build()
        flags = ' '.join(snap['industries']['commercial_banks']['coverage']['flags'])
        self.assertIn('1 of 3 2026 collection work units are stored_complete (3 fetched)', flags)

    def test_an_industry_without_a_published_workbook_is_reported_unavailable_not_invented(self):
        snap, _ = self.world.build()
        self.assertEqual(list(snap['industries']), ['commercial_banks'])
        self.assertIn('property_casualty', snap['unavailable'])
        self.assertIn('not in the report directory', snap['unavailable']['property_casualty'])

    def test_a_missing_lda_file_is_named_not_reported_as_missing_rows(self):
        (self.world.base / 'sector_spend.csv').unlink()
        self.world.options['lda_csv'] = self.world.base / 'sector_spend.csv'
        snap, _ = self.world.build()
        lda = snap['industries']['commercial_banks']['lda']
        self.assertEqual(lda['status'], 'unavailable')
        self.assertIn('sector_spend.csv was not found', lda['reason'])
        self.assertEqual(bga.validate_snapshot(snap), [])

    def test_missing_lda_receipts_leave_the_lda_unavailable_and_without_nowcast_errors_never_the_filings_only_rule(self):
        receipts = self.world.options['lda_receipts']
        errors = {'pool': ['bank'], 'series': ['income_total', 'expenses_total'], 'targets': [2025], 'by_cut': {}}          # a stand-in: the fixture is too thin for real bands
        with patch.object(lda_mod, 'nowcast_errors', return_value=errors) as spy:
            receipts.unlink()
            snap, inputs = self.world.build()
            lda = snap['industries']['commercial_banks']['lda']
            self.assertEqual((lda['status'], lda['series'], lda['through']), ('unavailable', {}, None))
            self.assertEqual(lda['reason'], 'The Melange LDA receipts (fullscope_receipts.json) were not found, so the partial year cannot be dated.')
            self.assertEqual(snap['industries']['commercial_banks']['status']['lda'], 'unavailable')
            self.assertNotIn('lda_nowcast_errors', snap)
            spy.assert_not_called()
            self.assertNotIn('lda_receipts', {i['role'] for i in inputs})
            self.assertEqual(bga.validate_snapshot(snap), [])
            receipts.write_text(json.dumps({'scope_sweeps': {'2026': {}, '2025': {'sweep_through_dt_posted': '2026-01-05T00:00:00-05:00'}}}))      # no date for the data's last year
            snap, _ = self.world.build()
            self.assertEqual(snap['industries']['commercial_banks']['lda']['reason'],
                             'The Melange LDA receipts (fullscope_receipts.json) record no sweep date for 2026, so the partial year cannot be dated.')
            self.assertNotIn('lda_nowcast_errors', snap)
            spy.assert_not_called()

    def test_the_lda_receipts_are_a_recorded_input_and_their_sweep_date_dates_the_partial_year(self):
        receipts = self.world.options['lda_receipts']
        with patch.object(lda_mod, 'nowcast_errors', return_value={'stand_in': True}) as spy:
            snap, inputs = self.world.build()
        self.assertEqual([i for i in inputs if i['role'] == 'lda_receipts'],
                         [{'role': 'lda_receipts', 'path': str(receipts), 'sha256': hashlib.sha256(receipts.read_bytes()).hexdigest()}])
        self.assertEqual(spy.call_args.args[1], date(2026, 8, 1))
        self.assertEqual(snap['lda_nowcast_errors'], {'stand_in': True})
        self.assertEqual(snap['industries']['commercial_banks']['lda']['series']['income_total']['partial']['through_quarter'], 'Q2')
        receipts.write_text(json.dumps({'scope_sweeps': {'2026': {'sweep_through_dt_posted': '2026-04-19T08:00:00-04:00'}}}))      # Q1 is due April 20: not yet covered
        snap, _ = self.world.build()
        self.assertIsNone(snap['industries']['commercial_banks']['lda']['series']['income_total']['partial'])

    RULED = {'general_giving': {'excluded_funding_sources': ['non_giving']}, 'candidate_receipts': {'excluded_funding_sources': ['non_giving']}}   # on the 2026-10-01 basis

    def test_a_partial_build_keeps_the_other_industries_of_the_published_snapshot(self):
        old = {'schema_version': 1, 'kind': 'giving-anchors', 'industries': {'a': {'v': 'old a', **self.RULED}, 'b': {'v': 'old b'}},
               'unavailable': {'c': 'no index', 'b': 'stale reason'}}
        new = {'schema_version': 1, 'kind': 'giving-anchors', 'industries': {'b': {'v': 'new b'}}, 'unavailable': {'d': 'new reason'}}
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / 'published.json'
            path.write_text(json.dumps(old))
            merged = bga.merge_with_published(new, path, ['b', 'd'])
            self.assertEqual(merged['industries'], {'a': {'v': 'old a', **self.RULED}, 'b': {'v': 'new b'}})
            self.assertEqual(merged['unavailable'], {'c': 'no index', 'd': 'new reason'})
            self.assertEqual(bga.merge_with_published(new, Path(out) / 'absent.json', ['b']), new)       # nothing published yet: the partial build stands alone

    def test_a_partial_build_does_not_mix_in_anchors_built_under_another_exclusion_rule(self):
        def named(general, candidate):
            return {'general_giving': {'excluded_funding_sources': general}, 'candidate_receipts': {'excluded_funding_sources': candidate}}
        elsewhere = {'before_the_rule': {'v': 'no list at all', 'general_giving': {'cycles': {}}, 'candidate_receipts': {'cycles': {}}},
                     'empty_list': {'v': 'names no source', **named([], [])},
                     'another_bucket': {'v': 'left out another bucket', **named(['pac_activity'], ['pac_activity'])},
                     'general_only': {'v': 'the candidate block predates the rule', 'general_giving': {'excluded_funding_sources': ['non_giving']}, 'candidate_receipts': {'cycles': {}}},
                     'candidate_differs': {'v': 'the candidate block names another rule', **named(['non_giving'], [])},
                     'not_a_list': {'v': 'a string, not a list', **named('non_giving', 'non_giving')}}
        old = {'schema_version': 1, 'kind': 'giving-anchors', 'unavailable': {}, 'industries': {'same_rule': {'v': 'built under the rule', **self.RULED}, **elsewhere, 'redone': {'v': 'old'}}}
        new = {'schema_version': 1, 'kind': 'giving-anchors', 'industries': {'redone': {'v': 'rebuilt', **self.RULED}}, 'unavailable': {}}
        with tempfile.TemporaryDirectory() as out:
            path = Path(out) / 'published.json'
            path.write_text(json.dumps(old))
            merged = bga.merge_with_published(new, path, ['redone'])
            self.assertEqual(sorted(merged['industries']), ['redone', 'same_rule'])               # only an industry built under THIS rule is carried over ...
            self.assertEqual(merged['industries']['redone']['v'], 'rebuilt')
            self.assertEqual(merged['unavailable'], {k: bga.OTHER_RULE_REASON for k in elsewhere})   # ... the others are refused with the reason until they are rebuilt
            self.assertIn('different exclusion rule', bga.OTHER_RULE_REASON)
            with patch.object(ms, 'FORECAST_EXCLUDED_FUNDING_SOURCES', frozenset()):                # the comparison is with the CURRENT rule: now nothing is left out
                merged = bga.merge_with_published(new, path, ['redone'])
            self.assertEqual(sorted(merged['industries']), ['empty_list', 'redone'])
            self.assertIn('same_rule', merged['unavailable'])

    def test_a_partial_build_does_not_drop_an_industry_reported_unavailable_by_the_full_build(self):
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            args = list(self._run(out))
            _final, _receipt, full = bga.run_build(*args)
            self.assertIn('property_casualty', full['unavailable'])
            args[2] = ['commercial_banks']
            final, _receipt, partial = bga.run_build(*args)
            self.assertEqual(json.loads(final.read_text(encoding='utf-8'))['unavailable'], full['unavailable'])
            self.assertEqual(list(partial['industries']), ['commercial_banks'])

    def test_an_industry_with_no_lda_sector_or_roster_is_unavailable_per_measure(self):
        self.world.registry['industries'][0].update(lda_sector=None, pac_roster=[])
        snap, _ = self.world.build()
        ind = snap['industries']['commercial_banks']
        self.assertEqual(ind['lda']['status'], 'unavailable')
        self.assertIn('No Melange-LDA sector', ind['lda']['reason'])
        self.assertEqual(ind['pac_to_candidates']['status'], 'unavailable')
        self.assertEqual(ind['status']['general_giving'], 'ready')
        self.assertEqual(bga.validate_snapshot(snap), [])

    def test_a_missing_index_for_the_published_workbook_is_refused_with_a_reason(self):
        self.world.workbook.write_bytes(b'republished workbook v2')            # its sha no longer matches the index
        snap, _ = self.world.build()
        self.assertEqual(snap['industries'], {})
        self.assertIn('No contribution-analytics index matches the published workbook', snap['unavailable']['commercial_banks'])

    def test_the_newest_index_of_the_published_workbook_is_used(self):
        self.world._index(built_at=1790000100.0, token='u' * 64)
        snap, _ = self.world.build()
        self.assertEqual(snap['industries']['commercial_banks']['mentat']['index_token'], 'u' * 64)

    # --- contract ---------------------------------------------------------------------------------------------------------------
    def test_the_built_snapshot_satisfies_the_shared_schema_and_invariants(self):
        snap, _ = self.world.build()
        self.assertEqual(contract.validate_kind(snap, 'anchors'), [])
        self.assertEqual(contract.check_anchor_invariants(snap), [])

    def test_every_anchor_negative_control_fails_against_the_built_snapshot_and_the_example(self):
        snap, _ = self.world.build()
        for label, doc in (('built', snap), ('example', contract.example('anchors'))):
            for control in (c for c in contract.negative_controls() if c['target'] == 'anchors'):
                path = list(control['path'])
                ident = 'commercial_banks'
                path = [ident if step == '<industry>' else step for step in path]
                mutated = contract.apply_mutation(doc, {**control, 'path': path})
                problems = contract.validate_kind(mutated, 'anchors')
                if control['kind'] == 'invariant' and not problems:
                    problems = contract.check_anchor_invariants(mutated)
                with self.subTest(document=label, control=control['id']):
                    self.assertNotEqual(problems, [], control['why'])

    # --- guarantees -------------------------------------------------------------------------------------------------------------
    def _hashes(self):
        out = {}
        for folder in (self.world.project, self.world.report, self.world.fec):
            for path in sorted(folder.rglob('*')):
                if path.is_file():
                    out[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        for name in ('sector_spend.csv', 'fullscope_receipts.json', '00_controls.xlsx'):
            out[name] = hashlib.sha256((self.world.base / name).read_bytes()).hexdigest()
        return out

    def _run(self, out, run_id=None):
        registry_path = Path(out) / 'registry.json'
        registry_path.write_text(json.dumps(self.world.registry))
        return (self.world.project, Path(out) / 'state', None, registry_path, self.world.fec, self.world.base / 'sector_spend.csv',
                self.world.base / '00_controls.xlsx', self.world.report, run_id, self.world.options['lda_receipts'])

    def test_the_build_changes_no_input_file(self):
        before = self._hashes()
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            final, receipt, snapshot = bga.run_build(*self._run(out))
            self.assertEqual(self._hashes(), before)
            self.assertEqual(receipt['snapshot_sha256'], hashlib.sha256(final.read_bytes()).hexdigest())
            self.assertEqual(json.loads(final.read_text(encoding='utf-8')), snapshot)
            self.assertEqual(sorted(p.name for p in final.parent.iterdir()), sorted([bga.RECEIPT_NAME, bga.SNAPSHOT_NAME]))   # no temp file left behind

    def test_sqlite_connections_cannot_write(self):
        db = ms.ro_connect(self.world.index)
        self.addCleanup(db.close)
        with self.assertRaises(sqlite3.OperationalError):
            db.execute('CREATE TABLE should_fail(x)')
        with self.assertRaises(sqlite3.OperationalError):
            db.execute('DELETE FROM facts')
        self.assertIn('immutable=1', ms.ro_uri(self.world.index))

    def test_an_invalid_build_leaves_the_previous_snapshot_in_place(self):
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            args = self._run(out)
            final, _receipt, _snap = bga.run_build(*args)
            good = final.read_bytes()
            with patch.object(bga, 'validate_snapshot', return_value=['forced problem']):
                with self.assertRaises(ValueError):
                    bga.run_build(*args)
            self.assertEqual(final.read_bytes(), good)
            self.world.workbook.write_bytes(b'republished workbook v2')    # no industry can be anchored any more
            with self.assertRaises(ValueError):
                bga.run_build(*args)
            self.assertEqual(final.read_bytes(), good)

    def test_glass_box_events_are_emitted_for_a_run(self):
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            bga.run_build(*self._run(out, 'test-run'))
            events = [json.loads(p.read_text()) for p in sorted((Path(out) / 'gb').glob('evt-*.json'))]
            self.assertEqual(events[0]['kind'], 'run.start')
            self.assertEqual(events[-1]['kind'], 'run.finish')
            self.assertEqual({e['run_id'] for e in events}, {'test-run'})
            self.assertEqual({e['source'] for e in events}, {'giving-forecast-anchors'})
            self.assertTrue(all(e['parent_id'] in ('', events[0]['id']) for e in events))

    def test_check_reports_a_republished_workbook_as_stale(self):
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            final, _r, _s = bga.run_build(*self._run(out))
            self.assertEqual(bga.check_fresh(final, self.world.project, self.world.report), {'commercial_banks': 'fresh'})
            self.world.workbook.write_bytes(b'republished workbook v2')
            self.assertEqual(bga.check_fresh(final, self.world.project, self.world.report), {'commercial_banks': 'stale'})
            self.world.workbook.unlink()
            self.assertEqual(bga.check_fresh(final, self.world.project, self.world.report), {'commercial_banks': 'missing'})

    def test_a_fec_file_that_differs_from_its_source_receipt_makes_the_pac_measure_unavailable(self):
        (self.world.fec / 'fec_beneficiary' / 'pas224' / 'itpas2.txt').write_text('tampered\n')
        snap, _ = self.world.build()
        pac = snap['industries']['commercial_banks']['pac_to_candidates']
        self.assertEqual(pac['status'], 'unavailable')
        self.assertIn('differs from its source receipt', pac['reason'])
        self.assertEqual(snap['industries']['commercial_banks']['general_giving']['status'], 'ready')


class ExclusionWorld(World):
    """The World plus non_giving receipts in every place the forecast sums money, and receipts that must keep counting.
    NO_SOURCE ids have no row in the pinned source database, so they are unmatched in the chamber join."""
    FACTS = World.FACTS + [
        (16, '2023-2024', '2024-10-10', 'D', 'non_giving', 12000, 'S'),          # anchor cycle, Senate D: left out of the 2024 Senate base
        (17, '2023-2024', '2024-10-11', 'I', 'employee_original', 700, 'S'),     # an ordinary other-party receipt: still counts
        (18, '2025-2026', '2025-04-20', 'D', 'non_giving', 4000, 'S'),           # open cycle, inside the observed window, Senate D
        (19, '2025-2026', '2025-08-05', 'R', 'non_giving', 1500, 'Q'),           # open cycle, not a House / Senate committee: general giving only
        (20, '2025-2026', '2026-07-25', 'R', 'non_giving', 900, 'H'),            # open cycle, after the coverage end (2026-06-30)
        (23, '2025-2026', '2026-02-02', 'D', 'employee_original', 1100, 'H'),    # no source row: counted, outside the chamber split
        (24, '2025-2026', '2026-02-03', 'D', 'non_giving', 1300, 'H'),           # no source row and non_giving: counted nowhere
        (25, '2019-2020', '2020-05-05', 'D', 'non_giving', 5000, 'H'),           # a cycle whose only receipt is non_giving: its entries stay, at zero
    ]
    NO_SOURCE = frozenset({23, 24})


class ControlWorld(ExclusionWorld):
    """ExclusionWorld without its non_giving receipts: the forecast basis of the two must be identical."""
    FACTS = [f for f in ExclusionWorld.FACTS if f[4] != 'non_giving']


class ExclusionTests(unittest.TestCase):
    """Owner ruling 2026-10-01 (D4, option b): receipts the accounting engine buckets as non_giving are left out of the forecast's general-giving levels,
    observed cuts and employee candidate bases (in the forecast only), and the amounts left out are reported."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = ExclusionWorld(self.tmp.name)
        self.snap, _ = self.world.build()
        self.ind = self.snap['industries']['commercial_banks']

    def cut(self, block, through):
        return next(c for c in block['observed'] if c['through'] == through)

    def test_general_giving_levels_leave_out_non_giving_and_say_what_was_left_out(self):
        g = self.ind['general_giving']
        # 2024: 15,049 ordinary + 7 other-party; the $25 and $120 non_giving receipts are left out. 2026: 2,800 + 11 (a receipt with no source row still counts).
        # 2020: its only receipt is non_giving, so the cycle keeps its entry at zero.
        self.assertEqual(g['cycles'], {'2020': 0.0, '2022': 700.0, '2024': 15056.0, '2026': 2811.0})
        self.assertEqual(g['excluded_non_giving'], {'2020': 50.0, '2022': 0.0, '2024': 145.0, '2026': 77.0})
        self.assertEqual(g['excluded_funding_sources'], ['non_giving'])
        self.assertEqual(g['by_funding_source']['2024'], {'employee_original': 15056.0, 'non_giving': 145.0})     # the split of the Analysis Amount stays complete
        self.assertEqual(g['by_funding_source']['2026'], {'employee_original': 2811.0, 'non_giving': 77.0})
        for cycle, level in g['cycles'].items():
            self.assertEqual(round(level + g['excluded_non_giving'][cycle], 2), round(sum(g['by_funding_source'].get(cycle, {}).values()), 2))

    def test_observed_cuts_leave_out_non_giving_inside_the_window_and_after_it(self):
        g = self.ind['general_giving']
        by = {c['through']: c['amount'] for c in g['observed']}
        self.assertEqual(by['2025-03-31'], 300.0)
        self.assertEqual(by['2025-04-30'], 300.0)        # the $40 non_giving receipt of 2025-04-20 is not observed
        self.assertEqual(by['2025-08-31'], 300.0)        # nor the $15 one of 2025-08-05 (not a candidate committee, still left out of general giving)
        self.assertEqual(by['2025-09-30'], 700.0)
        self.assertEqual(by['2026-01-31'], 1300.0)
        self.assertEqual(by['2026-02-28'], 1311.0)       # the $11 receipt without a source row counts; the $13 non_giving one beside it does not
        self.assertEqual(by['2026-06-30'], 2011.0)
        # the open cycle holds every counted row to date: observed + what is dated after the coverage end ($800; the $9 non_giving one is not part of it)
        self.assertEqual(g['cycles']['2026'], by['2026-06-30'] + 800.0)
        self.assertTrue(any('dated after the coverage end ($800.00)' in f for f in self.ind['coverage']['flags']))

    def test_candidate_bases_leave_out_non_giving_and_say_what_was_left_out(self):
        c = self.ind['candidate_receipts']
        self.assertEqual(c['cycles']['2024']['House'], {'D': 950.0, 'R': 2000.0, 'other': 0.0})
        self.assertEqual(c['cycles']['2024']['Senate'], {'D': 500.0, 'R': 1500.0, 'other': 7.0})              # the ordinary other-party receipt counts, the $120 D one does not
        self.assertEqual(c['excluded_non_giving']['2024'], {'House': {'D': 0.0, 'R': 0.0, 'other': 25.0}, 'Senate': {'D': 120.0, 'R': 0.0, 'other': 0.0}})
        self.assertEqual(c['cycles']['2026'], {'House': {'D': 1000.0, 'R': 400.0, 'other': 0.0}, 'Senate': {'D': 800.0, 'R': 600.0, 'other': 0.0}})
        self.assertEqual(c['excluded_non_giving']['2026'], {'House': {'D': 0.0, 'R': 9.0, 'other': 0.0}, 'Senate': {'D': 40.0, 'R': 0.0, 'other': 0.0}})
        self.assertEqual(c['excluded_funding_sources'], ['non_giving'])
        self.assertEqual(self.cut(c, '2025-04-30')['Senate'], {'D': 0.0, 'R': 0.0, 'other': 0.0})             # the non_giving Senate receipt of 2025-04-20 is not observed
        self.assertEqual(self.cut(c, '2026-06-30')['House'], {'D': 1000.0, 'R': 400.0, 'other': 0.0})         # the July non_giving House receipt is after the coverage end
        self.assertEqual(self.cut(c, '2026-06-30')['Senate'], {'D': 0.0, 'R': 600.0, 'other': 0.0})
        # a cycle whose only receipt is non_giving keeps its entry (zeros), flagged thin, so a consumer reads "no giving" rather than "no data"
        self.assertEqual(c['cycles']['2020'], {'House': {'D': 0.0, 'R': 0.0, 'other': 0.0}, 'Senate': {'D': 0.0, 'R': 0.0, 'other': 0.0}})
        self.assertEqual(c['excluded_non_giving']['2020']['House'], {'D': 50.0, 'R': 0.0, 'other': 0.0})
        self.assertEqual(c['thin_base']['2020'], {'House': True, 'Senate': True})

    def test_the_forecast_basis_is_identical_to_a_world_without_the_non_giving_receipts(self):
        with tempfile.TemporaryDirectory() as other:
            control, _ = ControlWorld(other).build()
        ctl = control['industries']['commercial_banks']

        def without_2020(per_cycle):          # the all-non_giving cycle exists only in the world that has those receipts
            return {cycle: value for cycle, value in per_cycle.items() if cycle != '2020'}
        self.assertEqual(without_2020(self.ind['general_giving']['cycles']), ctl['general_giving']['cycles'])
        self.assertEqual(self.ind['general_giving']['observed'], ctl['general_giving']['observed'])
        self.assertEqual(without_2020(self.ind['candidate_receipts']['cycles']), ctl['candidate_receipts']['cycles'])
        self.assertEqual(self.ind['candidate_receipts']['observed'], ctl['candidate_receipts']['observed'])
        self.assertEqual(without_2020(self.ind['candidate_receipts']['thin_base']), ctl['candidate_receipts']['thin_base'])
        self.assertEqual(ctl['general_giving']['excluded_non_giving'], {'2022': 0.0, '2024': 0.0, '2026': 0.0})     # nothing left out: nothing to report, no flag either
        self.assertFalse(any('Excluded from the forecast' in f for f in ctl['flags']))

    def test_the_flag_says_the_amounts_are_excluded_from_the_forecast_per_cycle(self):
        flag = next(f for f in self.ind['flags'] if f.startswith('Excluded from the forecast (owner ruling 2026-10-01)'))
        self.assertIn("receipts Mentat's accounting engine marks non-giving - FEC-reported interest, refunds, insurance claims and loans - ", flag)
        self.assertIn('$50.00 in 2019-2020; $145.00 in 2023-2024; $77.00 in 2025-2026 to date', flag)
        self.assertIn("They stay in the published workbook's Analysis Amount", flag)
        self.assertFalse(any('stay inside the Analysis Amount' in f or 'reported, not changed' in f for f in self.ind['flags']))
        self.assertIn('1 included facts ($11.00) have no source row', ' '.join(self.ind['flags']))        # only the counted unmatched receipt: the $13 non_giving one is not counted

    def test_the_manifest_check_still_compares_the_full_analysis_amount(self):
        cuts = fec_pas2.month_ends(2025, '2026-06-30')
        agg = ms.index_aggregates(self.world.index, self.world.source_db, 2026, cuts)
        db = ms.ro_connect(self.world.index)
        try:
            manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
        finally:
            db.close()
        self.assertEqual(agg['analysis_cycles'], {k.split('-')[-1]: v for k, v in manifest['receipt_cycles'].items()})
        for cycle, total in agg['analysis_cycles'].items():
            self.assertEqual(agg['cycles'][cycle] + agg['excluded'][cycle], total)           # counted + left out = the published Analysis Amount, in exact cents
        self.assertEqual(agg['excluded'], {'2020': 5000, '2022': 0, '2024': 14500, '2026': 7700})

    def test_a_tampered_index_is_refused_even_when_only_a_non_giving_receipt_changes(self):
        """The trust check compares the index with its own manifest on the FULL Analysis Amount (counted + left out), so a change confined to a receipt the
        forecast leaves out is caught like any other, and so is a manifest that no longer matches its index."""
        def edit_index(world, sql):
            db = sqlite3.connect(world.index)                      # a writable handle on this test's own copy of the index
            try:
                db.execute(sql)
                db.commit()
            finally:
                db.close()

        def edit_manifest(world, delta):
            db = sqlite3.connect(world.index)
            try:
                manifest = json.loads(db.execute("SELECT value FROM metadata WHERE key='manifest'").fetchone()[0])
                manifest['receipt_cycles']['2023-2024'] += delta
                db.execute("UPDATE metadata SET value=? WHERE key='manifest'", (json.dumps(manifest),))
                db.commit()
            finally:
                db.close()
        cases = {'a non_giving receipt +1 cent (sub_id 8)': lambda w: edit_index(w, "UPDATE facts SET receipt_cents = receipt_cents + 1 WHERE source_id = '8'"),
                 'a non_giving receipt deleted (sub_id 8)': lambda w: edit_index(w, "DELETE FROM facts WHERE source_id = '8'"),
                 'an ordinary receipt +1 cent (sub_id 1)': lambda w: edit_index(w, "UPDATE facts SET receipt_cents = receipt_cents + 1 WHERE source_id = '1'"),
                 'the manifest total +1 cent': lambda w: edit_manifest(w, 1)}
        for label, tamper in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                world = ExclusionWorld(tmp)
                tamper(world)
                snap, _ = world.build()
                self.assertEqual(snap['industries'], {})
                self.assertIn('not trustworthy', snap['unavailable']['commercial_banks'])
        with tempfile.TemporaryDirectory() as tmp:                 # control: the same world, untouched, is trusted
            snap, _ = ExclusionWorld(tmp).build()
            self.assertIn('commercial_banks', snap['industries'])

    def test_the_contract_wants_both_blocks_to_report_the_exclusion_completely(self):
        def problems(edit):
            doc = copy.deepcopy(self.snap)
            edit(doc['industries']['commercial_banks'])
            return contract.validate_kind(doc, 'anchors') or contract.check_anchor_invariants(doc)
        self.assertEqual(problems(lambda i: None), [])
        # a block that names what it left out but not the amounts (or the reverse) is refused, in either block
        self.assertTrue(any('excluded_funding_sources is reported without excluded_non_giving' in p for p in problems(lambda i: i['general_giving'].pop('excluded_non_giving'))))
        self.assertTrue(any('excluded_non_giving is reported without excluded_funding_sources' in p for p in problems(lambda i: i['general_giving'].pop('excluded_funding_sources'))))
        self.assertTrue(any('candidate_receipts: excluded_funding_sources is reported without' in p for p in problems(lambda i: i['candidate_receipts'].pop('excluded_non_giving'))))
        # the two blocks must agree on whether the exclusion is reported
        self.assertTrue(any('must both report' in p for p in problems(lambda i: [i['candidate_receipts'].pop(k) for k in ('excluded_funding_sources', 'excluded_non_giving')])))
        self.assertTrue(any('must both report' in p for p in problems(lambda i: [i['general_giving'].pop(k) for k in ('excluded_funding_sources', 'excluded_non_giving')])))
        # the candidate block reports one entry per candidate cycle, and the same excluded sources as the general-giving block
        self.assertTrue(any('its cycles differ from the candidate cycles' in p for p in problems(lambda i: i['candidate_receipts']['excluded_non_giving'].pop('2022'))))
        self.assertTrue(any('its cycles differ from the candidate cycles' in p for p in problems(lambda i: i['candidate_receipts']['excluded_non_giving'].update({'1999': i['candidate_receipts']['excluded_non_giving']['2022']}))))
        self.assertTrue(any('differs from general_giving.excluded_funding_sources' in p for p in problems(lambda i: i['candidate_receipts'].update(excluded_funding_sources=[]))))

    def test_the_stored_label_and_the_module_docstring_describe_the_exclusion(self):
        label = self.ind['general_giving']['label']
        self.assertIn("all funding sources except those Mentat's accounting engine marks non-giving", label)
        self.assertNotIn('without the receipts', label)            # it used to say "all funding sources ... without the receipts ...": self-contradictory
        self.assertNotIn('all channels', bga.__doc__)              # PAC measure (2) is the Analysis Amount LESS the non_giving receipts, not "all channels"
        self.assertIn('less the non_giving receipts', bga.__doc__)

    def test_the_rule_is_one_named_constant_that_drives_every_sum(self):
        self.assertEqual(ms.FORECAST_EXCLUDED_FUNDING_SOURCES, frozenset({'non_giving'}))
        with patch.object(ms, 'FORECAST_EXCLUDED_FUNDING_SOURCES', frozenset()):                 # nothing excluded: every receipt counts again (the pre-ruling basis)
            snap, _ = self.world.build()
        ind = snap['industries']['commercial_banks']
        self.assertEqual(ind['general_giving']['cycles'], {'2020': 50.0, '2022': 700.0, '2024': 15201.0, '2026': 2888.0})
        self.assertEqual(ind['general_giving']['excluded_funding_sources'], [])
        self.assertEqual(ind['general_giving']['excluded_non_giving'], {'2020': 0.0, '2022': 0.0, '2024': 0.0, '2026': 0.0})
        self.assertEqual(self.cut(ind['general_giving'], '2026-06-30')['amount'], 2079.0)         # 2,011 + the $40, $15 and $13 non_giving receipts inside the window
        self.assertEqual(ind['candidate_receipts']['cycles']['2024']['Senate']['D'], 620.0)
        self.assertEqual(ind['candidate_receipts']['cycles']['2024']['House']['other'], 25.0)
        self.assertEqual(self.cut(ind['candidate_receipts'], '2025-04-30')['Senate']['D'], 40.0)
        self.assertTrue(any('dated after the coverage end ($809.00)' in f for f in ind['coverage']['flags']))
        self.assertFalse(any('Excluded from the forecast' in f for f in ind['flags']))

    def test_exactly_one_condition_holds_for_every_receipt(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE f(funding_source TEXT, receipt_cents INTEGER)')
        db.executemany('INSERT INTO f VALUES (?, ?)', [(None, 1), ('', 2), ('non_giving', 4), ('employee_original', 8), ("it's", 16)])

        def sums():
            counted, excluded = ms.forecast_conditions('f')
            return [db.execute(f'SELECT COALESCE(SUM(receipt_cents), 0) FROM f WHERE {cond}').fetchone()[0] for cond in (counted, excluded)]
        self.assertEqual(sums(), [27, 4])                                                        # a NULL or empty source counts; only the named bucket is left out
        with patch.object(ms, 'FORECAST_EXCLUDED_FUNDING_SOURCES', frozenset({"it's", 'non_giving'})):
            self.assertEqual(sums(), [11, 20])                                                   # names are quoted, not interpolated raw
        with patch.object(ms, 'FORECAST_EXCLUDED_FUNDING_SOURCES', frozenset()):
            self.assertEqual(sums(), [31, 0])

    def test_the_snapshot_validates_and_its_exclusion_adds_up(self):
        self.assertEqual(contract.validate_kind(self.snap, 'anchors'), [])
        self.assertEqual(contract.check_anchor_invariants(self.snap), [])
        legacy = copy.deepcopy(self.snap)                                                        # a snapshot from before the ruling carries none of the new fields
        for block in ('general_giving', 'candidate_receipts'):
            for ind in legacy['industries'].values():
                del ind[block]['excluded_funding_sources'], ind[block]['excluded_non_giving']
        self.assertEqual(contract.validate_kind(legacy, 'anchors'), [])
        self.assertEqual(contract.check_anchor_invariants(legacy), [])

        def problems(edit):
            doc = copy.deepcopy(self.snap)
            edit(doc['industries']['commercial_banks']['general_giving'])
            return contract.validate_kind(doc, 'anchors') or contract.check_anchor_invariants(doc)
        self.assertTrue(any('excluded_non_giving' in p for p in problems(lambda g: g['excluded_non_giving'].update({'2024': g['excluded_non_giving']['2024'] + 1}))))
        self.assertTrue(any('cycles + excluded_non_giving' in p for p in problems(lambda g: g['cycles'].update({'2024': g['cycles']['2024'] + 1}))))
        self.assertTrue(any('cycles + excluded_non_giving' in p for p in problems(lambda g: g['by_funding_source']['2024'].update({'non_giving': 0.0}))))
        self.assertTrue(problems(lambda g: g.update(excluded_non_giving=[1.0])))                  # typed: a per-cycle object of numbers
        self.assertTrue(problems(lambda g: g.update(excluded_funding_sources='non_giving')))      # typed: a list of names

    def test_the_full_build_passes_the_receipt_totals_check_and_writes_both_files(self):
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            registry_path = Path(out) / 'registry.json'
            registry_path.write_text(json.dumps(self.world.registry))
            final, receipt, snapshot = bga.run_build(self.world.project, Path(out) / 'state', None, registry_path, self.world.fec,
                                                     self.world.base / 'sector_spend.csv', self.world.base / '00_controls.xlsx', self.world.report,
                                                     lda_receipts=self.world.options['lda_receipts'])
            self.assertEqual(list(snapshot['industries']), ['commercial_banks'])            # not refused as "index not trustworthy" although levels differ from the manifest
            self.assertEqual(sorted(p.name for p in final.parent.iterdir()), sorted([bga.RECEIPT_NAME, bga.SNAPSHOT_NAME]))
            self.assertEqual(receipt['snapshot_sha256'], hashlib.sha256(final.read_bytes()).hexdigest())
            self.assertEqual(snapshot['industries']['commercial_banks']['general_giving']['excluded_non_giving']['2024'], 145.0)


class OddYearTests(unittest.TestCase):
    """The odd-year split of every COMPLETE cycle (cycle model, 2026-10-07): the receipts dated on or before December 31 of the cycle's first year, same population and
    basis as the cycle totals, for general giving (forecast basis) and for the roster PACs (D-049 aggregation)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.world = World(self.tmp.name)
        H, S = 'H2AA00001', 'S2BB00002'
        for yy, extra in (('24', [pas2_line('C00000001', '24K', 700, H, '11152023', 'O1', 100, 1010),            # odd year: House D
                                  pas2_line('C00000002', '24Z', 300, S, '12312023', 'O2', 100, 1011),            # odd year, the last day: Senate R (in kind)
                                  pas2_line('C00000001', '24K', 900, H, '01012024', 'O3', 100, 1012)]),          # the first day of the even year: not odd
                          ('22', [pas2_line('C00000001', '24K', 200, H, '12202021', 'O4', 50, 5010)])):
            folder = self.world.fec / 'fec_beneficiary' / ('pas2' + yy)
            path = folder / 'itpas2.txt'
            path.write_text(path.read_text(encoding='latin-1') + '\n'.join(extra) + '\n', encoding='latin-1')
            (folder / 'source-receipt.json').write_text(json.dumps({'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'last_modified': 'Sun, 06 Sep 2026 15:55:17 GMT'}))
        self.snap, _ = self.world.build()
        self.ind = self.snap['industries']['commercial_banks']

    def test_general_giving_odd_year_is_the_receipts_dated_in_the_first_year_of_each_complete_cycle(self):
        g = self.ind['general_giving']
        self.assertEqual(g['odd_year'], {'2022': 0.0, '2024': 1000.0})        # FACTS: sub 1 (2023-05-10) is the only 2023 receipt; the 2022 receipt is dated 2022-06-01
        self.assertNotIn('2026', g['odd_year'])                                  # the open cycle has no odd-year split
        for cycle, amount in g['odd_year'].items():
            self.assertLessEqual(amount, g['cycles'][cycle])

    def test_pac_odd_year_is_the_d049_aggregation_cut_at_december_31_of_the_first_year(self):
        odd = self.ind['pac_to_candidates']['odd_year']
        self.assertEqual(odd['2024'], {'House': {'D': 700.0, 'R': 0.0, 'other': 0.0}, 'Senate': {'D': 0.0, 'R': 300.0, 'other': 0.0}, 'to_house_senate': 1000.0})
        self.assertEqual(odd['2022']['to_house_senate'], 200.0)
        self.assertEqual(sorted(odd), ['2022', '2024'])
        year = self.ind['pac_to_candidates']['cycles']['2024']
        self.assertEqual(year['to_house_senate'], 3200.0 + 700.0 + 300.0 + 900.0)            # the extra rows are in the cycle total; only the dated-odd ones in the odd split

    def test_the_snapshot_validates_and_an_industry_without_a_roster_has_no_pac_odd_year(self):
        self.assertEqual(contract.validate_kind(self.snap, 'anchors'), [])
        self.assertEqual(contract.check_anchor_invariants(self.snap), [])
        pc = self.snap['industries'].get('property_casualty')
        if pc is not None:
            self.assertNotIn('odd_year', pc['pac_to_candidates'])

    def test_an_undated_receipt_is_in_the_cycle_total_and_in_no_odd_year_amount(self):
        agg = ms.index_aggregates(self.world.index, self.world.source_db, 2026, ['2026-06-30'])
        self.assertEqual(agg['odd_year'], {'2022': 0, '2024': 100000})                          # cents; the open cycle (2026) is not a complete cycle
        db = sqlite3.connect(self.world.index)
        db.execute("UPDATE facts SET date = '' WHERE source_id = '1'")
        db.commit()
        db.close()
        agg = ms.index_aggregates(self.world.index, self.world.source_db, 2026, ['2026-06-30'])
        self.assertEqual(agg['odd_year']['2024'], 0)
        self.assertEqual(agg['cycles']['2024'], 1504900)                                          # the blank-dated receipt is still in the cycle total


class PartsTests(unittest.TestCase):
    def test_an_undated_fec_row_is_in_the_cycle_total_and_in_no_observed_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'itpas2.txt'
            write_text(path, [pas2_line('C00000001', '24K', 1000, 'H2AA00001', '', 'U1', 1, 9001),             # no usable date
                              pas2_line('C00000001', '24K', 200, 'H2AA00001', '03152026', 'U2', 1, 9002)])
            out = fec_pas2.aggregate_file(path, '26', {('H2AA00001', '26'): 'DEM'}, {'C00000001': ['x']}, {'x': ['2026-02-28', '2026-03-31']})['x']
            self.assertEqual(out['totals']['all_recipients'], 120000)
            self.assertEqual(out['cuts']['2026-02-28']['all_recipients'], 0)
            self.assertEqual(out['cuts']['2026-03-31']['all_recipients'], 20000)
            self.assertEqual(out['undated_cents'], 100000)

    def test_coverage_end_is_the_last_quarter_whose_report_deadline_passed(self):
        cases = {'2026-09-09': '2026-06-30', '2026-07-28': '2026-06-30', '2026-07-14': '2026-03-31', '2026-07-15': '2026-06-30',
                 '2026-10-14': '2026-06-30', '2026-10-15': '2026-09-30', '2027-01-14': '2026-09-30', '2027-01-15': '2026-12-31', '2026-04-15': '2026-03-31'}
        for collected, expected in cases.items():
            with self.subTest(collected=collected):
                self.assertEqual(coverage.rule_coverage_end(collected).isoformat(), expected)

    def test_cut_back_applies_only_while_the_rule_end_is_the_one_it_names(self):
        rule = {'when_rule_coverage_end': '2026-06-30', 'coverage_end': '2026-05-31', 'reason': 'June incomplete'}
        self.assertEqual(coverage.coverage('2026-07-28', rule)['coverage_end'], '2026-05-31')
        self.assertEqual(coverage.coverage('2026-10-20', rule)['coverage_end'], '2026-09-30')     # a later collection is not cut back
        self.assertIsNone(coverage.coverage('2026-10-20', rule)['reason'])

    def test_month_ends_include_leap_february_and_stop_at_the_last_date(self):
        ends = fec_pas2.month_ends(2023, '2024-03-31')
        self.assertEqual(ends[0], '2023-01-31')
        self.assertIn('2024-02-29', ends)
        self.assertEqual(ends[-1], '2024-03-31')

    def test_d049_counts_the_latest_filing_of_an_identity_once(self):
        def row(rid, sub, file_num, amount, tran='T1', memo=''):
            return dict(rid=rid, cmte_id='C1', rpt_tp='Q1', tran_id=tran, sub_id=sub, file_num=file_num, amndt_ind='N', cand_id='H1', other_id='',
                        amount_cents=amount, date='2024-03-15', transaction_tp='24K', memo_cd=memo, order=rid)
        out = d049.resolve([row(0, '1', 10, 500), row(1, '2', 11, 500), row(2, '3', 11, 700, tran='T9', memo='X')])
        included = [rid for rid, v in out.items() if v['include']]
        self.assertEqual(included, [1])                     # the same identity re-filed later supersedes; a memo row never counts
        self.assertEqual(out[0]['status'], 'superseded_filing_version')
        self.assertEqual(out[2]['status'], 'memo_only')

    def test_a_reused_transaction_id_with_a_different_identity_is_a_different_transaction(self):
        def row(rid, sub, amount, date):
            return dict(rid=rid, cmte_id='C1', rpt_tp='Q1', tran_id='SE.5', sub_id=sub, file_num=10, amndt_ind='N', cand_id='H1', other_id='',
                        amount_cents=amount, date=date, transaction_tp='24K', memo_cd='', order=rid)
        out = d049.resolve([row(0, '1', 500, '2024-03-15'), row(1, '2', 900, '2024-09-15')])
        self.assertTrue(all(v['include'] for v in out.values()))

    def test_lda_partial_year_needs_filings_and_never_adds_the_two_series(self):
        head = 'sector,year,period,is_partial_year,distinct_organizations,filings,income_total,expenses_total'
        rows = [dict(zip(head.split(','), line.split(','))) for line in (
            'x,2025,annual,false,1,4,100,900', 'x,2026,annual,true,1,2,40,80', 'x,2026,Q1,true,1,1,10,20', 'x,2026,Q2,true,1,1,30,60', 'x,2026,Q3,true,0,0,0,0')]
        levels = lda_mod.sector_levels(rows, 'x', AFTER_EVERY_DEADLINE)
        self.assertEqual(levels['series']['income_total']['partial']['quarters'], {'Q1': 10.0, 'Q2': 30.0})
        self.assertEqual(levels['series']['expenses_total']['annual'], {'2025': 900.0})
        self.assertNotIn('total_both', levels['series'])
        self.assertIsNone(lda_mod.sector_levels(rows, 'missing', AFTER_EVERY_DEADLINE))

    def test_an_early_filing_before_the_ld2_deadline_does_not_extend_the_partial_year(self):
        # crypto, 2026: Q1 and Q2 filed, and one early $0 filing for Q3 (its LD-2 deadline is October 20), as in Melange's output of 2026-10-07
        head = 'sector,year,period,is_partial_year,distinct_organizations,filings,income_total,expenses_total'.split(',')
        row = lambda *values: dict(zip(head, values))       # noqa: E731
        income = {2023: (100, 110, 120, 130), 2024: (140, 150, 160, 170), 2025: (180, 190, 200, 210)}
        rows = []
        for year, values in income.items():
            rows.append(row('crypto', str(year), 'annual', 'false', '5', '40', str(sum(values)), str(2 * sum(values))))
            rows += [row('crypto', str(year), 'Q%d' % (i + 1), 'false', '5', '10', str(v), str(2 * v)) for i, v in enumerate(values)]
        rows += [row('crypto', '2026', 'annual', 'true', '5', '21', '620', '1240'), row('crypto', '2026', 'Q1', 'true', '5', '10', '300', '600'),
                 row('crypto', '2026', 'Q2', 'true', '5', '10', '320', '640'), row('crypto', '2026', 'Q3', 'true', '1', '1', '0', '0'), row('crypto', '2026', 'Q4', 'true', '0', '0', '0', '0')]
        swept = lda_mod.sector_levels(rows, 'crypto', date(2026, 8, 1))                                      # the producer's sweep date: Q3 is not due yet
        for sid, (q1, q2) in (('income_total', (300.0, 320.0)), ('expenses_total', (600.0, 640.0))):
            partial = swept['series'][sid]['partial']
            self.assertEqual((partial['through_quarter'], partial['quarters'], partial['total']), ('Q2', {'Q1': q1, 'Q2': q2}, q1 + q2), sid)
            self.assertEqual((partial['nowcast']['through_quarter'], partial['nowcast']['observed']), ('Q2', q1 + q2), sid)
        self.assertEqual(swept['through'], '2026-06-30')
        for as_of, last in ((date(2026, 10, 19), 'Q2'), (date(2026, 10, 20), 'Q3')):                          # the deadline day itself counts (<=), the day before does not
            with self.subTest(as_of=as_of.isoformat()):
                levels = lda_mod.sector_levels(rows, 'crypto', as_of)
                self.assertEqual({sid: s['partial']['through_quarter'] for sid, s in levels['series'].items()}, {'income_total': last, 'expenses_total': last})
                self.assertEqual(levels['through'], '2026-06-30' if last == 'Q2' else '2026-09-30')
        early_q4 = [row('crypto', '2026', 'Q4', 'true', '1', '1', '5', '7')]                                  # Q4 is due January 20 of the next year
        self.assertEqual(lda_mod.quarter_table(early_q4, 'crypto', 'income_total', date(2027, 1, 19)), {})
        self.assertEqual(lda_mod.quarter_table(early_q4, 'crypto', 'income_total', date(2027, 1, 20)), {2026: {4: 5.0}})


# Melange LDA bank income_total by quarter (Q1..Q4), the data of the 2026-10-08 blind-test study of the lobbying nowcast.
BANK_INCOME = {2021: (4553000., 4462500., 4533000., 4627000.), 2022: (4558500., 4623500., 4664000., 4794000.), 2023: (4940000., 4945500., 5329500., 5292500.),
               2024: (5049500., 5170000., 5071532., 5048000.), 2025: (5232000., 5933000., 6066000., 6593000.)}


class NowcastTests(unittest.TestCase):
    """The in-year lobbying nowcast pred = O + (1 - s) B (owner rulings 2026-10-08) and the blind errors that bound it."""
    QUARTERS = {year: {i + 1: v for i, v in enumerate(values)} for year, values in BANK_INCOME.items()}

    def test_the_nowcast_reproduces_a_row_of_the_blind_test_study(self):
        # study row: bank / income_total / cut Q2 / target 2025: s = 0.4923, O = 11,165,000, B = 20,339,032, prediction 21,491,225 against 23,824,000 filed (-9.79%)
        now = lda_mod.nowcast(self.QUARTERS, 2025, 2)
        self.assertEqual((now['observed'], now['prior_total'], now['share_years'], now['through_quarter']), (11165000., 20339032., [2022, 2023, 2024], 'Q2'))
        self.assertAlmostEqual(now['share'], 0.4923, places=4)
        self.assertLessEqual(abs(now['point'] - 21491225), 1)
        self.assertAlmostEqual(now['point'], now['observed'] + (1 - now['share']) * now['prior_total'], places=6)          # the remaining-share form
        self.assertGreater(abs(now['point'] - now['observed'] / now['share']), 1e5)                                       # and not the pace O / s
        self.assertAlmostEqual(math.log(now['point'] / sum(BANK_INCOME[2025])), math.log(1 - 0.0979), places=3)

    def test_the_share_uses_up_to_three_prior_years_and_at_least_one(self):
        self.assertEqual(lda_mod.nowcast(self.QUARTERS, 2025, 1)['share_years'], [2022, 2023, 2024])
        self.assertEqual(lda_mod.nowcast(self.QUARTERS, 2022, 3)['share_years'], [2021])                                   # only 2021 is on file before 2022
        self.assertEqual(lda_mod.nowcast(self.QUARTERS, 2023, 2)['share_years'], [2021, 2022])
        only = lda_mod.nowcast(self.QUARTERS, 2022, 3)
        self.assertAlmostEqual(only['share'], sum(BANK_INCOME[2021][:3]) / sum(BANK_INCOME[2021]), places=12)
        self.assertIsNone(lda_mod.nowcast(self.QUARTERS, 2021, 2))                                                         # no prior full year, no nowcast
        thin = {y: dict(q) for y, q in self.QUARTERS.items()}
        del thin[2025][2]
        self.assertIsNone(lda_mod.nowcast(thin, 2025, 2))                                                                  # the cut quarter is not filed yet

    def test_the_live_nowcast_rides_on_the_partial_year(self):
        head = 'sector,year,period,is_partial_year,distinct_organizations,filings,income_total,expenses_total'
        rows = []
        for year, values in BANK_INCOME.items():
            rows.append(dict(zip(head.split(','), ['x', str(year), 'annual', 'false', '1', '4', str(sum(values)), str(2 * sum(values))])))
            for i, v in enumerate(values):
                rows.append(dict(zip(head.split(','), ['x', str(year), 'Q%d' % (i + 1), 'false', '1', '1', str(v), str(2 * v)])))
        rows += [dict(zip(head.split(','), ['x', '2026', 'Q1', 'true', '1', '1', '6000000', '12000000'])), dict(zip(head.split(','), ['x', '2026', 'Q2', 'true', '1', '1', '6500000', '13000000'])),
                 dict(zip(head.split(','), ['x', '2026', 'Q3', 'true', '0', '0', '0', '0']))]
        now = lda_mod.sector_levels(rows, 'x', AFTER_EVERY_DEADLINE)['series']['income_total']['partial']['nowcast']
        self.assertEqual((now['year'], now['through_quarter'], now['observed'], now['prior_total'], now['share_years']), (2026, 'Q2', 12500000., sum(BANK_INCOME[2025]), [2023, 2024, 2025]))
        self.assertAlmostEqual(now['point'], now['observed'] + (1 - now['share']) * sum(BANK_INCOME[2025]), places=6)
        self.assertEqual(contract.check_anchor_invariants(self._snapshot_with(lda_mod.sector_levels(rows, 'x', AFTER_EVERY_DEADLINE))), [])

    @staticmethod
    def _snapshot_with(levels):
        snapshot = contract.example('anchors')
        snapshot['industries']['commercial_banks']['lda']['series'] = levels['series']
        return snapshot

    def test_the_invariants_catch_a_nowcast_that_is_not_the_formula(self):
        for edit, needle in ((lambda n: n.update(point=n['point'] + 1000.), 'point is not observed'), (lambda n: n.update(share=n['share'] * 0.99), 'geometric mean'),
                             (lambda n: n.update(observed=n['observed'] + 1000.), 'observed differs'), (lambda n: n.update(prior_total=n['prior_total'] + 1000.), 'prior_total differs')):
            snapshot = contract.example('anchors')
            edit(snapshot['industries']['commercial_banks']['lda']['series']['income_total']['partial']['nowcast'])
            with self.subTest(needle=needle):
                self.assertTrue(any(needle in problem for problem in contract.check_anchor_invariants(snapshot)))

    def test_the_blind_errors_are_pooled_by_cut_with_a_split_conformal_80th_percentile_and_the_largest_miss(self):
        head = 'sector,year,period,is_partial_year,distinct_organizations,filings,income_total,expenses_total'
        rows = []
        for year, values in BANK_INCOME.items():
            for i, v in enumerate(values):
                rows.append(dict(zip(head.split(','), ['x', str(year), 'Q%d' % (i + 1), 'false', '1', '1', str(v), str(v)])))
        errors = lda_mod.nowcast_errors(rows, AFTER_EVERY_DEADLINE, pool=('x',))
        self.assertEqual(errors['pool'], ['x'])
        self.assertEqual(errors['targets'], [2022, 2023, 2024, 2025])
        self.assertEqual({cut: row['n'] for cut, row in errors['by_cut'].items()}, {'1': 8, '2': 8, '3': 8})            # 4 target years x 2 series
        self.assertAlmostEqual(errors['by_cut']['2']['max'], abs(math.log(21491225.2107 / sum(BANK_INCOME[2025]))), places=6)         # the 2025 miss of the study row above
        self.assertEqual(errors['by_cut']['2']['q80'], errors['by_cut']['2']['max'])                                      # ceil((8 + 1) 0.8) = 8th of 8
        self.assertIsNone(lda_mod.nowcast_errors(rows[:8], AFTER_EVERY_DEADLINE, pool=('x',)))                                            # one target year: too few blind tests, no bands
        self.assertEqual(lda_mod.conformal_quantile([0.3, -0.1, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0], 0.8), 0.9)       # ceil(11 x 0.8) = 9th smallest |e|
        self.assertIsNone(lda_mod.conformal_quantile([0.1, 0.2], 0.8))


@unittest.skipUnless(os.environ.get('MENTAT_ANCHORS_REAL'), 'set MENTAT_ANCHORS_REAL=1 (credit unions) or =all (banks and property & casualty too) to read the real Mentat tree')
class RealMentatTests(unittest.TestCase):
    """ADDENDUM section F, reproduced from the real published workbooks, indexes and FEC files (read-only)."""
    PROJECT = Path(os.environ.get('MENTAT_PROJECT_ROOT') or r'C:\Users\pgoff\Mentat\mentat')

    def build(self, ident):
        with tempfile.TemporaryDirectory() as out, patch.dict(os.environ, {'ANCHORS_GLASSBOX_DIR': str(Path(out) / 'gb')}):
            _final, _receipt, snap = bga.run_build(self.PROJECT, Path(out) / 'state', [ident])
        return snap['industries'][ident]

    # Section F as published on 2026-09-30, less the receipts the accounting engine buckets as non_giving (owner ruling 2026-10-01, D4 option b). The amounts left out
    # were measured separately from the index and the pinned source databases (credit unions: SchoolsFirst FCU's interest on Katie Porter's committee accounts).
    def test_credit_unions_reproduce_section_f_without_non_giving(self):
        ind = self.build('credit_unions')
        self.assertEqual(ind['coverage']['coverage_end'], '2026-05-31')
        self.assertEqual(ind['general_giving']['cycles'], {'2022': 258944.62, '2024': 620046.88, '2026': 270483.65})                 # section F: 369423.16 / 779088.35 / 273494.14
        self.assertEqual(ind['general_giving']['excluded_non_giving'], {'2022': 110478.54, '2024': 159041.47, '2026': 3010.49})
        self.assertEqual(ind['general_giving']['observed'][-1]['amount'], 269130.67)                                                  # section F: 272141.16
        house, senate = ind['candidate_receipts']['cycles']['2024']['House'], ind['candidate_receipts']['cycles']['2024']['Senate']
        self.assertEqual((house['D'], house['R'], senate['D'], senate['R']), (30832.81, 24291.13, 17284.95, 16751.75))               # section F Senate D: 176326.42
        self.assertEqual(ind['candidate_receipts']['excluded_non_giving']['2024']['Senate']['D'], 159041.47)                          # all of it sits in the Senate employee D base
        self.assertEqual(ind['candidate_receipts']['cycles']['2022']['House']['D'], 14421.13)                                         # section F: 124899.67
        self.assertEqual(ind['candidate_receipts']['excluded_non_giving']['2022']['House']['D'], 110478.54)
        self.assertEqual(ind['candidate_receipts']['thin_base']['2024'], {'House': True, 'Senate': True})                             # the Senate base was not thin while it held the interest
        self.assertEqual(ind['pac_to_candidates']['cycles']['2024']['to_house_senate'], 80800.0)
        self.assertEqual(ind['pac_to_candidates']['observed'][-1]['to_house_senate'], 54500.0)
        self.assertEqual(ind['general_giving']['odd_year']['2024'], 123767.83)                                                        # SPEC 2.4: the odd-year (2023) share of the 2024 cycle
        self.assertEqual(ind['pac_to_candidates']['odd_year']['2024']['to_house_senate'], 15000.0)

    @unittest.skipUnless(os.environ.get('MENTAT_ANCHORS_REAL') == 'all', 'banks need the 861 MB join: MENTAT_ANCHORS_REAL=all')
    def test_banks_reproduce_section_f_without_non_giving(self):
        ind = self.build('commercial_banks')
        self.assertEqual(ind['coverage']['coverage_end'], '2026-06-30')
        self.assertEqual(ind['general_giving']['cycles']['2022'], 16652458.77)
        self.assertEqual(ind['general_giving']['cycles']['2024'], 25915482.79)                                                        # section F: 26107204.16
        self.assertEqual(ind['general_giving']['excluded_non_giving'], {'2022': 0.0, '2024': 191721.37, '2026': 67940.35})
        self.assertEqual(ind['general_giving']['observed'][-1]['amount'], 11498864.42)                                                # section F: 11566804.77
        self.assertEqual(ind['candidate_receipts']['cycles']['2024']['Senate'], {'D': 751277.47, 'R': 795893.13, 'other': 36101.0})   # none of it reached a House / Senate committee
        self.assertEqual(ind['pac_to_candidates']['cycles']['2024']['to_house_senate'], 5692500.0)
        self.assertEqual(ind['pac_to_candidates']['observed'][-1]['to_house_senate'], 4673000.0)
        self.assertEqual(ind['general_giving']['odd_year']['2024'], 6408717.29)                                                       # SPEC 2.4
        self.assertEqual(ind['pac_to_candidates']['odd_year']['2024']['to_house_senate'], 2903000.0)

    @unittest.skipUnless(os.environ.get('MENTAT_ANCHORS_REAL') == 'all', 'property & casualty: MENTAT_ANCHORS_REAL=all')
    def test_property_casualty_leaves_out_its_non_giving_receipts(self):
        ind = self.build('property_casualty')
        self.assertEqual(ind['general_giving']['cycles'], {'2022': 5156891.21, '2024': 7762490.48, '2026': 3532480.89})              # 5240339.66 / 7857445.42 / 3589580.51 before
        self.assertEqual(ind['general_giving']['excluded_non_giving'], {'2022': 83448.45, '2024': 94954.94, '2026': 57099.62})
        self.assertEqual(ind['general_giving']['observed'][-1]['amount'], 3532180.89)                                                 # 3589280.51 before
        senate = ind['candidate_receipts']['cycles']['2024']['Senate']
        self.assertEqual((senate['D'], senate['R'], senate['other']), (88516.96, 136741.02, 52.0))                                    # D was 159839.94: Progressive's claim payments to Fetterman for PA
        self.assertEqual(ind['candidate_receipts']['excluded_non_giving']['2024']['Senate'], {'D': 71322.98, 'R': 4609.88, 'other': 5197.68})
        self.assertEqual(ind['general_giving']['odd_year']['2024'], 2238275.03)                                                       # SPEC 2.4


if __name__ == '__main__':
    unittest.main()
