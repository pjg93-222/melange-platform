"""D4 -- CROSSPOST_DEDUP: every signed post is scored evidence.

Packet: audits/members_section_20260914, design-d2-d5.md SS D4;
evidence/q11-score-every-post.md.

Nothing here executes a producer. The desk rule is lifted out of
make_member_desk.py with ast (the pattern test_member_desk_honors.py uses), and
the engine rule is exec'd from the exact source lines of build_media_blend.py,
so both tests run the SHIPPED bytes rather than a paraphrase of them.
"""
import ast
import collections
import json
import os
from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))
import stance.media_rules as MR

DESK_SOURCE = FEC / 'surfaces' / 'make_member_desk.py'
BLEND_SOURCE = FEC / 'stance' / 'build_media_blend.py'
BUNDLE = ROOT / 'member_desk_bundle.json'
DUPE_LABEL = 'exact cross-post duplicate'
LINK_ONLY_LABEL = 'link-only post: no scorable text'
# FIX-UP 2026-09-17. This module used to pin the census measured on the
# 2026-09-16 bundle: 1,028 rows carrying DUPE_LABEL, decomposing into 895 newly
# scored + 121 neutral + 12 link-only repeats, with 7 link-only firsts lost.
# Every one of those numbers was read out of member_desk_bundle.json, which the
# chain rewrites on every run -- and D4 then SHIPPED, so the chain republished
# the bundle under CROSSPOST_DEDUP=0 and the label those tests counted is now
# carried by exactly ZERO rows. All seven tests that read it went red without a
# line of shipped code changing: the same defect commit 4dc148fc1 retired from
# test_per_area_board.py (evidence/board-tests-and-order.md).
#
# What is asserted now, from the same rows, on both dial positions:
#   * CROSSPOST_DEDUP=0 (shipped): no row carries DUPE_LABEL, and every social
#     row reproduces social_scoring(..., dedup=False) with `duplicate`
#     RECOMPUTED from the bundle's own rows.
#   * CROSSPOST_DEDUP=1 (revert): the rule labels exactly n-1 rows per key --
#     the exclusion set re-derived from today's rows, never a stored count --
#     and the rows that stop being scored are exactly the signed, texted
#     repeats.
# The censuses are printed as receipts. Measured 2026-09-17: 6,579 social rows,
# 1,025 repeats = 895 newly scored + 118 neutral + 12 link-only repeats, 7
# link-only firsts lost, 5,722 scored. The 2026-09-16 counts were 1,028/121.

# --- the desk rule, lifted without importing the desk's data producers -------
_TREE = ast.parse(DESK_SOURCE.read_text(encoding='utf-8'))
_NODES = [n for n in _TREE.body
          if (isinstance(n, ast.FunctionDef) and n.name == 'social_scoring')
          or (isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and t.id == 'CROSSPOST_DEDUP'
                      for t in n.targets))]
NAMESPACE = {'os': os}
exec(compile(ast.Module(body=_NODES, type_ignores=[]), str(DESK_SOURCE), 'exec'),
     NAMESPACE)
social_scoring = NAMESPACE['social_scoring']


def _blend_dedup_block():
    """The shipped dedup statements from build_media_blend.py, as runnable source.

    They are wrapped in a one-pass loop so their `continue` keeps its meaning --
    skip the rest of this row -- and a trailing marker records the rows that
    reach the scoring code below them in the producer.
    """
    lines = BLEND_SOURCE.read_text(encoding='utf-8').splitlines()
    start = next(i for i, line in enumerate(lines)
                 if line.strip().startswith('_dupe=_event in _seen'))
    end = next(i for i, line in enumerate(lines)
               if line.strip().startswith('elif not _event[1]'))
    body = [line[4:] for line in lines[start:end + 1]] + ['_kept=True']
    return 'for _row_pass in (0,):\n' + ''.join('    %s\n' % line for line in body)


BLEND_SRC = _blend_dedup_block()
BLEND_BLOCK = compile(BLEND_SRC, str(BLEND_SOURCE), 'exec')


def blend_retained(events, dedup):
    """Run the shipped engine rule over (member, normalized_text) events."""
    state = {'_seen': set(), 'ndupe': 0, 'nlinkonly': 0, 'DEDUP': dedup}
    kept = []
    for event in events:
        state['_event'] = event
        state['_kept'] = False
        exec(BLEND_BLOCK, state)
        if state['_kept']:
            kept.append(event)
    return kept, state


