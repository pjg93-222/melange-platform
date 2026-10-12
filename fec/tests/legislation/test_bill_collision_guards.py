# -*- coding: utf-8 -*-
"""Pure regression fixtures for D3 collision guards and candidate expansion."""
import ast
import csv
import hashlib
import json
import os
import sys
import tempfile
import traceback


FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

from legislation import bill_coverage_audit as BCA  # noqa: E402
import bill_rules as BR  # noqa: E402
from legislation import leg_evidence_recompute as LER  # noqa: E402
from legislation import make_bill_directions_congress_candidate as CAND  # noqa: E402


def _archive_row(name, congress, title, section='CFPB'):
    return {
        'Name': name,
        'Congress': congress,
        'Title': title,
        'Section': section,
    }


def _direction(bill, source='fixture.csv', locator=2, value=1):
    return {
        'source': source,
        'locator': locator,
        'bill': bill,
        'direction': value,
    }


def _builder_direction_functions():
    """Load only the pure registry helpers; importing the builder executes a build."""
    path = os.path.join(FEC, 'legislation', 'build_archive_legislative.py')
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), filename=path)
    wanted = {
        'billkey',
        'load_direction_registry',
        'direction_for',
        'direction_registry_path',
        'legacy_direction_enabled',
    }
    definitions = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]
    assert {node.name for node in definitions} == wanted
    module = ast.Module(body=definitions, type_ignores=[])
    namespace = {'BR': BR, 'csv': csv, '_o': os}
    exec(compile(module, path, 'exec'), namespace)
    return namespace


