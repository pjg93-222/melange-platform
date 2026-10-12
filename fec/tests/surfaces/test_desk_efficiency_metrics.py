# -*- coding: utf-8 -*-
"""F4 desk metrics (Paul 2026-09-30: "F4. Proceed"; the ROI gate follows "F1. Yes").

FIXTURE ONLY. Synthetic rows, no live tree read, nothing written outside tmp_path.

make_member_desk.py, build_dashboard_bundle.py, flip_model.py and efficiency_layers.py all execute on
import (workbook, network and artifact reads), so every function under test is lifted out of the shipped
source with ast and run on its own -- the real function bodies, none of those modules imported. Only the
import-safe kernel (moneyball_core) and district.demographic_normalization are imported.

  gap       desk_plan_fields: the plan's increment above existing CU money, never negative.
  eff_value desk_eff_values: midrank percentiles, so a row shuffle changes nothing.
  rounding  build_dashboard_bundle.opt_alloc_thousands keeps a $500 allocation non-zero, and
            efficiency_layers._load_dashboard rebuilds its exact dollars.
  ROI       desk_roi equals flip_model's own topic_setup + pflip on a fixture, counts Highly Skeptical
            as anti like the engine, and is 0 for a member the optimizer's stance gate excludes.
  peer max  2026-10-01 (Paul: "Match backend"): desk_roi and the dashboard template's flipModel divide
            peer pressure by the REAL camp maximum, 1 only when it is 0, like flip_model.topic_setup.
            Checked on a fixture whose maxima all lie strictly between 0 and 1, on the system logic
            review's own three-member fixture, and desk-vs-browser by running the template's JS in node.
Each carries a negative control showing the test fails on the pre-fix rule.
"""
from __future__ import annotations

import ast
import inspect
import json
import math
import os
import random
import shutil
import subprocess
import sys
import types

import pytest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import moneyball_core as MC  # noqa: E402  import-safe kernel: lazy loaders, reads no data at import
import district.demographic_normalization as DN  # noqa: E402  pure helpers (the desk's midrank rule)

DESK = os.path.join(FEC, 'surfaces', 'make_member_desk.py')
BUNDLE = os.path.join(FEC, 'surfaces', 'build_dashboard_bundle.py')
EFFICIENCY = os.path.join(FEC, 'engine', 'efficiency_layers.py')
FLIP_MODEL = os.path.join(FEC, 'engine', 'flip_model.py')
KERNEL = os.path.join(FEC, 'moneyball_core.py')
DASH_TPL = os.path.join(FEC, 'surfaces', 'templates', 'make_dashboard_html_00.tpl')
# QME 2026-10-01: PATH first (shutil.which), the Windows codex runtime only as a fallback; NODE is None when neither exists.
_NODE_FALLBACK = os.path.join(os.path.expanduser('~'), '.cache', 'codex-runtimes',
                              'codex-primary-runtime', 'dependencies', 'node', 'bin', 'node.exe')
NODE = shutil.which('node') or (_NODE_FALLBACK if os.path.isfile(_NODE_FALLBACK) else None)
NODE_SKIP = ("node not found: shutil.which('node') returned None and the fallback %s does not exist; "
             "install Node or put it on PATH -- the template-in-node parity checks are SKIPPED, not passed" % _NODE_FALLBACK)


def _source(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def _targets(node):
    return {sub.id for target in node.targets for sub in ast.walk(target) if isinstance(sub, ast.Name)}


def _lift(path, functions=(), assigns=(), classes=(), namespace=None):
    """Compile ONLY the named module-level defs, classes and assignments of a file."""
    tree = ast.parse(_source(path), filename=path)
    body = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in functions)
            or (isinstance(node, ast.ClassDef) and node.name in classes)
            or (isinstance(node, ast.Assign) and _targets(node) & set(assigns))]
    found = {getattr(node, 'name', None) for node in body} | {t for node in body
                                                              if isinstance(node, ast.Assign)
                                                              for t in _targets(node)}
    missing = (set(functions) | set(classes) | set(assigns)) - found
    assert not missing, '%s no longer defines %s at module scope' % (path, sorted(missing))
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=body, type_ignores=[]), path, 'exec'), ns)  # noqa: S102 - vetted defs
    return ns


DESK_NS = _lift(DESK, functions=('desk_roi', 'desk_eff_values', 'desk_plan_fields', 'desk_plan_for', 'desk_roi_for',
                                 'desk_opt_reconcile', 'desk_adj'), assigns=('TIERNAME',))
DESK_ROI, EFF_VALUES, PLAN = DESK_NS['desk_roi'], DESK_NS['desk_eff_values'], DESK_NS['desk_plan_fields']
PLAN_FOR, ROI_FOR, RECONCILE, DESK_ADJ = (DESK_NS['desk_plan_for'], DESK_NS['desk_roi_for'],
                                          DESK_NS['desk_opt_reconcile'], DESK_NS['desk_adj'])
TIERNAME = DESK_NS['TIERNAME']
LETTER = {name: letter for letter, name in TIERNAME.items()}
ENCODE = _lift(BUNDLE, functions=('opt_alloc_thousands',))['opt_alloc_thousands']


# ---------------------------------------------------------------- gap
def test_gap_is_the_plan_increment_and_never_negative():
    assert list(inspect.signature(PLAN).parameters) == ['planned']   # gap cannot depend on invested
    for planned in (0, 500, 10000, 7500.0, None):
        opt_alloc, gap = PLAN(planned)
        assert gap >= 0 and gap == opt_alloc == round(planned or 0)
    assert PLAN(-500) == (-500, 0)                         # defensive: never shown as negative
    # Sherman on the 2026-09-20 chain: $35,500 already invested, the plan adds $10,000. The desk used to
    # show opt_alloc - invested = -$25,500 in red; it now shows the $10,000 the plan adds.
    assert PLAN(10000) == (10000, 10000)


