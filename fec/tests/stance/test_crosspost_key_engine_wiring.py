"""N2 -- the ENGINE site is wired to the shared cross-post key.

Packet: audits/laundry_list_closeout_20260918, FINDINGS.md SS A A3;
receipts fixM/m4_crosspost_key_census.*.

build_media_blend.py writes the media blend into 'Press Release - Raw', the sheet the
workbook's SUMPRODUCT chain and press_matrix() both read, so its duplicate key is an
ENGINE input.  It is a separate implementation from the desk's (pandas here, openpyxl
there), so it gets its own wiring pin.  The producer cannot be imported -- import ==
execution: build_media_blend.py runs its whole body and overwrites the workbook -- so the
source is read as text, the pattern test_crosspost_scoring.py uses.

This file ships with the engine hook.  It asserts three things that must move together:
the key function, the dial that reverts it, and the two keys that must NOT move.
"""
import os
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import stance.media_rules as MR

BLEND_SOURCE = FEC / 'stance' / 'build_media_blend.py'


class EngineWiring(unittest.TestCase):

    def setUp(self):
        self.source = BLEND_SOURCE.read_text(encoding='utf-8')

    def test_the_duplicate_key_is_the_shared_function(self):
        self.assertIn("_event=(_member,MR.crosspost_key(r['text']))", self.source)
        self.assertNotIn("_event=(_member,MR.normalized_text(r['text']))", self.source)

    def test_the_stance_stack_join_still_uses_normalized_text(self):
        """_nrm keys the tweet_stance_final override; moving it would move stances."""
        self.assertIn("def _nrm(t): return MR.normalized_text(t)", self.source)

    def test_the_lifted_dedup_block_is_unchanged(self):
        """test_crosspost_scoring.py execs these exact lines; they are the D-034 rule."""
        self.assertIn("_dupe=_event in _seen", self.source)
        self.assertIn("elif not _event[1]: nlinkonly+=1; continue", self.source)
        self.assertIn("DEDUP=os.environ.get('CROSSPOST_DEDUP','0')=='1'", self.source)

    def test_the_shared_key_exists_with_the_documented_default(self):
        self.assertEqual(MR.CROSSPOST_KEY_CHARS,
                         os.environ.get('CROSSPOST_KEY_CHARS') or '0')
        long_text = 'x y ' * 80
        self.assertGreater(len(MR.crosspost_key(long_text)), 120)
        self.assertEqual(MR.crosspost_key(long_text, chars=120),
                         MR.normalized_text(long_text))

    def test_an_empty_key_still_means_link_only(self):
        """The engine reads `not _event[1]` as 'link-only post'; the key must agree."""
        self.assertEqual(MR.crosspost_key('https://only.example/link'), '')
        self.assertEqual(MR.crosspost_key(''), '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
