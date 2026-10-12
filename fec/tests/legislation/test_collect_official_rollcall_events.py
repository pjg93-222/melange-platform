# -*- coding: utf-8 -*-
"""Offline fixture contracts for staged official roll-call ingestion."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))
from legislation import collect_official_rollcall_events as RC  # noqa: E402


HOUSE_URL = "https://clerk.house.gov/evs/2025/roll005.xml"
SENATE_URL = (
    "https://www.senate.gov/legislative/LIS/roll_call_votes/"
    "vote1191/vote_119_1_00007.xml"
)


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_ndjson(path: Path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def read_ndjson(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def accepted_row(
    canonical_key: str,
    category: str,
    *,
    direction=1,
    provenance=True,
):
    row = {
        "canonical_key": canonical_key,
        "category_label": category,
        "disposition": "include",
    }
    if direction is not None:
        row["direction"] = direction
        if provenance:
            row["direction_source_url"] = (
                "https://www.congress.gov/bill/" + canonical_key.replace(":", "/")
            )
            row["direction_reason"] = "Fixture policy direction verified from official text."
    return row


def house_xml(legis_num="H R 1", *, second_measure=False, missing_id=False):
    extra = " / H RES 1" if second_measure else ""
    id_one = "" if missing_id else ' name-id="A000001"'
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rollcall-vote>
  <vote-metadata>
    <congress>119</congress><session>1st</session>
    <rollcall-num>5</rollcall-num>
    <legis-num>{legis_num}{extra}</legis-num>
    <vote-question>On Passage</vote-question><vote-result>Passed</vote-result>
    <action-date>10-Jan-2025</action-date>
  </vote-metadata>
  <vote-data>
    <recorded-vote>
      <legislator party="R" state="AL"{id_one}>Alpha</legislator><vote>Yea</vote>
    </recorded-vote>
    <recorded-vote>
      <legislator party="D" state="CA" name-id="B000002">Beta</legislator><vote>Nay</vote>
    </recorded-vote>
    <recorded-vote>
      <legislator party="I" state="VT" name-id="C000003">Gamma</legislator>
      <vote>Not Voting</vote>
    </recorded-vote>
  </vote-data>
</rollcall-vote>
""".encode("utf-8")


def senate_xml():
    return b"""<?xml version="1.0" encoding="UTF-8"?>
<roll_call_vote>
  <congress>119</congress><session>1</session><vote_number>00007</vote_number>
  <vote_date>January 20, 2025</vote_date>
  <question>On Passage of the Bill</question><vote_result>Bill Passed</vote_result>
  <vote_title>Example Act</vote_title>
  <document><document_congress>119</document_congress>
    <document_type>S.</document_type><document_number>2</document_number>
  </document>
  <members>
    <member><member_full>Example (R-UT)</member_full><state>UT</state><party>R</party>
      <lis_member_id>S999</lis_member_id><vote_cast>Yea</vote_cast></member>
    <member><member_full>Other (D-WA)</member_full><state>WA</state><party>D</party>
      <lis_member_id>S998</lis_member_id><vote_cast>Nay</vote_cast></member>
  </members>
</roll_call_vote>
"""


def billstatus_xml(
    *,
    congress=119,
    measure_type="HR",
    number=1,
    chamber="House",
    roll=5,
    session=1,
    action_text="Passed House by Yea-Nay Vote.",
):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<billStatus>
  <version>3.0.0</version>
  <bill>
    <congress>{congress}</congress>
    <billType>{measure_type}</billType>
    <billNumber>{number}</billNumber>
    <actions>
      <item>
        <actionDate>2025-01-20</actionDate>
        <text>{action_text}</text>
        <type>Floor</type>
        <recordedVotes>
          <recordedVote>
            <chamber>{chamber}</chamber>
            <congress>{congress}</congress>
            <date>2025-01-20</date>
            <rollNumber>{roll}</rollNumber>
            <sessionNumber>{session}</sessionNumber>
          </recordedVote>
        </recordedVotes>
      </item>
    </actions>
  </bill>