def test_gap_and_opt_alloc_are_looked_up_by_name_not_by_invested():
    # QME 2026-10-01: behavioural (was a source-text grep). The desk row's plan comes from optimize_result.alloc
    # keyed by NAME; invested never enters, a name the optimizer did not fund reads (0, 0).
    opt = {'Sherman': 10000, 'Half Grand': 500.4, 'Other': 7500}
    assert PLAN_FOR(opt, 'Sherman') == (10000, 10000)
    assert PLAN_FOR(opt, 'Half Grand') == (500, 500)
    assert PLAN_FOR(opt, 'Unfunded') == (0, 0)
    assert list(inspect.signature(PLAN_FOR).parameters) == ['opt_alloc', 'name']


def test_desk_roi_for_indexes_by_sna_idx_and_rounds_to_four_places():
    roi = [0.0, 0.123456, 0.5, 0.00004, 2.99996]
    assert ROI_FOR(roi, 1) == 0.1235 and ROI_FOR(roi, 3) == 0.0 and ROI_FOR(roi, 4) == 3.0
    assert ROI_FOR(roi, None) == 0                         # no SNA index -> 0, never roi[-1]
    # Index alignment through a fixture: desk row k carries ROI of the member at ITS sna_idx, however rows are ordered.
    eng, stance, adj, party = _desk_inputs()
    full = DESK_ROI(eng, stance, TIERNAME, adj, party)
    rows = list(reversed(range(len(NAMES))))               # desk order != SNA order
    assert [ROI_FOR(full, si) for si in rows] == [round(full[si], 4) for si in rows]
    assert ROI_FOR(full, rows[0]) == round(full[len(NAMES) - 1], 4)


def test_unmatched_optimizer_dollars_are_reported_and_counted():
    opt = {'Ann Pro': 10000, 'Ben Swing': 500, 'Zoe Ghost': 2500, 'Zero Ghost': 0}
    matched, total, unmatched = RECONCILE(opt, ['Ann Pro', 'Ben Swing', 'Cal Swing'])
    assert (matched, total, unmatched) == (10500, 13000, {'Zoe Ghost': 2500})   # a $0 unmatched name loses nothing
    assert RECONCILE(opt, list(opt))[2] == {} and RECONCILE({}, [])[:2] == (0, 0)
    # accent drift is the realistic failure: NFC vs NFD names do not match, so the caller must raise
    nfd = 'Jose\u0301 Ruiz'
    assert RECONCILE({'Jos\u00e9 Ruiz': 4000}, [nfd])[2] == {'Jos\u00e9 Ruiz': 4000}


# ---------------------------------------------------------------- eff_value
def _pre_fix_eff_values(scores, invested):
    """The rule before 2026-09-30, verbatim from make_member_desk.py: stable sort, ties by row order."""
    n = len(scores)

    def pct_rank(values):
        order = sorted(range(n), key=lambda i: (values[i] or 0))
        pr = {}
        for r, i in enumerate(order): pr[i] = (r / (n - 1)) if n > 1 else 0
        return pr
    spct, ipct = pct_rank(scores), pct_rank(invested)
    return [round(spct[i] - ipct[i], 3) for i in range(n)]


def _tied_rows():
    rng = random.Random(20260930)
    rows = []
    for k in range(80):
        invested = 0 if k < 30 else 1000 if k < 50 else 5000 if k < 60 else 7000 + 250 * k
        score = round(rng.uniform(10, 90), 1) if k % 7 else 50.0    # a tie block in score as well
        rows.append(('member %02d' % k, score, invested if k != 5 else None))
    return rows


def _by_member(rows, rule):
    values = rule([score for _n, score, _i in rows], [inv for _n, _s, inv in rows])
    return {name: value for (name, _s, _i), value in zip(rows, values)}


def _midrank(scores, invested):
    return EFF_VALUES(scores, invested, DN.midrank_percentiles)


def test_eff_value_is_order_invariant_under_ties():
    rows = _tied_rows()
    base = _by_member(rows, _midrank)
    changed = 0
    for seed in range(40):
        shuffled = list(rows)
        random.Random(seed).shuffle(shuffled)
        again = _by_member(shuffled, _midrank)
        changed += sum(1 for name in base if again[name] != base[name])
    assert changed == 0


def test_pre_fix_rule_fails_the_same_shuffle():
    rows = _tied_rows()
    base = _by_member(rows, _pre_fix_eff_values)
    shuffled = list(rows)
    random.Random(1).shuffle(shuffled)
    again = _by_member(shuffled, _pre_fix_eff_values)
    assert sum(1 for name in base if again[name] != base[name]) > 0   # the suite can fail


def test_tied_members_share_one_percentile_and_untied_rows_are_unchanged():
    rows = _tied_rows()
    values = _by_member(rows, _midrank)
    same_score_zero_invested = [name for name, score, inv in rows if score == 50.0 and not inv]
    assert len(same_score_zero_invested) >= 2
    assert len({values[name] for name in same_score_zero_invested}) == 1
    distinct = [('m%d' % k, 10.0 + k * 1.5, 100 * k + 1) for k in range(40)]
    assert (_by_member(distinct, _midrank) == _by_member(distinct, _pre_fix_eff_values))   # no ties: identical


# ---------------------------------------------------------------- rounding
def test_rounding_keeps_a_500_allocation_non_zero_and_exact():
    for dollars in range(0, 20001, 500):
        value = ENCODE(dollars)
        assert value * 1000 == dollars
        if dollars % 1000 == 0:
            assert isinstance(value, int)                  # whole thousands keep the old bytes
    assert ENCODE(500) == 0.5 and ENCODE(10500) == 10.5
    assert round(500 / 1000) == 0 and round(10500 / 1000) == 10   # negative control: the old encoding lost both
    assert abs(ENCODE(1234) * 1000 - 1234) < 1e-9
    assert json.loads(json.dumps([ENCODE(500), ENCODE(10000), ENCODE(10500)])) == [0.5, 10, 10.5]


