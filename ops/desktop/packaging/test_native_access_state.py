import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_access_state as state


@unittest.skipUnless(os.name == 'nt', 'Actual Windows DPAPI required')
class ProtectedStateTests(unittest.TestCase):
    def test_actual_dpapi_roundtrip_profile_kind_and_tamper_binding(self):
        with tempfile.TemporaryDirectory(prefix='access-state-fixture-') as directory:
            root=Path(directory);other=root/'other';other.mkdir()
            value={'schemaVersion':1,'fixture':'not a real credential','items':[1,2]}
            state.write(root,'state',value)
            self.assertEqual(state.read(root,'state'),value)
            raw=(root/'access-state.dpapi').read_bytes()
            self.assertNotIn(b'not a real credential',raw)
            (root/'access-owner.dpapi').write_bytes(raw)
            with self.assertRaises(state.StateError):state.read(root,'owner')
            (other/'access-state.dpapi').write_bytes(raw)
            with self.assertRaises(state.StateError):state.read(other,'state')
            (root/'access-state.dpapi').write_bytes(raw[:-1]+bytes([raw[-1]^1]))
            with self.assertRaises(state.StateError):state.read(root,'state')
            self.assertRegex(state.describe(root)['windowsSid'],r'^S-1-')
            self.assertEqual(state.path_hash(root),state.path_hash(root/'.'))

    def test_atomic_failure_preserves_prior_ciphertext_and_rejects_unsafe_kind_size(self):
        with tempfile.TemporaryDirectory(prefix='access-state-fixture-') as directory:
            root=Path(directory);state.write(root,'state',{'old':True})
            with patch.object(state.os,'replace',side_effect=OSError('fixture replacement failure')):
                with self.assertRaises(OSError):state.write(root,'state',{'new':True})
            self.assertEqual(state.read(root,'state'),{'old':True})
            self.assertEqual(list(root.glob('*.tmp')),[])
            with self.assertRaises(state.StateError):state.write(root,'../outside',{})
            with self.assertRaises(state.StateError):state.write(root,'state',{'huge':'a'*(state.MAX_VALUE+1)})
            with self.assertRaises(ValueError):state.write(root,'state',{'bad':float('nan')})
            with self.assertRaises(state.StateError):state.canonical_path('relative')
            self.assertIsNone(state.read(root,'identity'))

    def test_github_district_high_water_is_independent_and_kind_bound(self):
        with tempfile.TemporaryDirectory(prefix='github-district-state-fixture-') as directory:
            root = Path(directory)
            value = {'schemaVersion': 1, 'catalogSequence': 1, 'fixture': True}
            state.write(root, 'github-district-state', value)
            state.write(root, 'dataset-journal', {'recipient': True})
            state.write(root, 'state', {'appUpdateSequence': 700})
            self.assertEqual(state.read(root, 'github-district-state'), value)
            self.assertEqual(state.read(root, 'dataset-journal'), {'recipient': True})
            self.assertEqual(state.read(root, 'state'), {'appUpdateSequence': 700})
            (root / 'access-github-district-state.dpapi').write_bytes(
                (root / 'access-dataset-journal.dpapi').read_bytes())
            with self.assertRaises(state.StateError):
                state.read(root, 'github-district-state')


if __name__=='__main__':unittest.main()
