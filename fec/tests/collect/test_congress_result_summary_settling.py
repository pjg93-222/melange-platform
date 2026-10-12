import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "fec" / "collect" / "collect_congress_advanced_pages.mjs"


class CongressResultSummarySettlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = COLLECTOR.read_text(encoding="utf-8")

    def test_transient_summary_is_polled_without_becoming_a_result(self) -> None:
        helper_start = self.source.index(
            "async function waitForSettledResultSummary"
        )
        helper_end = self.source.index(
            "\nfunction parseCanonicalUrl",
            helper_start,
        )
        helper = self.source[helper_start:helper_end]
        self.assertIn("resultSummaryIsInProgress,", self.source)
        self.assertIn("await sleep(RESULT_SUMMARY_POLL_MS)", helper)
        self.assertIn(
            "no result was accepted from:",
            helper,
        )
        self.assertNotRegex(
            helper,
            re.compile(
                r"resultSummaryIsInProgress\(.*?\).*?"
                r"total\s*:\s*(?:0|1)",
                re.DOTALL,
            ),
        )

    def test_settled_state_still_requires_existing_strict_parsers(self) -> None:
        helper_start = self.source.index(
            "async function waitForSettledResultSummary"
        )
        helper_end = self.source.index(
            "\nfunction parseCanonicalUrl",
            helper_start,
        )
        helper = self.source[helper_start:helper_end]
        self.assertIn("parseResultRange(lastMainText)", helper)
        self.assertIn("classifySingleResultRedirect({", helper)
        self.assertIn("RESULT_SUMMARY_WAIT_MS", helper)
        self.assertLess(
            helper.index("classifySingleResultRedirect({"),
            helper.index("lastMainText = await strictMainText(page)"),
        )

    def test_duplicate_main_nodes_must_be_textually_identical(self) -> None:
        strict_start = self.source.index("async function strictMainText")
        strict_end = self.source.index(
            "\nasync function waitForSettledResultSummary",
            strict_start,
        )
        strict = self.source[strict_start:strict_end]
        self.assertIn('page.locator("#main:visible").allInnerTexts()', strict)
        self.assertIn("selectStrictMainText(", strict)
        self.assertIn("parseResultRange", strict)

    def test_configure_search_uses_settled_summary(self) -> None:
        configure_start = self.source.index(
            "async function configureAndSearch"
        )
        configure_end = self.source.index(
            "\nasync function extractSingleResultRedirect",
            configure_start,
        )
        configure = self.source[configure_start:configure_end]
        self.assertIn("await waitForSettledResultSummary({", configure)
        self.assertIn('settled.kind === "single_result_redirect"', configure)
        self.assertIn("total: settled.range.total", configure)
        self.assertNotIn(
            "const mainText = await page.locator(\"#main\").innerText();",
            configure,
        )

    def test_zero_result_resume_requires_current_surface_validation(self) -> None:
        self.assertIn(
            'const ZERO_RESULT_SURFACE_VALIDATION = "strict-main-v2"',
            self.source,
        )
        self.assertIn(
            "search.total === 0 ? ZERO_RESULT_SURFACE_VALIDATION : null",
            self.source,
        )
        self.assertIn(
            "artifact.zero_result_surface_validation ===",
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
