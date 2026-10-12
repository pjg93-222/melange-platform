"""source_memo: a validated, single-flight memo of contribution_data._sources() (perf H6).

Synthetic snapshots live in a temporary report folder and project root; nothing here
writes outside them. The published-folder test only reads.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import contribution_data as data
import source_memo as memo

ORIGINAL = memo._original
_REALPATH, _STAT, _SCANDIR = os.path.realpath, os.stat, os.scandir
_RECORDING = []


def _audit(event, args):
    if _RECORDING and event == 'open' and args and isinstance(args[0], (str, bytes, os.PathLike)):
        _RECORDING[-1].add(os.fsdecode(args[0]))


sys.addaudithook(_audit)


def normal(path):
    return os.path.normcase(_REALPATH(os.fsdecode(path)))


@contextmanager
def recording():
    """Every path stat()ed, opened, resolved or listed by the calling thread's code."""
    touched, inside = set(), []

    def stat(path, *args, **kwargs):
        if not inside:
            touched.add(os.fsdecode(path))
        return _STAT(path, *args, **kwargs)

    def realpath(path, *args, **kwargs):
        touched.add(os.fsdecode(path))
        inside.append(1)  # realpath's own lstat/stat of path prefixes are not inputs
        try:
            return _REALPATH(path, *args, **kwargs)
        finally:
            inside.pop()

    def scandir(path='.'):
        touched.add(os.fsdecode(path))
        return _SCANDIR(path)

    _RECORDING.append(touched)
    try:
        with patch('os.stat', stat), patch('os.path.realpath', realpath), patch('os.scandir', scandir):
            yield touched
    finally:
        _RECORDING.pop()


def watched(spec):
    return {normal(argument) for kind, argument in spec if kind in ('stat', 'real', 'glob')}


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value), encoding='utf-8')


def changed(path, write):
    """Write, and make sure the file's stat moved (a same-size rewrite inside one clock
    tick keeps the old mtime; real writers do not rewrite that fast)."""
    before = _STAT(path) if os.path.exists(path) else None
    write()
    after = _STAT(path)
    if before and (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns):
        os.utime(path, ns=(after.st_atime_ns, after.st_mtime_ns + 1_000_000))


def book(path, digest):
    import openpyxl
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = 'About'
    sheet.append(['Key', 'Value'])
    sheet.append(['Lineage', f'Source DB SHA-256: {digest}'])
    workbook.save(path)
    workbook.close()
    return path


class Snapshots:
    """A report folder plus a project root holding each industry's pinned evidence."""

    def __init__(self, root):
        self.root = Path(root)
        self.reports = self.root / 'Published Reports'
        self.project = self.root / 'project'
        self.reports.mkdir()
        (self.project / 'tools').mkdir(parents=True)
        (self.project / 'tools' / 'cross_reference_engine.py').write_text('# fixture\n')

    def workbook(self, name):
        return self.reports / f'{name} Industry Spending Overview.xlsx'

    def cache(self, name):
        return self.project / '.cache' / 'industry-report' / data._slug(name)

    def receipt(self, name):
        return self.project / '.cache/receipt-metadata-repair-20260910' / data._slug(name) / 'mentat.db.hydration.json'

    def add(self, name, digest, hydrated=False):
        book(self.workbook(name), digest)
        slug = data._slug(name)
        write_json(self.cache(name) / f'manifest-{digest[:16]}.json', {'database_sha256': digest, 'data_sha256': 'd' * 64})
        database = self.root / 'databases' / f'{slug}.db'
        database.parent.mkdir(exist_ok=True)
        if hydrated:
            recovered = self.project / '.cache' / 'recovered' / f'{slug}.db'
            recovered.parent.mkdir(parents=True, exist_ok=True)
            recovered.write_bytes(b'recovered ' + slug.encode())
            write_json(self.receipt(name), {'source_sha256': digest, 'output': str(recovered)})
        else:
            database.write_bytes(b'database ' + slug.encode())
        write_json(self.cache(name) / f'industry_data-{digest[:16]}.json',
                   {'metadata': {'database_path': str(database), 'expected_cycle_totals': {'2023-2024': 12.5},
                                 'destination_schema': 1}, 'raw_rows': []})


A, B, C = 'a' * 64, 'b' * 64, 'c' * 64


class SourceMemoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.snapshots = Snapshots(self.tmp.name)
        self.snapshots.add('Alpha', A)
        self.snapshots.add('Beta', B, hydrated=True)
        project = patch.object(data, 'PROJECT', self.snapshots.project)
        project.start()
        self.addCleanup(project.stop)
        data._workbook_source.cache_clear()
        self.addCleanup(data._workbook_source.cache_clear)
        memo._entries.clear()
        self.addCleanup(memo._entries.clear)
        self.reports = self.snapshots.reports

    def entry(self):
        return memo._entries.get(memo._key(self.reports))

    def test_memo_equals_unmemoized_and_every_call_gets_its_own_copy(self):
        expected = ORIGINAL(self.reports)
        self.assertEqual([s['available'] for s in expected], [True, True])
        self.assertTrue(expected[1]['hydration_manifest'])
        first = memo.sources(self.reports)
        self.assertEqual(first, expected)
        self.assertIsNotNone(self.entry(), 'a stable discovery must be kept')
        first[0]['ledger'] = 'mutated by a caller'
        first[0]['expected_cycle_totals']['2023-2024'] = -1
        second = memo.sources(self.reports)
        self.assertEqual(second, expected)
        self.assertNotIn('ledger', second[0])

    def mutations(self):
        s = self.snapshots
        alpha_manifest = s.cache('Alpha') / f'manifest-{A[:16]}.json'
        alpha_payload = s.cache('Alpha') / f'industry_data-{A[:16]}.json'
        beta_payload = s.cache('Beta') / f'industry_data-{B[:16]}.json'
        recovered = s.project / '.cache' / 'recovered' / 'beta.db'

        def payload(path, **metadata):
            value = json.loads(Path(path).read_text(encoding='utf-8'))
            value['metadata'].update(metadata)
            write_json(path, value)

        def replace_file(path, content):
            pending = Path(str(path) + '.new')
            pending.write_bytes(content)
            os.replace(pending, path)

        return {
            'new overview workbook': lambda: s.add('Gamma', C),
            'workbook removed': lambda: s.workbook('Alpha').unlink(),
            'workbook republished on another pinned source': lambda: changed(
                s.workbook('Alpha'), lambda: s.add('Alpha', C)),
            'companion Data workbook appears': lambda: book(s.reports / 'Alpha Industry Spending Data.xlsx', B),
            'manifest edited': lambda: changed(alpha_manifest, lambda: write_json(
                alpha_manifest, {'database_sha256': A, 'data_sha256': 'e' * 64})),
            'manifest deleted': lambda: alpha_manifest.unlink(),
            'payload metadata edited': lambda: changed(alpha_payload, lambda: payload(
                alpha_payload, expected_cycle_totals={'2023-2024': 99.5})),
            'database deleted (falls back to a missing recovery)': lambda: (s.root / 'databases' / 'alpha.db').unlink(),
            'recovered database rewritten': lambda: changed(recovered, lambda: recovered.write_bytes(b'recovered BETA')),
            'recovered database replaced by rename': lambda: replace_file(recovered, b'recovered beta 2'),
            'hydration receipt points elsewhere': lambda: (
                (s.project / '.cache' / 'recovered' / 'other.db').write_bytes(b'other'),
                changed(s.receipt('Beta'), lambda: write_json(
                    s.receipt('Beta'), {'source_sha256': B, 'output': str(s.project / '.cache' / 'recovered' / 'other.db')}))),
            'original database reappears': lambda: (s.root / 'databases' / 'beta.db').write_bytes(b'original beta'),
            'payload names another database': lambda: (
                (s.root / 'databases' / 'moved.db').write_bytes(b'moved'),
                changed(beta_payload, lambda: payload(beta_payload, database_path=str(s.root / 'databases' / 'moved.db')))),
        }

    def test_every_input_change_invalidates_and_frozen_stats_would_serve_stale(self):
        names = list(self.mutations())
        for name in names:
            with self.subTest(change=name):
                with tempfile.TemporaryDirectory() as other:
                    self.snapshots = Snapshots(other)
                    self.snapshots.add('Alpha', A)
                    self.snapshots.add('Beta', B, hydrated=True)
                    self.reports = self.snapshots.reports
                    with patch.object(data, 'PROJECT', self.snapshots.project):
                        before = memo.sources(self.reports)
                        self.assertEqual(before, ORIGINAL(self.reports))
                        entry = self.entry()
                        self.assertIsNotNone(entry)
                        self.mutations()[name]()
                        expected = ORIGINAL(self.reports)
                        # The change is real: the unmemoized discovery sees it.
                        self.assertNotEqual(expected, before)
                        # Negative control: had validation seen no input move, the memo
                        # would serve the stale snapshot.
                        with patch.object(memo, '_evaluate', return_value=entry[1]):
                            self.assertEqual(memo.sources(self.reports), before)
                        self.assertEqual(memo.sources(self.reports), expected)
                        self.assertEqual(memo.sources(self.reports), expected)

    def test_excel_lock_file_keeps_the_answer(self):
        before = memo.sources(self.reports)
        (self.reports / '~$Alpha Industry Spending Overview.xlsx').write_bytes(b'lock')
        self.assertEqual(ORIGINAL(self.reports), before)
        self.assertEqual(memo.sources(self.reports), before)

    def test_transient_read_failure_is_never_kept(self):
        real = data._hash_stamp
        locked = str(self.snapshots.workbook('Alpha').resolve())

        def flaky(stamp):
            if stamp[0] == locked:
                raise PermissionError(13, 'The process cannot access the file')
            return real(stamp)

        data._hash_stamp.cache_clear()
        with patch.object(data, '_hash_stamp', flaky):
            during = memo.sources(self.reports)
        self.assertEqual(during[0]['message'], 'Report workbook is unreadable.')
        self.assertIsNone(self.entry())
        self.assertEqual(memo.sources(self.reports), ORIGINAL(self.reports))
        self.assertTrue(memo.sources(self.reports)[0]['available'])
        # Negative control: keeping that discovery would have pinned the failure.
        memo._entries.clear()
        real_discover = memo._discover
        with patch.object(memo, '_discover', lambda d: (*real_discover(d)[:2], True)):
            with patch.object(data, '_hash_stamp', flaky):
                memo.sources(self.reports)
            self.assertEqual(memo.sources(self.reports)[0]['message'], 'Report workbook is unreadable.')

    def test_single_flight_under_an_explicit_lock(self):
        calls = []

        def slow(directory):
            calls.append(directory)
            time.sleep(.3)
            return ORIGINAL(directory)

        def race(lock):
            calls.clear()
            memo._entries.clear()
            with patch.object(memo, '_original', slow), patch.object(memo, '_lock', lock):
                with ThreadPoolExecutor(8) as pool:
                    results = list(pool.map(lambda _: memo.sources(self.reports), range(8)))
            self.assertTrue(all(result == results[0] for result in results))
            return len(calls)

        self.assertEqual(race(memo._lock), 1)
        # Negative control: an lru-style memo with no lock runs every concurrent caller.
        self.assertGreater(race(nullcontext()), 1)

    def test_install_is_idempotent_and_serves_contribution_data_internals(self):
        memo.uninstall()
        self.addCleanup(memo.uninstall)
        self.assertIs(data._sources, ORIGINAL)
        memo.install()
        memo.install()
        self.assertIs(data._sources, memo.sources)
        calls = []

        def counted(directory):
            calls.append(directory)
            return ORIGINAL(directory)

        with patch.object(memo, '_original', counted):
            first = data.catalog(self.reports)
            second = data.catalog(self.reports)
            found = data._source(self.reports, {'dataset': 'Beta'})
        self.assertEqual(first, second)
        self.assertEqual(found['id'], 'Beta')
        self.assertEqual(len(calls), 1)
        memo.uninstall()
        self.assertIs(data._sources, ORIGINAL)

    def test_the_ledger_is_resolved_on_every_copy(self):
        with patch.object(data, '_compatible_ledger', return_value=(None, None)) as ledger:
            first = memo.sources(self.reports)[0]
            token = data._token(first)
            self.assertIn('ledger', first)
            second = memo.sources(self.reports)[0]
            self.assertNotIn('ledger', second)
            self.assertEqual(data._token(second), token)
            self.assertEqual(ledger.call_count, 2)
            # Negative control: a memo handing back the same dicts would skip the
            # second resolution, so a new ledger report would go unseen.
            data._token(first)
            self.assertEqual(ledger.call_count, 2)

    def test_every_path_sources_reads_is_watched(self):
        book(self.snapshots.reports / 'Alpha Industry Spending Data.xlsx', A)
        memo.sources(self.reports)
        spec = self.entry()[0]
        data._workbook_source.cache_clear()
        data._hash_stamp.cache_clear()
        with recording() as touched:
            ORIGINAL(self.reports)
        self.assertTrue(touched)
        self.assertEqual({normal(p) for p in touched} - watched(spec), set())


