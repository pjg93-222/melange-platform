"""build_zip_centroids against the builder it reuses, then against its output.

Three halves, in the shape test_district_geo.py uses.

  * Pure tests that need nothing built: the gazetteer reader against a zip this
    test writes, the region/box helpers, and the proof that this tool's
    projection IS build_district_geo's - projecting a known lat/lon both ways
    and demanding the same pixel.
  * Tests that read the downloaded Census gazetteer and skip when it is absent.
  * Tests that read the generated mentat/app/geo/zcta_points.json and skip when
    it is absent, because it is built by

        python mentat/tools/build_zip_centroids.py
        python mentat/tests/test_zip_centroids.py
"""
import json
import math
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

MENTAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MENTAT / 'tools'))
import build_district_geo as builder                                # noqa: E402
import build_zip_centroids as centroids                             # noqa: E402

GEO = MENTAT / 'app' / 'geo'
CACHE = MENTAT / '.cache' / 'geo'
POINTS = GEO / 'zcta_points.json'
GAZETTEER = CACHE / centroids.GAZETTEER
STATE_ZIP = CACHE / centroids.STATE_ZIP

# Places whose ZCTA has to survive the region choice, one per placed region.
# (zip5, region, state, a lat/lon near it, a human name)
LANDMARKS = [
    ('20001', 'lower48', 'DC', 'Washington DC'),
    ('96813', 'hawaii', 'HI', 'Honolulu'),
    ('99501', 'alaska', 'AK', 'Anchorage'),
    ('00901', 'caribbean', 'PR', 'San Juan'),
]


def fake_gazetteer(path):
    """A gazetteer zip with the real file's trailing-space header quirk."""
    header = ('GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG'
              + ' ' * 40)
    rows = ['00601\t166836392\t798613\t64.416\t0.308\t18.180555\t-66.749961' + ' ' * 30,
            '20001\t2611853\t0\t1.008\t0.0\t38.9100\t-77.0176' + ' ' * 12,
            'ABCDE\t1\t0\t0.1\t0.0\t38.0\t-77.0',              # not a 5-digit ZCTA
            '99999\t1\t0']                                      # short row
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('2024_Gaz_zcta_national.txt',
                         '\n'.join([header] + rows) + '\n')
    return path


def albers_from_payload(longitude, latitude, region):
    """The projection a vintage file documents, implemented from its numbers.

    Deliberately a second implementation, the same one test_district_geo.py
    carries, so 'the tool reuses the builder' is checked against a third party
    rather than against itself.
    """
    first, second = (math.radians(value) for value in region['parallels'])
    n = (math.sin(first) + math.sin(second)) / 2
    c = math.cos(first) ** 2 + 2 * n * math.sin(first)
    rho = math.sqrt(max(c - 2 * n * math.sin(math.radians(latitude)), 0.0)) / n
    rho0 = math.sqrt(max(c - 2 * n * math.sin(math.radians(region['origin'])), 0.0)) / n
    delta = math.radians(longitude) - math.radians(region['meridian'])
    delta = delta - 2 * math.pi if delta > math.pi else delta + 2 * math.pi if delta < -math.pi else delta
    theta = n * delta
    x, y = rho * math.sin(theta), rho0 - rho * math.cos(theta)
    return (region['translate'][0] + region['scale'] * x,
            region['translate'][1] - region['scale'] * y)


