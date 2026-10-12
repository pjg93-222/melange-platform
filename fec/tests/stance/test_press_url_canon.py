"""A5 -- PRESS_URL_CANON: one release ingested under two URL forms counts once.

Packet: audits/laundry_list_closeout_20260918, FINDINGS.md SS A A5;
receipts fixM/m1_url_form_classes.*, fixM/m2_canon_candidates.*,
fixM/m3_press_url_canon_effect.*.

Measured on the shipped corpus: ALL 12 surviving (member, title, date) groups differ by
PATH ALIAS alone -- a CMS section prefix, an inserted date path, a section rename, or a
Drupal duplicate-slug marker.  No scheme/www./trailing-slash/case/query/fragment rule
reaches any of them, so the extra rules are path rules and they live in
``release_url_key``, which only ``tone_turn_key`` calls.

``canonical_url`` itself is deliberately NOT strengthened and is pinned here byte for
byte: ``api_press_stance.csv`` is keyed on the RAW url, so folding the path into
``canonical_url`` drops the press router's PRIMARY basis from 627 api-target hits to 93
(receipt m2).  The full-text body map, the stack-label map, the collector's own turn key
and ``press_baseline_fold``'s pinned divergence all key on ``canonical_url`` too.

Synthetic fixtures only.  Nothing under fec/ is executed; press_stream.py has no
top-level I/O (verified line by line) and is imported as a rules module.
"""
import importlib
import io
import os
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
import press_stream as PS

STREAM_SOURCE = FEC / 'press_stream.py'

# The four alias shapes measured in receipt m1, one representative pair each, plus the
# one group that is NOT a URL-form difference at all.
ALIAS_PAIRS = {
    'section prefix (news/)': (
        'https://www.britt.senate.gov/news/press-releases/u-s-senator-katie-britt-basel-iii-endgame-proposal-undermines-proven-strength-of-u-s-banking-sector',
        'https://www.britt.senate.gov/press-releases/u-s-senator-katie-britt-basel-iii-endgame-proposal-undermines-proven-strength-of-u-s-banking-sector'),
    'section prefix (media/)': (
        'https://www.husted.senate.gov/media/press-releases/husted-backs-bill-blocking-fed-from-issuing-a-central-bank-digital-currency',
        'https://www.husted.senate.gov/press-releases/husted-backs-bill-blocking-fed-from-issuing-a-central-bank-digital-currency'),
    'inserted date path': (
        'https://www.crapo.senate.gov/media/videos/watch/small-banks-and-credit-unions-need-relief',
        'https://www.crapo.senate.gov/news/videos/watch/2018/03/06/small-banks-and-credit-unions-need-relief'),
    'section rename': (
        'https://www.marshall.senate.gov/newsroom/op-eds/sen-marshall-in-washington-examiner-credit-card-swipe-fees-are-hurting-consumers/',
        'https://www.marshall.senate.gov/newsroom/press-releases/sen-marshall-in-washington-examiner-credit-card-swipe-fees-are-hurting-consumers/'),
    'duplicate-slug marker': (
        'https://www.americascreditunions.org/news-media/news/leagues-credit-unions-connect-policymakers-congress-returns-home-election#L000571',
        'https://www.americascreditunions.org/news-media/news/leagues-credit-unions-connect-policymakers-congress-returns-home-election-0#L000571'),
}
TRUNCATED_PAIR = (
    'https://www.britt.senate.gov/news/press-releases/u-s-senators-katie-britt-marc',
    'https://www.britt.senate.gov/press-releases/u-s-senators-katie-britt-marco-rubio-colleagues-demand-answers-on-irans-nuclear-program-violations-in-wake-of-barbaric-attacks-on-israel')

