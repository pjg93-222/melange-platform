# -*- coding: utf-8 -*-
"""F1: the joint optimizer inherits the system-level stance gate (Paul 2026-09-30: "F1. Yes").

FIXTURE ONLY. Synthetic members, no live tree read, nothing written.

optimize.py, flip_model.py and per_topic_flip.py all execute on import (they read congestion.json, the
workbook, the network and category_stance.json): the "import == execution" hazard this repo bans. So this
suite lifts optimize.py's REAL module statements with ast -- the gate, the marginal functions, the lazy
greedy loop and the certificate -- and runs them against a synthetic flip model whose pflip is
flip_model's own function body, lifted the same way. None of those three files is imported.

Pinned here, each with a negative control that runs the SAME lifted code with the gate set emptied:
  * a stance-gated member receives $0, and the gate changes no ungated member's marginal;
  * with a budget that does not bind, every ungated member's allocation is identical with and without
    the gate; with one that binds, the gated members' money is re-homed and the whole pot is spent;
  * the certificate stays correct: the plan is the exact optimum over fundable members, the G-K bound
    covers it, and the concavity check and conversion_reachable range over fundable members only;
  * per_topic_flip's output is unchanged by construction: the kernel helper returns exactly its old
    inline OVT expression, and every copy of the blocked set in the repo agrees.
"""
from __future__ import annotations

import ast
import collections
import heapq
import itertools
import os
import re
import sys
import types

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path:
    sys.path.insert(0, FEC)
import moneyball_core as MC  # noqa: E402  import-safe kernel: lazy loaders, reads no data at import

OPTIMIZE = os.path.join(FEC, 'engine', 'optimize.py')
FLIP_MODEL = os.path.join(FEC, 'engine', 'flip_model.py')
PER_TOPIC = os.path.join(FEC, 'engine', 'per_topic_flip.py')
LT_PRIMING = os.path.join(FEC, 'engine', 'lt_priming.py')
DASH_TPL = os.path.join(FEC, 'surfaces', 'templates', 'make_dashboard_html_00.tpl')
DESK = os.path.join(FEC, 'surfaces', 'make_member_desk.py')


def _tree(path):
    with open(path, encoding='utf-8') as handle:
        source = handle.read()
    return source, ast.parse(source, filename=path)


def _compact(path):
    return _tree(path)[0].replace(' ', '')


def _targets(node):
    return {sub.id for target in node.targets for sub in ast.walk(target) if isinstance(sub, ast.Name)}


def _lift(path, functions=(), assigns=(), namespace=None):
    """Compile ONLY the named module-level defs/assignments of a file into a fresh namespace."""
    _source, tree = _tree(path)
    body = [node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in functions)
            or (isinstance(node, ast.Assign) and _targets(node) & set(assigns))]
    ns = dict(namespace or {})
    exec(compile(ast.Module(body=body, type_ignores=[]), path, 'exec'), ns)  # noqa: S102 - vetted defs
    return ns


def _optimizer_slice(last_target, inclusive):
    """optimize.py's module statements from the gate assignment (OVT=) to the named assignment."""
    _source, tree = _tree(OPTIMIZE)
    start = next(i for i, node in enumerate(tree.body)
                 if isinstance(node, ast.Assign) and 'OVT' in _targets(node))
    stop = next(i for i, node in enumerate(tree.body)
                if isinstance(node, ast.Assign) and last_target in _targets(node))
    assert start < stop, 'optimize.py no longer computes the gate before %s' % last_target
    body = tree.body[start:stop + (1 if inclusive else 0)]
    assert not any(isinstance(sub, ast.Name) and sub.id == 'dump_atomic'
                   for node in body for sub in ast.walk(node)), 'the lifted slice must not write'
    return compile(ast.Module(body=body, type_ignores=[]), OPTIMIZE, 'exec')


# The gate, the functions, the greedy loop and the certificate, through `guarantee=` (never the write).
OPT_BLOCK = _optimizer_slice('guarantee', inclusive=True)
# The same slice stopping before the first objective evaluation: the gate, the state and the functions.
OPT_SETUP = _optimizer_slice('base', inclusive=False)
# flip_model's own pflip and the constants it closes over.
PFLIP = _lift(FLIP_MODEL, functions=('pflip',), assigns=('WM', 'WP', 'HALFF'), namespace={'os': os})['pflip']