def _registry_rows():
    with open(os.path.join(ROOT, 'data', 'legislation', 'bill_directions.csv'),
              encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def _wiring_report():
    with open(os.path.join(ROOT, 'audits', 'legfix3', 'wiring_report.json'),
              encoding='utf-8') as handle:
        return json.load(handle)


def test_canonical_category_exhaustive_identity_and_numbered_prefixes():
    assert len(BR.CATEGORIES) == 18
    for index, category in enumerate(BR.CATEGORIES, start=1):
        assert BR.canonical_category(category) == category, category
        keyword = dict(BR.SECTION_KEYWORDS)[category]
        numbered = '%02d %s' % (index, keyword.swapcase())
        assert BR.canonical_category(numbered) == category, numbered


def test_canonical_category_aliases_case_variants_and_junk():
    for alias, category in BR.LABEL_TO_CANONICAL.items():
        assert BR.canonical_category(alias) == category, alias
    case_variants = {
        'pAyMeNtS': 'Payments',
        'CFPB ISSUES & STRUCTURAL REFORM': 'CFPB',
        'credit union tax status': 'CU Tax Status',
        'tElEpHoNe CoNsUmEr PrOtEcTiOn AcT iSsUeS': 'TCPA',
    }
    for value, category in case_variants.items():
        assert BR.canonical_category(value) == category, value
    for value in ('', None, 'totally unrelated', '???', 12345):
        assert BR.canonical_category(value) is None, value


def test_bill_type_key_edge_cases():
    assert BR.bill_type_key('H.Con.Res.14') == ('H', 'CONRES', 14)
    assert BR.bill_type_key('S.Con.Res.7') == ('S', 'CONRES', 7)
    assert BR.bill_type_key('HJRES59') == ('H', 'JRES', 59)
    assert BR.bill_type_key('S919') == ('S', 'R', 919)
    for value in (None, '', 'H.X.12', 'LTR-TEST', 'H.R.'):
        assert BR.bill_type_key(value) is None, value


def test_bill_type_key_documents_informal_s_r_limitation():
    # Known limitation: S.R.NNN collapses to plain S.NNN.  Use S.Res.NNN.
    assert BR.bill_type_key('S.R.12') == BR.bill_type_key('S.12')
    assert BR.bill_type_key('S.R.12') == ('S', 'R', 12)
    assert BR.bill_type_key('S.Res.12') == ('S', 'RES', 12)


def test_key_collisions_fail_closed_for_untyped_legacy_ids():
    result = BR.key_collisions(['H.R.59', 'H.J.59'])
    assert result == {('H', 59): ['H.J.59', 'H.R.59']}


def test_evidence_numeric_poison_never_matches_zero():
    invalid_values = (
        float('nan'),
        float('inf'),
        float('-inf'),
        'NaN',
        'Infinity',
        '#DIV/0!',
        None,
    )
    for value in invalid_values:
        comparison = LER.compare_member_maps(
            {'A': value},
            {'A': 0.0},
            ['A'],
        )
        assert comparison['verdict'] == 'DATA-INVALID', value
        assert comparison['data_invalid'], value


def test_evidence_flag_precedence():
    assert LER.all_flags_verdict(
        ['DRIFT', 'NOT-RECOMPUTABLE-OFFLINE']
    ) == 'DRIFT'
    assert LER.all_flags_verdict(
        ['DATA-INVALID', 'DRIFT', 'NOT-RECOMPUTABLE-OFFLINE']
    ) == 'DATA-INVALID'
    assert LER.all_flags_verdict(
        ['MATCH', 'NOT-RECOMPUTABLE-OFFLINE']
    ) == 'NOT-RECOMPUTABLE-OFFLINE'


def test_e16_primary_weight_cannot_be_reproduced_by_variant_hypothesis():
    result = LER.e16_weight_reconciliation(
        1231.84,
        10000.0,
        {
            'all_dispositions_absolute': 1416.02,
            'all_dispositions_signed': 1200.0,
        },
    )
    assert result['primary_method'] == 'selected_only_absolute_p9_exact'
    assert result['primary_weight'] == 1231.84
    assert result['weight_flag'] == 'DRIFT'
    assert result['weight_pct_flag'] == 'DRIFT'
    assert result['status'] == 'DRIFT'
    assert result['attempts_metadata']['hypothesis_for_reported_scope'] == (
        'all_dispositions_absolute'
    )
    assert result['attempts_metadata']['hypothesis_weight'] == 1416.02
    assert not any(
        key.startswith('reproduced') or key.startswith('reproducing')
        for key in result
    )
    assert not any(
        key.startswith('reproduced') or key.startswith('reproducing')
        for key in result['attempts_metadata']
    )


def test_e19c_near_miss_bands_and_current_cu_cw_residuals():
    assert LER.e19c_unidentified_verdict(
        0.5, aggregate_like=True, percent_scale=True
    ) == 'NEAR-MISS-DIVERGENT'
    assert LER.e19c_unidentified_verdict(
        0.05, aggregate_like=False, percent_scale=False
    ) == 'NEAR-MISS-DIVERGENT'
    assert LER.e19c_unidentified_verdict(
        0.500001, aggregate_like=True, percent_scale=True
    ) == 'NOT-RECOMPUTABLE-OFFLINE'
    assert LER.e19c_unidentified_verdict(
        0.050001, aggregate_like=False, percent_scale=False
    ) == 'UNIDENTIFIED-RAW-MEASURE'
    assert LER.e19c_unidentified_verdict(
        0.64, aggregate_like=False, percent_scale=True
    ) == 'UNIDENTIFIED-RAW-MEASURE'
    assert LER.e19c_unidentified_verdict(
        0.8, aggregate_like=False, percent_scale=False
    ) == 'UNIDENTIFIED-RAW-MEASURE'
    assert LER.e19c_part_verdict(
        [],
        [{'verdict': 'NEAR-MISS-DIVERGENT'}],
        [],
    ) == 'MISMATCH'


def test_e19d_new_weight_or_structural_findings_are_nonmatching():
    required = [
        {'cell': cell, 'observed_mismatch': True}
        for cell in ('P2', 'X2', 'DG2')
    ]
    known_weights = [
        {'raw_cell': cell, 'normalized_cell': cell}
        for cell in ('P2', 'X2', 'DG2')
    ]
    known_structure = [{'finding_key': 'CP1'}]
    confirmed = LER.e19d_classify_findings(
        True, required, known_weights, known_structure, [])
    assert confirmed['verdict'] == 'FINDINGS-CONFIRMED'
    new_weight = LER.e19d_classify_findings(
        True,
        required,
        known_weights + [{'raw_cell': 'EF2', 'normalized_cell': 'EF2'}],
        known_structure,
        [],
    )
    assert new_weight['verdict'] == 'NEW-FINDINGS'
    assert new_weight['new_findings'][0]['raw_cell'] == 'EF2'
    new_structure = LER.e19d_classify_findings(
        True,
        required,
        known_weights,
        known_structure + [{'finding_key': 'CQ1'}],
        [],
    )
    assert new_structure['verdict'] == 'NEW-FINDINGS'
    assert new_structure['new_findings'][0]['finding_key'] == 'CQ1'
    assert LER.e19_overall_verdict({
        'E19a': 'MATCH',
        'E19b': 'MATCH',
        'E19c': 'MATCH',
        'E19d': 'NEW-FINDINGS',
    }) == 'MISMATCH'


def test_c14_collision_unkeyable_and_clean_decisions():
    collision_archive = [
        _archive_row('S.919', 117, 'Data Care Act of 2021',
                     'Data Privacy & Cybersecurity'),
        _archive_row('S.919', 119, 'GENIUS Act 2025',
                     'Technology & Emerging Issues'),
    ]
    collision = BCA.evaluate_c14(
        [_direction('S919', source='data/legislation/cu_bill_directions.json', locator='S919')],
        collision_archive,
    )
    assert len(collision['collisions']) == 1
    assert collision['collisions'][0]['bill_type_key'] == ['S', 'R', 919]
    assert [item['congress'] for item in
            collision['collisions'][0]['instances']] == ['117', '119']

    unkeyable = BCA.evaluate_c14(
        [_direction('H.Con.Res.14')],
        [_archive_row('H.Con.Res.14', 119, 'Concurrent Resolution 14')],
    )
    assert len(unkeyable['unkeyable_directions']) == 1
    assert unkeyable['archive_rows_bill_key_unkeyable'] == 1

    clean = BCA.evaluate_c14(
        [_direction('H.R.7')],
        [_archive_row('H.R.7', 119, 'Unique Act')],
    )
    assert clean['collisions'] == []
    assert clean['unkeyable_directions'] == []


def test_c14_clean_pass_requires_three_nonempty_direction_sources():
    clean = BCA.evaluate_c14(
        [_direction('H.R.7')],
        [_archive_row('H.R.7', 119, 'Unique Act')],
    )
    degraded_sources = {
        'data/legislation/bill_directions.csv': {
            'status': 'PRESENT',
            'direction_rows': 1,
        },
        'data/legislation/cu_bill_directions.json': {
            'status': 'PRESENT',
            'direction_rows': 0,
        },
        'data/stance/cu_valence_registry.json': {
            'status': 'MISSING',
            'direction_rows': 0,
        },
    }
    degraded = BCA.c14_clean_state(clean, degraded_sources)
    assert degraded['status'] == 'WARN'
    assert degraded['name'] == (
        'C14 no collisions found BUT 2 direction source(s) unavailable '
        '\u2014 PASS is vacuous'
    )
    # Alphabetical source-name order moved with the Phase 2 path prefix.
    assert degraded['unavailable_sources'] == [
        {'source': 'data/legislation/cu_bill_directions.json', 'status': 'EMPTY'},
        {'source': 'data/stance/cu_valence_registry.json', 'status': 'MISSING'},
    ]
    complete_sources = {
        name: {'status': 'PRESENT', 'direction_rows': 1}
        for name in degraded_sources
    }
    complete = BCA.c14_clean_state(clean, complete_sources)
    assert complete['status'] == 'PASS'
    assert complete['unavailable_sources'] == []


def test_c14_warns_on_informal_senate_style_direction_ids():
    result = BCA.evaluate_c14(
        [
            _direction('S.R.12'),
            _direction(' S R 13', locator=3),
            _direction('S.14', locator=4),
            _direction('S.Res.15', locator=5),
        ],
        [],
    )
    assert [
        row['bill'] for row in result['informal_senate_style_directions']
    ] == ['S.R.12', ' S R 13']


def test_c14_lists_same_title_reintroduction_class():
    same_title = BCA.evaluate_c14(
        [_direction('H.R.166')],
        [
            _archive_row('H.R.166', 117, 'Fair Lending for All Act'),
            _archive_row('H.R.166', 118, ' Fair Lending   for All Act '),
        ],
    )
    assert len(same_title['collisions']) == 1
    assert same_title['collisions'][0]['same_title'] is True
    assert [item['congress'] for item in
            same_title['collisions'][0]['instances']] == ['117', '118']
    assert same_title['collisions'][0]['titles_empty'] is False


def test_c14_lists_empty_title_reintroduction_class():
    empty_title = BCA.evaluate_c14(
        [_direction('H.R.166')],
        [
            _archive_row('H.R.166', 117, ''),
            _archive_row('H.R.166', 118, None),
        ],
    )
    assert len(empty_title['collisions']) == 1
    assert empty_title['collisions'][0]['same_title'] is True
    assert empty_title['collisions'][0]['titles_empty'] is True


def test_c14_warns_when_legacy_key_parses_but_typed_key_does_not():
    result = BCA.evaluate_c14(
        [_direction('H.J.14'), _direction('S.J.100', locator=3)],
        [],
    )
    assert [row['bill'] for row in result['legacy_keyable_unparsed']] == [
        'H.J.14',
        'S.J.100',
    ]
    assert result['legacy_keyable_unparsed'][0]['legacy_bill_key'] == ['H', 14]
    assert all('collision risk' in row['risk'] for row in
               result['legacy_keyable_unparsed'])


def test_c14_scans_valence_families_without_inventing_bill_ids():
    rows, stats = BCA.direction_rows_from_documents(
        [],
        {'entries': [{'key': 'stablecoin', 'valence': 1}]},
        {'bills': {}},
    )
    assert stats['data/stance/cu_valence_registry.json']['direction_rows'] == 1
    result = BCA.evaluate_c14(rows, [])
    assert result['collisions'] == []
    assert result['unkeyable_directions'] == []
    assert result['unparsed_directions'][0]['bill'] == 'stablecoin'
    assert result['unparsed_directions'][0]['identifier_field'] == 'key'


def test_c15_disagree_agree_and_single_instance_decisions():
    disagree = BCA.evaluate_c15([
        _archive_row('H.R.1', 118, 'Shared  Act', 'CFPB'),
        _archive_row('S.2', 119, 'shared act', 'Payments'),
    ])
    assert disagree['multi_number_acts'] == 1
    assert disagree['disagreeing_acts'] == 1
    assert disagree['rows_involved'] == 2
    assert len(disagree['worst_examples']) == 1

    agree = BCA.evaluate_c15([
        _archive_row('H.R.1', 118, 'Shared Act', 'CFPB'),
        _archive_row('S.2', 119, 'Shared Act',
                     'CFPB Issues & Structural Reform'),
    ])
    assert agree['multi_number_acts'] == 1
    assert agree['disagreeing_acts'] == 0

    single = BCA.evaluate_c15([
        _archive_row('H.R.1', 119, 'Only Act', 'CFPB'),
        _archive_row('H.R.1', 119, 'Only Act', 'CFPB'),
    ])
    assert single['multi_number_acts'] == 0
    assert single['disagreeing_acts'] == 0

    blank = BCA.evaluate_c15([
        _archive_row('H.R.1', 119, '', 'CFPB'),
        _archive_row('S.2', 119, '   ', 'Payments'),
    ])
    assert blank['blank_title_rows'] == 2
    assert blank['multi_number_acts'] == 0
    assert blank['disagreeing_acts'] == 0


def test_c15_folds_unicode_apostrophes_and_punctuation():
    curly = "Federal Home Loan Banks\u2019 Mission Implementation Act"
    straight = "federal home loan banks' mission implementation act"
    assert BCA.normalized_title(curly) == BCA.normalized_title(straight)
    result = BCA.evaluate_c15([
        _archive_row('H.R.3323', 117, curly, 'CFPB'),
        _archive_row('H.R.3323', 118, straight,
                     'CFPB Issues & Structural Reform'),
    ])
    assert result['multi_number_acts'] == 1
    assert result['disagreeing_acts'] == 0


def test_congress_normalization_rejects_non_integral_values():
    assert BCA.congress_text(119) == '119'
    assert BCA.congress_text('118.0') == '118'
    for value in (
        None, 'junk', '118.5', float('nan'), float('inf'), float('-inf')
    ):
        assert BCA.congress_text(value) == BCA.NON_INTEGRAL_CONGRESS


def test_candidate_unique_ambiguous_absent_resolution():
    source_rows = [
        {'Bill': 'H.R.7', 'Direction': '1'},
        {'Bill': 'S.919', 'Direction': '1'},
        {'Bill': 'S.404', 'Direction': '-1'},
    ]
    archive_rows = [
        _archive_row('H.R.7', 119, 'Unique Act'),
        _archive_row('S.919', 117, 'Data Care Act of 2021'),
        _archive_row('S.919', 119, 'GENIUS Act 2025'),
    ]
    rows, counts = CAND.resolve_candidate_rows(source_rows, archive_rows)
    assert counts == {
        'ARCHIVE_UNIQUE': 1,
        'AMBIGUOUS': 1,
        'AMBIGUOUS-TITLES': 0,
        'ABSENT': 1,
    }
    assert [(row['Bill'], row['Congress'], row['Resolution']) for row in rows] == [
        ('H.R.7', '119', 'ARCHIVE_UNIQUE'),
        ('S.919', '117', 'AMBIGUOUS'),
        ('S.919', '119', 'AMBIGUOUS'),
        ('S.404', '', 'ABSENT'),
    ]


def test_candidate_same_congress_distinct_titles_are_ambiguous():
    rows, counts = CAND.resolve_candidate_rows(
        [{'Bill': 'H.R.7', 'Direction': '1'}],
        [
            _archive_row('H.R.7', 119, 'Alpha Act'),
            _archive_row('H.R.7', 119, 'Beta Act'),
            _archive_row('H.R.7', 119, '  alpha   act  '),
        ],
    )
    assert counts == {
        'ARCHIVE_UNIQUE': 0,
        'AMBIGUOUS': 0,
        'AMBIGUOUS-TITLES': 1,
        'ABSENT': 0,
    }
    assert [
        (row['Congress'], row['Resolution'])
        for row in rows
    ] == [
        ('119', 'AMBIGUOUS-TITLES'),
        ('119', 'AMBIGUOUS-TITLES'),
    ]


def test_candidate_csv_fields_are_formula_neutralized():
    assert [
        CAND.neutralize_csv_field(value)
        for value in ('=SUM(A1:A2)', '+1', '-1', '@name', 'plain', None)
    ] == [
        "'=SUM(A1:A2)",
        "'+1",
        "'-1",
        "'@name",
        'plain',
        '',
    ]
    assert 'fields beginning = + - @' in CAND.USER_GATE_COMMENT
    assert 'single quote' in CAND.USER_GATE_COMMENT


def test_direction_registry_exact_blank_fallback_and_type_separation():
    functions = _builder_direction_functions()
    load_registry = functions['load_direction_registry']
    direction_for = functions['direction_for']
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, 'data', 'legislation', 'bill_directions.csv')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle, lineterminator='\n')
            writer.writerow(
                ['Bill', 'Congress', 'Category', 'Direction', 'Note'])
            writer.writerow(['H.J.Res.59', '119', '', '1', 'fixture'])
            writer.writerow(['H.R.59', '119', '', '-1', 'fixture'])
            writer.writerow(['H.R.60', '', '', '1', 'fixture'])
        registry = load_registry(path)
    assert direction_for(registry, 'H.J.Res.59', 119) == (
        '1', 'registry-exact')
    assert direction_for(registry, 'H.R.59', 119) == (
        '-1', 'registry-exact')
    assert direction_for(registry, 'H.R.60', 117) == (
        '1', 'registry-blank')
    assert direction_for(registry, 'H.J.Res.60', 117) == (None, None)


