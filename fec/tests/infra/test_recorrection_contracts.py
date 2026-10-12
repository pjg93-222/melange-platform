# -*- coding: utf-8 -*-
"""TDD contracts for the 2026-07-15 re-correction gated fixes (Paul-directed).
Kept dependency-light (source-text + kernel-import assertions) so they run in a clean runtime."""
from __future__ import annotations
import os, re, sys, unittest
from pathlib import Path
FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
sys.path.insert(0, str(FEC))
def source(name: str) -> str:
    matches = [
        path for path in FEC.rglob(name)
        if not any(
            part in {"tests", "attic", "__pycache__"} or part.startswith("_quarantine")
            for part in path.relative_to(FEC).parts[:-1]
        )
    ]
    if len(matches) != 1:
        raise AssertionError("producer basename %s resolved to %r" % (name, matches))
    return matches[0].read_text(encoding="utf-8-sig")


class DialContracts(unittest.TestCase):
    def test_overall_scale_shifted_and_ladder_monotonic(self):
        # User-directed 2026-07-18: tighten the fixed ladder while retaining a zero sign boundary.
        import importlib
        MC = importlib.import_module("moneyball_core")
        self.assertEqual(MC.OVERALL_OPPONENT_CUTOFF, -1.50)
        cuts = [c for c, _ in MC.OVERALL_BANDS]
        self.assertEqual(cuts, sorted(cuts, reverse=True), "OVERALL_BANDS cutoffs must strictly descend")
        self.assertEqual(len(cuts), len(set(cuts)), "OVERALL_BANDS cutoffs must be distinct")
        self.assertEqual(
            dict((tier, cut) for cut, tier in MC.OVERALL_BANDS),
            {'Ally':3.0,'Highly Persuadable':2.0,'Persuadable':1.0,
             'Lean Persuadable':.4,'True Neutral':0.0,'Lean Skeptic':-.3,
             'Skeptic':-.75,'Highly Skeptical':-1.5},
        )
        # Highly Skeptical floor (== opponent cutoff) must sit BELOW the Skeptic floor
        skeptic = dict((t, c) for c, t in MC.OVERALL_BANDS)['Skeptic']
        self.assertLess(MC.OVERALL_OPPONENT_CUTOFF, skeptic)

    def test_cu_tax_status_is_defensive(self):
        # Paul-directed 2026-07-15: CU Tax Status is a defend-type fight -> DEFENSIVE weighting.
        txt = source("per_category_stance.py")
        m = re.search(r"DEFENSIVE\s*=\s*\{([^}]*)\}", txt)
        self.assertIsNotNone(m, "DEFENSIVE set literal not found")
        self.assertIn("CU Tax Status", m.group(1))

    def test_money_response_reads_python_stance_authority_not_workbook_view(self):
        # Col G is a synchronized display.  Consumers must derive the same overall tier directly
        # from category_stance.json, or an open/stale workbook can erase the Opponent rung.
        txt = source("money_response.py")
        self.assertIn("category_stance.json", txt)
        self.assertIn("MC.overall_tier", txt)
        self.assertNotIn("openpyxl.load_workbook", txt)


class StackContracts(unittest.TestCase):
    def test_tweet_classical_read_uses_classifier_class_order(self):
        # 3rd-pass S2: sklearn predict_proba columns are alphabetical (neutral,oppose,support,unrelated),
        # NOT CL order (support,oppose,neutral,unrelated); indexing pr's argmax into CL swaps neutral<->support
        # in classical_read + the tier weight. Must index the classifier's OWN classes_.
        txt = source("tweet_stance_stack.py")
        self.assertNotIn("clab = CL[int(np.argmax(pr))]", txt)
        self.assertIn("clab = pc.classes_[int(np.argmax(pr))]", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