def _load_dashboard():
    ns = _lift(EFFICIENCY, functions=('_load_json', '_number', '_integer', '_load_dashboard'),
               classes=('EfficiencyLayersError',), namespace={'json': json, 'math': math})
    return ns['_load_dashboard']


def _dashboard(tmp_path, alloc_pairs):
    doc = {'name': ['Planned Ten', 'Planned Five Hundred', 'Planned Fifteen Hundred'],
           'party': 'DRD', 'engine': {'cu': [0, 0, 0], 'ind': [0, 0, 0]},
           'opt': {'alloc': alloc_pairs}}
    path = tmp_path / 'dashboard_data.json'
    path.write_text(json.dumps(doc), encoding='utf-8')
    return str(path)


def test_efficiency_layers_rebuilds_exact_dollars_from_the_bundle(tmp_path):
    plan = {0: 10000, 1: 500, 2: 1500}
    load = _load_dashboard()
    _doc, records, present = load(_dashboard(tmp_path, [[i, ENCODE(v)] for i, v in plan.items()]))
    assert present
    assert [records[name]['engine_allocation_dollars'] for name in
            ('Planned Ten', 'Planned Five Hundred', 'Planned Fifteen Hundred')] == [10000.0, 500.0, 1500.0]
    _doc, old, _present = load(_dashboard(tmp_path, [[i, round(v / 1000)] for i, v in plan.items()]))
    assert old['Planned Five Hundred']['engine_allocation_dollars'] == 0.0      # negative control
    assert old['Planned Fifteen Hundred']['engine_allocation_dollars'] == 2000.0


# ---------------------------------------------------------------- ROI
NAMES = ['Ann Pro', 'Ben Swing', 'Cal Swing', 'Dee Deep', 'Eve Opp', 'Fay Mild']
PARTY = {'Ann Pro': 'D', 'Ben Swing': 'D', 'Cal Swing': 'R', 'Dee Deep': 'R', 'Eve Opp': 'R', 'Fay Mild': 'I'}
ISSUES = ('Interchange', 'Payments')
# Per-category cells. Dee's two Highly Skeptical cells make an overall Opponent; Eve is an overall
# Opponent who still holds a mild Payments cell. Both are behind the optimizer's stance gate.
MATRIX = {
    'Ann Pro': {'Interchange': 'Champion', 'Payments': 'Ally'},
    'Ben Swing': {'Interchange': 'Persuadable', 'Payments': 'Lean Persuadable'},
    'Cal Swing': {'Interchange': 'Highly Persuadable', 'Payments': 'True Neutral'},
    'Dee Deep': {'Interchange': 'Highly Skeptical', 'Payments': 'Highly Skeptical'},
    'Eve Opp': {'Interchange': 'Opponent', 'Credit Card Mandates': 'Opponent', 'Payments': 'Persuadable'},
    'Fay Mild': {'Payments': 'Lean Persuadable'},
}
EDGES = [('Ann Pro', 'Ben Swing', 2.0), ('Ann Pro', 'Cal Swing', 1.5), ('Ben Swing', 'Dee Deep', 1.2),
         ('Cal Swing', 'Dee Deep', 2.5), ('Ben Swing', 'Eve Opp', 1.0), ('Cal Swing', 'Fay Mild', 0.8),
         ('Dee Deep', 'Eve Opp', 1.1), ('Ann Pro', 'Fay Mild', 0.9)]
CU = [0, 5000, 0, 20000, 0, 0]
IND = [50000, 60000, 45000, 50000, 55000, 40000]
RECEIPTS = [1000000, 800000, 1200000, 1000000, 900000, 1000000]
ENGAGEMENT = [[0.5, 1.2, 0.8, 0.3, 0.9, 0.4], [0.2, 0.6, 1.1, 0.1, 0.7, 0.5]]
PRIORITY = [[1.0, 0.7, 0.6, 0.2, 0.3, 0.4], [0.9, 0.5, 0.8, 0.1, 0.2, 0.6]]
BPF = [0.3, 0.2, 0.25, 0.1, 0.15, 0.2]
PMULT = [1.0, 1.1, 0.9, 1.0, 1.2, 0.95]
DMULT = [1.0, 0.9, 1.0, 0.85, 1.0, 0.95]


def _engine_const():
    fm = _lift(FLIP_MODEL, assigns=('WM', 'WP', 'HALFF', 'BASE'), namespace={'os': os})
    return {'WM': fm['WM'], 'WP': fm['WP'], 'HALFF': fm['HALFF'], 'BASE': fm['BASE'],
            'SRCPRO': 1.7, 'SRCANTI': 0.5}


CMULT = [1.0, 1.83, 1.45, 1.12, 2.0, 1.31]      # COMPET_MULT fixture: the per-member competitiveness multiplier c (dial ON)


def _desk_inputs(scale=1.0, cmult=None):
    """The dashboard bundle's shapes: issue rows plus the appended Overall row the ROI loop skips.
    `scale` multiplies every edge weight; SUB_ONE puts every camp maximum strictly between 0 and 1."""
    overall = [MC.overall_tier(MATRIX[n]) for n in NAMES]
    stance = [''.join(LETTER[MATRIX[n].get(c, '—')] for c in ISSUES) + LETTER[o] for n, o in zip(NAMES, overall)]
    mean = [round(sum(row[k] for row in ENGAGEMENT) / len(ENGAGEMENT), 3) for k in range(len(NAMES))]
    eng = {'const': _engine_const(), 'pers': dict(MC.PERS), 'cu': CU, 'ind': IND, 'rcp': RECEIPTS,
           'tierp': [0.5] * len(NAMES), 'bpf': BPF, 'eng': ENGAGEMENT + [mean],
           'priority': PRIORITY + [PRIORITY[0]], 'pmult': PMULT, 'dmult': DMULT}
    if cmult is not None:
        eng['cmult'] = list(cmult)
    idx = {n: i for i, n in enumerate(NAMES)}
    adj = {}
    for a, b, w in EDGES:
        adj.setdefault(idx[a], []).append((idx[b], w * scale))
        adj.setdefault(idx[b], []).append((idx[a], w * scale))
    return eng, stance, adj, ''.join(PARTY[n] for n in NAMES)


