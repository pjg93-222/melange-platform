"""The offline assignment: sjoin correctness, the tract tables, and hexagon agreement.

Lane G (2026-09-25). Runs under the SYSTEM Python that has GeoPandas (the whole
class is skipped anywhere else); the console-side twin, test_geo_lookup.py,
runs under the console's own Python and proves that side needs none of this.

  Known points      geo_assign's bulk assignment (gpd.sjoin, predicate
                    'within') puts the Metropolitan Museum in NY-12 on the 119th
                    map, uptown Charlotte in NC-12 on the 119th (NC-14 on the
                    118th, as the Census tabulation says) and Mecklenburg County,
                    and Anchorage in AK-AL, Anchorage municipality. Negative
                    controls: Hoboken is not NY-12, and the Manhattan point with
                    latitude and longitude swapped lands in no state at all.
  Tract tables      the GeoPandas overlay agrees with the Census CD-to-tract
                    tabulation for most tracts the Census calls whole, and the
                    table carries the Census answer for every one of them (water
                    tracts and Connecticut's 2020 codes included); a ZIP-precision
                    row assigned by sjoin equals its lookup row.
  Hexagons          1,000 seeded random points: geo_lookup's plain-math EPSG:5070
                    is within 1 mm of pyproj and its hexagon ids equal the
                    tool's; every id's hexagon polygon contains its point; the
                    shipped hex_pins.json is exactly what the tool computes now.
                    Negative controls: a 0.5-degree error in the projection
                    origin or banker's rounding changes ids.

Run from inside mentat/tests with the system Python:
    C:\\Users\\pgoff\\AppData\\Local\\Programs\\Python\\Python312\\python.exe -m unittest test_geo_v2_assign
"""
import csv
import json
import math
import os
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

HERE = Path(__file__).resolve().parent
MENTAT = Path(os.environ.get('MENTAT_DIR') or HERE.parent)
# the installed tree when there is one, else the staging tree it is built in
INSTALLED = MENTAT / 'app' / 'geo' / 'v2'
STAGING = MENTAT / '.cache' / 'geo_build' / 'v2'
V2 = Path(os.environ.get('MENTAT_GEO_V2') or (INSTALLED if INSTALLED.is_dir() else STAGING))
sys.path.insert(0, str(MENTAT / 'tools'))
sys.path.insert(0, str(MENTAT / 'app'))

try:
    import numpy as np
    import geopandas  # noqa: F401
    import pyproj
    import shapely
    HAVE_GEO = True
except ImportError:                                     # the console's Python
    HAVE_GEO = False

KNOWN = [
    # id, latitude, longitude, expectations (the 118th-map answers are checked against the
    # Census CD-to-tract tabulation instead, in test_known_points_match_the_census_tabulation)
    ('met-museum', 40.7794, -73.9632, {'cd119': '3612', 'county': '36061', 'state': '36',
                                        'place': '3651000'}),
    ('charlotte-uptown', 35.2271, -80.8431, {'cd119': '3712', 'county': '37119', 'state': '37',
                                              'place': '3712000'}),
    ('anchorage', 61.2181, -149.9003, {'cd119': '0200', 'cd118': '0200', 'cd116': '0200',
                                       'county': '02020', 'state': '02', 'place': '0203000'}),
    ('hoboken', 40.7440, -74.0324, {'state': '34'}),
    ('manhattan-swapped', -73.9632, 40.7794, {}),
]


def need_tables():
    if not (V2 / 'lookup' / 'states.json').is_file():
        raise unittest.SkipTest('lookup tables are not built; run mentat/tools/geo_assign.py tables')


def census_key_geoids(vintage):
    """{'NY-12': '3612', 'DC-AL': '1198', 'AK-AL': '0200', ...} from the district file itself:
    an at-large seat is CDFP 00 but a delegate seat (DC, PR) is 98, so no arithmetic."""
    districts = json.loads((V2 / 'albers' / 'national' / f'cd{vintage}.json')
                           .read_text(encoding='utf-8'))['districts']
    mapping = {key: entry['geoid'] for key, entry in districts.items()}
    for fips, usps in (('60', 'AS'), ('66', 'GU'), ('69', 'MP'), ('78', 'VI')):
        mapping.setdefault(f'{usps}-AL', f'{fips}98')
    return mapping


