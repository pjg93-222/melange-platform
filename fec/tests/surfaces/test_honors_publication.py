import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from surfaces.honors_publication import mark_page, validate_pages


class PublicationTests(unittest.TestCase):
    def test_mixed_missing_and_duplicate_generations_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch('stance.honors_views.load_honors', return_value={'generation_id': 'generation1'}):
                for invalid in ('<html></html>', mark_page('<head></head>', 'generation2'),
                                mark_page(mark_page('<head></head>', 'generation1'), 'generation1')):
                    (root / 'page.html').write_text(invalid, encoding='utf-8')
                    with self.assertRaises(ValueError):
                        validate_pages(root, ['page.html'])
                (root / 'page.html').write_text(mark_page('<head></head>', 'generation1'), encoding='utf-8')
                self.assertEqual(validate_pages(root, ['page.html']), 'generation1')

    def test_legacy_preserves_page_bytes(self):
        self.assertEqual(mark_page('<head></head>', None), '<head></head>')
        with patch('stance.honors_views.load_honors', return_value=None):
            self.assertIsNone(validate_pages('/nonexistent', ['legacy.html'], {'legacy.html': '<head></head>'}))
            with self.assertRaises(ValueError):
                validate_pages('/nonexistent', ['legacy.html'], {'legacy.html': mark_page('<head></head>', 'generation1')})

    def test_validation_checks_captured_content_not_later_disk_state(self):
        with patch('stance.honors_views.load_honors', return_value={'generation_id': 'new'}):
            with self.assertRaises(ValueError):
                validate_pages('/nonexistent', ['page.html'], {'page.html': mark_page('<head></head>', 'old')})


if __name__ == '__main__':
    unittest.main()