SUB_ONE = 0.1


def _engine_roi(anti=None, scale=1.0, floor_at_one=False, edges=None, cmult=None):
    """flip_model's OWN topic_setup + pflip (lifted), summed like optimize.marg_at for one $10k chunk.
    Returns the ROI and each issue's (mAt, mBt). floor_at_one replays the desk/browser rule before
    2026-10-01, max(1, camp maximum), on the engine's own arithmetic: the negative control."""
    cpf = _lift(KERNEL, functions=('cpf_pressure',), namespace={'party': lambda: PARTY})['cpf_pressure']
    idx = {n: i for i, n in enumerate(NAMES)}
    stub_mc = types.SimpleNamespace(priority_base_signal=MC.priority_base_signal,
                                    delivery_mult=lambda n: DMULT[idx[n]])
    namespace = {
        'os': os, 'members': list(NAMES), '_cache': {}, '_rowcache': {}, 'PERS': MC.PERS, 'MC': stub_mc,
        'adj': {n: {} for n in NAMES}, 'cpf': cpf, 'bpf': {n: BPF[idx[n]] for n in NAMES},
        'cat2sc': {c: k for k, c in enumerate(ISSUES)},
        'eng': {k: {n: ENGAGEMENT[k][idx[n]] for n in NAMES} for k in range(len(ISSUES))},
        'topic_priority': lambda cat, mode: {n: PRIORITY[ISSUES.index(cat)][idx[n]] for n in NAMES},
    }
    for a, b, w in (EDGES if edges is None else edges):    # flip_model's name-keyed dict: last row wins
        namespace['adj'][a][b] = w * scale
        namespace['adj'][b][a] = w * scale
    fm = _lift(FLIP_MODEL, functions=('pflip', 'camp', 'topic_setup', '_stance_rows'),
               assigns=('WM', 'WP', 'HALFF', 'BASE', 'PRO', 'ANTI'), namespace=namespace)
    if anti is not None:
        fm['ANTI'] = anti
    rows = {n: dict(MATRIX[n]) for n in NAMES}
    roi = [0.0] * len(NAMES)
    maxima = []
    for c in ISSUES:
        _cs, _camp, nA, nB, mAt, mBt, tgt = fm['topic_setup'](c, rows)
        if floor_at_one:
            mAt, mBt = max(1.0, mAt), max(1.0, mBt)
        maxima.append((mAt, mBt))
        for n in NAMES:
            i = idx[n]
            args = (IND[i], RECEIPTS[i], nA[n], nB[n], mAt, mBt, PMULT[i]) + (() if cmult is None else (cmult[i],))
            roi[i] += tgt[n] * (fm['pflip'](CU[i] + 10000.0, *args) - fm['pflip'](CU[i], *args))
    return roi, maxima


def test_desk_roi_matches_the_engine_on_a_fixture():
    # 2026-10-01 (Paul: "Match backend"): the desk divides by the real camp maximum, as flip_model does.
    # Until then it floored each maximum at 1 and this test could only use a fixture with every maximum
    # >= 1; parity now has to hold there AND on the same fixture scaled so every maximum is below 1.
    for scale in (1.0, SUB_ONE):
        engine, maxima = _engine_roi(scale=scale)
        eng, stance, adj, party = _desk_inputs(scale)
        desk = DESK_ROI(eng, stance, TIERNAME, adj, party)
        assert all(abs(d - e) <= 1e-12 for d, e in zip(desk, engine)), (scale, list(zip(desk, engine)))
        assert sum(1 for value in engine if value > 0) >= 4              # a non-trivial fixture
    assert all(0.0 < m < 1.0 for pair in maxima for m in pair), maxima     # the case the ruling changed


def test_pre_ruling_floor_fails_the_sub_one_parity():
    eng, stance, adj, party = _desk_inputs(SUB_ONE)
    desk = DESK_ROI(eng, stance, TIERNAME, adj, party)
    floored, _maxima = _engine_roi(scale=SUB_ONE, floor_at_one=True)
    assert max(abs(d - f) for d, f in zip(desk, floored)) > 1e-6       # the parity test can fail


# The system logic review's independent fixture (audits/qme_fix_round_20260930/SYSTEM_LOGIC_REVIEW.md,
# P2 finding; quant_logic_review/quant_logic_review.py): a pro, an anti and a neutral member, CU $1,000,
# industry $2,000, receipts $200,000, persuadability .5, engagement 1, priority contribution .15. The
# neutral member's next-$10,000 ROI is 0.575 * (pflip(11000) - pflip(1000)). The review spelled True
# Neutral with a private letter 'N'; the shipped letter is 'T' (TIERNAME and the template's SCN), so the
# same fixture runs through the desk and the browser unchanged.
REVIEW_BACKEND = 0.021468455244484982    # backend algebra; the same at camp maximum .1 and 2
REVIEW_FLOORED = 0.11724112402938276     # the desk at camp maximum .1 before the ruling


def _review_fixture(weight):
    eng = {'const': {'WM': .35, 'WP': .65, 'HALFF': .1, 'BASE': .15, 'SRCPRO': 1., 'SRCANTI': 1.},
           'pers': {'Champion': 0., 'Opponent': 0., 'True Neutral': .5},
           'cu': [1000.] * 3, 'ind': [2000.] * 3, 'rcp': [200000.] * 3, 'tierp': [1.] * 3, 'bpf': [0.] * 3,
           'eng': [[1.] * 3, [1.] * 3]}
    adj = {0: [(2, weight)], 1: [(2, weight)], 2: [(0, weight), (1, weight)]}
    return eng, ['CC', 'OO', 'TT'], adj, 'DDD'


