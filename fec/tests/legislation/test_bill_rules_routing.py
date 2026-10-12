# -*- coding: utf-8 -*-
"""Regression tests for shared legislative category and bill-id routing."""
import os
import sys
import traceback


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

import bill_rules as BR  # noqa: E402


def test_canonical_names_route_to_themselves():
    for category in BR.CATEGORIES:
        assert BR.canonical_category(category) == category, category


def test_label_aliases_route_to_canonical_categories():
    for label in BR.LABEL_TO_CANONICAL:
        mapped = BR.canonical_category(label)
        assert mapped in BR.CATEGORIES, (label, mapped)


def test_truncated_archive_sheet_names_route_correctly():
    expected = {
        '01 Examination & Supervision': 'Examination & Supervision',
        '02 Credit Union Capital Require': 'CU Capital Requirements',
        '03 NCUA Budget & Share Insuranc': 'NCUA Budget & Insurance',
        '04 Interchange': 'Interchange',
        '05 Payments': 'Payments',
        '06 Technology & Emerging Issues': 'Technology & Emerging',
        '07 Fraud Prevention': 'Fraud Prevention',
        '08 Data Privacy & Cybersecurity': 'Data Privacy & Cyber',
        '09 Charter Modernization & Gove': 'Charter Modernization',
        '10 Defense Credit Union Issues': 'Defense CU Issues',
        '11 Field of Membership': 'Field of Membership',
        '12 CDFI Fund': 'CDFI Fund',
        '13 CFPB Issues & Structural Ref': 'CFPB',
        '14 Credit Card & Consumer-Fee M': 'Credit Card Mandates',
        '15 Telephone Consumer Protectio': 'TCPA',
        '16 Member Business Lending': 'Member Business Lending',
        '17 Mortgage Lending & Housing': 'Mortgage & Housing',
    }
    for sheet_name, category in expected.items():
        assert BR.canonical_category(sheet_name) == category, (sheet_name, category)


def test_press_only_category_is_deliberately_not_legislative():
    # Documented and deliberate: this category belongs only to press routing.
    assert BR.canonical_category('Credit Union-Bank Competitive Dynamics') is None


def test_blank_categories_do_not_route():
    assert BR.canonical_category('') is None
    assert BR.canonical_category(None) is None


def test_disapproval_drafting_styles_share_one_matcher():
    assert BR.is_disapproval('A joint resolution disapproving the rule submitted by...')
    assert BR.is_disapproval('...providing for congressional disapproval under chapter 8...')
    assert not BR.is_disapproval('A bill to improve access to financial services')


def test_collision_guard_is_quiet_today_and_flags_synthetic_collision():
    real_identifiers = ['H.R.7733', 'S.869', 'H.R.7036', 'H.R.7022', 'S.4698']
    assert BR.key_collisions(real_identifiers) == {}
    assert BR.key_collisions(['H.R.59', 'H.J.Res.59']) == {
        ('H', 59): ['H.J.Res.59', 'H.R.59']}
    assert BR.key_collisions(['H.R.59', 'H.J.59']) == {
        ('H', 59): ['H.J.59', 'H.R.59']}


def test_type_aware_key_is_additive_to_legacy_bill_key():
    assert BR.bill_type_key('H.R.59') == ('H', 'R', 59)
    assert BR.bill_type_key('H.J.Res.59') == ('H', 'JRES', 59)
    assert BR.bill_type_key('H.Res.59') == ('H', 'RES', 59)
    assert BR.bill_type_key('H.Con.Res.14') == ('H', 'CONRES', 14)
    assert BR.bill_type_key('S.557') == ('S', 'R', 557)
    assert {BR.bill_key(value) for value in
            ('H.R.59', 'H.J.Res.59', 'H.Res.59')} == {('H', 59)}


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
    print('test_bill_rules_routing: %d passed, %d failed' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
