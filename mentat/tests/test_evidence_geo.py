"""S8: the district geography behind the PAC evidence map.

Fixture-only by default: every test here writes its own data root, its own
boundary files and its own snapshot, so nothing depends on the built
app/geo/*.json or on the FEC bulk masters. The few tests that do read the
shipped files skip when they are absent.

Run: python mentat/tests/test_evidence_geo.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from decimal import Decimal
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))

import district_geo  # noqa: E402
import evidence_csv  # noqa: E402
import evidence_geo  # noqa: E402

CONTRACT = (Path(__file__).resolve().parents[2] / 'audits' / 'mentat_failings_20260915'
            / 'contracts' / 'evidence-geo.json')


# --------------------------------------------------------------- the fixtures

def write_geo(root):
    """A three-district map, two states and a four-ZIP crosswalk."""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'index.json').write_text(json.dumps({
        'width': 100, 'height': 60, 'built_at': 'fixture',
        'cycles': {'2022': '118', '2024': '118', '2026': '119'},
        'vintages': {
            '118': {'file': 'cd118.json', 'cycle': 2024, 'congress': '118th',
                    'zcta_file': 'zcta_cd118.json', 'zcta_basis': 'land area'},
            '119': {'file': 'cd119.json', 'cycle': 2026, 'congress': '119th',
                    'zcta_file': 'zcta_cd119.json', 'zcta_basis': 'land area'}}}), encoding='utf-8')
    for vintage in ('118', '119'):
        (root / f'cd{vintage}.json').write_text(json.dumps({
            'width': 100, 'height': 60,
            'districts': {'CA-12': {'path': 'M0 0L1 0L1 1Z', 'centroid': [1, 1]},
                          'CA-45': {'path': 'M2 0L3 0L3 1Z', 'centroid': [2, 1]},
                          'NY-16': {'path': 'M4 0L5 0L5 1Z', 'centroid': [4, 1]}},
            'states': {'CA': {'path': 'M0 0L3 0L3 2Z'}, 'NY': {'path': 'M4 0L6 0L6 2Z'}}}),
            encoding='utf-8')
        (root / f'zcta_cd{vintage}.json').write_text(json.dumps({
            '94607': [['CA-12', 1.0]],
            '92625': [['CA-45', 0.6], ['CA-12', 0.4]],
            '10024': [['NY-16', 1.0]],
            '99999': [['ZZ-99', 1.0]]}), encoding='utf-8')


def write_masters(root):
    """A candidate master and a committee master with a state that MOVED."""
    (root / 'fec_beneficiary' / 'cn24').mkdir(parents=True, exist_ok=True)
    (root / 'fec_beneficiary' / 'cn24' / 'cn.txt').write_text(
        'H0CA48198|STEEL, MICHELLE|REP|2024|CA|H|45|I|C|C00700000|||\n'
        'S4CA00522|PORTER, KATHERINE|DEM|2024|CA|S|00|C|C|C00700001|||\n'
        'H2NY16000|BOWMAN, JAMAAL|DEM|2024|NY|H|16|I|C|C00700002|||\n'
        'H9TX66000|NOSUCH, SEAT|REP|2024|TX|H|66|C|C|C00700003|||\n'
        'P0US00000|CANDIDATE, PRESIDENTIAL|DEM|2024|US|P|00|C|C|C00700004|||\n',
        encoding='utf-8')
    (root / 'fec_committee_master' / 'cm24').mkdir(parents=True, exist_ok=True)
    (root / 'fec_committee_master' / 'cm24' / 'cm.txt').write_text(
        'C00848440|PROTECT PROGRESS|TRES|ST1|ST2|PALM CITY|FL|34990|U|O|UNK|Q|||\n'
        'C00836221|DEFEND AMERICAN JOBS|TRES|ST1|ST2|ARLINGTON|VA|22201|U|O|UNK|Q|||\n',
        encoding='utf-8')
    (root / 'fec_committee_master' / 'cm26').mkdir(parents=True, exist_ok=True)
    (root / 'fec_committee_master' / 'cm26' / 'cm.txt').write_text(
        'C00848440|PROTECT PROGRESS|TRES|ST1|ST2|ST. PAUL|MN|55116|U|O|UNK|Q|||\n',
        encoding='utf-8')


def disbursement(line, amount, **fields):
    row = {'line_number': line, 'disbursement_amount': amount, 'memo_code': '',
           'memoed_subtotal': False, 'recipient_name': '', 'recipient_committee_id': '',
           'recipient_state': '', 'recipient_zip': '', 'disbursement_description': '',
           'candidate_id': '', 'sub_id': str(abs(hash((line, amount))) % 10 ** 12)}
    row.update(fields)
    return row


def expenditure(amount, candidate, direction, **fields):
    row = {'expenditure_amount': amount, 'candidate_id': candidate,
           'support_oppose_indicator': direction, 'candidate_name': candidate,
           'memo_code': '', 'memoed_subtotal': False,
           'sub_id': str(abs(hash((amount, candidate, direction))) % 10 ** 12)}
    row.update(fields)
    return row


def receipt(amount, **fields):
    row = {'contribution_receipt_amount': amount, 'contributor_name': 'A DONOR',
           'contributor_zip': '', 'contributor_state': '', 'memo_code': '',
           'memoed_subtotal': False, 'receipt_type_full': '', 'memo_text': '',
           'sub_id': str(abs(hash((amount, fields.get('contributor_zip')))) % 10 ** 12)}
    row.update(fields)
    return row


def write_snapshot(root, identifier='fixture-snap'):
    """One committee, one cycle, with E, B22, B23, B21B and a conversion twin."""
    directory = root / identifier
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = {
        'committee_id': 'C00835959', 'cycle': 2024,
        'receipts': [
            receipt(1000, contributor_zip='94607', contributor_state='CA', contributor_name='COINBASE'),
            receipt(2000, contributor_zip='92625', contributor_state='CA'),
            receipt(500, contributor_zip='', contributor_state='NY'),
            receipt(250, contributor_zip='', contributor_state=''),
            receipt(9999, contributor_zip='94607', contributor_state='CA', memo_code='X'),
            receipt(7000, contributor_zip='94607', contributor_state='CA', contributor_name='COINBASE',
                    receipt_type_full='IN-KIND - BITCOIN - NOT LIQUIDATED')],
        'disbursements': [
            disbursement('22', 5000, recipient_committee_id='C00848440', recipient_name='PROTECT PROGRESS'),
            disbursement('22', 3000, recipient_committee_id='C00836221', recipient_name='DEFEND AMERICAN JOBS'),
            disbursement('23', 1500, recipient_committee_id='C00700000', recipient_name='STEEL FOR CONGRESS',
                         candidate_id='H0CA48198'),
            disbursement('21B', 800, recipient_name='SCHOEN SURVEY RESEARCH', recipient_state='NY',
                         recipient_zip='10024', disbursement_description='POLLING'),
            disbursement('21B', 7000, recipient_name='COINBASE', recipient_state='CA',
                         recipient_zip='94607',
                         disbursement_description='IN-KIND - BITCOIN - NOT LIQUIDATED'),
            disbursement('21B', 400, recipient_name='NO STATE VENDOR'),
            disbursement('22', 99999, recipient_committee_id='C00848440', memo_code='X')],
        'expenditures': [
            expenditure(12000, 'S4CA00522', 'O'),
            expenditure(4000, 'H0CA48198', 'S'),
            expenditure(2000, 'H2NY16000', 'O'),
            expenditure(700, 'H9TX66000', 'S'),
            expenditure(300, 'P0US00000', 'S'),
            expenditure(55555, 'S4CA00522', 'O', memo_code='X')],
        'totals': {'disbursements': 36700.0, 'receipts': 10750.0}}
    later = {'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [],
             'disbursements': [disbursement('22', 100, recipient_committee_id='C00848440',
                                            recipient_name='PROTECT PROGRESS')],
             'expenditures': [], 'totals': {'disbursements': 100.0, 'receipts': 0.0}}
    (directory / 'snapshots.json').write_text(json.dumps([snapshot, later]), encoding='utf-8')
    (directory / 'evidence.json').write_text(json.dumps({
        'version': 1, 'company': 'Coinbase', 'aliases': ['COINBASE'],
        'committee_finances': [{'committee_id': 'C00835959', 'cycle': 2024,
                                'committee_name': 'FAIRSHAKE'}]}), encoding='utf-8')
    return directory


IMPORT_CSV = '\n'.join([
    'Committee ID,Cycle,Contributor,Amount,Date,State,ZIP,Record Type,Recipient',
    'C00804179,2023-2024,"ONE, PERSON",100,2024-01-02,CA,94607,CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"TWO, PERSON",200,2024-01-03,CA,92625,CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"THREE, PERSON",50,2024-01-04,NY,,CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"FOUR, PERSON",25,2024-01-05,,,CONTRIBUTION,COINBASE PAC',
    'C00804179,2025-2026,"FIVE, PERSON",400,2026-01-05,CA,94607,CONTRIBUTION,COINBASE PAC',
    '']) + '\n'


class GeoFixture(unittest.TestCase):
    """Every test in this file runs against a data root it wrote itself."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_geo(self.root / 'geo')
        write_masters(self.root / 'inputs')
        self.saved = {name: os.environ.get(name) for name in ('MENTAT_GEO_DIR', 'MENTAT_INPUTS_DIR')}
        os.environ['MENTAT_GEO_DIR'] = str(self.root / 'geo')
        os.environ['MENTAT_INPUTS_DIR'] = str(self.root / 'inputs')
        district_geo.reset_caches()
        self.addCleanup(self.restore)
        self.snapshot_root = self.root / 'snapshots'
        write_snapshot(self.snapshot_root)

    def restore(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        district_geo.reset_caches()
        self.temp.cleanup()

    def store(self):
        store = evidence_csv.Store(self.root / 'data', evict=False)
        job = store.wait(store.ingest_text('Crypto Industry Spending Overview.csv', IMPORT_CSV)['id'])
        self.assertEqual(job['state'], 'ready', job.get('message'))
        return store, job['id']

    def snapshot_spend(self, cycle='2024'):
        return evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', cycle, 'spent',
                                      snapshot_root=self.snapshot_root)


