# -*- coding: utf-8 -*-
"""COMPET_MULT (Paul 2026-10-05): the competitiveness multiplier c.

FIXTURE ONLY. Synthetic members/rows, no live tree read, nothing written outside tmp_path.
flip_model.py, money_response.py, per_topic_flip.py, lt_priming.py and budget_frontier.py execute on import, so
their REAL statements are lifted with ast (the repo's test idiom); the builder and the kernel are import-safe.

  formula     c = 1+exp(-max(lean,0)/5): 2.0 at lean<=0 / EVEN, 1+e^-1 at 5, monotone, in (1,2], 6 dp
  orientation lean = signed PVI x (+1 R / -1 D); the sign flips what counts as competitive
  join        Cook incumbent placement beats the district number; at-large -> SS-00 (never the string 'AL');
              DC -> state row; PR -> 1.0; Sanders/King caucus-oriented (Paul ruling 2026-10-07); an independent with no ruling
              (fixture 'K1') -> exactly 1.0 (Kiley's real bioguide is oriented R by ORIENTATION_OVERRIDES, Paul ruling 2026-10-07);
              open seats keep the boost and are flagged; unmatched -> exactly 1.0 + flag
  fail-closed the kernel refuses a short/forged/stale artifact; the dial OFF reads no file; a lone .candidate is
              never consumed implicitly
  identity    cm=1.0 is BITWISE the old arithmetic (==) in pflip, per_topic_flip.pflip, shift_lean, lt_priming,
              budget_frontier lev; both money legs carry the same c, so pflip(x,x,nA=nB) stays 0.5
"""
from __future__ import annotations

import ast
import hashlib
import itertools
import json
import math
import os
import sys
import types

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import moneyball_core as MC  # noqa: E402  import-safe kernel
from engine import build_competitiveness as BC  # noqa: E402  pure functions + __main__ guard

ENG = os.path.join(FEC, 'engine')
FLIP_MODEL = os.path.join(ENG, 'flip_model.py')
PER_TOPIC = os.path.join(ENG, 'per_topic_flip.py')
MONEY = os.path.join(ENG, 'money_response.py')
LT = os.path.join(ENG, 'lt_priming.py')
FRONTIER = os.path.join(ENG, 'budget_frontier.py')


def _targets(node):
    return {sub.id for target in node.targets for sub in ast.walk(target) if isinstance(sub, ast.Name)}


def _lift(path, functions=(), assigns=(), namespace=None):
    with open(path, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), filename=path)
    body = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in functions)
            or (isinstance(n, ast.Assign) and _targets(n) & set(assigns))]
    found = {getattr(n, 'name', None) for n in body} | {t for n in body if isinstance(n, ast.Assign) for t in _targets(n)}
    assert not ((set(functions) | set(assigns)) - found), '%s lost %s' % (path, (set(functions) | set(assigns)) - found)
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=body, type_ignores=[]), path, 'exec'), ns)  # noqa: S102 - vetted statements
    return ns


# ---------------------------------------------------------------- formula
def test_c_formula_values_and_bounds():
    assert MC.compet_c(0) == 2.0 and MC.compet_c(-7) == 2.0 and MC.compet_c(-0.0) == 2.0     # EVEN and the other party's seats
    assert MC.compet_c(5) == round(1 + math.exp(-1), 6)
    assert MC.compet_c(None) == 1.0                                                          # unmatched / unoriented: exactly 1
    leans = list(range(-30, 60))
    cs = [MC.compet_c(x) for x in leans]
    assert all(1.0 < c <= 2.0 for c in cs)
    assert all(a >= b for a, b in zip(cs, cs[1:]))                                           # monotone non-increasing in lean
    assert cs[leans.index(0)] > cs[leans.index(1)] > cs[leans.index(10)]
    assert all(c == round(c, 6) for c in cs)                                                 # 6 dp at the source
    assert (MC.COMPET_AMPLITUDE, MC.COMPET_SCALE) == (1.0, 5.0)


# ---------------------------------------------------------------- orientation + join
def _cook(did, st, num, text, party, p26, p25=None):
    return {'district_id': did, 'state': st, 'district_number': num, 'incumbent_source_text': text,
            'incumbent_party_source': party, 'pvi_2026_signed_points': p26,
            'pvi_2025_signed_points': p26 if p25 is None else p25}


