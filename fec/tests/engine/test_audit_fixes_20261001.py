# -*- coding: utf-8 -*-
"""QME 2026-10-01 zero-delta guards: optimize dials/validation/knee, money_response ORDER.index, flip_model engagement warning,
moneyball_core NaN warnings + stance_gate_tiers contract. FIXTURE ONLY: engine scripts execute on import, so their REAL statements
are lifted with ast and run on synthetic inputs; only import-safe moneyball_core is imported."""
from __future__ import annotations
import ast, contextlib, io, os, sys, unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FEC not in sys.path: sys.path.insert(0, FEC)
import moneyball_core as MC  # noqa: E402

def _src(name):
    with open(os.path.join(FEC, 'engine', name), encoding='utf-8') as h: s = h.read()
    return s, ast.parse(s)
def _exec(node, ns): exec(compile(ast.Module([node], []), 'lift', 'exec'), ns)
def _targets(node): return {t.id for t in getattr(node, 'targets', []) if isinstance(t, ast.Name)}
def _guard_ifs(fname, word):   # top-level `if` statements that raise and mention `word`
    s, t = _src(fname)
    return [n for n in t.body if isinstance(n, ast.If) and any(isinstance(x, ast.Raise) for x in ast.walk(n)) and word in ast.get_source_segment(s, n)]

class Guards(unittest.TestCase):
    def _check(self, fname, word, ns):
        hits = _guard_ifs(fname, word); self.assertTrue(hits, (fname, word))
        for n in hits: _exec(n, dict(ns))
    def test_half_dthresh(self):
        for bad in (0, -1.0):
            with self.assertRaises(ValueError): self._check('money_response.py', 'HALF must', {'HALF': bad, 'DTHRESH': 1.0})
            with self.assertRaises(ValueError): self._check('money_response.py', 'DTHRESH must', {'HALF': 0.1, 'DTHRESH': bad})
            with self.assertRaises(ValueError): self._check('flip_model.py', 'HALFF must', {'HALFF': bad})
        self._check('money_response.py', 'HALF must', {'HALF': 0.1, 'DTHRESH': 11.25})
        self._check('money_response.py', 'DTHRESH must', {'HALF': 0.1, 'DTHRESH': 11.25})
        self._check('flip_model.py', 'HALFF must', {'HALFF': 0.1})
    def test_conv_maxstep(self):
        with self.assertRaises(ValueError): self._check('optimize.py', 'CONV_MAXSTEP must', {'CONV_MAXSTEP': -1})
        for ok in (0, 1, 3): self._check('optimize.py', 'CONV_MAXSTEP must', {'CONV_MAXSTEP': ok})
    def test_conv_maxstep_read_once(self):
        s, t = _src('optimize.py')
        self.assertEqual(s.count("os.environ.get('CONV_MAXSTEP'"), 1)
        shift = next(n for n in t.body if isinstance(n, ast.FunctionDef) and n.name == 'shift_pro')
        self.assertNotIn('environ', ast.get_source_segment(s, shift))

class MoneyResponseTier(unittest.TestCase):
    def _fn(self, base, tier):
        s, t = _src('money_response.py')
        ns = {'MC': type('M', (), {'overall_tier': staticmethod(lambda *a: base)}), '_OVERALL_CAPS': {}, '_tier': {'x': tier}}
        for n in t.body:
            if (isinstance(n, ast.Assign) and _targets(n) == {'ORDER'}) or (isinstance(n, ast.FunctionDef) and n.name == '_money_overall_tier'): _exec(n, ns)
        return ns['_money_overall_tier']('x', {})
    def test_dash_tier1_no_raise(self): self.assertEqual(self._fn('—', 1), '—')
    def test_offladder_nonpromotable(self): self.assertEqual(self._fn('Weird', 1), 'Weird')
    def test_promotion_unchanged(self):
        self.assertEqual(self._fn('Ally', 1), 'Ally'); self.assertEqual(self._fn('Persuadable', 1), 'Ally'); self.assertEqual(self._fn('Skeptic', 1), 'Ally')
        self.assertEqual(self._fn('Skeptic', 2), 'Skeptic'); self.assertEqual(self._fn('—', 2), '—')

class FlipEngagementWarning(unittest.TestCase):
    def _go(self, members, nrow, eng):
        s, t = _src('flip_model.py'); buf = io.StringIO()
        ns = {'members': members, 'nrow': nrow, 'eng': eng, 'sys': type('S', (), {'stderr': buf}),
              'print': lambda *a, file=None, **k: file.write(' '.join(map(str, a)))}
        before = {k: dict(v) for k, v in eng.items()}
        for n in t.body:
            if (isinstance(n, ast.Assign) and _targets(n) & {'_eng_missing', '_eng_zero'}) or (isinstance(n, ast.If) and '_eng_zero' in ast.get_source_segment(s, n)): _exec(n, ns)
        self.assertEqual(eng, before)   # values never touched
        return buf.getvalue()
    def test_counts_and_names(self):
        out = self._go(['a', 'b', 'c', 'd'], {'a': 1, 'b': 2, 'c': 3},
                       {'X': {'a': 1.0, 'b': 0.0, 'c': 0.0, 'd': 0.0}, 'Y': {'a': 0.0, 'b': 0.0, 'c': 2.0, 'd': 0.0}})
        self.assertIn('WARNING', out); self.assertIn('2 members have zero engagement on ALL issues (first: b, d)', out)
        self.assertIn('1 members missing', out); self.assertIn('(first: d)', out)
    def test_silent_when_clean(self): self.assertEqual(self._go(['a'], {'a': 1}, {'X': {'a': 1.0}}), '')

