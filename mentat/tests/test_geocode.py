"""S8b: street-address geocoding for the PAC evidence district map.

NO TEST HERE TOUCHES THE NETWORK. The batch client is exercised against a
RECORDED response, copied byte-for-byte out of a real call made on 2026-09-18
(see RECORDED_BATCH), and every job runs with a stub client that hands back
that same text. The live service belongs in the receipt, not in a unit test.

Run: python mentat/tests/test_geocode.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))

import district_geo  # noqa: E402
import evidence_csv  # noqa: E402
import evidence_geo  # noqa: E402
import geocode  # noqa: E402


# What the live service answered on 2026-09-18 to
#   POST https://geocoding.geo.census.gov/geocoder/geographies/addressbatch
#   benchmark=4  vintage=420  returntype=geographies
# for the six addresses in BATCH_INPUT. Copied verbatim, including the fact
# that the rows come back OUT OF ORDER and that a refusal carries three fields
# where a match carries twelve.
RECORDED_BATCH = (
    '"r2","1455 Market St, San Francisco, CA, 94103","Match","Exact",'
    '"1455 MARKET ST, SAN FRANCISCO, CA, 94103","-122.418085649048,37.775961452833",'
    '"654513845","L","06","075","017603","4000"\n'
    '"r3","1 Frank H Ogawa Plz, Oakland, CA, 94612","No_Match"\n'
    '"r4","1600 Pennsylvania Ave NW, Washington, DC, 20500","Match","Exact",'
    '"1600 PENNSYLVANIA AVE NW, WASHINGTON, DC, 20500","-77.03518753691,38.89869893252",'
    '"76225813","L","11","001","980000","1034"\n'
    '"r5","999 Nowhere Blvd, Nowhereville, ZZ, 00000","No_Match"\n'
    '"r6","Main St, Springfield, IL, 62701","No_Match"\n'
    '"r1","100 Pine St, San Francisco, CA, 94111","Match","Exact",'
    '"100 PINE ST, SAN FRANCISCO, CA, 94111","-122.39867061781,37.792455266252",'
    '"192281482","R","06","075","011700","2014"\n')

BATCH_INPUT = [
    ('r1', '100 PINE ST', 'SAN FRANCISCO', 'CA', '94111'),
    ('r2', '1455 MARKET ST', 'SAN FRANCISCO', 'CA', '94103'),
    ('r3', '1 FRANK H OGAWA PLZ', 'OAKLAND', 'CA', '94612'),
    ('r4', '1600 PENNSYLVANIA AVE NW', 'WASHINGTON', 'DC', '20500'),
    ('r5', '999 NOWHERE BLVD', 'NOWHEREVILLE', 'ZZ', '00000'),
    ('r6', 'MAIN ST', 'SPRINGFIELD', 'IL', '62701'),
]

# A tie and a non-exact match, which the recorded call happened not to contain.
# Both shapes are what the service documents, so they are exercised rather than
# assumed.
EXTRA_GRADES = (
    '"t1","9 Ambiguous Way, Springfield, IL, 62701","Tie"\n'
    '"n1","5 Near Miss Rd, Reno, NV, 89501","Match","Non_Exact",'
    '"5 NEAR MISS RD, RENO, NV, 89501","-119.8,39.5","1","L","32","031","002101","1000"\n')

#: The tracts the recording carries, all sent to ONE seat no ZIP in this
#: fixture reaches, so a street basis and a ZIP basis can never be confused.
FIXTURE_TRACTS = {'06075011700': 'TX-07', '06075017603': 'TX-07', '11001980000': 'TX-07'}

# Six supported rows: three with a street address (two the recording matches,
# one it refuses), one on a single-district ZIP, one on a ZIP that straddles,
# one with neither.
IMPORT_CSV = '\n'.join([
    'Committee ID,Cycle,Contributor,Amount,Date,Street 1,City,State,ZIP,Record Type,Recipient',
    'C00804179,2023-2024,"ONE, PERSON",100,2024-01-02,100 Pine St.,San Francisco,CA,94111-2708,'
    'CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"TWO, PERSON",200,2024-01-03,1455 Market St,San Francisco,CA,94103,'
    'CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"THREE, PERSON",50,2024-01-04,1 Frank H Ogawa Plz,Oakland,CA,94612,'
    'CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"FOUR, PERSON",25,2024-01-05,,,CA,94607,CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"FIVE, PERSON",40,2024-01-06,,,CA,92625,CONTRIBUTION,COINBASE PAC',
    'C00804179,2023-2024,"SIX, PERSON",10,2024-01-07,,,NY,,CONTRIBUTION,COINBASE PAC',
    '']) + '\n'


def write_geo(root):
    """Four districts, two states, and a crosswalk with one straddling ZIP."""
    root.mkdir(parents=True, exist_ok=True)
    (root / 'index.json').write_text(json.dumps({
        'width': 100, 'height': 60, 'built_at': 'fixture',
        'cycles': {'2022': '118', '2024': '118', '2026': '119'},
        'vintages': {
            '118': {'file': 'cd118.json', 'cycle': 2024, 'congress': '118th',
                    'zcta_file': 'zcta_cd118.json', 'zcta_basis': 'land area',
                    'tract_file': 'tract_cd118.json',
                    'tract_basis': 'whole 2020 census tracts'},
            '119': {'file': 'cd119.json', 'cycle': 2026, 'congress': '119th',
                    'zcta_file': 'zcta_cd119.json', 'zcta_basis': 'land area',
                    'tract_file': 'tract_cd119.json',
                    'tract_basis': 'whole 2020 census tracts'}}}), encoding='utf-8')
    for vintage in ('118', '119'):
        (root / f'cd{vintage}.json').write_text(json.dumps({
            'width': 100, 'height': 60,
            'districts': {'CA-12': {'path': 'M0 0L1 0L1 1Z'}, 'CA-45': {'path': 'M2 0L3 0L3 1Z'},
                          'NY-16': {'path': 'M4 0L5 0L5 1Z'}, 'TX-07': {'path': 'M6 0L7 0L7 1Z'}},
            'states': {'CA': {'path': 'M0 0L3 0L3 2Z'}, 'NY': {'path': 'M4 0L6 0L6 2Z'},
                       'TX': {'path': 'M6 0L8 0L8 2Z'}}}), encoding='utf-8')
        (root / f'zcta_cd{vintage}.json').write_text(json.dumps({
            '94607': [['CA-12', 1.0]],
            '92625': [['CA-45', 0.6], ['NY-16', 0.4]],
            '10024': [['NY-16', 1.0]]}), encoding='utf-8')
        (root / f'tract_cd{vintage}.json').write_text(json.dumps({
            'vintage': vintage, 'basis': 'whole 2020 census tracts',
            'districts': dict(FIXTURE_TRACTS)}), encoding='utf-8')


class StubClient:
    """The batch client's shape, answering the recording. Records its calls."""

    benchmark = geocode.BENCHMARK
    vintage = geocode.VINTAGE

    def __init__(self, text=RECORDED_BATCH, fail=None):
        self.text = text
        self.fail = fail
        self.calls = []

    def geocode(self, items):
        items = list(items)
        self.calls.append(items)
        if self.fail:
            raise ValueError(self.fail)
        answered = geocode.parse_batch_csv(self.text)
        # The recording used ids r1..r6; a real call uses cache keys. The stub
        # therefore answers by POSITION within the chunk it is handed.
        recorded = [f'r{index + 1}' for index in range(len(BATCH_INPUT))]
        results = {}
        for index, item in enumerate(items):
            source = answered.get(recorded[index % len(recorded)])
            if source:
                results[item[0]] = {**source, 'id': item[0]}
        return results


