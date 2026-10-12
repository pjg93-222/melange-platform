"""Contract fixtures for official full-history milestone and role adapters."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

from fec.legislation.build_bill_milestones import build_artifacts, iter_archive_bills, parse_bill_xml


def action(day, text, kind="Floor", code="", source="House floor actions"):
    return f"<item><actionDate>{day}</actionDate><text>{escape(text)}</text><type>{kind}</type><actionCode>{code}</actionCode><sourceSystem><name>{source}</name></sourceSystem></item>"


def cosponsor(member, joined, original="False", withdrawn=None):
    withdrawal = f"<sponsorshipWithdrawnDate>{withdrawn}</sponsorshipWithdrawnDate>" if withdrawn else ""
    return f"<item><bioguideId>{member}</bioguideId><sponsorshipDate>{joined}</sponsorshipDate><isOriginalCosponsor>{original}</isOriginalCosponsor>{withdrawal}</item>"


def xml(actions="", roles="", typ="HR", title="Policy Act", chamber="House", extra="", number=1):
    return f"""<billStatus><bill><congress>118</congress><type>{typ}</type><number>{number}</number><originChamber>{chamber}</originChamber><introducedDate>2023-01-03</introducedDate><title>{escape(title)}</title><sponsors><item><bioguideId>S000001</bioguideId><fullName>Test Sponsor</fullName></item></sponsors><cosponsors>{roles}</cosponsors><actions>{actions}</actions>{extra}</bill></billStatus>""".encode()


class MilestoneTests(unittest.TestCase):
    def test_referral_not_committee_action(self):
        bill, roles = parse_bill_xml(xml(action("2023-01-03", "Referred to the House Committee on Financial Services.", "IntroReferral", "H11100")))
        self.assertTrue(bill["milestones"]["BILL"]["reached"])
        self.assertFalse(bill["milestones"]["AIC"]["reached"])
        self.assertFalse(bill["milestones"]["ABC"]["reached"])
        self.assertEqual(roles[0]["role"], "sponsor")

    def test_stages_use_first_qualifying_date_and_preserve_early_success(self):
        hearing = action("2023-02-01", "Subcommittee Hearings Held.", "Committee", source="House committee actions")
        report_order = action("2023-02-02", "Ordered to be Reported by voice vote.", "Committee", source="House committee actions")
        report = action("2023-02-03", "Reported by the Committee on Financial Services.", "Committee", "5000", "Library of Congress")
        passed = action("2023-03-01", "On passage Passed by voice vote.", code="H37100")
        veto = action("2023-04-01", "Vetoed by President.", "President", "31000", "Library of Congress")
        bill, _ = parse_bill_xml(xml(hearing + hearing + report_order + report + passed + veto))
        self.assertEqual(bill["milestones"]["AIC"]["date"], "2023-02-01")
        self.assertEqual(bill["milestones"]["ABC"]["date"], "2023-02-03")
        self.assertEqual(bill["milestones"]["PASS"]["date"], "2023-03-01")
        self.assertFalse(bill["milestones"]["LAW"]["reached"])
        self.assertEqual(len(bill["milestones"]["AIC"]["evidence"]), 1)

    def test_order_to_report_is_not_actual_report(self):
        bill, _ = parse_bill_xml(xml(action("2023-02-02", "Ordered to be Reported by voice vote.", "Committee", source="House committee actions")))
        self.assertTrue(bill["milestones"]["AIC"]["reached"])
        self.assertFalse(bill["milestones"]["ABC"]["reached"])

    def test_amended_report_and_subcommittee_forwarding(self):
        forwarded = action("2023-02-01", "Forwarded by Subcommittee to Full Committee (Amended) by Voice Vote.", "Committee", source="House committee actions")
        reported = action("2023-02-03", "Reported (Amended) by the Committee on Financial Services.", "Committee", "5000", "Library of Congress")
        bill, _ = parse_bill_xml(xml(forwarded + reported))
        self.assertEqual(bill["milestones"]["AIC"]["date"], "2023-02-01")
        self.assertEqual(bill["milestones"]["ABC"]["date"], "2023-02-03")

    def test_pre_introduction_subject_hearing_is_not_a_completed_report(self):
        hearing = action("2023-01-01", "Committee on Armed Services. Hearings held on the subject prior to committee ordering to be reported an original measure.", "Committee", source="Senate")
        report = action("2023-02-01", "Committee on Armed Services. Original measure reported to Senate by Senator Reed.", "Committee", "14000", "Library of Congress")
        bill, _ = parse_bill_xml(xml(hearing + report, typ="S", chamber="Senate"))
        self.assertEqual(bill["milestones"]["AIC"]["date"], "2023-01-01")
        self.assertEqual(bill["milestones"]["ABC"]["date"], "2023-02-01")
        self.assertIn("official_milestone_precedes_introduction", bill["coverage_exceptions"])

    def test_defeated_committee_vote_is_action_but_not_advancement_beyond_committee(self):
        bill, _ = parse_bill_xml(xml(action("2023-02-01", "Motion to Report Measure Defeated by the Yeas and Nays: 32 - 32.", "Committee", source="House committee actions")))
        self.assertTrue(bill["milestones"]["AIC"]["reached"])
        self.assertFalse(bill["milestones"]["ABC"]["reached"])

    def test_committee_bypass_does_not_invent_aic(self):
        bill, _ = parse_bill_xml(xml(action("2023-02-01", "Senate Committee on Finance discharged by Unanimous Consent.", "Committee", "14500", "Library of Congress") + action("2023-02-02", "Passed Senate without amendment by Unanimous Consent.", source="Senate"), typ="S", chamber="Senate"))
        self.assertFalse(bill["milestones"]["AIC"]["reached"])
        self.assertEqual(bill["milestones"]["ABC"]["date"], "2023-02-01")
        self.assertTrue(bill["milestones"]["PASS"]["reached"])

    def test_other_chamber_does_not_count_as_home_chamber(self):
        bill, _ = parse_bill_xml(xml(action("2023-02-01", "Committee on Finance. Hearings held.", "Committee", source="Senate") + action("2023-03-01", "Passed/agreed to in Senate: Passed Senate.", code="17000", source="Library of Congress")))
        for stage in ("AIC", "ABC", "PASS"):
            self.assertFalse(bill["milestones"][stage]["reached"])

    def test_procedural_motions_and_related_rule_are_not_passage(self):
        events = [
            action("2023-02-01", "On motion to suspend the rules and pass the bill Failed by the Yeas and Nays."),
            action("2023-02-02", "Motion to proceed to consideration of measure agreed to in Senate by Unanimous Consent.", source="Senate", code="S05205"),
            action("2023-02-03", "Rule H. Res. 398 passed House.", code="H1L220"),
            action("2023-02-04", "Presented to President.", "President", "28000", "Library of Congress"),
        ]
        bill, _ = parse_bill_xml(xml("".join(events)))
        self.assertFalse(bill["milestones"]["PASS"]["reached"])
        self.assertFalse(bill["milestones"]["LAW"]["reached"])

    def test_actual_enactment_is_not_companion_enactment(self):
        related = "<relatedBills><item><latestAction><actionDate>2023-04-01</actionDate><text>Became Public Law No: 118-2.</text></latestAction></item></relatedBills>"
        bill, _ = parse_bill_xml(xml(action("2023-01-03", "Introduced in House", "IntroReferral"), extra=related))
        self.assertFalse(bill["milestones"]["LAW"]["reached"])
        law, _ = parse_bill_xml(xml(action("2023-04-01", "Became Public Law No: 118-2.", "BecameLaw", "36000", "Library of Congress")))
        self.assertEqual(law["milestones"]["LAW"]["date"], "2023-04-01")

    def test_ineligible_measures_never_have_law_milestones(self):
        for typ in ("HRES", "SCONRES", "HAMDT", "LETTER"):
            bill, _ = parse_bill_xml(xml(typ=typ))
            self.assertFalse(bill["eligible"])
            self.assertTrue(all(m["state"] == "not_applicable" for m in bill["milestones"].values()))
        bill, _ = parse_bill_xml(xml(typ="HJRES", title="Proposing an amendment to the Constitution of the United States."))
        self.assertEqual(bill["exclusion_reason"], "constitutional_amendment_joint_resolution")
        bill, _ = parse_bill_xml(xml(typ="HJRES", title="Disapproving a rule submitted by the Department of Labor."))
        self.assertTrue(bill["eligible"])

    def test_missing_history_is_unknown_not_no_advancement(self):
        bill, _ = parse_bill_xml(xml())
        self.assertEqual(bill["milestones"]["BILL"]["state"], "observed")
        self.assertEqual(bill["milestones"]["AIC"]["state"], "unknown")
        self.assertIsNone(bill["milestones"]["LAW"]["reached"])

    def test_as_of_censors_future_actions_and_summary_updates(self):
        summary = "<summaries><summary><actionDate>2023-01-03</actionDate><updateDate>2023-03-01</updateDate><text>Future published summary</text></summary></summaries>"
        bill, _ = parse_bill_xml(xml(action("2023-01-03", "Introduced in House", "IntroReferral") + action("2023-04-01", "On passage Passed.", code="H37100"), extra=summary), "2023-02-01")
        self.assertFalse(bill["milestones"]["PASS"]["reached"])
        self.assertEqual(bill["summary"], "")
        self.assertEqual(bill["coverage"]["censored_action_rows"], 1)
        self.assertEqual(bill["coverage"]["censored_summary_rows"], 1)

    def test_role_dates_original_later_withdrawn_and_invalid(self):
        roles_xml = cosponsor("O000001", "2023-01-03", "True") + cosponsor("L000001", "2023-02-01", withdrawn="2023-03-01") + cosponsor("F000001", "2023-05-01") + cosponsor("B000001", "2023-01-01") + cosponsor("X000001", "2023-02-01", "True")
        bill, roles = parse_bill_xml(xml(roles=roles_xml), "2023-02-15")
        indexed = {r["bioguide_id"]: r for r in roles}
        self.assertNotIn("F000001", indexed)
        self.assertEqual(indexed["O000001"]["role"], "original")
        self.assertEqual(indexed["L000001"]["days_from_introduction"], 29)
        self.assertIsNone(indexed["L000001"]["withdrawn_date"])
        self.assertEqual(indexed["B000001"]["chronology_status"], "quarantined")
        self.assertEqual(indexed["X000001"]["chronology_status"], "quarantined")
        self.assertEqual(bill["candidate_features"]["unique_cosponsor_count"], 2)

    def test_duplicate_roles_and_rejoin_episodes(self):
        first = cosponsor("L000001", "2023-02-01", withdrawn="2023-03-01")
        second = cosponsor("L000001", "2023-04-01")
        bill, roles = parse_bill_xml(xml(roles=first + first + second))
        episodes = [r for r in roles if r["bioguide_id"] == "L000001"]
        self.assertEqual(len(episodes), 2)
        self.assertTrue(all(r["chronology_status"] == "verified" for r in episodes))
        self.assertEqual(bill["candidate_features"]["unique_cosponsor_count"], 1)

    def test_amendments_censored_from_full_actions_not_latest_action(self):
        extra = "<amendments><amendment><congress>118</congress><type>HAMDT</type><number>4</number><submittedDate>2023-02-01</submittedDate><actions><actions>" + action("2023-02-02", "An amendment was offered.") + action("2023-03-01", "On agreeing to the amendment Agreed to.") + "</actions></actions></amendment><amendment><congress>118</congress><type>HAMDT</type><number>5</number><submittedDate>2023-04-01</submittedDate></amendment></amendments>"
        bill, _ = parse_bill_xml(xml(extra=extra), "2023-02-15")
        features = bill["candidate_features"]
        self.assertEqual(features["unique_amendment_count"], 1)
        self.assertEqual(features["amendment_status_counts"]["submitted"], 1)
        self.assertEqual(features["amendment_status_counts"]["proposed"], 1)
        self.assertEqual(features["amendment_status_counts"]["agreed"], 0)

    def test_deterministic_archives_manifest_and_conflicting_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "a.zip", root / "b.zip"
            with zipfile.ZipFile(first, "w") as archive:
                archive.writestr("BILLSTATUS-118hr1.xml", xml())
            with zipfile.ZipFile(second, "w") as archive:
                archive.writestr("BILLSTATUS-118hr1.xml", xml())
            self.assertEqual(len(list(iter_archive_bills([first, second]))), 1)
            manifest = build_artifacts([first, second], root / "out")
            self.assertEqual(manifest["counts"]["118:House:eligible"], 1)
            again = build_artifacts([second, first], root / "out2")
            self.assertEqual(manifest["artifacts"], again["artifacts"])
            self.assertEqual(json.loads((root / "out/effectiveness_universe_manifest.json").read_text())["mapper_version"], manifest["mapper_version"])
            with zipfile.ZipFile(second, "w") as archive:
                archive.writestr("BILLSTATUS-118hr1.xml", xml(title="Conflicting title"))
            with self.assertRaisesRegex(ValueError, "Conflicting frozen records"):
                list(iter_archive_bills([first, second]))


if __name__ == "__main__":
    unittest.main()