class PublishedSourcesTests(unittest.TestCase):
    """Read-only: the real report folder and pinned evidence."""

    def setUp(self):
        try:
            from report_directory import report_directory
            self.directory = Path(report_directory(data.PROJECT))
        except Exception as error:  # noqa: BLE001
            self.skipTest('no published report directory: ' + str(error))
        if not self.directory.is_dir():
            self.skipTest('no published report directory')
        memo._entries.clear()
        self.addCleanup(memo._entries.clear)

    def test_memo_tokens_and_watched_inputs_match_the_published_folder(self):
        first = memo.sources(self.directory)
        expected = ORIGINAL(self.directory)
        self.assertEqual(first, expected)
        entry = memo._entries.get(memo._key(self.directory))
        self.assertIsNotNone(entry, 'the published folder must be memoizable')
        served = memo.sources(self.directory)
        self.assertEqual(served, expected)
        self.assertEqual(len(expected), len({s['id'] for s in expected}))
        for plain, copied in zip(copy.deepcopy(expected), served):
            with self.subTest(industry=plain['id']):
                if plain.get('available'):
                    self.assertEqual(data._token(copied), data._token(plain))
        with recording() as touched:
            ORIGINAL(self.directory)
        self.assertEqual({normal(p) for p in touched} - watched(entry[0]), set())


if __name__ == '__main__':
    unittest.main()