TOPICS = ('Interchange', 'Payments')
# Per-category stance cells. The gate reads the IMP-weighted overall tier (moneyball_core.overall_tier),
# so a gated member can still hold a mild cell -- exactly the case per_topic_flip.py:34-35 describes.
MATRIX = {
    'Gated Opponent': {'Interchange': 'Opponent', 'Credit Card Mandates': 'Opponent', 'Payments': 'Persuadable'},
    'Gated Highly Skeptical': {'Interchange': 'Highly Skeptical', 'CU Tax Status': 'Skeptic',
                               'Payments': 'Lean Persuadable'},
    'Gated Skeptic': {'Interchange': 'Skeptic', 'Payments': 'True Neutral'},
    'Lean Skeptic Member': {'Interchange': 'Lean Skeptic', 'Payments': 'Persuadable'},
    'Persuadable A': {'Interchange': 'Persuadable', 'Payments': 'Persuadable'},
    'Persuadable B': {'Interchange': 'Highly Persuadable', 'Payments': 'Lean Persuadable'},
}
MEMBERS = list(MATRIX)
GATED = {'Gated Opponent', 'Gated Highly Skeptical', 'Gated Skeptic'}
FUNDABLE = [m for m in MEMBERS if m not in GATED]
# The gated members carry the largest target weights, so an ungated optimizer funds them first.
WEIGHT = {'Gated Opponent': 9.0, 'Gated Highly Skeptical': 8.0, 'Gated Skeptic': 7.0,
          'Lean Skeptic Member': 2.0, 'Persuadable A': 1.5, 'Persuadable B': 1.0}
R_MARK = 777777
RECEIPTS = {m: (R_MARK if m == 'Gated Highly Skeptical' else 1000000) for m in MEMBERS}
CU0 = {m: (20000 if m == 'Persuadable A' else 0) for m in MEMBERS}   # existing CU money: headroom sits above it
IND0 = {m: 50000 for m in MEMBERS}
INC, PMAX = 5000, 10000


def _flip_model(pflip=PFLIP):
    tgt = {c: {m: MC.PERS[MATRIX[m][c]] * WEIGHT[m] for m in MEMBERS} for c in TOPICS}
    ones = {m: 1.0 for m in MEMBERS}

    def topic_setup(c, MAT):
        return {m: MAT[m][c] for m in MEMBERS}, {}, ones, ones, 2.0, 2.0, tgt[c]

    def flip_value(c, MAT, cu_state, ind_state):
        _cs, _camp, nA, nB, mAt, mBt, t = topic_setup(c, MAT)
        return sum(t[m] * pflip(cu_state[m], ind_state[m], RECEIPTS[m], nA[m], nB[m], mAt, mBt, 1.0)
                   for m in MEMBERS)

    return types.SimpleNamespace(
        CS0=MATRIX, _CS_DOC={'matrix': MATRIX, 'gate': {'overall_capped': {}}},
        cu0=dict(CU0), ind0=dict(IND0), R=dict(RECEIPTS), rc=lambda m, key, d=1.0: 1.0,
        CM={m: 1.0 for m in MEMBERS},      # COMPET_MULT off: every competitiveness multiplier exactly 1.0 (optimize.py: CM=fm.CM)
        topic_setup=topic_setup, pflip=pflip, flip_value=flip_value, _cache={})


def _money_response(reachable=()):
    """No modeled conversion fires; `reachable` members could convert at PMAX (conversion_reachable)."""
    return types.SimpleNamespace(
        OV={m: 'Persuadable' for m in MEMBERS}, COMMITTED={'Champion', 'Opponent'}, DTHRESH=11.25,
        shift_lean=lambda m, source, amount, cm=1.0: 20.0 if m in reachable else 0.5,
        defection=lambda m, dlean: None)


def _convex_for(mark):
    """flip_model's pflip, except rising chunk returns for the member whose receipts equal `mark`."""
    def pflip(cu_n, ind_n, R_n, nA_n, nB_n, mAt, mBt, pm=1.0, cm=1.0):
        if R_n == mark:
            return min(0.99, (cu_n / 20000.0) ** 2)
        return PFLIP(cu_n, ind_n, R_n, nA_n, nB_n, mAt, mBt, pm, cm)
    return pflip


