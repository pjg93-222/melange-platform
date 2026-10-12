"""The transfer index the Python writer builds is the one the R reader trusts (DE-034).

R/fec_transfer_graph.R refuses any index whose version or ambiguous-version policy differs
from what it requires, and a refused index publishes nothing transfer-derived for its cycle.
2829d2828 moved the reader to VERSION 5 count_all and never rebuilt the index, so every build
from 09-24 read a VERSION 4 index as stale. Two checks close that gap:
  * the writer's VERSION and AMBIGUOUS_VERSION_POLICY equal what the reader's guard requires,
    proved by parsing the guard and by running the real reader on a fresh index;
  * the index on disk is current for every cycle the export reads. It skips, visibly, in a
    checkout with no active FEC source. MENTAT_TRANSFER_INDEX_DIRECTORY points it at another
    fec_beneficiary folder (a staged rebuild); MENTAT_TRANSFER_INDEX_HASH_RAW=1 also hashes each
    itoth.txt, the reader's own test, instead of trusting source-receipt.json.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import types
import unittest

MENTAT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MENTAT / 'tools'))
import fec_transfer_graph  # noqa: E402

READER = MENTAT / 'R' / 'fec_transfer_graph.R'
EXPORTER = MENTAT / 'tools' / 'export_crypto_spending_data.R'
RSCRIPT = Path('C:/Program Files/R/R-4.6.0/bin/Rscript.exe')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 22), b''):
            digest.update(block)
    return digest.hexdigest()


def reader_requirement(text):
    """(version, policy) that fec_transfer_context() accepts, parsed from its guard."""
    version = re.search(r'index\$version\s*!=\s*(\d+)L', text)
    policy = re.search(r'identical\(index\$ambiguous_version_policy,\s*"([a-z_]+)"\)', text)
    return (int(version[1]) if version else None, policy[1] if policy else None)


def contract_mismatch(reader_text, writer):
    required = reader_requirement(reader_text)
    if None in required:
        return 'cannot read the version guard in R/fec_transfer_graph.R'
    produced = (writer.VERSION, writer.AMBIGUOUS_VERSION_POLICY)
    if produced != required:
        return (f'tools/fec_transfer_graph.py writes VERSION {produced[0]} {produced[1]}; '
                f'R/fec_transfer_graph.R requires VERSION {required[0]} {required[1]}')
    return None


def build_cycles(text):
    """The cycles export_crypto_spending_data.R reads (its supported_cycles)."""
    match = re.search(r'supported_cycles\s*<-\s*c\(([^)]*)\)', text)
    return [int(value) for value in re.findall(r'(\d{4})L', match[1])] if match else []


def stale_reasons(folder, required, cycle, hash_raw=False):
    """Why the reader would refuse this cycle's index; [] when it would trust it."""
    info_path = folder / 'transfer-info.json'
    if not info_path.exists():
        return ['missing: no transfer-info.json']
    info = json.loads(info_path.read_text(encoding='utf-8'))
    reasons = []
    if (info.get('version'), info.get('ambiguous_version_policy')) != required:
        reasons.append(f"stale: VERSION {info.get('version')} {info.get('ambiguous_version_policy')}, "
                       f'the reader requires VERSION {required[0]} {required[1]}')
    if info.get('cycle') != cycle:
        reasons.append(f"stale: built for cycle {info.get('cycle')}")
    edges = folder / 'transfer-edges.csv'
    if not edges.exists() or sha(edges) != info.get('edges_sha256'):
        reasons.append('stale: transfer-edges.csv does not match edges_sha256')
    raw, receipt = folder / 'itoth.txt', folder / 'source-receipt.json'
    if receipt.exists():
        recorded = json.loads(receipt.read_text(encoding='utf-8'))
        if recorded.get('sha256') != info.get('source_sha256'):
            reasons.append('stale: built from another OTH source than source-receipt.json names')
        if raw.exists() and raw.stat().st_size != recorded.get('bytes'):
            reasons.append('stale: itoth.txt is not the size source-receipt.json records')
    if hash_raw and raw.exists() and sha(raw) != info.get('source_sha256'):
        reasons.append('stale: itoth.txt does not hash to source_sha256')
    return reasons


def rebuild_command(directory, cycle):
    return (f'python "{MENTAT / "tools" / "fec_transfer_graph.py"}" --directory "{directory}" --cycles {cycle} '
            f'--filing-header-directory "{MENTAT / "state" / "cross-reference" / "filing-headers"}"')


def transfer_row(cycle):
    fields = [''] * 21
    for index, value in {0: 'C00000001', 1: 'N', 2: 'Q1', 5: '24K', 13: f'0102{cycle}', 14: '100',
                         15: 'C00000002', 16: 'T1', 17: '1', 20: '1'}.items():
        fields[index] = value
    return '|'.join(fields)


