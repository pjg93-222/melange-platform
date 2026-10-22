# -*- coding: utf-8 -*-
"""fec/tools/live_patch.py -- the shared line-wise cutter every patch cutter now goes through.

Ruling 2026-09-19 ("Reconcile those loose ends and apply"): leave the live files alone and make
every patch cutter work line-wise, out of ONE shared helper module.  The census behind that
ruling found 300 of 2,502 tracked text files MIXED in the working tree while every blob is pure
LF, so a string-replacement cutter whose anchor spans a CRLF silently returns -1 on exactly the
files that need cutting, while an EOL-normalised hash still reports "no drift".  Test 3 below
reproduces that -1 and pins the fix.

What this suite pins: the LF-only line split (str.splitlines() also breaks on FF, VT, FS, GS,
RS, NEL, U+2028 and U+2029 and produced a patch git apply rejected -- test 1 pins the contrast),
the EOL census and its drift verdicts, per-line anchors that must hit exactly once, the majority
terminator ruling for newly inserted lines, the promise that an untouched line is never
rewritten, non-ASCII surviving as its own UTF-8 bytes (the DE-025 regression), and byte-exact
round trips proved by running `git apply` in a throwaway directory.

Synthetic byte fixtures only: no repository file is read, no artifact, no workbook, no network.
The proof tests run inside a fresh tempfile.mkdtemp() that is OUTSIDE every repository and skip
-- never fail -- when git is not on PATH.
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MODULE_PATH = os.path.normpath(
    os.path.join(_HERE, os.pardir, os.pardir, 'tools', 'live_patch.py'))


def _load_module_under_test():
    """Load by file path so the suite depends on neither sys.path nor the runner."""
    spec = importlib.util.spec_from_file_location('live_patch_under_test', _MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LP = _load_module_under_test()
GIT = shutil.which('git')

# ---------------------------------------------------------------- byte fixtures
PURE_CRLF = b'alpha\r\nbeta\r\ngamma\r\n'
PURE_LF = b'alpha\nbeta\ngamma\n'
# Line 4 ends with a BARE LF inside an otherwise CRLF file: the exact live shape that breaks a
# string-replacement anchor spanning lines 4 and 5.
MIXED = b'L1\r\nL2\r\nL3\r\nL4\nL5\r\nL6\r\n'
MIXED_AS_CRLF = b'L1\r\nL2\r\nL3\r\nL4\r\nL5\r\nL6\r\n'
MIXED_OTHER_CONTENT = b'L1\r\nL2\r\nL3\r\nL4\nL5\r\nL7\r\n'
CRCRLF = b'aaa\r\r\nbbb\r\n'
LONE_CR = b'aaa\rbbb\r\nccc\r\n'
NO_FINAL_EOL = b'alpha\r\nbeta\r\ngamma'
NO_EOL_AT_ALL = b'one single unterminated line'
EMPTY = b''
BOM_CRLF = b'\xef\xbb\xbfalpha\r\nbeta\r\n'
# UTF-16 keeps an LF as TWO bytes, so a byte-LF line model would cut these in half.  The census
# found 11 UTF-16 Task Scheduler XML files in this tree; LiveFile must refuse them outright.
UTF16LE_CRLF = b'\xff\xfe' + u'alpha\r\nbeta\r\n'.encode('utf-16-le')
UTF16BE_CRLF = b'\xfe\xff' + u'alpha\r\nbeta\r\n'.encode('utf-16-be')
TIE = b'a\r\nb\r\nc\nd\n'
LF_MAJORITY = b'a\r\nb\nc\nd\n'
# E7 (the lab case): eight LF-terminated lines, but line 3 carries a raw form feed and line 6 a
# raw U+2028.  str.splitlines() sees TEN lines here and cuts a patch git apply rejects.
E7 = (b'aaa\r\n'
      b'bbb\r\n'
      b'ccc\x0cddd\r\n'
      b'eee\r\n'
      b'fff\r\n'
      b'ggg\xe2\x80\xa8hhh\r\n'
      b'iii\r\n'
      b'jjj\r\n')

# A CRLF-majority mixed file with a SEVEN-line bare-LF run in the middle.  An insert deep inside
# that run, cut with eol='anchor', yields a hunk whose every old line ends with a bare LF -- the
# shape git applies by renormalising the WHOLE file to CRLF unless core.autocrlf is turned off.
LF_RUN_IN_CRLF = (b''.join(('head%02d\r\n' % n).encode('ascii') for n in range(14)) +
                  b''.join(('run%02d\n' % n).encode('ascii') for n in range(7)) +
                  b''.join(('tail%02d\r\n' % n).encode('ascii') for n in range(10)))

ALL_FIXTURES = (PURE_CRLF, PURE_LF, MIXED, MIXED_AS_CRLF, CRCRLF, LONE_CR, NO_FINAL_EOL,
                NO_EOL_AT_ALL, EMPTY, BOM_CRLF, TIE, LF_MAJORITY, E7, LF_RUN_IN_CRLF,
                UTF16LE_CRLF, UTF16BE_CRLF)


class TempMixin(unittest.TestCase):
    """Every byte this suite writes lands under one mkdtemp() and is removed again."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix='test_live_patch_')
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def write(self, name, data):
        path = os.path.join(self._tmp, name)
        with open(path, 'wb') as handle:
            handle.write(data)
        return path


