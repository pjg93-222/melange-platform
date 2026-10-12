# -*- coding: utf-8 -*-
"""Lint banner-gated findings documents for causal-claim wording defects."""

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
AUDITS_DIR = REPO_ROOT / "audits"

SCOPE_PHRASE_RE = re.compile(
    r"no causal claims|nothing here is causal|EXPLORATORY ASSOCIATIONS",
    re.IGNORECASE,
)
SCOPE_STATUS_RE = re.compile(r"STATUS.*EXPLORATORY", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}(?:\s+|$)")
FENCE_LINE_RE = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
SETEXT_OR_RULE_RE = re.compile(r"^ {0,3}(?:={3,}|-{3,})[ \t]*$")
INLINE_CODE_RE = re.compile(
    r"(?<!`)(?P<ticks>`+)(?!`).*?(?<!`)(?P=ticks)(?!`)"
)
DOUBLE_QUOTED_RE = re.compile(r'"[^"]*"')
SENTENCE_END_RE = re.compile(r"[.!?](?=\s|$)")
ABBREVIATION_END_RE = re.compile(
    r"(?:\b(?i:vs|e\.g|i\.e|cf|etc)\.|\b[A-Z]\.)$"
)
HYPHEN_CAUSAL_RE = re.compile(r"\b\w+-(?:driven|drives)\b", re.IGNORECASE)