_BUNDLE_CACHE = {}


def bundle():
    if 'doc' not in _BUNDLE_CACHE:
        with open(BUNDLE, encoding='utf-8') as handle:
            _BUNDLE_CACHE['doc'] = json.load(handle)
    return _BUNDLE_CACHE['doc']


def social_rows(member):
    return [a for a in member.get('articles') or [] if a.get('source') == 'social']


def recomputed_social_rows(member):
    """One member's social rows with the producer's own `duplicate` RECOMPUTED.

    make_member_desk.py:1754 computes ``duplicate = (name, normalized_text) in
    seen`` while it walks the member's rows and appends them to the bundle in
    that same order, so the first row under a key is the original and the rest
    are its cross-posts. Re-deriving the flag here is what makes these tests
    independent of the dial position that produced the bundle: under the shipped
    default no row carries DUPE_LABEL at all, so reading the flag back off the
    artifact is reading a snapshot of the previous rule.

    Yields (article, normalized_key, duplicate, first_post).
    """
    seen = {}
    out = []
    for art in social_rows(member):
        key = MR.normalized_text(art.get('title'))
        duplicate = key in seen
        if not duplicate:
            seen[key] = (art.get('rel') or 'social', art.get('date') or '')
        out.append((art, key, duplicate, seen[key]))
    return out


def roster_social_rows():
    for member in bundle()['members']:
        for row in recomputed_social_rows(member):
            yield (member,) + row


needs_bundle = unittest.skipUnless(
    BUNDLE.exists(), 'member_desk_bundle.json is not in this tree')


class DeskRule(unittest.TestCase):
    """The rule itself, both dial positions, no data."""

    def test_shipped_default_is_score_every_post(self):
        source = DESK_SOURCE.read_text(encoding='utf-8')
        self.assertIn("CROSSPOST_DEDUP = os.environ.get('CROSSPOST_DEDUP', '0') == '1'",
                      source)
        self.assertEqual(NAMESPACE['CROSSPOST_DEDUP'],
                         os.environ.get('CROSSPOST_DEDUP', '0') == '1')

    def test_dial_one_reproduces_the_retired_rule_exactly(self):
        for sign in (-1, 0, 1):
            for text in ('somewords', ''):
                for duplicate in (False, True):
                    with self.subTest(sign=sign, text=bool(text), dup=duplicate):
                        self.assertEqual(
                            social_scoring(sign, text, duplicate,
                                           ('x', '2025-01-01'), dedup=True),
                            (bool(sign and not duplicate),
                             (DUPE_LABEL if duplicate
                              else ('' if sign else 'neutral')),
                             ''))

    def test_signed_duplicate_is_scored_and_carries_the_note(self):
        scored, exclusion, also = social_scoring(
            -1, 'somewords', True, ('facebook', '2025-03-04'), dedup=False)
        self.assertTrue(scored)
        self.assertEqual(exclusion, '')
        self.assertEqual(also, 'also posted on facebook 2025-03-04')

    def test_the_retired_label_is_gone_under_the_default(self):
        labels = {social_scoring(sign, text, dup, ('x', '2025-01-01'),
                                 dedup=False)[1]
                  for sign in (-1, 0, 1) for text in ('somewords', '')
                  for dup in (False, True)}
        self.assertNotIn(DUPE_LABEL, labels)
        self.assertEqual(labels, {'', 'neutral', LINK_ONLY_LABEL})

    def test_link_only_post_is_never_scored(self):
        for duplicate in (False, True):
            scored, exclusion, also = social_scoring(
                1, '', duplicate, ('x', '2025-01-01'), dedup=False)
            self.assertFalse(scored)
            self.assertEqual(exclusion, LINK_ONLY_LABEL)
            self.assertEqual(also, '')

    def test_first_post_of_a_key_carries_no_note(self):
        self.assertEqual(
            social_scoring(1, 'somewords', False, ('x', '2025-01-01'),
                           dedup=False),
            (True, '', ''))

    def test_note_survives_a_missing_platform_or_date(self):
        self.assertEqual(
            social_scoring(1, 'somewords', True, ('', ''), dedup=False)[2],
            'also posted on social')

    def test_the_template_renders_the_note(self):
        tpl = (FEC / 'surfaces' / 'templates' / 'make_member_desk_00.tpl'
               ).read_text(encoding='utf-8')
        self.assertIn("${a.also?' · '+esc(a.also):''}", tpl)
        # D5's injection anchors must survive any edit made in this file.
        self.assertEqual(tpl.count('function openMember(i){'), 1)
        self.assertEqual(tpl.count('  ${conductCard(m)}\n  <div class=gen>'), 1)