# Releases that must stay APART.  Every one is a real shape in the shipped corpus.
DISTINCT_URLS = (
    # Boozman: 18 different articles behind one query-string endpoint (receipt r6).
    'https://boozman.senate.gov/public/index.cfm/press-releases?ContentRecord_id=2DDC0FE6',
    'https://boozman.senate.gov/public/index.cfm/press-releases?ContentRecord_id=58C023CD',
    # Scalise: numbered editions of one recurring title.
    'https://scalise.house.gov/media/press-releases/heat-report-22',
    'https://scalise.house.gov/media/press-releases/heat-report-26',
    # Pettersen: an aspx endpoint with a numeric document id.
    'https://pettersen.house.gov/news/documentsingle.aspx?DocumentID=412',
    'https://pettersen.house.gov/news/documentsingle.aspx?DocumentID=1044',
    # Same slug, different HOST -- never one release.
    'https://a.house.gov/media/press-releases/a-very-long-descriptive-slug',
    'https://b.house.gov/media/press-releases/a-very-long-descriptive-slug',
    # Same slug, different member FRAGMENT -- the turn is per member.
    'https://americascreditunions.org/news-media/news/one-two-three#A000001',
    'https://americascreditunions.org/news-media/news/one-two-three#B000002',
)

TONE_HEADER = 'member,bioguide,date,title,url,categories,stance,press_value\n'


def write_tone(path, rows):
    with io.open(path, 'w', encoding='utf-8', newline='') as handle:
        handle.write(TONE_HEADER)
        for row in rows:
            handle.write(','.join('"%s"' % str(v).replace('"', '""') for v in row) + '\n')


class CanonicalUrlIsUnchanged(unittest.TestCase):
    """The shared canonicaliser keeps every join it feeds. Pinned, not assumed."""

    def test_canonical_url_still_keeps_the_whole_path(self):
        for label, (left, right) in ALIAS_PAIRS.items():
            with self.subTest(label):
                self.assertNotEqual(PS.canonical_url(left), PS.canonical_url(right))

    def test_canonical_url_rules_are_exactly_the_shipped_five(self):
        self.assertEqual(PS.canonical_url('  HTTP://WWW.Example.COM/A/B/  '),
                         'http://example.com/A/B')
        self.assertEqual(PS.canonical_url('https://example.com'), 'https://example.com/')
        self.assertEqual(PS.canonical_url('example.com/x'), 'https:example.com/x')
        self.assertEqual(PS.canonical_url('https://e.com/a?Q=B#C000127'),
                         'https://e.com/a?Q=B#C000127')
        self.assertEqual(PS.canonical_url('https://e.com/a?Q=B#C000127', keep_fragment=False),
                         'https://e.com/a?Q=B')
        self.assertEqual(PS.canonical_url(''), '')
        self.assertEqual(PS.canonical_url(None), '')

    def test_the_baseline_fold_divergence_pin_still_holds(self):
        from collect.press_baseline_fold import index_key
        url = 'https://www.alpha.senate.gov/news/one'
        self.assertNotEqual(index_key(url), PS.canonical_url(url))


