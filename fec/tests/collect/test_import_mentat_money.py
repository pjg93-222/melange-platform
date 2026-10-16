"""Mentat money importer contract (decision B MW-1..MW-4, PM-A4). Synthetic fixtures only."""
from pathlib import Path
from datetime import date, timedelta
import json, sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from collect import import_mentat_money as IM

CMAP = {'H0AA00001': 'A000001', 'S0BB00002': 'B000002', 'H0CC00003': 'C000003'}
ROSTER = {'A000001', 'B000002'}
PAC44 = {'Big Bank NA': ['C00000044']}


def rf(cid='C00100001', tier='Employee', category='Candidate', amount=100.0, cycle='2025-2026',
       desig='P', rel='Principal campaign committee', company='Small Bank'):
    return {'Election Cycle': cycle, 'Committee ID': cid, 'Contributor Tier': tier, 'Recipient Category': category,
            'Committee Designation Code': desig, 'Committee Relationship': rel, 'Company': company, 'Giving': amount}


def sp(cid='C00100001', cand='H0AA00001', rel='Principal campaign committee', cycle='2025-2026', source='FEC ccl', **extra):
    return {'Committee ID': cid, 'Candidate ID': cand, 'Election Cycle': cycle, 'Relationship': rel, 'Link Source': source, **extra}


def run(rows, sponsors=(sp(),)):
    return IM.project_book(rows, list(sponsors), CMAP, ROSTER, PAC44)


def test_candidate_committee_joins_by_tier_channel():
    members, acct = run([rf(), rf(tier='Chief', amount=50), rf(tier='Middle Management', amount=7)])
    assert members == {'A000001': {'2025-2026': {'employee': 100.0, 'chief': 50.0, 'middle': 7.0}}}
    assert acct['2025-2026']['book_total'] == 157.0


def test_conduit_never_joins_and_is_reported_unattributed():
    members, acct = run([rf(cid='C00401224', category='Carey', desig='U'), rf(cid='C00694323', category='Carey')],
                        [sp(cid='C00401224'), sp(cid='C00694323')])
    assert members == {} and acct['2025-2026']['unattributed_conduit'] == 200.0


def test_jfc_never_joins():
    members, acct = run([rf(category='JFC', desig='J', rel='Joint fundraiser linked to candidate')],
                        [sp(rel='Joint fundraiser linked to candidate')])
    assert members == {} and acct['2025-2026']['excluded_jfc'] == 100.0


def test_downstream_like_and_unlinked_categories_never_enter_member_channels():
    rows = [rf(category='Super PAC', desig='U', rel='No direct candidate/officeholder link'),
            rf(category='Party Committee', desig='U'), rf(category='Candidate', desig='U',
                                                         rel='Unauthorized committee linked to candidate')]
    members, acct = run(rows)
    assert members == {} and acct['2025-2026']['other_recipients'] == 300.0


def test_own_leadership_pac_is_a_separate_channel_and_provisional_or_roster_links_excluded():
    lp = dict(category='Leadership PAC', desig='D', rel='Leadership PAC linked to candidate', cid='C00200002')
    members, _ = run([rf(**lp)], [sp(cid='C00200002', rel='Leadership PAC linked to candidate')])
    assert members == {'A000001': {'2025-2026': {'own_lpac': 100.0}}}
    members, acct = run([rf(**lp)], [sp(cid='C00200002', rel='Leadership PAC linked to candidate', Note='provisional')])
    assert members == {} and 'C00200002' in acct['2025-2026']['unlinked_committee']
    members, _ = run([rf(**lp)], [sp(cid='C00200002', rel='Leadership PAC linked to candidate', source='Leadership PAC roster')])
    assert members == {}


def test_corporate_row_of_a_fec_leg_company_is_dropped_planted_double_row():
    # Negative control: the same Corporate check from a company OUTSIDE the 44 is kept.
    kept, _ = run([rf(tier='Corporate', company='Small Bank', amount=5000)])
    assert kept['A000001']['2025-2026']['corporate'] == 5000.0
    members, acct = run([rf(tier='Corporate', company='Big Bank NA', amount=5000), rf(amount=10)])
    assert members['A000001']['2025-2026'] == {'employee': 10.0}
    assert acct['2025-2026']['dropped_fec_leg_duplicate'] == 5000.0


def test_unmatched_and_ambiguous_candidates_are_counted_not_dropped_silently():
    members, acct = run([rf(cid='C00300003')], [sp(cid='C00300003', cand='H0CC00003')])   # off-roster member
    assert members == {} and acct['2025-2026']['unmatched_candidate'] == {'H0CC00003': 100.0}
    members, acct = run([rf(cid='C00400004')], [sp(cid='C00400004'), sp(cid='C00400004', cand='S0BB00002')])
    assert members == {} and acct['2025-2026']['ambiguous_committee'] == {'C00400004': 100.0}


def test_blank_committee_id_unlinked_row_keeps_the_bucket_a_dict():
    members, acct = run([rf(cid=None, amount=30.25), rf(cid='C00200002', amount=12.5), rf(cid=None, amount=1.0)], [])
    assert members == {}
    assert acct['2025-2026']['unlinked_committee'] == {'(blank)': 31.25, 'C00200002': 12.5}
    assert acct['2025-2026']['book_total'] == 43.75
    json.dumps(acct, sort_keys=True)   # no None dict key