class EngineRule(unittest.TestCase):
    """build_media_blend.py's own, separate dedup -- the engine input."""

    def test_dial_one_drops_every_repeat(self):
        events = [('A', 'one'), ('A', 'one'), ('A', 'two'), ('B', 'one')]
        kept, state = blend_retained(events, dedup=True)
        self.assertEqual(kept, [('A', 'one'), ('A', 'two'), ('B', 'one')])
        self.assertEqual(state['ndupe'], 1)

    def test_default_retains_repeats_and_still_counts_them(self):
        events = [('A', 'one'), ('A', 'one'), ('A', 'two')]
        kept, state = blend_retained(events, dedup=False)
        self.assertEqual(kept, events)
        self.assertEqual(state['ndupe'], 1)
        self.assertEqual(state['nlinkonly'], 0)

    def test_default_skips_link_only_rows(self):
        events = [('A', ''), ('A', ''), ('A', 'one')]
        kept, state = blend_retained(events, dedup=False)
        self.assertEqual(kept, [('A', 'one')])
        self.assertEqual(state['nlinkonly'], 2)
        self.assertEqual(state['ndupe'], 1)

    def test_dial_one_keeps_the_first_link_only_row(self):
        """The revert switch restores the accident it replaced, exactly."""
        events = [('A', ''), ('A', ''), ('A', 'one')]
        kept, state = blend_retained(events, dedup=True)
        self.assertEqual(kept, [('A', ''), ('A', 'one')])
        self.assertEqual(state['nlinkonly'], 0)

    def test_dial_is_off_by_default_in_the_producer(self):
        text = BLEND_SOURCE.read_text(encoding='utf-8')
        self.assertIn("DEDUP=os.environ.get('CROSSPOST_DEDUP','0')=='1'", text)
        self.assertNotIn('exact cross-post duplicates removed |', text)
        self.assertIn('link-only posts skipped', text)


class PressSheetProducer(unittest.TestCase):
    """Which producer of 'Press Release - Raw' is live (design SS D4.c, RISK 5)."""

    def test_the_chain_runs_build_media_blend_and_not_apply_press_dedup(self):
        chain = (FEC / 'run_pipeline.py').read_text(encoding='utf-8')
        self.assertIn("('build_media_blend.py',[])", chain)
        self.assertNotIn('apply_press_dedup', chain)

    def test_apply_press_dedup_has_no_caller_in_the_tree(self):
        hits = []
        for path in FEC.rglob('*.py'):
            parts = set(path.parts)
            if path.name == 'apply_press_dedup.py' or 'tests' in parts:
                continue
            if any(p.startswith('_quarantine') for p in path.parts):
                continue
            if 'apply_press_dedup' in path.read_text(encoding='utf-8', errors='replace'):
                hits.append(str(path))
        self.assertEqual(hits, [])