def _review_algebra(weight, floor_at_one=False):
    """Independent flip_model algebra for the neutral member: both camp maxima equal `weight`."""
    peak = max(1.0, weight) if floor_at_one else (weight or 1.0)
    def pf(cu):
        a = .35 * (cu + 1) / (cu + 1 + .1 * 200000) + .65 * weight / peak
        b = .35 * 2001 / (2001 + .1 * 200000) + .65 * weight / peak
        return a / (a + b)
    return (1 + .15 * 1) * .5 * (pf(11000) - pf(1000))


def test_review_fixture_desk_equals_backend_algebra():
    for weight in (0.1, 2.0):                    # the exposed camp maximum, and the max-2 control
        eng, stance, adj, party = _review_fixture(weight)
        desk = DESK_ROI(eng, stance, TIERNAME, adj, party)
        assert abs(desk[2] - _review_algebra(weight)) <= 1e-12, (weight, desk)
        assert abs(desk[2] - REVIEW_BACKEND) <= 1e-12, (weight, desk)
        assert desk[0] == desk[1] == 0.0                                  # zero persuadability
        assert DESK_ROI(eng, stance, TIERNAME, adj, party, gated={2})[2] == 0.0
    # Negative control: the old floor reproduces the review's pre-ruling desk value, at .1 only.
    assert abs(_review_algebra(0.1, floor_at_one=True) - REVIEW_FLOORED) <= 1e-12
    assert abs(_review_algebra(2.0, floor_at_one=True) - REVIEW_BACKEND) <= 1e-12
    assert REVIEW_FLOORED - REVIEW_BACKEND > 0.09


def _browser_roi(eng, stance, adj, party, rows=None):
    """The dashboard template's OWN flipModel and align, sliced from the .tpl as test_dashboard_honors
    does, run under node: the next-$10k finite difference per member over the real issues, plus each
    issue's (mA, mB). With `rows` (raw [u, v, w] engine rows) ADJ is built by the template's OWN builder line,
    `for(const [u,v,w] of EG.adj){ADJ[u][v]=w;ADJ[v][u]=w;}`, so duplicate rows resolve exactly as in the page."""
    if not NODE:
        pytest.skip(NODE_SKIP)
    source = _source(DASH_TPL)
    engine = source[source.index('const SCN='):source.index('// stance-integrity gates')]
    align = next(line for line in source.splitlines() if line.startswith('function align('))
    n = len(stance)
    fixture = {'DATA': {'stance': stance},
               'EG': {'pers': eng['pers'], 'theta': {}, 'eng': eng['eng'], 'priority': eng.get('priority'),
                      'tierp': eng['tierp'], 'rcp': eng['rcp'], 'cmult': eng.get('cmult')},
               'K': eng['const'], 'PM': eng.get('pmult'), 'DM': eng.get('dmult'), 'BPF': eng['bpf'],
               'ADJ': [{str(v): w for v, w in adj.get(u, [])} for u in range(n)], 'PTY': list(party),
               'cu': eng['cu'], 'ind': eng['ind']}
    build = ''
    if rows is not None:
        fixture['EG']['adj'] = rows
        build = next(line for line in source.splitlines() if line.startswith('let ADJ=Array.from({length:N}'))
        build = build.split('//')[0].replace('let ADJ=', 'ADJ=', 1)
    script = '''
const F=__FIXTURE__;const DATA=F.DATA,EG=F.EG,K=F.K,PM=F.PM,DM=F.DM,BPF=F.BPF,PTY=F.PTY,N=DATA.stance.length;let ADJ=F.ADJ;
__BUILD__
__ALIGN__
__ENGINE__
const roi=new Array(N).fill(0),peaks=[];
for(let i=0;i<EG.eng.length-1;i++){const t=baseTiers(i),base=flipModel(i,t,F.cu,F.ind);peaks.push([base.mA,base.mB]);
  for(let u=0;u<N;u++){const cu=F.cu.slice();cu[u]+=10000;roi[u]+=base.tg[u]*(flipModel(i,t,cu,F.ind).pf[u]-base.pf[u]);}}
console.log(JSON.stringify({roi,peaks}));
'''.replace('__FIXTURE__', json.dumps(fixture)).replace('__BUILD__', build).replace('__ALIGN__', align).replace('__ENGINE__', engine)
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    return out['roi'], [tuple(pair) for pair in out['peaks']]


def test_browser_flip_model_matches_the_desk_and_the_backend_peer_maximum():
    for scale in (1.0, SUB_ONE):
        eng, stance, adj, party = _desk_inputs(scale)
        desk = DESK_ROI(eng, stance, TIERNAME, adj, party)
        browser, peaks = _browser_roi(eng, stance, adj, party)
        _engine, maxima = _engine_roi(scale=scale)
        assert all(abs(b - d) <= 1e-12 for b, d in zip(browser, desk)), (scale, list(zip(browser, desk)))
        assert all(abs(p - m) <= 1e-12 for pp, mm in zip(peaks, maxima) for p, m in zip(pp, mm)), (peaks, maxima)
    for weight in (0.1, 2.0):
        eng, stance, adj, party = _review_fixture(weight)
        browser, peaks = _browser_roi(eng, stance, adj, party)
        assert abs(browser[2] - REVIEW_BACKEND) <= 1e-12, (weight, browser)
        assert peaks == [(weight, weight)]
    # (the template's no-floor rule is the peaks equality above at scale SUB_ONE, where a floor at 1 would show)


def test_highly_skeptical_neighbours_count_as_anti_like_the_engine():
    eng, stance, adj, party = _desk_inputs()
    desk = DESK_ROI(eng, stance, TIERNAME, adj, party)
    pre_fix, _floors = _engine_roi(anti={'Opponent', 'Skeptic'})     # 'K' neutral, the old desk letters
    assert max(abs(d - p) for d, p in zip(desk, pre_fix)) > 1e-6         # the parity test can fail