def _run(block, budget, gate=True, fm=None, mr=None):
    ns = {'fm': fm or _flip_model(), 'mr': mr or _money_response(), 'members': list(MEMBERS),
          'cats': list(TOPICS), 'INC': INC, 'BUDGET': budget, 'PMAX': PMAX, 'ONLY': None,
          'stance_gate_tiers': MC.stance_gate_tiers,
          'STANCE_GATE_BLOCKED': MC.STANCE_GATE_BLOCKED if gate else frozenset(),
          'os': os, 'heapq': heapq, 'collections': collections}
    exec(block, ns)  # noqa: S102 - optimize.py's own statements, lifted above
    return ns


def test_fixture_overall_tiers_are_what_the_gate_reads():
    tiers = MC.stance_gate_tiers({'matrix': MATRIX, 'gate': {}}, MEMBERS)
    assert {m for m, t in tiers.items() if t in MC.STANCE_GATE_BLOCKED} == GATED
    assert {tiers[m] for m in GATED} == {'Opponent', 'Highly Skeptical', 'Skeptic'}
    assert tiers['Lean Skeptic Member'] == 'Lean Skeptic'      # the boundary tier stays fundable
    assert all(MC.PERS[MATRIX[m]['Payments']] > 0 for m in GATED)   # each gated member has a mild cell


def test_gated_members_get_zero_and_the_pot_is_rehomed():
    budget = 4 * INC                                       # capacity is 12 chunks, so the budget binds
    open_run = _run(OPT_BLOCK, budget, gate=False)
    gated_run = _run(OPT_BLOCK, budget, gate=True)
    assert any(open_run['alloc'][m] > 0 for m in GATED)    # negative control: ungated, they are funded
    assert set(gated_run['GATED']) == GATED
    assert all(gated_run['alloc'][m] == 0 for m in GATED)
    assert gated_run['spent'] == open_run['spent'] == budget == sum(gated_run['alloc'].values())


def test_ungated_allocations_are_unaffected_when_the_budget_does_not_bind():
    budget = 40 * INC                                      # far above the 6 x PMAX capacity
    open_run = _run(OPT_BLOCK, budget, gate=False)
    gated_run = _run(OPT_BLOCK, budget, gate=True)
    for m in FUNDABLE:
        assert gated_run['alloc'][m] == open_run['alloc'][m] == PMAX
    assert all(gated_run['alloc'][m] == 0 for m in GATED)
    assert all(open_run['alloc'][m] == PMAX for m in GATED)


def test_the_gate_changes_only_gated_marginals():
    gated_run = _run(OPT_SETUP, 4 * INC, gate=True)
    open_run = _run(OPT_SETUP, 4 * INC, gate=False)
    for m in MEMBERS:
        if m in GATED:
            assert gated_run['marg'](m) == 0.0 < open_run['marg'](m)
        else:
            assert gated_run['marg'](m) == open_run['marg'](m) > 0.0


