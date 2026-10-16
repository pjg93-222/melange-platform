"""Data roots survive installation, lazy imports, and collector child processes."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import runtime_paths as paths


class RuntimePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mentat-roots-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data, self.console = self.root/'selected-data', self.root/'selected-console'
        self.data.mkdir(); self.console.mkdir()
        self.powershell = shutil.which('powershell.exe')
        self.system_root = os.environ.get('SystemRoot','')
        self.environment = patch.dict(os.environ, {'MENTAT_DATA_ROOT':str(self.data),
            'MENTAT_CONSOLE_ROOT':str(self.console)}, clear=True)
        self.environment.start(); self.addCleanup(self.environment.stop)

    def test_source_default_preserved(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(paths.data_root(), APP.parent)
            self.assertEqual(paths.app_data_root(), APP/'data')

    def test_portable_roundtrip_and_integrity(self):
        for root, label in ((self.data,'data'), (self.console,'console')):
            file = root/'run'/'source.sqlite'; file.parent.mkdir(); file.write_bytes(b'fixture')
            ref = paths.portable_receipt(file)
            self.assertEqual(ref, f'mentat-{label}:/run/source.sqlite')
            self.assertEqual(paths.resolve_receipt(ref, hashlib.sha256(b'fixture').hexdigest()), file)
            with self.assertRaisesRegex(ValueError, 'pinned digest'):
                paths.resolve_receipt(ref, '0'*64)

    def test_explicit_windows_rebase_selected_root_wins(self):
        old = self.root/'old-machine'; old.mkdir()
        (old/'source.sqlite').write_bytes(b'old')
        (self.data/'source.sqlite').write_bytes(b'selected')
        os.environ['MENTAT_RECEIPT_ROOTS'] = json.dumps([{'sourceRoot':str(old),
            'targetRoot':'data','relativePath':''}])
        self.assertEqual(paths.resolve_receipt(str(old/'source.sqlite')), self.data/'source.sqlite')
        unmapped = self.root/'unmapped'/'source.sqlite'
        self.assertEqual(paths.resolve_receipt(str(unmapped)), unmapped)
        with self.assertRaisesRegex(ValueError, 'pinned digest'):
            paths.resolve_receipt(str(old/'source.sqlite'), hashlib.sha256(b'old').hexdigest())

    def test_duplicate_or_malformed_maps_refused(self):
        item = {'sourceRoot':'C:\\Old\\Mentat','targetRoot':'data','relativePath':''}
        for value in ([item,dict(item,sourceRoot='c:\\old\\mentat')],
                      [dict(item,targetRoot=[])], [dict(item,relativePath='../escape')]):
            os.environ['MENTAT_RECEIPT_ROOTS'] = json.dumps(value)
            with self.assertRaises(ValueError):
                paths.resolve_receipt('C:\\Old\\Mentat\\file.sqlite')

    def test_traversal_and_junction_escape_refused(self):
        for ref in ('mentat-data:/../file','mentat-data:/x/../../file',
                    'mentat-data://absolute','mentat-data:/C:/file','mentat-data:/x//file'):
            with self.assertRaises(ValueError): paths.resolve_receipt(ref)
        outside = self.root/'outside'; outside.mkdir()
        link = self.data/'escape'
        if os.name == 'nt':
            env = dict(os.environ, TEST_LINK=str(link), TEST_TARGET=str(outside), SystemRoot=self.system_root)
            subprocess.run([self.powershell,'-NoProfile','-Command',
                'New-Item -ItemType Junction -Path $env:TEST_LINK -Target $env:TEST_TARGET | Out-Null'],
                env=env, capture_output=True, check=True)
        else: link.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'leaves'):
            paths.resolve_receipt('mentat-data:/escape/file')

    def test_collector_child_keeps_shared_data_and_pins_run_inputs(self):
        runtime = self.root/'run'/'runtime'/'mentat'
        env = paths.collector_environment(os.environ, runtime)
        payload = subprocess.check_output([sys.executable, str(APP/'runtime_paths.py')], env=env, text=True)
        value = json.loads(payload)
        self.assertEqual(value['data'], str(self.data))
        self.assertEqual(value['console'], str(self.console))
        self.assertEqual(value['reportCache'], str(self.data/'.cache'/'industry-report'))
        self.assertEqual(env['MENTAT_INPUTS_DIR'], str(runtime/'inputs'))
        self.assertEqual(env['MENTAT_WORKSPACE_ROOT'], str(runtime))

    def test_late_forecast_import_resolves_selected_models_and_state(self):
        models = self.root/'model-registry'; models.mkdir()
        os.environ['MENTAT_APP_DATA_ROOT'] = str(models)
        sys.path.insert(0, str(APP.parent/'tools'))
        forecast = importlib.reload(importlib.import_module('giving_forecast'))
        self.assertEqual(forecast.MODEL_FILE.parent, models)
        self.assertEqual(forecast.ANCHOR_FILE, self.data/'state/giving-anchors/giving-anchors-v1.json')
        self.assertEqual(forecast.WAIVER_FILE, self.data/'forecast/refit/registry/gate_waivers.json')
        pac = importlib.reload(importlib.import_module('pac_giving_forecast'))
        bundle = {'schema_version':1, 'model_id':pac.MODEL_ID, 'currency':'USD',
                  'accounting_basis':pac.ACCOUNTING_BASIS, 'built_at':'2026-10-14T00:00:00Z'}
        raw = json.dumps(bundle).encode(); digest = hashlib.sha256(raw).hexdigest()
        file = models/'pac-versions'/digest/'pac-giving-v1.json'
        file.parent.mkdir(parents=True); file.write_bytes(raw)
        (models/'pac-giving-current.json').write_text(json.dumps({'schema_version':1,
            'model_id':pac.MODEL_ID,'sha256':digest,'active':f'pac-versions/{digest}/pac-giving-v1.json'}))
        self.assertEqual(pac._load_bundle(), (bundle,digest))


if __name__ == '__main__': unittest.main()