def _m(name, bio, chamber, st, dist, party, vv='House'):
    return {'name': name, 'bioguide': bio, 'chamber': chamber, 'state': st, 'district_code': dist,
            'party': party, 'vv_chamber': vv}


COOK = [
    _cook('TX-35', 'TX', 35, 'OPEN (Casar)', 'D', 4, -19),
    _cook('TX-37', 'TX', 37, 'Greg Casar', 'D', -30, -26),
    _cook('AK-00', 'AK', 0, 'Nick Begich', 'R', 6),
    _cook('ME-02', 'ME', 2, 'OPEN (Golden)', 'D', 9),
    _cook('CO-07', 'CO', 7, 'Brittany Petterson', 'D', -8),
    _cook('IA-01', 'IA', 1, 'Mariannette Miller-Meeks', 'R', 0),
    _cook('CA-03', 'CA', 3, 'Kevin Kiley', 'R', 4),
    _cook('AL-01', 'AL', 1, 'Jo Seated', 'R', 17, 27),
    _cook('NY-10', 'NY', 10, 'Dan Goldman', 'D', -20),
]
STATES = {'ME': {'label': 'D+4', 'pts': -4}, 'VT': {'label': 'D+17', 'pts': -17}, 'DC': {'label': 'D+44', 'pts': -44},
          'UT': {'label': 'R+11', 'pts': 11}}
ROSTER = [
    _m('Greg Casar', 'C1', 'House', 'TX', 35, 'D'),                   # Voteview says TX-35; Cook places him in TX-37
    _m('Nick Begich', 'B1', 'House', 'AK', 1, 'R'),                   # Voteview at-large is district 1
    _m('Jared Golden', 'G1', 'House', 'ME', 2, 'D'),                  # open seat
    _m('Brittany Pettersen', 'P1', 'House', 'CO', 7, 'D'),            # Cook misspells (Petterson)
    _m('Mariannette Miller-Meeks', 'M1', 'House', 'IA', 1, 'R'),      # EVEN seat
    _m('Kevin Kiley', 'K1', 'House', 'CA', 3, 'I'),                   # independent, no caucus
    _m('Jo Seated', 'S1', 'House', 'AL', 1, 'R'),                     # redistricted state + 10 point PVI move
    _m('Zed Nomatch', 'Z1', 'House', 'WY', 1, 'R'),                   # no Cook row at all
    _m('Dan Goldman', 'G2', 'House', 'NY', 10, 'D'),
    _m('Eleanor Norton', 'N1', 'House', 'DC', 0, 'D'),
    _m('Pablo Hernandez Rivera', 'H1', 'House', 'PR', 0, 'D'),
    _m('Bernard Sanders', 'S2', 'Senate', 'VT', 0, 'I', vv='Senate'),
    _m('Angus King', 'K2', 'Senate', 'ME', 0, 'I', vv='Senate'),
    _m('Mike Lee', 'L1', 'Senate', 'UT', 0, 'R', vv='Senate'),
]
BC_CAUCUS = {'S2': 'D', 'K2': 'D'}


@pytest.fixture(scope='module')
def records():
    saved = dict(BC.CAUCUS)
    BC.CAUCUS.clear(); BC.CAUCUS.update(BC_CAUCUS)
    try:
        return BC.build_records(ROSTER, COOK, STATES)
    finally:
        BC.CAUCUS.clear(); BC.CAUCUS.update(saved)


def test_orientation_sign_decides_competitiveness(records):
    assert records['Greg Casar']['lean'] == 30 and records['Greg Casar']['c'] == MC.compet_c(30)
    assert records['Jo Seated']['lean'] == 17 and records['Jo Seated']['oriented_party'] == 'R'
    g = records['Jared Golden']                                      # a Democrat in an R+9 seat: lean -9 -> c = 2.0
    assert (g['lean'], g['c']) == (-9, 2.0)
    assert records['Mariannette Miller-Meeks']['c'] == 2.0 and records['Mariannette Miller-Meeks']['pvi2026'] == 0


def test_cook_incumbent_placement_beats_the_district_number(records):
    r = records['Greg Casar']
    assert r['join_key'] == 'TX-37' and r['join_method'] == 'cook_incumbent_exact'
    assert 'cook_placement_differs_from_voteview_district' in r['flags']
    assert records['Brittany Pettersen']['join_method'] == 'cook_incumbent_fuzzy'            # Cook's spelling slip
    assert records['Brittany Pettersen']['join_key'] == 'CO-07'


