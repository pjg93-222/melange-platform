"""Selected-cycle snapshots retain evidence and remain private to the run."""
import itertools
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'tools'))
import runtime_inputs
from runtime_inputs import copy_fec_sources

# The cycles a run may select. 2020 is on disk too (F16: the carryover source of 2022), never selected.
SELECTABLE = (2022, 2024, 2026)
CARRYOVER_PATHS = ('fec_beneficiary/ccl', 'fec_beneficiary/oth')


def staged(relative, cycle, cycles):
    """Whether a runtime staged for `cycles` holds this fixture file of `cycle`: every file of a
    selected cycle, and the ccl and oth files of a selected cycle's previous cycle (F16)."""
    return cycle in cycles or (cycle + 2 in cycles and relative.startswith(CARRYOVER_PATHS))


def legacy_source_ignore(source_folder, cycles):
    """runtime_inputs.source_ignore before F16, verbatim: bulk folders of 2022/2024/2026 only (any other
    cycle's folders were copied into every run as if shared), and no C-1 carryover inputs."""
    source_folder = Path(source_folder)
    selected = {int(cycle) for cycle in cycles}
    if not selected or not selected <= {2022, 2024, 2026}:
        raise ValueError('Select supported election cycles before copying FEC inputs.')
    transient = shutil.ignore_patterns('*.sqlite', '*.pending*', 'transfer-index-*', 'cache')

    def ignore(directory, names):
        excluded = set(transient(directory, names))
        if Path(directory) == source_folder:
            for name in names:
                bulk = re.fullmatch(r'(?:cm|cn|ccl|oth|pas2)(22|24|26)(?:\.zip)?', name, re.I)
                leadership = re.fullmatch(r'leadership(2022|2024|2026)\.csv', name, re.I)
                cycle = 2000 + int(bulk[1]) if bulk else int(leadership[1]) if leadership else None
                if cycle is not None and cycle not in selected:
                    excluded.add(name)
        return excluded

    return ignore


class RuntimeInputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'source'
        self.required = {}
        for cycle in (2020, *SELECTABLE):
            suffix = str(cycle)[2:]
            paths = [f'fec_committee_master/cm{suffix}/cm.txt']
            paths += [f'fec_beneficiary/{prefix}{suffix}/{name}' for prefix, name in
                      [('cn', 'cn.txt'), ('ccl', 'ccl.txt'), ('pas2', 'itpas2.txt'), ('oth', 'itoth.txt')]]
            paths += [f'fec_beneficiary/leadership{cycle}.csv',
                      f'fec_beneficiary/oth{suffix}/source-receipt.json',
                      f'fec_beneficiary/oth{suffix}/transfer-current.json',
                      f'fec_beneficiary/oth{suffix}/published/transfer-edges.csv']
            self.required[cycle] = paths
        for relative in sum(self.required.values(), []) + ['fec_beneficiary/shared.json',
                                                          'fec_beneficiary/cache/scratch',
                                                          'fec_beneficiary/oth26/lookup.sqlite']:
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative, encoding='utf-8')

    def misplaced(self, destination, cycles):
        """Every fixture file whose presence in `destination` breaks the staging rule, as (found, path)."""
        return [('present' if (destination / relative).exists() else 'missing', relative)
                for cycle, paths in self.required.items() for relative in paths
                if (destination / relative).exists() != staged(relative, cycle, cycles)]

    def test_every_cycle_selection_preserves_evidence_and_copy_isolation(self):
        for count in (1, 2, 3):
            for cycles in itertools.combinations(SELECTABLE, count):
                with self.subTest(cycles=cycles):
                    destination = self.root / '-'.join(map(str, cycles))
                    copy_fec_sources(self.source, destination, cycles)
                    for cycle, paths in self.required.items():
                        for relative in paths:
                            target = destination / relative
                            self.assertEqual(target.exists(), staged(relative, cycle, cycles), relative)
                            if target.exists():
                                self.assertEqual(target.read_bytes(), (self.source / relative).read_bytes())
                    self.assertTrue((destination / 'fec_beneficiary/shared.json').is_file())
                    self.assertFalse((destination / 'fec_beneficiary/cache').exists())
                    self.assertFalse((destination / 'fec_beneficiary/oth26/lookup.sqlite').exists())
                    relative = self.required[cycles[0]][0]
                    (destination / relative).write_text('private edit')
                    self.assertEqual((self.source / relative).read_text(), relative)

    def test_previous_cycle_stages_only_its_carryover_inputs(self):
        # F16: a 2024 run no longer carries 2020's 1.45 GB OTH file; it carries 2022's transfer index
        # and candidate committees, which its carryover reads, and nothing else of 2022.
        cases = {(2024,): ('22', ('20',)), (2022,): ('20', ()), (2026,): ('24', ('20', '22')),
                 (2022, 2026): ('24', ())}
        for cycles, (previous, absent) in cases.items():
            with self.subTest(cycles=cycles):
                destination = self.root / ('carry-' + '-'.join(map(str, cycles)))
                copy_fec_sources(self.source, destination, cycles)
                self.assertEqual(self.misplaced(destination, cycles), [])
                for relative in (f'ccl{previous}/ccl.txt', f'oth{previous}/itoth.txt',
                                 f'oth{previous}/source-receipt.json', f'oth{previous}/published/transfer-edges.csv'):
                    self.assertTrue((destination / 'fec_beneficiary' / relative).is_file(), relative)
                if int('20' + previous) not in cycles:
                    for relative in (f'fec_beneficiary/cn{previous}', f'fec_beneficiary/pas2{previous}',
                                     f'fec_beneficiary/leadership20{previous}.csv', f'fec_committee_master/cm{previous}'):
                        self.assertFalse((destination / relative).exists(), relative)
                for suffix in absent:
                    for relative in (f'fec_beneficiary/oth{suffix}', f'fec_beneficiary/ccl{suffix}',
                                     f'fec_beneficiary/cn{suffix}', f'fec_beneficiary/pas2{suffix}',
                                     f'fec_beneficiary/leadership20{suffix}.csv', f'fec_committee_master/cm{suffix}'):
                        self.assertFalse((destination / relative).exists(), relative)

    def test_negative_control_each_wrong_staging_is_caught(self):
        # The rule check must flag the pre-F16 copy and three plausible wrong fixes.
        variants = {
            'before F16': (mock.patch.object(runtime_inputs, 'source_ignore', legacy_source_ignore), (2024,),
                           [('present', 'fec_beneficiary/oth20/itoth.txt'), ('present', 'fec_beneficiary/ccl20/ccl.txt'),
                            ('missing', 'fec_beneficiary/oth22/itoth.txt'), ('missing', 'fec_beneficiary/ccl22/ccl.txt')]),
            'every family of C-1': (mock.patch.object(runtime_inputs, 'CARRYOVER_FAMILIES', ('ccl', 'cm', 'cn', 'oth', 'pas2')),
                                    (2024,), [('present', 'fec_beneficiary/cn22/cn.txt'),
                                              ('present', 'fec_committee_master/cm22/cm.txt')]),
            'the earliest C-1 only': (mock.patch.object(runtime_inputs, 'carryover_cycles', lambda s: {min(s) - 2} - set(s)),
                                      (2022, 2026), [('missing', 'fec_beneficiary/oth24/itoth.txt')]),
            'no carryover': (mock.patch.object(runtime_inputs, 'carryover_cycles', lambda s: set()), (2022,),
                             [('missing', 'fec_beneficiary/oth20/itoth.txt'), ('missing', 'fec_beneficiary/ccl20/ccl.txt')]),
        }
        for label, (patcher, cycles, flagged) in variants.items():
            with self.subTest(label), patcher:
                destination = self.root / ('control-' + re.sub(r'\W+', '-', label))
                runtime_inputs.copy_fec_sources(self.source, destination, cycles)
                wrong = self.misplaced(destination, cycles)
                for item in flagged:
                    self.assertIn(item, wrong)
        # And the fix itself passes the same check.
        for cycles in ((2024,), (2022, 2026), (2022,)):
            destination = self.root / ('fixed-' + '-'.join(map(str, cycles)))
            runtime_inputs.copy_fec_sources(self.source, destination, cycles)
            self.assertEqual(self.misplaced(destination, cycles), [])

    def test_invalid_selection_fails_before_copying(self):
        for cycles in ([], [2028], [2026, 2028]):
            with self.assertRaises(ValueError):
                copy_fec_sources(self.source, self.root / 'invalid', cycles)
            self.assertFalse((self.root / 'invalid').exists())

    def missing_source(self, *folders):
        """A copy of the fixture snapshot with the named bulk folders removed."""
        broken = self.root / ('broken-' + '-'.join(folders))
        shutil.copytree(self.source, broken)
        for folder in folders:
            shutil.rmtree(broken / folder)
        return broken

    def test_missing_source_folder_names_it_and_says_how_to_install_it(self):
        for folder in ('fec_beneficiary', 'fec_committee_master'):
            with self.subTest(folder=folder):
                broken = self.missing_source(folder)
                destination = self.root / ('missing-' + folder)
                with self.assertRaises(FileNotFoundError) as caught:
                    copy_fec_sources(broken, destination, [2026])
                message = str(caught.exception)
                self.assertIn(str(broken / folder), message)
                self.assertIn('refresh_fec_sources.py --root', message)
                self.assertFalse(destination.exists())

    def test_both_folders_are_checked_before_anything_is_staged(self):
        # The SECOND folder missing must not leave the first one half-staged: a
        # partially populated runtime reads as a complete snapshot downstream.
        broken = self.missing_source('fec_committee_master')
        destination = self.root / 'half-staged'
        with self.assertRaises(FileNotFoundError):
            copy_fec_sources(broken, destination, [2026])
        self.assertFalse(destination.exists())

    def test_a_missing_folder_outranks_nothing_and_still_reports_bad_cycles(self):
        # Cycle validation keeps precedence, so a caller that got both wrong is
        # told about the selection it controls rather than the data it lacks.
        with self.assertRaises(ValueError):
            copy_fec_sources(self.missing_source('fec_beneficiary'), self.root / 'both-wrong', [2028])
        self.assertFalse((self.root / 'both-wrong').exists())

    def test_present_but_empty_folders_are_staged_and_gated_downstream(self):
        # Existence is the only question answered here. Completeness belongs to
        # build_industry_report.ps1, which checks each required file by name.
        empty = self.root / 'empty-but-present'
        for folder in ('fec_beneficiary', 'fec_committee_master'):
            (empty / folder).mkdir(parents=True)
        destination = self.root / 'from-empty'
        copy_fec_sources(empty, destination, [2026])
        for folder in ('fec_beneficiary', 'fec_committee_master'):
            self.assertTrue((destination / folder).is_dir())
            self.assertEqual(list((destination / folder).iterdir()), [])

    @unittest.skipUnless(os.name == 'nt', 'PowerShell report build contract')
    def test_report_required_inputs_match_each_selected_snapshot(self):
        builder = (PROJECT / 'tools/build_industry_report.ps1').read_text(encoding='utf-8-sig')
        block = builder[builder.index('$inputCycles ='):builder.index('foreach ($requiredPath')]
        script = self.root / 'check-inputs.ps1'
        script.write_text("param([string]$Snapshot)\n$ErrorActionPreference = 'Stop'\n"
                          "$CommitteeMasterDirectory = Join-Path $Snapshot 'fec_committee_master'\n"
                          "$BeneficiaryDirectory = Join-Path $Snapshot 'fec_beneficiary'\n"
                          "$results = @(foreach ($CycleLabel in @('2021-2022','2023-2024','2025-2026','')) {\n"
                          + block + "\n[pscustomobject]@{label=$CycleLabel; files=@($committeeFiles)+@($beneficiaryFiles)}\n})\n"
                          "$results | ConvertTo-Json -Depth 4\n", encoding='utf-8')
        process = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Snapshot', str(self.source)],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        for row in json.loads(process.stdout):
            cycles = [int(row['label'][-4:])] if row['label'] else [2022, 2024, 2026]
            self.assertEqual(len(row['files']), 5 * len(cycles))
            destination = self.root / ('build-' + (row['label'] or 'all'))
            copy_fec_sources(self.source, destination, cycles)
            for name in row['files']:
                relative = Path(name).relative_to(self.source)
                self.assertTrue((destination / relative).is_file(), name)


if __name__ == '__main__':
    unittest.main()