class SnapshotSpend(GeoFixture):
    def test_saved_filing_links_survive_map_and_network_aggregation(self):
        snapshot = self.snapshot_root / 'fixture-snap/snapshots.json'
        units = json.loads(snapshot.read_text())
        for schedule in ('receipts', 'disbursements', 'expenditures'):
            for index, row in enumerate(units[0][schedule]):
                row['image_number'] = str(202409210000000000 + index)
        snapshot.write_text(json.dumps(units))
        for direction in ('raised', 'spent'):
            payload = evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', '2024', direction,
                                             snapshot_root=self.snapshot_root)
            records = [record for kind in payload['kinds']
                       for place in kind['districts'] + kind['states']
                       for recipient in place['recipients'] for record in recipient['filings']]
            self.assertTrue(records)
            self.assertTrue(all(record['source_url'].startswith('https://docquery.fec.gov/cgi-bin/fecimg/?')
                                for record in records))
            self.assertFalse(any(record['amount'] in (9999, 99999, 55555) for record in records))
            self.assertEqual(payload['reported_total'], 10750 if direction == 'raised' else 36700)
        # Split ZIPs keep the original receipt and its URL on each matching district.
        buckets, *_ = evidence_geo.snapshot_raised(units[0], '2024')
        placed = buckets['contributors'].payload('')['districts']
        split = [record for place in placed for recipient in place['recipients']
                 for record in recipient['filings'] if record['amount'] == 2000]
        self.assertEqual(len(split), 2)
        self.assertEqual(split[0], split[1])

    def test_view_cache_invalidates_snapshot_and_reference_data(self):
        evidence_geo._views.clear()
        def read():
            return evidence_geo.cached_geography('snapshot','fixture-snap','C00835959','2024','spent',snapshot_root=self.snapshot_root)
        first=read()
        self.assertEqual(read(),first)
        self.assertEqual(evidence_geo._views.info()['hits'],1)
        # Same directory, changed candidate seat: both the view and the
        # underlying reference-data cache must refresh.
        master=self.root/'inputs/fec_beneficiary/cn24/cn.txt'
        master.write_text(master.read_text().replace('|CA|H|45|','|CA|H|12|'))
        moved=read()
        old=next(k for k in first['kinds'] if k['kind']=='candidates')
        new=next(k for k in moved['kinds'] if k['kind']=='candidates')
        self.assertNotEqual(old['districts'],new['districts'])
        self.assertEqual(first['reported_total'],moved['reported_total'])
        snapshot=self.snapshot_root/'fixture-snap/snapshots.json'
        payload=json.loads(snapshot.read_text())
        payload[0]['totals']['disbursements']+=1
        snapshot.write_text(json.dumps(payload))
        self.assertEqual(read()['reported_total'],first['reported_total']+1)

    def test_import_cache_with_geocoder_is_stable_and_read_only(self):
        import geocode
        store,identifier=self.store()
        geocode.Cache(self.root/'data')
        evidence_geo._views.clear()
        args=('csv',identifier,'C00804179','2024','raised')
        first=evidence_geo.cached_geography(*args,store=store,data_root=self.root/'data')
        self.assertEqual(first,evidence_geo.cached_geography(*args,store=store,data_root=self.root/'data'))
        self.assertEqual(evidence_geo._views.info()['hits'],1)

    def test_four_kinds_are_present_with_their_bases(self):
        payload = self.snapshot_spend()
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        self.assertEqual(set(kinds), {'candidates', 'transfers', 'operating', 'conversions'})
        # State view defaults to all spend, including asset conversions.
        self.assertTrue(kinds['conversions']['default_on'])
        self.assertTrue(all(kinds[name]['default_on'] for name in ('candidates', 'transfers', 'operating')))
        self.assertTrue(all(kinds[name]['basis'] for name in kinds))

    def test_located_plus_unlocated_equals_reported(self):
        payload = self.snapshot_spend()
        self.assertAlmostEqual(payload['located_total'] + payload['unlocated_total'],
                               payload['reported_total'], places=2)
        self.assertTrue(payload['reconciles'])

    def test_residual_is_visible_but_does_not_claim_source_reconciliation(self):
        original=evidence_geo.network_spent
        def gap(*args,**kwargs):
            buckets,missing,total=original(*args,**kwargs)
            return buckets,missing,total+Decimal('1')
        with patch.object(evidence_geo,'network_spent',side_effect=gap):
            payload=self.snapshot_spend()
        self.assertFalse(payload['reconciles'])
        self.assertEqual(payload['reconciliation_residual'],1)
        self.assertEqual(payload['located_total']+payload['unlocated_total'],payload['reported_total'])

    def test_selected_root_snapshot_is_not_replaced_by_a_newer_saved_run(self):
        expected=self.snapshot_spend()['reported_total']
        later=write_snapshot(self.snapshot_root,'newer')
        units=json.loads((later/'snapshots.json').read_text())
        units[0]['totals']['coverage_end_date']='2024-12-31'
        units[0]['totals']['disbursements']=999999
        (later/'snapshots.json').write_text(json.dumps(units))
        payload=self.snapshot_spend()
        self.assertEqual(payload['reported_total'],expected)
        root=next(r for r in payload['downstream_sources'] if r['committee']=='C00835959')
        self.assertIn('fixture-snap',root['source'])

    def test_schedule_e_support_and_oppose_stay_apart(self):
        payload = self.snapshot_spend()
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        districts = {row['key']: row for row in kinds['candidates']['districts']}
        self.assertAlmostEqual(districts['CA-SEN']['oppose'], 12000, places=2)
        self.assertAlmostEqual(districts['CA-SEN']['support'], 0, places=2)
        # Both land in the district, but only Schedule E carries an explicit
        # support direction. The 1,500 gift remains in total candidate activity.
        self.assertAlmostEqual(districts['CA-45']['support'], 4000, places=2)
        self.assertAlmostEqual(districts['CA-45']['amount'], 5500, places=2)
        self.assertAlmostEqual(districts['NY-16']['oppose'], 2000, places=2)

    def test_memo_rows_are_excluded_exactly_as_pac_evidence_does(self):
        payload = self.snapshot_spend()
        everything = sum(kind['total'] for kind in payload['kinds'])
        self.assertNotIn(55555, [row['amount'] for kind in payload['kinds']
                                 for row in kind['districts'] + kind['states']])
        self.assertLess(everything, 55555)

    def test_transfers_use_the_cycle_s_committee_state_not_the_newest(self):
        payload = self.snapshot_spend()
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        states = {row['key']: row for row in kinds['transfers']['states']}
        # C00848440 is FL in the 2024 master and MN in the 2026 one.
        self.assertIn('FL', states)
        self.assertNotIn('MN', states)
        self.assertAlmostEqual(states['FL']['amount'], 5000, places=2)
        self.assertAlmostEqual(states['VA']['amount'], 3000, places=2)

    def test_conversions_are_their_own_kind_and_leave_operating_clean(self):
        payload = self.snapshot_spend()
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        self.assertAlmostEqual(kinds['conversions']['total'], 7000, places=2)
        self.assertAlmostEqual(kinds['operating']['total'], 800, places=2)

    def test_a_seat_no_map_carries_and_a_payee_with_no_state_are_unlocated(self):
        payload = self.snapshot_spend()
        reasons = ' '.join(item['reason'] for item in payload['unlocated'])
        amounts = sum(item['amount'] for item in payload['unlocated'])
        self.assertIn('TX-66', reasons)
        self.assertGreaterEqual(amounts, 700 + 300 + 400)

    def test_every_district_key_matches_the_contract_pattern(self):
        payload = self.snapshot_spend()
        for kind in payload['kinds']:
            for row in kind['districts']:
                self.assertRegex(row['key'], r'^[A-Z]{2}-(\d{2}|AL|SEN)$')
            for row in kind['states']:
                self.assertRegex(row['key'], r'^[A-Z]{2}$')

    def test_shares_are_inside_zero_to_one(self):
        payload = self.snapshot_spend()
        for kind in payload['kinds']:
            for row in kind['districts'] + kind['states']:
                self.assertGreaterEqual(row['share'], 0.0)
                self.assertLessEqual(row['share'], 1.0)


