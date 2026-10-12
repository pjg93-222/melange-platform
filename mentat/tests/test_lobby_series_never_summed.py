"""Lobbying: the in-house series (LDA expenses_total) and the fees outside firms report (income_total) are never added (D-064, Paul).

An in-house filer's reported expenses already include what it pays outside firms, so their sum counts that money twice. Nothing the console serves for the lobbying card may
carry the sum: not the history (catalog.anchors.industries.<id>.lda_history), not the nowcast rows (each series with its own projection and its own bands). Telecom's real levels
(Melange sector_spend.csv: 2025 in-house 102,385,884.55, outside-firm fees 37,000,500) are the fixture.

Also the display-only presidential-cycle ratio of the industry's own audited workbooks (FIX 7): the helper, the zip reader of the workbook's Spending Overview rows, and the catalog field.
"""
import copy
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'app'))
sys.path.insert(0, str(ROOT / 'forecast' / 'contracts'))
import giving_forecast as gf  # noqa: E402
from test_giving_forecast_v2 import TELECOM, World, keys_of, telecom_files  # noqa: E402

try:
    import openpyxl
except ImportError:         # the reader is exercised on a workbook this test writes
    openpyxl = None

TELECOM_2025 = dict(in_house=102385884.55, fees=37000500.0)
COUNTS = {'2025': {'organizations': 24, 'lobbyists': 532}, '2026': {'organizations': 23, 'lobbyists': 479}}


def numbers_of(node):
    """Every number anywhere in a nested payload."""
    if isinstance(node, dict):
        for value in node.values():
            yield from numbers_of(value)
    elif isinstance(node, (list, tuple)):
        for value in node:
            yield from numbers_of(value)
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        yield node


def cents(values):
    return {round(v * 100) for v in values}


def telecom_world_files():
    bundle, anchors = telecom_files()
    anchors['industries'][TELECOM]['lda']['counts'] = copy.deepcopy(COUNTS)
    return bundle, anchors


def series_sums(lda):
    """The two series added, year by year (annual and partial): the numbers that must appear nowhere."""
    fees, house = lda['series']['income_total'], lda['series']['expenses_total']
    sums = [fees['annual'][y] + house['annual'][y] for y in fees['annual']]
    return sums + [fees['partial']['total'] + house['partial']['total']]


class HistoryTests(unittest.TestCase):
    def test_the_history_rows_carry_the_two_series_apart_and_no_total_or_sum(self):
        bundle, anchors = telecom_world_files()
        lda = anchors['industries'][TELECOM]['lda']
        history = gf._lda_history(lda)
        row = next(r for r in history['annual'] if r['year'] == 2025)
        self.assertEqual((row['hired_firms'], row['in_house'], row['orgs'], row['lobbyists']), (TELECOM_2025['fees'], TELECOM_2025['in_house'], 24, 532))
        self.assertNotIn('total', set(keys_of(history)))
        self.assertEqual(cents(series_sums(lda)) & cents(numbers_of(history)), set())
        self.assertNotIn(round((TELECOM_2025['in_house'] + TELECOM_2025['fees']) * 100), cents(numbers_of(history)))        # 139,386,384.55

    def test_the_catalog_serves_the_history_without_a_sum(self):
        bundle, anchors = telecom_world_files()
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle, anchors=anchors):
            entry = gf.catalog('2026-09-30')['anchors']['industries'][TELECOM]
        self.assertNotIn('total', set(keys_of(entry['lda_history'])))
        self.assertEqual(cents(series_sums(anchors['industries'][TELECOM]['lda'])) & cents(numbers_of(entry['lda_history'])), set())