def test_at_large_maps_to_ss_00_never_the_string_al(records):
    r = records['Nick Begich']
    assert r['join_key'] == 'AK-00' and r['pvi2026'] == 6
    assert all(v['join_key'] != 'AL' for v in records.values())
    # a district_code-1 member of a state that ALSO has numbered Cook rows is not at-large
    assert BC.place_house_members([ROSTER[0]], COOK)['Greg Casar'][0]['district_id'] == 'TX-37'


def test_dc_pr_caucus_independent_and_unmatched(records):
    assert records['Eleanor Norton']['join_method'] == 'state_table' and records['Eleanor Norton']['pvi2026'] == -44
    assert records['Eleanor Norton']['c'] == MC.compet_c(44)
    pr = records['Pablo Hernandez Rivera']
    assert pr['c'] == 1.0 and 'territory_delegate_c1' in pr['flags'] and pr['join_status'] != 'MATCHED'
    for nm in ('Bernard Sanders', 'Angus King'):
        assert records[nm]['oriented_party'] == 'D' and 'independent_caucus_oriented' in records[nm]['flags']
    assert records['Angus King']['lean'] == 4 and records['Bernard Sanders']['lean'] == 17
    k = records['Kevin Kiley']
    assert k['c'] == 1.0 and k['lean'] is None and 'unoriented_independent' in k['flags']
    z = records['Zed Nomatch']
    assert z['c'] == 1.0 and z['join_method'] == 'unmatched' and 'no_cook_row' in z['flags']


def test_district_row_claimed_by_another_incumbent_gets_c_one(records):
    # agent default (D-105): district-number fallback joins land on a Cook row naming ANOTHER incumbent -> c exactly 1.0, flag kept; kept for district-number
    # joins with no ballot override (D-107 R3/R4 supersede it for members with a row in ballot_district_overrides.csv)
    roster = ROSTER + [_m('Lloyd Doggett', 'D9', 'House', 'TX', 37, 'D')]            # Cook TX-37 is Greg Casar's row
    out = BC.build_records(roster, COOK, STATES)
    d = out['Lloyd Doggett']
    assert d['join_method'] == 'district_number' and d['join_key'] == 'TX-37'
    assert 'district_number_row_claimed_by_other_incumbent' in d['flags']
    assert d['c'] == 1.0 and d['lean'] is None and d['join_status'] == 'MATCHED_DISTRICT_ROW_CLAIMED_C1'
    assert d['pvi2026'] == -30                                                         # the row's PVI stays recorded
    assert out['Greg Casar']['c'] == MC.compet_c(30) and out['Greg Casar']['c'] != 1.0   # the incumbent Cook names keeps his c
    # a district-number join onto a row whose named incumbent is NOT a roster member claims nobody: boosted as before
    solo = BC.build_records([_m('Some Newcomer', 'Z9', 'House', 'ME', 2, 'D')], COOK, STATES)['Some Newcomer']
    assert solo['join_method'] == 'district_number' and 'district_number_row_claimed_by_other_incumbent' not in solo['flags']
    assert solo['c'] == MC.compet_c(-9) == 2.0
    # the artifact validator accepts the 1.0 row and still refuses a forged boost on it
    cong = {n: {'member': n, 'bioguide': v['bioguide']} for n, v in out.items()}
    good = {'header': {'schema': MC.COMPET_SCHEMA, 'amplitude': 1.0, 'scale': 5.0, 'roster_sha256': MC.compet_roster_hash(cong),
                       'ballot_overrides': {'running_house': [], 'not_running_house': []}, 'orientation_overrides': {}},
            'members': json.loads(json.dumps(out))}
    assert MC.compet_validate(good, cong)['Lloyd Doggett'] == 1.0
    good['members']['Lloyd Doggett']['c'] = 1.5
    with pytest.raises(ValueError):
        MC.compet_validate(good, cong)


def test_open_seat_keeps_the_boost_and_flags(records):
    g = records['Jared Golden']
    assert g['c'] == 2.0 and 'open_seat' in g['flags']
    assert 'open_seat' not in records['Greg Casar']['flags'] or records['Greg Casar']['join_key'] != 'TX-35'