class SnapshotRaise(GeoFixture):
    def test_receipts_go_by_zip_and_state(self):
        payload = evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', '2024', 'raised',
                                         snapshot_root=self.snapshot_root)
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        districts = {row['key']: row for row in kinds['contributors']['districts']}
        self.assertAlmostEqual(districts['CA-12']['amount'], 1000 + 7000 + 2000 * 0.4, places=2)
        self.assertAlmostEqual(districts['CA-45']['amount'], 2000 * 0.6, places=2)
        self.assertTrue(districts['CA-45']['split'])
        self.assertFalse(districts['NY-16']['amount'] if 'NY-16' in districts else False)
        states = {row['key']: row for row in kinds['contributors']['states']}
        self.assertIn('NY', states)
        self.assertAlmostEqual(payload['located_total'] + payload['unlocated_total'],
                               payload['reported_total'], places=2)


class ImportGeography(GeoFixture):
    def test_import_uses_complete_worksheet_filing_records_without_a_snapshot(self):
        store, identifier = self.store()
        records = [{'amount':2500,'source_url':'https://docquery.fec.gov/cgi-bin/fecimg/?202401010000000001'},
                   {'amount':1500,'source_url':'https://docquery.fec.gov/cgi-bin/fecimg/?202401010000000002'}]
        activity = [{'cycle':'2024','committee':'C00804179','candidate_id':'H0CA48198',
                     'candidate':'STEEL, MICHELLE','direction':'Support','amount':Decimal(4000),
                     'filings':records}]
        with patch('graph_data.report_activity', return_value=activity), patch(
                'downstream.inventory', side_effect=AssertionError('worksheet citations need no snapshot')):
            payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'spent',
                                             store=store, snapshot_root=self.snapshot_root)
        recipient = payload['kinds'][0]['districts'][0]['recipients'][0]
        self.assertEqual(recipient['filings'], records)

    def test_old_report_candidate_total_uses_only_exact_matched_saved_filings(self):
        store, identifier = self.store()
        unit = json.loads((self.snapshot_root / 'fixture-snap/snapshots.json').read_text())[0]
        unit['committee_id'] = 'C00804179'
        unit['_source'] = 'saved fixture'
        unit['expenditures'] = [
            expenditure(value, 'H0CA48198', 'S', image_number=str(202501309748316995 + index))
            for index, value in enumerate((178200, 31050, 190750))]
        unit['disbursements'] = [
            disbursement('23', 5000, candidate_id='H0CA48198',
                         sub_id=str(202401299600546010 + index),
                         image_number=str(202401299600546010 + index))
            for index in range(2)]
        activity = [
            {'cycle':'2024','committee':'C00804179','candidate_id':'H0CA48198',
             'candidate':'FITZPATRICK, BRIAN','direction':direction,'amount':Decimal(value),
             'filings':[]}
            for direction, value in (('Support', 400000), ('Contribution / Other', 10000))]
        with patch('graph_data.report_activity', return_value=activity), patch(
                'downstream.inventory', return_value={('2024','C00804179'): unit}):
            payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'spent',
                                             store=store, snapshot_root=self.snapshot_root)
            recipient = next(row for place in payload['kinds'][0]['districts']
                             for row in place['recipients'] if row['name'] == 'FITZPATRICK, BRIAN')
            self.assertEqual(recipient['amount'], 410000)
            self.assertEqual(len(recipient['filings']), 5)
            self.assertEqual(sum(row['amount'] for row in recipient['filings']), 410000)
            self.assertTrue(all(row['source_url'].startswith('https://docquery.fec.gov/cgi-bin/fecimg/?')
                                for row in recipient['filings']))
            activity[0]['amount'] += 1
            mismatched = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'spent',
                                                store=store, snapshot_root=self.snapshot_root)
            recipient = next(row for place in mismatched['kinds'][0]['districts']
                             for row in place['recipients'] if row['name'] == 'FITZPATRICK, BRIAN')
            self.assertEqual(recipient['amount'], 410001)
            self.assertEqual(recipient['filings'], [])

    def test_raised_splits_a_straddling_zip_and_marks_it(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised', store=store)
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        districts = {row['key']: row for row in kinds['contributors']['districts']}
        self.assertAlmostEqual(districts['CA-12']['amount'], 100 + 200 * 0.4, places=2)
        self.assertAlmostEqual(districts['CA-45']['amount'], 200 * 0.6, places=2)
        self.assertTrue(districts['CA-45']['split'])
        self.assertAlmostEqual(payload['reported_total'], 375, places=2)

    def test_a_row_with_no_zip_falls_back_to_its_state_then_to_unlocated(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised', store=store)
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        states = {row['key']: row for row in kinds['contributors']['states']}
        self.assertAlmostEqual(states['NY']['amount'], 50, places=2)
        self.assertAlmostEqual(sum(item['amount'] for item in payload['unlocated']), 25, places=2)
        self.assertAlmostEqual(payload['located_total'] + payload['unlocated_total'],
                               payload['reported_total'], places=2)

    def test_the_cycle_narrows_the_rows(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2026', 'raised', store=store)
        self.assertAlmostEqual(payload['reported_total'], 400, places=2)

    def test_spent_says_plainly_when_the_import_carries_no_outbound_rows(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'spent', store=store)
        self.assertEqual(payload['kinds'], [])
        self.assertTrue(payload['reason'])
        self.assertIn('outbound', payload['reason'].lower())


class CycleToVintage(GeoFixture):
    def test_the_payload_names_the_vintage_it_drew(self):
        payload = self.snapshot_spend('2024')
        self.assertEqual(payload['vintage'], '118')
        self.assertEqual(payload['cycle'], '2024')

    def test_2026_draws_the_119th(self):
        payload = self.snapshot_spend('2026')
        self.assertEqual(payload['vintage'], '119')


class ShippedIndex(unittest.TestCase):
    """The shipped index.json, not a fixture. Skips when it is not built."""

    def setUp(self):
        district_geo.reset_caches()
        self.addCleanup(district_geo.reset_caches)
        if not (APP / 'geo' / 'index.json').is_file():
            self.skipTest('mentat/app/geo/index.json is not built in this checkout')

    def test_2022_is_drawn_on_the_118th_map(self):
        # Correction to S6_DESIGN ruling 9, accepted by Paul 2026-09-17: the
        # 2021-2022 candidate master already numbers House seats on the 118th
        # map (it carries NC-14), so money in the 2022 cycle belongs on 118th
        # districts even though the sitting members sat on 116th-map seats.
        self.assertEqual(district_geo.vintage_for_cycle('2021-2022'), '118')
        self.assertEqual(district_geo.vintage_for_cycle(2022), '118')

    def test_2024_and_2026_are_unchanged(self):
        self.assertEqual(district_geo.vintage_for_cycle(2024), '118')
        self.assertEqual(district_geo.vintage_for_cycle(2026), '119')

    def test_the_index_records_why_2022_moved(self):
        payload = json.loads((APP / 'geo' / 'index.json').read_text(encoding='utf-8'))
        notes = ' '.join(payload.get('notes') or [])
        self.assertIn('NC-14', notes)
        self.assertNotIn('OPEN QUESTION', notes)


class Contract(GeoFixture):
    """The contract's examples are asserted against the producer, with controls."""

    def setUp(self):
        super().setUp()
        if not CONTRACT.is_file():
            self.skipTest(f'{CONTRACT.name} is not written yet')
        self.contract = json.loads(CONTRACT.read_text(encoding='utf-8'))

    def test_the_contract_is_v3_and_names_the_route(self):
        # v2 was S8b: ruling A (no blank cycle), the per-row `basis`, and the
        # three raised-row counters. P4b's map needs geography wider than a
        # district, so v3 adds by_state, by_region, by_zip and `sides`, all
        # additive: every v2 field and every v2 rule is still here, and the v2
        # negative controls still fail for their own stated reasons. The five
        # examples were regenerated from the producer at the same time, so an
        # example cannot record a shape the route no longer emits.
        # v4 (lane C, 2026-09-25) documents lane B's four fields and item 7 of
        # F4, additively; test_evidence_geo_contract_v4.py asserts it.
        self.assertEqual(self.contract['version'], 4)
        self.assertEqual(self.contract['route'], '/api/evidence-geo')
        self.assertIn('RULING A', ' '.join(self.contract['rules']))
        self.assertIn('by_zip', ' '.join(self.contract['rules']))

    def test_every_example_validates(self):
        self.assertGreaterEqual(len(self.contract['examples']), 1)
        for example in self.contract['examples']:
            self.assertEqual(evidence_geo.contract_problems(example['payload']), [], example['name'])

    def test_every_negative_control_is_refused_for_its_stated_reason(self):
        self.assertGreaterEqual(len(self.contract['negative_controls']), 3)
        for control in self.contract['negative_controls']:
            problems = evidence_geo.contract_problems(control['payload'])
            self.assertTrue(problems, control['name'])
            self.assertTrue(any(control['expect'] in problem for problem in problems),
                            f'{control["name"]}: {problems}')

    def test_a_real_payload_satisfies_the_contract(self):
        self.assertEqual(evidence_geo.contract_problems(self.snapshot_spend()), [])

    def test_the_controls_are_not_vacuously_wrong(self):
        """A control must fail only for its own reason, not for a shape error."""
        for control in self.contract['negative_controls']:
            payload = json.loads(json.dumps(control['payload']))
            self.assertNotEqual(evidence_geo.contract_problems(payload), [],
                                control['name'])


class Levels(GeoFixture):
    """P4b: the wider layers, and the rule that every one of them adds back.

    The district layer was already pinned by SnapshotSpend and ImportGeography.
    These tests exist for the layers the map gained - state, region and ZIP -
    and for `sides`, which decides which direction the map opens on.
    """

    def sums(self, payload, field):
        return sum(row['amount'] for row in payload[field])

    def test_by_state_and_by_region_each_add_to_the_located_total(self):
        for payload in (self.snapshot_spend(),
                        evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', '2024',
                                               'raised', snapshot_root=self.snapshot_root)):
            for field in ('by_state', 'by_region'):
                self.assertAlmostEqual(self.sums(payload, field), payload['located_total'],
                                       places=2, msg=f'{field} on {payload["direction"]}')
                self.assertAlmostEqual(self.sums(payload, field) + payload['unlocated_total'],
                                       payload['reported_total'], places=2)

    def test_an_import_reconciles_at_every_level(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised',
                                         store=store)
        for field in ('by_state', 'by_region'):
            self.assertAlmostEqual(self.sums(payload, field) + payload['unlocated_total'],
                                   payload['reported_total'], places=2, msg=field)
        self.assertAlmostEqual(self.sums(payload, 'by_zip')
                               + payload['zip_truncated_amount']
                               + payload['zip_missing']['amount'],
                               payload['reported_total'], places=2)

    def test_a_senate_row_folds_into_its_own_state_not_a_seat(self):
        payload = self.snapshot_spend()
        states = {row['key']: row for row in payload['by_state']}
        # The fixture's CA money is CA-SEN ($12,000) and CA-45 ($5,500) under
        # `candidates`, plus the $7,000 asset conversion whose payee is in CA.
        # The state layer carries EVERY kind, including one the page has off by
        # default, which is why the row also states its own per-kind split.
        self.assertAlmostEqual(states['CA']['amount'], 24500, places=2)
        self.assertAlmostEqual(states['CA']['kinds']['candidates'], 17500, places=2)
        self.assertAlmostEqual(states['CA']['kinds']['conversions'], 7000, places=2)
        self.assertEqual(states['CA']['region'], 'West')

    def test_every_region_is_a_census_region_and_the_states_agree(self):
        payload = self.snapshot_spend()
        by_state = {row['key']: row for row in payload['by_state']}
        for row in payload['by_region']:
            self.assertIn(row['key'], payload['region_order'])
            self.assertAlmostEqual(row['amount'],
                                   sum(by_state[key]['amount'] for key in row['states']),
                                   places=2, msg=row['key'])

    def test_a_row_kinds_split_adds_to_the_row(self):
        payload = self.snapshot_spend()
        for field in ('by_state', 'by_region'):
            for row in payload[field]:
                self.assertAlmostEqual(sum(row['kinds'].values()), row['amount'], places=2,
                                       msg=f'{field} {row["key"]}')

    def test_the_zip_layer_keeps_a_row_with_no_readable_zip(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised',
                                         store=store)
        # Two of the four 2024 rows carry no ZIP ($50 and $25).
        self.assertAlmostEqual(payload['zip_missing']['amount'], 75, places=2)
        self.assertEqual(payload['zip_missing']['rows'], 2)
        self.assertEqual({row['zip'] for row in payload['by_zip']}, {'94607', '92625'})

    def test_a_spent_map_carries_no_contributor_zips(self):
        payload = self.snapshot_spend()
        self.assertEqual(payload['by_zip'], [])
        self.assertEqual(payload['zip_truncated'], 0)

    def test_the_zip_cap_states_what_it_cost(self):
        rows = evidence_geo.Zips()
        for index in range(12):
            rows.add(f'{10000 + index:05d}', evidence_geo.money(index + 1), who=f'person {index}')
        rows.add('', evidence_geo.money(5))
        payload = rows.payload(cap=5)
        self.assertEqual(len(payload['by_zip']), 5)
        self.assertEqual(payload['zip_truncated'], 7)
        self.assertAlmostEqual(sum(row['amount'] for row in payload['by_zip'])
                               + payload['zip_truncated_amount']
                               + payload['zip_missing']['amount'],
                               float(rows.total()), places=2)

    def test_sides_states_both_directions_and_agrees_with_its_own(self):
        for direction in ('raised', 'spent'):
            payload = evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', '2024',
                                             direction, snapshot_root=self.snapshot_root)
            self.assertEqual(set(payload['sides']), {'raised', 'spent'})
            self.assertAlmostEqual(payload['sides'][direction]['reported_total'],
                                   payload['reported_total'], places=2)
            self.assertGreater(payload['sides']['raised']['reported_total'], 0)
            self.assertGreater(payload['sides']['spent']['reported_total'], 0)

    def test_sides_names_the_empty_side_with_the_route_s_own_refusal(self):
        store, identifier = self.store()
        payload = evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised',
                                         store=store)
        self.assertAlmostEqual(payload['sides']['raised']['reported_total'], 375, places=2)
        self.assertEqual(payload['sides']['spent']['reported_total'], 0.0)
        self.assertIn('outbound', payload['sides']['spent']['reason'].lower())

    def test_the_pane_counts_the_distinct_counterparties_it_shows(self):
        store, identifier = self.store()
        stats = evidence_geo.counterparty_count(store, identifier, 'C00804179', '2024')
        self.assertEqual(stats, {'contributors': 4, 'rows': 4})
        self.assertEqual(evidence_geo.counterparty_count(store, identifier, 'C00804179', '')
                         ['contributors'], 5)