def test_unknown_tier_fails_loud_and_unknown_book_refuses_import():
    with pytest.raises(ValueError): run([rf(tier='Intern')])
    side = {'Credit Union': 'cu'}
    assert IM.admit_books({'Credit Union': ['x']}, side)
    with pytest.raises(ValueError, match='not in the side map'):
        IM.admit_books({'Credit Union': ['x'], 'Payday Lenders': ['y']}, side)


def test_side_map_rejects_unknown_side(tmp_path):
    p = tmp_path / 'side.json'; p.write_text(json.dumps({'Fintech': 'maybe'}))
    with pytest.raises(ValueError): IM.load_side_map(p)


def test_live_side_map_matches_decision_mw1():
    root = Path(__file__).resolve().parents[3]
    side = IM.load_side_map(root / 'data/money/mentat_side_map.json')
    assert side == {'Credit Union': 'cu', 'Banking': 'ind', 'Community Banks': 'ind', 'Credit Arms': 'context',
                    'Fintech': 'context', 'Telecom': 'context', 'Crypto': 'context',
                    'Property & Casualty Insurance': 'context'}


# ---- MW-4 freshness, fixed clocks -------------------------------------------------------------
ABOUT = {'Source DB SHA-256': 'ab' * 32}
FRESH = [{'Election Cycle': '2025-2026', 'FEC Dataset': 'pas2', 'Server Last Modified': 'Sun, 06 Sep 2026 15:55:17 GMT'}]


def test_deadline_rule_passes_at_deadline_plus_14_not_before():
    assert IM.last_deadline(date(2026, 10, 29), 14) == date(2026, 10, 15)
    assert IM.last_deadline(date(2026, 10, 28), 14) == date(2026, 7, 15)
    assert IM.last_deadline(date(2026, 2, 13), 14) == date(2025, 10, 15)
    assert IM.last_deadline(date(2026, 2, 14), 14) == date(2026, 1, 31)


def test_book_fresh_at_deadline_plus_14():
    d_last = IM.last_deadline(date(2026, 7, 29), 14)
    assert d_last == date(2026, 7, 15)
    st, pas2 = IM.book_status(ABOUT, FRESH, d_last, '2025-2026', 'ab' * 32, date(2026, 9, 10))
    assert st == 'ok' and pas2 == date(2026, 9, 6)


def test_book_stale_when_source_dates_precede_d_last():
    d_last = IM.last_deadline(date(2026, 10, 29), 14)   # Oct 15
    assert IM.book_status(ABOUT, FRESH, d_last, '2025-2026', 'ab' * 32, date(2026, 10, 20))[0] == 'mentat_stale'
    late = [dict(FRESH[0], **{'Server Last Modified': 'Fri, 16 Oct 2026 00:00:00 GMT'})]
    assert IM.book_status(ABOUT, late, d_last, '2025-2026', 'ab' * 32, date(2026, 9, 10))[0] == 'mentat_stale'
    assert IM.book_status(ABOUT, late, d_last, '2025-2026', 'ab' * 32, date(2026, 10, 16))[0] == 'ok'


def test_book_hash_mismatch_or_missing_store_fails():
    d_last = date(2026, 7, 15)
    assert IM.book_status(ABOUT, FRESH, d_last, '2025-2026', 'cd' * 32, date(2026, 9, 10))[0] == 'mentat_hash_mismatch'
    assert IM.book_status(ABOUT, FRESH, d_last, '2025-2026', None, None)[0] == 'mentat_hash_mismatch'
    assert IM.book_status({}, FRESH, d_last, '2025-2026', 'ab' * 32, date(2026, 9, 10))[0] == 'mentat_hash_mismatch'


def test_missing_source_freshness_date_counts_as_stale():
    d_last = date(2026, 7, 15)
    assert IM.book_status(ABOUT, [], d_last, '2025-2026', 'ab' * 32, date(2026, 9, 10))[0] == 'mentat_stale'
    blank = [dict(FRESH[0], **{'Server Last Modified': None})]
    assert IM.book_status(ABOUT, blank, d_last, '2025-2026', 'ab' * 32, date(2026, 9, 10))[0] == 'mentat_stale'
    other_cycle = [dict(FRESH[0], **{'Election Cycle': '2023-2024'})]
    assert IM.book_status(ABOUT, other_cycle, d_last, '2025-2026', 'ab' * 32, date(2026, 9, 10))[0] == 'mentat_stale'


def test_projection_is_deterministic_and_carries_pm_a4_record():
    rows = [rf(), rf(tier='Chief', amount=50), rf(cycle='2023-2024', amount=9)]
    sps = [sp(), sp(cycle='2023-2024')]
    a = json.dumps(run(rows, sps), sort_keys=True); b = json.dumps(run(list(reversed(rows)), sps), sort_keys=True)
    assert a == b
    dup = IM.KNOWN_CROSS_CYCLE_DUPLICATES[0]
    assert dup['sub_ids'] == ['4030420261386079658', '4030420261386079660'] and dup['amount'] == 11175000