@needs_bundle
class LiveBundle(unittest.TestCase):
    """The published bundle is the evidence; the rule is applied to its rows."""

    def test_dial_one_excludes_exactly_the_recomputed_repeats(self):
        """The revert switch's exclusion set, re-derived from today's rows.

        n rows under one key must produce exactly n-1 DUPE_LABEL rows when the
        rule runs at dedup=True. This is the census the retired
        test_duplicate_flags_are_the_key_census tried to make by COUNTING the
        stored label; the stored label is gone under the shipped dial, so the
        set is recomputed and the rule is what labels it.
        """
        keys = labelled = 0
        for member in bundle()['members']:
            groups = collections.defaultdict(list)
            for art, key, duplicate, first in recomputed_social_rows(member):
                groups[key].append((art, duplicate, first))
            for key, rows in groups.items():
                flagged = sum(
                    1 for art, duplicate, first in rows
                    if social_scoring(art.get('pol'), key, duplicate, first,
                                      dedup=True)[1] == DUPE_LABEL)
                self.assertEqual(flagged, len(rows) - 1,
                                 '%s key %r' % (member['name'], key[:40]))
                keys += 1
                labelled += flagged
        self.assertGreater(keys, 1000)
        self.assertGreater(labelled, 0)
        print('dial-1 exclusion receipt: %d repeats over %d keys'
              % (labelled, keys))

    def test_the_published_bundle_is_the_shipped_rule_on_its_own_rows(self):
        """Every social row reproduces social_scoring(..., dedup=False) exactly.

        Replaces test_dial_one_reproduces_every_live_social_row, which asserted
        the bundle was built at dedup=True. It no longer is: D4 shipped and the
        chain republished under the default.
        """
        checked = 0
        for member, art, key, duplicate, first in roster_social_rows():
            self.assertEqual(
                social_scoring(art.get('pol'), key, duplicate, first,
                               dedup=False)[:2],
                (bool(art.get('scored')), art.get('exclusion')),
                '%s: %r' % (member['name'], (art.get('title') or '')[:60]))
            # The D4 claim in one line: a signed post with scorable text is
            # scored evidence, whether or not it is a cross-post.
            self.assertEqual(bool(art.get('scored')),
                             bool(art.get('pol')) and bool(key))
            checked += 1
        self.assertGreater(checked, 0)
        print('shipped-rule receipt: %d social rows, %d scored'
              % (checked, sum(1 for m in bundle()['members']
                              for a in social_rows(m) if a.get('scored'))))

    def test_the_retired_label_is_absent_from_the_published_bundle(self):
        """CROSSPOST_DEDUP=0 ships, so no row may still carry DUPE_LABEL.

        Replaces the 1,028 pin, which counted the PREVIOUS bundle's rows. The
        repeat census itself is a collection number that moves with every
        refresh, so it is re-derived and printed, never pinned.
        """
        labels = collections.Counter(
            art.get('exclusion') for member in bundle()['members']
            for art in social_rows(member))
        self.assertEqual(labels[DUPE_LABEL], 0)
        self.assertEqual(set(labels), {'', 'neutral', LINK_ONLY_LABEL})
        repeats = sum(1 for _m, _a, _k, duplicate, _f in roster_social_rows()
                      if duplicate)
        self.assertGreater(repeats, 0)
        print('repeat receipt: %d cross-post repeats over %d social rows '
              '(1,028 on the pre-D4 bundle)' % (repeats, sum(labels.values())))

    def test_the_also_note_marks_cross_posts_and_never_the_first_post(self):
        """The display note is the only trace D4 leaves on a repeat.

        Order-independent: a note may only sit on a scored, signed, texted row
        inside a multi-row key, and no key may have every one of its rows
        annotated -- the first post is never a cross-post of itself.
        """
        annotated = 0
        for member in bundle()['members']:
            groups = collections.defaultdict(list)
            for art, key, _dup, _first in recomputed_social_rows(member):
                groups[key].append(art)
            for key, rows in groups.items():
                noted = [a for a in rows if a.get('also')]
                self.assertLess(len(noted), max(len(rows), 2),
                                '%s key %r' % (member['name'], key[:40]))
                for art in noted:
                    self.assertTrue(art.get('scored'))
                    self.assertEqual(art.get('exclusion'), '')
                    self.assertIn(art.get('pol'), (1, -1))
                    self.assertTrue(key)
                    self.assertGreater(len(rows), 1)
                annotated += len(noted)
        self.assertGreater(annotated, 0)
        print('also-note receipt: %d annotated cross-posts' % annotated)

    def test_link_only_census_is_nineteen_posts_across_seven_members(self):
        per_member = collections.Counter()
        for member in bundle()['members']:
            for art in social_rows(member):
                if not MR.normalized_text(art.get('title')):
                    per_member[member['name']] += 1
        self.assertEqual(sum(per_member.values()), 19)
        self.assertEqual(len(per_member), 7)
        self.assertEqual(per_member['Elizabeth Warren'], 12)

    def test_link_only_posts_stay_unscored_under_the_default(self):
        for member in bundle()['members']:
            for art in social_rows(member):
                if MR.normalized_text(art.get('title')):
                    continue
                scored, exclusion, _ = social_scoring(
                    art.get('pol'), '', art.get('exclusion') == DUPE_LABEL,
                    None, dedup=False)
                self.assertFalse(scored, member['name'])
                self.assertEqual(exclusion, LINK_ONLY_LABEL)

    def test_davis_gains_evidence_from_the_shipped_dial(self):
        """Davis's published scored set IS the shipped rule on his own rows, and
        the revert switch strictly removes evidence from him.

        Replaces the 12 -> 17 / 0.8235 pin. 17 and 0.8235 were measured on the
        POST-change bundle while 12 was the PRE-change count read out of the
        same file; the chain has since republished, so 12 no longer exists on
        disk. Both counts are recomputed here and printed as receipts.
        """
        davis = next(m for m in bundle()['members']
                     if m['name'] == 'Donald Davis')
        published = [a for a in davis['articles'] if a.get('scored')]
        shipped, reverted = [], []
        non_social = [a for a in davis['articles']
                      if a.get('source') != 'social' and a.get('scored')]
        for art, key, duplicate, first in recomputed_social_rows(davis):
            if social_scoring(art.get('pol'), key, duplicate, first,
                              dedup=False)[0]:
                shipped.append(art)
            if social_scoring(art.get('pol'), key, duplicate, first,
                              dedup=True)[0]:
                reverted.append(art)
        self.assertEqual(sorted(id(a) for a in shipped + non_social),
                         sorted(id(a) for a in published))
        self.assertGreater(len(shipped), len(reverted))
        pos = sum(1 for a in published if a.get('pol') == 1)
        neg = sum(1 for a in published if a.get('pol') == -1)
        net = (pos - neg) / len(published)
        self.assertGreater(net, 0)
        print('Davis receipt: %d scored today (%d social + %d other), %d under '
              'CROSSPOST_DEDUP=1, net %.4f'
              % (len(published), len(shipped), len(non_social),
                 len(reverted) + len(non_social), net))

    def test_every_repeat_keeps_its_own_sign(self):
        """No sign is borrowed from a twin: pol is the row's own, unchanged.

        The rows are the RECOMPUTED repeats, not the ones the retired rule
        happened to label on an older bundle.
        """
        checked = 0
        for member, art, key, duplicate, first in roster_social_rows():
            if not duplicate:
                continue
            scored, exclusion, _also = social_scoring(
                art.get('pol'), key, duplicate, first, dedup=False)
            self.assertEqual(scored, bool(art.get('pol')) and bool(key))
            self.assertEqual(scored, bool(art.get('scored')))
            if scored:
                self.assertEqual(exclusion, '')
                self.assertIn(art.get('pol'), (1, -1))
                checked += 1
        self.assertGreater(checked, 0)
        print('own-sign receipt: %d signed cross-post repeats scored' % checked)

    def test_the_repeats_decompose_exactly(self):
        """Every repeat is accounted for: scored, neutral, or link-only -- and
        the scored share is exactly what the revert switch would take away."""
        newly = neutral = link_only = repeats = 0
        lost = 0
        for _member, art, key, duplicate, first in roster_social_rows():
            if not duplicate:
                continue
            repeats += 1
            if not art.get('pol'):
                neutral += 1
            elif not key:
                link_only += 1
            else:
                newly += 1
            if art.get('scored') and not social_scoring(
                    art.get('pol'), key, duplicate, first, dedup=True)[0]:
                lost += 1
        self.assertEqual(newly + neutral + link_only, repeats)
        self.assertEqual(lost, newly)
        self.assertGreater(newly, 0)
        print('decomposition receipt: %d repeats = %d scored + %d neutral + %d '
              'link-only (2026-09-16: 1028 = 895 + 121 + 12)'
              % (repeats, newly, neutral, link_only))

    def test_the_dial_moves_evidence_both_ways_by_its_own_census(self):
        """Flipping to CROSSPOST_DEDUP=1 removes exactly the signed, texted
        repeats and restores exactly the signed link-only firsts -- the one
        direction in which D4 removes evidence, stated as a policy in
        make_member_desk.py rather than measured off a bundle."""
        shipped = reverted = lost = gained = link_only_firsts = 0
        for _member, art, key, duplicate, first in roster_social_rows():
            live = bool(art.get('scored'))
            revert = social_scoring(art.get('pol'), key, duplicate, first,
                                    dedup=True)[0]
            shipped += live
            reverted += bool(revert)
            lost += live and not revert
            gained += bool(revert) and not live
            if not key and not duplicate and art.get('pol'):
                link_only_firsts += 1
        self.assertEqual(gained, link_only_firsts)
        self.assertEqual(shipped - reverted, lost - gained)
        self.assertGreater(lost, 0)
        self.assertGreater(gained, 0)
        print('dial receipt: %d scored shipped, %d under dial 1 (-%d +%d)'
              % (shipped, reverted, lost, gained))


if __name__ == '__main__':
    unittest.main(verbosity=2)