class RulingA(GeoFixture):
    """Paul, 2026-09-18: "There should be no all cycles, street address yes".

    A district map is drawn on ONE Congress, so the route refuses a blank
    cycle instead of answering an empty or a silently-chosen map. The
    newest-cycle fallback is the CLIENT's job, so what is asserted here is the
    SERVER's refusal - the thing the client can rely on.
    """

    def test_a_blank_cycle_on_an_import_is_refused_and_names_the_cycles(self):
        store, identifier = self.store()
        with self.assertRaises(ValueError) as caught:
            evidence_geo.geography('csv', identifier, 'C00804179', '', 'raised', store=store)
        message = str(caught.exception)
        self.assertIn('one cycle, not all of them', message)
        self.assertIn('2024', message)
        self.assertIn('2026', message)

    def test_a_blank_cycle_on_a_snapshot_is_refused_rather_than_silently_picked(self):
        """The snapshot path used to answer with the FIRST unit's cycle.

        That is worse than an empty map: the pane said All cycles and the map
        answered 2024 without saying so. It is a refusal now.
        """
        with self.assertRaises(ValueError) as caught:
            evidence_geo.geography('snapshot', 'fixture-snap', 'C00835959', '', 'raised',
                                   snapshot_root=self.snapshot_root)
        message = str(caught.exception)
        self.assertIn('one cycle, not all of them', message)
        self.assertIn('2024', message)
        self.assertIn('2026', message)

    def test_an_absent_cycle_is_refused_the_same_way_a_blank_one_is(self):
        store, identifier = self.store()
        for value in (None, '', '   '):
            with self.assertRaises(ValueError) as caught:
                evidence_geo.geography('csv', identifier, 'C00804179', value, 'spent', store=store)
            self.assertIn('one cycle', str(caught.exception))

    def test_the_refusal_is_not_an_empty_map(self):
        """A refusal, never a payload: an empty map would read as "no money"."""
        store, identifier = self.store()
        try:
            payload = evidence_geo.geography('csv', identifier, 'C00804179', '', 'raised',
                                             store=store)
        except ValueError:
            return
        self.fail(f'a blank cycle answered with a payload: {payload.get("cycle")!r}')

    def test_the_cycles_a_source_offers_are_concrete_and_newest_first(self):
        store, identifier = self.store()
        self.assertEqual(evidence_geo.source_cycles('csv', identifier, 'C00804179', store),
                         [2026, 2024])
        self.assertEqual(
            evidence_geo.source_cycles('snapshot', 'fixture-snap', 'C00835959',
                                       snapshot_root=self.snapshot_root), [2026, 2024])
        for cycles in (evidence_geo.source_cycles('csv', identifier, 'C00804179', store),
                       evidence_geo.source_cycles('snapshot', 'fixture-snap', 'C00835959',
                                                  snapshot_root=self.snapshot_root)):
            self.assertTrue(all(isinstance(value, int) for value in cycles))
            self.assertNotIn('', cycles)

    def test_a_source_with_no_cycle_still_gets_a_sentence_not_a_crash(self):
        self.assertIn('no cycle to draw', evidence_geo.M_BLANK_CYCLE([]))

    def test_every_payload_this_route_answers_states_a_cycle(self):
        store, identifier = self.store()
        for payload in (self.snapshot_spend('2024'),
                        evidence_geo.geography('csv', identifier, 'C00804179', '2024', 'raised',
                                               store=store)):
            self.assertTrue(str(payload['cycle']).strip())
            self.assertTrue(payload['vintage'])
            self.assertEqual(evidence_geo.contract_problems(payload), [])


