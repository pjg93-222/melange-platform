# -*- coding: utf-8 -*-
"""COMPET_MULT rulings (Paul 2026-10-07, brain D-107): R1 pressure-field alias, R2 Kiley orientation, R3/R4 ballot-district overrides,
R5 caucus provenance, R6 LEV=0 forces the unit factor, R7 dial + c-artifact provenance and input-hash re-check.

FIXTURE ONLY. Synthetic members/rows, no live tree read, nothing written outside tmp_path. receptivity_layer.py, budget_frontier.py,
per_topic_flip.py and salience_shock.py execute on import, so their REAL statements are lifted with ast (same idiom as
test_competitiveness_c.py) or checked as source text; the builder and the kernel are import-safe.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import json
import os
import sys
import types

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import moneyball_core as MC  # noqa: E402  import-safe kernel
from engine import build_competitiveness as BC  # noqa: E402  pure functions + __main__ guard

RECEPT = os.path.join(FEC, 'members', 'receptivity_layer.py')
FRONTIER = os.path.join(FEC, 'engine', 'budget_frontier.py')
PER_TOPIC = os.path.join(FEC, 'engine', 'per_topic_flip.py')
SALIENCE = os.path.join(FEC, 'engine', 'salience_shock.py')
BUNDLE = os.path.join(FEC, 'surfaces', 'build_dashboard_bundle.py')
DESK = os.path.join(FEC, 'surfaces', 'make_member_desk.py')
EFFIC = os.path.join(FEC, 'engine', 'efficiency_layers.py')


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


def _src(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _cook(did, st, num, text, party, p26, p25=None):
    return {'district_id': did, 'state': st, 'district_number': num, 'incumbent_source_text': text,
            'incumbent_party_source': party, 'pvi_2026_signed_points': p26,
            'pvi_2025_signed_points': p26 if p25 is None else p25}


def _m(name, bio, chamber, st, dist, party, vv='House'):
    return {'name': name, 'bioguide': bio, 'chamber': chamber, 'state': st, 'district_code': dist,
            'party': party, 'vv_chamber': vv}


# ---------------------------------------------------------------- R1: pressure field
def test_incent_term_off_is_bitwise_the_old_expression():
    ns = _lift(RECEPT, functions=('clip', 'incent_term', 'incent_term_compet'))
    for sec in (0.0, 0.2, 0.4499, 0.45, 0.5, 0.73, 1.0):
        for retiring in (False, True):
            old = 0.60 if retiring else max(0.85, min(1.15, 1 + .30 * ((1 - sec) - 0.5)))
            assert ns['incent_term'](retiring, sec) == old                              # == , not approx
    assert ns['incent_term_compet'](False) == 1.0 and ns['incent_term_compet'](True) == 0.60


def test_receptivity_compet_fields_are_written_only_when_on():
    src = _src(RECEPT)
    assert src.count('if COMPET_ON else {}') == 2                                       # member dict + header, both dial-gated
    assert "'pressure_mult':round(clip(susc_mult*incent*pt,0.50,1.60),3)" in src and src.count("'pressure_mult':round(") == 1
    assert "'pressure_mult_compet':round(clip(susc_mult*incent_c*pt,0.50,1.60),3)" in src
    assert 'pt=1.15 if loss else 0.92' in src                                           # Linde-Vis pt untouched
    assert "PW=float(os.environ.get('PERSONA_W','0.30'))" in src                        # law 9 guard


def test_rc_pressure_alias_follows_the_dial(tmp_path, monkeypatch):
    (tmp_path / 'member_receptivity.json').write_text(json.dumps(
        {'members': {'a': {'pressure_mult': 1.2, 'pressure_mult_compet': 1.05, 'edge_mult': 0.9}}}), encoding='utf-8')
    monkeypatch.setattr(MC, 'p', lambda *parts: os.path.join(str(tmp_path), *parts))

    def fresh():
        MC._c.pop('rec', None); MC._c.pop('rec_pkey', None); MC._c.pop('rec_checked', None)

    try:
        monkeypatch.delenv('RECEPT_LAYER', raising=False)
        fresh(); monkeypatch.setenv('COMPET_MULT', '0')
        assert MC.pressure_field() == 'pressure_mult' and MC.rc('a', 'pressure_mult') == 1.2 and MC.rc('a', 'edge_mult') == 0.9
        fresh(); monkeypatch.delenv('COMPET_MULT', raising=False)                       # unset == ON
        assert MC.pressure_field() == 'pressure_mult_compet' and MC.rc('a', 'pressure_mult') == 1.05 and MC.rc('a', 'edge_mult') == 0.9
        assert MC.rc('zz', 'pressure_mult') == 1.0                                       # unknown member: the neutral default
        (tmp_path / 'member_receptivity.json').write_text(json.dumps({'members': {'a': {'pressure_mult': 1.2, 'edge_mult': 0.9}}}), encoding='utf-8')
        fresh()
        assert MC.rc('a', 'edge_mult') == 0.9                                            # non-pressure readers never need the compet field
        with pytest.raises(RuntimeError, match='receptivity_layer'):                     # pre-ruling file consumed ON for pressure: fail closed
            MC.rc('a', 'pressure_mult')
        fresh(); monkeypatch.setenv('COMPET_MULT', '0')
        assert MC.rc('a', 'pressure_mult') == 1.2                                        # the same file reads fine with the dial off
        fresh(); monkeypatch.delenv('COMPET_MULT', raising=False); monkeypatch.setenv('RECEPT_LAYER', '0')
        assert MC.rc('a', 'pressure_mult') == 1.0                                        # layer off -> neutral, no raise
    finally:
        MC._c.pop('rec', None); MC._c.pop('rec_pkey', None); MC._c.pop('rec_checked', None)


def test_surface_readers_use_the_kernel_field():
    assert 'fmE.MC.pressure_field()' in _src(BUNDLE)
    assert 'MBC.pressure_field()' in _src(DESK)
    ef = _src(EFFIC)
    assert 'pressure_field()' in ef and '"headline_scalar": pressure_field()' in ef


# ---------------------------------------------------------------- R2: Kiley oriented R
def test_kiley_override_orients_r_and_gets_c_two():
    roster = [_m('Kevin Kiley', 'K000401', 'House', 'CA', 3, 'I'), _m('Ind Two', 'I9999', 'House', 'CA', 6, 'I')]
    out = BC.build_records(roster, [_cook('CA-06', 'CA', 6, 'Kevin Kiley', 'I', -5, -8)], {})
    k = out['Kevin Kiley']
    assert k['oriented_party'] == 'R' and k['lean'] == -5 and k['c'] == 2.0 and k['join_status'] == 'MATCHED'
    assert 'orientation_override' in k['flags'] and 'unoriented_independent' not in k['flags']
    assert BC.ORIENTATION_OVERRIDES == {'K000401': ('R', 'Paul ruling 2026-10-07 (D-107 R2)')}
    t = out['Ind Two']                                                                   # an independent with no ruling stays unoriented
    assert t['c'] == 1.0 and t['oriented_party'] is None and 'unoriented_independent' in t['flags']


# ---------------------------------------------------------------- R3/R4: ballot-district overrides
COOK = [
    _cook('TX-37', 'TX', 37, 'Greg Casar', 'D', -30, -26),
    _cook('TX-35', 'TX', 35, 'OPEN (Casar)', 'D', 4, -19),
    _cook('CA-40', 'CA', 40, 'Young Kim', 'R', 6, 1),
    _cook('CA-41', 'CA', 41, 'Linda Sanchez', 'D', -9, 2),
]
ROSTER = [
    _m('Greg Casar', 'C000001', 'House', 'TX', 35, 'D'),
    _m('Lloyd Doggett', 'D000399', 'House', 'TX', 37, 'D'),
    _m('Young Kim', 'K000002', 'House', 'CA', 40, 'R'),
    _m('Ken Calvert', 'C000059', 'House', 'CA', 41, 'R'),
    _m('Linda Sanchez', 'S000003', 'House', 'CA', 38, 'D'),
    _m('Mike Lee', 'L000577', 'Senate', 'UT', 0, 'R', vv='Senate'),
]
GOOD_ROWS = [
    {'bioguide': 'C000059', 'name': 'Ken Calvert', 'ballot_district': 'CA-40', 'status': 'RUNNING_HOUSE', 'pvi_basis': 'ballot_2026',
     'source_url': 'https://example.org/calvert', 'source_date': '2026-06-12', 'ruling': 'Paul ruling 2026-10-07 (D-107 R3)'},
    {'bioguide': 'D000399', 'name': 'Lloyd Doggett', 'ballot_district': 'TX-37', 'status': 'NOT_RUNNING_HOUSE', 'pvi_basis': 'current_seat_2025',
     'source_url': 'https://example.org/doggett', 'source_date': '2025-12-05', 'ruling': 'Paul ruling 2026-10-07: retiring, current seat 2025 PVI'},
]


def _write_csv(tmp_path, rows, header=None, name='ov.csv'):
    path = tmp_path / name
    with open(path, 'w', encoding='utf-8', newline='') as fh:
        w = csv.writer(fh, lineterminator='\n')
        w.writerow(header or BC.BALLOT_HEADER)
        for r in rows:
            w.writerow(r if isinstance(r, list) else [r.get(k, '') for k in BC.BALLOT_HEADER])
    return str(path)


def _load(tmp_path, rows, header=None):
    return BC.load_ballot_overrides(_write_csv(tmp_path, rows, header), ROSTER, COOK)


def test_override_csv_header_is_exact():
    assert BC.BALLOT_HEADER == ['bioguide', 'name', 'ballot_district', 'status', 'pvi_basis', 'source_url', 'source_date', 'ruling']


def test_override_csv_parser_accepts_valid_and_refuses_each_defect(tmp_path):
    ov = _load(tmp_path, GOOD_ROWS)
    assert set(ov) == {'C000059', 'D000399'} and ov['C000059']['ballot_district'] == 'CA-40'
    assert _load(tmp_path, []) == {}                                                     # header-only file = zero overrides

    cal, dog = GOOD_ROWS

    def bad(rows, header=None, match=None):
        with pytest.raises(ValueError, match=match):
            _load(tmp_path, rows, header)

    bad([cal], header=list(reversed(BC.BALLOT_HEADER)), match='header')
    bad([cal, dict(cal)], match='duplicate')
    bad([dict(cal, bioguide='Z000001')], match='not in the roster')
    bad([dict(cal, bioguide='bad')], match='bad bioguide')
    bad([dict(cal, name='Somebody Else')], match='name')
    bad([dict(cal, bioguide='L000577', name='Mike Lee')], match='not a House member')
    bad([dict(cal, status='RUNNING')], match='status')
    bad([dict(cal, pvi_basis='current_seat_2025')], match='requires pvi_basis')          # RUNNING needs the ballot-year PVI
    bad([dict(dog, pvi_basis='ballot_2026')], match='requires pvi_basis')                # NOT_RUNNING needs the current-seat 2025 PVI
    bad([dict(cal, pvi_basis='')], match='requires pvi_basis')
    bad([dict(cal, ballot_district='')], match='not a Cook district_id')
    bad([dict(cal, ballot_district='CA-99')], match='not a Cook district_id')
    bad([dict(cal, ballot_district='TX-37')], match='outside CA')
    bad([dict(cal, source_url='http://example.org/x')], match='https')
    bad([dict(cal, source_date='2026/10/07')], match='YYYY-MM-DD')
    bad([dict(cal, ruling='')], match='ruling is empty')
    bad([[cal['bioguide'], cal['name']]], match='columns')                               # short row


def _build(overrides=None, roster=None):
    return BC.build_records(roster or ROSTER, COOK, {'UT': {'label': 'R+11', 'pts': 11}}, overrides)


def test_override_beats_placement_and_fallback_and_leaves_others_untouched(tmp_path):
    base = _build()
    assert base['Ken Calvert']['join_status'] == 'MATCHED_DISTRICT_ROW_CLAIMED_C1' and base['Ken Calvert']['c'] == 1.0
    assert base['Lloyd Doggett']['join_status'] == 'MATCHED_DISTRICT_ROW_CLAIMED_C1' and base['Lloyd Doggett']['c'] == 1.0
    ov = _load(tmp_path, [GOOD_ROWS[0]])
    out = _build(ov)
    c = out['Ken Calvert']
    assert c['join_method'] == 'ballot_district_override' and c['join_key'] == 'CA-40' and c['lean'] == 6 and c['c'] == MC.compet_c(6)
    assert {'ballot_district_override', 'ballot_district_differs_from_cook_row', 'ballot_district_shared_with_other_incumbent'} <= set(c['flags'])
    assert 'district_number_row_claimed_by_other_incumbent' not in c['flags'] and c['join_status'] == 'MATCHED'
    assert c['ballot_override']['pvi_basis'] == 'ballot_2026'
    for n in base:                                                                       # every non-overridden record is identical
        if n != 'Ken Calvert':
            assert out[n] == base[n], n
    # an override for a Cook-placed member moves only that member
    row = dict(GOOD_ROWS[0], bioguide='C000001', name='Greg Casar', ballot_district='TX-35')
    out2 = _build(_load(tmp_path, [row]))
    assert out2['Greg Casar']['join_key'] == 'TX-35' and out2['Greg Casar']['join_method'] == 'ballot_district_override'
    assert out2['Lloyd Doggett'] == base['Lloyd Doggett']


def test_not_running_member_takes_the_current_seat_2025_pvi(tmp_path):
    out = _build(_load(tmp_path, [GOOD_ROWS[1]]))
    d = out['Lloyd Doggett']
    assert d['join_method'] == 'ballot_district_override' and d['join_key'] == 'TX-37' and d['join_status'] == 'MATCHED'
    assert d['pvi2025'] == -26 and d['pvi2026'] == -30 and d['lean'] == 26 and d['c'] == MC.compet_c(26)   # the claimed-row c = 1 rule no longer applies
    assert 'district_number_row_claimed_by_other_incumbent' not in d['flags']
    assert {'ballot_status_not_running_house', 'pvi_basis_current_seat_2025'} <= set(d['flags'])
    assert d['ballot_override']['status'] == 'NOT_RUNNING_HOUSE' and d['ballot_override']['pvi_basis'] == 'current_seat_2025'
    assert out['Greg Casar'] == _build()['Greg Casar']


def _doc(records, running, not_running, orient=None, schema=None):
    cong = {n: {'member': n, 'bioguide': v['bioguide']} for n, v in records.items()}
    doc = {'header': {'schema': schema or MC.COMPET_SCHEMA, 'amplitude': 1.0, 'scale': 5.0, 'roster_sha256': MC.compet_roster_hash(cong),
                      'ballot_overrides': {'running_house': running, 'not_running_house': not_running},
                      'orientation_overrides': orient or {}},
           'members': json.loads(json.dumps(records))}
    return doc, cong


def test_validator_cross_checks_the_override_header(tmp_path):
    recs = _build(_load(tmp_path, GOOD_ROWS))
    doc, cong = _doc(recs, ['C000059'], ['D000399'])
    assert MC.compet_validate(doc, cong)['Ken Calvert'] == MC.compet_c(6)

    def broken(running, not_running, mutate=None, schema=None):
        d, cg = _doc(recs, running, not_running, schema=schema)
        if mutate: mutate(d)
        with pytest.raises(ValueError):
            MC.compet_validate(d, cg)

    broken([], ['D000399'])                                                               # override-joined member missing from the header
    broken(['C000059'], ['D000399', 'Z000001'])                                           # header names a member no row was joined for
    broken(['C000059'], ['D000399'], schema='melange-competitiveness-c-v1')
    for lean, name in ((7, 'Ken Calvert'), (30, 'Lloyd Doggett')):                        # lean must follow the PVI of the declared basis
        def forge(d, lean=lean, name=name):
            d['members'][name].update(lean=lean, c=MC.compet_c(lean))
        broken(['C000059'], ['D000399'], forge)
    broken([], ['D000399'], lambda d: d['members']['Ken Calvert'].update(join_method='cook_incumbent_exact'))   # a stray ballot_override on a non-override join
    d, cg = _doc(recs, ['C000059'], ['D000399'])
    d['header'].pop('ballot_overrides')
    with pytest.raises(ValueError, match='schema v2'):
        MC.compet_validate(d, cg)
    # an orientation override must be applied to its member
    k = BC.build_records([_m('Kevin Kiley', 'K000401', 'House', 'CA', 3, 'I')], [_cook('CA-06', 'CA', 6, 'Kevin Kiley', 'I', -5, -8)], {})
    kd, kc = _doc(k, [], [], orient={'K000401': {'party': 'R', 'provenance': 'x'}})
    assert MC.compet_validate(kd, kc)['Kevin Kiley'] == 2.0
    kd['members']['Kevin Kiley'].update(oriented_party='D', lean=5, c=MC.compet_c(5))
    with pytest.raises(ValueError, match='orientation override'):
        MC.compet_validate(kd, kc)


# ---------------------------------------------------------------- R5
def test_caucus_map_is_unchanged_and_labelled():
    assert BC.CAUCUS == {'S000033': 'D', 'K000383': 'D'}
    line = [ln for ln in _src(os.path.join(FEC, 'engine', 'build_competitiveness.py')).splitlines() if ln.startswith('CAUCUS =')][0]
    assert 'Paul ruling 2026-10-07' in line


# ---------------------------------------------------------------- R6
@pytest.mark.parametrize('lv,on,expect,src', [
    (False, False, {'a': 1.0, 'b': 1.0}, 'none'), (False, True, {'a': 1.0, 'b': 1.0}, 'none'),
    (True, False, {'a': 1.4, 'b': 1.0}, 'ballotpedia_lev'), (True, True, {'a': 1.9, 'b': 1.9}, 'competitiveness_c')])
def test_lev_zero_forces_unit_factor_in_both_dial_positions(lv, on, expect, src):
    ns = _lift(FRONTIER, assigns=('LEV_SRC', 'lev'),
               namespace={'EL': {'a': {'leverage': 1.4}}, 'members': ['a', 'b'], 'LEVON': lv,
                          'MBC': types.SimpleNamespace(compet_on=lambda: on, cmult=lambda n: 1.9)})
    assert ns['lev'] == expect and ns['LEV_SRC'] == src


@pytest.mark.parametrize('on', [True, False])
def test_frontier_header_key_order(on):
    ns = _lift(FRONTIER, assigns=('out',),
               namespace={'ELECTORAL': False, 'LEVON': True, 'ONLY': None, 'LEV_SRC': 'competitiveness_c' if on else 'ballotpedia_lev',
                          'MBC': types.SimpleNamespace(compet_on=lambda: on, compet_record=lambda: ('1', 'f' * 64)),
                          'EP': {}, 'defend_ov': [], 'frontier': {}})
    keys = list(ns['out'])
    if on:
        assert keys[:6] == ['mode', 'leverage', 'topic', 'compet_mult', 'leverage_source', 'compet_artifact_sha256']
    else:
        assert keys[:4] == ['mode', 'leverage', 'topic', 'leverage_source'] and 'compet_mult' not in keys and 'compet_artifact_sha256' not in keys


# ---------------------------------------------------------------- R7
def _inputs(tmp_path):
    for key, rel in MC.COMPET_INPUTS:
        f = tmp_path.joinpath(*rel.split('/')); f.parent.mkdir(parents=True, exist_ok=True); f.write_bytes(('bytes of %s\n' % key).encode())
    return {k: hashlib.sha256(tmp_path.joinpath(*rel.split('/')).read_bytes()).hexdigest() for k, rel in MC.COMPET_INPUTS}


def test_compet_check_inputs_refuses_drift(tmp_path, monkeypatch):
    monkeypatch.setattr(MC, 'p', lambda *parts: os.path.join(str(tmp_path), *parts))
    good = _inputs(tmp_path)
    MC.compet_check_inputs({'header': {'input_sha256': dict(good)}})                     # matching header passes
    key = MC.COMPET_INPUTS[2][0]
    with pytest.raises(ValueError, match=key):
        MC.compet_check_inputs({'header': {'input_sha256': {k: v for k, v in good.items() if k != key}}})
    with pytest.raises(ValueError, match=key):
        MC.compet_check_inputs({'header': {'input_sha256': dict(good, **{key: 'abc'})}})
    with pytest.raises(ValueError):
        MC.compet_check_inputs({'header': {}})
    tmp_path.joinpath(*MC.COMPET_INPUTS[1][1].split('/')).write_bytes(b'one changed byte')
    with pytest.raises(ValueError, match=MC.COMPET_INPUTS[1][0]):
        MC.compet_check_inputs({'header': {'input_sha256': dict(good)}})
    os.remove(tmp_path.joinpath(*MC.COMPET_INPUTS[0][1].split('/')))
    with pytest.raises(ValueError, match='missing'):
        MC.compet_check_inputs({'header': {'input_sha256': dict(good)}})
    assert dict(MC.COMPET_INPUTS)['ballot_district_overrides_csv'] == BC.BALLOT_CSV and BC.HOUSE_CSV == MC.COMPET_INPUTS[0][1]


def test_write_engine_dials_sidecar(tmp_path, monkeypatch):
    art = tmp_path / 'x.json'
    art.write_bytes(b'{"topic": {"member": 1.0}}')
    before = art.read_bytes()
    monkeypatch.setattr(MC, 'compet_record', lambda: ('1', 'a' * 64))
    MC.write_engine_dials(str(art))
    side = json.loads((tmp_path / 'x.dials.json').read_text(encoding='utf-8'))
    assert side['artifact_sha256'] == hashlib.sha256(before).hexdigest() and side['artifact'] == 'x.json'
    assert side['dials'] == {'COMPET_MULT': '1', 'COMPET_SRC_SHA256': 'a' * 64} and art.read_bytes() == before
    monkeypatch.setattr(MC, 'compet_record', lambda: ('0', None))
    MC.write_engine_dials(str(art))
    assert json.loads((tmp_path / 'x.dials.json').read_text(encoding='utf-8'))['dials'] == {'COMPET_MULT': '0', 'COMPET_SRC_SHA256': None}


def test_engines_record_the_dial():
    ptf = _src(PER_TOPIC)
    assert 'MC.dump_atomic(ALL,outf)\n' in ptf.replace('\r\n', '\n')
    assert 'MC.dump_atomic(ALL,outf)\n    MC.write_engine_dials(outf)' in ptf.replace('\r\n', '\n')
    sal = _src(SALIENCE)
    assert "'dials':dict(zip(('COMPET_MULT','COMPET_SRC_SHA256'),MBC.compet_record()))" in sal and 'if MBC.compet_on() else {}' in sal