def test_stance_gated_members_show_no_roi():
    eng, stance, adj, party = _desk_inputs()
    tiers = MC.stance_gate_tiers({'matrix': MATRIX, 'gate': {}}, NAMES)
    gated = frozenset(i for i, n in enumerate(NAMES) if tiers[n] in MC.STANCE_GATE_BLOCKED)
    assert {NAMES[i] for i in gated} == {'Dee Deep', 'Eve Opp'}
    open_roi = DESK_ROI(eng, stance, TIERNAME, adj, party)
    gated_roi = DESK_ROI(eng, stance, TIERNAME, adj, party, gated=gated)
    assert all(open_roi[i] > 0 for i in gated)                           # the gate is not vacuous here
    assert all(gated_roi[i] == 0.0 for i in gated)
    assert all(gated_roi[i] == open_roi[i] for i in range(len(NAMES)) if i not in gated)


def test_desk_reads_the_gate_from_the_kernel_helper():
    compact = _source(DESK).replace(' ', '')
    assert '_GATE_TIERS=MBC.stance_gate_tiers(CS,snames)' in compact
    assert 'if_GATE_TIERS.get(n)inMBC.STANCE_GATE_BLOCKED' in compact
    assert 'ROI=desk_roi(eng,STANCE,TIERNAME,ADJ,PARTY_SNA,gated=ROI_GATED,chunk=CHUNK)' in compact



# ---------------------------------------------------------------- adjacency duplicates
def _rows(edges, scale=1.0):
    idx = {n: i for i, n in enumerate(NAMES)}
    return [[idx[a], idx[b], w * scale] for a, b, w in edges]


def _append_adj(rows):
    """The desk's rule before 2026-10-01: both directions of EVERY row appended, so a pair listed twice is summed."""
    adj = {}
    for u, v, w in rows:
        adj.setdefault(u, []).append((v, w))
        adj.setdefault(v, []).append((u, w))
    return adj


# Ann-Ben listed twice, the second time reversed and at another weight (a duplicated reciprocal row).
DUP_EDGES = EDGES + [('Ben Swing', 'Ann Pro', 0.4)]


def test_desk_adj_keeps_one_weight_per_pair_last_row_wins():
    assert DESK_ADJ([[0, 1, 2.0], [1, 0, 0.4]]) == {0: [(1, 0.4)], 1: [(0, 0.4)]}
    assert DESK_ADJ([[0, 1, 2.0], [0, 1, 3.0], [1, 2, 1.0]]) == {0: [(1, 3.0)], 1: [(0, 3.0), (2, 1.0)], 2: [(1, 1.0)]}
    assert DESK_ADJ([]) == {}
    assert _append_adj([[0, 1, 2.0], [1, 0, 0.4]])[0] == [(1, 2.0), (1, 0.4)]     # the old rule kept both


def test_desk_browser_and_backend_agree_on_a_duplicated_reciprocal_row():
    eng, stance, _adj, party = _desk_inputs()
    rows = _rows(DUP_EDGES)
    desk = DESK_ROI(eng, stance, TIERNAME, DESK_ADJ(rows), party)
    backend, _maxima = _engine_roi(edges=DUP_EDGES)
    assert all(abs(d - b) <= 1e-12 for d, b in zip(desk, backend)), list(zip(desk, backend))
    browser, _peaks = _browser_roi(eng, stance, DESK_ADJ(rows), party, rows=rows)
    assert all(abs(d - b) <= 1e-12 for d, b in zip(desk, browser)), list(zip(desk, browser))
    # negative control: the pre-fix append rule double counts the Ann-Ben pair and is not the backend
    old = DESK_ROI(eng, stance, TIERNAME, _append_adj(rows), party)
    assert max(abs(o - b) for o, b in zip(old, backend)) > 1e-6
    # and the duplicate is not a no-op: dropping it moves the backend
    plain, _m = _engine_roi(edges=EDGES)
    assert max(abs(a - b) for a, b in zip(plain, backend)) > 1e-6


# ---------------------------------------------------------------- camp / party tokens
def test_empty_or_multichar_tokens_are_neither_camp_nor_party():
    eng, stance, adj, party = _desk_inputs()
    base = DESK_ROI(eng, stance, TIERNAME, adj, party)
    # a '' / 'DR' party token used to satisfy `pu in 'DR'` (substring) and score as co-partisan
    for token in ('', 'DR'):
        odd = DESK_ROI(eng, stance, TIERNAME, adj, [token] * len(NAMES))
        neutral = DESK_ROI(eng, stance, TIERNAME, adj, ['?'] * len(NAMES))
        assert odd == neutral, token
    assert DESK_ROI(eng, stance, TIERNAME, adj, ['?'] * len(NAMES)) != base           # party does matter here
    # a '' tier on a neighbour used to be 'pro' (`'' in 'CFA'`); it must equal a no-evidence '-' neighbour
    rows = [list(row) for row in stance]
    other = [list(row) for row in stance]
    for row in rows: row[0] = ''
    for row in other: row[0] = '-'
    assert DESK_ROI(eng, rows, TIERNAME, adj, party) == DESK_ROI(eng, other, TIERNAME, adj, party)


def test_template_camp_and_align_treat_empty_and_undefined_as_neutral():
    if not NODE:
        pytest.skip(NODE_SKIP)
    source = _source(DASH_TPL)
    camp = next(line for line in source.splitlines() if line.startswith('const CAMP='))
    align = next(line for line in source.splitlines() if line.startswith('function align('))
    script = ('const K={SRCPRO:1.7,SRCANTI:0.5};let PTY=[];\n' + camp + '\n' + align + '\n' +
              'const out={camp:["C","F","A","O","S","K","T","H","-","","CF",undefined].map(CAMP),'
              'align:[["D","D"],["D","R"],["R","D"],["I","D"],["","D"],["",""],["DR","DR"],[undefined,undefined]]'
              '.map(([a,b])=>{PTY=[a,b];return align(0,1);})};console.log(JSON.stringify(out));')
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out['camp'] == ['pro', 'pro', 'pro', 'anti', 'anti', 'anti', 'neu', 'neu', 'neu', 'neu', 'neu', 'neu']
    assert out['align'] == [1.7, 0.5, 0.5, 1, 1, 1, 1, 1]