class LaneBFixture(unittest.TestCase):
    """A 119th map with single-seat states and a two-seat MT, the FAIRSHAKE-shaped
    network (a root that transfers to two saved PACs), and its own masters."""

    SEATS = ('WY-AL', 'AK-AL', 'MT-01', 'MT-02', 'CA-12')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        geo, inputs = self.root / 'geo', self.root / 'inputs'
        geo.mkdir(parents=True)
        shape = {'path': 'M0 0L1 0L1 1Z', 'centroid': [1, 1]}
        (geo / 'index.json').write_text(json.dumps({
            'width': 100, 'height': 60, 'cycles': {'2026': '119'},
            'vintages': {'119': {'file': 'cd119.json', 'congress': '119th'}}}), encoding='utf-8')
        (geo / 'cd119.json').write_text(json.dumps({
            'width': 100, 'height': 60, 'districts': {key: shape for key in self.SEATS},
            'states': {state: shape for state in ('WY', 'AK', 'MT', 'CA', 'VA', 'FL')}}), encoding='utf-8')
        (inputs / 'fec_beneficiary' / 'cn26').mkdir(parents=True)
        (inputs / 'fec_beneficiary' / 'cn26' / 'cn.txt').write_text(
            'H6CA12000|HOUSE, HOPE|DEM|2026|CA|H|12|I|C|C00700000|||\n', encoding='utf-8')
        (inputs / 'fec_committee_master' / 'cm26').mkdir(parents=True)
        (inputs / 'fec_committee_master' / 'cm26' / 'cm.txt').write_text(
            'C00848440|PROTECT PROGRESS|TRES|ST1|ST2|ST. PAUL|FL|55116|U|O|UNK|Q|||\n'
            'C00836221|DEFEND AMERICAN JOBS|TRES|ST1|ST2|ARLINGTON|VA|22201|U|O|UNK|Q|||\n'
            'C00571703|SENATE LEADERSHIP FUND|TRES|ST1|ST2|WASHINGTON|VA|20001|U|O|UNK|Q|||\n',
            encoding='utf-8')
        self.saved = {name: os.environ.get(name) for name in ('MENTAT_GEO_DIR', 'MENTAT_INPUTS_DIR')}
        os.environ['MENTAT_GEO_DIR'] = str(geo)
        os.environ['MENTAT_INPUTS_DIR'] = str(inputs)
        district_geo.reset_caches()
        self.addCleanup(self.restore)
        self.snapshot_root = self.root / 'snapshots'

    def restore(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        district_geo.reset_caches()
        evidence_geo._views.clear()
        self.temp.cleanup()

    def write(self, identifier, units):
        directory = self.snapshot_root / identifier
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'snapshots.json').write_text(json.dumps(units), encoding='utf-8')
        (directory / 'evidence.json').write_text(json.dumps({'version': 1, 'company': 'Coinbase'}),
                                                 encoding='utf-8')

    def spend(self, identifier, committee='C00835959'):
        return evidence_geo.geography('snapshot', identifier, committee, '2026', 'spent',
                                      snapshot_root=self.snapshot_root)