class GeocodeFixture(unittest.TestCase):
    """Its own data root, its own geography, its own tract crosswalk."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        write_geo(self.root / 'geo')
        self.saved = {name: os.environ.get(name)
                      for name in ('MENTAT_GEO_DIR', 'MENTAT_INPUTS_DIR')}
        os.environ['MENTAT_GEO_DIR'] = str(self.root / 'geo')
        os.environ['MENTAT_INPUTS_DIR'] = str(self.root / 'inputs')
        district_geo.reset_caches()
        self.addCleanup(self.restore)

    def restore(self):
        for name, value in self.saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        district_geo.reset_caches()
        geocode._JOBS.clear()
        geocode._RUNS.clear()
        try:
            self.temp.cleanup()
        except OSError:
            pass

    def store(self):
        store = evidence_csv.Store(self.root / 'data', evict=False)
        job = store.wait(store.ingest_text('Crypto Industry Spending Overview.csv', IMPORT_CSV)['id'])
        self.assertEqual(job['state'], 'ready', job.get('message'))
        return store, job['id']

    def geocoded(self, store, identifier, client=None):
        """Run the whole job once, against the recording, and return its cache."""
        cache = geocode.Cache(self.root)
        geocode.Job(self.root, identifier,
                    geocode.addresses_for(store, identifier, 'C00804179', '2024'),
                    cache=cache, client=client or StubClient()).run()
        return cache


# ---------------------------------------------------------------- normalising

class Normalising(GeocodeFixture):
    def test_case_punctuation_and_spacing_fold_to_one_key(self):
        first = geocode.normalise('100 Pine St.', 'San  Francisco', 'ca', '94111')
        second = geocode.normalise('  100   PINE ST ', 'SAN FRANCISCO', 'CA', '94111')
        self.assertEqual(first[0], second[0])
        self.assertEqual(first[0], '100 PINE ST|SAN FRANCISCO|CA|94111')

    def test_zip_plus_four_truncates_to_five(self):
        plus = geocode.normalise('100 Pine St', 'SF', 'CA', '94111-2708')
        run_on = geocode.normalise('100 Pine St', 'SF', 'CA', '941112708')
        five = geocode.normalise('100 Pine St', 'SF', 'CA', '94111')
        self.assertEqual(plus[0], five[0])
        self.assertEqual(run_on[0], five[0])
        self.assertEqual(plus[4], '94111')

    def test_an_address_without_a_street_number_is_refused_not_sent(self):
        self.assertIsNone(geocode.normalise('', 'SF', 'CA', '94111'))
        self.assertIsNone(geocode.normalise('PO BOX', 'SF', 'CA', '94111'))

    def test_a_street_with_no_state_and_no_zip_is_refused(self):
        self.assertIsNone(geocode.normalise('100 Pine St', '', '', ''))
        self.assertIsNotNone(geocode.normalise('100 Pine St', '', 'CA', '94111'))

    def test_a_bad_state_code_is_dropped_rather_than_sent(self):
        parts = geocode.normalise('100 Pine St', 'SF', 'CALIFORNIA', '94111')
        self.assertEqual(parts[3], '')

    def test_the_address_columns_are_found_and_nothing_else_is(self):
        row = {'street_1': '100 Pine St', 'city': 'SF', 'contributor_state': 'CA',
               'zip5': '94111', 'contributor_name': 'A DONOR', 'amount': '100',
               'employer': 'COINBASE'}
        self.assertEqual(geocode.address_of(row), ('100 Pine St', 'SF', 'CA', '94111'))

    def test_a_none_valued_column_does_not_raise(self):
        row = {'street_1': None, 'city': None, 'contributor_state': 'CA', 'zip': 94111}
        self.assertEqual(geocode.address_of(row), ('', '', 'CA', '94111'))


# --------------------------------------------------------------------- cache

class CacheRoundTrip(GeocodeFixture):
    def row(self, **fields):
        row = {'key': 'K', 'street': '100 PINE ST', 'city': 'SAN FRANCISCO', 'state': 'CA',
               'zip5': '94111', 'latitude': 37.79, 'longitude': -122.39,
               'tract': '06075011700', 'district_119': 'TX-07', 'district_118': 'TX-07',
               'matched': 'exact', 'source': geocode.SERVICE, 'fetched_at': 1.0}
        row.update(fields)
        return row

    def test_a_row_written_comes_back_with_every_column(self):
        cache = geocode.Cache(self.root)
        cache.put_many([self.row()])
        row = cache.get('K')
        self.assertEqual(sorted(row), sorted(geocode.COLUMNS))
        self.assertEqual(row['district_118'], 'TX-07')
        self.assertEqual(row['matched'], 'exact')
        self.assertAlmostEqual(row['latitude'], 37.79, places=2)
        self.assertEqual(row['source'], geocode.SERVICE)

    def test_the_file_survives_a_new_cache_object_on_the_same_data_root(self):
        geocode.Cache(self.root).put_many([self.row()])
        self.assertIsNotNone(geocode.Cache(self.root).get('K'))
        self.assertEqual(geocode.Cache(self.root).total(), 1)
        self.assertTrue((self.root / 'geocode' / 'addresses.sqlite').is_file())

    def test_an_unknown_key_is_absent_rather_than_an_error(self):
        self.assertIsNone(geocode.Cache(self.root).get('NOT THERE'))
        self.assertEqual(geocode.Cache(self.root).get_many([]), {})

    def test_the_cycle_decides_which_map_the_cached_district_is_read_from(self):
        row = {'district_119': 'CA-45', 'district_118': 'CA-12'}
        self.assertEqual(geocode.district_of(row, '2026'), 'CA-45')
        self.assertEqual(geocode.district_of(row, '2024'), 'CA-12')
        self.assertEqual(geocode.district_of(row, ''), '')


# ------------------------------------------------------------- batch client

class BatchRequest(GeocodeFixture):
    def test_the_upload_is_id_street_city_state_zip_with_no_header(self):
        payload = geocode.build_batch_csv(BATCH_INPUT).decode('utf-8')
        lines = payload.strip().split('\n')
        self.assertEqual(len(lines), len(BATCH_INPUT))
        self.assertEqual(lines[0], 'r1,100 PINE ST,SAN FRANCISCO,CA,94111')
        self.assertNotIn('id,street', payload)

    def test_nothing_but_the_address_can_reach_the_upload(self):
        payload = geocode.build_batch_csv(
            [('key', '100 PINE ST', 'SAN FRANCISCO', 'CA', '94111')]).decode('utf-8')
        for secret in ('DONOR', 'COINBASE', '100.00', 'employer'):
            self.assertNotIn(secret, payload)

    def test_a_comma_in_a_street_is_quoted_not_spilled_into_the_next_column(self):
        payload = geocode.build_batch_csv(
            [('k', 'SUITE 3, 100 PINE ST', 'SF', 'CA', '94111')]).decode('utf-8').strip()
        self.assertEqual(payload, 'k,"SUITE 3, 100 PINE ST",SF,CA,94111')

    def test_more_than_the_service_ceiling_is_refused_before_it_is_sent(self):
        def refuse(*_args, **_kwargs):
            raise AssertionError('the client must not open a connection here')

        client = geocode.Client(opener=refuse)
        rows = [(str(index), 'S', 'C', 'CA', '94111')
                for index in range(geocode.BATCH_LIMIT + 1)]
        with self.assertRaises(ValueError) as caught:
            client.geocode(rows)
        self.assertIn('at most', str(caught.exception))

    def test_an_empty_batch_asks_the_service_nothing(self):
        def refuse(*_args, **_kwargs):
            raise AssertionError('the client must not open a connection here')

        self.assertEqual(geocode.Client(opener=refuse).geocode([]), {})


class BatchResponse(GeocodeFixture):
    def test_the_recorded_answer_parses_by_id_not_by_position(self):
        results = geocode.parse_batch_csv(RECORDED_BATCH)
        self.assertEqual(set(results), {'r1', 'r2', 'r3', 'r4', 'r5', 'r6'})
        self.assertEqual(results['r1']['tract'], '06075011700')
        self.assertEqual(results['r2']['tract'], '06075017603')
        self.assertEqual(results['r4']['tract'], '11001980000')
        self.assertAlmostEqual(results['r1']['latitude'], 37.792455, places=5)
        self.assertAlmostEqual(results['r1']['longitude'], -122.398670, places=5)

    def test_every_match_class_is_graded(self):
        results = geocode.parse_batch_csv(RECORDED_BATCH + EXTRA_GRADES)
        self.assertEqual(results['r1']['matched'], 'exact')
        self.assertEqual(results['r3']['matched'], 'no_match')
        self.assertEqual(results['r5']['matched'], 'no_match')
        self.assertEqual(results['t1']['matched'], 'tie')
        self.assertEqual(results['n1']['matched'], 'non_exact')
        self.assertEqual({result['matched'] for result in results.values()},
                         set(geocode.MATCH_CLASSES))

    def test_a_refusal_carries_no_coordinate_and_no_tract(self):
        results = geocode.parse_batch_csv(RECORDED_BATCH)
        self.assertIsNone(results['r3']['latitude'])
        self.assertEqual(results['r3']['tract'], '')

    def test_the_batch_answer_carries_no_district_which_is_why_a_crosswalk_exists(self):
        """The discovery this lane rests on, pinned as a test.

        The batch route returns STATE, COUNTY, TRACT, BLOCK and never a CD
        field, so a geocoded address reaches its seat through the shipped
        tract crosswalk. If the service ever starts answering a district, this
        test fails and the bridge can go.
        """
        self.assertNotIn('CD119', RECORDED_BATCH)
        self.assertNotIn('CD118', RECORDED_BATCH)
        results = geocode.parse_batch_csv(RECORDED_BATCH)
        self.assertNotIn('district', results['r1'])
        self.assertEqual(district_geo.district_for_tract(results['r1']['tract'], '118'), 'TX-07')

    def test_a_tract_the_crosswalk_omits_leaves_the_row_to_its_zip(self):
        self.assertEqual(district_geo.district_for_tract('06075999999', '118'), '')
        self.assertIn('keeps its approximate ZIP basis', district_geo.last_problem)


# ----------------------------------------------------------------- the job

class TheJob(GeocodeFixture):
    def addresses(self, count=6):
        return [(f'{index} PINE ST|CITY|CA|94111', f'{index} PINE ST', 'CITY', 'CA', '94111')
                for index in range(1, count + 1)]

    def run_job(self, addresses, client=None, identifier='csv-' + '0' * 32):
        cache = geocode.Cache(self.root)
        geocode.Job(self.root, identifier, addresses, cache=cache,
                    client=client or StubClient()).run()
        return geocode.read_job(self.root, identifier), cache

    def test_a_finished_job_reports_every_row_and_stores_every_answer(self):
        state, cache = self.run_job(self.addresses())
        self.assertEqual(state['state'], 'done')
        self.assertEqual(state['rows_total'], 6)
        self.assertEqual(state['rows_done'], 6)
        self.assertEqual(state['matched'], 3)          # r1, r2, r4 in the recording
        self.assertEqual(state['no_match'], 3)
        self.assertEqual(cache.total(), 6)
        self.assertIn('matched', state['progress'])

    def test_a_cached_address_is_never_sent_again(self):
        addresses = self.addresses()
        _state, cache = self.run_job(addresses)
        client = StubClient()
        second = 'csv-' + '1' * 32
        geocode.Job(self.root, second, addresses, cache=cache, client=client).run()
        self.assertEqual(client.calls, [])
        state = geocode.read_job(self.root, second)
        self.assertEqual(state['cached'], 6)
        self.assertEqual(state['sent'], 0)
        self.assertEqual(state['state'], 'done')

    def test_stopping_leaves_the_cache_valid_and_the_job_resumable(self):
        addresses = self.addresses(6)
        cache = geocode.Cache(self.root)
        identifier = 'csv-' + '2' * 32
        client = StubClient()
        job = geocode.Job(self.root, identifier, addresses, cache=cache, client=client)
        answer = client.geocode

        def stop_after_the_first_request(items):
            result = answer(items)
            job.stop()
            return result

        client.geocode = stop_after_the_first_request
        original = geocode.CHUNK
        geocode.CHUNK = 2
        try:
            job.run()
        finally:
            geocode.CHUNK = original
        stopped = geocode.read_job(self.root, identifier)
        self.assertEqual(stopped['state'], 'stopped')
        self.assertEqual(stopped['rows_done'], 2)
        self.assertEqual(cache.total(), 2)
        # Resume: the two already answered are not sent again, the other four are.
        resumed_client = StubClient()
        geocode.Job(self.root, identifier, addresses, cache=cache,
                    client=resumed_client).run()
        self.assertEqual(geocode.read_job(self.root, identifier)['state'], 'done')
        self.assertEqual(sum(len(call) for call in resumed_client.calls), 4)
        self.assertEqual(cache.total(), 6)

    def test_a_failing_request_names_its_reason_and_keeps_what_it_had(self):
        state, cache = self.run_job(self.addresses(),
                                    client=StubClient(fail='The service answered 503.'))
        self.assertEqual(state['state'], 'failed')
        self.assertIn('503', state['message'])
        self.assertEqual(cache.total(), 0)

    def test_the_progress_line_names_rows_done_and_rows_total(self):
        line = geocode.progress_line({'state': 'running', 'rows_done': 12, 'rows_total': 300,
                                      'matched': 9, 'cached': 3})
        self.assertIn('12', line)
        self.assertIn('300', line)
        self.assertIn(geocode.SERVICE, line)

    def test_start_refuses_a_second_job_for_the_same_import(self):
        identifier = 'csv-' + '3' * 32
        holding, released = threading.Event(), threading.Event()
        client = StubClient()

        def hold(_items):
            holding.set()
            released.wait(10)
            return {}

        client.geocode = hold
        geocode.start(self.root, identifier, self.addresses(), client=client)
        try:
            self.assertTrue(holding.wait(10))
            with self.assertRaises(ValueError) as caught:
                geocode.start(self.root, identifier, self.addresses())
            self.assertEqual(str(caught.exception), geocode.M_RUNNING)
        finally:
            released.set()

    def test_start_refuses_an_import_with_no_address_at_all(self):
        with self.assertRaises(ValueError) as caught:
            geocode.start(self.root, 'csv-' + '4' * 32, [])
        self.assertEqual(str(caught.exception), geocode.M_NO_ROWS)

    def test_the_addresses_of_an_import_are_unique_and_usable_only(self):
        store, identifier = self.store()
        items = geocode.addresses_for(store, identifier, 'C00804179', '2024')
        self.assertEqual(len(items), 3)                      # three rows carry no street
        self.assertTrue(all(len(item) == 5 for item in items))
        self.assertIn('100 PINE ST|SAN FRANCISCO|CA|94111', [item[0] for item in items])

    def test_status_says_idle_before_any_job_and_carries_the_privacy_sentence(self):
        state = geocode.status(self.root, 'csv-' + '5' * 32)
        self.assertEqual(state['state'], 'idle')
        self.assertIn('US Census Geocoder', state['privacy'])
        self.assertIn('No name, amount, employer or occupation is sent.', state['privacy'])


class ReadOnlyConsole(GeocodeFixture):
    def console(self, read_only, store=None):
        return types.SimpleNamespace(read_only=read_only, root=self.root, evidence_store=store)

    def test_a_read_only_console_refuses_to_start_a_job(self):
        import server                                        # noqa: PLC0415
        identifier = 'csv-' + '6' * 32
        with self.assertRaises(ValueError) as caught:
            server.Console.start_evidence_geocode(self.console(True), {'id': identifier})
        self.assertEqual(str(caught.exception), geocode.M_READ_ONLY)
        self.assertIsNone(geocode.read_job(self.root, identifier))

    def test_a_writable_console_starts_one_and_stops_it(self):
        import server                                        # noqa: PLC0415
        store, identifier = self.store()
        original = geocode.Client
        geocode.Client = StubClient
        try:
            started = server.Console.start_evidence_geocode(
                self.console(False, store),
                {'id': identifier, 'committee': 'C00804179', 'cycle': '2024'})
            self.assertEqual(started['rows_total'], 3)
            self.assertIn(started['state'], ('running', 'done'))
            stopped = server.Console.start_evidence_geocode(
                self.console(False, store), {'id': identifier, 'action': 'stop'})
            self.assertIn(stopped['state'], ('running', 'stopped', 'done'))
        finally:
            geocode.Client = original

    def test_an_identifier_that_is_not_an_import_is_refused(self):
        import server                                        # noqa: PLC0415
        with self.assertRaises(ValueError):
            server.Console.start_evidence_geocode(self.console(False), {'id': '../etc/passwd'})


# ------------------------------------------------------------------ the mix

class TheMix(GeocodeFixture):
    def geography(self, store, identifier, cycle='2024', direction='raised'):
        return evidence_geo.geography('csv', identifier, 'C00804179', cycle, direction,
                                      store=store, data_root=self.root)

    def districts(self, payload):
        return {row['key']: row for kind in payload['kinds'] for row in kind['districts']}

    def test_with_an_empty_cache_every_addressed_row_is_pending(self):
        store, identifier = self.store()
        payload = self.geography(store, identifier)
        self.assertEqual(payload['geocoded_rows'], 0)
        self.assertEqual(payload['pending_rows'], 3)
        self.assertEqual(payload['zip_rows'], 3)
        self.assertEqual(payload['geocoded_rows'] + payload['pending_rows']
                         + payload['zip_rows'], 6)

    def test_a_geocoded_row_is_placed_by_street_and_says_so(self):
        store, identifier = self.store()
        self.geocoded(store, identifier)
        payload = self.geography(store, identifier)
        rows = self.districts(payload)
        self.assertEqual(payload['geocoded_rows'], 2)        # the third is a No_Match
        self.assertEqual(payload['zip_rows'], 4)
        self.assertEqual(payload['pending_rows'], 0)
        self.assertEqual(rows['TX-07']['basis'], 'street')
        self.assertFalse(rows['TX-07']['split'])
        self.assertAlmostEqual(rows['TX-07']['amount'], 300, places=2)

    def test_a_split_zip_row_is_marked_zip_split_and_a_plain_one_zip(self):
        store, identifier = self.store()
        rows = self.districts(self.geography(store, identifier))
        self.assertEqual(rows['CA-45']['basis'], 'zip_split')   # 92625 straddles
        self.assertTrue(rows['CA-45']['split'])
        self.assertEqual(rows['CA-12']['basis'], 'zip')        # 94607 does not
        self.assertFalse(rows['CA-12']['split'])

    def test_a_spent_row_states_no_contributor_basis(self):
        store, identifier = self.store()
        payload = self.geography(store, identifier, direction='spent')
        self.assertEqual((payload['geocoded_rows'], payload['zip_rows'],
                          payload['pending_rows']), (0, 0, 0))

    def test_no_row_is_dropped_whatever_its_basis(self):
        store, identifier = self.store()
        before = self.geography(store, identifier)
        self.geocoded(store, identifier)
        after = self.geography(store, identifier)
        self.assertAlmostEqual(before['reported_total'], after['reported_total'], places=2)
        self.assertAlmostEqual(after['located_total'] + after['unlocated_total'],
                               after['reported_total'], places=2)
        self.assertTrue(after['reconciles'])

    def test_the_payload_still_satisfies_the_contract_with_a_street_basis(self):
        store, identifier = self.store()
        self.geocoded(store, identifier)
        self.assertEqual(evidence_geo.contract_problems(self.geography(store, identifier)), [])

    def test_reading_a_map_never_creates_the_cache_file(self):
        """A GET must not write into the data root.

        A read-only preview shares the owning console's data root, so a map
        request that quietly created a database would be writing through a
        door marked read-only. Only Start creates it.
        """
        store, identifier = self.store()
        sqlite_path = self.root / 'geocode' / 'addresses.sqlite'
        self.assertFalse(sqlite_path.exists())
        payload = self.geography(store, identifier)
        self.assertFalse(sqlite_path.exists(), 'a map GET created the geocode cache')
        self.assertEqual(payload['pending_rows'], 3)
        self.assertEqual(payload['geocoded_rows'], 0)

    def test_the_cache_is_shared_so_a_second_import_pays_nothing(self):
        store, identifier = self.store()
        self.geocoded(store, identifier)
        client = StubClient()
        other = 'csv-' + '7' * 32
        geocode.Job(self.root, other,
                    geocode.addresses_for(store, identifier, 'C00804179', '2024'),
                    cache=geocode.Cache(self.root), client=client).run()
        self.assertEqual(client.calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
