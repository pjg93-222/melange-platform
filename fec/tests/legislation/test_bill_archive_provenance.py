# -*- coding: utf-8 -*-
"""Pure regression tests for bill-archive query provenance and C13."""
import os
import sys
import traceback


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)

from legislation import bill_archive_provenance as BAP  # noqa: E402


GOOD_NUMBERING = {
    'expected': list(range(1, 18)),
    'observed': list(range(1, 18)),
    'missing': [],
    'unexpected': [],
    'duplicates': [],
    'contiguous_01_through_17': True,
}


def _row(excel_row, detail_link, title='', summary=''):
    return {
        '_excel_row': excel_row,
        'DetailLink': detail_link,
        'Title': title,
        'bill_summary': summary,
    }


def _analysis(sheets, numbering=None):
    return {
        'sheet_order': list(sheets),
        'sheets': sheets,
        'sheet_numbering': numbering or dict(GOOD_NUMBERING),
        'totals': {},
    }


def _event(result, name):
    return next(item for item in result['events'] if item['name'] == name)


def test_decode_plain_hl():
    result = BAP.decode_hl('/bill/119/1?hl=credit+union&s=1&r=2')
    assert result['status'] == 'OK'
    assert result['raw_hl'] == 'credit+union'
    assert result['decoded_hl'] == 'credit union'


def test_decode_url_encoded_hl():
    result = BAP.decode_hl(
        '/bill/119/1?hl=%22Credit%5C+Card%5C+Competition%5C+Act%22'
    )
    assert result['status'] == 'OK'
    assert result['decoded_hl'] == '"Credit\\ Card\\ Competition\\ Act"'
    parsed = BAP.parse_query_atoms(result['decoded_hl'])
    assert parsed['query_atom_count'] == 1
    assert parsed['quoted_phrases'] == ['Credit Card Competition Act']


def test_decode_empty_and_missing_hl():
    assert BAP.decode_hl('/bill/119/1?hl=')['status'] == 'EMPTY'
    assert BAP.decode_hl('/bill/119/1?s=1')['status'] == 'MISSING'
    assert BAP.decode_hl('')['status'] == 'MISSING'


def test_decode_rejects_malformed_percent_or_utf8():
    bad_escape = BAP.decode_hl('/bill/119/1?hl=credit%ZZunion')
    bad_utf8 = BAP.decode_hl('/bill/119/1?hl=%FF')
    duplicate_parameter = BAP.decode_hl('/bill/119/1?hl=one&hl=two')
    bad_name_only = BAP.decode_hl('/bill/119/1?bad%ZZname=value')
    bad_name_with_hl = BAP.decode_hl(
        '/bill/119/1?hl=credit+union&bad%ZZname=value')
    assert bad_escape['status'] == 'MALFORMED'
    assert bad_utf8['status'] == 'MALFORMED'
    assert duplicate_parameter['status'] == 'MALFORMED'
    assert bad_name_only['status'] == 'MALFORMED'
    assert bad_name_with_hl['status'] == 'MALFORMED'
    assert 'parameter name' in bad_name_only['error']


def test_query_atom_structure():
    parsed = BAP.parse_query_atoms(
        '"Credit\\ Card" AND union NEAR/5 bare'
    )
    assert parsed['query_atom_count'] == 5
    assert parsed['whitespace_token_count'] == 6
    assert parsed['quoted_phrases'] == ['Credit Card']
    assert parsed['bare_terms'] == ['union', 'bare']
    assert parsed['operators'] == ['AND', 'NEAR/5']
    assert parsed['parse_errors'] == []


def test_grouping_tracks_full_queries_and_rows():
    sheet = BAP.analyze_sheet_rows(
        '02 Credit Union Capital Require',
        [
            _row(2, '/bill/1?hl=credit+union', 'Credit Union Act'),
            _row(3, '/bill/2?hl=credit+union', 'Capital Act'),
            _row(4, '/bill/3?s=1', 'Missing Query Act'),
        ],
    )
    assert sheet['rows_total'] == 3
    assert sheet['rows_decodable_hl'] == 2
    assert sheet['rows_missing_hl'] == 1
    assert sheet['distinct_full_hl_query_count'] == 1
    assert sheet['query_list'][0]['rows'] == [2, 3]
    assert sheet['query_list'][0]['row_count'] == 2


