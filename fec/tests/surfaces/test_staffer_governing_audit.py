from datetime import date

import pytest

from fec.staffer_network.closeness_math import ExposureIndex
from fec.staffer_network.relationship_uncertainty import uncertainty
from fec.surfaces.staffer_routing import AdvocacyRouter
from fec.tests.surfaces.test_staffer_payroll_promotion import make, payroll
from fec.tests.surfaces.test_staffer_work_time_routes import episode, person


def test_unrelated_report_window_does_not_relabel_documented_overlap():
    older = {**payroll('a', precision='REPORT_PERIOD'), 'start': '2000-01-01', 'end_exclusive': '2001-01-01'}
    route = make([older, payroll('a'), payroll('b')]).route('a', 'b', {'objective': 'work_time'})
    hop = route['hops'][0]
    assert not hop['modeled_exposure']
    assert [r['start'] for r in hop['provenance']['source_evidence']['a']] == ['2020-01-01']


def test_internship_census_rows_do_not_appear_as_route_evidence():
    internship = {**payroll('a', title='INTERN', precision='REPORT_PERIOD'), 'start': '2000-01-01', 'end_exclusive': '2001-01-01'}
    route = make([internship, payroll('a'), payroll('b')]).route('a', 'b', {'objective': 'work_time'})
    assert route['work_source_coverage']['episodes_by_source']['payroll_trajectory'] == 2
    assert not route['hops'][0]['modeled_exposure']


def test_future_payroll_does_not_create_a_present_route():
    rows = [{**payroll(p), 'start': '2030-01-01', 'end_exclusive': '2031-01-01'} for p in 'ab']
    result = make(rows).route('a', 'b', {'objective': 'work_time'})
    assert result['status'] != 'found'
    rows = [{**payroll(p), 'start': '2026-09-01', 'end_exclusive': '2031-01-01'} for p in 'ab']
    result = make(rows).route('a', 'b', {'objective': 'work_time'})
    assert result['hops'][0]['overlap_duration_days'] == 10
    assert result['hops'][0]['provenance']['source_evidence']['a'][0]['source_end_exclusive'] == '2031-01-01'


def test_null_node_metadata_does_not_erase_payroll_workplace_type():
    nodes = {p: person(p.upper()) for p in 'ab'}
    nodes['office:one'] = {'kind': 'container', 'container_kind': None, 'chamber': None}
    route = AdvocacyRouter(nodes, [payroll('a'), payroll('b')], 'test', '2026-09-10').route('a', 'b', {'objective': 'work_time'})
    assert route['hops'][0]['workplaces'][0]['kappa'] == 1


def test_brief_return_to_same_office_does_not_refresh_old_exposure():
    rows = [episode(p, 'office', s, e) for p in 'ab' for s, e in [('2000-01-01', '2010-01-01'), ('2026-09-09', '2026-09-10')]]
    index = ExposureIndex(rows)
    pair = index.pair('a', 'b')
    risk = uncertainty(pair['workplaces'], '2026-09-10')
    assert risk['weighted_years_since_overlap'] > 16
    assert pair['workplaces'][0]['last_overlap_day'] == date(2026, 9, 9).toordinal()
    assert index.office_uncertainty('a', 'office', '2026-09-10')['weighted_years_since_overlap'] > 16


def test_headcount_changes_do_not_split_a_continuous_contact_for_recency():
    rows = [episode(p, 'office', '2020-01-01', '2026-09-10') for p in 'ab']
    rows.append(episode('c', 'office', '2020-01-01', '2022-01-01'))
    risk = uncertainty(ExposureIndex(rows).pair('a', 'b')['workplaces'], '2026-09-10')
    assert risk['weighted_years_since_overlap'] == pytest.approx(1 / 365.2425)


def test_installer_covers_its_staffer_python_dependencies():
    import ast
    from pathlib import Path
    from fec.surfaces.install_staffer_live import FILES
    root = Path(__file__).resolve().parents[3]
    files = set(FILES)
    assert len(files) == len(FILES)
    for filename in files:
        assert (root / filename).is_file(), filename
        if not filename.endswith('.py') or '/test' in filename:
            continue
        package = filename[:-3].split('/')[:-1]
        for node in ast.walk(ast.parse((root / filename).read_text(encoding='utf-8-sig'))):
            if not isinstance(node, ast.ImportFrom):
                continue
            parts = (package[:len(package)-node.level+1] + (node.module.split('.') if node.module else [])) if node.level else (node.module or '').split('.')
            for pieces in [parts, *[parts + [a.name] for a in node.names]]:
                dependency = '/'.join(pieces) + '.py'
                if dependency.startswith(('fec/surfaces/staffer', 'fec/staffer_network/')) and (root / dependency).is_file():
                    assert dependency in files, (filename, dependency)