def test_legdir_v1_fixed_path_and_legacy_parity():
    functions = _builder_direction_functions()
    path_for = functions['direction_registry_path']
    legacy_enabled = functions['legacy_direction_enabled']
    load_registry = functions['load_direction_registry']
    direction_for = functions['direction_for']
    backup = os.path.join(
        ROOT, 'backups', 'bill_directions.PRE-LEGFIX3.csv')
    assert path_for(ROOT, True) == backup
    assert path_for(ROOT, False) == os.path.join(
        ROOT, 'data', 'legislation', 'bill_directions.csv')
    assert legacy_enabled({'LEGDIR_V1': '1'}) is True
    assert legacy_enabled({'LEGDIR_V1': 'true'}) is False
    assert legacy_enabled({}) is False
    with open(backup, 'rb') as handle:
        assert hashlib.sha256(handle.read()).hexdigest() == (
            'b7a1e0f8d331feda30727a9bd247c22e698d10f051d38b4414a7b5a374a3007e')
    expected = {}
    with open(backup, encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            key = BR.bill_key(row['Bill'])
            if key:
                expected[key] = row['Direction'].strip()
    legacy = load_registry(backup, legacy=True)
    assert legacy == expected
    assert direction_for(
        legacy, 'H.R.59', 119, legacy=True) == (
            expected[BR.bill_key('H.J.Res.59')], 'legacy-registry')


def test_wiring_conflicts_keep_the_earlier_source():
    rows = _registry_rows()
    report = _wiring_report()
    assert len(report['precedence_decisions']) == 47
    source_for_note = {
        'migrated from cfpb_bill_directions.csv': 'existing',
        'cu_bill_directions 2026-07-25': 'cu_bill_directions',
        'queue-FILLED 2026-07-25': 'queue',
    }
    by_identity = {}
    for row in rows:
        identity = (
            BR.bill_type_key(row['Bill']),
            BR._congress(row.get('Congress')),
        )
        assert identity not in by_identity
        by_identity[identity] = row
    for decision in report['precedence_decisions']:
        identity = (
            BR.bill_type_key(decision['bill']),
            BR._congress(decision.get('congress')),
        )
        kept = by_identity[identity]
        assert source_for_note[kept['Note']] == decision['kept_source']
        assert int(kept['Direction']) == decision['kept_direction']
        assert decision['kept_direction'] == decision['dropped_direction']


def test_wiring_quarantines_fail_closed_before_precedence():
    rows = _registry_rows()
    report = _wiring_report()
    identities = {
        (BR.bill_type_key(row['Bill']), BR._congress(row.get('Congress')))
        for row in rows
    }
    assert len(report['quarantines']) == 7
    for quarantine in report['quarantines']:
        identity = (
            BR.bill_type_key(quarantine['bill']),
            BR._congress(quarantine['congress']),
        )
        assert identity not in identities
        assert quarantine['known_title']
        assert quarantine['archive_titles']
    assert (BR.bill_type_key('S.919'), '117') not in identities
    assert (BR.bill_type_key('S.919'), '119') in identities


def test_c14_congress_filled_rows_clear_only_their_cross_congress_warning():
    archive = [
        _archive_row('S.919', 117, 'Data Care Act of 2021'),
        _archive_row('S.919', 119, 'GENIUS Act of 2025'),
    ]
    rows, _ = BCA.direction_rows_from_documents(
        [{'Bill': 'S.919', 'Congress': '119', 'Direction': '1'}],
        {},
        {},
    )
    assert rows[0]['congress'] == '119'
    filled = BCA.evaluate_c14(rows, archive)
    assert filled['collisions'] == []
    blank = BCA.evaluate_c14([_direction('S.919')], archive)
    assert len(blank['collisions']) == 1


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
        'test_bill_collision_guards: %d passed, %d failed'
        % (passed, failed)
    )
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