class ReaderTests(unittest.TestCase):
    """The gazetteer reader and the region helpers, against written fixtures."""

    def test_the_reader_strips_the_trailing_spaces_the_census_header_carries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = fake_gazetteer(Path(tmp) / 'gaz.zip')
            rows, malformed = centroids.read_gazetteer(path)
        self.assertEqual([row[0] for row in rows], ['00601', '20001'])
        self.assertAlmostEqual(rows[0][1], 18.180555)
        self.assertAlmostEqual(rows[0][2], -66.749961)
        self.assertEqual(malformed, 2)      # the non-ZCTA row and the short row

    def test_a_gazetteer_with_an_unexpected_header_refuses_rather_than_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'gaz.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('g.txt', 'ZIP\tLAT\tLON\n00601\t1\t2\n')
            with self.assertRaises(ValueError):
                centroids.read_gazetteer(path)

    def test_states_come_off_the_crosswalk_and_a_missing_crosswalk_is_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            geo = Path(tmp)
            self.assertEqual(centroids.zcta_states(geo), {})
            (geo / 'zcta_cd119.json').write_text(json.dumps(
                {'94607': [['CA-12', 1.0]], '95111': [['CA-13', 0.72], ['CA-12', 0.28]],
                 '00601': [['PR-AL', 1.0]], '00000': []}), encoding='utf-8')
            self.assertEqual(centroids.zcta_states(geo),
                             {'94607': 'CA', '95111': 'CA', '00601': 'PR'})

    def test_the_box_order_asks_the_insets_before_the_catch_all(self):
        # region_of treats lower48 as the region that claims every STATEFP no
        # other region lists, so the box test has to ask it last or an Alaskan
        # point could be claimed by the lower-48 box.
        self.assertEqual(centroids.BOX_ORDER[-1], 'lower48')
        self.assertEqual(sorted(centroids.BOX_ORDER), sorted(builder.REGIONS))

    def test_the_meridian_guard_keeps_guam_out_of_the_lower_forty_eight(self):
        self.assertGreater(centroids.meridian_delta(144.8, 'lower48'),
                           centroids.MAX_MERIDIAN_DELTA)
        self.assertLess(centroids.meridian_delta(-96.5, 'lower48'), 1.0)
        self.assertLess(centroids.meridian_delta(-157.9, 'hawaii'), 1.0)

    def test_project_point_is_the_builders_own_screen_transform(self):
        # A hand-made placement, so this tests the transform and not the fit.
        placements = {'lower48': (1000.0, 480.0, 300.0)}
        got = centroids.project_point(-96.0, 37.5, 'lower48', placements)
        x, y = builder.projector('lower48')(-96.0, 37.5)
        self.assertEqual(got, (480.0 + 1000.0 * x, 300.0 - 1000.0 * y))
        self.assertIsNone(centroids.project_point(-96.0, 37.5, 'samoa', placements))


@unittest.skipUnless(STATE_ZIP.is_file() and (GEO / 'cd119.json').is_file(),
                     f'{STATE_ZIP.name} or cd119.json is absent; '
                     'run mentat/tools/build_district_geo.py first')
class PlacementTests(unittest.TestCase):
    """The placements this tool projects with are the shipped ones."""

    @classmethod
    def setUpClass(cls):
        cls.placements, cls.codes, cls.fips_of = centroids.canvas_placements(
            CACHE, log=lambda *_: None)
        cls.vintage = json.loads((GEO / 'cd119.json').read_text(encoding='utf-8'))

    def test_the_recomputed_placements_match_the_ones_cd119_ships(self):
        shipped = self.vintage['projection']['regions']
        self.assertEqual(sorted(shipped), sorted(self.placements))
        for name, region in shipped.items():
            scale, tx, ty = self.placements[name]
            self.assertAlmostEqual(scale, region['scale'], places=6, msg=name)
            self.assertAlmostEqual(tx, region['translate'][0], places=6, msg=name)
            self.assertAlmostEqual(ty, region['translate'][1], places=6, msg=name)

    def test_projecting_dc_with_this_tool_equals_projecting_it_with_the_builder(self):
        # The proof the projection was reused and not rewritten: this tool's
        # project_point, the builder's own project_rings, and the independent
        # implementation above all have to agree on the same pixel. The ring is
        # a degree-wide triangle round Washington because project_rings drops a
        # ring that simplifies below three distinct points.
        longitude, latitude = -77.0369, 38.9072
        corners = [(longitude, latitude), (longitude + 1.0, latitude),
                   (longitude, latitude + 1.0)]
        rings, region = builder.project_rings([corners], '11', self.placements)
        self.assertEqual(region, 'lower48')
        self.assertEqual(len(rings), 1)
        for corner, drawn in zip(corners, rings[0]):
            mine = centroids.project_point(corner[0], corner[1], 'lower48', self.placements)
            self.assertEqual(tuple(round(value, builder.DECIMALS) for value in mine), drawn)
        mine = centroids.project_point(longitude, latitude, 'lower48', self.placements)
        theirs = albers_from_payload(longitude, latitude,
                                     self.vintage['projection']['regions']['lower48'])
        self.assertAlmostEqual(mine[0], theirs[0], places=3)
        self.assertAlmostEqual(mine[1], theirs[1], places=3)
        x0, y0, x1, y1 = self.vintage['districts']['DC-AL']['bbox']
        self.assertTrue(x0 <= mine[0] <= x1 and y0 <= mine[1] <= y1, mine)