class ReleaseUrlKey(unittest.TestCase):
    """The new key: release identity, used only by the ingest dedup."""

    def test_the_dial_defaults_to_the_new_behaviour(self):
        self.assertEqual(PS.PRESS_URL_CANON, os.environ.get('PRESS_URL_CANON') or '2')
        self.assertIn('os.environ.get("PRESS_URL_CANON", "2")',
                      STREAM_SOURCE.read_text(encoding='utf-8'))

    def test_dial_one_is_canonical_url_byte_for_byte(self):
        urls = (list(sum(ALIAS_PAIRS.values(), ())) + list(DISTINCT_URLS)
                + list(TRUNCATED_PAIR) + ['', None, 'not a url', 'https://e.com'])
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(PS.release_url_key(url, canon='1'),
                                 PS.canonical_url(url, keep_fragment=True))

    def test_every_measured_alias_shape_merges(self):
        for label, (left, right) in ALIAS_PAIRS.items():
            with self.subTest(label):
                self.assertEqual(PS.release_url_key(left), PS.release_url_key(right))

    def test_distinct_releases_never_merge(self):
        keys = [PS.release_url_key(u) for u in DISTINCT_URLS]
        self.assertEqual(len(set(keys)), len(DISTINCT_URLS), keys)

    def test_a_truncated_stored_url_is_left_alone(self):
        """Not a URL-form difference: one of the two stored urls is cut mid-slug."""
        left, right = TRUNCATED_PAIR
        self.assertNotEqual(PS.release_url_key(left), PS.release_url_key(right))

    def test_the_duplicate_slug_rule_only_strips_a_zero_marker(self):
        base = 'https://e.house.gov/media/press-releases/a-long-descriptive-slug'
        self.assertEqual(PS.release_url_key(base + '-0'), PS.release_url_key(base))
        for suffix in ('-1', '-22', '-2026'):
            with self.subTest(suffix):
                self.assertNotEqual(PS.release_url_key(base + suffix),
                                    PS.release_url_key(base))

    def test_a_short_path_segment_is_never_used_as_the_key(self):
        """A one-hyphen tail like 'press-releases' is a SECTION, not a release."""
        for url in ('https://e.senate.gov/public/index.cfm/press-releases',
                    'https://e.senate.gov/newsroom/press-releases',
                    'https://e.senate.gov/a/b/index.html',
                    'https://e.senate.gov/a/b/2026'):
            with self.subTest(url):
                self.assertEqual(PS.release_url_key(url),
                                 PS.canonical_url(url, keep_fragment=True))

    def test_the_query_and_fragment_survive_the_path_rule(self):
        key = PS.release_url_key('https://e.gov/a/b/one-two-three?ID=7#X000001')
        self.assertIn('?ID=7', key)
        self.assertIn('#X000001', key)

    def test_an_unrecognised_dial_value_fails_closed(self):
        """A misspelled dial must never fall through to either behaviour silently."""
        for bad in ('3', '0', 'legacy', 'release'):
            with self.subTest(bad):
                with self.assertRaises(ValueError):
                    PS.release_url_key('https://e.gov/a/one-two-three', canon=bad)

    def test_an_unrecognised_dial_value_fails_at_import(self):
        previous = os.environ.get('PRESS_URL_CANON')
        try:
            os.environ['PRESS_URL_CANON'] = 'release'
            with self.assertRaises(ValueError):
                importlib.reload(PS)
        finally:
            if previous is None:
                os.environ.pop('PRESS_URL_CANON', None)
            else:
                os.environ['PRESS_URL_CANON'] = previous
            importlib.reload(PS)

    def test_the_key_is_pure_and_deterministic(self):
        url = ALIAS_PAIRS['section rename'][0]
        self.assertEqual(PS.release_url_key(url), PS.release_url_key(url))
        self.assertEqual(PS.release_url_key(''), '')
        self.assertEqual(PS.release_url_key(None), '')

    def test_the_turn_key_uses_the_release_key(self):
        left, right = ALIAS_PAIRS['section prefix (news/)']
        self.assertEqual(PS.tone_turn_key({'url': left, 'bioguide': 'B001261'}),
                         PS.tone_turn_key({'url': right, 'bioguide': 'B001261'}))
        self.assertNotEqual(PS.tone_turn_key({'url': left, 'bioguide': 'B001261'}),
                            PS.tone_turn_key({'url': left, 'bioguide': 'H001098'}))


