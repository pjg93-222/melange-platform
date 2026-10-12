"""build_state_legislative_geo against fixtures, then against its output.

The shape test_district_geo.py uses. The fixture half needs nothing built and
always runs; the rest reads what is under mentat/app/geo/sld and skips when it
is absent, because those files are built from Census downloads by

    python mentat/tools/build_state_legislative_geo.py
    python mentat/tests/test_state_legislative_geo.py

WHAT THE CENSUS FILES ACTUALLY CONTAIN, which these tests assert rather than
assume (counted 2026-09-19 from cb_2024_us_sldu_500k.zip and ..._sldl_500k.zip):

  Texas      31 upper, 150 lower - the numbers the Texas constitution fixes
  Nebraska   49 upper, NO lower record at all: the legislature is unicameral,
             so the tool writes no sld/lower/NE.json
  DC          8 upper, no lower. The Census files the eight DC Council wards
             as the upper chamber, so DC has an upper file. The brief expected
             DC to have neither; the file says otherwise, and the file wins.
  Puerto Rico 8 upper, 40 lower
  AS, GU, MP, VI  absent from both files, as they are from cd119.json
"""
import json
import sys
import unittest
from pathlib import Path

MENTAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MENTAT / 'tools'))
import build_district_geo as builder                                # noqa: E402
import build_state_legislative_geo as sld                           # noqa: E402

GEO = MENTAT / 'app' / 'geo'
CACHE = MENTAT / '.cache' / 'geo'
SLD = GEO / 'sld'
INDEX = SLD / 'index.json'
ZCTA_SLD = GEO / 'zcta_sld.json'
UPPER_ZIP = CACHE / sld.CHAMBERS['upper'][0]
LOWER_ZIP = CACHE / sld.CHAMBERS['lower'][0]

# Downtown Austin. Travis County is split across many seats, so the test
# asserts the ZIP lands in SOME Texas seat that the Texas files contain,
# which is the claim zcta_sld.json actually makes.
AUSTIN = '78701'


class FixtureTests(unittest.TestCase):
    """The helpers, against rings this test wrote."""

    def test_closed_repeats_the_first_point_and_drops_degenerate_rings(self):
        square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        self.assertEqual(sld.closed([square]), [square + [square[0]]])
        self.assertEqual(sld.closed([square + [square[0]]]), [square + [square[0]]])
        self.assertEqual(sld.closed([[(0.0, 0.0), (1.0, 1.0)]]), [])

    def test_an_unclosed_ring_would_lose_an_edge_without_closed(self):
        # The reason closed() exists: point_in_rings walks len(ring) - 1
        # segments, so an open ring silently omits the closing edge - here the
        # square's west side - and a point to the WEST of it reads as inside.
        open_ring = [(0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)]
        outside_to_the_west = (-1.0, 2.0)
        self.assertTrue(builder.point_in_rings([open_ring], outside_to_the_west),
                        'the open ring is expected to answer wrongly')
        self.assertFalse(builder.point_in_rings(sld.closed([open_ring]),
                                                outside_to_the_west))
        self.assertTrue(builder.point_in_rings(sld.closed([open_ring]), (1.0, 2.0)))

    def test_the_basis_sentence_is_the_one_the_console_must_show(self):
        self.assertEqual(sld.BASIS,
                         'ZCTA internal point in district polygon; a ZIP that straddles '
                         'districts is assigned to the one containing its internal point')

    def test_the_tool_restores_the_builders_tolerance_it_borrows(self):
        # build() dials builder.MIN_STEP down for the run and must put it back,
        # or a later build_district_geo call in the same process would re-cut
        # the congressional paths at the wrong tolerance.
        self.assertEqual(builder.MIN_STEP, 0.5)
        self.assertEqual(sld.DEFAULT_MIN_STEP, 0.1)


@unittest.skipUnless(UPPER_ZIP.is_file() and LOWER_ZIP.is_file(),
                     f'{UPPER_ZIP.name} / {LOWER_ZIP.name} are not in mentat/.cache/geo')