def test_truncated_query_makes_distinct_count_a_lower_bound():
    oversized = 'a' * (BAP.MAX_DECODED_HL_CHARS + 1)
    sheet = BAP.analyze_sheet_rows(
        '02 Credit Union Capital Require',
        [_row(2, '/bill/1?hl=' + oversized, 'Credit Union Act')],
    )
    assert sheet['rows_truncated_hl'] == 1
    assert sheet['query_catalog_overflow_rows'] == 0
    assert sheet['distinct_full_hl_query_count_is_lower_bound'] is True


def test_markdown_query_text_is_inert_but_json_value_stays_raw():
    sheet = BAP.analyze_sheet_rows(
        'All Bills',
        [_row(2, '/bill/1?hl=%60%60%60%0Aline%01', 'Control Act')],
    )
    raw_query = sheet['query_list'][0]['decoded_query']
    assert raw_query == '```\nline\x01'
    analysis = _analysis({sheet['sheet_name']: sheet})
    analysis['totals'] = {
        'all_bills_rows_total': sheet['rows_total'],
        'all_bills_rows_nonempty_hl': sheet['rows_nonempty_hl'],
        'all_bills_rows_decodable_hl': sheet['rows_decodable_hl'],
        'all_bills_rows_missing_hl': sheet['rows_missing_hl'],
        'all_bills_rows_malformed_hl': sheet['rows_malformed_hl'],
        'all_bills_nonempty_hl_delta': 0,
        'numbered_sheets_rows_total': 0,
        'numbered_sheets_rows_nonempty_hl': 0,
        'numbered_sheets_rows_decodable_hl': 0,
        'numbered_sheets_rows_missing_hl': 0,
        'numbered_sheets_rows_malformed_hl': 0,
        'numbered_sheets_nonempty_hl_delta': 0,
    }
    document = BAP.build_document(
        analysis,
        {
            'scratch_name': 'fixture.xlsx',
            'sha256': 'fixture-sha256',
            'bytes': 1,
        },
    )
    markdown = BAP.render_markdown(document)
    assert '\\x60\\x60\\x60\\nline\\x01' in markdown
    assert raw_query not in markdown
    assert '\x01' not in markdown


def test_zero_yield_quoted_phrase_is_artifact_local():
    sheet = BAP.analyze_sheet_rows(
        '04 Interchange',
        [
            _row(
                2,
                '/bill/1?hl=%22Credit%5C+Card%22+%22Ghost%5C+Phrase%22',
                'Credit Card Competition Act',
                'Competition in payment card routing.',
            ),
        ],
    )
    suspects = sheet['suspect_zero_yield']
    assert [item['phrase'] for item in suspects] == ['Ghost Phrase']
    assert suspects[0]['status'] == 'SUSPECT-ZERO-YIELD'
    phrase_results = sheet['query_list'][0]['quoted_phrase_yields']
    assert {
        item['phrase']: item['artifact_row_hits'] for item in phrase_results
    } == {'Credit Card': 1, 'Ghost Phrase': 0}