def test_certificate_is_exact_and_bounded_on_the_gated_plan():
    run = _run(OPT_BLOCK, 4 * INC, gate=True)
    assert run['conversion_reachable'] is False and run['concavity_bad'] == []
    assert run['certificate_valid'] is True
    assert run['guarantee']['kind'] == 'exact_discrete_separable_concave'
    assert run['opt_bound'] == run['cur']
    assert run['data_bound'] >= run['cur'] - 1e-12
    fm = run['fm']
    # The gate moves money, not the objective's scope: base still counts every member's flip value.
    assert abs(run['base'] - sum(fm.flip_value(c, MATRIX, fm.cu0, fm.ind0) for c in TOPICS)) < 1e-12

    def objective(chunks):
        cu = dict(fm.cu0)
        cu.update({m: fm.cu0[m] + INC * k for m, k in zip(FUNDABLE, chunks)})
        return sum(fm.flip_value(c, MATRIX, cu, fm.ind0) for c in TOPICS)
    best = max(objective(ks) for ks in itertools.product(range(PMAX // INC + 1), repeat=len(FUNDABLE))
               if sum(ks) == 4)
    assert abs(run['cur'] - best) < 1e-9                   # exact optimum over fundable members


def test_certificate_scope_is_fundable_members_only():
    # A gated member that money could convert at PMAX cannot refuse the certificate: it is never funded.
    reach = _money_response(reachable={'Gated Highly Skeptical'})
    gated_run = _run(OPT_BLOCK, 4 * INC, gate=True, mr=reach)
    open_run = _run(OPT_BLOCK, 4 * INC, gate=False, mr=reach)
    assert gated_run['conversion_reachable'] is False and gated_run['certificate_valid'] is True
    assert open_run['conversion_reachable'] is True and open_run['certificate_valid'] is False
    # Nor can a gated member whose chunk returns rise (the concavity check's scope).
    gated_run = _run(OPT_BLOCK, 4 * INC, gate=True, fm=_flip_model(_convex_for(R_MARK)))
    open_run = _run(OPT_BLOCK, 4 * INC, gate=False, fm=_flip_model(_convex_for(R_MARK)))
    assert gated_run['concavity_bad'] == [] and gated_run['certificate_valid'] is True
    assert [row['member'] for row in open_run['concavity_bad']] == ['Gated Highly Skeptical']
    assert open_run['certificate_valid'] is False


def test_kernel_helper_is_per_topic_flips_old_inline_expression():
    members = MEMBERS + ['No Record Member']               # absent from the matrix -> '—', never gated
    for doc in ({'matrix': MATRIX, 'gate': {'overall_capped': {'Persuadable A': 'Ally'}}},
                {'matrix': MATRIX, 'gate': None}, {'matrix': MATRIX}):
        # per_topic_flip.py:32-33 before 2026-09-30, verbatim
        CS = doc['matrix']
        _OVERALL_CAPS = (doc.get('gate') or {}).get('overall_capped') or {}
        old = {n: MC.overall_tier(CS.get(n, {}), n, _OVERALL_CAPS) for n in members}
        new = MC.stance_gate_tiers(doc, members)
        assert new == old and list(new) == list(old)
    assert new['No Record Member'] == '—'
    assert MC.STANCE_GATE_BLOCKED == frozenset({'Skeptic', 'Highly Skeptical', 'Opponent'})   # :36 before


def test_both_allocators_read_the_gate_from_the_kernel_helper():
    per_topic = _compact(PER_TOPIC)
    assert 'OVT=MC.stance_gate_tiers(_CS_DOC,members)' in per_topic
    assert 'BLOCK_OVERALL=MC.STANCE_GATE_BLOCKED' in per_topic
    assert 'tgt={n:(0.0ifOVT.get(n)inBLOCK_OVERALLelse' in per_topic
    assert '_OVERALL_CAPS' not in per_topic                # the inline copy moved; it was not duplicated
    optimize = _compact(OPTIMIZE)
    assert 'OVT=stance_gate_tiers(fm._CS_DOC,members)' in optimize
    assert 'GATED=frozenset(mforminmembersifOVT.get(m)inSTANCE_GATE_BLOCKED)' in optimize
    assert 'ifminGATEDorminwonorcu[m]+INC>fm.cu0[m]+PMAX:return0.0' in optimize
    assert "'stance_gate':{" in optimize


def test_every_copy_of_the_blocked_set_agrees():
    _source, tree = _tree(LT_PRIMING)
    lt_node = next(node.value for node in tree.body
                   if isinstance(node, ast.Assign) and 'BLOCK_OVERALL' in _targets(node))
    assert ast.unparse(lt_node) == 'MBC.STANCE_GATE_BLOCKED'   # QME 2026-10-01: lt_priming now reads the kernel's one copy (was a literal set)
    with open(DASH_TPL, encoding='utf-8') as handle:
        codes = re.search(r'const BLOCKOV=\{([^}]*)\}', handle.read()).group(1)
    letters = {pair.split(':')[0] for pair in codes.split(',')}
    tiername = _lift(DESK, assigns=('TIERNAME',))['TIERNAME']
    assert {tiername[letter] for letter in letters} == set(MC.STANCE_GATE_BLOCKED)