class CensusFileTests(unittest.TestCase):
    """What the downloaded shapefiles contain, read but not rebuilt."""

    @classmethod
    def setUpClass(cls):
        quiet = lambda *_a, **_k: None                               # noqa: E731
        cls.upper, _url, cls.upper_no_seat = sld.read_chamber(CACHE, 'upper', log=quiet)
        cls.lower, _url, cls.lower_no_seat = sld.read_chamber(CACHE, 'lower', log=quiet)

    def test_the_upper_file_has_texas_and_nebraska_and_dc_and_the_lower_has_no_nebraska(self):
        self.assertEqual(len(self.upper['48']), 31)          # Texas Senate
        self.assertEqual(len(self.lower['48']), 150)         # Texas House
        self.assertEqual(len(self.upper['31']), 49)          # the Nebraska unicameral
        self.assertNotIn('31', self.lower, 'Nebraska has no lower chamber')
        self.assertEqual(len(self.upper['11']), 8, 'the Census files the DC wards as upper')
        self.assertNotIn('11', self.lower, 'DC has no lower chamber in the Census file')
        for statefp in ('60', '66', '69', '78'):
            self.assertNotIn(statefp, self.upper)
            self.assertNotIn(statefp, self.lower)

    def test_the_water_records_are_dropped_so_the_seat_counts_are_the_real_ones(self):
        # Three "ZZZ" records are water, not seats. With them dropped the
        # counts are the ones those states elect: Louisiana 39 senators,
        # Wisconsin 33 senators and 99 assembly members.
        self.assertEqual([entry['geoid'] for entry in self.upper_no_seat],
                         ['22ZZZ', '55ZZZ'])
        self.assertEqual([entry['geoid'] for entry in self.lower_no_seat], ['55ZZZ'])
        for entry in self.upper_no_seat + self.lower_no_seat:
            self.assertIn('not defined', entry['name'])
        self.assertEqual(len(self.upper['22']), 39)     # Louisiana Senate
        self.assertEqual(len(self.upper['55']), 33)     # Wisconsin Senate
        self.assertEqual(len(self.lower['55']), 99)     # Wisconsin Assembly
        self.assertEqual(len(self.lower['22']), 105)    # Louisiana House, no water record

    def test_has_seat_matches_district_keys_rule_for_water(self):
        self.assertFalse(sld.has_seat('ZZZ'))
        self.assertFalse(sld.has_seat('zzz'))
        self.assertFalse(sld.has_seat(''))
        self.assertFalse(sld.has_seat(None))
        self.assertTrue(sld.has_seat('00A'))            # an Alaska senate seat
        self.assertTrue(sld.has_seat('D01'))            # a Massachusetts one
        self.assertTrue(sld.has_seat('014'))
        self.assertIsNone(builder.district_key('LA', 'ZZ'))


@unittest.skipUnless(INDEX.is_file(),
                     'run mentat/tools/build_state_legislative_geo.py first')