# ------------------------------------------------------------------ 1. line model
class SplitLinesTests(unittest.TestCase):

    def test_round_trip_is_exact_for_every_shape(self):
        for data in ALL_FIXTURES:
            self.assertEqual(b''.join(LP.split_lines(data)), data, repr(data[:40]))

    def test_empty_input_has_no_lines(self):
        self.assertEqual(LP.split_lines(EMPTY), [])

    def test_line_counts(self):
        self.assertEqual(len(LP.split_lines(PURE_CRLF)), 3)
        self.assertEqual(len(LP.split_lines(PURE_LF)), 3)
        self.assertEqual(len(LP.split_lines(MIXED)), 6)
        self.assertEqual(len(LP.split_lines(NO_FINAL_EOL)), 3)
        self.assertEqual(len(LP.split_lines(NO_EOL_AT_ALL)), 1)

    def test_crcrlf_keeps_the_extra_cr_in_the_content(self):
        lines = LP.split_lines(CRCRLF)
        self.assertEqual(lines, [b'aaa\r\r\n', b'bbb\r\n'])
        self.assertEqual(LP.terminator(lines[0]), b'\r\n')
        self.assertEqual(LP.content(lines[0]), b'aaa\r')

    def test_a_lone_cr_never_splits_a_line(self):
        self.assertEqual(LP.split_lines(LONE_CR), [b'aaa\rbbb\r\n', b'ccc\r\n'])

    def test_last_line_without_a_terminator(self):
        lines = LP.split_lines(NO_FINAL_EOL)
        self.assertEqual(lines[-1], b'gamma')
        self.assertEqual(LP.terminator(lines[-1]), b'')
        self.assertEqual(LP.content(lines[-1]), b'gamma')

    def test_form_feed_and_u2028_do_not_split_lines(self):
        # The whole reason this module exists rather than str.splitlines().
        self.assertEqual(len(LP.split_lines(E7)), 8)
        self.assertEqual(len(E7.decode('utf-8').splitlines(keepends=True)), 10)
        self.assertEqual(LP.content(LP.split_lines(E7)[2]), b'ccc\x0cddd')
        self.assertEqual(LP.content(LP.split_lines(E7)[5]), b'ggg\xe2\x80\xa8hhh')

    def test_terminator_kinds(self):
        self.assertEqual(LP.terminator(b'x\r\n'), b'\r\n')
        self.assertEqual(LP.terminator(b'x\n'), b'\n')
        self.assertEqual(LP.terminator(b'x'), b'')
        self.assertEqual(LP.terminator(b'x\r'), b'')