def test_senate_state_table_and_flags(records):
    assert records['Mike Lee']['join_method'] == 'state_table' and records['Mike Lee']['pvi2026'] == 11
    assert records['Mike Lee']['c'] == MC.compet_c(11) and records['Mike Lee']['pvi2025'] is None
    s = records['Jo Seated']
    assert 'mid_decade_redistricted_state' in s['flags'] and 'pvi_shift_ge_5' in s['flags']
    assert 'boundary_unverified_here' not in s['flags']   # House boundaries verified by Paul 2026-10-06


# ---------------------------------------------------------------- fail-closed kernel
CONG = {r['name']: {'member': r['name'], 'bioguide': r['bioguide']} for r in ROSTER}


def _doc(records):
    return {'header': {'schema': MC.COMPET_SCHEMA, 'amplitude': 1.0, 'scale': 5.0,
                       'roster_sha256': MC.compet_roster_hash(CONG),
                       'ballot_overrides': {'running_house': [], 'not_running_house': []}, 'orientation_overrides': {}},
            'members': json.loads(json.dumps(records))}


def test_validate_accepts_a_good_artifact_and_refuses_each_defect(records):
    good = _doc(records)
    out = MC.compet_validate(good, CONG)
    assert set(out) == set(CONG) and all(1.0 <= v <= 2.0 for v in out.values())
    def broken(mutate):
        d = _doc(records); mutate(d)
        with pytest.raises(ValueError):
            MC.compet_validate(d, CONG)
    broken(lambda d: d['members'].pop('Mike Lee'))                                       # a member missing
    broken(lambda d: d['members'].update({'Extra Person': dict(d['members']['Mike Lee'])}))
    broken(lambda d: d['members']['Mike Lee'].update(c=2.5))                             # out of range
    broken(lambda d: d['members']['Mike Lee'].update(c=1.5))                             # does not recompute from lean
    broken(lambda d: d['members']['Kevin Kiley'].update(c=1.7))                          # unoriented must be exactly 1.0
    broken(lambda d: d['header'].update(roster_sha256='0' * 64))                         # stale roster
    broken(lambda d: d['header'].update(scale=4.0))                                      # constants drift
    broken(lambda d: d.update(header={}))


def test_dial_off_reads_no_file_and_returns_exactly_one(monkeypatch):
    monkeypatch.setenv('COMPET_MULT', '0')                                            # the revert switch (the dial is ON by default since 2026-10-06)
    def boom(*a, **k): raise AssertionError('dial OFF must not touch the artifact')
    monkeypatch.setattr(MC, 'compet_state', boom)
    monkeypatch.setattr(MC, 'compet_artifact_path', boom)
    assert MC.cmult('Anyone') == 1.0 and MC.compet_record() == ('0', None)
    monkeypatch.setenv('COMPET_MULT', '2')
    with pytest.raises(ValueError):
        MC.compet_on()


def test_dial_on_never_consumes_a_lone_candidate(tmp_path, monkeypatch, records):
    money = tmp_path / 'data' / 'money'; money.mkdir(parents=True)
    cand = money / 'competitiveness_c.json.candidate'
    cand.write_text(json.dumps(_doc(records)), encoding='utf-8')
    monkeypatch.setattr(MC, 'p', lambda *parts: os.path.join(str(tmp_path), *parts))
    monkeypatch.setattr(MC, 'congestion', lambda: CONG)
    for _key, rel in MC.COMPET_INPUTS:                                                # R7: the loader re-hashes the three input CSVs
        f = tmp_path.joinpath(*rel.split('/')); f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(('input %s\n' % _key).encode())
    doc = _doc(records)
    doc['header']['input_sha256'] = {k: hashlib.sha256(tmp_path.joinpath(*rel.split('/')).read_bytes()).hexdigest() for k, rel in MC.COMPET_INPUTS}
    cand.write_text(json.dumps(doc), encoding='utf-8')
    MC._c.pop('compet', None)
    monkeypatch.setenv('COMPET_MULT', '1'); monkeypatch.delenv('COMPET_SRC', raising=False)
    with pytest.raises(RuntimeError, match='COMPET_SRC'):
        MC.cmult('Mike Lee')
    monkeypatch.setenv('COMPET_SRC', str(cand))
    MC._c.pop('compet', None)
    assert MC.cmult('Mike Lee') == records['Mike Lee']['c'] and MC.cmult('Kevin Kiley') == 1.0
    dial, sha = MC.compet_record()
    assert dial == '1' and len(sha) == 64
    MC._c.pop('compet', None)
    tmp_path.joinpath(*MC.COMPET_INPUTS[0][1].split('/')).write_bytes(b'edited after the build\n')   # an input drifted since the build
    with pytest.raises(ValueError, match='changed since the build'):
        MC.cmult('Mike Lee')
    MC._c.pop('compet', None)


