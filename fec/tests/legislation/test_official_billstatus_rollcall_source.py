# -*- coding: utf-8 -*-
"""Offline contracts for official no-key BILLSTATUS action discovery."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from legislation import official_billstatus_rollcall_source as BS  # noqa: E402


def billstatus_xml(
    *,
    congress="119",
    bill_type="HR",
    bill_number="1",
    vote_congress="119",
    version="3.0.0",
    vote_url="",
):
    url_node = f"<url>{vote_url}</url>" if vote_url else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<billStatus>
  <version>{version}</version>
  <bill>
    <congress>{congress}</congress>
    <billType>{bill_type}</billType>
    <billNumber>{bill_number}</billNumber>
    <actions>
      <item>
        <actionDate>2025-01-10</actionDate>
        <text>Introduced in House</text>
        <type>IntroReferral</type>
      </item>
      <item>
        <actionDate>2025-01-20</actionDate>
        <text>Passed House by Yea-Nay Vote.</text>
        <type>Floor</type>
        <recordedVotes>
          <recordedVote>
            <chamber>House</chamber>
            <congress>{vote_congress}</congress>
            <date>2025-01-20</date>
            <rollNumber>5</rollNumber>
            <sessionNumber>1</sessionNumber>
            {url_node}
          </recordedVote>
        </recordedVotes>
      </item>
    </actions>
  </bill>
</billStatus>
""".encode("utf-8")


class OfficialBillStatusSourceTests(unittest.TestCase):
    def test_canonical_source_and_official_url_templates(self) -> None:
        self.assertEqual(
            (
                "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/"
                "BILLSTATUS-119hr1.xml"
            ),
            BS.billstatus_url(119, "H.R.", 1),
        )
        self.assertEqual(
            "https://clerk.house.gov/evs/2025/roll005.xml",
            BS.official_rollcall_xml_url(
                chamber="House",
                congress=119,
                session=1,
                roll=5,
                vote_date="2025-01-20",
            ),
        )
        self.assertEqual(
            (
                "https://www.senate.gov/legislative/LIS/roll_call_votes/"
                "vote1191/vote_119_1_00007.xml"
            ),
            BS.official_rollcall_xml_url(
                chamber="Senate",
                congress=119,
                session=1,
                roll=7,
            ),
        )

    def test_bulk_xml_identity_is_exact_and_all_actions_are_preserved(self) -> None:
        url = BS.billstatus_url(119, "hr", 1)
        parsed = BS.parse_billstatus_actions(
            billstatus_xml(),
            congress=119,
            measure_type="hr",
            number=1,
            source_url=url,
        )
        self.assertTrue(parsed["identity_verified"])
        self.assertTrue(parsed["actions_complete"])
        self.assertEqual(2, parsed["action_count"])
        self.assertEqual(1, parsed["recorded_vote_count"])
        self.assertEqual(
            "Introduced in House", parsed["actions"][0]["text"]
        )
        vote = parsed["actions"][1]["recordedVotes"][0]
        self.assertEqual(
            "https://clerk.house.gov/evs/2025/roll005.xml", vote["url"]
        )
        self.assertEqual(
            "deterministic_official_event_key_template",
            vote["urlProvenance"],
        )

    def test_supplied_official_recorded_vote_url_is_retained(self) -> None:
        source = BS.billstatus_url(119, "hr", 1)
        official = "https://clerk.house.gov/evs/2025/roll005.xml"
        parsed = BS.parse_billstatus_actions(
            billstatus_xml(vote_url=official),
            congress=119,
            measure_type="hr",
            number=1,
            source_url=source,
        )
        vote = parsed["actions"][1]["recordedVotes"][0]
        self.assertEqual(official, vote["url"])
        self.assertEqual(official, vote["associationUrl"])
        self.assertEqual(
            "billstatus_recorded_vote_xml_url", vote["urlProvenance"]
        )

    def test_official_human_vote_page_is_preserved_but_xml_is_canonical(self) -> None:
        source = BS.billstatus_url(119, "hr", 1)
        association = "https://clerk.house.gov/Votes/2025005"
        parsed = BS.parse_billstatus_actions(
            billstatus_xml(vote_url=association),
            congress=119,
            measure_type="hr",
            number=1,
            source_url=source,
        )
        vote = parsed["actions"][1]["recordedVotes"][0]
        self.assertEqual(association, vote["associationUrl"])
        self.assertEqual(
            "https://clerk.house.gov/evs/2025/roll005.xml", vote["url"]
        )
        self.assertEqual(
            "deterministic_official_event_key_template",
            vote["urlProvenance"],
        )

    def test_wrong_measure_identity_and_noncanonical_source_fail_closed(self) -> None:
        source = BS.billstatus_url(119, "hr", 1)
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "identity mismatch"
        ):
            BS.parse_billstatus_actions(
                billstatus_xml(bill_number="2"),
                congress=119,
                measure_type="hr",
                number=1,
                source_url=source,
            )
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "not canonical"
        ):
            BS.parse_billstatus_actions(
                billstatus_xml(),
                congress=119,
                measure_type="hr",
                number=1,
                source_url=source + "?download=1",
            )

    def test_amendments_and_event_key_conflicts_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "does not publish amendment records"
        ):
            BS.billstatus_url(119, "hamdt", 5)
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "conflicts with Congress/session"
        ):
            BS.official_rollcall_xml_url(
                chamber="House",
                congress=119,
                session=1,
                roll=5,
                vote_date="2026-01-20",
            )

    def test_wrapper_version_entities_and_vote_congress_fail_closed(self) -> None:
        source = BS.billstatus_url(119, "hr", 1)
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "root must be billStatus"
        ):
            BS.parse_billstatus_actions(
                billstatus_xml()
                .replace(b"<billStatus>", b"<response>", 1)
                .replace(b"</billStatus>", b"</response>", 1),
                congress=119,
                measure_type="hr",
                number=1,
                source_url=source,
            )
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "allowlisted format version"
        ):
            BS.parse_billstatus_actions(
                billstatus_xml(version="4.0.0"),
                congress=119,
                measure_type="hr",
                number=1,
                source_url=source,
            )
        with self.assertRaisesRegex(
            BS.BillStatusSourceError, "DOCTYPE or ENTITY"
        ):
            BS.parse_billstatus_actions(
                billstatus_xml().replace(
                    b"<billStatus>",
                    (
                        b"<!DOCTYPE billStatus "
                        b"[<!ENTITY injected 'recorded vote'>]>"
                        b"<billStatus>"
                    ),
                    1,
                ),
                congress=119,
                measure_type="hr",
                number=1,
                source_url=source,
            )
        with self.assertRaisesRegex(
            BS.BillStatusSourceError,
            "recorded vote Congress conflicts",
        ):
            BS.parse_billstatus_actions(
                billstatus_xml(vote_congress="118"),
                congress=119,
                measure_type="hr",
                number=1,
                source_url=source,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