</billStatus>
""".encode("utf-8")


def actions_payload(chamber, url, roll, text):
    return {
        "actions": [
            {
                "actionDate": "2025-01-20",
                "text": text,
                "type": "Floor",
                "recordedVotes": [
                    {
                        "rollNumber": roll,
                        "url": url,
                        "chamber": chamber,
                        "congress": 119,
                        "sessionNumber": 1,
                    }
                ],
            }
        ],
        "pagination": {"count": 1},
    }


class FixtureRun:
    def __init__(self, base: Path, accepted_rows, responses, member_rows=None):
        self.base = base
        self.accepted = base / "accepted.ndjson"
        write_ndjson(self.accepted, accepted_rows)
        fixture_dir = base / "fixtures"
        fixture_dir.mkdir()
        manifest_responses = {}
        for index, (url, value) in enumerate(responses.items(), 1):
            suffix = ".json" if isinstance(value, dict) else ".xml"
            path = fixture_dir / f"response-{index}{suffix}"
            if isinstance(value, dict):
                write_json(path, value)
                content_type = "application/json"
            else:
                path.write_bytes(value)
                content_type = "application/xml"
            manifest_responses[url] = {
                "path": path.name,
                "status": 200,
                "content_type": content_type,
            }
        self.fixtures = fixture_dir / "manifest.json"
        write_json(
            self.fixtures,
            {"schema": RC.FIXTURE_SCHEMA, "responses": manifest_responses},
        )
        self.members = None
        if member_rows is not None:
            self.members = base / "members.ndjson"
            write_ndjson(self.members, member_rows)

    def collect(self, name="audit-stage", *, action_source="auto"):
        out = self.base / name
        manifest = RC.collect(
            accepted_path=self.accepted,
            out_path=out,
            workspace_root=self.base,
            fixture_manifest=self.fixtures,
            member_map_path=self.members,
            action_source=action_source,
        )
        return out, manifest


class OfficialRollcallFixtureTests(unittest.TestCase):
    def test_excluded_and_non_scoring_edges_do_not_enter_rollcall_universe(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            included = accepted_row("119:hr:1", "Payments", direction=1)
            included.update(
                {"membership_status": "included", "scoring_category": True}
            )
            excluded = accepted_row(
                "119:hr:2", "Interchange", direction=None
            )
            excluded.update(
                {"membership_status": "excluded", "scoring_category": True}
            )
            monitor = accepted_row(
                "119:hr:3", "CU Tax Status", direction=None
            )
            monitor.update(
                {"membership_status": "included", "scoring_category": False}
            )
            ledger = base / "accepted.ndjson"
            write_ndjson(ledger, [included, excluded, monitor])
            measures, direction_audit = RC.load_accepted_measures(ledger)
            self.assertEqual(
                [RC.MeasureKey(119, "hr", 1)], sorted(measures)
            )
            self.assertEqual(1, len(direction_audit))
            self.assertEqual("Payments", direction_audit[0]["category"])
            self.assertEqual("included", direction_audit[0]["disposition"])

    def test_house_action_association_direction_and_deterministic_outputs(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "hr", 1)
            run = FixtureRun(
                base,
                [accepted_row("119:hr:1", "CU Tax Status", direction=1)],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "House", HOUSE_URL, 5, "Passed House by Yea-Nay Vote."
                    ),
                    HOUSE_URL: house_xml(),
                },
            )
            out1, manifest1 = run.collect("audit-stage-one")
            out2, manifest2 = run.collect("audit-stage-two")

            self.assertTrue(manifest1["acceptance_passed"])
            identity = manifest1["member_identity_resolution"]
            self.assertEqual(
                {
                    "status": "absent",
                    "present": False,
                    "receipt": None,
                },
                identity["supplemental_member_map"],
            )
            self.assertEqual(
                {
                    "official_xml_bioguide": 3,
                    "supplemental_member_map": 0,
                },
                identity["included_resolution_counts"],
            )
            self.assertEqual(
                0, identity["unresolved_or_ambiguous_member_row_count"]
            )
            self.assertTrue(
                identity["all_accepted_event_members_uniquely_resolved"]
            )
            event = read_ndjson(out1 / "official_rollcall_event_ledger.ndjson")[0]
            self.assertEqual("119:house:1:5", event["event_id"])
            self.assertEqual("119:hr:1", event["measure_key"]["canonical_key"])
            self.assertEqual("included", event["disposition"])
            aggregate = json.loads(
                (out1 / "official_rollcall_aggregate.json").read_text(encoding="utf-8")
            )
            alpha = aggregate["members"]["A000001"]["CU Tax Status"]
            beta = aggregate["members"]["B000002"]["CU Tax Status"]
            self.assertEqual(1, alpha["lean"])
            self.assertEqual(-1, beta["lean"])
            self.assertEqual(["119:house:1:5"], alpha["event_ids"])
            self.assertNotIn("C000003", aggregate["members"])
            self.assertFalse((out1 / "vote_category_lean.json").exists())

            for name in manifest1["outputs"]:
                self.assertEqual(
                    manifest1["outputs"][name]["sha256"],
                    manifest2["outputs"][name]["sha256"],
                    name,
                )

    def test_senate_official_association_uses_lis_to_bioguide_mapping(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "s", 2)
            run = FixtureRun(
                base,
                [accepted_row("119:s:2", "Interchange", direction=-1)],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "Senate", SENATE_URL, 7, "Passed Senate by Yea-Nay Vote."
                    ),
                    SENATE_URL: senate_xml(),
                },
                member_rows=[
                    {
                        "bioguide_id": "D000004",
                        "lis_member_id": "S999",
                        "chamber": "Senate",
                        "congress": 119,
                        "member_name": "Example",
                        "state": "UT",
                        "source_reference": "official Senate identity crosswalk fixture",
                    },
                    {
                        "bioguide_id": "E000005",
                        "lis_member_id": "S998",
                        "chamber": "Senate",
                        "congress": 119,
                        "member_name": "Other",
                        "state": "WA",
                        "source_reference": "official Senate identity crosswalk fixture",
                    },
                ],
            )
            out, manifest = run.collect()
            self.assertTrue(manifest["acceptance_passed"])
            identity = manifest["member_identity_resolution"]
            self.assertEqual(
                "present", identity["supplemental_member_map"]["status"]
            )
            self.assertEqual(
                {
                    "official_xml_bioguide": 0,
                    "supplemental_member_map": 2,
                },
                identity["included_resolution_counts"],
            )
            aggregate = json.loads(
                (out / "official_rollcall_aggregate.json").read_text(encoding="utf-8")
            )
            # Direction -1 means the Nay vote is industry-aligned.
            self.assertEqual(-1, aggregate["members"]["D000004"]["Interchange"]["lean"])
            self.assertEqual(1, aggregate["members"]["E000005"]["Interchange"]["lean"])
            vote_rows = read_ndjson(out / "official_rollcall_member_votes.ndjson")
            self.assertEqual(
                {"D000004", "E000005"},
                {row["bioguide_id"] for row in vote_rows},
            )

    def test_billstatus_fixture_needs_no_api_key_and_binds_exact_identity(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "hr", 1)
            bulk_url = RC.BILLSTATUS.billstatus_url(
                key.congress, key.measure_type, key.number
            )
            run = FixtureRun(
                base,
                [accepted_row("119:hr:1", "Payments", direction=1)],
                {
                    bulk_url: billstatus_xml(),
                    HOUSE_URL: house_xml(),
                },
            )
            out, manifest = run.collect(action_source="billstatus")
            self.assertTrue(manifest["acceptance_passed"])
            policy = manifest["official_action_source_policy"]
            self.assertFalse(policy["congress_api_key_present"])
            self.assertEqual(
                {"billstatus": 1}, policy["selected_source_counts"]
            )
            event = read_ndjson(
                out / "official_rollcall_event_ledger.ndjson"
            )[0]
            self.assertEqual(HOUSE_URL, event["official_vote_url"])
            self.assertEqual(
                "deterministic_official_event_key_template",
                event["congress_action"]["recorded_vote"]["urlProvenance"],
            )
            match = read_ndjson(
                out / "rollcall_measure_match_audit.ndjson"
            )[0]
            self.assertEqual("billstatus", match["official_action_source"])
            self.assertEqual(bulk_url, match["official_action_source_url"])

    def test_no_key_amendment_source_is_explicitly_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            accepted = base / "accepted.ndjson"
            write_ndjson(
                accepted,
                [accepted_row("119:hamdt:1", "Payments", direction=1)],
            )
            out = base / "audit-amendment"
            manifest = RC.collect(
                accepted_path=accepted,
                out_path=out,
                workspace_root=base,
            )
            self.assertFalse(manifest["acceptance_passed"])
            match = read_ndjson(
                out / "rollcall_measure_match_audit.ndjson"
            )[0]
            self.assertEqual("ambiguous", match["disposition"])
            self.assertIn(
                "official amendment action source unavailable",
                match["error"],
            )
            self.assertEqual(
                {"unavailable": 1},
                manifest["official_action_source_policy"][
                    "selected_source_counts"
                ],
            )

    def test_type_aware_identity_collision_is_unmatched_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "hr", 1)
            run = FixtureRun(
                base,
                [accepted_row("119:hr:1", "Payments")],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "House", HOUSE_URL, 5, "Passed House by Yea-Nay Vote."
                    ),
                    HOUSE_URL: house_xml("H RES 1"),
                },
            )
            out, manifest = run.collect()
            self.assertFalse(manifest["acceptance_passed"])
            event = read_ndjson(out / "official_rollcall_event_ledger.ndjson")[0]
            self.assertEqual("unmatched", event["disposition"])
            self.assertEqual("official_xml_measure_identity_mismatch", event["reason"])
            self.assertEqual(0, manifest["aggregate_cell_count"])

    def test_non_unique_official_measure_and_missing_bioguide_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "hr", 1)
            ambiguous = FixtureRun(
                base,
                [accepted_row("119:hr:1", "Payments")],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "House", HOUSE_URL, 5, "Passed House by Yea-Nay Vote."
                    ),
                    HOUSE_URL: house_xml(second_measure=True),
                },
            )
            out, manifest = ambiguous.collect("audit-ambiguous")
            event = read_ndjson(out / "official_rollcall_event_ledger.ndjson")[0]
            self.assertFalse(manifest["acceptance_passed"])
            self.assertEqual("ambiguous", event["disposition"])
            self.assertEqual("official_xml_measure_identity_not_unique", event["reason"])

        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            unmatched_member = FixtureRun(
                base,
                [accepted_row("119:hr:1", "Payments")],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "House", HOUSE_URL, 5, "Passed House by Yea-Nay Vote."
                    ),
                    HOUSE_URL: house_xml(missing_id=True),
                },
            )
            out, manifest = unmatched_member.collect("audit-unmatched-member")
            self.assertFalse(manifest["acceptance_passed"])
            rows = read_ndjson(out / "official_rollcall_member_votes.ndjson")
            self.assertEqual("unmatched", rows[0]["disposition"])
            identity = manifest["member_identity_resolution"]
            self.assertEqual(
                1, identity["unresolved_or_ambiguous_member_row_count"]
            )
            self.assertFalse(
                identity["all_accepted_event_members_uniquely_resolved"]
            )
            self.assertTrue(
                any("unresolved_member_bioguide_rows" in issue
                    for issue in manifest["blocking_issues"])
            )

    def test_ambiguous_supplemental_member_map_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "hr", 1)
            run = FixtureRun(
                base,
                [accepted_row("119:hr:1", "Payments")],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "House", HOUSE_URL, 5, "Passed House by Yea-Nay Vote."
                    ),
                    HOUSE_URL: house_xml(missing_id=True),
                },
                member_rows=[
                    {
                        "bioguide_id": "A000001",
                        "chamber": "House",
                        "congress": 119,
                        "member_name": "Alpha",
                        "state": "AL",
                    },
                    {
                        "bioguide_id": "Z000009",
                        "chamber": "House",
                        "congress": 119,
                        "member_name": "Alpha",
                        "state": "AL",
                    },
                ],
            )
            _out, manifest = run.collect()
            identity = manifest["member_identity_resolution"]
            self.assertFalse(manifest["acceptance_passed"])
            self.assertEqual(1, identity["ambiguous_member_row_count"])
            self.assertEqual(
                1, identity["unresolved_or_ambiguous_member_row_count"]
            )

    def test_direction_missing_and_incomplete_provenance_are_distinct(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            key = RC.MeasureKey(119, "hr", 1)
            run = FixtureRun(
                base,
                [
                    accepted_row(
                        "119:hr:1", "CFPB", direction=1, provenance=False
                    ),
                    accepted_row(
                        "119:hr:1", "CU Tax Status", direction=None
                    ),
                ],
                {
                    RC.actions_endpoint(key): actions_payload(
                        "House", HOUSE_URL, 5, "Passed House by Yea-Nay Vote."
                    ),
                    HOUSE_URL: house_xml(),
                },
            )
            out, manifest = run.collect()
            self.assertFalse(manifest["acceptance_passed"])
            audits = {
                row["category"]: row
                for row in read_ndjson(out / "rollcall_direction_audit.ndjson")
            }
            self.assertEqual(
                "direction_provenance_incomplete", audits["CFPB"]["reason"]
            )
            self.assertEqual(
                "missing_explicit_category_direction",
                audits["CU Tax Status"]["reason"],
            )
            self.assertEqual(
                1,
                manifest["direction_reason_counts"][
                    "direction_provenance_incomplete"
                ],
            )
            self.assertEqual(
                1,
                manifest["direction_reason_counts"][
                    "missing_explicit_category_direction"
                ],
            )
            self.assertEqual(0, manifest["aggregate_cell_count"])

    def test_output_path_must_explicitly_be_audit_or_staging(self):
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            accepted = base / "accepted.ndjson"
            write_ndjson(accepted, [accepted_row("119:hr:1", "Payments")])
            fixtures = base / "fixture.json"
            write_json(
                fixtures, {"schema": RC.FIXTURE_SCHEMA, "responses": {}}
            )
            with self.assertRaisesRegex(
                RC.CollectorError, "audit/staging directory"
            ):
                RC.collect(
                    accepted_path=accepted,
                    out_path=base / "plain-output",
                    workspace_root=base,
                    fixture_manifest=fixtures,
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
