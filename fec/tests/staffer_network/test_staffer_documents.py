import hashlib
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StafferDocumentAdjudicationTests(unittest.TestCase):
    def test_20260829_evidence_documents_remain_byte_preserved(self):
        expected = {
            "STAFFER_NETWORK_METRICS_20260829.md": "6d0db9a3a520c4d547e13bf2764fea68b109678b54dbebcccacc6d5e0033bee6",
            "STAFFER_NETWORK_EXPANSION_DESIGN_20260829.md": "122f10ff2057e323b2f9b2723ba944a7ae7e44c4d781fa0b476d4a1ce8443d73",
            "STAFFER_NETWORK_CORPUS_SPEC_20260829.md": "9c6040b094e26aaa342a9a7eb583ed2c0a47ed4313265cd3ab8d10410e929642",
            "audits/STAFFER_NETWORK_IMPLEMENTATION_DIAGNOSTIC_20260829.md": "9817e08a83e65a0567c091890e70d831464b5769b2088368f555bc669911e108",
        }
        for locator, digest in expected.items():
            with self.subTest(locator=locator):
                self.assertEqual(_sha256(ROOT / locator), digest)

    def test_final_documents_bind_corrected_counts_and_closed_gates(self):
        paths = [
            ROOT / "STAFFER_NETWORK_METRICS_20260830.md",
            ROOT / "STAFFER_NETWORK_EXPANSION_DESIGN_20260830.md",
            ROOT / "STAFFER_NETWORK_CORPUS_SPEC_20260830.md",
            ROOT / "audits" / "STAFFER_NETWORK_IMPLEMENTATION_DIAGNOSTIC_20260830.md",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("873,236", text)
                self.assertIn("6,682,108", text)
                self.assertIn("6,978", text)
                self.assertIn("424,323", text)
                self.assertIn("1,456,165", text)
                self.assertIn("STAFF_NET=off", text)
                self.assertNotIn("1,267,716 Lobbying", text)

    def test_readiness_claim_is_qualified_and_model_review_not_human_gold(self):
        metrics = (ROOT / "STAFFER_NETWORK_METRICS_20260830.md").read_text(encoding="utf-8")
        design = (ROOT / "STAFFER_NETWORK_EXPANSION_DESIGN_20260830.md").read_text(encoding="utf-8")
        diagnostic = (ROOT / "audits" / "STAFFER_NETWORK_IMPLEMENTATION_DIAGNOSTIC_20260830.md").read_text(encoding="utf-8")
        self.assertIn(
            "two separately conserved restricted\nsource-native generations",
            metrics,
        )
        self.assertIn("C-5 LDA filing census | `INVARIANT`; `PASS`", metrics)
        self.assertNotIn("FAIL / REMEDIATION RUNNING", metrics)
        self.assertIn("Human gold is zero; precision is null", metrics)
        self.assertIn("not ready for a canonical person graph", diagnostic)
        self.assertIn("ready for implementation", design)


if __name__ == "__main__":
    unittest.main()
