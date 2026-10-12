"""N2 -- CROSSPOST_KEY_CHARS: the cross-post duplicate key is the WHOLE statement.

Packet: audits/laundry_list_closeout_20260918, FINDINGS.md SS A A3;
receipts fixM/m4_crosspost_key_census.*.

``media_rules.normalized_text`` truncates at 120 characters, and both cross-post sites
used it as their duplicate key.  Measured on the shipped social corpus: 8,647 of 9,502
rows are longer than 120 normalised characters, so 445 of 1,434 duplicate groups (490
rows) were FALSE collisions -- different statements sharing a prefix.  At
CROSSPOST_DEDUP=0 no score is wrong, but up to 490 desk notes named the wrong statement,
and on the documented rollback (CROSSPOST_DEDUP=1) those rows would be dropped as
duplicates of posts they are not.

``normalized_text`` itself is NOT changed and is pinned here byte for byte: it is also
the join key against ``tweet_stance_final.csv`` (make_member_desk.py ~1720/1737,
build_media_blend.py ``_nrm``), and moving that join would move per-post stances.  The
duplicate key is a different job, so it gets its own pure function.

Synthetic fixtures only.  No producer is imported or executed; the wiring is asserted
against the shipped source text, the pattern test_crosspost_scoring.py uses.
"""
import importlib
import os
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import stance.media_rules as MR

DESK_SOURCE = FEC / 'surfaces' / 'make_member_desk.py'
RULES_SOURCE = FEC / 'stance' / 'media_rules.py'

# 120 normalised characters of shared prefix, then a divergence.  This is the shape of
# all 445 false-collision groups: a boilerplate lead-in, then the actual statement.
PREFIX = ('Today I am proud to announce that our bipartisan work with credit unions '
          'across the state has finally reached the point where members on both sides '
          'agree that ')
TAIL_A = 'the Interchange bill moves to the floor.'
TAIL_B = 'the CFPB rule is withdrawn for good.'
STATEMENT_A = PREFIX + TAIL_A
STATEMENT_B = PREFIX + TAIL_B


class NormalizedTextIsUnchanged(unittest.TestCase):
    """The tweet_stance_final join key. Pinned so N2 cannot move a single stance."""

    def test_normalized_text_still_truncates_at_120(self):
        self.assertEqual(len(MR.normalized_text(STATEMENT_A)), 120)
        self.assertEqual(MR.normalized_text(STATEMENT_A), MR.normalized_text(STATEMENT_B))

    def test_normalized_text_still_strips_urls_punctuation_and_case(self):
        self.assertEqual(MR.normalized_text('Hi, THERE! https://x.co/ab #tag'), 'hitheretag')
        self.assertEqual(MR.normalized_text('https://only.example/link'), '')
        self.assertEqual(MR.normalized_text(''), '')
        self.assertEqual(MR.normalized_text(None), '')
        self.assertEqual(MR.normalized_text(12345), '12345')