# ---------------------------------------------------------------- COMPET_MULT: c on the desk, the engine and the browser
def test_compet_desk_equals_lifted_flip_model_equals_browser_within_1e12():
    for scale in (1.0, SUB_ONE):
        eng, stance, adj, party = _desk_inputs(scale, cmult=CMULT)
        desk = DESK_ROI(eng, stance, TIERNAME, adj, party)
        engine, _maxima = _engine_roi(scale=scale, cmult=CMULT)
        browser, _peaks = _browser_roi(eng, stance, adj, party)
        assert all(abs(d - e) <= 1e-12 for d, e in zip(desk, engine)), (scale, list(zip(desk, engine)))
        assert all(abs(d - b) <= 1e-12 for d, b in zip(desk, browser)), (scale, list(zip(desk, browser)))
        eng0, stance0, adj0, party0 = _desk_inputs(scale)
        off = DESK_ROI(eng0, stance0, TIERNAME, adj0, party0)
        assert max(abs(a - b) for a, b in zip(desk, off)) > 1e-4         # c moves the ROI: the parity above is not vacuous


def test_compet_negative_control_a_desk_that_ignores_c_fails_parity():
    eng_c, stance, adj, party = _desk_inputs(cmult=CMULT)
    eng_blind = {k: v for k, v in eng_c.items() if k != 'cmult'}          # the desk before COMPET_MULT: no c
    blind = DESK_ROI(eng_blind, stance, TIERNAME, adj, party)
    engine, _maxima = _engine_roi(cmult=CMULT)
    assert max(abs(b - e) for b, e in zip(blind, engine)) > 1e-4
    browser, _peaks = _browser_roi(eng_c, stance, adj, party)
    assert max(abs(b - w) for b, w in zip(blind, browser)) > 1e-4


def test_compet_all_ones_cmult_is_bitwise_the_no_cmult_desk():
    eng, stance, adj, party = _desk_inputs()
    ones = dict(eng, cmult=[1.0] * len(NAMES))
    assert DESK_ROI(ones, stance, TIERNAME, adj, party) == DESK_ROI(eng, stance, TIERNAME, adj, party)    # ==, not within a tolerance
    assert DESK_ROI(dict(eng, cmult=[]), stance, TIERNAME, adj, party) == DESK_ROI(eng, stance, TIERNAME, adj, party)   # empty = absent
    base, _m = _engine_roi()                                   # and the engine (no cm argument) still agrees within 1e-12
    assert all(abs(a - b) <= 1e-12 for a, b in zip(DESK_ROI(ones, stance, TIERNAME, adj, party), base))


def test_compet_browser_shift_lean_dollars_for_units_and_break_even_carry_c():
    if not NODE:
        pytest.skip(NODE_SKIP)
    source = _source(DASH_TPL)
    engine = source[source.index('const SCN='):source.index('// stance-integrity gates')]
    pick = lambda prefix: next(line for line in engine.splitlines() if line.startswith(prefix))
    need_line = next(line for line in source.splitlines() if 'const need=Math.max(0' in line)
    start = need_line.index('const need=')
    need_stmt = need_line[start:need_line.index(';', start) + 1].replace('EG.cu[sel]', 'F.cu[sel]')
    fixture = {'EG': {'rcp': RECEIPTS, 'cmult': CMULT},
               'K': {'MAXSHIFT': 18, 'HALF': 0.10, 'HALFF': 0.10, 'LTPAR': 1, 'LTHALF': 0.10},
               'PM': PMULT, 'cu': CU}
    script = chr(10).join([
        'const F=%s;const EG=F.EG,K=F.K,PM=F.PM;' % json.dumps(fixture),
        pick('function shiftLean'), pick('function ltPM'), pick('function dollarsForUnits'),
        'const ids=[0,1,2,3,4,5];',
        'const need=sel=>{const q=0.4,cmS=EG.cmult[sel];%s return need;};' % need_stmt,
        'console.log(JSON.stringify({shift:ids.map(u=>shiftLean(u,25000)),dfu:ids.map(u=>dollarsForUnits(u,0.4)),need:[1,2,4].map(need)}));'])
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    for u in range(len(NAMES)):
        r = CMULT[u] * 25000 / RECEIPTS[u]
        assert abs(out['shift'][u] - 18 * (r / (r + 0.10)) * PMULT[u]) <= 1e-12            # shift_lean: r = c*amount/receipts
        units = min(0.4 / max(PMULT[u], 1e-9), 0.98)
        assert abs(out['dfu'][u] - 0.10 * RECEIPTS[u] * units / (1 - units) / CMULT[u]) <= 1e-9   # real $ = effective $ / c
    for sel, dollars in zip((1, 2, 4), out['need']):                                        # break-even: the CU $ whose c-scaled pull equals q
        assert dollars > 0
        eff = CMULT[sel] * (CU[sel] + dollars)
        assert abs((eff + 1) / (eff + 1 + 0.10 * RECEIPTS[sel]) - 0.4) <= 1e-5, (sel, dollars)


def _template_pflip_terms(source):
    """The template's OWN pflipOf and its head-to-head A / Bv lines (runScenario), sliced from `source` and run under node on a fixture
    with a non-1 CMULT. Returns (pflipOf results per (member, camp, money) case, head-to-head CU share per case)."""
    engine = source[source.index('const SCN='):source.index('// stance-integrity gates')]
    pick = lambda text, prefix: next(line.strip() for line in text.splitlines() if line.strip().startswith(prefix))
    c_line = pick(source, 'const c=baseCache[issue],pm=PM?PM[sel]:1')
    a_line, b_line = pick(source, 'const A=K.WM*pm*'), pick(source, 'const Bv=K.WM*pm*')
    cases = [(u, nA, nB, cu, ind, d_cu, d_co) for u in (1, 2, 4, 5) for nA, nB in ((0.8, 0.4), (0.3, 1.1))
             for cu, ind in ((0, 0), (5000, 60000)) for d_cu, d_co in ((0, 0), (10000, 0), (0, 25000))]
    fixture = {'EG': {'rcp': RECEIPTS, 'cmult': CMULT, 'cu': CU, 'ind': IND},
               'K': {'WM': 0.35, 'WP': 0.65, 'HALFF': 0.10}, 'PM': PMULT, 'cases': cases}
    script = chr(10).join([
        'const F=%s;const EG=F.EG,K=F.K,PM=F.PM;' % json.dumps(fixture),
        pick(engine, 'function pflipOf'),
        'const out={p:[],h:[]};',
        'for(const [u,nA,nB,cu0,ind0,cu,co] of F.cases){out.p.push(pflipOf(u,nA,nB,2.0,1.5,cu0,ind0));',
        '  const sel=u,issue=0,baseCache=[{nAsel:nA,nBsel:nB,mA:2.0,mB:1.5}];',
        c_line, a_line, b_line, '  out.h.push(A/(A+Bv));}',
        'console.log(JSON.stringify(out));'])
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    return cases, json.loads(result.stdout)