class BuiltIndexTests(unittest.TestCase):
    """sld/index.json, read the way the page will read it."""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads(INDEX.read_text(encoding='utf-8'))

    def test_the_index_names_both_chambers_the_basis_and_the_canvas(self):
        self.assertEqual(sorted(self.index['chambers']), ['lower', 'upper'])
        self.assertEqual(self.index['basis'], sld.BASIS)
        self.assertEqual(self.index['canvas'], [builder.WIDTH, builder.HEIGHT])
        self.assertEqual(self.index['vintage'], sld.VINTAGE)

    def test_every_state_the_index_lists_has_the_file_and_the_size_it_claims(self):
        # Sizes are LF sizes (the bytes git stores), so this holds on a CRLF checkout
        # and on an LF export alike (lane F2c, 2026-09-26: the index recorded the
        # CRLF-checkout size and an eol=lf export failed here by 1 byte per file).
        for chamber, entry in self.index['chambers'].items():
            self.assertEqual(len(entry['states']), entry['files'], chamber)
            for state, info in entry['states'].items():
                path = GEO / info['file']
                self.assertTrue(path.is_file(), info['file'])
                self.assertEqual(sld.lf_size(path), info['bytes'], info['file'])
                self.assertEqual(len(path.read_bytes().replace(b'\r\n', b'\n')), info['bytes'], info['file'])
                payload = json.loads(path.read_text(encoding='utf-8'))
                self.assertEqual(len(payload['districts']), info['districts'], info['file'])
                self.assertEqual(payload['state'], state)
                self.assertEqual(payload['chamber'], chamber)

    def test_texas_has_thirty_one_senators_and_a_hundred_and_fifty_representatives(self):
        self.assertEqual(self.index['chambers']['upper']['states']['TX']['districts'], 31)
        self.assertEqual(self.index['chambers']['lower']['states']['TX']['districts'], 150)

    def test_the_shipped_counts_are_seats_and_the_water_records_are_recorded(self):
        upper, lower = self.index['chambers']['upper'], self.index['chambers']['lower']
        self.assertEqual(upper['districts'], 1958)      # 1,960 records less 2 water
        self.assertEqual(lower['districts'], 4873)      # 4,874 records less 1 water
        self.assertEqual([entry['geoid'] for entry in upper['no_seat']], ['22ZZZ', '55ZZZ'])
        self.assertEqual([entry['geoid'] for entry in lower['no_seat']], ['55ZZZ'])
        self.assertEqual(upper['states']['LA']['districts'], 39)
        self.assertEqual(upper['states']['WI']['districts'], 33)
        self.assertEqual(lower['states']['WI']['districts'], 99)

    def test_nebraska_is_unicameral_and_dc_has_only_the_council_wards(self):
        self.assertEqual(self.index['chambers']['upper']['states']['NE']['districts'], 49)
        self.assertNotIn('NE', self.index['chambers']['lower']['states'])
        self.assertFalse((SLD / 'lower' / 'NE.json').is_file(),
                         'Nebraska has no lower chamber, so no file may exist')
        self.assertEqual(self.index['chambers']['upper']['states']['DC']['districts'], 8)
        self.assertNotIn('DC', self.index['chambers']['lower']['states'])
        self.assertFalse((SLD / 'lower' / 'DC.json').is_file())

    def test_no_district_was_lost_to_the_simplifier(self):
        for chamber, entry in self.index['chambers'].items():
            self.assertEqual(entry['collapsed'], {},
                             f'{chamber}: districts dropped by the simplifier')

    def test_the_sub_pixel_districts_are_named_and_stay_a_small_minority(self):
        # The honest limit of a 975x610 map of the whole country: a city seat
        # is under a pixel across. 29 of 1,958 upper and 264 of 4,873 lower
        # districts are - Manhattan, Providence, Hartford, Anchorage, the DC
        # wards. They ship and they are clickable, and only a zoom makes them
        # readable, so the index names every one rather than letting the page
        # discover it. The bound catches a projection fault, where the count
        # would be a large fraction rather than a handful of dense cities.
        for chamber, entry in self.index['chambers'].items():
            named = sum(len(value) for value in entry['subpixel'].values())
            self.assertTrue(entry['subpixel'], chamber)
            self.assertLess(named, entry['districts'] * 0.10, chamber)
            for state, geoids in entry['subpixel'].items():
                self.assertIn(state, self.index['chambers'][chamber]['states'], chamber)
                self.assertEqual(len(geoids), len(set(geoids)), f'{chamber}/{state}')
            everything = {geoid for geoids in entry['subpixel'].values() for geoid in geoids}
            for geoid in entry['anchor_fallback']:
                # A district with no interior point for centroid_of to find is
                # always one of these; anywhere else it would be a real fault.
                self.assertIn(geoid, everything, f'{chamber} {geoid}')

    def test_only_the_northwestern_hawaiian_islands_are_clipped_and_nothing_falls_off(self):
        # The one place where geometry is dropped rather than drawn, and the
        # index has to name it: HI Senate 25 and HI House 51 reach out to Kure
        # Atoll, 1,300 miles west of the Hawaii inset the canvas was fitted to.
        # Lane L3, 2026-09-26: the Hawaii inset is clipped to its extent, as d3
        # clips it, so all fifteen NWHI rings of each are removed and counted
        # under clipped_rings (their two Oahu rings stay), and no ring is left
        # to fall off the canvas. Before: offcanvas_rings named 15025 and 15051
        # (2 rings each), and five more NWHI rings of each drew west of the
        # inset, inside the Alaska box.
        for chamber, geoid in (('upper', '15025'), ('lower', '15051')):
            entry = self.index['chambers'][chamber]
            self.assertEqual(entry['offcanvas_rings'], {}, chamber)
            self.assertEqual(entry['clipped_rings'], {geoid: 15}, chamber)

    def test_the_largest_file_stays_inside_the_per_state_budget(self):
        for chamber, entry in self.index['chambers'].items():
            self.assertLess(entry['largest_file']['bytes'], 600 * 1024,
                            f'{chamber} largest file {entry["largest_file"]}')


