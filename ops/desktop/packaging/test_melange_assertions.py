import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace

from desktop_backend import configure_melange_assertions


class MelangeAssertionPathTests(unittest.TestCase):
    def test_existing_decisions_are_read_from_selected_data_without_rewriting(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from fec.surfaces import staffer_identity_resolution as identity, staffer_assertions
        previous = identity.USER_ASSERTIONS
        try:
            with tempfile.TemporaryDirectory(prefix='melange-decisions-test-') as folder:
                root = Path(folder)
                file = root/'fec'/'surfaces'/'staffer_user_assertions.json'
                file.parent.mkdir(parents=True)
                original = json.dumps({'coworker_assertions':[{'source_person_ids':['fixture-a','person:fixture-b'], 'id':'fixture'}]}).encode()
                file.write_bytes(original)
                self.assertEqual(configure_melange_assertions(identity, root), file)
                self.assertEqual(staffer_assertions.load_coworker_assertions()[0]['person_ids'], ['person:fixture-a','person:fixture-b'])
                self.assertEqual(file.read_bytes(), original)
        finally:
            identity.USER_ASSERTIONS = previous

    def test_missing_decisions_are_not_silently_created(self):
        with tempfile.TemporaryDirectory(prefix='melange-decisions-missing-') as folder:
            identity = SimpleNamespace()
            path = configure_melange_assertions(identity, Path(folder))
            self.assertFalse(path.exists())
            with self.assertRaises(FileNotFoundError):
                path.read_text()


if __name__ == '__main__':
    unittest.main()
