# -*- coding: utf-8 -*-
"""The legislative archive's name join is verified against BILLSTATUS (CU_IDENTITY_FIX).

2026-10-11: congress_bills_archive keys members by NAME, so Sen. Robert Menendez's (M000639,
off-roster) Senate bills resolved to Rep. Robert Menendez (M001226) -- 42 routing rows, his
Leg% 0.637 -> 0.143 and rank 201 -> 450 once removed. A census of all 10,885 routing rows
against bill_repository_live.json found those 42 and no other unsupported row.

build_archive_legislative.py writes artifacts on import, so its wiring is asserted on the
SOURCE TEXT (the test_sponsor_column_producer_wiring.py technique); the helper is pure.
"""
import json
import os
import sys

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from members.member_identity import billstatus_unverified  # noqa: E402
import moneyball_core as MC  # noqa: E402

PRODUCER = os.path.join(FEC, 'legislation', 'build_archive_legislative.py')
with open(PRODUCER, encoding='utf-8') as _h:
    SOURCE = _h.read()

BILLS = {'118:s:2142': {'sponsor': {'bioguide': 'M000639'},
                        'cosponsors': [{'bioguide': 'C001056'}],
                        'original_cosponsors': [{'bioguide': 'B001288'}]},
         '118:hr:3170': {'sponsor': {'bioguide': 'X000001'},
                         'cosponsors': [{'bioguide': 'M001226', 'withdrawn_date': ''}],
                         'original_cosponsors': []}}


def test_house_member_on_a_senate_bill_is_unverified():
    assert billstatus_unverified(BILLS, '118:s:2142', 'M001226') is True


def test_real_sponsor_and_cosponsors_are_verified():
    for bio in ('M000639', 'C001056', 'B001288'):
        assert billstatus_unverified(BILLS, '118:s:2142', bio) is False
    assert billstatus_unverified(BILLS, '118:hr:3170', 'M001226') is False


def test_uncovered_bill_or_missing_bioguide_is_not_judged():
    assert billstatus_unverified(BILLS, '118:s:9999', 'M001226') is False
    assert billstatus_unverified(BILLS, '', 'M001226') is False
    assert billstatus_unverified(BILLS, '118:s:2142', None) is False
    assert billstatus_unverified({}, '118:s:2142', 'M001226') is False


def test_live_billstatus_rejects_the_menendez_senate_bills():
    path = os.path.join(ROOT, 'bill_repository_live.json')
    if not os.path.exists(path):
        return  # fixture tree without the data plane; the pure tests above still run
    bills = json.load(open(path, encoding='utf-8'))['bills']
    for key in ('118:s:2142', '117:s:3128', '118:s:2860', '117:s:910'):
        if key in bills:
            assert billstatus_unverified(bills, key, 'M001226') is True, key
    for key in ('118:hr:3170', '118:hr:4233', '119:hr:2094'):
        if key in bills:
            assert billstatus_unverified(bills, key, 'M001226') is False, key


def test_dial_contract():
    old = os.environ.get('CU_IDENTITY_FIX')
    try:
        for v, want in (('', True), ('1', True), ('0', False)):
            os.environ['CU_IDENTITY_FIX'] = v
            assert MC.cu_identity_fix_on() is want, v
        os.environ['CU_IDENTITY_FIX'] = '2'
        try:
            MC.cu_identity_fix_on()
            raise AssertionError('CU_IDENTITY_FIX=2 must raise')
        except ValueError:
            pass
        os.environ.pop('CU_IDENTITY_FIX')
        assert MC.cu_identity_fix_on() is True
    finally:
        if old is None:
            os.environ.pop('CU_IDENTITY_FIX', None)
        else:
            os.environ['CU_IDENTITY_FIX'] = old


def test_producer_checks_identity_after_the_name_join_and_before_any_scoring():
    call = 'if billstatus_unverified(_billstatus,_repository_key,_member_bios.get(mm)):'
    assert SOURCE.count(call) == 1
    assert "_billstatus=load_billstatus(MB_ROOT)" in SOURCE
    assert 'cu_identity_fix_on' not in SOURCE and 'bill_repository_live.json' not in SOURCE  # one dial gate
    assert "'disposition':'identity-unverified-by-billstatus','member':mm,'contribution':0.0" in SOURCE
    assert SOURCE.index('mm=matchmember(r[\'Member\'])') < SOURCE.index(call)
    assert SOURCE.index(call) < SOURCE.index('if not OM.eligible(')
    assert SOURCE.index(call) < SOURCE.index('leg[mm]') if 'leg[mm]' in SOURCE else True


# The receptivity-side readers join the same archive by exact NAME (r[iM] in C) and credited
# Sen. Menendez's Senate bills to M001226 too: coop_v2_candidate.py (A-slot cooperation_v2_z),
# behavioral_dispositions.py (breadth_z) and behavior_panel.py (n_sponsored / n_cosponsored).
def test_archive_row_unverified_parses_the_raw_archive_columns():
    from members.member_identity import archive_row_unverified
    assert archive_row_unverified(BILLS, 'S. 2142', 118, 'M001226') is True
    assert archive_row_unverified(BILLS, 'S.2142', '118', 'M000639') is False
    assert archive_row_unverified(BILLS, 'H.R. 3170', 118, 'M001226') is False
    assert archive_row_unverified(BILLS, 'Letter to the CFPB', 118, 'M001226') is False  # not judged
    assert archive_row_unverified(BILLS, None, None, 'M001226') is False


def test_load_billstatus_is_the_dial_gate():
    import tempfile
    from members.member_identity import load_billstatus
    old = os.environ.get('CU_IDENTITY_FIX')
    with tempfile.TemporaryDirectory() as root:
        with open(os.path.join(root, 'bill_repository_live.json'), 'w', encoding='utf-8') as h:
            json.dump({'bills': BILLS}, h)
        try:
            os.environ['CU_IDENTITY_FIX'] = '1'
            assert load_billstatus(root) == BILLS
            os.environ['CU_IDENTITY_FIX'] = '0'
            assert load_billstatus(root) == {}
        finally:
            if old is None:
                os.environ.pop('CU_IDENTITY_FIX', None)
            else:
                os.environ['CU_IDENTITY_FIX'] = old


READERS = {'coop_v2_candidate.py': 'bill_sponsor[bk] = m',
           'behavioral_dispositions.py': 'secs[r[iM]].add(',
           'behavior_panel.py': 'sp[m] += 1'}


def test_receptivity_readers_check_identity_before_counting_a_row():
    for name, first_use in READERS.items():
        with open(os.path.join(FEC, 'members', name), encoding='utf-8') as h:
            src = h.read()
        call = 'if archive_row_unverified(_billstatus, r[iN], r[iCg], '
        assert src.count(call) == 1, name
        assert '_billstatus = load_billstatus(B)' in src, name
        assert 'cu_identity_fix_on' not in src and 'bill_repository_live.json' not in src, name
        assert src.index(call) < src.index(first_use), name


if __name__ == '__main__':
    import traceback
    passed = failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn(); passed += 1; print('  ok  ', name)
            except Exception:
                failed += 1; print('  FAIL', name); traceback.print_exc()
    print('%d passed, %d failed' % (passed, failed))
    sys.exit(1 if failed else 0)
