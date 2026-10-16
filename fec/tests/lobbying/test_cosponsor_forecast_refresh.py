# -*- coding: utf-8 -*-
"""Kernel tests for the two dial-gated refresh producers.

Covers fec/legislation/refresh_billstatus_bulk.py (BUILD A: the weekly GovInfo bulk
archive refresh) and fec/lobbying/refresh_cosponsor_forecast.py (BUILD B: the
hash-gated chain step in front of bill_lobbying_sidecar.py), plus the
``compare_inputs`` function that audit_full.py's ``cosponsor_forecast_freshness``
check shares with BUILD B's chain gate.

SELF-CONTAINED BY CONSTRUCTION.  Every test runs against synthetic fixtures in a
``tempfile`` directory.  Nothing here reaches %LOCALAPPDATA%\\Melange, any live
repo artifact, or the network: BUILD A takes an injected ``fetch`` and BUILD B an
injected ``runner``, so neither the GovInfo transport nor the 16-minute forecaster
is ever exercised.  GLASSBOX_EVENTS_DIR is redirected to a temp directory BEFORE
the producers are imported, because surfaces/glassbox_emit.py resolves its output
directory at import time.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

# Redirect the Glass Box stream before importing anything that emits to it.
_GB_TMP = tempfile.mkdtemp(prefix='fcst-refresh-glassbox-')
os.environ['GLASSBOX_EVENTS_DIR'] = _GB_TMP

FEC_DIR = Path(__file__).resolve().parents[2]
if str(FEC_DIR) not in sys.path:
    sys.path.insert(0, str(FEC_DIR))

from legislation import refresh_billstatus_bulk as BULK
from lobbying import refresh_cosponsor_forecast as FCST


# ------------------------------------------------------------------------------------
# fixtures
# ------------------------------------------------------------------------------------
def zip_bytes(member='BILLSTATUS-119-hr/BILLS-119hr1.xml', payload=b'<billStatus/>'):
    """A deterministic zip: identical arguments always produce identical bytes."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        info = zipfile.ZipInfo(member, date_time=(2026, 1, 1, 0, 0, 0))
        archive.writestr(info, payload)
    return buffer.getvalue()


def sha_upper(data):
    return hashlib.sha256(data).hexdigest().upper()


def fake_fetcher(bodies, calls=None):
    """Return a fetch(url, dest) serving ``bodies`` keyed by bill type."""
    def fetch(url, dest):
        type_token = url.rsplit('/', 1)[-1].rsplit('-', 1)[-1][:-4]
        if calls is not None:
            calls.append(url)
        body = bodies[type_token]
        if isinstance(body, Exception):
            raise body
        with open(dest, 'wb') as handle:
            handle.write(body)
        return len(body)
    return fetch


def write_report(out_dir, inputs, billstatus=None):
    """Write a synthetic cosponsor_forecast_report.json in the SHIPPED provenance shape.

    fec/lobbying/cosponsor_forecast.py:3191-3199 emits::

        "provenance": {
            "every_input_sha256": [
                {
                    **inputs[path],
                    "roles": sorted(inputs[path]["roles"]),
                }
                for path in sorted(inputs)
            ],

    where each ``inputs[path]`` was built by ``_record_input`` (:411-421) as::

            inputs[absolute] = {
                "path": absolute,
                "sha256": _sha256_file(absolute),
                "roles": [role],
            }

    and ``_sha256_file`` (:400-408) returns ``digest.hexdigest().upper()``.  So the
    list is one ``{path, sha256, roles}`` record per input, sorted by path, with
    UPPERCASE hex — exactly what this fixture reproduces.  ``provenance.billstatus``
    (:3201) is the ``load_billstatus`` receipt (:617-664): one entry per congress
    carrying ``archives: [{bill_type, path, sha256, ...}]``.
    """
    report = {
        'provenance': {
            'every_input_sha256': [
                {'path': path, 'roles': ['billstatus_congress_119'], 'sha256': sha}
                for path, sha in sorted(inputs.items())
            ],
            'billstatus': billstatus if billstatus is not None else [],
        },
    }
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, FCST.REPORT_NAME)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    return path


class TempCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='fcst-refresh-')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def path(self, *parts):
        return os.path.join(self.tmp, *parts)

    def read_json(self, path):
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)


# ------------------------------------------------------------------------------------
# BUILD A — fec/legislation/refresh_billstatus_bulk.py
# ------------------------------------------------------------------------------------
class BillstatusBulkRefreshTests(TempCase):
    TYPES = ['hr', 's']

    def argv(self, **over):
        argv = ['--congress', '119', '--types'] + list(over.get('types', self.TYPES)) + [
            '--raw-root', over.get('raw_root', self.path('raw')),
            '--receipt', over.get('receipt', self.path('receipt.json'))]
        return argv

    def test_dial_off_writes_skip_receipt_and_downloads_nothing(self):
        calls = []
        receipt = self.path('receipt.json')
        code = BULK.run(self.argv(), fetch=fake_fetcher({}, calls), environ={})
        self.assertEqual(code, 0)
        payload = self.read_json(receipt)
        self.assertEqual(payload['status'], 'SKIPPED_DIAL_OFF')
        self.assertEqual(payload['archives'], [])
        self.assertFalse(payload['any_changed'])
        self.assertEqual(calls, [], 'dial-off must not fetch anything')
        # The archive directory must not even be created.
        self.assertFalse(os.path.exists(self.path('raw', 'billstatus119')))

    def test_dial_unset_is_off(self):
        # Absence and '0' must behave identically -- the default reproduces production.
        for environ in ({}, {'BILLSTATUS_BULK_REFRESH': '0'}):
            code = BULK.run(self.argv(), fetch=fake_fetcher({}), environ=environ)
            self.assertEqual(code, 0)
            self.assertEqual(self.read_json(self.path('receipt.json'))['status'],
                             'SKIPPED_DIAL_OFF')

    def test_all_or_nothing_one_bad_archive_leaves_last_good_untouched(self):
        directory = self.path('raw', 'billstatus119')
        os.makedirs(directory)
        # Seed BOTH production archives with recognisable last-good bytes.
        last_good = {}
        for token in self.TYPES:
            body = zip_bytes(payload=b'<lastGood type="%s"/>' % token.encode())
            target = os.path.join(directory, 'BILLSTATUS-119-%s.zip' % token)
            with open(target, 'wb') as handle:
                handle.write(body)
            last_good[token] = sha_upper(body)
        # 'hr' downloads fine; 's' arrives corrupt (not a zip at all).
        bodies = {'hr': zip_bytes(payload=b'<fresh/>'), 's': b'this is not a zip'}
        code = BULK.run(self.argv(), fetch=fake_fetcher(bodies),
                        environ={'BILLSTATUS_BULK_REFRESH': '1'})
        self.assertEqual(code, 1)
        payload = self.read_json(self.path('receipt.json'))
        self.assertEqual(payload['status'], 'FAILED')
        self.assertTrue(payload['error'])
        self.assertEqual(payload['archives'], [])
        self.assertFalse(payload['any_changed'])
        # The good archive must NOT have been published just because it passed.
        for token in self.TYPES:
            target = os.path.join(directory, 'BILLSTATUS-119-%s.zip' % token)
            with open(target, 'rb') as handle:
                self.assertEqual(sha_upper(handle.read()), last_good[token],
                                 'archive %s was modified by a failed run' % token)
        # No temp files may survive a failure.
        self.assertEqual([n for n in os.listdir(directory) if not n.endswith('.zip')], [])
        # No data-side sidecar receipt is written for a failed run.
        self.assertFalse(os.path.exists(os.path.join(directory, 'BILLSTATUS-119.receipt.json')))

    def test_archive_without_xml_member_is_refused(self):
        # A structurally valid zip carrying no XML is not a BILLSTATUS archive.
        bodies = {'hr': zip_bytes(member='readme.txt', payload=b'nope'),
                  's': zip_bytes(payload=b'<ok/>')}
        code = BULK.run(self.argv(), fetch=fake_fetcher(bodies),
                        environ={'BILLSTATUS_BULK_REFRESH': '1'})
        self.assertEqual(code, 1)
        self.assertEqual(self.read_json(self.path('receipt.json'))['status'], 'FAILED')

    def test_success_records_changed_and_unchanged_per_archive(self):
        directory = self.path('raw', 'billstatus119')
        os.makedirs(directory)
        unchanged_body = zip_bytes(payload=b'<same/>')
        # 'hr' already holds exactly the bytes that will be fetched -> changed False.
        with open(os.path.join(directory, 'BILLSTATUS-119-hr.zip'), 'wb') as handle:
            handle.write(unchanged_body)
        bodies = {'hr': unchanged_body, 's': zip_bytes(payload=b'<new/>')}
        calls = []
        code = BULK.run(self.argv(), fetch=fake_fetcher(bodies, calls),
                        environ={'BILLSTATUS_BULK_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.path('receipt.json'))
        self.assertEqual(payload['status'], 'RAN')
        self.assertTrue(payload['any_changed'])
        by_type = {row['type']: row for row in payload['archives']}
        self.assertEqual(sorted(by_type), ['hr', 's'])
        self.assertFalse(by_type['hr']['changed'])
        self.assertEqual(by_type['hr']['previous_sha256'], sha_upper(unchanged_body))
        self.assertEqual(by_type['hr']['sha256'], sha_upper(unchanged_body))
        self.assertTrue(by_type['s']['changed'])
        self.assertIsNone(by_type['s']['previous_sha256'], 'no prior archive existed')
        self.assertEqual(by_type['s']['sha256'], sha_upper(bodies['s']))
        self.assertEqual(by_type['s']['bytes'], len(bodies['s']))
        # The official URL shape is part of the contract.
        self.assertIn(
            'https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/BILLSTATUS-119-hr.zip', calls)
        # Bytes actually landed on the production names.
        with open(os.path.join(directory, 'BILLSTATUS-119-s.zip'), 'rb') as handle:
            self.assertEqual(handle.read(), bodies['s'])
        # The data-side sidecar mirrors the archive list.
        sidecar = self.read_json(os.path.join(directory, 'BILLSTATUS-119.receipt.json'))
        self.assertEqual(sidecar['archives'], payload['archives'])
        self.assertEqual(sidecar['congress'], 119)

    def test_all_unchanged_reports_any_changed_false(self):
        directory = self.path('raw', 'billstatus119')
        os.makedirs(directory)
        bodies = {}
        for token in self.TYPES:
            body = zip_bytes(payload=b'<steady type="%s"/>' % token.encode())
            bodies[token] = body
            with open(os.path.join(directory, 'BILLSTATUS-119-%s.zip' % token), 'wb') as handle:
                handle.write(body)
        code = BULK.run(self.argv(), fetch=fake_fetcher(bodies),
                        environ={'BILLSTATUS_BULK_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.path('receipt.json'))
        self.assertEqual(payload['status'], 'RAN')
        self.assertFalse(payload['any_changed'])
        self.assertTrue(all(not row['changed'] for row in payload['archives']))

    def test_official_url_pinning_refuses_non_govinfo(self):
        for bad in ('http://www.govinfo.gov/x.zip', 'https://evil.example/x.zip',
                    'https://user:pw@www.govinfo.gov/x.zip'):
            with self.assertRaises(BULK.RefreshError):
                BULK.official_url(bad)
        BULK.official_url(BULK.archive_url(119, 'hr'))


# ------------------------------------------------------------------------------------
# BUILD B — fec/lobbying/refresh_cosponsor_forecast.py
# ------------------------------------------------------------------------------------
class CosponsorForecastRefreshTests(TempCase):
    def setUp(self):
        super().setUp()
        self.out_dir = self.path('cosponsor_sc')
        self.raw = self.path('raw', 'billstatus119')
        os.makedirs(self.out_dir)
        os.makedirs(self.raw)
        # One recorded input: a BILLSTATUS archive whose bytes we control.
        self.zip_path = os.path.join(self.raw, 'BILLSTATUS-119-hr.zip')
        self.body = zip_bytes(payload=b'<v1/>')
        with open(self.zip_path, 'wb') as handle:
            handle.write(self.body)
        self.receipt = self.path('receipt.json')
        self.log = self.path('forecast.log')

    def write_current_report(self, extra_inputs=None):
        inputs = {os.path.abspath(self.zip_path): sha_upper(self.body)}
        inputs.update(extra_inputs or {})
        billstatus = [{'congress': 119, 'archives': [
            {'bill_type': 'HR', 'path': os.path.abspath(self.zip_path),
             'sha256': sha_upper(self.body)}]}]
        write_report(self.out_dir, inputs, billstatus)
        # Production scores exist alongside the report.
        with open(os.path.join(self.out_dir, FCST.SCORES_NAME), 'w', encoding='utf-8') as handle:
            handle.write('congress,bill,member\n119,hr1,A000001\n')

    def argv(self, *extra):
        return ['--out-dir', self.out_dir, '--forecaster', self.path('never_invoked.py'),
                '--receipt', self.receipt, '--log', self.log] + list(extra)

    def make_runner(self, returncode=0, write_outputs=True, calls=None,
                    report_body='{"fresh": true}', scores_body='congress,bill,member\n'):
        def runner(cmd, log_path):
            if calls is not None:
                calls.append(cmd)
            staging = cmd[cmd.index('--out-dir') + 1]
            self.assertIn('--same-chamber-only', cmd)
            self.assertTrue(os.path.isdir(staging), 'staging dir must exist before the run')
            self.assertNotEqual(os.path.abspath(staging), os.path.abspath(self.out_dir),
                                'the forecaster must never write straight to production')
            if write_outputs:
                # newline='' throughout: Windows text mode would otherwise rewrite '\n'
                # as '\r\n', so the bytes on disk would not be the bytes this fixture
                # declares and every sha256 assertion below would be checking a fiction.
                with open(os.path.join(staging, FCST.REPORT_NAME), 'w',
                          encoding='utf-8', newline='') as h:
                    h.write(report_body)
                with open(os.path.join(staging, FCST.SCORES_NAME), 'w',
                          encoding='utf-8', newline='') as h:
                    h.write(scores_body)
            return returncode
        return runner

    # -- dial ------------------------------------------------------------------------
    def test_dial_off_skips_without_running_forecaster(self):
        self.write_current_report()
        calls = []
        code = FCST.run(self.argv(), runner=self.make_runner(calls=calls), environ={})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'SKIPPED_DIAL_OFF')
        self.assertEqual(calls, [])

    # -- gate decisions --------------------------------------------------------------
    def test_missing_report_runs(self):
        # No report at all: the gate cannot prove freshness, so it must rebuild.
        calls = []
        code = FCST.run(self.argv(), runner=self.make_runner(calls=calls),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'RAN')
        self.assertIn('report missing', payload['decided_because'])
        self.assertEqual(len(calls), 1)

    def test_unchanged_skips_with_zero_runner_calls(self):
        self.write_current_report()
        calls = []
        code = FCST.run(self.argv(), runner=self.make_runner(calls=calls),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'SKIPPED_UNCHANGED')
        self.assertEqual(calls, [], 'an unchanged input set must not cost 16 minutes')
        self.assertEqual(len(payload['inputs']), 1)
        self.assertFalse(payload['inputs'][0]['changed'])
        self.assertEqual(payload['new_zips'], [])
        # No staging directory may be left behind by a skip.
        self.assertEqual([n for n in os.listdir(self.out_dir) if n.startswith('.staging')], [])

    def test_changed_hash_runs_and_promotes_cleanly(self):
        self.write_current_report()
        # The archive moves underneath the report.
        with open(self.zip_path, 'wb') as handle:
            handle.write(zip_bytes(payload=b'<v2-moved/>'))
        code = FCST.run(self.argv(), runner=self.make_runner(report_body='{"v": 2}',
                                                            scores_body='congress\n119\n'),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'RAN')
        self.assertTrue(any('hash' in reason for reason in payload['decided_because']))
        # Staging outputs were promoted onto the production names...
        with open(os.path.join(self.out_dir, FCST.REPORT_NAME), encoding='utf-8') as handle:
            self.assertEqual(handle.read(), '{"v": 2}')
        with open(os.path.join(self.out_dir, FCST.SCORES_NAME), encoding='utf-8') as handle:
            self.assertEqual(handle.read(), 'congress\n119\n')
        # ...and the undo copies were cleaned up, along with the staging dir.
        leftovers = [n for n in os.listdir(self.out_dir)
                     if n.endswith('.previous') or n.startswith('.staging')]
        self.assertEqual(leftovers, [])
        self.assertEqual(payload['outputs']['report']['bytes'], len('{"v": 2}'))
        self.assertEqual(payload['outputs']['scores']['sha256'],
                         sha_upper(b'congress\n119\n'))
        self.assertEqual(payload['forecaster_returncode'], 0)
        self.assertEqual(payload['log_path'], self.log)

    def test_missing_input_file_runs(self):
        self.write_current_report()
        os.unlink(self.zip_path)
        code = FCST.run(self.argv(), runner=self.make_runner(),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'RAN')
        self.assertTrue(any('missing' in reason for reason in payload['decided_because']))

    def test_new_unlisted_zip_runs(self):
        # Every recorded hash still matches; only a NEW archive has appeared. A hash
        # check over the report's own paths cannot see this, which is why the glob exists.
        self.write_current_report()
        with open(os.path.join(self.raw, 'BILLSTATUS-119-s.zip'), 'wb') as handle:
            handle.write(zip_bytes(payload=b'<brand-new/>'))
        calls = []
        code = FCST.run(self.argv(), runner=self.make_runner(calls=calls),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'RAN')
        self.assertTrue(any('absent from the report' in r for r in payload['decided_because']))
        self.assertEqual(len(payload['new_zips']), 1)
        self.assertTrue(payload['new_zips'][0].endswith('BILLSTATUS-119-s.zip'))
        self.assertEqual(len(calls), 1)

    def test_sidecar_receipt_is_not_mistaken_for_an_archive(self):
        # BUILD A drops BILLSTATUS-119.receipt.json beside the zips; the glob is
        # BILLSTATUS-*.zip, so it must not read as an unlisted archive.
        self.write_current_report()
        with open(os.path.join(self.raw, 'BILLSTATUS-119.receipt.json'), 'w',
                  encoding='utf-8') as handle:
            handle.write('{}')
        code = FCST.run(self.argv(), runner=self.make_runner(),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        self.assertEqual(self.read_json(self.receipt)['status'], 'SKIPPED_UNCHANGED')

    def test_force_runs_even_when_unchanged(self):
        self.write_current_report()
        code = FCST.run(self.argv('--force'), runner=self.make_runner(),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 0)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'RAN')
        self.assertIn('--force', payload['decided_because'])

    # -- failure -----------------------------------------------------------------------
    def test_runner_failure_leaves_production_untouched(self):
        self.write_current_report()
        original_report = Path(os.path.join(self.out_dir, FCST.REPORT_NAME)).read_bytes()
        original_scores = Path(os.path.join(self.out_dir, FCST.SCORES_NAME)).read_bytes()
        with open(self.zip_path, 'wb') as handle:      # force a RUN decision
            handle.write(zip_bytes(payload=b'<moved/>'))
        code = FCST.run(self.argv(), runner=self.make_runner(returncode=3, write_outputs=False),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 1)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'FAILED')
        self.assertEqual(payload['forecaster_returncode'], 3)
        self.assertIn('exited 3', payload['error'])
        self.assertEqual(
            Path(os.path.join(self.out_dir, FCST.REPORT_NAME)).read_bytes(), original_report)
        self.assertEqual(
            Path(os.path.join(self.out_dir, FCST.SCORES_NAME)).read_bytes(), original_scores)
        leftovers = [n for n in os.listdir(self.out_dir)
                     if n.endswith('.previous') or n.startswith('.staging')]
        self.assertEqual(leftovers, [], 'a failed run must leave no staging or undo residue')

    def test_forecaster_exit_zero_without_outputs_is_a_failure(self):
        self.write_current_report()
        original_report = Path(os.path.join(self.out_dir, FCST.REPORT_NAME)).read_bytes()
        code = FCST.run(self.argv('--force'),
                        runner=self.make_runner(returncode=0, write_outputs=False),
                        environ={'COSPONSOR_FORECAST_REFRESH': '1'})
        self.assertEqual(code, 1)
        payload = self.read_json(self.receipt)
        self.assertEqual(payload['status'], 'FAILED')
        self.assertIn('did not write', payload['error'])
        self.assertEqual(
            Path(os.path.join(self.out_dir, FCST.REPORT_NAME)).read_bytes(), original_report)


# ------------------------------------------------------------------------------------
# compare_inputs — the function audit_full.py's cosponsor_forecast_freshness check shares
# with BUILD B's chain gate. If these disagree, the audit certifies what the producer denies.
# ------------------------------------------------------------------------------------
class CompareInputsTests(TempCase):
    def make(self, payload=b'<v1/>'):
        raw = self.path('raw', 'billstatus119')
        os.makedirs(raw, exist_ok=True)
        zip_path = os.path.join(raw, 'BILLSTATUS-119-hr.zip')
        body = zip_bytes(payload=payload)
        with open(zip_path, 'wb') as handle:
            handle.write(body)
        out_dir = self.path('cosponsor_sc')
        write_report(out_dir, {os.path.abspath(zip_path): sha_upper(body)},
                     [{'congress': 119, 'archives': [
                         {'bill_type': 'HR', 'path': os.path.abspath(zip_path),
                          'sha256': sha_upper(body)}]}])
        return out_dir, zip_path

    def audit_verdict(self, out_dir):
        """Exactly the call sequence audit_full.py's check performs."""
        comparison = FCST.compare_inputs(FCST.load_report(out_dir))
        return comparison['agrees'], FCST.freshness_detail(comparison, limit=5), comparison

    def test_pass_case(self):
        out_dir, _ = self.make()
        agrees, detail, comparison = self.audit_verdict(out_dir)
        self.assertTrue(agrees)
        self.assertEqual(detail, '')
        self.assertEqual(len(comparison['inputs']), 1)
        self.assertFalse(comparison['inputs'][0]['changed'])
        self.assertEqual(comparison['reasons'], [])

    def test_fail_case_hash_moved(self):
        out_dir, zip_path = self.make()
        with open(zip_path, 'wb') as handle:
            handle.write(zip_bytes(payload=b'<v2/>'))
        agrees, detail, comparison = self.audit_verdict(out_dir)
        self.assertFalse(agrees)
        self.assertIn('changed', detail)
        self.assertIn('BILLSTATUS-119-hr.zip', detail)
        self.assertEqual(comparison['changed'], [os.path.abspath(zip_path)])

    def test_fail_case_report_absent(self):
        agrees, detail, comparison = self.audit_verdict(self.path('nonexistent'))
        self.assertFalse(agrees)
        self.assertIn('absent', detail)
        self.assertFalse(comparison['report_present'])
        self.assertEqual(comparison['reasons'], ['report missing'])

    def test_fail_case_unlisted_archive(self):
        out_dir, zip_path = self.make()
        with open(os.path.join(os.path.dirname(zip_path), 'BILLSTATUS-119-s.zip'), 'wb') as h:
            h.write(zip_bytes(payload=b'<extra/>'))
        agrees, detail, comparison = self.audit_verdict(out_dir)
        self.assertFalse(agrees)
        self.assertIn('unlisted-archive', detail)
        self.assertEqual(len(comparison['new_zips']), 1)

    def test_detail_truncates_to_five(self):
        out_dir = self.path('cosponsor_sc')
        inputs = {self.path('gone-%02d.bin' % i): sha_upper(b'x') for i in range(9)}
        write_report(out_dir, inputs)
        comparison = FCST.compare_inputs(FCST.load_report(out_dir))
        detail = FCST.freshness_detail(comparison, limit=5)
        self.assertEqual(detail.count('missing '), 5)
        self.assertIn('(+4 more)', detail)

    def test_normalize_accepts_mapping_shape(self):
        # Defensive: a future {path: sha} shape must degrade to a working gate,
        # never to a silently empty one.
        mapping = FCST.normalize_inputs(
            {'provenance': {'every_input_sha256': {'C:/a.zip': 'AB', 'C:/b.zip': 'CD'}}})
        self.assertEqual(mapping, {'C:/a.zip': 'AB', 'C:/b.zip': 'CD'})
        listed = FCST.normalize_inputs(
            {'provenance': {'every_input_sha256': [
                {'path': 'C:/a.zip', 'sha256': 'AB', 'roles': ['r']}]}})
        self.assertEqual(listed, {'C:/a.zip': 'AB'})

    def test_empty_or_unrecognised_provenance_never_agrees(self):
        # An empty comparison proves nothing: it must not read as "fresh".
        for report in ({}, {'provenance': {}}, {'provenance': {'every_input_sha256': 'x'}},
                       {'provenance': 'junk'}):
            comparison = FCST.compare_inputs(report)
            self.assertFalse(comparison['agrees'], report)
            self.assertTrue(comparison['reasons'], report)
        self.assertFalse(FCST.compare_inputs([1])['agrees'])

    def test_non_object_report_loads_as_missing(self):
        out_dir = self.path('cosponsor_sc')
        os.makedirs(out_dir)
        Path(os.path.join(out_dir, FCST.REPORT_NAME)).write_text('[1]', encoding='utf-8')
        self.assertIsNone(FCST.load_report(out_dir))

    def test_deleted_scores_csv_is_a_change_when_out_dir_given(self):
        out_dir, _ = self.make()
        self.assertFalse(FCST.compare_inputs(FCST.load_report(out_dir), out_dir)['agrees'])
        Path(os.path.join(out_dir, FCST.SCORES_NAME)).write_text('x\n', encoding='utf-8')
        self.assertTrue(FCST.compare_inputs(FCST.load_report(out_dir), out_dir)['agrees'])

    def test_hash_comparison_is_case_insensitive(self):
        out_dir, zip_path = self.make()
        report = FCST.load_report(out_dir)
        entry = report['provenance']['every_input_sha256'][0]
        entry['sha256'] = entry['sha256'].lower()
        comparison = FCST.compare_inputs(report)
        self.assertTrue(comparison['agrees'],
                        'lowercase hex in the report must not fake a freshness failure')


if __name__ == '__main__':
    # exit=False so the temp Glass Box directory is always cleaned up, but the exit CODE
    # is still the verdict: run_tests.py requires returncode==0 as well as a readable
    # "Ran N tests / OK" tail, and a suite that always exits 0 cannot fail the kernel gate.
    try:
        _result = unittest.main(verbosity=2, exit=False).result
    finally:
        shutil.rmtree(_GB_TMP, ignore_errors=True)
    sys.exit(0 if _result.wasSuccessful() else 1)