class NowcastTests(unittest.TestCase):
    def project(self):
        bundle, anchors = telecom_world_files()
        with tempfile.TemporaryDirectory() as tmp, World(tmp, bundle=bundle, anchors=anchors):
            return gf.project({'industry': TELECOM, 'as_of': '2026-09-30'}), anchors['industries'][TELECOM]['lda']

    def test_each_series_carries_its_own_projection_and_bands_and_nothing_is_added(self):
        out, lda = self.project()
        blocks = [h['lobbying'] for h in out['horizons']]
        self.assertEqual([b['status'] for b in blocks], ['ready', 'ready'])
        rows = [row for b in blocks for row in b['rows']]
        self.assertEqual([r['year'] for r in rows], [2026, 2027, 2028])
        for row in rows:
            self.assertEqual([s['id'] for s in row['series']], ['income_total', 'expenses_total'])
            for s in row['series']:
                self.assertLess(s['band95_low'], s['band80_low'])
                self.assertLess(s['band80_low'], s['projected'])
                self.assertLess(s['projected'], s['band80_high'])
                self.assertLess(s['band80_high'], s['band95_high'])
            self.assertEqual(set(row) & {'total', 'projected', 'band80_low', 'band80_high', 'combined'}, set(), 'no figure at the level of the row, only inside a series')
        self.assertNotIn('total', set(keys_of(blocks)))
        everything = cents(numbers_of(blocks))
        self.assertEqual(cents(series_sums(lda)) & everything, set(), 'neither the 2025 sum nor the 2026 partial sum is in the payload')
        for row in rows:                                    # nor the sum of the two projections or of the two bands of any year
            fees, house = row['series']
            for key in ('base_level', 'projected', 'band80_low', 'band80_high', 'band95_low', 'band95_high'):
                self.assertNotIn(round((fees[key] + house[key]) * 100), everything, f"{row['year']} {key}")

    def test_the_two_projections_are_the_nowcasts_of_the_two_series(self):
        out, lda = self.project()
        fees, house = out['horizons'][0]['lobbying']['rows'][0]['series']
        self.assertAlmostEqual(fees['projected'], lda['series']['income_total']['partial']['nowcast']['point'], places=6)
        self.assertAlmostEqual(house['projected'], lda['series']['expenses_total']['partial']['nowcast']['point'], places=6)


class OwnCycleRatioTests(unittest.TestCase):
    TOTALS = {'2021-2022': 12463873.75000004, '2023-2024': 17978153.890000038, '2025-2026': 7256093.449999991}

    def test_the_ratio_is_the_latest_presidential_cycle_over_the_midterm_before_it(self):
        got = gf._own_cycle_ratio({'2021-2022': 12463873.75, '2023-2024': 17978153.89}, 'Telecom')
        self.assertEqual(round(got['ratio'], 2), 1.44)
        self.assertEqual((got['dataset'], got['presidential_cycle'], got['presidential_total'], got['midterm_cycle'], got['midterm_total']),
                         ('Telecom', '2023-2024', 17978153.89, '2021-2022', 12463873.75))
        self.assertEqual(round(gf._own_cycle_ratio(self.TOTALS, 'Telecom')['ratio'], 2), 1.44, 'the open 2025-2026 cycle is not a presidential cycle and is ignored')

    def test_the_latest_presidential_cycle_is_used_when_there_are_older_pairs(self):
        got = gf._own_cycle_ratio({'2017-2018': 10.0, '2019-2020': 30.0, '2021-2022': 20.0, '2023-2024': 50.0}, 'X')
        self.assertEqual((got['presidential_cycle'], got['midterm_cycle'], got['ratio']), ('2023-2024', '2021-2022', 2.5))

    def test_it_needs_both_totals_and_a_positive_midterm(self):
        for totals in ({}, {'2023-2024': 5.0}, {'2021-2022': 5.0}, {'2021-2022': 5.0, '2025-2026': 9.0}, {'2021-2022': 0.0, '2023-2024': 5.0}, {'2019-2020': 5.0, '2023-2024': 7.0}):
            self.assertIsNone(gf._own_cycle_ratio(totals, 'X'), totals)


