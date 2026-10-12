from __future__ import annotations

import unittest

from fec.audit import audit_named_outline_recall_misses as AUDIT


class NamedOutlineRecallMissAuditTests(unittest.TestCase):
    def test_known_bill_identity_is_congress_qualified(self) -> None:
        self.assertEqual(
            AUDIT.canonical_key_from_known_bill(
                {"congress": 119, "number": "H.R.7056"}
            ),
            "119:hr:7056",
        )
        self.assertEqual(
            AUDIT.canonical_key_from_known_bill(
                {"congress": 119, "number": "S. 875"}
            ),
            "119:s:875",
        )

    def test_known_bill_identity_rejects_missing_congress(self) -> None:
        with self.assertRaises(AUDIT.ContractError):
            AUDIT.canonical_key_from_known_bill({"number": "H.R.7056"})

    def test_section_receipt_preserves_missing_state(self) -> None:
        section = {
            "status": "missing",
            "source_xpath": "bill/laws",
            "source_record_sha256": "a" * 64,
            "parsed_fragment_sha256": None,
            "direct_child_count": None,
            "item_count": None,
            "leaf_text_count": None,
            "data": None,
            "ignored_field": "not copied",
        }
        self.assertEqual(
            AUDIT.section_receipt(section),
            {
                "status": "missing",
                "source_xpath": "bill/laws",
                "source_record_sha256": "a" * 64,
                "parsed_fragment_sha256": None,
                "direct_child_count": None,
                "item_count": None,
                "leaf_text_count": None,
                "data": None,
            },
        )


if __name__ == "__main__":
    unittest.main()