@unittest.skipUnless(INDEX.is_file(),
                     'run mentat/tools/build_state_legislative_geo.py first')
class BuiltGeometryTests(unittest.TestCase):
    """Every per-state file's paths and boxes."""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads(INDEX.read_text(encoding='utf-8'))
        cls.files = {}
        for chamber, entry in cls.index['chambers'].items():
            for state, info in entry['states'].items():
                cls.files[(chamber, state)] = json.loads(
                    (GEO / info['file']).read_text(encoding='utf-8'))

    def test_every_path_starts_with_m_and_every_box_is_on_the_canvas(self):
        for (chamber, state), payload in self.files.items():
            self.assertIn(payload['region'], builder.REGIONS, f'{chamber}/{state}')
            for district in payload['districts']:
                where = f'{chamber}/{state}/{district["id"]}'
                self.assertTrue(district['path'].startswith('M'), where)
                self.assertTrue(district['path'].rstrip().endswith('Z'), where)
                x0, y0, x1, y1 = district['bbox']
                self.assertTrue(0 <= x0 <= x1 <= builder.WIDTH, where)
                self.assertTrue(0 <= y0 <= y1 <= builder.HEIGHT, where)
                self.assertIsNotNone(district['anchor'], f'{where} has no anchor')
                ax, ay = district['anchor']
                self.assertTrue(x0 <= ax <= x1 and y0 <= ay <= y1,
                                f'{where} anchor {district["anchor"]} outside its bbox')
                # STATEFP plus a three-character district code, which is NOT
                # always digits: Alaska letters its senate districts (0200A),
                # Massachusetts codes its named ones D01..D40, Vermont uses
                # town abbreviations (50ADD) and hyphens (50A-1).
                self.assertRegex(district['id'], r'^\d{2}[0-9A-Z-]{3}$', where)
                self.assertNotEqual(district['id'][2:], 'ZZZ',
                                    f'{where} is water, not a seat')
                self.assertTrue(district['name'], where)
                self.assertNotIn('not defined', district['name'], where)

    def test_every_district_id_is_unique_within_its_file_and_starts_with_the_statefp(self):
        for (chamber, state), payload in self.files.items():
            statefp = self.index['chambers'][chamber]['states'][state]['statefp']
            ids = [district['id'] for district in payload['districts']]
            self.assertEqual(len(ids), len(set(ids)), f'{chamber}/{state}')
            for value in ids:
                self.assertTrue(value.startswith(statefp), f'{chamber}/{state}/{value}')


@unittest.skipUnless(ZCTA_SLD.is_file() and INDEX.is_file(),
                     'run mentat/tools/build_state_legislative_geo.py first')
