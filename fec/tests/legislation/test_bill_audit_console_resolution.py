# -*- coding: utf-8 -*-
"""Regression tests for audit-console bill resolution."""
import os
import sys
import traceback


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from legislation import bill_audit_console as BAC  # noqa: E402
import bill_rules as BR  # noqa: E402


def _row(name, title):
    return {
        'Name': name,
        'Congress': '119',
        'Title': title,
        'bill_summary': '',
        '_source_category': 'Payments',
        'Role': 'cosponsor',
        'DecayWeight': 1.0,
    }


def _override(row, include):
    return {
        (BR.bill_key(row['Name']), row['Congress']): {
            'Include': include,
        },
    }


def test_title_exclusion_refuses_include_override_rescue():
    phrase = BR.EXCLUDED_TITLE_PHRASES[0]
    row = _row('H.R.9901', 'An Act concerning %s' % phrase)
    result = BAC.resolved(row, _override(row, '1'), {})
    assert result['include'] is False


def test_unexcluded_bill_honors_exclude_override():
    row = _row('H.R.9902', 'Financial Services Access Act')
    result = BAC.resolved(row, _override(row, '0'), {})
    assert result['include'] is False
    assert result['reason'].startswith('excluded by override')


def test_unexcluded_bill_without_override_is_included():
    row = _row('H.R.9903', 'Financial Services Access Act')
    result = BAC.resolved(row, {}, {})
    assert result['include'] is True


def test_excluded_reason_names_phrase_and_precedence():
    phrase = BR.EXCLUDED_TITLE_PHRASES[0]
    row = _row('H.R.9904', 'An Act concerning %s' % phrase)
    result = BAC.resolved(row, _override(row, '1'), {})
    assert phrase in result['reason']
    assert 'exclusion precedes override' in result['reason']


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
    print('test_bill_audit_console_resolution: %d passed, %d failed' % (passed, failed))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
