"""geo_lookup, the console side of the v2 geography: no GeoPandas, same answers.

Lane G (2026-09-25). This suite is meant to run under the CONSOLE's Python,
Mentat's venv (%LOCALAPPDATA%\\Mentat\\venv\\Scripts\\python.exe). That venv also
carries GeoPandas, Shapely, pyproj and GeoPy for the offline tools, so what proves
the console needs none of them is the sys.modules check below:

  No heavy imports  after every public function has run, none of geopandas,
                    shapely, pyproj, pyogrio or geopy is in sys.modules, and the
                    module's source imports only the standard library.
  Hexagon pins      every point in lookup/hex_pins.json (written by the offline
                    tool from pyproj): albers_5070 within 1 mm and the same hexagon
                    id at 10, 25 and 50 miles. Negative controls: the wrong ellipsoid
                    or round-half-to-even rounding must break the pins.
  Precision tags    address, zip, city, state and none, each from the real tables,
                    with located_line; negative controls: a city in the wrong state
                    is not a city answer, ZIP 00000 is not a zip answer.

Run from inside mentat/tests:
    "%LOCALAPPDATA%\\Mentat\\venv\\Scripts\\python.exe" -m unittest test_geo_lookup
"""
import ast
import json
import math
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MENTAT = Path(os.environ.get('MENTAT_DIR') or HERE.parent)
# The console reads only the installed tree; until build_geo_layers.py --install has
# moved it into mentat/app/geo/v2, this suite reads the staging tree instead.
INSTALLED = MENTAT / 'app' / 'geo' / 'v2'
STAGING = MENTAT / '.cache' / 'geo_build' / 'v2'
if not os.environ.get('MENTAT_GEO_V2') and not INSTALLED.is_dir() and STAGING.is_dir():
    os.environ['MENTAT_GEO_V2'] = str(STAGING)
sys.path.insert(0, str(MENTAT / 'app'))
import geo_lookup  # noqa: E402

HEAVY = ('geopandas', 'shapely', 'pyproj', 'pyogrio', 'geopy')


def lookup_ready():
    return (geo_lookup.lookup_dir() / 'states.json').is_file()


class NoHeavyImports(unittest.TestCase):

    def test_source_imports_only_the_standard_library(self):
        tree = ast.parse((MENTAT / 'app' / 'geo_lookup.py').read_text(encoding='utf-8'))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split('.')[0])
        allowed = {'__future__', 'functools', 'json', 'math', 'os', 'pathlib', 're',
                   'unicodedata'}
        self.assertEqual(imported - allowed, set())

    def test_running_everything_loads_no_geo_library(self):
        # A fresh interpreter: an earlier geo test in this pytest process may already
        # have imported geopandas/shapely/pyproj, and sys.modules is shared.
        code = (
            'import json, sys\n'
            'sys.path.insert(0, sys.argv[1])\n'
            'import geo_lookup\n'
            'geo_lookup.hex_id(40.75, -73.98, 25)\n'
            "geo_lookup.hex_center('h25:0:0')\n"
            'geo_lookup.albers_5070_inverse(0.0, 0.0)\n'
            'geo_lookup.locate(latitude=40.75, longitude=-73.98)\n'
            "geo_lookup.locate(zip='10028')\n"
            "geo_lookup.locate(city='Charlotte', state='NC')\n"
            "geo_lookup.locate(state='AK')\n"
            "geo_lookup.assignment('0' * 64)\n"
            'print(json.dumps(sorted(n for n in json.loads(sys.argv[2]) if n in sys.modules)))\n'
        )
        done = subprocess.run(
            [sys.executable, '-c', code, str(MENTAT / 'app'), json.dumps(HEAVY)],
            cwd=MENTAT, capture_output=True, text=True, timeout=120)
        self.assertEqual(done.returncode, 0, done.stderr)
        loaded = json.loads(done.stdout.strip().splitlines()[-1])
        self.assertEqual(loaded, [], f'{sys.executable} loaded {loaded}')