# ---------------------------------------------------------------- bitwise identity (cm=1.0)
FM = _lift(FLIP_MODEL, functions=('pflip',), assigns=('WM', 'WP', 'HALFF'), namespace={'os': os})
WM, WP, HALFF = FM['WM'], FM['WP'], FM['HALFF']


def _old_pflip(cu_n, ind_n, R_n, nA_n, nB_n, mAt, mBt, pm=1.0):
    a = WM * pm * (cu_n + 1) / ((cu_n + 1) + HALFF * R_n) + WP * nA_n / mAt
    b = WM * pm * (ind_n + 1) / ((ind_n + 1) + HALFF * R_n) + WP * nB_n / mBt
    return a / (a + b) if a + b > 0 else 0.0


GRID = list(itertools.product((0, 5000, 20000.5, 123456), (0, 40000, 250000.0), (200000, 1234567.0),
                              (0.0, 0.3, 1.7), (0.0, 0.9), (2.0,), (1.5,), (1.0, 0.93, 1.21)))


def test_pflip_cm_one_is_bitwise_the_old_arithmetic():
    for cu, ind, R, nA, nB, mAt, mBt, pm in GRID:
        assert FM['pflip'](cu, ind, R, nA, nB, mAt, mBt, pm) == _old_pflip(cu, ind, R, nA, nB, mAt, mBt, pm)
        assert FM['pflip'](cu, ind, R, nA, nB, mAt, mBt, pm, 1.0) == _old_pflip(cu, ind, R, nA, nB, mAt, mBt, pm)


def test_c_scales_both_money_legs_so_equal_money_and_peers_stay_even():
    for x, c in itertools.product((0, 7000, 90000.0), (1.0, 1.2, 1.83, 2.0)):
        assert FM['pflip'](x, x, 500000, 0.7, 0.7, 2.0, 2.0, 1.1, c) == 0.5          # same c on both legs
    # c lifts the effective CU dollars: more c -> more pull from the same real dollars; ind-side too
    assert FM['pflip'](5000, 0, 400000, 0.5, 0.5, 2, 2, 1.0, 2.0) > FM['pflip'](5000, 0, 400000, 0.5, 0.5, 2, 2, 1.0, 1.0)
    assert FM['pflip'](0, 5000, 400000, 0.5, 0.5, 2, 2, 1.0, 2.0) < FM['pflip'](0, 5000, 400000, 0.5, 0.5, 2, 2, 1.0, 1.0)


def test_per_topic_flip_pflip_cm_one_is_bitwise_and_carries_c_on_both_legs():
    cu, ind, Rcap = {'m': 4000, 'n': 0, 'q': 52000}, {'m': 52000, 'n': 52000, 'q': 52000}, {'m': 300000, 'n': 300000, 'q': 300000}
    old_ns = {'WM': 0.35, 'WP': 0.65, 'HALFF': 0.10, 'cu': cu, 'ind': ind, 'Rcap': Rcap, 'rc': lambda n, k: 1.07}
    one = _lift(PER_TOPIC, functions=('pflip',), namespace=dict(old_ns, CM={'m': 1.0, 'n': 1.0, 'q': 1.0}))['pflip']
    def old(n, al, nA, nB, mAt, mBt):
        pm = 1.07
        a = 0.35 * pm * (cu[n] + al + 1) / ((cu[n] + al + 1) + 0.10 * Rcap[n]) + 0.65 * nA[n] / mAt
        b = 0.35 * pm * (ind[n] + 1) / ((ind[n] + 1) + 0.10 * Rcap[n]) + 0.65 * nB[n] / mBt
        return a / (a + b) if a + b > 0 else 0
    nA, nB = {'m': 0.8, 'n': 0.8}, {'m': 0.4, 'n': 0.8}
    for n, al in itertools.product(('m', 'n'), (0, 500, 10000)):
        assert one(n, al, nA, nB, 2.0, 1.6) == old(n, al, nA, nB, 2.0, 1.6)
    hi = _lift(PER_TOPIC, functions=('pflip',), namespace=dict(old_ns, CM={'m': 1.9, 'n': 1.9, 'q': 1.9}))['pflip']
    assert hi('q', 0, {'q': 0.8}, {'q': 0.8}, 1.0, 1.0) == 0.5          # equal money on both legs + equal peers stays 0.5 under c
    assert hi('n', 0, {'n': 0.8}, {'n': 0.8}, 1.0, 1.0) < one('n', 0, {'n': 0.8}, {'n': 0.8}, 1.0, 1.0)   # c amplifies the industry leg too