def _template_vs_lifted_pflip_gap(source):
    flip = _lift(FLIP_MODEL, functions=('pflip',), assigns=('WM', 'WP', 'HALFF'), namespace={'os': os})['pflip']
    cases, out = _template_pflip_terms(source)
    worst = 0.0
    for (u, nA, nB, cu, ind, dcu, dco), p, h in zip(cases, out['p'], out['h']):
        want_p = flip(cu, ind, RECEIPTS[u], nA, nB, 2.0, 1.5, PMULT[u], CMULT[u])
        want_h = flip(CU[u] + dcu, IND[u] + dco, RECEIPTS[u], nA, nB, 2.0, 1.5, PMULT[u], CMULT[u])
        worst = max(worst, abs(p - want_p), abs(h - want_h))
    return worst


def test_compet_template_pflip_of_and_head_to_head_match_lifted_pflip_and_fail_without_cm():
    if not NODE:
        pytest.skip(NODE_SKIP)
    source = _source(DASH_TPL)
    assert _template_vs_lifted_pflip_gap(source) <= 1e-12                      # pflipOf and the A/Bv head-to-head carry c on both legs
    # negative controls: the same slices with cm removed (in-memory mutations of the template text) must FAIL the parity
    no_cm_pflip = source.replace('cm=EG.cmult?EG.cmult[u]:1;const a=K.WM*pm*(cm*cu+1)', 'cm=1;const a=K.WM*pm*(cm*cu+1)')
    assert no_cm_pflip != source and _template_vs_lifted_pflip_gap(no_cm_pflip) > 1e-6
    no_cm_ind = source.replace('(cm*ind+1)/((cm*ind+1)', '(ind+1)/((ind+1)')
    assert no_cm_ind != source and _template_vs_lifted_pflip_gap(no_cm_ind) > 1e-6
    no_cm_h2h = source.replace('cmS=EG.cmult?EG.cmult[sel]:1;', 'cmS=1;')
    assert no_cm_h2h != source and _template_vs_lifted_pflip_gap(no_cm_h2h) > 1e-6


# ---------------------------------------------------------------- flip / coal thousands
def test_flip_coal_and_overall_thousands_keep_the_dollars():
    flip = {'a': 10500, 'b': 10000, 'c': 25500}
    coal = {'a': 500, 'b': 3000, 'c': 1234}
    assert {k: ENCODE(v) for k, v in flip.items()} == {'a': 10.5, 'b': 10, 'c': 25.5}
    assert {k: ENCODE(v) for k, v in coal.items()} == {'a': 0.5, 'b': 3, 'c': 1.234}
    assert round(10500 / 1000) == 10 and round(500 / 1000) == 0                # the old half-even loss
    # Overall layer = sum over issues, encoded once from the dollar sum (not a sum of rounded thousands)
    per_issue = [10500, 10500, 500]
    assert ENCODE(sum(per_issue)) == 21.5 and sum(round(v / 1000) for v in per_issue) == 20
    # the template's "$Nk" formatting prints exact thousands and no float noise
    if NODE:
        source = _source(DASH_TPL)
        kfmt = next(line for line in source.splitlines() if line.startswith('function kfmt('))
        script = kfmt + '\nconsole.log(JSON.stringify([10,10.5,0.5,1.234,0.30000000000000004].map(kfmt)));'
        result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == ['10', '10.5', '0.5', '1.234', '0.3']


def test_template_self_check_tolerance_is_tight_and_skips_a_zero_reference():
    if not NODE:
        pytest.skip(NODE_SKIP)
    source = _source(DASH_TPL)
    start = source.index('const t=flipModel(issue,baseTiers(issue),EG.cu,EG.ind).total,ok=')
    expression = source[start:source.index(';', start)].split('ok=', 1)[1]
    script = ('const ok=(t,r)=>' + expression + ';console.log(JSON.stringify([ok(1.0,1.01),ok(1.0,1.05),ok(0.5,0.51),'
              'ok(0.5,0.55),ok(10,10.09),ok(10,10.2)]));')
    result = subprocess.run([NODE, '-e', script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    # old rule max(0.2, 3%) passed 0.55 vs 0.5 and 1.05 vs 1.0; the new max(0.02, 1%) fails both
    assert json.loads(result.stdout) == [True, False, True, False, True, False]
    assert 'if(!r){' in source[source.index('function updateEngChk'):start]              # refflip 0 (Overall) still skipped


# ---------------------------------------------------------------- empty / all-zero allocation
def test_empty_or_all_zero_allocation_is_absent_so_the_fallback_runs(tmp_path):
    load = _load_dashboard()
    for pairs in ([], [[0, 0], [1, 0.0]]):
        _doc, records, present = load(_dashboard(tmp_path, pairs))
        assert present is False, pairs
        assert all(rec['engine_allocation_dollars'] is None for rec in records.values())
    _doc, records, present = load(_dashboard(tmp_path, [[0, 0], [1, 0.5]]))
    assert present is True and records['Planned Five Hundred']['engine_allocation_dollars'] == 500.0