@unittest.skipUnless(HAVE_GEO, 'GeoPandas is not in this Python (run under the system Python)')
class KnownPoints(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        need_tables()
        import geo_assign
        cls.tmp = tempfile.TemporaryDirectory()
        points = Path(cls.tmp.name) / 'known.csv'
        with open(points, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['id', 'latitude', 'longitude'])
            for identifier, lat, lon, _expect in KNOWN:
                writer.writerow([identifier, lat, lon])
        args = Namespace(snapshot='', points=str(points), force=True,
                         assigned=Path(cls.tmp.name) / 'assigned',
                         cache=geo_assign.DEFAULT_CACHE, v2=V2)
        cls.payload, cls.target, cls.hit = geo_assign.assign(args, log=lambda *_: None)
        cls.fields = cls.payload['fields']
        cls.rows = {key: dict(zip(cls.fields, values)) for key, values in cls.payload['rows'].items()}
        cls.args = args
        cls.geo_assign = geo_assign

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_known_points(self):
        for identifier, _lat, _lon, expect in KNOWN[:3]:
            row = self.rows[identifier]
            self.assertEqual(row['precision'], 'address', identifier)
            for level, value in expect.items():
                self.assertEqual(row[level], value, f'{identifier} {level}')
            self.assertTrue(str(row['tract']).startswith(expect['county']), identifier)
            for size in (10, 25, 50):
                self.assertTrue(str(row[f'h{size}']).startswith(f'h{size}:'), identifier)

    def test_known_points_match_the_census_tabulation(self):
        """The Census CD-to-tract tabulation (geo/tract_cd*.json) is the authority for a
        whole tract: uptown Charlotte's tract 37119000101 is NC-14 on the 118th map and
        NC-12 on the 119th (checked 2026-09-25), and the assignment must say the same."""
        whole = straddling = 0
        for vintage in ('118', '119'):
            census = json.loads((MENTAT / 'app' / 'geo' / f'tract_cd{vintage}.json')
                                .read_text(encoding='utf-8'))['districts']
            geoid_of = census_key_geoids(vintage)
            for identifier, _lat, _lon, _expect in KNOWN[:3]:
                row = self.rows[identifier]
                key = census.get(row['tract'])
                if key is not None:
                    self.assertEqual(row[f'cd{vintage}'], geoid_of[key], f'{identifier} cd{vintage}')
                    whole += 1
                    continue
                # a straddling tract: the point's district must be one of the tract's
                table = json.loads((V2 / 'lookup' / 'tract' / f'{row["tract"][:2]}.json')
                                   .read_text(encoding='utf-8'))
                value = dict(zip(table['fields'], table['rows'][row['tract']]))[f'cd{vintage}']
                options = [value] if isinstance(value, str) else [g for g, _s in value]
                self.assertIn(row[f'cd{vintage}'], options, f'{identifier} cd{vintage}')
                straddling += 1
        print(f'\n  known points: {whole} answers checked against Census whole tracts, '
              f'{straddling} against a straddling tract\'s districts')
        self.assertEqual(whole + straddling, 6)
        self.assertEqual(self.rows['charlotte-uptown']['tract'], '37119000101')
        self.assertEqual(self.rows['charlotte-uptown']['cd118'], '3714')

    def test_negative_control_hoboken_is_not_ny12(self):
        row = self.rows['hoboken']
        self.assertEqual(row['state'], '34')
        self.assertNotEqual(row['cd119'], '3612')
        self.assertTrue(str(row['cd119']).startswith('34'))

    def test_negative_control_swapped_coordinates_land_nowhere(self):
        row = self.rows['manhattan-swapped']
        self.assertIsNone(row['state'])
        self.assertIsNone(row['cd119'])

    def test_second_run_is_a_cache_hit(self):
        args = Namespace(**dict(vars(self.args), force=False))
        _payload, target, hit = self.geo_assign.assign(args, log=lambda *_: None)
        self.assertTrue(hit)
        self.assertEqual(target, self.target)

    def test_zip_rows_equal_their_lookup_rows(self):
        points = Path(self.tmp.name) / 'zips.csv'
        with open(points, 'w', newline='', encoding='utf-8') as handle:
            writer = csv.writer(handle)
            writer.writerow(['id', 'zip', 'city', 'state'])
            for number, zip5 in enumerate(('10028', '28202', '99501', '07030', '94103', '60614',
                                           '00901', '96813', '33139', '75201')):
                writer.writerow([f'z{number}', zip5, '', ''])
            writer.writerow(['c1', '', 'Charlotte', 'NC'])
            writer.writerow(['c2', '', 'Nowhereville Xyz', 'NC'])
            writer.writerow(['s1', '', '', 'AK'])
            writer.writerow(['n1', '', '', ''])
        args = Namespace(**dict(vars(self.args), points=str(points), force=True))
        payload, _target, _hit = self.geo_assign.assign(args, log=lambda *_: None)
        summary = payload['summary']
        self.assertEqual(summary['zip_rows_disagreeing_with_lookup'], 0,
                         summary['disagreement_examples'])
        self.assertEqual(summary['by_precision'], {'address': 0, 'zip': 10, 'city': 1, 'state': 2,
                                                   'none': 1})
        self.assertEqual(summary['city_leftovers']['names'], 1)
        self.assertEqual(summary['line'], '11 of 14 located')


@unittest.skipUnless(HAVE_GEO, 'GeoPandas is not in this Python (run under the system Python)')
class TractTables(unittest.TestCase):

    def test_overlay_agrees_with_census_whole_tracts(self):
        need_tables()
        index = json.loads((V2 / 'index.json').read_text(encoding='utf-8'))
        agreement = index['build_notes']['lookup_tables']['census_agreement']
        agree = sum(v['agree'] for state in agreement.values() for v in state.values())
        disagree = sum(v['count'] for state in agreement.values() for v in state.values())
        print(f'\n  overlay vs Census whole tracts: {agree:,} agree, {disagree:,} disagree '
              f'({agree / (agree + disagree):.3%})')
        self.assertGreater(agree + disagree, 150000)
        self.assertGreaterEqual(agree / (agree + disagree), 0.97)

    def test_tract_table_carries_the_census_answer(self):
        """Every tract the Census calls whole answers the Census district - including
        the water tracts the 500k file omits and Connecticut's 2020 codes."""
        need_tables()
        import geo_lookup
        os.environ['MENTAT_GEO_V2'] = str(V2)
        geo_lookup.reset_caches()
        for vintage in ('118', '119'):
            census = json.loads((MENTAT / 'app' / 'geo' / f'tract_cd{vintage}.json')
                                .read_text(encoding='utf-8'))['districts']
            geoid_of = census_key_geoids(vintage)
            wrong, renumbered = [], 0
            for tract, key in census.items():
                row = geo_lookup.for_tract(tract)
                if row is None or row[f'cd{vintage}'] != geoid_of.get(key):
                    wrong.append((tract, key, row and row[f'cd{vintage}']))
                renumbered += bool(row and row.get('tract_asked'))
            print(f'\n  cd{vintage}: {len(census):,} Census whole tracts, {len(wrong)} wrong, '
                  f'{renumbered} Connecticut 2020 codes answered through TRACTCE')
            self.assertEqual(wrong, [])
            self.assertGreater(renumbered, 800)

    def test_negative_control_unknown_tract(self):
        import geo_lookup
        os.environ['MENTAT_GEO_V2'] = str(V2)
        geo_lookup.reset_caches()
        self.assertIsNone(geo_lookup.for_tract('36061999999'))
        self.assertIsNone(geo_lookup.for_tract('09001999999'))


@unittest.skipUnless(HAVE_GEO, 'GeoPandas is not in this Python (run under the system Python)')
class Hexagons(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import build_geo_layers
        import geo_assign
        import geo_lookup
        cls.layers, cls.geo_assign, cls.lookup = build_geo_layers, geo_assign, geo_lookup
        rng = np.random.default_rng(1000)
        lat = np.concatenate([rng.uniform(24, 49.5, 900), rng.uniform(51, 71.5, 60),
                              rng.uniform(18.9, 22.3, 40)])
        lon = np.concatenate([rng.uniform(-125, -66.9, 900), rng.uniform(-190, -129, 60),
                              rng.uniform(-160.3, -154.8, 40)])
        cls.lat, cls.lon = lat, (lon + 180.0) % 360.0 - 180.0
        transformer = pyproj.Transformer.from_crs(4269, 5070, always_xy=True)
        cls.x, cls.y = transformer.transform(cls.lon, cls.lat)

    def test_plain_math_projection_matches_pyproj(self):
        worst = 0.0
        for lat, lon, x, y in zip(self.lat, self.lon, self.x, self.y):
            mx, my = self.lookup.albers_5070(lat, lon)
            worst = max(worst, abs(mx - x), abs(my - y))
        print(f'\n  EPSG:5070 plain math vs pyproj: worst {worst:.2e} m over {len(self.lat)} points')
        self.assertLess(worst, 1e-3)

    def test_inverse_round_trips(self):
        worst = 0.0
        for lat, lon, x, y in zip(self.lat[:200], self.lon[:200], self.x[:200], self.y[:200]):
            back = self.lookup.albers_5070_inverse(x, y)
            worst = max(worst, abs(back[0] - lat), abs(((back[1] - lon + 180) % 360) - 180))
        self.assertLess(worst, 1e-9)

    def test_hex_ids_agree_on_1000_points(self):
        self.assertEqual(len(self.lat), 1000)
        for size in (10, 25, 50):
            tool = self.layers.hex_ids_lonlat(self.lon, self.lat, size)
            console = [self.lookup.hex_id(lat, lon, size) for lat, lon in zip(self.lat, self.lon)]
            self.assertEqual(tool, console, f'hex{size}')

    def test_each_point_lies_in_its_hexagon(self):
        for size in (10, 25, 50):
            ids = [self.lookup.hex_id(lat, lon, size) for lat, lon in zip(self.lat, self.lon)]
            q = np.array([int(i.split(':')[1]) for i in ids])
            r = np.array([int(i.split(':')[2]) for i in ids])
            polygons = self.layers.hex_polygons(q, r, size)
            points = shapely.points(self.x, self.y)
            inside = shapely.covers(shapely.buffer(polygons, 1e-6), points)
            self.assertTrue(bool(inside.all()), f'hex{size}: {int((~inside).sum())} outside')

    def test_shipped_pins_are_the_tools_answers(self):
        pins = json.loads((V2 / 'lookup' / 'hex_pins.json').read_text(encoding='utf-8'))
        fresh = self.geo_assign.hex_pins()
        self.assertEqual(pins['points'], fresh['points'])

    def test_negative_control_wrong_origin_changes_ids(self):
        transformer = pyproj.Transformer.from_crs(
            4269, '+proj=aea +lat_0=23.5 +lon_0=-96 +lat_1=29.5 +lat_2=45.5 +ellps=GRS80 +units=m',
            always_xy=True)
        x, y = transformer.transform(self.lon, self.lat)
        q, r = self.layers.hex_axial(np.asarray(x), np.asarray(y), 10)
        wrong = [f'h10:{a}:{b}' for a, b in zip(q.tolist(), r.tolist())]
        right = [self.lookup.hex_id(lat, lon, 10) for lat, lon in zip(self.lat, self.lon)]
        differ = sum(a != b for a, b in zip(wrong, right))
        self.assertGreater(differ, 100)

    def test_negative_control_bankers_rounding_changes_ids(self):
        size = 10
        side = self.lookup.hex_side(size)
        differ = 0
        for x, y in zip(self.x, self.y):
            # evaluate exactly at the midpoint between two neighbouring centres
            q, r = self.lookup.hex_axial(x, y, size)
            mid_x = side * math.sqrt(3.0) * (q + 0.5 + r / 2.0)
            mid_y = side * 1.5 * r
            qf = (math.sqrt(3.0) / 3.0 * mid_x - mid_y / 3.0) / side
            banker = round(qf)
            floor_half = math.floor(qf + 0.5)
            differ += banker != floor_half
        self.assertGreater(differ, 0)


if __name__ == '__main__':
    unittest.main()
