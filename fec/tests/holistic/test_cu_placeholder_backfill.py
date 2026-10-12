# -*- coding: utf-8 -*-
"""CU placeholder-link backfill (CU_PLACEHOLDER_BACKFILL, 2026-10-11).

The 06-25 CU scrape gave 172 archive rows the glossary link '/help/legislative-glossary#...' as
DetailLink; Stage 3 deduped sponsored bills by link and 111 bills lost every cosponsor edge.
Checks: the builder's pure rules (lost-bill selection, Stage 3 weighting, identity refusal), the
kernel merge (same-direction SUM, new pairs appended, dial off = file rows exactly, fail-closed),
and that all four CU base readers route through moneyball_core.cu_edge_rows.
"""
import json
import math
import os
import sys
import tempfile

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(FEC)
sys.path.insert(0, FEC)

import openpyxl  # noqa: E402
import moneyball_core as MC  # noqa: E402
from network import build_cu_placeholder_backfill as BF  # noqa: E402

PH = '/help/legislative-glossary#glossary_billsummary'
REAL = 'https://www.congress.gov/bill/118th-congress/house-bill/7?s=1'


def _env(name, value):
    old = os.environ.get(name)
    if value is None: os.environ.pop(name, None)
    else: os.environ[name] = value
    return old


def test_placeholder_is_not_a_bill_link():
    assert not BF.is_bill_link(PH) and not BF.is_bill_link(None)
    assert BF.is_bill_link(REAL) and BF.is_bill_link('/bill/117th-congress/senate-joint-resolution/3')


def test_lost_bills_are_placeholder_only_bills_once_each():
    rows = [dict(Role='sponsor', Name='H.R.1', Congress=118, DetailLink=PH, Member='A'),
            dict(Role='sponsor', Name='H.R.1', Congress=118, DetailLink=PH, Member='A'),   # 2nd section
            dict(Role='sponsor', Name='H.R.2', Congress=118, DetailLink=PH, Member='B'),
            dict(Role='sponsor', Name='H.R.2', Congress=118, DetailLink=REAL, Member='B'),  # real link wins
            dict(Role='sponsor', Name='H.R.1', Congress=117, DetailLink=PH, Member='A'),    # other Congress
            dict(Role='cosponsor', Name='H.R.9', Congress=118, DetailLink=PH, Member='C')]
    got = [(r['Name'], r['Congress']) for r in BF.lost_sponsored_bills(rows)]
    assert got == [('H.R.1', 118), ('H.R.1', 117)], got


def test_stage3_weighting_and_identity_refusal():
    cache = {(118, 'hr', 1): {'sponsor': 'S1', 'cos': [['O1', True, False], ['R1', False, False], ['W1', False, True]]},
             (118, 'hr', 2): {'sponsor': 'OTHER', 'cos': [['O1', True, False]]}}
    bills = [dict(Name='H.R.1', Congress=118, Member='A', LastAction='Passed House', DecayWeight=0.8),
             dict(Name='H.R.2', Congress=118, Member='B', LastAction='Introduced', DecayWeight=1.0),
             dict(Name='H.R.3', Congress=118, Member='A', LastAction='Introduced', DecayWeight=1.0)]
    ties, refused = BF.backfill_ties(bills, cache, {'A': 'S1', 'B': 'B9'})
    f = 3.0 * 0.8 / math.sqrt(3)   # withdrawn W1 counts in C, as on the cosponsors page
    assert abs(ties[('S1', 'O1')][0] - 1.0 * f) < 1e-12 and ties[('S1', 'O1')][2] == 1
    assert abs(ties[('S1', 'R1')][0] - 0.5 * f) < 1e-12 and ties[('S1', 'R1')][3] == 1
    assert ('S1', 'W1') in ties and not any(k[0] == 'OTHER' for k in ties)
    assert [r[0] for r in refused] == ['H.R.2', 'H.R.3'], refused


def _fixture(tmp, base, edges, sidecar):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = 'Edges'
    ws.append(['From', 'To', 'Weight'])
    for e in edges: ws.append(list(e))
    path = os.path.join(tmp, base); wb.save(path)
    if sidecar is not None:
        os.makedirs(os.path.join(tmp, 'data', 'network'), exist_ok=True)
        with open(os.path.join(tmp, MC.CU_PLACEHOLDER_BACKFILL_FILES[BF.VINTAGE]), 'w', encoding='utf-8') as fh:
            json.dump(sidecar, fh)
    return path, openpyxl.load_workbook(path, data_only=True)['Edges']


def test_kernel_merge_sums_same_direction_and_appends_new_pairs():
    side = {'vintage': BF.VINTAGE, 'edges': [{'from': 'A', 'to': 'B', 'weight': 0.25},
                                              {'from': 'B', 'to': 'A', 'weight': 2.0},
                                              {'from': 'C', 'to': 'D', 'weight': 1.5}]}
    old = _env('CU_PLACEHOLDER_BACKFILL', None)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, ws = _fixture(tmp, BF.VINTAGE, [('A', 'B', 1.0), ('A', 'C', 0.5)], side)
            rows = list(MC.cu_edge_rows(path, ws))
            assert rows == [('A', 'B', 1.25), ('A', 'C', 0.5), ('B', 'A', 2.0), ('C', 'D', 1.5)], rows
            os.environ['CU_PLACEHOLDER_BACKFILL'] = '0'
            assert list(MC.cu_edge_rows(path, ws)) == [('A', 'B', 1.0), ('A', 'C', 0.5)]
            os.environ['CU_PLACEHOLDER_BACKFILL'] = '2'
            try:
                list(MC.cu_edge_rows(path, ws)); raise AssertionError('dial 2 must raise')
            except ValueError:
                pass
            os.environ.pop('CU_PLACEHOLDER_BACKFILL')
            other, ws2 = _fixture(tmp, 'congress_cosponsor_network_20990101_000000.xlsx', [('A', 'B', 1.0)], None)
            assert list(MC.cu_edge_rows(other, ws2)) == [('A', 'B', 1.0)]   # other vintages untouched
        with tempfile.TemporaryDirectory() as tmp:
            path, ws = _fixture(tmp, BF.VINTAGE, [('A', 'B', 1.0)], None)
            try:
                list(MC.cu_edge_rows(path, ws)); raise AssertionError('missing sidecar must raise')
            except FileNotFoundError:
                pass
    finally:
        _env('CU_PLACEHOLDER_BACKFILL', old)


def test_all_four_cu_base_readers_route_through_the_merge():
    def src(rel):
        with open(os.path.join(FEC, rel), encoding='utf-8') as fh: return fh.read()
    assert src('moneyball_core.py').count('for a,b,w in cu_edge_rows(net,ne):') == 2   # load_network + directed
    assert 'for a,b,w in cu_edge_rows(src,ws):' in src(os.path.join('network', 'build_multilayer_network.py'))
    assert 'for a,b,w in cu_edge_rows(_n0,_e0):' in src(os.path.join('surfaces', 'build_dashboard_bundle.py'))


def test_live_sidecar_matches_the_measured_loss():
    path = os.path.join(ROOT, MC.CU_PLACEHOLDER_BACKFILL_FILES[BF.VINTAGE])
    if not os.path.exists(path):
        return
    doc = json.load(open(path, encoding='utf-8'))
    assert doc['vintage'] == BF.VINTAGE and doc['refused'] == []
    assert len(doc['lost_bills']) == 111 and len(doc['edges']) == 1272, (len(doc['lost_bills']), len(doc['edges']))


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
