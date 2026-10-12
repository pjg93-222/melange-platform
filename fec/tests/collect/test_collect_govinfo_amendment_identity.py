"""Regression suite for the engrossed-amendment identity path.

``amend.dtd`` carries no ``<legis-num>``.  For an engrossed amendment the only
official identity surface in the document is the Dublin Core title, which
states congress, measure, and version together -- "116 S178 EAH: ...",
"117 HR 1437 EAH: ...".  ``validate_xml`` therefore has a narrow fallback,
``AMENDMENT_DC_TITLE_RE``, that binds all three.

This module is deliberately self-contained: it builds its own fixtures rather
than reusing the helpers in ``test_collect_govinfo_bill_xml_versions.py``, so a
future change to those shared helpers cannot silently defeat it.  The fix it
covers was lost once already; this suite is what makes a second loss loud.

Run with::

    python -m pytest fec/tests/collect/test_collect_govinfo_amendment_identity.py -q
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import unittest


SCRIPT = (
    pathlib.Path(__file__).resolve().parents[2]
    / "legislation"
    / "collect_govinfo_bill_xml_versions.py"
)
SPEC = importlib.util.spec_from_file_location(
    "govinfo_version_collector_amendment_identity", SCRIPT
)
assert SPEC is not None and SPEC.loader is not None
collector = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = collector
SPEC.loader.exec_module(collector)


AMEND_DOCTYPE = (
    '<!DOCTYPE amendment-doc PUBLIC "-//US Congress//DTDs/amend.dtd//EN" '
    '"amend.dtd">'
)


def amendment_doc(
    canonical_key: str,
    version_code: str,
    *,
    title_identity: str | None = None,
    congress_text: str | None = None,
    legis_num: str | None = None,
    include_dublin_core: bool = True,
) -> bytes:
    """Mirror a real GovInfo engrossed-amendment document.

    ``legis_num`` is normally absent -- that absence is the whole reason the
    Dublin Core path exists.  Passing it produces the rarer document that does
    carry a measure element, which must keep using the ordinary path.
    """

    congress, measure_type, number = canonical_key.split(":")
    identity = title_identity
    if identity is None:
        identity = f"{congress} {measure_type.upper()}{number} {version_code.upper()}"

    metadata = ""
    if include_dublin_core:
        metadata = (
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dublinCore>'
            f"<dc:title>{identity}: An Act to do a thing</dc:title>"
            "<dc:publisher>United States Government Publishing Office</dc:publisher>"
            "</dublinCore></metadata>"
        )
    measure_element = f"<legis-num>{legis_num}</legis-num>" if legis_num else ""

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        + AMEND_DOCTYPE
        + '<amendment-doc amend-type="engrossed-amendment">'
        + metadata
        + "<engrossed-amendment-form>"
        + f"<congress>{congress_text or congress + 'th CONGRESS'}</congress>"
        + measure_element
        + "</engrossed-amendment-form>"
        + "<legis-body><section><text>Be it enacted.</text></section></legis-body>"
        + "</amendment-doc>"
    ).encode()


class AmendmentDublinCoreIdentityTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 1. the Dublin Core path accepts a well-formed engrossed amendment
    # ------------------------------------------------------------------
    def test_dublin_core_title_accepts_well_formed_engrossed_amendment(self) -> None:
        job = {"canonical_key": "116:s:178", "version_code": "eah"}
        accepted = collector.validate_xml(amendment_doc("116:s:178", "eah"), job)

        self.assertEqual(accepted["identity_source"], "dublin_core_title")
        self.assertEqual(accepted["xml_root"], "amendment-doc")
        self.assertEqual(accepted["surface"], "BILLS")
        self.assertEqual(accepted["matched_congress"], "116")
        self.assertEqual(accepted["matched_measure"], "s178")
        self.assertEqual(accepted["matched_amendment_identity"], "116s178eah")
        # The document really does lack a measure element -- that is the gap.
        self.assertEqual(accepted["observed_measure_fields"], [])

    def test_dublin_core_binding_covers_congress_measure_and_version(self) -> None:
        # Each of the three components must be load-bearing on its own; a fix
        # that only pinned the measure would be weaker than the legis-num path
        # it substitutes for, not stronger.
        key, version = "116:s:178", "eah"
        job = {"canonical_key": key, "version_code": version}

        for wrong_title in ("116 S179 EAH", "116 S178 EAS"):
            with self.subTest(title=wrong_title):
                with self.assertRaisesRegex(
                    collector.CollectionError, "amendment-doc identity mismatch"
                ):
                    collector.validate_xml(
                        amendment_doc(key, version, title_identity=wrong_title),
                        job,
                    )

        # A wrong Congress is refused earlier still, by the congress check.
        with self.assertRaisesRegex(
            collector.CollectionError, "Congress identity mismatch"
        ):
            collector.validate_xml(
                amendment_doc(
                    key,
                    version,
                    title_identity="115 S178 EAH",
                    congress_text="115th CONGRESS",
                ),
                job,
            )

    # ------------------------------------------------------------------
    # 2. the guard: the fallback must not fire when a measure element exists
    # ------------------------------------------------------------------
    def test_guard_defers_to_measure_element_when_present(self) -> None:
        # `and not measure_values` means the fallback only fills the gap.  An
        # amendment document that does carry <legis-num> keeps the ordinary
        # path, even when the Dublin Core title would disagree with it.
        key, version = "116:s:178", "eah"
        job = {"canonical_key": key, "version_code": version}
        accepted = collector.validate_xml(
            amendment_doc(
                key, version, title_identity="116 S9999 EAS", legis_num="S. 178"
            ),
            job,
        )

        self.assertEqual(accepted["matched_measure"], "s178")
        self.assertEqual(accepted["observed_measure_fields"], ["S. 178"])
        # Proof the Dublin Core branch was skipped, not merely agreed with.
        self.assertNotIn("identity_source", accepted)
        self.assertNotIn("matched_amendment_identity", accepted)

    def test_guard_keeps_measure_element_authoritative_on_rejection(self) -> None:
        # The sharp case: a correct Dublin Core title cannot rescue a document
        # whose measure element is wrong.  The error must come from the
        # ordinary path, which proves the fallback never ran.
        key, version = "116:s:178", "eah"
        job = {"canonical_key": key, "version_code": version}
        with self.assertRaises(collector.CollectionError) as caught:
            collector.validate_xml(
                amendment_doc(
                    key, version, title_identity="116 S178 EAH", legis_num="S. 179"
                ),
                job,
            )

        message = str(caught.exception)
        self.assertIn("XML measure identity mismatch", message)
        self.assertNotIn("amendment-doc", message)

    def test_guard_path_does_not_require_a_planned_version_code(self) -> None:
        # The Dublin Core branch requires job["version_code"]; the ordinary
        # path does not.  A measure-bearing amendment document must therefore
        # still validate when the job carries no version code at all.
        accepted = collector.validate_xml(
            amendment_doc("116:s:178", "eah", legis_num="S. 178"),
            {"canonical_key": "116:s:178"},
        )
        self.assertEqual(accepted["matched_measure"], "s178")
        self.assertNotIn("identity_source", accepted)

    # ------------------------------------------------------------------
    # 3. spacing variants -- GovInfo is not consistent about the title layout
    # ------------------------------------------------------------------
    def test_dublin_core_title_spacing_variants_all_bind(self) -> None:
        cases = [
            # (canonical_key, version, dc:title identity as GovInfo writes it)
            ("116:s:178", "eah", "116 S178 EAH"),          # measure unspaced
            ("117:hr:1437", "eah", "117 HR 1437 EAH"),     # measure spaced
            ("117:hr:1437", "eah", "117 H.R. 1437 EAH"),   # punctuated measure
            ("117:hr:1437", "eah", "117 HR1437 EAH"),      # unspaced, two-letter
            ("116:s:1069", "eah", "116  S  1069  EAH"),    # doubled separators
            ("116:s:178", "eah", "116 S178 eah"),          # lowercase version
            ("116:s:178", "eah", "116 S178 EAH "),         # trailing space
            ("118:hres:1234", "eah", "118 HRES 1234 EAH"),
            ("119:sconres:14", "eah", "119 S.CON.RES. 14 EAH"),
        ]
        for key, version, title in cases:
            with self.subTest(title=title):
                job = {"canonical_key": key, "version_code": version}
                accepted = collector.validate_xml(
                    amendment_doc(key, version, title_identity=title), job
                )
                congress, measure_type, number = key.split(":")
                self.assertEqual(
                    accepted["matched_amendment_identity"],
                    f"{congress}{measure_type}{number}{version}",
                )
                self.assertEqual(accepted["identity_source"], "dublin_core_title")

    # ------------------------------------------------------------------
    # 4. fail closed -- never default open when identity is absent or unclear
    # ------------------------------------------------------------------
    def test_missing_dublin_core_block_fails_closed(self) -> None:
        # This is 118:s:2051 eah, the one residual the fix could not recover:
        # the document has no <metadata>/dublinCore at all.
        with self.assertRaisesRegex(
            collector.CollectionError, "missing its Dublin Core title"
        ):
            collector.validate_xml(
                amendment_doc("118:s:2051", "eah", include_dublin_core=False),
                {"canonical_key": "118:s:2051", "version_code": "eah"},
            )

    def test_unparsable_or_version_free_title_fails_closed(self) -> None:
        key, version = "116:s:178", "eah"
        job = {"canonical_key": key, "version_code": version}
        for title in (
            "Uighur Intervention and Global Humanitarian Unified Response Act",
            "116 S178",  # no version token: must not be accepted on two of three
        ):
            with self.subTest(title=title):
                with self.assertRaisesRegex(
                    collector.CollectionError, "title identity unparsable"
                ):
                    collector.validate_xml(
                        amendment_doc(key, version, title_identity=title), job
                    )

    def test_dublin_core_path_requires_a_planned_version_code(self) -> None:
        # Without a planned version code there is nothing to bind the third
        # component against, so the fallback must refuse rather than degrade
        # to a measure-only check.
        with self.assertRaisesRegex(
            collector.CollectionError, "requires a planned version code"
        ):
            collector.validate_xml(
                amendment_doc("116:s:178", "eah"),
                {"canonical_key": "116:s:178"},
            )

    def test_regex_and_tag_constants_are_present(self) -> None:
        # A future refactor that drops either constant would silently return
        # the collector to the revision that quarantined all 41 amendments.
        self.assertEqual(
            collector.DC_TITLE_TAG, "{http://purl.org/dc/elements/1.1/}title"
        )
        match = collector.AMENDMENT_DC_TITLE_RE.match("117 HR 1437 EAH: An Act")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("identity"), "117 HR 1437 EAH")
        self.assertIsNone(collector.AMENDMENT_DC_TITLE_RE.match("An Act to do"))


if __name__ == "__main__":
    unittest.main()