@unittest.skipUnless(GAZETTEER.is_file(),
                     f'{GAZETTEER.name} is not in mentat/.cache/geo')
class GazetteerTests(unittest.TestCase):
    """The real gazetteer, read but not rebuilt."""

    @classmethod
    def setUpClass(cls):
        cls.rows, cls.malformed = centroids.read_gazetteer(GAZETTEER)

    def test_the_real_gazetteer_is_all_five_digit_zctas_with_usable_points(self):
        self.assertGreater(len(self.rows), 30000)
        self.assertEqual(self.malformed, 0)
        for zcta, latitude, longitude in self.rows:
            self.assertRegex(zcta, r'^\d{5}$')
            self.assertTrue(-90 <= latitude <= 90, zcta)
            self.assertTrue(-180 <= longitude <= 180, zcta)

    def test_every_landmark_zip_is_in_the_gazetteer(self):
        have = {zcta for zcta, _lat, _lon in self.rows}
        for zip5, _region, _state, name in LANDMARKS:
            self.assertIn(zip5, have, name)


@unittest.skipUnless(POINTS.is_file(),
                     'run mentat/tools/build_zip_centroids.py first')
class BuiltPointsTests(unittest.TestCase):
    """The generated zcta_points.json, read the way the page will read it."""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(POINTS.read_text(encoding='utf-8'))
        cls.points = cls.payload['points']

    def test_the_file_is_the_shape_the_page_expects(self):
        self.assertEqual(self.payload['canvas'], [builder.WIDTH, builder.HEIGHT])
        self.assertEqual(self.payload['vintage'], centroids.VINTAGE)
        self.assertEqual(self.payload['count'], len(self.points))
        self.assertGreater(self.payload['count'], 30000)
        for zip5, value in self.points.items():
            self.assertRegex(zip5, r'^\d{5}$')
            self.assertEqual(len(value), 4, zip5)
            self.assertIsInstance(value[0], float)
            self.assertIsInstance(value[1], float)
            self.assertIn(value[2], builder.REGIONS, zip5)
            self.assertRegex(value[3], r'^[A-Z]{2}$|^$')

    def test_every_point_is_on_the_canvas(self):
        for zip5, (x, y, _region, _state) in self.points.items():
            self.assertTrue(0 <= x <= builder.WIDTH, f'{zip5} x={x}')
            self.assertTrue(0 <= y <= builder.HEIGHT, f'{zip5} y={y}')

    def test_every_point_is_in_its_region_box_unless_the_file_names_it(self):
        named = self.payload.get('outside_region_box', {})
        for zip5, (x, y, region, _state) in self.points.items():
            x0, y0, x1, y1 = builder.REGIONS[region]['box']
            if x0 <= x <= x1 and y0 <= y <= y1:
                self.assertNotIn(zip5, named, f'{zip5} is in its box but listed as outside')
            else:
                self.assertEqual(named.get(zip5), region,
                                 f'{zip5} ({x}, {y}) is outside the {region} box and unnamed')

    def test_point_roberts_is_kept_rather_than_dropped_for_missing_a_layout_box(self):
        # ZCTA 98281 is the Washington exclave below Vancouver. The 20m state
        # file generalises it away, so the lower-48 fit never saw it and it
        # projects just above the top of the lower-48 box - on the canvas, and
        # a real US ZIP, so it stays and the file says why.
        self.assertIn('98281', self.points)
        x, y, region, state = self.points['98281']
        self.assertEqual((region, state), ('lower48', 'WA'))
        self.assertTrue(0 <= x <= builder.WIDTH and 0 <= y <= builder.HEIGHT)
        self.assertEqual(self.payload['outside_region_box'].get('98281'), 'lower48')
        self.assertLess(y, builder.REGIONS['lower48']['box'][1])
        self.assertLess(builder.REGIONS['lower48']['box'][1] - y, 5.0,
                        'a point this far outside the box is a projection fault, not an exclave')

    def test_the_landmarks_land_in_their_own_region_and_state(self):
        for zip5, region, state, name in LANDMARKS:
            self.assertIn(zip5, self.points, name)
            x, y, got_region, got_state = self.points[zip5]
            self.assertEqual(got_region, region, name)
            self.assertEqual(got_state, state, name)
            x0, y0, x1, y1 = builder.REGIONS[region]['box']
            self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1,
                            f'{name} ({x}, {y}) outside the {region} box')

    def test_the_insets_are_where_the_canvas_puts_them_not_where_the_globe_does(self):
        # Anchorage and Honolulu sit BELOW and LEFT of Washington on this
        # canvas because they are insets; a point that merely projected with
        # the lower-48 conic would be off the west edge instead.
        dc_x, dc_y = self.points['20001'][0], self.points['20001'][1]
        for zip5 in ('99501', '96813'):
            x, y = self.points[zip5][0], self.points[zip5][1]
            self.assertLess(x, dc_x, zip5)
            self.assertGreater(y, dc_y, zip5)

    def test_what_was_omitted_is_named_and_is_only_the_unplaced_territories(self):
        omitted = self.payload.get('omitted', {})
        self.assertIsInstance(omitted, dict)
        self.assertTrue(omitted, 'AS, GU and MP cannot be placed; an empty list is suspect')
        for zip5, state in omitted.items():
            self.assertRegex(zip5, r'^\d{5}$')
            # AS/GU/MP have no 20m boundary, so build_district_geo fits no
            # placement for their regions. Nothing else may be dropped - the
            # US Virgin Islands were expected here and are NOT: all six VI
            # ZCTAs land inside the caribbean box.
            self.assertIn(state, {'AS', 'GU', 'MP'}, f'{zip5} -> {state}')
            self.assertNotIn(zip5, self.points)

    def test_the_virgin_islands_are_placed_in_the_caribbean_inset(self):
        virgin = {zip5: value for zip5, value in self.points.items() if value[3] == 'VI'}
        self.assertTrue(virgin, 'VI ZCTAs must be on the canvas, not omitted')
        x0, y0, x1, y1 = builder.REGIONS['caribbean']['box']
        for zip5, (x, y, region, _state) in virgin.items():
            self.assertEqual(region, 'caribbean', zip5)
            self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1, f'{zip5} ({x}, {y})')

    @unittest.skipUnless(GAZETTEER.is_file(), 'no gazetteer to compare against')
    def test_every_gazetteer_zcta_is_either_placed_or_named_in_omitted(self):
        rows, _malformed = centroids.read_gazetteer(GAZETTEER)
        omitted = self.payload.get('omitted', {})
        for zcta, _lat, _lon in rows:
            self.assertTrue(zcta in self.points or zcta in omitted, zcta)
        self.assertEqual(len(rows), len(self.points) + len(omitted))


if __name__ == '__main__':
    unittest.main(verbosity=2)
