# -*- coding: utf-8 -*-
"""QME 2026-10-01 lt_priming fixes. FIXTURE ONLY: lt_priming.py / audit_full.py execute on import, so the real
function bodies are ast-lifted and run on synthetic inputs; nothing is imported or written."""
from __future__ import annotations
import ast, os, re
import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LT = os.path.join(FEC, 'engine', 'lt_priming.py'); AUD = os.path.join(FEC, 'audit', 'audit_full.py')
TPL = os.path.join(FEC, 'surfaces', 'templates', 'make_dashboard_html_00.tpl')
CATS = ['Interchange', 'Mortgage & Housing', 'Credit Card Mandates', 'CFPB', 'CU Capital Requirements']

def lift(path, names, ns=None):
    src = open(path, encoding='utf-8').read(); tree = ast.parse(src)
    keep = [n for n in tree.body if (isinstance(n, ast.FunctionDef) and n.name in names)]
    ns = dict(ns or {}); ns['re'] = re; ns['os'] = os
    exec(compile(ast.Module(keep, []), path, 'exec'), ns)
    return ns

def test_pick_topic():
    f = lift(LT, {'pick_topic'})['pick_topic']
    assert f('Mortgage', CATS) == 'Mortgage & Housing'           # the production default: unique substring
    assert f('cfpb', CATS) == 'CFPB'                             # exact beats substring, case-insensitive
    assert f('CU Capital', CATS) == 'CU Capital Requirements'
    with pytest.raises(ValueError, match='matches no category'): f('Nonesuch', CATS)
    with pytest.raises(ValueError, match='ambiguous'): f('c', CATS)   # several substrings -> no silent first-hit
    assert f('c', CATS + ['C']) == 'C'                           # an exact hit still wins over many substrings

def test_scoped_output_path():
    f = lift(LT, {'scoped_output_path'})['scoped_output_path']
    d = os.path.join('x', 'lt_priming.json')
    assert f(None, d, None) == d and f('o.json', d, 'CFPB') == 'o.json'
    assert f(None, d, 'Credit Card Mandates') == os.path.join('x', 'lt_priming_topic_credit_card_mandates.json')

def test_lt_closes_can_fail():
    f = lift(AUD, {'_lt_closes'})['_lt_closes']
    good = {'cost': 5, 'gap': .4, 'units': {'theta': 1.0, 'pressure': .6, 'recruit_pull': .1, 'money_units': .3}}
    assert f(good)
    bad = dict(good, units=dict(good['units'], money_units=.2))          # recipe 0.1 short of theta
    assert not f(bad)
    assert not f({'cost': 5, 'gap': .4})                                  # stale artifact without units
    assert not f(dict(good, cost=0))

def test_money_parity_and_inverse():
    # CMX: the COMPET_MULT table (2026-10-05) these bodies now read; c == 1.0 is the dial-off identity.
    src = {'MONEY_PARITY': True, 'HALF': 0.10, 'MAXSHIFT': 18.0, 'RFLOOR': 200000, 'CMX': {'a': 1.0, 'b': 1.0}}
    class M:  # synthetic rc
        @staticmethod
        def rc(m, k, d=1.0): return {'a': 0.5, 'b': 1.0}[m]
    names = {'pmult', 'money_units', 'dollars_for_units'}
    ns = lift(LT, names, dict(src, MBC=M, receipts=lambda m: 1e6))
    for m, pm in (('a', .5), ('b', 1.0)):
        d = ns['dollars_for_units'](m, 0.3)
        assert abs(ns['money_units'](m, d) - 0.3) < 1e-9
        r = d / 1e6                                                     # == shift_lean/MAXSHIFT = pm*r/(r+HALF)
        assert abs(pm * r / (r + 0.10) - 0.3) < 1e-9
    off = lift(LT, names, dict(src, MONEY_PARITY=False, MBC=M, receipts=lambda m: 1e6))
    assert off['dollars_for_units']('a', 0.3) == 0.10 * 1e6 * 0.3 / 0.7  # old formula exactly

def test_wiring_and_js_mirror():
    s = open(LT, encoding='utf-8').read()
    assert 'for u in sorted(proset) if u in P[v]' in s and 'MBC.dump_atomic(res,OUTF)' in s
    assert "if tier=='Opponent' and not OPP_TARGET: return None" in s
    t = open(TPL, encoding='utf-8').read()
    assert "if(tier==='Opponent'&&!K.LTOPP)return null" in t and 'ltPM(v)' in t