class ZipAssignmentTests(unittest.TestCase):
    """zcta_sld.json against the state files it points at."""

    @classmethod
    def setUpClass(cls):
        cls.table = json.loads(ZCTA_SLD.read_text(encoding='utf-8'))
        cls.index = json.loads(INDEX.read_text(encoding='utf-8'))
        cls.ids = {'upper': set(), 'lower': set()}
        for chamber, entry in cls.index['chambers'].items():
            for _state, info in entry['states'].items():
                payload = json.loads((GEO / info['file']).read_text(encoding='utf-8'))
                cls.ids[chamber].update(district['id'] for district in payload['districts'])

    def test_the_table_carries_the_basis_the_console_must_show(self):
        self.assertEqual(self.table['basis'], sld.BASIS)

    def test_every_key_is_a_zip_or_the_basis_and_every_value_is_a_pair(self):
        for key, value in self.table.items():
            if key == 'basis':
                continue
            self.assertRegex(key, r'^\d{5}$')
            self.assertEqual(len(value), 2, key)
            self.assertIsInstance(value[0], str)
            self.assertIsInstance(value[1], str)

    def test_every_district_id_in_the_table_exists_in_a_state_file(self):
        for key, value in self.table.items():
            if key == 'basis':
                continue
            upper, lower = value
            if upper:
                self.assertIn(upper, self.ids['upper'], f'{key} upper')
            if lower:
                self.assertIn(lower, self.ids['lower'], f'{key} lower')

    def test_an_austin_zip_lands_in_a_texas_upper_and_lower_district(self):
        self.assertIn(AUSTIN, self.table)
        upper, lower = self.table[AUSTIN]
        self.assertTrue(upper.startswith('48'), f'{AUSTIN} upper={upper!r}')
        self.assertTrue(lower.startswith('48'), f'{AUSTIN} lower={lower!r}')
        # 48014 is Texas Senate District 14 and 48049 is House District 49;
        # both are the seats that cover downtown Austin, so this pins the ray
        # cast to a right answer and not merely to a Texas-shaped one.
        self.assertEqual((upper, lower), ('48014', '48049'))
        texas_upper = json.loads((SLD / 'upper' / 'TX.json').read_text(encoding='utf-8'))
        texas_lower = json.loads((SLD / 'lower' / 'TX.json').read_text(encoding='utf-8'))
        by_id = {d['id']: d['name'] for d in texas_upper['districts']}
        self.assertEqual(by_id.get(upper), 'State Senate District 14')
        self.assertIn(lower, {d['id'] for d in texas_lower['districts']})

    def test_a_nebraska_zip_has_an_upper_seat_and_no_lower_seat(self):
        # The unicameral legislature, seen from the ZIP side: Omaha.
        upper, lower = self.table['68102']
        self.assertTrue(upper.startswith('31'), upper)
        self.assertEqual(lower, '', 'Nebraska has no lower chamber to assign to')

    def test_the_unassigned_counts_the_index_reports_match_the_table(self):
        for chamber, slot in (('upper', 0), ('lower', 1)):
            placed = sum(1 for key, value in self.table.items()
                         if key != 'basis' and value[slot])
            stats = self.index['chambers'][chamber]['zcta_assignment']
            self.assertEqual(placed, stats['placed'], chamber)
            total = len(self.table) - 1
            self.assertEqual(total - placed,
                             stats['state_has_no_chamber'] + stats['in_no_district'], chamber)

    def test_the_zips_that_fell_in_no_polygon_are_named_not_merely_counted(self):
        for chamber, slot in (('upper', 0), ('lower', 1)):
            stats = self.index['chambers'][chamber]['zcta_assignment']
            named = stats['in_no_district_zctas']
            self.assertEqual(len(named), stats['in_no_district'], chamber)
            for zcta in named:
                self.assertEqual(self.table[zcta][slot], '', f'{chamber} {zcta}')
            # These are coastal generalisation misses, not a broken ray cast:
            # a percent of the country falling outside every polygon would be.
            self.assertLess(len(named), len(self.table) / 100, chamber)


if __name__ == '__main__':
    unittest.main(verbosity=2)
