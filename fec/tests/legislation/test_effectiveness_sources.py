"""Frozen acceptance/roster adapter checks, including sparse and non-law inputs."""
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, FEC)
from legislation.effectiveness_sources import accepted_signals, historical_roster, measure_id, read_service_spine
from engine.legislative_effectiveness import compute_effectiveness


def write_json(root, name, data):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data), encoding="utf-8")


def write_lines(root, name, data):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(json.dumps(row) for row in data), encoding="utf-8")


def yaml_person(bio, name, terms):
    return (f"- id:\n    bioguide: {bio}\n  name:\n    first: {name}\n    last: Member\n  bio:\n    gender: F\n  terms:\n"
            + "".join(f"  - type: {kind}\n    start: '{start}'\n    end: '{end}'\n    state: CA\n    party: {party}\n"
                      for kind,start,end,party in terms))


class EffectivenessSourcesTests(unittest.TestCase):
    def test_measure_ids_are_congress_qualified_and_letters_stable(self):
        self.assertEqual(measure_id("H.R. 001", 118), "118:hr:1")
        self.assertEqual(measure_id("118:HR:001", 118), "118:hr:1")
        self.assertIsNone(measure_id("119:hr:1", 118))
        self.assertEqual(measure_id("LTR-2024-AB", 118), "118:letter:LTR-2024-AB")
        self.assertEqual(measure_id("118:letter:LTR-2024-AB", 118), "118:letter:LTR-2024-AB")
        self.assertIsNone(measure_id("ambiguous 123", 118))

    def test_roster_deduplicates_replacements_and_keeps_chamber_switch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "votes").mkdir()
            with (root / "votes/HS118_members.csv").open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=["bioguide_id","chamber","bioname","party_code"])
                writer.writeheader()
                for bio,chamber in [("A000001","House"),("A000001","House"),("B000001","House"),("C000001","House"),("D000001","House")]:
                    writer.writerow({"bioguide_id":bio,"chamber":chamber,"bioname":bio,"party_code":"100"})
            spine = root / "service.yaml"
            spine.write_text(yaml_person("A000001","A",[("rep","2023-01-03","2024-12-08","Democrat"),
                                                          ("sen","2024-12-09","2031-01-03","Democrat")])
                             + yaml_person("B000001","B",[("rep","2024-03-01","2025-01-03","Republican")])
                             + yaml_person("C000001","C",[("rep","2025-01-03","2027-01-03","Republican")]), encoding="utf-8")
            rows,contexts,receipt = historical_roster(root,118,"2024-12-31",service_paths=[spine])
            self.assertEqual(receipt["by_chamber"], {"House":3,"Senate":1})
            self.assertEqual(receipt["spine_additions"], 1)
            self.assertEqual(receipt["exclusions"]["no_service_by_cutoff"], 1)
            self.assertEqual(len([r for r in rows if r["bioguide_id"] == "A000001"]), 2)
            self.assertEqual(next(r for r in rows if r["bioguide_id"] == "D000001")["coverage_status"], "voteview_member_term_only")
            self.assertEqual(next(r for r in contexts if r["bioguide_id"] == "B000001")["majority_status"], "majority")
            early,_,_ = historical_roster(root,118,"2024-11-01",service_paths=[spine])
            self.assertFalse(any(r["chamber"] == "Senate" for r in early))
            with self.assertRaises(ValueError):
                historical_roster(root,118,"2022-12-31",service_paths=[spine])

    def test_invalid_service_interval_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.yaml"
            path.write_text(yaml_person("A000001","A",[("rep","2024-01-03","2023-01-03","Democrat")]), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_service_spine([path])

    def _acceptance_fixture(self, root):
        displays = ["H.R. 1","H.R. 2","H.Amdt. 1","LTR-TEST"]
        members = [{"name":"Member A","chamber":"House","bio":{"bioguide":"A000001"},
                    "bills":[{"bill":name,"congress":118,"role":"sponsor"} for name in displays]},
                   {"name":"Member B","chamber":"House","bio":{"bioguide":"B000001"},"bills":[]}]
        write_json(root,"member_desk_bundle.json",{"members":members})
        routing = [{"member":"Member A","bill":name,"congress":118,"disposition":"selected",
                    "effective_category":"CU Tax Status","sign":-1,"effective_role":"sponsor"} for name in displays]
        routing.append({**routing[0], "member":"Member B"})
        write_json(root,"legislation_routing_audit.json",{"rows":routing})
        tags = [{"bill":name,"congress":118,"class":"shared","acu_position":"OPPOSE" if name == "H.R. 1" else ""} for name in displays]
        write_lines(root,"bill_beneficiary_tags.ndjson",tags)
        cells = [{"bill":name,"congress":118,"industry":"credit_union","basis":"operative text","rationale":"test", "adjudicated_by":"reviewer",
                  "adjudicated":"2024-01-01","status":"active","relevance":.8,"valence":-.5,"confidence":.01}
                 for name in ["H.R. 1","H.R. 2"]]
        write_lines(root,"data/lobbying/industry_axes.ndjson",cells)
        write_json(root,"data/engine/live_legislative_pillar.json",{"topic_weights":{"CU Tax Status":1}})
        return [{"measure_id":measure_id(name,118),"congress":118,"chamber":"House","measure_type":measure_id(name,118).split(":")[1],
                 "introduced_date":"2023-01-03","eligible":False,"milestones":{}} for name in displays]

    def test_accepted_membership_tensor_scope_confidence_and_nonlaw_letters(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bills = self._acceptance_fixture(root)
            signals,_,_,letters,coverage = accepted_signals(root,bills,118,"2024-12-31")
            a = [row for row in signals if row["bioguide_id"] == "A000001"]
            self.assertEqual(len(a),4)
            self.assertAlmostEqual(next(r for r in a if r["measure_id"] == "118:hr:1")["tensor_cu"], -.4)
            # Undeclared-position tensor does not silently enlarge legacy scope.
            self.assertAlmostEqual(next(r for r in a if r["measure_id"] == "118:hr:2")["tensor_cu"], .5)
            self.assertIsNone(next(r for r in signals if r["bioguide_id"] == "B000001")["tensor_cu"])
            self.assertEqual(next(r for r in a if r["measure_id"] == "118:hamdt:1")["advocacy_discount"], .5)
            self.assertEqual(len(letters),1)
            self.assertNotIn("letter_signature_date_and_document_unverified", coverage["counts"])
            roles = [{"measure_id":b["measure_id"],"bioguide_id":"A000001","role":"sponsor","joined_date":"2023-01-03","chronology_status":"verified"} for b in bills]
            scores = compute_effectiveness(bills,roles,[{"measure_id":b["measure_id"],"weight":5} for b in bills],
                                           [{"bioguide_id":"A000001","chamber":"House","congress":118}],signals,as_of="2024-12-31")
            member = scores["members"][0]
            self.assertAlmostEqual(member["standard"]["advocacy"]["net"], -10.5)
            self.assertTrue(all(r["stage"] == "ADVOCACY" for r in scores["contributions"]))

    def test_missing_letter_evidence_and_future_intro_held_out(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bills = self._acceptance_fixture(root)
            bills = [b for b in bills if b["measure_type"] != "letter"]
            bills[0]["introduced_date"] = "2025-01-01"
            signals,_,_,letters,coverage = accepted_signals(root,bills,118,"2024-12-31")
            self.assertFalse(any(row["measure_id"] in {"118:hr:1","118:letter:LTR-TEST"} for row in signals))
            self.assertEqual(coverage["counts"]["letter_signature_date_and_document_unverified"],1)
            self.assertEqual(letters[0]["impact_weight"],5)
            self.assertEqual(letters[0]["milestones"],"not_applicable")


if __name__ == "__main__":
    unittest.main()
