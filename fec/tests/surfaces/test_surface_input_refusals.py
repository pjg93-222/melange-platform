"""Two defensive input repairs (2026-10-16, G2), synthetic fixtures only:
- build_audit_workbook.ensure_inputs: a cached handoff that is valid JSON but not an object (e.g. []) is
  treated as stale and re-extracted, instead of raising AttributeError on .get.
- build_clean_workspace.page_data: a page without a page-data script raises a ValueError naming the page,
  instead of a TypeError on None. (No atomic write is involved in either repair.)"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from surfaces import build_audit_workbook as builder  # noqa: E402
from surfaces import extract_workbook_audit_inputs as extractor  # noqa: E402
import moneyball_core  # noqa: E402


def clean_workspace():
    spec = importlib.util.spec_from_file_location('build_clean_workspace_refusals', FEC / 'surfaces' / 'build_clean_workspace.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CachedHandoffShape(unittest.TestCase):
    def test_a_non_object_cached_handoff_is_re_extracted_not_a_crash(self):
        for cached in ([], 'text', 7):
            with self.subTest(cached=cached), tempfile.TemporaryDirectory() as tmp:
                handoff = Path(tmp) / 'handoff.json'
                handoff.write_text(json.dumps(cached), encoding='utf-8')
                workbook = Path(tmp) / 'source.xlsx'
                workbook.write_bytes(b'fixture')
                with mock.patch.object(extractor, 'extract', return_value={'fresh': True}) as extract, \
                     mock.patch.object(extractor, 'sha256_file', return_value='0' * 64), \
                     mock.patch.object(moneyball_core, 'dump_atomic') as dump:
                    self.assertEqual(builder.ensure_inputs(str(handoff), str(workbook)), str(handoff))
                extract.assert_called_once_with(str(workbook.resolve()))
                dump.assert_called_once()
                self.assertEqual(dump.call_args.args[0], {'fresh': True})


class PageDataRefusal(unittest.TestCase):
    def test_a_page_without_page_data_names_the_page(self):
        module = clean_workspace()
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / 'plain.html').write_text('<html><script>var x=1</script></html>', encoding='utf-8')
            (Path(tmp) / 'data.html').write_text('<script type="application/json" id="page-data">{"a": 1}</script>',
                                                 encoding='utf-8')
            with mock.patch.object(module, 'ROOT', Path(tmp)):
                with self.assertRaisesRegex(ValueError, r'^plain\.html: no page-data script$'):
                    module.page_data('plain.html')
                self.assertEqual(module.page_data('data.html'), {'a': 1})


if __name__ == '__main__':
    unittest.main()