def test_shift_lean_cm_one_is_bitwise_and_c_scales_receipts_ratio():
    ns = {'receipts': lambda m: 640000, 'MAXSHIFT': 18.0, 'HALF': 0.10, 'LOSSFRAME': 1.10, 'rc': lambda m, k: 1.13}
    shift = _lift(MONEY, functions=('shift_lean',), namespace=ns)['shift_lean']
    def old(m, source, amount):
        r = amount / 640000
        mag = 18.0 * (r / (r + 0.10)); mag *= 1.13
        if source != 'cu': mag *= 1.10
        return mag if source == 'cu' else -mag
    for amount, src in itertools.product((0, 500, 10000, 100000.0, 400000), ('cu', 'comp')):
        assert shift('m', src, amount) == old('m', src, amount) == shift('m', src, amount, 1.0)
    # c*amount is the effective dollar: shift(c=2, $X) == shift(c=1, $2X)
    assert shift('m', 'cu', 10000, 2.0) == shift('m', 'cu', 20000, 1.0)
    assert shift('m', 'comp', 10000, 1.6) == shift('m', 'comp', 16000, 1.0)


def test_lt_priming_money_units_and_inverse_carry_c():
    ns = {'MAXSHIFT': 18.0, 'HALF': 0.10, 'receipts': lambda m: 500000, 'pmult': lambda m: 1.2, 'CMX': {'m': 1.0}}
    one = _lift(LT, functions=('money_units', 'dollars_for_units'), namespace=ns)
    def old_units(m, d):
        r = d / 500000; return (18.0 * (r / (r + 0.10)) * 1.2) / 18.0
    def old_dollars(m, u):
        u = min(u / 1.2, 0.98); return 0.10 * 500000 * u / (1 - u)
    for d in (0, 1000, 25000.0, 300000):
        assert one['money_units']('m', d) == old_units('m', d)
    for u in (0.0, 0.1, 0.5, 0.97):
        assert one['dollars_for_units']('m', u) == old_dollars('m', u)
    hi = _lift(LT, functions=('money_units', 'dollars_for_units'), namespace=dict(ns, CMX={'m': 1.7}))
    assert abs(hi['money_units']('m', 20000) - one['money_units']('m', 20000 * 1.7)) <= 1e-15
    assert abs(hi['dollars_for_units']('m', hi['money_units']('m', 20000)) - 20000) <= 1e-6      # the pair still inverts
    assert hi['dollars_for_units']('m', 0.4) < one['dollars_for_units']('m', 0.4)                 # c>1: cheaper per unit


def _lev_ns(compet_on, cm):
    EL = {'a': {'leverage': 1.4}, 'b': {'leverage': 1.1}}
    return {'EL': EL, 'LEVON': True, 'members': ['a', 'b', 'c'],
            'MBC': types.SimpleNamespace(compet_on=lambda: compet_on, cmult=lambda n: cm[n])}


def test_budget_frontier_lev_is_unchanged_off_and_replaced_by_c_on():
    off = _lift(FRONTIER, assigns=('LEV_SRC', 'lev'), namespace=_lev_ns(False, {'a': 1.9, 'b': 1.9, 'c': 1.9}))['lev']
    assert off == {'a': 1.4, 'b': 1.1, 'c': 1.0}                                         # the Ballotpedia leverage, byte for byte
    on = _lift(FRONTIER, assigns=('LEV_SRC', 'lev'), namespace=_lev_ns(True, {'a': 1.9, 'b': 1.2, 'c': 1.0}))['lev']
    assert on == {'a': 1.9, 'b': 1.2, 'c': 1.0}                                          # c in the same position
    src = open(FRONTIER, encoding='utf-8').read().replace(' ', '')
    assert 'min(0.9,ind[n]/BSCALE)' in src                                                # bystander activation untouched


