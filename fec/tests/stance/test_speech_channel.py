# -*- coding: utf-8 -*-
"""TDD contracts for the floor-speech CU-stance channel (Paul-approved 2026-07-15).

RED-first: written before speech_stance_stack.py existed and before build_media_blend.py /
run_pipeline.py were wired.  Dependency-light — a behavioral import of the API-free scorer plus
source-text gate assertions, so it runs in a clean runtime.

Standalone: python fec/tests/stance/test_speech_channel.py
"""
from __future__ import annotations
import csv, io, os, re, sys, unittest
from pathlib import Path

FEC = Path(__file__).resolve().parents[2]
ROOT = FEC.parent
DATA_ROOT = Path(os.environ.get("MONEYBALL_ROOT", ROOT))
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


class SpeechScorerContracts(unittest.TestCase):
    """The API-free precedence scorer: gold > registry-compose+relaxed-CFPB-pin > neutral guard."""

    @classmethod
    def setUpClass(cls):
        import stance.speech_stance_stack as sss
        cls.sss = sss

    def test_module_exposes_scorer_api(self):
        for fn in ("score_speech_text", "score_row", "row_id", "load_gold",
                   "compose", "fam_lookup", "cue_stance_toward_target"):
            self.assertTrue(hasattr(self.sss, fn), "speech_stance_stack must expose %s" % fn)

    def test_anti_cfpb_speech_scores_support(self):
        # John-Rose-style anti-CFPB floor speech: attacking/defunding the CFPB = pro-CU = support.
        txt = ("Mr. Speaker, we must rein in the unaccountable CFPB and end its endless overreach "
               "on America's credit unions.")
        self.assertEqual(self.sss.score_speech_text(txt), "support")

    def test_cfpb_defense_speech_scores_oppose(self):
        # Warren/Waters-style CFPB-defense floor speech: defending the CFPB = anti-CU = oppose.
        txt = ("Madam Speaker, we must protect and defend the CFPB, save it from these attacks and "
               "strengthen the CFPB so it can keep shielding consumers.")
        self.assertEqual(self.sss.score_speech_text(txt), "oppose")

    def test_non_directional_cfpb_mention_keeps_neutral(self):
        # Anti-populist neutral guard: a bare CFPB mention with no directional cue stays neutral.
        txt = "Mr. Speaker, the CFPB issued a report today about the financial system."
        self.assertEqual(self.sss.score_speech_text(txt), "neutral")

    def test_interchange_attack_scores_support(self):
        # Anti-CU (-1) target: opposing the Durbin interchange mandate is pro-CU (attack the target).
        txt = ("Mr. Speaker, I rise to rein in the swipe fee interchange mandate, an unaccountable "
               "overreach that hurts community credit unions.")
        self.assertEqual(self.sss.score_speech_text(txt), "support")

    def test_gold_beats_heuristic(self):
        # Precedence (a): Paul's gold_stance overrides the registry/heuristic read entirely.
        row = {"bioguide": "R000612", "date": "2025-01-01",
               "text": "Madam Speaker, we must protect and defend the CFPB."}
        heur_stance, heur_tier = self.sss.score_row(row, {})
        self.assertEqual((heur_stance, heur_tier), ("oppose", "review"))
        rid = self.sss.row_id(row["bioguide"], row["date"], row["text"])
        gold_stance, gold_tier = self.sss.score_row(row, {rid: "support"})
        self.assertEqual((gold_stance, gold_tier), ("support", "gold"))

    def test_row_id_matches_gold_queue_content_hash(self):
        # row_id must reproduce build_speech_gold_queue.py's content-hash id so labels bind by content.
        import hashlib
        bio, date, text = "A000001", "2024-03-03", "some floor speech about the cfpb"
        expected = "x" + hashlib.md5(("%s|%s|%s" % (bio, date, text)).encode("utf-8", "replace")).hexdigest()[:12]   # x-hardened (Excel-proof) id, 2026-07-15
        self.assertEqual(self.sss.row_id(bio, date, text), expected)

    def test_precision_negative_neutral_gold_can_retire_without_weak_relabeling(self):
        road = {
            "gold_stance": "neutral",
            "categories": "Interchange",
            "text_snippet": "The I-74 Mississippi River Bridge won an infrastructure award.",
        }
        directional = {
            "gold_stance": "support",
            "categories": "Interchange",
            "text_snippet": "We must rein in the Durbin interchange mandate for credit unions.",
        }
        self.assertTrue(self.sss.retired_precision_orphan(road))
        self.assertFalse(self.sss.retired_precision_orphan(directional))


class SpeechCsvEmission(unittest.TestCase):
    """speech_stance_stack must emit speech_stance_final.csv with the contracted schema + valid values."""

    def test_main_emits_wellformed_csv(self):
        if not (DATA_ROOT / "speeches_cu.csv").exists():
            self.skipTest("speeches_cu.csv absent in this tree")
        import stance.speech_stance_stack as sss
        sss.main()
        out = DATA_ROOT / "speech_stance_final.csv"
        self.assertTrue(out.exists(), "speech_stance_final.csv not written")
        with io.open(out, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreater(len(rows), 0)
        self.assertEqual(list(rows[0].keys()),
                         ["bioguide", "member", "date", "categories", "stance", "tier",
                          "model_stance", "rule_read", "local_read", "transfer_read",
                          "gold_applied"])
        for r in rows:
            self.assertIn(r["stance"], ("support", "oppose", "neutral"))
            self.assertIn(r["tier"], ("gold", "unanimous", "majority", "review"))


class BlendGateContracts(unittest.TestCase):
    """Validated speech evidence is part of the press-side media score by default."""

    def test_blend_references_speech_sidecar(self):
        txt = source("build_media_blend.py")
        self.assertIn("speech_stance_final", txt)

    def test_blend_gate_defaults_on(self):
        txt = source("build_media_blend.py")
        m = re.search(r"os\.environ\.get\(\s*'CU_SPEECH_CHANNEL'\s*,\s*'1'\s*\)", txt)
        self.assertIsNotNone(m, "CU_SPEECH_CHANNEL must default to '1' (ON)")
        self.assertIn("CU_SPEECH_CHANNEL", txt)

    def test_blend_speech_fold_is_gated(self):
        # Every speech contribution must sit behind the SPEECH_ON gate (OFF => untouched math).
        txt = source("build_media_blend.py")
        self.assertIn("SPEECH_ON", txt)
        self.assertIn("if SPEECH_ON", txt)


class PipelineWiringContracts(unittest.TestCase):
    """extract_cu_speeches + speech_stance_stack must run before build_media_blend in the chain."""

    def test_chain_order(self):
        import run_pipeline
        scripts = [s for s, _a in run_pipeline.CHAIN]
        for s in ("extract_cu_speeches.py", "speech_stance_stack.py", "build_media_blend.py"):
            self.assertIn(s, scripts, "%s missing from CHAIN" % s)
        self.assertLess(scripts.index("extract_cu_speeches.py"), scripts.index("build_media_blend.py"))
        self.assertLess(scripts.index("speech_stance_stack.py"), scripts.index("build_media_blend.py"))
        self.assertLess(scripts.index("extract_cu_speeches.py"), scripts.index("speech_stance_stack.py"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