class HexPins(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        path = geo_lookup.lookup_dir() / 'hex_pins.json'
        if not path.is_file():
            raise unittest.SkipTest('lookup/hex_pins.json is not built; run geo_assign.py tables')
        cls.pins = json.loads(path.read_text(encoding='utf-8'))
        cls.fields = cls.pins['fields']

    def rows(self):
        return [dict(zip(self.fields, values)) for values in self.pins['points']]

    def test_projection_within_a_millimetre(self):
        worst = 0.0
        for row in self.rows():
            x, y = geo_lookup.albers_5070(row['lat'], row['lon'])
            worst = max(worst, abs(x - row['x']), abs(y - row['y']))
        print(f'\n  {sys.executable}: EPSG:5070 worst {worst:.2e} m over {len(self.rows())} pins')
        self.assertLess(worst, 1e-3)

    def test_hex_ids_equal_the_tools(self):
        rows = self.rows()
        self.assertEqual(len(rows), 1000)
        for row in rows:
            for size in (10, 25, 50):
                self.assertEqual(geo_lookup.hex_id(row['lat'], row['lon'], size), row[f'h{size}'],
                                 (row['lat'], row['lon'], size))

    def test_hex_center_is_inside_its_own_hexagon(self):
        for row in self.rows()[:200]:
            for size in (10, 25, 50):
                centre = geo_lookup.hex_center(row[f'h{size}'])
                self.assertEqual(geo_lookup.hex_id(centre['latitude'], centre['longitude'], size),
                                 row[f'h{size}'])

    def test_negative_control_wrong_ellipsoid_breaks_the_pins(self):
        # the same formula on a sphere of the GRS80 semi-major axis
        def sphere(lat, lon):
            n = (math.sin(math.radians(29.5)) + math.sin(math.radians(45.5))) / 2
            c = math.cos(math.radians(29.5)) ** 2 + 2 * n * math.sin(math.radians(29.5))
            rho = 6378137.0 * math.sqrt(c - 2 * n * math.sin(math.radians(lat))) / n
            rho0 = 6378137.0 * math.sqrt(c - 2 * n * math.sin(math.radians(23.0))) / n
            theta = n * math.radians((lon + 96.0 + 180.0) % 360.0 - 180.0)
            return rho * math.sin(theta), rho0 - rho * math.cos(theta)
        differ = 0
        for row in self.rows():
            x, y = sphere(row['lat'], row['lon'])
            q, r = geo_lookup.hex_axial(x, y, 10)
            differ += f'h10:{q}:{r}' != row['h10']
        # measured 454 of 1,000 on 2026-09-25: the pins catch a spherical projection
        self.assertGreater(differ, 100)

    def test_negative_control_bankers_rounding_breaks_the_pins(self):
        def banker(x, y, size):
            side = geo_lookup.hex_side(size)
            qf = (math.sqrt(3.0) / 3.0 * x - y / 3.0) / side
            rf = (2.0 / 3.0 * y) / side
            return round(qf), round(rf)            # no cube correction, half-to-even
        differ = 0
        for row in self.rows():
            q, r = banker(row['x'], row['y'], 10)
            differ += f'h10:{q}:{r}' != row['h10']
        self.assertGreater(differ, 50)


class PrecisionTags(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        if not lookup_ready():
            raise unittest.SkipTest('lookup tables are not built; run geo_assign.py tables')
        table = json.loads((geo_lookup.lookup_dir() / 'tract' / '36.json').read_text(encoding='utf-8'))
        fields = table['fields']
        cls.ny12 = next(dict(zip(fields, values), tract=tract)
                        for tract, values in sorted(table['rows'].items())
                        if dict(zip(fields, values))['cd119'] == '3612')

    def test_address(self):
        answer = geo_lookup.locate(latitude=self.ny12['lat'], longitude=self.ny12['lon'],
                                   tract=self.ny12['tract'], zip='10028')
        self.assertEqual(answer['precision'], 'address')
        self.assertTrue(answer['located'])
        self.assertEqual(answer['geoid']['cd119'], '3612')
        self.assertEqual(answer['district']['119'], 'NY-12')
        self.assertEqual(answer['geoid']['county'], self.ny12['tract'][:5])
        self.assertEqual(answer['geoid_basis'], 'tract')
        self.assertEqual(answer['hex']['25'], geo_lookup.hex_id(self.ny12['lat'], self.ny12['lon'], 25))

    def test_zip(self):
        answer = geo_lookup.locate(zip='10028-1234', city='New York', state='NY')
        self.assertEqual(answer['precision'], 'zip')
        self.assertEqual(answer['geoid']['zcta'], '10028')
        self.assertEqual(answer['district']['119'], 'NY-12')
        self.assertEqual(answer['state'], 'NY')
        self.assertEqual(answer['hex']['10'], geo_lookup.hex_id(answer['point'][0],
                                                               answer['point'][1], 10))

    def test_city(self):
        answer = geo_lookup.locate(city='CHARLOTTE', state='NC')
        self.assertEqual(answer['precision'], 'city')
        self.assertEqual(answer['geoid']['place'], '3712000')
        self.assertEqual(answer['geoid']['county'], '37119')
        for spelling, state, place in (('ST LOUIS', 'MO', '2965000'), ('SAINT LOUIS', 'MO', '2965000'),
                                       ('NEW YORK CITY', 'NY', '3651000'),
                                       ('NASHVILLE', 'TN', '4752006'),
                                       ('WASHINGTON DC', 'DC', '1150000')):
            found = geo_lookup.locate(city=spelling, state=state)
            self.assertEqual(found['precision'], 'city', spelling)
            self.assertEqual(found['geoid']['place'], place, spelling)

    def test_connecticut_2020_tract_codes(self):
        """The geocoder's 2020 Connecticut codes (09001-09015) answer through TRACTCE."""
        census = json.loads((MENTAT / 'app' / 'geo' / 'tract_cd119.json').read_text(encoding='utf-8'))
        old = sorted(code for code in census['districts'] if code.startswith('09001'))[0]
        row = geo_lookup.for_tract(old)
        self.assertIsNotNone(row)
        self.assertEqual(row['tract_asked'], old)
        self.assertEqual(row['tract'][5:], old[5:])
        self.assertTrue(row['tract'][:5] in {f'091{n}0' for n in range(1, 10)})
        answer = geo_lookup.locate(tract=old)
        self.assertEqual(answer['precision'], 'address')
        number = census['districts'][old].split('-')[1]
        self.assertEqual(answer['geoid']['cd119'], '09' + number)

    def test_state_and_none(self):
        self.assertEqual(geo_lookup.locate(state='AK')['precision'], 'state')
        self.assertEqual(geo_lookup.locate(state='02')['geoid']['state'], '02')
        self.assertEqual(geo_lookup.locate()['precision'], 'none')
        self.assertFalse(geo_lookup.locate()['located'])

    def test_negative_controls(self):
        wrong_state = geo_lookup.locate(city='Charlotte', state='AK')
        self.assertEqual(wrong_state['precision'], 'state')
        self.assertIsNone(wrong_state['point'])
        self.assertEqual(geo_lookup.locate(zip='00000')['precision'], 'none')
        self.assertEqual(geo_lookup.locate(zip='00000', city='Anchorage', state='AK')['precision'],
                         'city')
        self.assertEqual(geo_lookup.locate(latitude=0, longitude=0)['precision'], 'none')

    def test_located_line(self):
        answers = [geo_lookup.locate(latitude=self.ny12['lat'], longitude=self.ny12['lon'],
                                     tract=self.ny12['tract']),
                   geo_lookup.locate(zip='28202'), geo_lookup.locate(city='Anchorage', state='AK'),
                   geo_lookup.locate(state='TX'), geo_lookup.locate()]
        self.assertEqual(geo_lookup.located_line(answers, 'point'), '3 of 5 located')
        self.assertEqual(geo_lookup.located_line(answers, 'tract'), '1 of 5 located')
        self.assertEqual(geo_lookup.located_line(answers, 'zip'), '2 of 5 located')
        self.assertEqual(geo_lookup.located_line(answers, 'state'), '4 of 5 located')
        self.assertEqual(geo_lookup.precision_counts(answers),
                         {'address': 1, 'zip': 1, 'city': 1, 'state': 1, 'none': 1})


if __name__ == '__main__':
    unittest.main()