DETECTORS = (
    (
        "ASSERTIVE_BACKFIRE",
        re.compile(
            r"\b(?:can|could|will|would|does|did|may|might)\s+backfire\b"
            r"|\bbackfired\b|\bit\s+backfires\b",
            re.IGNORECASE,
        ),
    ),
    (
        "BARE_NO_BACKFIRE",
        re.compile(
            r"\bNO\s+BACKFIRE\b(?!\s*(?:SIGNAL|SIGNATURE)\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "BACKFIRE_TEST_COMPOUND",
        re.compile(r"\bbackfire\s+test\b", re.IGNORECASE),
    ),
    (
        "BACKFIRE_EXISTENCE",
        re.compile(
            r"\bbackfire\s+(?:exists|is\s+present|is\s+real)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "OTHER_CAUSAL",
        re.compile(
            r"\b(?:causes|drives|drove|leads\s+to|led\s+to|"
            r"results\s+in|resulted\s+in)\b"
            r"|\bcaused\s+by\b|\bdriven\s+by\b",
            re.IGNORECASE,
        ),
    ),
)

NEGATION_OR_CAPABILITY_RE = re.compile(
    r"\b(?:not|cannot|never|neither)\b"
    r"|no evidence|does not identify|cannot identify|unidentified|was capable of",
    re.IGNORECASE,
)

F1 = (
    "YES, it can backfire, in two measurable ways: (1) traditional industries "
    "courting crypto-aligned members show a robust negative marginal association"
)
F2 = (
    "Result: simple align_cu~exp_cu residual +0.028 (82nd pctile of 533); full "
    "backfire-model fitted +0.561 vs actual +0.533 (residual -0.028, ~zero). "
    "NO BACKFIRE."
)
F3 = (
    "OLS: align_B ~ exp_B + align_A + exp_B*align_A + senate + log_total_exp "
    "(n=533 per model). The interaction term is the backfire test."
)
F4 = (
    "YES, it can\nbackfire, in two\nmeasurable ways: (1) traditional industries "
    "courting crypto-aligned members show a robust negative marginal association"
)
F5 = "Backfire is real."
F6 = "Sustained lobbying drives turnout on this axis."
F7 = "A knot drives the mechanism."
F8 = "Nevertheless, policy drives turnout."

P1 = "NO TEST HERE THAT WAS CAPABLE OF SHOWING A BACKFIRE SIGNAL DID"
P2 = "~zero). NO BACKFIRE SIGNATURE ON THIS TEST."
P3 = "(n=533 per model). The interaction term is the backfire-signature test."
P4 = (
    "when industry B lobbies members who are allies/champions of industry A on a "
    "shared issue, what is the likely result — can courting another industry's "
    "champion backfire?"
)
P5 = (
    "they can detect a backfire SIGNATURE (negative residuals, falling trajectories, "
    "conflict holdings) but cannot identify causal backfire or its absence."
)
P6 = (
    "A \"separate industry's champion\" is, on average, MILDLY ALIGNED with yours — "
    "the baseline works against backfire."
)
P7 = (
    "1. The answer line read \"YES, it can backfire, in two measurable ways\". A "
    "causal verb sitting 80 lines below this document's own \"no causal claims\" "
    "banner."
)
P8 = (
    "THE CRYPTO FAULT LINE IS DEMOCRAT-DRIVEN. All three negative interactions are "
    "Holm-significant within Democrats and none is within Republicans."
)
P9 = (
    "### 2. The backfire signature is present and DIRECTIONAL — Democrat-located "
    "(crypto fault line)"
)
P10 = "There is no evidence that U.S. policy drives turnout."
P11 = "Would backfire risk rise here?"
P12 = "This design cannot show that exposure drives alignment."
P13 = "The prior wording \"exposure drives alignment\" was corrected."
P14 = "Exposure co-drives alignment among Democrats."


def _joined_blocks(text):
    blocks = []
    current = []
    start_line = None
    open_fence_character = None
    open_fence_length = None

    def flush():
        nonlocal current, start_line
        if current:
            blocks.append(
                (start_line, " ".join(line.strip() for line in current))
            )
        current = []
        start_line = None

    for line_number, line in enumerate(text.splitlines(), 1):
        fence_match = FENCE_LINE_RE.match(line)
        if open_fence_character is not None:
            if fence_match:
                marker = fence_match.group("marker")
                if (
                    marker[0] == open_fence_character
                    and len(marker) >= open_fence_length
                ):
                    open_fence_character = None
                    open_fence_length = None
            continue
        if fence_match:
            flush()
            marker = fence_match.group("marker")
            open_fence_character = marker[0]
            open_fence_length = len(marker)
            continue
        if SETEXT_OR_RULE_RE.match(line):
            flush()
            continue
        if not line.strip():
            flush()
            continue
        if HEADING_RE.match(line):
            flush()
            blocks.append((line_number, line.strip()))
            continue
        if start_line is None:
            start_line = line_number
        current.append(line)
    flush()
    return blocks


def _sentences(text):
    fragments = []
    start = 0
    for match in SENTENCE_END_RE.finditer(text):
        sentence = text[start : match.end()].strip()
        if sentence:
            fragments.append(sentence)
        start = match.end()
        while start < len(text) and text[start].isspace():
            start += 1
    trailing = text[start:].strip()
    if trailing:
        fragments.append(trailing)

    sentences = []
    for fragment in fragments:
        if sentences and ABBREVIATION_END_RE.search(sentences[-1]):
            sentences[-1] = f"{sentences[-1]} {fragment}"
        else:
            sentences.append(fragment)
    return sentences


def _lint_blocks(blocks):
    findings = []
    detectors = dict(DETECTORS)
    for start_line, block in blocks:
        surviving = INLINE_CODE_RE.sub("", block)
        surviving = DOUBLE_QUOTED_RE.sub("", surviving)
        surviving = re.sub(r"\s+", " ", surviving).strip()
        for sentence in _sentences(surviving):
            if sentence.endswith("?"):
                continue

            if detectors["BARE_NO_BACKFIRE"].search(sentence):
                findings.append((start_line, "BARE_NO_BACKFIRE", sentence))

            other_causal_text = HYPHEN_CAUSAL_RE.sub("", sentence)
            if NEGATION_OR_CAPABILITY_RE.search(other_causal_text):
                continue

            for pattern_id in (
                "ASSERTIVE_BACKFIRE",
                "BACKFIRE_TEST_COMPOUND",
                "BACKFIRE_EXISTENCE",
            ):
                if detectors[pattern_id].search(sentence):
                    findings.append((start_line, pattern_id, sentence))
            if detectors["OTHER_CAUSAL"].search(other_causal_text):
                findings.append((start_line, "OTHER_CAUSAL", sentence))
    return findings


def _lint_text(text):
    return _lint_blocks(_joined_blocks(text))


def _is_in_scope(text):
    first_60_lines = text.splitlines()[:60]
    return any(
        SCOPE_PHRASE_RE.search(line) or SCOPE_STATUS_RE.search(line)
        for line in first_60_lines
    )


class MustFlagFixtureTests(unittest.TestCase):
    def assert_fixture_flags(self, fixture, trigger, pattern_id):
        self.assertIn(trigger, fixture)
        findings = _lint_text(fixture)
        self.assertIn(pattern_id, {finding[1] for finding in findings}, findings)

    def test_f1_assertive_backfire(self):
        self.assert_fixture_flags(F1, "can backfire", "ASSERTIVE_BACKFIRE")

    def test_f2_bare_no_backfire(self):
        self.assert_fixture_flags(F2, "NO BACKFIRE", "BARE_NO_BACKFIRE")

    def test_f3_backfire_test_compound(self):
        self.assert_fixture_flags(
            F3,
            "backfire test",
            "BACKFIRE_TEST_COMPOUND",
        )

    def test_f4_joined_line_wrap_assertive_backfire(self):
        self.assertIn("can\nbackfire", F4)
        blocks = _joined_blocks(F4)
        self.assertEqual(len(blocks), 1, blocks)
        self.assertIn("can backfire", blocks[0][1])
        findings = _lint_blocks(blocks)
        self.assertIn(
            "ASSERTIVE_BACKFIRE",
            {finding[1] for finding in findings},
            findings,
        )

    def test_f5_backfire_existence(self):
        self.assert_fixture_flags(
            F5,
            "Backfire is real",
            "BACKFIRE_EXISTENCE",
        )

    def test_f6_other_causal_drives(self):
        self.assert_fixture_flags(F6, "drives", "OTHER_CAUSAL")

    def test_f7_knot_does_not_supply_negation(self):
        self.assert_fixture_flags(F7, "drives", "OTHER_CAUSAL")

    def test_f8_nevertheless_does_not_supply_negation(self):
        self.assert_fixture_flags(F8, "drives", "OTHER_CAUSAL")


class MustPassFixtureTests(unittest.TestCase):
    def assert_fixture_passes(self, fixture, trigger=None):
        if trigger is not None:
            self.assertIn(trigger, fixture)
        self.assertEqual(_lint_text(fixture), [])

    def test_p1_capability_language(self):
        self.assert_fixture_passes(P1)

    def test_p2_backfire_signature(self):
        self.assert_fixture_passes(P2)

    def test_p3_backfire_signature_test(self):
        self.assert_fixture_passes(P3)

    def test_p4_interrogative(self):
        self.assert_fixture_passes(P4)

    def test_p5_cannot_identify(self):
        self.assert_fixture_passes(P5)

    def test_p6_double_quoted_specimen(self):
        self.assert_fixture_passes(P6)

    def test_p7_double_quoted_corrections(self):
        self.assert_fixture_passes(P7)

    def test_p8_hyphen_compound(self):
        self.assert_fixture_passes(P8)

    def test_p9_backfire_signature_existence(self):
        self.assert_fixture_passes(P9)

    def test_p10_abbreviation_keeps_negation_attached(self):
        self.assert_fixture_passes(P10, "drives")

    def test_p11_interrogative_live_trigger(self):
        self.assert_fixture_passes(P11, "Would backfire")

    def test_p12_capability_live_trigger(self):
        self.assert_fixture_passes(P12, "drives")

    def test_p13_double_quoted_live_trigger(self):
        self.assert_fixture_passes(P13, "drives")

    def test_p14_hyphen_compound_live_trigger(self):
        self.assert_fixture_passes(P14, "drives")


class StructuralExemptionTests(unittest.TestCase):
    def test_equal_length_inline_backtick_runs_are_removed(self):
        fixture = "The specimen ``can backfire`` was removed."
        self.assertIn("``can backfire``", fixture)
        self.assertEqual(_lint_text(fixture), [])

    def test_raw_fence_character_and_length_control_closing(self):
        fixture = (
            "Before.\n"
            "````python\n"
            "YES, it can backfire.\n"
            "```\n"
            "Sustained lobbying drives turnout.\n"
            "~~~~\n"
            "`````\n"
            "After."
        )
        self.assertEqual(
            _joined_blocks(fixture),
            [(1, "Before."), (8, "After.")],
        )
        self.assertEqual(_lint_text(fixture), [])

    def test_setext_and_rule_lines_flush_and_are_skipped(self):
        fixture = "First line\n---\nSecond line\n===\nThird line"
        self.assertEqual(
            _joined_blocks(fixture),
            [(1, "First line"), (3, "Second line"), (5, "Third line")],
        )


class LiveCorpusTests(unittest.TestCase):
    def test_banner_gated_findings_documents_have_no_causal_claims(self):
        in_scope = []
        findings = []
        for path in sorted(AUDITS_DIR.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError as exc:
                self.fail(f"{path.name}: UTF-8 decode failed: {exc}")
            if not _is_in_scope(text):
                continue
            in_scope.append(path.name)
            for start_line, pattern_id, sentence in _lint_text(text):
                findings.append(
                    f"{path.name}:{start_line}: {pattern_id}: {sentence}"
                )

        self.assertTrue(in_scope, "no banner-gated audits/*.md files found")
        self.assertEqual(
            findings,
            [],
            "causal-claim findings:\n" + "\n".join(findings),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