class CrosspostKey(unittest.TestCase):
    """The shared duplicate key, both dial positions, no data."""

    def test_the_dial_defaults_to_the_whole_statement(self):
        self.assertEqual(MR.CROSSPOST_KEY_CHARS, os.environ.get('CROSSPOST_KEY_CHARS') or '0')
        self.assertIn("os.environ.get('CROSSPOST_KEY_CHARS', '0')",
                      RULES_SOURCE.read_text(encoding='utf-8'))

    def test_the_default_key_separates_a_shared_120_char_prefix(self):
        self.assertNotEqual(MR.crosspost_key(STATEMENT_A), MR.crosspost_key(STATEMENT_B))

    def test_the_default_key_is_the_full_normalised_text(self):
        import re
        expected = re.sub(r'[^a-z0-9]', '',
                          re.sub(r'https?://\S+', '', STATEMENT_A.lower()))
        self.assertEqual(MR.crosspost_key(STATEMENT_A), expected)
        self.assertGreater(len(expected), 120)
        self.assertTrue(expected.startswith(MR.normalized_text(STATEMENT_A)))

    def test_dial_120_reproduces_the_legacy_key_byte_for_byte(self):
        for text in (STATEMENT_A, STATEMENT_B, 'short one', '', None, 12345,
                     'https://only.example/link', 'a' * 500):
            with self.subTest(text=str(text)[:30]):
                self.assertEqual(MR.crosspost_key(text, chars=120),
                                 MR.normalized_text(text))

    def test_the_same_statement_still_matches_itself(self):
        self.assertEqual(MR.crosspost_key(STATEMENT_A),
                         MR.crosspost_key(STATEMENT_A.upper() + '  '))

    def test_a_link_only_post_still_keys_empty_under_both_values(self):
        """Both call sites read emptiness as 'link-only post: no scorable text'."""
        for text in ('https://only.example/link', '   ', '', None, '#### ...'):
            with self.subTest(text=repr(text)):
                self.assertEqual(MR.crosspost_key(text), '')
                self.assertEqual(MR.crosspost_key(text, chars=120), '')
                self.assertEqual(bool(MR.crosspost_key(text)),
                                 bool(MR.normalized_text(text)))

    def test_emptiness_agrees_with_normalized_text_on_every_input(self):
        for text in (STATEMENT_A, 'x', 'https://a.b/c', '!!!', '0', None):
            with self.subTest(text=repr(text)):
                self.assertEqual(bool(MR.crosspost_key(text)),
                                 bool(MR.normalized_text(text)))

    def test_the_key_is_pure(self):
        self.assertEqual(MR.crosspost_key(STATEMENT_A), MR.crosspost_key(STATEMENT_A))
        self.assertEqual(MR.crosspost_key(STATEMENT_A, chars=0),
                         MR.crosspost_key(STATEMENT_A, chars='0'))
        self.assertEqual(MR.crosspost_key(STATEMENT_A, chars=120),
                         MR.crosspost_key(STATEMENT_A, chars='120'))

    def test_a_non_numeric_dial_value_fails_closed(self):
        with self.assertRaises(ValueError):
            MR.crosspost_key(STATEMENT_A, chars='full')

    def test_a_non_numeric_dial_value_fails_at_import(self):
        """Before any producer does I/O, not halfway through the corpus loop."""
        previous = os.environ.get('CROSSPOST_KEY_CHARS')
        try:
            os.environ['CROSSPOST_KEY_CHARS'] = 'full'
            with self.assertRaises(ValueError):
                importlib.reload(MR)
        finally:
            if previous is None:
                os.environ.pop('CROSSPOST_KEY_CHARS', None)
            else:
                os.environ['CROSSPOST_KEY_CHARS'] = previous
            importlib.reload(MR)

    def test_the_dial_is_read_from_the_environment(self):
        previous = os.environ.get('CROSSPOST_KEY_CHARS')
        try:
            os.environ['CROSSPOST_KEY_CHARS'] = '120'
            reloaded = importlib.reload(MR)
            self.assertEqual(reloaded.CROSSPOST_KEY_CHARS, '120')
            self.assertEqual(reloaded.crosspost_key(STATEMENT_A),
                             reloaded.normalized_text(STATEMENT_A))
        finally:
            if previous is None:
                os.environ.pop('CROSSPOST_KEY_CHARS', None)
            else:
                os.environ['CROSSPOST_KEY_CHARS'] = previous
            importlib.reload(MR)


class DeskWiring(unittest.TestCase):
    """The desk site: make_member_desk.py's `seen` key and the bluesky lookup into it."""

    def setUp(self):
        self.source = DESK_SOURCE.read_text(encoding='utf-8')

    def test_the_seen_key_uses_the_shared_function(self):
        self.assertIn("key = (nm, MR.crosspost_key(text)); duplicate = key in seen",
                      self.source)
        self.assertNotIn("key = (nm, MR.normalized_text(text)); duplicate = key in seen",
                         self.source)

    def test_the_bluesky_footprint_looks_up_the_same_key(self):
        """`seen` is keyed by the crosspost key, so its only other reader must match."""
        self.assertIn("if (nm, MR.crosspost_key(tx)) in seen:", self.source)
        self.assertNotIn("if (nm, MR.normalized_text(tx)) in seen:", self.source)

    def test_the_stance_stack_join_still_uses_normalized_text(self):
        """The 120-char truncation IS the tweet_stance_final join key: it must not move."""
        self.assertIn("stack[(sr.get('member') or '', MR.normalized_text(sr.get('text')))] = sr",
                      self.source)
        self.assertIn("hit = stack.get((str(sv(row, 'member') or ''), MR.normalized_text(text)))",
                      self.source)

    def test_the_crosspost_dedup_dial_default_is_untouched(self):
        """D-034 owns CROSSPOST_DEDUP's default; N2 changes the key, not the policy."""
        self.assertIn("CROSSPOST_DEDUP = os.environ.get('CROSSPOST_DEDUP', '0') == '1'",
                      self.source)

    def test_every_crosspost_key_call_in_the_desk_is_a_seen_key(self):
        """No third site drifted onto the new key by accident."""
        self.assertEqual(self.source.count('MR.crosspost_key('), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