class KneeExclusion(unittest.TestCase):
    def _knee(self, sel, conv, INC=500):
        s, t = _src('optimize.py'); ns = {'selected_marginals': list(sel), 'conv_idx': set(conv), 'INC': INC, 'spent': INC * len(sel)}
        start = next(i for i, n in enumerate(t.body) if isinstance(n, ast.Assign) and _targets(n) == {'sel_all'})
        end = next(i for i, n in enumerate(t.body) if isinstance(n, ast.Assign) and _targets(n) == {'knee_found'})
        for n in t.body[start:end + 1]: _exec(n, ns)
        return ns
    def test_conversion_step_dropped(self): self.assertEqual(self._knee([1.0, 0.9, 50.0, 0.8], {2})['selected_marginals'], [1.0, 0.9, 0.8])
    def test_no_conversion_identical(self):
        ns = self._knee([1.0, 0.9, 0.1, 0.05], set()); self.assertEqual(ns['selected_marginals'], [1.0, 0.9, 0.1, 0.05]); self.assertEqual(ns['knee'], 1000)
    def test_knee_keeps_step_index(self): self.assertEqual(self._knee([1.0, 99.0, 0.9, 0.1], {1})['knee'], 1500)   # the 99.0 conversion spike must not set peak
    def test_dials_recorded(self):
        s, t = _src('optimize.py'); d = next(n for n in t.body if isinstance(n, ast.Assign) and _targets(n) == {'dials'})
        keys = {k.value for k in d.value.keys}
        self.assertTrue({'BUDGET', 'INC', 'PMAX', 'TOPIC', 'CONV_MAXSTEP', 'HALFF', 'WM', 'WP', 'BASE', 'MAXSHIFT', 'HALF', 'DTHRESH', 'LOSSFRAME', 'IMPUTE_IND0', 'PERS_NONE', 'RECEPT_LAYER'} <= keys)
        # COMPET_MULT: recorded only when the dial is ON (dial OFF keeps optimize_result.json byte-identical), so the keys are added by a guarded statement, not the literal
        self.assertTrue(any(isinstance(n, ast.If) and 'compet_on' in ast.dump(n.test) and "'COMPET_MULT'" in ast.unparse(n) and "'COMPET_SRC_SHA256'" in ast.unparse(n) for n in t.body))
        dump = next(n for n in ast.walk(t) if isinstance(n, ast.Call) and getattr(n.func, 'id', '') == 'dump_atomic')
        self.assertIn('dials', {k.value for k in dump.args[0].keys})

class CoreWarnings(unittest.TestCase):
    def setUp(self): MC._NONFINITE_WARNED.update({k: 0 for k in MC._NONFINITE_WARNED})
    def test_priority_nan_value_kept_warn_once(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            a = MC.priority_base_signal(float('nan'), 1.0); b = MC.priority_base_signal(0.2, float('nan')); c = MC.priority_base_signal(0.5, 1.0)
        self.assertEqual((a, b), (0.0, 0.0)); self.assertAlmostEqual(c, 0.65); self.assertEqual(err.getvalue().count('WARNING'), 1)
    def test_priority_finite_silent(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err): self.assertEqual(MC.priority_base_signal(-5, 1.0), 0.0)
        self.assertEqual(err.getvalue(), '')
    def test_delivery(self):
        MC._DELIV = {'nan': float('nan'), 'str': 'high', 'hi': 3.0, 'ok': 1.0}
        try:
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(MC.delivery_mult('nan'), 1.0); self.assertEqual(MC.delivery_mult('nan'), 1.0)
            self.assertEqual(err.getvalue().count('WARNING'), 1)
            self.assertEqual(MC.delivery_mult('missing'), 1.0); self.assertEqual(MC.delivery_mult('ok'), 1.0); self.assertAlmostEqual(MC.delivery_mult('hi'), 0.70)
            with self.assertRaises(TypeError) as cx: MC.delivery_mult('str')
            self.assertIn('must be numeric', str(cx.exception))
        finally: MC._DELIV = None

class StanceGateContract(unittest.TestCase):
    def test_fail_open_and_caps_dead(self):
        doc = {'matrix': {'a': {'Interchange': 'Opponent'}}, 'gate': {'overall_capped': {'a': 'Champion', 'zz': 'Opponent'}}}
        r = MC.stance_gate_tiers(doc, ['a', 'ghost'])
        self.assertEqual(r['ghost'], '—'); self.assertNotIn(r['ghost'], MC.STANCE_GATE_BLOCKED)
        self.assertEqual(MC.stance_gate_tiers({'matrix': doc['matrix']}, ['a', 'ghost']), r)   # caps never change a result
        self.assertIn('FAILS OPEN', MC.stance_gate_tiers.__doc__)

if __name__ == '__main__': unittest.main(verbosity=1)