def recipient(committee, spend, office_state, district):
    """A saved recipient PAC whose only spending is one Schedule E row."""
    return {'committee_id': committee, 'cycle': 2026, 'receipts': [], 'disbursements': [],
            'expenditures': [expenditure(spend, 'H6XX00000', 'S', candidate_office='H',
                                         candidate_office_state=office_state,
                                         candidate_office_district=district)],
            'totals': {'disbursements': float(spend), 'coverage_end_date': '2026-07-31'}}


def fairshake(itemized):
    """The root: its summary reports 65,000 on line 22; `itemized` rows say where it went."""
    return {'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [], 'expenditures': [],
            'disbursements': [disbursement('22', value, recipient_committee_id=committee,
                                           recipient_name=committee, disbursement_type='24G',
                                           sub_id=f'{committee}-{index}')
                              for index, (committee, value) in enumerate(itemized)],
            'totals': {'disbursements': 65000.0, 'transfers_to_affiliated_committee': 65000.0,
                       'coverage_end_date': '2026-07-31'}}


class NotItemizedTransfers(LaneBFixture):
    """F6 (Paul, 2026-09-25): no dollar may count twice. A transfer the summary
    reports but no row itemizes is its own disclosed line, outside every total."""

    def test_the_early_snapshot_no_longer_counts_the_unitemized_transfers_again(self):
        recipients = [recipient('C00836221', 40000, 'CA', '12'), recipient('C00848440', 25000, 'CA', '12')]
        self.write('early', [fairshake([('C00836221', 30000), ('C00848440', 21000)]), *recipients])
        self.write('later', [fairshake([('C00836221', 30000), ('C00848440', 21000),
                                        ('C00836221', 9000), ('C00848440', 5000)])])
        early, later = self.spend('early'), self.spend('later')
        # 40,000 + 25,000 of recipient spending, and not the 14,000 again.
        self.assertEqual(early['reported_total'], 65000)
        self.assertEqual(later['reported_total'], 65000)
        self.assertTrue(early['reconciles'])
        self.assertEqual(early['transfers_not_itemized'],
                         [{'committee': 'C00835959', 'cycle': '2026', 'committee_name': 'C00835959',
                           'reason': 'transfers reported, recipient not itemized', 'amount': 14000.0,
                           'summary_amount': 65000.0, 'itemized_amount': 51000.0}])
        self.assertEqual(early['transfers_not_itemized_total'], 14000)
        self.assertEqual(later['transfers_not_itemized'], [])
        self.assertEqual(later['transfers_not_itemized_total'], 0)
        # Never in the geography: located plus unlocated is the total without it.
        self.assertAlmostEqual(early['located_total'] + early['unlocated_total'], 65000, places=2)
        self.assertEqual(early['sides']['spent']['reported_total'], 65000)
        for payload in (early, later):
            self.assertEqual(evidence_geo.contract_problems(payload), [])

    def test_the_contract_refuses_a_disclosed_line_that_does_not_add_up(self):
        payload = {'transfers_not_itemized': [{'committee': 'C1', 'reason': 'x', 'amount': 5,
                                               'summary_amount': 10, 'itemized_amount': 4}],
                   'transfers_not_itemized_total': 7}
        problems = evidence_geo.not_itemized_problems(payload)
        self.assertTrue(any('not its summary less its itemized rows' in problem for problem in problems))
        self.assertTrue(any('does not add up' in problem for problem in problems))
        self.assertEqual(evidence_geo.not_itemized_problems({}), [])