def test_c13_warns_on_atoms_not_repeated_full_queries():
    capital = BAP.analyze_sheet_rows(
        '02 Credit Union Capital Require',
        [
            _row(2, '/bill/1?hl=credit+union', 'Credit Union Act'),
            _row(3, '/bill/2?hl=credit+union', 'Capital Act'),
        ],
    )
    interchange = BAP.analyze_sheet_rows(
        '04 Interchange',
        [
            _row(
                2,
                '/bill/3?hl=%22Credit%5C+Card%5C+Competition%5C+Act%22',
                'Credit Card Competition Act',
            ),
        ],
    )
    result = BAP.evaluate_c13(
        _analysis({
            capital['sheet_name']: capital,
            interchange['sheet_name']: interchange,
        }),
        canonical_categories=('CU Capital Requirements', 'Interchange'),
        known_missing=(),
    )
    assert result['coverage_by_sheet'][capital['sheet_name']]['status'] == 'PASS'
    assert capital['distinct_full_hl_query_count'] == 1
    assert capital['query_list'][0]['row_count'] == 2
    assert [item['category'] for item in result['single_atom_queries']] == [
        'Interchange'
    ]


def test_c13_sheet_numbering_decision():
    sheet = BAP.analyze_sheet_rows(
        '01 Examination & Supervision',
        [_row(2, '/bill/1?hl=examination+supervision', 'Exam Act')],
    )
    clean = BAP.evaluate_c13(
        _analysis({sheet['sheet_name']: sheet}),
        canonical_categories=('Examination & Supervision',),
        known_missing=(),
    )
    assert _event(
        clean, 'C13 numbered archive sheets are contiguous 01..17'
    )['status'] == 'PASS'
    broken_numbering = dict(GOOD_NUMBERING)
    broken_numbering.update({
        'observed': list(range(1, 17)),
        'missing': [17],
        'contiguous_01_through_17': False,
    })
    broken = BAP.evaluate_c13(
        _analysis({sheet['sheet_name']: sheet}, broken_numbering),
        canonical_categories=('Examination & Supervision',),
        known_missing=(),
    )
    assert _event(
        broken, 'C13 numbered archive sheet sequence is broken'
    )['status'] == 'FAIL'


def test_c13_query_set_allowlist_decisions():
    exam = BAP.analyze_sheet_rows(
        '01 Examination & Supervision',
        [_row(2, '/bill/1?hl=examination+supervision', 'Exam Act')],
    )
    documented = BAP.evaluate_c13(
        _analysis({exam['sheet_name']: exam}),
        canonical_categories=('Examination & Supervision', 'CDFI Fund'),
        known_missing=('CDFI Fund',),
    )
    assert documented['query_sets']['documented_missing'] == ['CDFI Fund']
    assert documented['query_sets']['unexpected_missing'] == []
    assert _event(
        documented, 'C13 no undocumented canonical query-set gaps'
    )['status'] == 'PASS'

    unexpected = BAP.evaluate_c13(
        _analysis({exam['sheet_name']: exam}),
        canonical_categories=(
            'Examination & Supervision',
            'CDFI Fund',
            'Payments',
        ),
        known_missing=('CDFI Fund',),
    )
    assert unexpected['query_sets']['unexpected_missing'] == ['Payments']
    assert _event(
        unexpected, 'C13 canonical categories with undocumented query gaps'
    )['status'] == 'FAIL'

    cdfi = BAP.analyze_sheet_rows(
        '12 CDFI Fund',
        [_row(2, '/bill/2?hl=cdfi+fund', 'CDFI Fund Act')],
    )
    stale = BAP.evaluate_c13(
        _analysis({
            exam['sheet_name']: exam,
            cdfi['sheet_name']: cdfi,
        }),
        canonical_categories=('Examination & Supervision', 'CDFI Fund'),
        known_missing=('CDFI Fund',),
    )
    assert stale['query_sets']['stale_allowlist'] == ['CDFI Fund']
    assert _event(
        stale, 'C13 query-set zero allowlist entries are stale'
    )['status'] == 'WARN'


def main():
    tests = [
        function for name, function in sorted(globals().items())
        if name.startswith('test_') and callable(function)
    ]
    passed = failed = 0
    for function in tests:
        try:
            function()
            passed += 1
            print('PASS %s' % function.__name__)
        except BaseException:
            failed += 1
            print('FAIL %s' % function.__name__)
            traceback.print_exc()
    print(
        'test_bill_archive_provenance: %d passed, %d failed'
        % (passed, failed)
    )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
