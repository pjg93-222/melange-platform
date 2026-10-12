"""FIX 4 in the Mentat Pocket snapshot: a Telecom-like fixture carries its collection gaps.

The extractor reads the console's own catalogue, whose datasets now carry `collection_gaps`
(collection_gaps.py). The snapshot keeps them (`gp`) and the page names the cells the chosen
company x cycle touches, in coverage-view.js's words. Nothing here talks to a console or
publishes a snapshot: the console is a fake that groups a small fixed table, the page is built
into a temporary folder, and the pocket's own self-check, render harness and negative control
are run against that folder.
"""
from collections import defaultdict
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent / 'app'))
sys.path.insert(0, str(TESTS.parent / 'tools' / 'pocket'))
sys.path.insert(0, str(TESTS))
import collection_gaps                                                # noqa: E402
import extract_pocket_data as extractor                               # noqa: E402
from test_collection_gaps import REAL_DETAIL                          # noqa: E402

POCKET = TESTS.parent / 'tools' / 'pocket'
CYCLES = ['2021-2022', '2023-2024', '2025-2026']
COMPANIES = ['Comcast', 'Verizon', 'Lumen Technologies', 'Altice USA', 'AT&T']


LONG_NAME = 'National Cable and Telecommunications Association Political Action Committee'