class AtLargeSeats(LaneBFixture):
    """F7 (Paul, 2026-09-25): filings write a single-seat state's seat as 01."""

    def test_a_filed_01_in_a_single_seat_state_lands_on_its_at_large_seat(self):
        self.write('seats', [{'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [],
                              'disbursements': [],
                              'expenditures': [
                                  expenditure(1927706, 'H2AK01158', 'S', candidate_office='H',
                                              candidate_office_state='AK', candidate_office_district='01'),
                                  expenditure(500, 'H6WY00001', 'O', candidate_office='H',
                                              candidate_office_state='WY', candidate_office_district='1'),
                                  expenditure(700, 'H6MT01000', 'S', candidate_office='H',
                                              candidate_office_state='MT', candidate_office_district='01')],
                              'totals': {'disbursements': 1928906.0}}])
        payload = self.spend('seats')
        districts = {row['key']: row['amount'] for kind in payload['kinds'] for row in kind['districts']}
        self.assertEqual(districts, {'AK-AL': 1927706, 'WY-AL': 500, 'MT-01': 700})
        self.assertEqual(payload['unlocated'], [])
        self.assertTrue(payload['reconciles'])


class TransactionTypeOnTheMap(LaneBFixture):
    """F8: the map reads a transfer by its transaction type, like the index."""

    def test_a_24k_on_line_21b_is_a_transfer_and_a_24z_on_line_23_is_not(self):
        self.write('types', [{'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [], 'expenditures': [],
                              'disbursements': [
                                  disbursement('21B', 3000, recipient_committee_id='C00571703',
                                               recipient_name='SENATE LEADERSHIP FUND',
                                               disbursement_type='24K', sub_id='t1'),
                                  disbursement('23', 500, recipient_committee_id='C00836221',
                                               recipient_name='DEFEND AMERICAN JOBS',
                                               disbursement_type='24Z', sub_id='t2'),
                                  disbursement('22', 700, recipient_committee_id='C00848440',
                                               recipient_name='PROTECT PROGRESS', sub_id='t3')],
                              'totals': {'disbursements': 4200.0}}])
        payload = self.spend('types')
        kinds = {kind['kind']: kind for kind in payload['kinds']}
        self.assertEqual({row['key']: row['amount'] for row in kinds['transfers']['states']},
                         {'VA': 3000, 'FL': 700})
        self.assertEqual(kinds['operating']['total'], 0)
        self.assertEqual([(row['reason'], row['amount']) for row in payload['unlocated']],
                         [('C00835959: a line 23 payment of transaction type 24Z, which is not a transfer', 500)])
        self.assertEqual(payload['transfer_rule'],
                         {'transaction_types': ['24G', '24K'], 'rows_without_transaction_type': 1,
                          'legs_by_line_fallback': 1, 'amount_by_line_fallback': 700.0})
        self.assertEqual(sorted((edge['destination'], edge['amount']) for edge in payload['downstream_edges']),
                         [('C00571703', 3000), ('C00848440', 700)])