# ------------------------------------------------------------------ 2. EOL census
class EolStatsTests(TempMixin):

    def test_pure_crlf(self):
        stats = LP.eol_stats(PURE_CRLF)
        self.assertEqual(stats['size'], len(PURE_CRLF))
        self.assertEqual(stats['bom'], 'none')
        self.assertEqual((stats['n_crlf'], stats['n_bare_lf'], stats['n_lone_cr']), (3, 0, 0))
        self.assertEqual(stats['n_crcrlf'], 0)
        self.assertTrue(stats['ends_with_eol'])
        self.assertEqual(stats['klass'], 'PURE_CRLF')

    def test_pure_lf(self):
        stats = LP.eol_stats(PURE_LF)
        self.assertEqual((stats['n_crlf'], stats['n_bare_lf'], stats['n_lone_cr']), (0, 3, 0))
        self.assertEqual(stats['klass'], 'PURE_LF')

    def test_mixed(self):
        stats = LP.eol_stats(MIXED)
        self.assertEqual((stats['n_crlf'], stats['n_bare_lf'], stats['n_lone_cr']), (5, 1, 0))
        self.assertEqual(stats['klass'], 'MIXED')

    def test_empty_and_no_eol(self):
        self.assertEqual(LP.eol_stats(EMPTY)['klass'], 'EMPTY')
        self.assertEqual(LP.eol_stats(EMPTY)['size'], 0)
        no_eol = LP.eol_stats(NO_EOL_AT_ALL)
        self.assertEqual(no_eol['klass'], 'NO_EOL')
        self.assertFalse(no_eol['ends_with_eol'])

    def test_cr_anomalies(self):
        crcrlf = LP.eol_stats(CRCRLF)
        self.assertEqual((crcrlf['n_crlf'], crcrlf['n_bare_lf'], crcrlf['n_lone_cr']), (2, 0, 1))
        self.assertEqual(crcrlf['n_crcrlf'], 1)
        self.assertEqual(crcrlf['klass'], 'CR_ANOMALY')
        lone = LP.eol_stats(LONE_CR)
        self.assertEqual((lone['n_crlf'], lone['n_bare_lf'], lone['n_lone_cr']), (2, 0, 1))
        self.assertEqual(lone['klass'], 'CR_ANOMALY')

    def test_no_final_eol_is_still_classified_by_its_terminators(self):
        stats = LP.eol_stats(NO_FINAL_EOL)
        self.assertEqual(stats['klass'], 'PURE_CRLF')
        self.assertFalse(stats['ends_with_eol'])

    def test_utf8_bom_with_crlf(self):
        stats = LP.eol_stats(BOM_CRLF)
        self.assertEqual(stats['bom'], 'utf8')
        self.assertEqual((stats['n_crlf'], stats['n_bare_lf']), (2, 0))
        self.assertEqual(stats['klass'], 'PURE_CRLF')

    def test_utf16_boms(self):
        self.assertEqual(LP.eol_stats(b'\xff\xfea\x00')['bom'], 'utf16le')
        self.assertEqual(LP.eol_stats(b'\xfe\xff\x00a')['bom'], 'utf16be')

    def test_format_stats_is_one_line(self):
        line = LP.format_stats(LP.eol_stats(MIXED))
        self.assertNotIn('\n', line)
        self.assertEqual(line, 'size=%d crlf=5 lf=1 lone_cr=0 class=MIXED' % len(MIXED))

    def test_drift_verdicts(self):
        self.assertEqual(LP.drift_report(MIXED, MIXED)['verdict'], 'IDENTICAL_BYTES')
        eol_only = LP.drift_report(MIXED, MIXED_AS_CRLF)
        self.assertEqual(eol_only['verdict'], 'EOL_ONLY')
        # The point of the report: the normalised hashes AGREE while the bytes do not.
        self.assertEqual(eol_only['normalised_a'], eol_only['normalised_b'])
        self.assertNotEqual(eol_only['raw_a'], eol_only['raw_b'])
        self.assertEqual(eol_only['stats_a']['klass'], 'MIXED')
        self.assertEqual(eol_only['stats_b']['klass'], 'PURE_CRLF')
        self.assertEqual(LP.drift_report(MIXED, MIXED_OTHER_CONTENT)['verdict'], 'CONTENT')

    def test_hashes(self):
        self.assertEqual(LP.normalised_sha256(MIXED), LP.normalised_sha256(MIXED_AS_CRLF))
        self.assertNotEqual(LP.raw_sha256(MIXED), LP.raw_sha256(MIXED_AS_CRLF))

    def _cli(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = LP.main(argv)
        return code, buffer.getvalue()

    def test_cli_drift_exit_codes(self):
        same_a = self.write('same_a.txt', MIXED)
        same_b = self.write('same_b.txt', MIXED)
        crlf = self.write('crlf.txt', MIXED_AS_CRLF)
        other = self.write('other.txt', MIXED_OTHER_CONTENT)
        code, text = self._cli(['drift', same_a, same_b])
        self.assertEqual(code, 0)
        self.assertIn('IDENTICAL_BYTES', text)
        code, text = self._cli(['drift', same_a, crlf])
        self.assertEqual(code, 1)
        self.assertIn('EOL_ONLY', text)
        code, text = self._cli(['drift', same_a, other])
        self.assertEqual(code, 2)
        self.assertIn('CONTENT', text)

    def test_cli_eol_prints_one_line_per_file(self):
        first = self.write('first.txt', MIXED)
        second = self.write('second.txt', PURE_LF)
        code, text = self._cli(['eol', first, second])
        self.assertEqual(code, 0)
        printed = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(len(printed), 2)
        self.assertIn('class=MIXED', printed[0])
        self.assertIn('class=PURE_LF', printed[1])
        self.assertIn(os.path.basename(first), printed[0])


# ------------------------------------------------------------------ 3-5. anchors
class AnchorTests(unittest.TestCase):

    def test_string_replacement_fails_where_the_line_anchor_succeeds(self):
        # THE REPRODUCED FAILURE.  Line 4 ends with a bare LF, line 5 with CRLF, so the obvious
        # two-line string anchor is simply not in the bytes -- .find() returns -1 and a cutter
        # that trusts it silently does nothing.
        self.assertEqual(MIXED.find(b'L4' + b'\r\n' + b'L5'), -1)
        live = LP.LiveFile('fec/fixture.py', MIXED)
        self.assertEqual(live.find([b'L4', b'L5']), [3])

    def test_one_anchor_matches_crlf_and_bare_lf_lines_alike(self):
        crlf_file = LP.LiveFile('fec/fixture.py', MIXED)
        lf_file = LP.LiveFile('fec/fixture.py', PURE_LF)
        self.assertEqual(crlf_file.find('L3'), [2])     # CRLF-terminated
        self.assertEqual(crlf_file.find('L4'), [3])     # bare-LF terminated
        self.assertEqual(lf_file.find('beta'), [1])     # bare-LF terminated, other file

    def test_str_and_bytes_anchors_agree(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        self.assertEqual(live.find('L4'), live.find(b'L4'))

    def test_exact_match_keeps_leading_whitespace_significant(self):
        data = b'    indented\r\nindented\r\n'
        live = LP.LiveFile('fec/fixture.py', data)
        self.assertEqual(live.find('    indented'), [0])
        self.assertEqual(live.find('indented'), [1])
        self.assertEqual(live.find('indented', contains=True), [0, 1])

    def test_contains_applies_to_every_line_of_a_multiline_anchor(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        self.assertEqual(live.find(['L4', 'L5'], contains=True), [3])
        self.assertEqual(live.find(['4', '5'], contains=True), [3])
        self.assertEqual(live.find(['L4', 'L6'], contains=True), [])

    def test_zero_hits_raise(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        with self.assertRaises(LP.AnchorError) as caught:
            live.insert_after('NOT PRESENT', ['# new'])
        message = str(caught.exception)
        self.assertIn('0 hits', message)
        self.assertIn('fec/fixture.py', message)
        self.assertIn('NOT PRESENT', message)

    def test_two_hits_raise_and_name_both_line_numbers(self):
        data = b'dup\r\nmiddle\r\ndup\r\n'
        live = LP.LiveFile('fec/fixture.py', data)
        with self.assertRaises(LP.AnchorError) as caught:
            live.insert_after('dup', ['# new'])
        message = str(caught.exception)
        self.assertIn('1', message)
        self.assertIn('3', message)
        self.assertEqual(caught.exception.hits, [0, 2])

    def test_anchor_error_truncates_a_long_anchor(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        with self.assertRaises(LP.AnchorError) as caught:
            live.replace('x' * 400, ['# new'])
        self.assertLess(len(str(caught.exception)), 300)


# ------------------------------------------------------------------ 6-8. cutting
class CutTests(unittest.TestCase):

    def test_majority_terminator_is_the_default(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)          # 5 CRLF, 1 bare LF
        live.insert_after('L4', ['# added'])                 # anchor line ends with a BARE LF
        self.assertIn(b'# added\r\n', live.after)
        self.assertEqual(LP.split_lines(live.after)[4], b'# added\r\n')

    def test_anchor_terminator_when_asked(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L4', ['# added'], eol='anchor')
        self.assertEqual(LP.split_lines(live.after)[4], b'# added\n')

    def test_anchor_policy_uses_the_last_anchor_line_for_insert_after(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after(['L3', 'L4'], ['# added'], eol='anchor')   # L4 ends bare LF
        self.assertEqual(LP.split_lines(live.after)[4], b'# added\n')

    def test_anchor_policy_uses_the_first_anchor_line_for_insert_before(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_before(['L4', 'L5'], ['# added'], eol='anchor')  # L4 ends bare LF
        self.assertEqual(LP.split_lines(live.after)[3], b'# added\n')

    def test_lf_majority_file_gets_lf(self):
        live = LP.LiveFile('fec/fixture.py', LF_MAJORITY)     # 1 CRLF, 3 bare LF
        live.insert_after('a', ['# added'])                   # anchor line ends CRLF
        self.assertEqual(LP.split_lines(live.after)[1], b'# added\n')

    def test_exact_tie_goes_to_crlf(self):
        live = LP.LiveFile('fec/fixture.py', TIE)             # 2 CRLF, 2 bare LF
        live.insert_after('c', ['# added'])
        self.assertEqual(LP.split_lines(live.after)[3], b'# added\r\n')

    def test_file_with_no_terminated_line_gets_crlf(self):
        live = LP.LiveFile('fec/fixture.py', NO_EOL_AT_ALL)
        live.insert_before(NO_EOL_AT_ALL, ['# added'])
        self.assertEqual(LP.split_lines(live.after)[0], b'# added\r\n')

    def test_explicit_terminator_is_honoured(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L2', ['# added'], eol='\n')
        self.assertEqual(LP.split_lines(live.after)[2], b'# added\n')
        other = LP.LiveFile('fec/fixture.py', PURE_LF)
        other.insert_after('beta', ['# added'], eol=b'\r\n')
        self.assertEqual(LP.split_lines(other.after)[2], b'# added\r\n')

    def test_a_bad_explicit_terminator_is_refused(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        with self.assertRaises(ValueError):
            live.insert_after('L2', ['# added'], eol='\r')

    def test_untouched_lines_are_never_rewritten(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L4', ['# one', '# two'])
        after_lines = LP.split_lines(live.after)
        del after_lines[4:6]                      # remove exactly the two inserted lines
        self.assertEqual(after_lines, LP.split_lines(MIXED))
        self.assertEqual(LP.eol_stats(live.after)['klass'], 'MIXED')
        self.assertEqual(LP.eol_stats(live.after)['n_bare_lf'], 1)

    def test_changed_flag(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        self.assertFalse(live.changed)
        self.assertEqual(live.after, MIXED)
        live.insert_after('L4', ['# added'])
        self.assertTrue(live.changed)

    def test_replace_swaps_exactly_the_anchor_lines(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.replace(['L3', 'L4'], ['# only'])
        self.assertEqual(live.after, b'L1\r\nL2\r\n# only\r\nL5\r\nL6\r\n')

    def test_delete_removes_exactly_the_anchor_lines(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.delete('L4')
        self.assertEqual(live.after, b'L1\r\nL2\r\nL3\r\nL5\r\nL6\r\n')
        multi = LP.LiveFile('fec/fixture.py', MIXED)
        multi.delete(['L3', 'L4'])
        self.assertEqual(multi.after, b'L1\r\nL2\r\nL5\r\nL6\r\n')

    def test_insert_before_places_above_the_anchor(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_before('L5', ['# above'])
        self.assertEqual(live.after, b'L1\r\nL2\r\nL3\r\nL4\n# above\r\nL5\r\nL6\r\n')

    def test_a_terminator_inside_a_new_line_is_refused(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        with self.assertRaises(ValueError):
            live.insert_after('L4', ['# one\n# two'])
        with self.assertRaises(ValueError):
            live.insert_after('L4', [b'# one\r\n'])
        with self.assertRaises(ValueError):
            live.insert_after('L4', ['# one\r'])
        self.assertFalse(live.changed)

    def test_final_eol_absence_survives_a_replace_of_the_last_line(self):
        live = LP.LiveFile('fec/fixture.py', NO_FINAL_EOL)
        live.replace('gamma', ['delta', 'epsilon'])
        self.assertEqual(live.after, b'alpha\r\nbeta\r\ndelta\r\nepsilon')
        self.assertFalse(LP.eol_stats(live.after)['ends_with_eol'])

    def test_final_eol_absence_survives_an_insert_after_the_last_line(self):
        live = LP.LiveFile('fec/fixture.py', NO_FINAL_EOL)
        live.insert_after('gamma', ['# tail'])
        self.assertEqual(live.after, b'alpha\r\nbeta\r\ngamma\r\n# tail')
        self.assertFalse(LP.eol_stats(live.after)['ends_with_eol'])

    def test_a_file_that_ended_with_eol_still_does(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L6', ['# tail'])
        self.assertTrue(live.after.endswith(b'# tail\r\n'))

    def test_utf16_is_refused_rather_than_cut(self):
        for data, name in ((UTF16LE_CRLF, 'utf16le'), (UTF16BE_CRLF, 'utf16be')):
            with self.assertRaises(ValueError) as caught:
                LP.LiveFile('fec/Task Scheduler.xml', data)
            self.assertIn('fec/Task Scheduler.xml', str(caught.exception))
            self.assertIn(name, str(caught.exception))
        # eol_stats itself is unchanged and still reports on them; only the cutter refuses.
        self.assertEqual(LP.eol_stats(UTF16LE_CRLF)['bom'], 'utf16le')
        self.assertEqual(LP.eol_stats(UTF16BE_CRLF)['bom'], 'utf16be')

    def test_non_ascii_survives_as_its_own_utf8_bytes(self):
        # DE-025: a patch cutter that opened the stream in text mode on this cp1252 console
        # mangled every em dash it wrote.  Bytes end to end is the fix.
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L4', [u'# note — one · two'])
        patch = LP.unified_diff('fec/fixture.py', live.before, live.after)
        self.assertIn(b'# note \xe2\x80\x94 one \xc2\xb7 two', patch)
        self.assertIn(b'\xe2\x80\x94', patch)
        self.assertIn(b'\xc2\xb7', patch)
        self.assertIn(b'# note \xe2\x80\x94 one \xc2\xb7 two\r\n', live.after)


# ------------------------------------------------------------------ 10. diff shape
class DiffShapeTests(TempMixin):

    def test_identical_bytes_produce_no_diff(self):
        self.assertEqual(LP.unified_diff('fec/fixture.py', MIXED, MIXED), b'')

    def test_header_shape(self):
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L4', ['# added'])
        patch = LP.unified_diff('fec/fixture.py', live.before, live.after)
        self.assertTrue(patch.startswith(b'diff --git a/fec/fixture.py b/fec/fixture.py\n'
                                         b'--- a/fec/fixture.py\n'
                                         b'+++ b/fec/fixture.py\n'
                                         b'@@ '))
        self.assertIn(b'+# added\r\n', patch)

    def test_no_newline_marker_is_emitted_not_a_bare_newline(self):
        live = LP.LiveFile('fec/fixture.py', NO_FINAL_EOL)
        live.replace('gamma', ['delta'])
        patch = LP.unified_diff('fec/fixture.py', live.before, live.after)
        self.assertIn(b'\\ No newline at end of file\n', patch)
        self.assertIn(b'-gamma\n\\ No newline at end of file\n', patch)
        self.assertIn(b'+delta\n\\ No newline at end of file\n', patch)

    def test_new_file_diff_shape(self):
        patch = LP.new_file_diff('fec/added.py', PURE_CRLF)
        self.assertTrue(patch.startswith(b'diff --git a/fec/added.py b/fec/added.py\n'
                                         b'new file mode 100644\n'
                                         b'--- /dev/null\n'
                                         b'+++ b/fec/added.py\n'
                                         b'@@ -0,0 +1,3 @@\n'))
        self.assertIn(b'+alpha\r\n', patch)

    def test_new_file_diff_refuses_empty_data(self):
        with self.assertRaises(ValueError):
            LP.new_file_diff('fec/added.py', b'')

    def test_label_validation(self):
        live = LP.LiveFile('fec' + os.sep + 'fixture.py', MIXED)
        live.insert_after('L4', ['# added'])
        with self.assertRaises(ValueError):
            LP.unified_diff('fec\\fixture.py', live.before, live.after)
        with self.assertRaises(ValueError):
            LP.unified_diff(u'fec/fixtüre.py', live.before, live.after)
        with self.assertRaises(ValueError):
            LP.unified_diff('fec/"quoted".py', live.before, live.after)
        with self.assertRaises(ValueError):
            LP.unified_diff(' fec/leading.py', live.before, live.after)
        with self.assertRaises(ValueError):
            LP.unified_diff('fec/trailing.py ', live.before, live.after)
        with self.assertRaises(ValueError):
            LP.unified_diff('fec/tab\there.py', live.before, live.after)
        # A space INSIDE the path is legal and must not be refused.
        self.assertTrue(LP.unified_diff('fec/two words.py', live.before, live.after))

    def test_label_cannot_escape_the_tree(self):
        # prove()/apply_to() join the label onto a root, so an escaping label must never survive
        # validation: os.path.join(root, 'C:', 'x') is 'C:x' on Windows, outside the tree.
        live = LP.LiveFile('fec/fixture.py', MIXED)
        live.insert_after('L4', ['# added'])
        for escaping in ('../outside.py', 'fec/../../outside.py', '/etc/passwd',
                         'C:/Windows/System32/drivers/etc/hosts', 'fec//double.py', './fec/x.py',
                         'fec/./x.py'):
            with self.assertRaises(ValueError, msg=escaping):
                LP.unified_diff(escaping, live.before, live.after)
            with self.assertRaises(ValueError, msg=escaping):
                LP.new_file_diff(escaping, PURE_CRLF)

    def test_prove_refuses_an_escaping_label_before_writing_anything(self):
        patch = LP.new_file_diff('fec/ok.py', PURE_CRLF)
        with self.assertRaises(ValueError):
            LP.prove(patch, {'../escape.py': (MIXED, MIXED)})

    def test_patchset_skips_unchanged_files_and_refuses_an_empty_patch(self):
        untouched = LP.LiveFile('fec/untouched.py', MIXED)
        patch_set = LP.PatchSet()
        patch_set.add(untouched)
        self.assertEqual(patch_set.to_bytes(), b'')
        target = os.path.join(self._tmp, 'empty.patch')
        with self.assertRaises(ValueError):
            patch_set.write(target)
        self.assertFalse(os.path.exists(target))

    def test_patchset_keeps_insertion_order(self):
        first = LP.LiveFile('fec/one.py', MIXED)
        first.insert_after('L4', ['# one'])
        second = LP.LiveFile('fec/two.py', PURE_LF)
        second.insert_after('beta', ['# two'])
        patch_set = LP.PatchSet()
        patch_set.add(first)
        patch_set.add(second)
        patch_set.add_new_file('fec/three.py', PURE_CRLF)
        body = patch_set.to_bytes()
        self.assertLess(body.index(b'a/fec/one.py'), body.index(b'a/fec/two.py'))
        self.assertLess(body.index(b'a/fec/two.py'), body.index(b'a/fec/three.py'))
        written = os.path.join(self._tmp, 'set.patch')
        patch_set.write(written)
        with open(written, 'rb') as handle:
            self.assertEqual(handle.read(), body)


# ------------------------------------------------------------------ 9. the proof
@unittest.skipUnless(GIT, 'git is not on PATH')
class ProveTests(unittest.TestCase):
    """Byte-exactness proved the only way that counts: git apply, then compare every byte."""

    def prove(self, patch, files):
        result = LP.prove(patch, files)
        self.assertTrue(result['ok'], result)
        return result

    def test_insert_into_a_mixed_file(self):
        live = LP.LiveFile('fec/stance/mixed_fixture.py', MIXED)
        live.insert_after('L4', ['# added by the cutter'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.prove(patch, {live.label: (live.before, live.after)})
        self.assertEqual(LP.eol_stats(live.after)['klass'], 'MIXED')

    def test_replace_of_a_last_line_without_a_final_eol(self):
        live = LP.LiveFile('fec/no_final_eol.py', NO_FINAL_EOL)
        live.replace('gamma', ['delta', 'epsilon'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.assertIn(b'\\ No newline at end of file\n', patch)
        self.prove(patch, {live.label: (live.before, live.after)})
        self.assertFalse(live.after.endswith(b'\n'))

    def test_insert_after_the_last_line_of_a_no_final_eol_file(self):
        live = LP.LiveFile('fec/no_final_eol.py', NO_FINAL_EOL)
        live.insert_after('gamma', ['# tail comment'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.prove(patch, {live.label: (live.before, live.after)})
        self.assertFalse(live.after.endswith(b'\n'))

    def test_new_file_diff_for_a_crlf_file(self):
        patch = LP.new_file_diff('fec/tools/brand_new.py', PURE_CRLF)
        self.prove(patch, {'fec/tools/brand_new.py': (None, PURE_CRLF)})

    def test_two_file_patchset(self):
        first = LP.LiveFile('fec/one.py', MIXED)
        first.insert_after('L4', ['# one'])
        second = LP.LiveFile('fec/deeper/two.py', LF_MAJORITY)
        second.insert_after('c', ['# two'])
        patch_set = LP.PatchSet()
        patch_set.add(first)
        patch_set.add(second)
        self.prove(patch_set.to_bytes(),
                   {first.label: (first.before, first.after),
                    second.label: (second.before, second.after)})
        self.assertEqual(LP.split_lines(second.after)[3], b'# two\n')

    def test_form_feed_and_u2028_file(self):
        live = LP.LiveFile('fec/e7_fixture.py', E7)
        live.insert_after(b'ccc\x0cddd', ['# after the form feed'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.prove(patch, {live.label: (live.before, live.after)})
        self.assertEqual(len(LP.split_lines(live.after)), 9)

    def test_non_ascii_insert_round_trips(self):
        live = LP.LiveFile('fec/unicode_fixture.py', MIXED)
        live.insert_after('L4', [u'# note — one · two'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.prove(patch, {live.label: (live.before, live.after)})

    def test_prove_reports_a_mismatch_rather_than_claiming_success(self):
        # Negative control: a patch that does not describe the expected result must NOT pass.
        live = LP.LiveFile('fec/one.py', MIXED)
        live.insert_after('L4', ['# one'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        result = LP.prove(patch, {live.label: (live.before, MIXED)})
        self.assertFalse(result['ok'])
        self.assertEqual(result['mismatches'], ['fec/one.py'])

    def test_prove_refuses_an_empty_patch(self):
        with self.assertRaises(ValueError):
            LP.prove(b'', {})


@unittest.skipUnless(GIT, 'git is not on PATH')
class CheckAgainstTests(TempMixin):

    def test_check_against_a_plain_directory(self):
        # check_against() is read-only: it only ever runs `git apply --check`.
        live = LP.LiveFile('fixture.py', MIXED)
        live.insert_after('L4', ['# added'])
        with open(os.path.join(self._tmp, 'fixture.py'), 'wb') as handle:
            handle.write(MIXED)
        patch_path = os.path.join(self._tmp, 'cut.patch')
        with open(patch_path, 'wb') as handle:
            handle.write(LP.unified_diff(live.label, live.before, live.after))
        result = LP.check_against(self._tmp, patch_path)
        self.assertEqual(result['rc'], 0, result)
        # Nothing was applied: the file on disk is untouched.
        with open(os.path.join(self._tmp, 'fixture.py'), 'rb') as handle:
            self.assertEqual(handle.read(), MIXED)

    def test_a_relative_patch_path_resolves_against_the_caller_not_the_root(self):
        # `git -C <root>` resolves a RELATIVE patch path against ROOT, so a bare name would point
        # at a file inside the tree being patched -- the wrong file, or none at all.
        live = LP.LiveFile('fixture.py', MIXED)
        live.insert_after('L4', ['# added'])
        tree = os.path.join(self._tmp, 'tree')
        elsewhere = os.path.join(self._tmp, 'patches')
        os.makedirs(tree)
        os.makedirs(elsewhere)
        with open(os.path.join(tree, 'fixture.py'), 'wb') as handle:
            handle.write(MIXED)
        with open(os.path.join(elsewhere, 'cut.patch'), 'wb') as handle:
            handle.write(LP.unified_diff(live.label, live.before, live.after))
        previous = os.getcwd()
        try:
            os.chdir(elsewhere)
            result = LP.check_against(tree, 'cut.patch')
            self.assertEqual(result['rc'], 0, result)
            self.assertTrue(os.path.isabs(LP.apply_argv('x', 'cut.patch')[-1]))
            self.assertTrue(os.path.isabs(LP.apply_argv('x', 'cut.patch')[2]))
        finally:
            os.chdir(previous)

    def test_check_against_reports_a_patch_that_does_not_apply(self):
        live = LP.LiveFile('fixture.py', MIXED)
        live.insert_after('L4', ['# added'])
        with open(os.path.join(self._tmp, 'fixture.py'), 'wb') as handle:
            handle.write(PURE_LF)          # not the file the patch was cut against
        patch_path = os.path.join(self._tmp, 'cut.patch')
        with open(patch_path, 'wb') as handle:
            handle.write(LP.unified_diff(live.label, live.before, live.after))
        result = LP.check_against(self._tmp, patch_path)
        self.assertNotEqual(result['rc'], 0)
        self.assertTrue(result['stderr'].strip())


class ApplyArgvTests(unittest.TestCase):

    def test_apply_argv_is_the_byte_exact_command(self):
        # Both paths come back absolute and in this platform's own separator form, because
        # `git -C <root>` resolves a relative patch path against root rather than the cwd.
        self.assertEqual(
            LP.apply_argv('C:/tree', 'C:/tmp/cut.patch'),
            ['git', '-C', os.path.abspath('C:/tree'), '-c', 'core.autocrlf=false',
             '-c', 'core.safecrlf=false', 'apply', os.path.abspath('C:/tmp/cut.patch')])

    def test_apply_argv_hands_back_a_fresh_list(self):
        LP.apply_argv('C:/tree', 'p').append('MUTATED')
        self.assertEqual(LP.GIT_BYTE_EXACT,
                         ['-c', 'core.autocrlf=false', '-c', 'core.safecrlf=false'])


class ManifestTests(TempMixin):

    def build_set(self):
        live = LP.LiveFile('fec/one.py', MIXED)
        live.insert_after('L4', ['# one'])
        patch_set = LP.PatchSet()
        patch_set.add(live)
        patch_set.add_new_file('fec/tools/fresh.py', PURE_CRLF)
        return live, patch_set

    def test_manifest_round_trips_and_pins_the_bytes(self):
        live, patch_set = self.build_set()
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        with open(patch_path + '.manifest.json', 'rb') as handle:
            raw = handle.read()
        self.assertNotIn(b'\r\n', raw)                       # LF newlines, written in binary
        loaded = json.loads(raw.decode('utf-8'))
        self.assertEqual(loaded, patch_set.manifest())
        self.assertEqual(loaded['fec/one.py']['before_sha256'], LP.raw_sha256(live.before))
        self.assertEqual(loaded['fec/one.py']['after_sha256'], LP.raw_sha256(live.after))
        self.assertEqual(loaded['fec/one.py']['before_eol']['klass'], 'MIXED')
        self.assertEqual(loaded['fec/one.py']['after_eol']['klass'], 'MIXED')
        self.assertIsNone(loaded['fec/tools/fresh.py']['before_sha256'])
        self.assertIsNone(loaded['fec/tools/fresh.py']['before_eol'])
        self.assertEqual(loaded['fec/tools/fresh.py']['after_sha256'], LP.raw_sha256(PURE_CRLF))

    def test_manifest_skips_a_file_the_set_did_not_change(self):
        patch_set = LP.PatchSet()
        patch_set.add(LP.LiveFile('fec/untouched.py', MIXED))
        self.assertEqual(patch_set.manifest(), {})

    def test_load_manifest_reads_the_sidecar_back(self):
        _live, patch_set = self.build_set()
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        self.assertEqual(LP.manifest_path(patch_path), patch_path + '.manifest.json')
        self.assertEqual(LP.load_manifest(patch_path), patch_set.manifest())

    def test_patch_labels_reads_every_header_in_order(self):
        first = LP.LiveFile('fec/one.py', MIXED)
        first.insert_after('L4', ['# one'])
        second = LP.LiveFile('fec/two words.py', PURE_LF)      # a space inside the label is legal
        second.insert_after('beta', ['# two'])
        patch_set = LP.PatchSet()
        patch_set.add(first)
        patch_set.add(second)
        patch_set.add_new_file('fec/tools/three.py', PURE_CRLF)
        self.assertEqual(LP.patch_labels(patch_set.to_bytes()),
                         ['fec/one.py', 'fec/two words.py', 'fec/tools/three.py'])

    def test_patch_labels_survives_a_label_that_contains_the_separator(self):
        # 'fec/a b/c.py' makes the header read `a/fec/a b/c.py b/fec/a b/c.py`, where the
        # RIGHTMOST ' b/' is inside the second copy. Reconstruction gets it right; a naive
        # rpartition would hand back 'fec/a b/c.py b/fec/a' and the agreement gate would misfire.
        live = LP.LiveFile('fec/a b/c.py', MIXED)
        live.insert_after('L4', ['# x'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.assertEqual(LP.patch_labels(patch), ['fec/a b/c.py'])

    def test_patch_labels_ignores_a_body_line_that_looks_like_a_header(self):
        live = LP.LiveFile('fec/one.py', MIXED)
        live.insert_after('L4', ['# diff --git a/fake.py b/fake.py'])
        patch = LP.unified_diff(live.label, live.before, live.after)
        self.assertEqual(LP.patch_labels(patch), ['fec/one.py'])


class AtomicWriteTests(TempMixin):

    def test_write_atomic_replaces_the_bytes_and_leaves_no_temp(self):
        target = os.path.join(self._tmp, 'atomic.txt')
        with open(target, 'wb') as handle:
            handle.write(b'old bytes that must go\r\n')
        LP._write_atomic(target, MIXED)
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), MIXED)
        self.assertEqual([n for n in os.listdir(self._tmp) if n.startswith('.live_patch_')], [])

    def test_write_atomic_creates_a_file_that_was_not_there(self):
        target = os.path.join(self._tmp, 'fresh.txt')
        LP._write_atomic(target, PURE_LF)
        with open(target, 'rb') as handle:
            self.assertEqual(handle.read(), PURE_LF)
        self.assertEqual([n for n in os.listdir(self._tmp) if n.startswith('.live_patch_')], [])


# ------------------------------------------------------- the renormalisation hazard, measured
@unittest.skipUnless(GIT, 'git is not on PATH')
class ByteExactApplyTests(TempMixin):
    """Why GIT_BYTE_EXACT exists, and that apply_to() refuses anything it cannot vouch for."""

    def work_tree(self, name='tree'):
        tree = os.path.join(self._tmp, name)
        os.makedirs(tree)
        LP._assert_outside_repository(tree)
        return tree

    def stage(self, tree, label, data):
        target = os.path.join(tree, *label.split('/'))
        parent = os.path.dirname(target)
        if not os.path.isdir(parent):
            os.makedirs(parent)
        with open(target, 'wb') as handle:
            handle.write(data)
        return target

    def read_back(self, tree, label):
        with open(os.path.join(tree, *label.split('/')), 'rb') as handle:
            return handle.read()

    def cut_lf_only_hunk(self):
        live = LP.LiveFile('fec/lf_run.py', LF_RUN_IN_CRLF)
        live.insert_after('run03', ['# added inside the bare-LF run'], eol='anchor')
        return live, LP.unified_diff(live.label, live.before, live.after)

    def build_set(self):
        live = LP.LiveFile('fec/one.py', MIXED)
        live.insert_after('L4', ['# one'])
        patch_set = LP.PatchSet()
        patch_set.add(live)
        patch_set.add_new_file('fec/tools/fresh.py', PURE_CRLF)
        return live, patch_set

    def test_the_hunk_under_test_really_is_lf_only(self):
        live, patch = self.cut_lf_only_hunk()
        body = patch.split(b'@@', 2)[-1]
        self.assertNotIn(b'\r', body)
        self.assertEqual(LP.eol_stats(LF_RUN_IN_CRLF)['klass'], 'MIXED')
        self.assertEqual(LP.eol_stats(live.after)['klass'], 'MIXED')
        self.assertEqual(LP.split_lines(live.after)[18], b'# added inside the bare-LF run\n')

    def test_prove_is_byte_exact_for_an_lf_only_hunk(self):
        live, patch = self.cut_lf_only_hunk()
        result = LP.prove(patch, {live.label: (live.before, live.after)})
        self.assertTrue(result['ok'], result)

    def test_plain_git_apply_renormalises_the_whole_file(self):
        # The hazard pin, MEASURED not assumed: git reads a hunk whose old lines are all bare LF
        # through convert_to_git(), and writes the pure-LF result back through
        # convert_to_working_tree(), which converts LF -> CRLF for a buffer that carries no CR.
        # Guarded on core.autocrlf so the suite stays green on a box without it.
        live, patch = self.cut_lf_only_hunk()
        tree = self.work_tree('plain')
        rc, out, _err = LP._run_git(['config', '--get', 'core.autocrlf'], cwd=tree)
        if rc != 0 or out.strip() != b'true':
            self.skipTest('core.autocrlf is not true outside a repository on this box')
        self.stage(tree, live.label, live.before)
        patch_path = os.path.join(self._tmp, 'plain.patch')
        with open(patch_path, 'wb') as handle:
            handle.write(patch)
        apply_rc, _out, err = LP._run_git(['apply', patch_path], cwd=tree)   # NO byte-exact flags
        self.assertEqual(apply_rc, 0, err)
        actual = self.read_back(tree, live.label)
        self.assertNotEqual(actual, live.after,
                            'git no longer renormalises an LF-only hunk under autocrlf=true; '
                            're-measure before relaxing GIT_BYTE_EXACT')
        self.assertEqual(LP.eol_stats(actual)['klass'], 'PURE_CRLF')
        self.assertEqual(LP.eol_stats(live.after)['klass'], 'MIXED')

    def test_apply_to_is_byte_exact(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        result = LP.apply_to(tree, patch_path, patch_set.manifest())
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.after)
        self.assertEqual(self.read_back(tree, 'fec/tools/fresh.py'), PURE_CRLF)
        self.assertEqual(LP.eol_stats(self.read_back(tree, 'fec/one.py'))['klass'], 'MIXED')

    def test_apply_to_refuses_a_target_that_moved(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        moved = MIXED + b'# a concurrent session got here first\r\n'
        self.stage(tree, 'fec/one.py', moved)
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        result = LP.apply_to(tree, patch_path, patch_set.manifest())
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'target moved since the patch was cut')
        self.assertEqual(result['mismatches'], ['fec/one.py'])
        self.assertIsNone(result['check_rc'])
        self.assertEqual(self.read_back(tree, 'fec/one.py'), moved)
        self.assertFalse(os.path.exists(os.path.join(tree, 'fec', 'tools', 'fresh.py')))

    def test_apply_to_refuses_a_new_file_that_already_exists(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        self.stage(tree, 'fec/tools/fresh.py', b'someone else wrote this\r\n')
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        result = LP.apply_to(tree, patch_path, patch_set.manifest())
        self.assertFalse(result['ok'])
        self.assertEqual(result['mismatches'], ['fec/tools/fresh.py'])
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.before)

    def test_apply_to_loads_the_sidecar_manifest(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        result = LP.apply_to(tree, patch_path)               # no manifest argument at all
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.after)

    def test_apply_to_refuses_when_there_is_no_sidecar(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        patch_path = os.path.join(self._tmp, 'bare.patch')
        with open(patch_path, 'wb') as handle:
            handle.write(patch_set.to_bytes())               # patch only, manifest never written
        result = LP.apply_to(tree, patch_path)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'no manifest beside the patch')
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.before)

    def test_apply_to_refuses_a_manifest_that_misses_a_patched_file(self):
        # Without this gate a short manifest would let the apply rewrite a file nobody hashed,
        # and the after-check would never look at it.
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        short = patch_set.manifest()
        del short['fec/tools/fresh.py']
        result = LP.apply_to(tree, patch_path, short)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'patch and manifest name different files')
        self.assertEqual(result['mismatches'], ['fec/tools/fresh.py'])
        self.assertIsNone(result['check_rc'])
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.before)
        self.assertFalse(os.path.exists(os.path.join(tree, 'fec', 'tools', 'fresh.py')))

    def test_apply_to_refuses_a_traditional_hunk_git_would_apply(self):
        # patch_labels sees only `diff --git` headers; git apply also applies bare ---/+++ sections.
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        self.stage(tree, 'fec/other.txt', b'x\n')
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        with open(patch_path, 'ab') as handle:
            handle.write(b'--- a/fec/other.txt\n+++ b/fec/other.txt\n@@ -1 +1 @@\n-x\n+HIJACK\n')
        result = LP.apply_to(tree, patch_path, patch_set.manifest())
        self.assertFalse(result['ok'])
        self.assertEqual(result['mismatches'], ['fec/other.txt'])
        self.assertEqual(self.read_back(tree, 'fec/other.txt'), b'x\n')
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.before)

    def test_apply_to_refuses_a_manifest_naming_a_file_the_patch_does_not(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        extra = patch_set.manifest()
        extra['fec/never_patched.py'] = {'before_sha256': LP.raw_sha256(MIXED),
                                         'after_sha256': LP.raw_sha256(MIXED),
                                         'before_eol': None, 'after_eol': None}
        result = LP.apply_to(tree, patch_path, extra)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'patch and manifest name different files')
        self.assertEqual(result['mismatches'], ['fec/never_patched.py'])
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.before)

    def test_apply_to_restores_when_the_result_does_not_match_the_manifest(self):
        live, patch_set = self.build_set()
        tree = self.work_tree()
        self.stage(tree, 'fec/one.py', live.before)
        patch_path = os.path.join(self._tmp, 'set.patch')
        patch_set.write(patch_path)
        manifest = patch_set.manifest()
        manifest['fec/one.py']['after_sha256'] = '0' * 64
        result = LP.apply_to(tree, patch_path, manifest)
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason'], 'result bytes do not match the manifest')
        self.assertEqual(result['mismatches'], ['fec/one.py'])
        self.assertEqual(self.read_back(tree, 'fec/one.py'), live.before)
        self.assertFalse(os.path.exists(os.path.join(tree, 'fec', 'tools', 'fresh.py')))


class NumstatPathsTests(unittest.TestCase):
    def test_normal_rename_and_trailing_empty(self):
        listing = (b'1\t2\tfec/a.py\0'
                   b'0\t0\t\0old.py\0new.py\0')
        self.assertEqual(LP._numstat_paths(listing), {'fec/a.py', 'old.py', 'new.py'})

    def test_unparseable_record_fails_closed(self):
        with self.assertRaises(ValueError):
            LP._numstat_paths(b'1\t2\tfec/a.py\0garbage-without-tabs\0')


if __name__ == '__main__':
    unittest.main()