@unittest.skipUnless(openpyxl, 'openpyxl is not installed')
class WorkbookReaderTests(unittest.TestCase):
    ROWS = [('2021-2022', 'Total Giving', 12463873.75), ('2021-2022', 'Corporate', 1827650.41), ('2023-2024', 'Total Giving', 17978153.89), ('2023-2024', 'Corporate', 4688748.28),
            ('2025-2026', 'Total Giving', 7256093.45), ('Notes', 'Total Giving', 1.0)]

    def write(self, directory, name='Telecom Industry Spending Overview.xlsx', rows=None, sheet='Spending Overview'):
        book = openpyxl.Workbook()
        book.active.title = 'Master Overview'
        book.active['A1'] = 'Total Giving'
        overview = book.create_sheet(sheet)
        overview['A1'] = 'Telecom Industry Spending Overview'
        overview.append([])
        overview.append([])
        overview.append([])
        overview.append(['Cycle', 'Scope', 'Total Giving', 'Democratic'])
        for row in (self.ROWS if rows is None else rows):
            overview.append(list(row))
        path = Path(directory) / name
        book.save(path)
        return path

    def totals(self, path):
        stat = path.stat()
        gf._workbook_cycle_totals.cache_clear()
        return gf._workbook_cycle_totals(str(path), stat.st_mtime_ns, stat.st_size)

    def test_it_reads_the_total_giving_row_of_each_cycle_and_nothing_else(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.totals(self.write(tmp)), {'2021-2022': 12463873.75, '2023-2024': 17978153.89, '2025-2026': 7256093.45})

    def test_the_catalog_carries_the_ratio_of_each_industry_from_its_published_workbook(self):
        bundle, anchors = telecom_world_files()
        gf._workbook_cycle_totals.cache_clear()
        with tempfile.TemporaryDirectory() as tmp:
            self.write(tmp)                                               # World's folder is the report directory: only Telecom's workbook is in it
            with World(tmp, bundle=bundle, anchors=anchors):
                industries = gf.catalog('2026-09-30')['anchors']['industries']
        ratio = industries[TELECOM]['own_cycle_ratio']
        self.assertEqual((ratio['dataset'], ratio['presidential_cycle'], ratio['presidential_total'], ratio['midterm_cycle'], ratio['midterm_total']),
                         ('Telecom', '2023-2024', 17978153.89, '2021-2022', 12463873.75))
        self.assertEqual(round(ratio['ratio'], 2), 1.44)
        self.assertNotIn('own_cycle_ratio', industries['commercial_banks'], 'a workbook that is not there leaves the note out')

    @staticmethod
    def damage(path, pattern, replacement):
        """Rewrite every worksheet part of the workbook with `pattern` replaced, as an odd producer would write it."""
        with zipfile.ZipFile(path) as book:
            parts = [(info.filename, book.read(info.filename)) for info in book.infolist()]
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as book:
            for name, data in parts:
                if name.startswith('xl/worksheets/sheet'):
                    data = re.sub(pattern, replacement, data.decode('utf-8')).encode('utf-8')
                book.writestr(name, data)

    ODD_ROWS = {'a cell with no r': (r'<c r="[A-Z]+\d+"', '<c'), 'a row with no r': (r'<row r="\d+"', '<row'),
                'a cell with an empty r': (r'<c r="[A-Z]+\d+"', '<c r=""')}

    def test_an_odd_sheet_row_leaves_the_ratio_out_instead_of_failing_the_catalog(self):
        # P2-3: _anchors_status catches only AnchorProblem, so a TypeError/AttributeError/IndexError here broke the whole catalog.
        entry = {'mentat': {'workbook': 'Telecom Industry Spending Overview.xlsx', 'dataset': 'Telecom'}}
        bundle, anchors = telecom_world_files()
        for label, (pattern, replacement) in self.ODD_ROWS.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as reports:
                path = self.write(reports)
                self.damage(path, pattern, replacement)
                gf._workbook_cycle_totals.cache_clear()
                with patch.dict(os.environ, {'MENTAT_REPORT_DIRECTORY': reports}):
                    self.assertIsNone(gf._industry_cycle_ratio(entry))
                with World(reports, bundle=bundle, anchors=anchors):
                    industries = gf.catalog('2026-09-30')['anchors']['industries']
                self.assertEqual(industries[TELECOM]['status'], 'ready')
                self.assertNotIn('own_cycle_ratio', industries[TELECOM])

    def test_a_workbook_without_the_sheet_or_with_a_missing_pair_gives_no_ratio(self):
        with tempfile.TemporaryDirectory() as reports:
            entry = {'mentat': {'workbook': 'Telecom Industry Spending Overview.xlsx', 'dataset': 'Telecom'}}
            def ratio():
                gf._workbook_cycle_totals.cache_clear()
                return gf._industry_cycle_ratio(entry)
            with patch.dict(os.environ, {'MENTAT_REPORT_DIRECTORY': reports}):
                self.assertIsNone(ratio(), 'no file')
                self.write(reports, sheet='Overview')
                self.assertIsNone(ratio(), 'no Spending Overview sheet')
                self.write(reports, rows=[('2023-2024', 'Total Giving', 5.0)])
                self.assertIsNone(ratio(), 'no midterm cycle')
                (Path(reports) / entry['mentat']['workbook']).write_bytes(b'not a workbook')
                self.assertIsNone(ratio(), 'not a workbook')


if __name__ == '__main__':
    unittest.main()