class ReloadsOnTheMap(LaneBFixture):
    """F11 (Paul, 2026-09-25): the map totals a reload - a row differing from
    another only in SUB_ID - once, on both sides; a distinct transaction id
    stays counted."""

    def test_a_receipt_reload_is_counted_once_on_the_raised_map(self):
        gift = receipt(1000, contributor_state='CA', report_type='M6', transaction_id='SA11AI.1',
                       file_number='5', contribution_receipt_date='2026-01-02', sub_id='r1')
        self.write('reloads', [{'committee_id': 'C00835959', 'cycle': 2026, 'disbursements': [], 'expenditures': [],
                                'receipts': [gift, {**gift, 'sub_id': 'r2'},
                                             {**gift, 'sub_id': 'r3', 'transaction_id': 'SA11AI.2'}],
                                'totals': {'receipts': 2000.0, 'disbursements': 0.0}}])
        payload = evidence_geo.geography('snapshot', 'reloads', 'C00835959', '2026', 'raised',
                                         snapshot_root=self.snapshot_root)
        self.assertEqual(payload['located_total'] + payload['unlocated_total'], 2000)
        self.assertTrue(payload['reconciles'])
        self.assertEqual(payload['sides']['raised']['rows'], 2)

    def test_a_spend_reload_is_counted_once_and_the_payload_says_so(self):
        spend = expenditure(500, 'H6XX00000', 'S', candidate_office='H', candidate_office_state='CA',
                            candidate_office_district='12', report_type='M6', transaction_id='SE.1',
                            file_number='5', expenditure_date='2026-03-01', payee_name='MEDIA', sub_id='e1')
        self.write('reloads', [{'committee_id': 'C00835959', 'cycle': 2026, 'receipts': [], 'disbursements': [],
                                'expenditures': [spend, {**spend, 'sub_id': 'e2'}],
                                'totals': {'disbursements': 500.0}}])
        payload = self.spend('reloads')
        self.assertEqual(payload['located_total'], 500)
        self.assertTrue(payload['reconciles'])
        self.assertEqual((payload['reload_rule']['reload_rows'], payload['reload_rule']['reload_amount']), (1, 500))
        self.assertEqual(payload['reload_rule']['rows_without_transaction_id_or_file_number'], 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