class WriterMatchesReader(unittest.TestCase):
    def test_the_writer_builds_the_version_and_policy_the_reader_requires(self):
        self.assertIsNone(contract_mismatch(READER.read_text(encoding='utf-8'), fec_transfer_graph))

    def test_negative_control_a_reader_or_writer_moved_alone_is_caught(self):
        text = READER.read_text(encoding='utf-8')
        version, policy = reader_requirement(text)
        bumped = text.replace(f'index$version != {version}L', f'index$version != {version + 1}L')
        other = text.replace(f'"{policy}"', '"count_latest"' if policy != 'count_latest' else '"withhold"')
        for reader_text in (bumped, other):
            self.assertIsNotNone(contract_mismatch(reader_text, fec_transfer_graph))
        old_writer = types.SimpleNamespace(VERSION=version - 1, AMBIGUOUS_VERSION_POLICY=policy)
        self.assertIn('requires VERSION', contract_mismatch(text, old_writer))
        self.assertEqual(contract_mismatch('no guard here', fec_transfer_graph),
                         'cannot read the version guard in R/fec_transfer_graph.R')

    @unittest.skipUnless(RSCRIPT.is_file(), 'The end-to-end check needs R 4.6 at C:/Program Files/R/R-4.6.0')
    def test_the_real_reader_trusts_a_fresh_index_and_refuses_an_older_one(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / 'oth24'
            folder.mkdir()
            (folder / 'itoth.txt').write_text(transfer_row(2024) + '\n', encoding='utf-8')
            fec_transfer_graph.publish_index(folder, fec_transfer_graph.transfer_index(folder / 'itoth.txt', 2024))
            script = root / 'probe.R'
            script.write_text('\n'.join([
                'suppressPackageStartupMessages({library(data.table); library(jsonlite)})',
                f'source("{READER.as_posix()}")',
                'roots <- data.table(cycle = 2024L, committee_id = "C00000001", committee_name = "",',
                '  recipient_category = "Super PAC", preliminary_crypto_funding = 1, traversal_seed = TRUE)',
                'links <- data.table(cycle = integer(), committee_id = character(), designation = character())',
                f'result <- fec_transfer_context("{root.as_posix()}", roots, links, 2024L)',
                'cat(result$coverage[[1]]$status, nrow(result$edges), "\\n")']), encoding='utf-8')

            def probe():
                done = subprocess.run([str(RSCRIPT), str(script)], capture_output=True, text=True, timeout=120)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                return done.stdout.split()

            self.assertEqual(probe(), ['indexed', '1'])
            info = json.loads((folder / 'transfer-info.json').read_text(encoding='utf-8'))
            info['version'] = fec_transfer_graph.VERSION - 1  # negative control: an older index
            (folder / 'transfer-info.json').write_text(json.dumps(info), encoding='utf-8')
            self.assertEqual(probe(), ['stale_transfer_index', '0'])


class IndexOnDiskIsCurrent(unittest.TestCase):
    def test_every_cycle_the_build_reads_has_a_current_index(self):
        override = os.environ.get('MENTAT_TRANSFER_INDEX_DIRECTORY')
        if override:
            base = Path(override)
        else:
            active = MENTAT / 'inputs' / 'active_fec_sources.json'
            if not active.is_file():
                self.skipTest('SKIPPED: no active FEC source in this checkout (mentat/inputs/active_fec_sources.json '
                              'is absent), so there is no index on disk to be stale')
            base = Path(json.loads(active.read_text(encoding='utf-8'))['path']) / 'fec_beneficiary'
        if not base.is_dir():
            self.skipTest(f'SKIPPED: the FEC beneficiary folder {base} does not exist here')
        cycles = build_cycles(EXPORTER.read_text(encoding='utf-8'))
        self.assertTrue(cycles, 'cannot read supported_cycles from tools/export_crypto_spending_data.R')
        required = reader_requirement(READER.read_text(encoding='utf-8'))
        hash_raw = os.environ.get('MENTAT_TRANSFER_INDEX_HASH_RAW') == '1'
        for cycle in cycles:
            with self.subTest(cycle=cycle):
                reasons = stale_reasons(base / f'oth{str(cycle)[-2:]}', required, cycle, hash_raw)
                self.assertEqual(reasons, [], f'{cycle}: {"; ".join(reasons)}. Every report build publishes this '
                                 f'cycle as not traced until you run {rebuild_command(base, cycle)}')

    def test_negative_control_a_version_4_index_is_reported_stale(self):
        required = reader_requirement(READER.read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / 'itoth.txt').write_text(transfer_row(2024) + '\n', encoding='utf-8')
            fec_transfer_graph.publish_index(folder, fec_transfer_graph.transfer_index(folder / 'itoth.txt', 2024))
            self.assertEqual(stale_reasons(folder, required, 2024, hash_raw=True), [])
            info = json.loads((folder / 'transfer-info.json').read_text(encoding='utf-8'))
            (folder / 'transfer-info.json').write_text(
                json.dumps(dict(info, version=4, ambiguous_version_policy='count_latest')), encoding='utf-8')
            self.assertEqual(stale_reasons(folder, required, 2024),
                             [f'stale: VERSION 4 count_latest, the reader requires VERSION {required[0]} {required[1]}'])
            (folder / 'transfer-info.json').unlink()
            self.assertEqual(stale_reasons(folder, required, 2024), ['missing: no transfer-info.json'])


if __name__ == '__main__':
    unittest.main()