class IngestAndLedger(unittest.TestCase):
    """load_tone_rows: which row survives, and the accounting that must still close."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.mkdtemp(prefix='a5-tone-')
        self.base = os.path.join(self._tmp, 'base.csv')
        self.refresh = os.path.join(self._tmp, 'refresh.csv')
        left, right = ALIAS_PAIRS['section prefix (news/)']
        self.left, self.right = left, right
        write_tone(self.base, [
            ('Katie Britt', 'B001319', '2023-11-15', 'Basel III', left, 'Interchange',
             'support', '0.4'),
            ('Katie Britt', 'B001319', '2023-11-15', 'Basel III', right, 'Interchange',
             'support', '0.4'),
            # an exact (url, identity) collision -- collapsed today, must stay collapsed
            ('Katie Britt', 'B001319', '2023-11-15', 'Basel III', left, 'Interchange',
             'support', '0.4'),
            # a row with no url, and an OFFICIAL_MENTION row, are their own buckets
            ('Katie Britt', 'B001319', '2023-11-15', 'No url', '', 'Interchange',
             'support', '0.4'),
        ])
        write_tone(self.refresh, [
            ('Jon Husted', 'H001098', '2026-01-12', 'CBDC',
             ALIAS_PAIRS['section prefix (media/)'][0], 'Payments', 'support', '0.2'),
            ('Jon Husted', 'H001098', '2026-01-12', 'CBDC',
             ALIAS_PAIRS['section prefix (media/)'][1], 'Payments', 'support', '0.2'),
        ])

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_the_two_url_forms_ingest_as_one_row(self):
        rows = PS.load_tone_rows(base_path=self.base, refresh_path=self.refresh)
        self.assertEqual(len(rows), 2)          # Britt x1, Husted x1

    def test_dial_one_ingests_both_forms(self):
        ledger = {}
        rows = PS.load_tone_rows(base_path=self.base, refresh_path=self.refresh,
                                 ledger=ledger, canon='1')
        self.assertEqual(len(rows), 4)          # the shipped 2026-09-18 behaviour
        self.assertEqual(ledger['duplicate_url_form_collapsed'], 0)
        self.assertEqual(ledger['exact_url_turn_collapsed'], 1)

    def test_the_first_row_wins_which_is_the_historical_row(self):
        """press_stream's stated precedence: 'Historical rows win exact collisions.'"""
        rows = PS.load_tone_rows(base_path=self.base, refresh_path=self.refresh)
        britt = [r for r in rows if r['bioguide'] == 'B001319' and r['url']]
        self.assertEqual([r['url'] for r in britt], [self.left])

    def test_the_ledger_accounts_for_every_row_read(self):
        ledger = {}
        rows = PS.load_tone_rows(base_path=self.base, refresh_path=self.refresh,
                                 ledger=ledger)
        self.assertEqual(ledger['schema'], 'press-tone-ingest-dedup-v1')
        self.assertEqual(ledger['canon'], '2')
        self.assertEqual(ledger['rows_read'], 6)
        self.assertEqual(ledger['rows_returned'], len(rows))
        self.assertEqual(
            ledger['rows_read'],
            ledger['rows_returned'] + ledger['official_mention_skipped']
            + ledger['no_url_skipped'] + ledger['exact_url_turn_collapsed']
            + ledger['duplicate_url_form_collapsed'])
        self.assertEqual(ledger['exact_url_turn_collapsed'], 1)
        self.assertEqual(ledger['duplicate_url_form_collapsed'], 2)

    def test_a_merged_row_is_named_in_the_ledger_not_deleted(self):
        ledger = {}
        PS.load_tone_rows(base_path=self.base, refresh_path=self.refresh, ledger=ledger)
        merged = ledger['duplicate_url_form_rows']
        self.assertEqual(len(merged), 2)
        entry = next(m for m in merged if m['bioguide'] == 'B001319')
        self.assertEqual(entry['kept_url'], self.left)
        self.assertEqual(entry['dropped_url'], self.right)
        self.assertEqual(entry['title'], 'Basel III')
        self.assertEqual(entry['date'], '2023-11-15')
        # the input file is untouched: both forms are still there to be read again
        self.assertIn(self.right, io.open(self.base, encoding='utf-8').read())

    def test_a_shared_slug_on_another_date_is_a_different_release(self):
        """The release key nominates a CANDIDATE pair; it is not the verdict.  Drupal appends
        '-0' whenever a NEW node reuses an existing title, so a recurring headline republished
        on another day carries the same release key as the original.  Two rows are one release
        only when the member, the date AND the title agree -- which all 12 measured pairs do.
        Anything else is kept, and counted, never silently dropped."""
        base = os.path.join(self._tmp, 'base_conflict.csv')
        refresh = os.path.join(self._tmp, 'refresh_conflict.csv')
        first = 'https://www.britt.senate.gov/press-releases/statement-on-the-budget-vote'
        again = 'https://www.britt.senate.gov/press-releases/statement-on-the-budget-vote-0'
        retitled = 'https://www.britt.senate.gov/news/press-releases/statement-on-the-budget-vote'
        write_tone(base, [
            ('Katie Britt', 'B001319', '2023-03-01', 'Statement on the budget vote', first,
             'Interchange', 'support', '0.4'),
            # same slug, a year later: a different release that reuses the headline
            ('Katie Britt', 'B001319', '2024-03-05', 'Statement on the budget vote', again,
             'Interchange', 'support', '0.4'),
            # same slug and date, different title: not provably the same release either
            ('Katie Britt', 'B001319', '2023-03-01', 'A different headline entirely', retitled,
             'Interchange', 'support', '0.4'),
        ])
        alias = 'https://www.britt.senate.gov/media/press-releases/statement-on-the-budget-vote'
        write_tone(refresh, [
            # a fourth URL form with the same member, date and title as the FIRST row (case and
            # spacing aside): this one IS the same release, and must merge into that row even
            # though two other rows share its release key
            ('Katie Britt', 'B001319', '2023-03-01', 'statement on  the Budget vote', alias,
             'Interchange', 'support', '0.4'),
            # an exact (url, identity) repeat of a row that was KEPT as a conflict still
            # collapses exactly as it always did
            ('Katie Britt', 'B001319', '2024-03-05', 'Statement on the budget vote', again,
             'Interchange', 'support', '0.4'),
        ])
        ledger = {}
        rows = PS.load_tone_rows(base_path=base, refresh_path=refresh, ledger=ledger)
        self.assertEqual([r['url'] for r in rows], [first, again, retitled])
        self.assertEqual(ledger['release_key_conflict_kept'], 2)
        self.assertEqual(ledger['duplicate_url_form_collapsed'], 1)
        self.assertEqual(ledger['exact_url_turn_collapsed'], 1)
        self.assertEqual(ledger['duplicate_url_form_rows'][0]['kept_url'], first)
        self.assertEqual(ledger['duplicate_url_form_rows'][0]['dropped_url'], alias)
        # a kept row is a returned row, so the accounting identity is unchanged and closes
        self.assertEqual(
            ledger['rows_read'],
            ledger['rows_returned'] + ledger['official_mention_skipped']
            + ledger['no_url_skipped'] + ledger['exact_url_turn_collapsed']
            + ledger['duplicate_url_form_collapsed'])

    def test_the_ledger_is_also_reachable_without_the_out_parameter(self):
        PS.load_tone_rows(base_path=self.base, refresh_path=self.refresh)
        self.assertEqual(PS.LAST_TONE_DEDUP_LEDGER['rows_read'], 6)

    def test_the_dial_reads_the_environment_at_call_time(self):
        previous = os.environ.get('PRESS_URL_CANON')
        try:
            os.environ['PRESS_URL_CANON'] = '1'
            reloaded = importlib.reload(PS)
            self.assertEqual(reloaded.PRESS_URL_CANON, '1')
            self.assertEqual(len(reloaded.load_tone_rows(base_path=self.base,
                                                         refresh_path=self.refresh)), 4)
        finally:
            if previous is None:
                os.environ.pop('PRESS_URL_CANON', None)
            else:
                os.environ['PRESS_URL_CANON'] = previous
            importlib.reload(PS)


if __name__ == '__main__':
    unittest.main(verbosity=2)