def test_every_consumer_is_wired_to_the_kernel_accessor():
    for rel, needle in (('engine/flip_model.py', 'MC.cmult'), ('engine/per_topic_flip.py', 'MC.cmult'),
                        ('engine/lt_priming.py', 'MBC.cmult'), ('engine/budget_frontier.py', 'MBC.cmult'),
                        ('engine/optimize.py', 'fm.CM'), ('engine/salience_shock.py', 'fm.CM'),
                        ('surfaces/build_dashboard_bundle.py', "engine['cmult']"),
                        ('surfaces/make_member_desk.py', "eng.get('cmult')"),
                        ('surfaces/templates/make_dashboard_html_00.tpl', 'EG.cmult')):
        with open(os.path.join(FEC, rel), encoding='utf-8') as handle:
            assert needle in handle.read(), (rel, needle)


# ---------------------------------------------------------------- review fixes (S2-2, S3)
def test_compet_on_defaults_on_and_only_zero_turns_it_off(monkeypatch):
    # Paul 2026-10-06: unset and '' are ON; '0' is the revert switch; '1' is on; anything else raises
    monkeypatch.delenv('COMPET_MULT', raising=False)
    assert MC.compet_on() is True
    monkeypatch.setenv('COMPET_MULT', '')
    assert MC.compet_on() is True
    monkeypatch.setenv('COMPET_MULT', ' 1 ')
    assert MC.compet_on() is True
    monkeypatch.setenv('COMPET_MULT', '0')
    assert MC.compet_on() is False and MC.cmult('Anyone') == 1.0
    for bad in ('2', 'true', 'on', '01'):
        monkeypatch.setenv('COMPET_MULT', bad)
        with pytest.raises(ValueError):
            MC.compet_on()


def test_validate_is_nan_safe_and_rejects_non_finite(records):
    def broken(mutate):
        d = _doc(records); mutate(d)
        with pytest.raises(ValueError):
            MC.compet_validate(d, CONG)
    nan, inf = float('nan'), float('inf')
    broken(lambda d: d['members']['Mike Lee'].update(c=nan))                      # NaN c: every comparison with it is False
    broken(lambda d: d['members']['Mike Lee'].update(lean=nan))
    broken(lambda d: d['members']['Mike Lee'].update(lean=inf, c=1.0))
    broken(lambda d: d['members']['Mike Lee'].update(lean=-inf, c=2.0))
    broken(lambda d: d['members']['Mike Lee'].update(c=inf))


def test_builder_failure_is_fatal_by_default_and_non_fatal_only_with_the_dial_explicitly_off(capsys):
    def boom():
        raise ValueError('synthetic input drift')
    assert BC.guarded_main(build=boom, env={'COMPET_MULT': '0'}) == 0
    assert 'COMPETITIVENESS BUILD FAILED (dial off, non-fatal): ValueError: synthetic input drift' in capsys.readouterr().out
    for env in ({}, {'COMPET_MULT': ''}, {'COMPET_MULT': '1'}, {'COMPET_MULT': '2'}):     # default ON -> the failure propagates
        with pytest.raises(ValueError):
            BC.guarded_main(build=boom, env=env)
    assert BC.guarded_main(build=lambda: None, env={}) == 0


def test_builder_script_exit_codes_with_missing_inputs(tmp_path):
    import subprocess
    script = os.path.join(ENG, 'build_competitiveness.py')
    base = {k: v for k, v in os.environ.items() if not k.startswith('COMPET_')}
    base['MONEYBALL_ROOT'] = str(tmp_path)                                         # an empty data plane: every input is missing
    off = subprocess.run([sys.executable, script], capture_output=True, text=True, env=dict(base, COMPET_MULT='0'))
    assert off.returncode == 0 and 'COMPETITIVENESS BUILD FAILED (dial off, non-fatal)' in off.stdout, (off.stdout, off.stderr)
    for env in (base, dict(base, COMPET_MULT='1')):                                         # default (unset) and explicit ON are both fatal
        on = subprocess.run([sys.executable, script], capture_output=True, text=True, env=env)
        assert on.returncode != 0 and 'COMPETITIVENESS BUILD FAILED' not in on.stdout, (on.stdout, on.stderr)


def test_frontier_labels_the_leverage_source_when_on():
    src = open(FRONTIER, encoding='utf-8').read()
    assert "'leverage_source':LEV_SRC" in src and "'compet_artifact_sha256':MBC.compet_record()[1]" in src
