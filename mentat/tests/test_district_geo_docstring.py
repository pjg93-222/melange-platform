"""Lane B's loose end 8: district_geo's module docstring states the cycle-to-map
mapping the code actually draws (index.json), which since Paul's 2026-09-17
correction puts cycle 2022 on the 118th map, not the 116th.
"""
import json
from pathlib import Path
import re
import sys
import unittest

APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))
import district_geo  # noqa: E402


def stated_mapping(doc):
    """{'2026': '119', ...} from the first paragraph under CYCLE TO VINTAGE."""
    paragraph = doc.split('CYCLE TO VINTAGE', 1)[1].split('\n\n', 2)[1]
    return dict(re.findall(r'(\d{4}) -> the (\d{3})th', paragraph))


class DocstringMatchesTheShippedIndex(unittest.TestCase):
    def setUp(self):
        self.shipped = json.loads((APP / 'geo' / 'index.json').read_text(encoding='utf-8'))['cycles']

    def test_the_docstring_states_the_mapping_index_json_draws(self):
        self.assertEqual(self.shipped['2022'], '118')
        self.assertEqual(stated_mapping(district_geo.__doc__), self.shipped)
        self.assertNotRegex(district_geo.__doc__, r'2022 -> the 116th')

    def test_negative_control_the_old_paragraph_is_refused(self):
        old = ('X\n\nCYCLE TO VINTAGE (S6_DESIGN ruling 9, and the open question under it)\n\n'
               '  2026 -> the 119th map, 2024 -> the 118th, 2022 -> the 116th (= 117th; both\n'
               '  Congresses sat on the same 2011 lines).\n\n  * more\n')
        self.assertNotEqual(stated_mapping(old), self.shipped)


if __name__ == '__main__':
    unittest.main()