def fixture_rows():
    """company, cycle, category, party, recipient, cents, records -- every dimension sums to one whole.

    Sized like a real industry so the harness's older negative-control mutants (money in millions, a long
    recipient name, an average in the hundreds of thousands, a scope with no receipts in a cycle) still bite."""
    rows = []
    for c, company in enumerate(COMPANIES):
        for y, cycle in enumerate(CYCLES):
            if company == 'Altice USA' and y == 0:
                continue
            base = 100_000_000 * (c + 1) * (y + 2)
            rows.append((company, cycle, 'Candidate', 'DEM', 'Friends of Ada', base, 4 + c))
            rows.append((company, cycle, 'Super PAC', 'REP', LONG_NAME, base // 2 + 1, 2 + y))
    return rows


FIELDS = ('company', 'cycle', 'recipient_category', 'party', 'recipient', 'amount', 'records')


class FakeConsole:
    """What extract_pocket_data.Console answers, grouped from FIXTURE; no sockets."""

    def __init__(self, *_):
        self.requests = 0
        self.polls = 0
        gaps = {'failed': True, **collection_gaps.parse_detail(REAL_DETAIL.replace('and 19 more', 'and 21 more')),
                'source': 'pinned payload export_guards'}
        self.datasets = [{'id': 'Telecom', 'available': True, 'collection_gaps': gaps}]

    def open(self):
        return {'read_only': True}

    def catalog(self):
        ids = ('cycle', 'recipient_category', 'party', 'recipient', 'company', 'amount', 'records')
        return {'amount_unit': 'cents', 'datasets': self.datasets,
                'fields': [{'id': i, 'label': i, 'unit': 'cents' if i == 'amount' else None,
                            'role': 'measure' if i in ('amount', 'records') else 'dimension'} for i in ids]}

    def _group(self, dimensions, filters):
        table = defaultdict(lambda: {'amount': 0, 'records': 0})
        for row in fixture_rows():
            record = dict(zip(FIELDS, row))
            if any(record[f['field']] != f['value'] for f in filters or [] if f['op'] == 'eq'):
                continue
            key = tuple(record[d] for d in dimensions)
            table[key]['amount'] += record['amount']
            table[key]['records'] += record['records']
        return [{**dict(zip(dimensions, key)), **value} for key, value in table.items()]

    def pages(self, dataset, dimensions, measures, filters=None, sort=None):
        rows = self._group(dimensions, filters)
        totals = {'amount': sum(r['amount'] for r in rows), 'records': sum(r['records'] for r in rows)}
        return rows, totals, len(rows), True

    def query(self, dataset, dimensions, measures, filters=None, sort=None, limit=100000, offset=0):
        rows = sorted(self._group(dimensions, filters), key=lambda r: -r['amount'])[:limit]
        return {'status': 'ready', 'rows': rows}


def run_extractor(directory, console=FakeConsole):
    with patch.object(extractor, 'Console', console), \
            patch.object(sys, 'argv', ['extract_pocket_data.py', '--out-dir', str(directory), '--datasets', 'Telecom']):
        extractor.main()
    return json.loads((directory / 'pocket-data.json').read_text(encoding='utf-8'))


def build(directory):
    for name in ('icon-180.png', 'manifest.webmanifest'):
        shutil.copy(POCKET / name, directory / name)
    subprocess.run([sys.executable, str(POCKET / 'build_page.py'), '--data', str(directory / 'pocket-data.json'),
                    '--out', str(directory / 'mentat-pocket.html')], check=True, capture_output=True)


def run(command, **environment):
    return subprocess.run(command, capture_output=True, text=True, env=dict(os.environ, **environment), cwd=str(POCKET))


class PocketGapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.folder = Path(cls.temp.name)
        with patch('builtins.print'):
            cls.data = run_extractor(cls.folder)
        build(cls.folder)
        cls.page = cls.folder / 'mentat-pocket.html'

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_snapshot_carries_the_datasets_collection_gaps(self):
        industry = self.data['ind'][0]
        gaps = industry['gp']
        self.assertEqual((gaps['f'], gaps['u'], gaps['s']), (1, 21, 'pinned payload export_guards'))
        cells = {(c[0], c[1], c[2]): c for c in gaps['c']}
        comcast = cells['Comcast', '2025-2026', 'contributions']
        self.assertEqual(comcast, ['Comcast', '2025-2026', 'contributions', 11753, 15269, 77])
        self.assertEqual(cells['Verizon', '2025-2026', 'contributions'][5], 93)
        self.assertEqual(len(gaps['c']), 12)
        self.assertEqual(industry['cyc_raw'], CYCLES)

    def test_a_report_with_a_passing_check_adds_no_gap_block(self):
        class Clean(FakeConsole):
            def __init__(self, *args):
                super().__init__(*args)
                self.datasets[0]['collection_gaps'] = {'failed': False, 'cells': [], 'unitemized': 0, 'source': 'Data Quality sheet'}

        class Unknown(FakeConsole):
            def __init__(self, *args):
                super().__init__(*args)
                del self.datasets[0]['collection_gaps']

        for console in (Clean, Unknown):
            with self.subTest(console=console.__name__), tempfile.TemporaryDirectory() as folder, patch('builtins.print'):
                self.assertNotIn('gp', run_extractor(Path(folder), console)['ind'][0])

    def test_a_cycle_report_whose_check_names_only_other_cycles_adds_no_gap_block(self):
        # P2-2: the console scopes a cycle report's gaps to its own cycle; a check that itemized other cycles
        # only has nothing to say here, so the snapshot carries no block (and the page no "not itemized" line).
        class OtherCycles(FakeConsole):
            def __init__(self, *args):
                super().__init__(*args)
                cells = [c for c in self.datasets[0]['collection_gaps']['cells'] if c['cycle'] == '2023-2024']
                self.datasets[0]['collection_gaps'] = collection_gaps.scoped(
                    {'failed': True, 'cells': cells, 'unitemized': 0, 'source': 'Data Quality sheet'}, [2026])

        with tempfile.TemporaryDirectory() as folder, patch('builtins.print'):
            self.assertNotIn('gp', run_extractor(Path(folder), OtherCycles)['ind'][0])

    def test_the_built_page_inlines_the_gaps_and_has_somewhere_to_show_them(self):
        text = self.page.read_text(encoding='utf-8')
        literal = text.split('/*__DATA_START__*/', 1)[1].split('/*__DATA_END__*/', 1)[0]
        self.assertEqual(json.loads(literal.replace('<\\/', '</'))['ind'][0]['gp'], self.data['ind'][0]['gp'])
        for needle in ('id="tile-gap"', 'id="gap-cycles"', 'gapchip'):
            self.assertIn(needle, text)

    def test_the_pockets_own_selfcheck_passes_and_recomputes_every_percent(self):
        result = run([sys.executable, str(POCKET / 'selfcheck.py'), '--dir', str(self.folder)])
        self.assertEqual(result.returncode, 0, result.stdout[-1500:])
        self.assertIn('ALL CHECKS PASSED', result.stdout)
        self.assertRegex(result.stdout, r'PASS  Telecom collection gaps: 12 cells, each percent recomputed from its counts')

    def test_the_selfcheck_fails_a_percent_that_is_not_its_counts(self):
        tampered = json.loads((self.folder / 'pocket-data.json').read_text(encoding='utf-8'))
        tampered['ind'][0]['gp']['c'][6][5] = 99
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            for name in ('icon-180.png', 'manifest.webmanifest', 'mentat-pocket.html'):
                shutil.copy(self.folder / name, folder / name)
            (folder / 'pocket-data.json').write_text(json.dumps(tampered), encoding='utf-8')
            result = run([sys.executable, str(POCKET / 'selfcheck.py'), '--dir', str(folder),
                          '--data', str(folder / 'pocket-data.json')])
        self.assertNotEqual(result.returncode, 0)
        self.assertRegex(result.stdout, r'FAIL  Telecom collection gaps')

    def test_the_render_harness_shows_comcast_2025_26_at_77_percent_of_what_fec_reported(self):
        result = run(['node', str(POCKET / 'render_harness.js')], POCKET_PAGE=str(self.page),
                     POCKET_DATA=str(self.folder / 'pocket-data.json'))
        self.assertEqual(result.returncode, 0, result.stdout[-1500:] + result.stderr[-500:])
        self.assertIn('RENDER HARNESS PASSED', result.stdout)
        self.assertRegex(result.stdout, r'gap scope  : Telecom / Comcast / 2025-26 -> .*Comcast 2025-26: 77% of FEC-reported records '
                                        r'\(11,753 of 15,269 contributions\); 21 more short cells are not itemized in the workbook\'s Data Quality check')
        self.assertRegex(result.stdout, r'gap scope  : Telecom / Verizon / 2025-26 -> .*Verizon 2025-26: 93% of FEC-reported records')
        self.assertRegex(result.stdout, r'coverage gaps checked\s*: [1-9]\d* scopes')

    def test_the_negative_control_catches_a_gap_line_that_ignores_the_scope_or_drops_the_remainder(self):
        # The mutants are textual: each anchor has to be in the shipped page for the control to mean anything.
        source = self.page.read_text(encoding='utf-8')
        self.assertIn('return (!company || cell[0] === company) && (!years.length ||', source)
        result = run([sys.executable, str(POCKET / 'negative_control.py')], POCKET_PAGE=str(self.page),
                     POCKET_DATA=str(self.folder / 'pocket-data.json'))
        self.assertEqual(result.returncode, 0, result.stdout[-2500:])
        self.assertIn('NEGATIVE CONTROL PASSED', result.stdout)
        for label in ('the gap line names every company, whatever company is chosen',
                      'the gap line names every cycle, whatever cycle is chosen',
                      'the gap line drops the count of cells the check did not itemize'):
            self.assertRegex(result.stdout, r'CAUGHT  ' + re.escape(label))


if __name__ == '__main__':
    unittest.main()
