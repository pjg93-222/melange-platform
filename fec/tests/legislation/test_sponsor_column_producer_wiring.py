# -*- coding: utf-8 -*-
"""The producer really is wired to the bill-level Sponsor column, and to its dial.

build_archive_legislative.py cannot be imported to check this -- importing it reads the
archive workbook and overwrites archive_leg.json and legislation_routing_audit.json -- so
the wiring is asserted against the producer's SOURCE TEXT, the same technique
test_bill_collision_guards.py and test_sentinel_bill_workbook_sync.py already use on this
file.  sponsor_column.py itself is pure and is imported normally.

What this guards: a helper that ships with nobody calling it is indistinguishable from a
helper that was never written, and a hook placed AFTER the row loop (or inside it) would
bypass every gate the rows are supposed to face.  Position, not just presence, is asserted.
"""
import os
import sys
import traceback


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from legislation import sponsor_column as SC  # noqa: E402

PRODUCER = os.path.join(FEC, 'legislation', 'build_archive_legislative.py')
with open(PRODUCER, encoding='utf-8') as _handle:
    SOURCE = _handle.read()

ROW_LOOP = 'for _idx,r in A.iterrows():'
ARCHIVE_READ = "A=pd.read_excel(sorted(glob.glob(MB_ROOT+r'\\congress_bills_archive_*.xlsx'))[-1]"
CALL = '_sponsor_column_rows=SC.sponsor_rows_from_column('


def test_the_producer_imports_the_helper():
    assert 'from legislation import sponsor_column as SC' in SOURCE


def test_the_producer_calls_the_helper_exactly_once():
    assert SOURCE.count('SC.sponsor_rows_from_column(') == 1, SOURCE.count(
        'SC.sponsor_rows_from_column(')
    assert CALL in SOURCE


def test_the_helper_is_handed_the_producers_own_rules():
    # Not re-derived copies: the Resolver, the category map and the Congress normaliser the
    # row loop itself uses, so a synthesised row can never resolve to a different member.
    assert "A.iloc[:_archive_rows].to_dict('records'),matchmember,seccat,BR._congress" in SOURCE


def test_only_the_archive_slice_is_searched_for_sponsors():
    # The bill-level Sponsor column exists only on archive rows; sentinel and manual
    # evidence is concatenated with that cell blank.
    assert '_archive_rows=len(A)' in SOURCE
    assert SOURCE.index(ARCHIVE_READ) < SOURCE.index('_archive_rows=len(A)') < SOURCE.index(CALL)


def test_the_rows_are_injected_before_the_row_loop_so_every_gate_still_judges_them():
    assert 'A=pd.concat([A,pd.DataFrame(_sponsor_column_rows)],ignore_index=True)' in SOURCE
    assert SOURCE.index(CALL) < SOURCE.index(ROW_LOOP)
    assert SOURCE.index('A=pd.concat([A,pd.DataFrame(_sponsor_column_rows)]') < SOURCE.index(ROW_LOOP)
    # Top-level, not nested inside the loop or a function.
    assert ('\n' + CALL) in SOURCE


def test_the_frame_assembly_the_hook_extends_is_untouched():
    # Negative control: the sentinel concat and the fail-closed letters floor are exactly
    # where they were, so this fix cannot be the reason either one moved.
    assert 'A=pd.concat([A,_verified[A.columns]],ignore_index=True)' in SOURCE
    assert '_LTR_MIN_ROWS, _LTR_MIN_IDS, _LTR_MIN_MEMBERS = 296, 7, 251' in SOURCE
    assert SOURCE.index('A=pd.concat([A,_verified[A.columns]]') < SOURCE.index(CALL)


def test_the_routing_audit_carries_the_provenance_marker():
    assert 'r.get(SC.SOURCE_COLUMN)' in SOURCE, 'the audit must read the helper constant'
    assert "audit['evidence_source']=_evidence_source" in SOURCE
    assert (SOURCE.index("'tracker_status':_clean(r.get('LastAction'))}")
            < SOURCE.index("audit['evidence_source']=_evidence_source")
            < SOURCE.index("cat=seccat(r['Section'])"))


def test_the_marker_is_recorded_only_on_the_rows_that_carry_it():
    # Guarded by a truth test, so rows the archive already held add no key and an audit
    # written with the dial off stays byte-identical to the pre-fix one.
    assert 'if _evidence_source: audit' in SOURCE


def test_the_dial_is_named_in_the_producer_and_defaults_to_the_fix():
    assert SC.DIAL == 'LEG_SPONSOR_FROM_COLUMN'
    assert SC.DIAL in SOURCE
    assert SC.enabled({}) is True
    assert SC.enabled({SC.DIAL: '0'}) is False
    assert SC.SOURCE == 'archive-sponsor-column'


def test_the_producer_never_reads_the_dial_itself():
    # One authority for the dial: sponsor_column.enabled. A second os.environ read in the
    # producer is how HALF and HALFF drifted apart; the producer may only NAME the dial in
    # its comment, and the helper decides.
    assert "environ.get('LEG_SPONSOR_FROM_COLUMN'" not in SOURCE
    assert "environ['LEG_SPONSOR_FROM_COLUMN']" not in SOURCE
    assert 'SC.enabled(' not in SOURCE


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith('test_') and callable(fn)]
    passed = failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
            print('PASS %s' % fn.__name__)
        except BaseException:
            failed += 1
            print('FAIL %s' % fn.__name__)
            traceback.print_exc()
    print('test_sponsor_column_producer_wiring: %d passed, %d failed' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
