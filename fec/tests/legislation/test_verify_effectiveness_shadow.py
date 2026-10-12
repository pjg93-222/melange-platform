"""A generation receipt cannot conceal altered evidence or invalid arithmetic."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

FEC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0,FEC)
from engine.legislative_effectiveness import compute_effectiveness, STAGES
from legislation.verify_effectiveness_shadow import verify_generation, ShadowVerificationError


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,sort_keys=True),encoding="utf-8")


def write_rows(path,rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows),encoding="utf-8")


class VerifyEffectivenessShadowTests(unittest.TestCase):
    def make_generation(self, temporary, *, sparse=False):
        workspace=Path(temporary)
        root=workspace/"generation"
        evidence=root/"119/data/legislation"
        engine=root/"119/data/engine"
        bill={"measure_id":"119:hr:1","congress":119,"chamber":"House","measure_type":"hr",
              "introduced_date":"2025-01-03","eligible":True,"milestones":{
                  stage:{"date":"2025-01-03" if stage == "BILL" else None if sparse else "2025-03-01",
                         "state":"unknown" if sparse and stage != "BILL" else "observed",
                         "reached":None if sparse and stage != "BILL" else True} for stage in STAGES}}
        roles=[{"measure_id":bill["measure_id"],"bioguide_id":"A000001","role":"sponsor",
                "joined_date":"2025-01-03","withdrawn_date":None,"chronology_status":"verified"}]
        impacts=[{"measure_id":bill["measure_id"],"weight":5,"class":"S"}]
        roster=[{"bioguide_id":"A000001","congress":119,"chamber":"House"}]
        signals=[{"measure_id":bill["measure_id"],"bioguide_id":"A000001","category":"Tax","standard_direction":1,"tensor_cu":-.4}]
        for name,rows in (("bill_milestones.ndjson",[bill]),("bill_impact.ndjson",impacts),
                          ("member_measure_roles.ndjson",roles),("member_population.ndjson",roster),("accepted_signals.ndjson",signals)):
            write_rows(evidence/name,rows)
        manifest={"as_of":"2026-09-06","historical_member_population":{"normalization_N":{"House":1}},
                  "artifacts":{path.name:{"sha256":digest(path)} for path in evidence.iterdir()},"source_archives":[]}
        manifest_path=evidence/"effectiveness_universe_manifest.json"
        write_json(manifest_path,manifest)
        score=compute_effectiveness([bill],roles,impacts,roster,signals,as_of="2026-09-06")
        score.update({"evidence_manifest_sha256":digest(manifest_path),"label":"current_term_provisional"})
        write_json(engine/"legislative_effectiveness_v1.json",score)
        input_path,code_path=workspace/"input.txt",workspace/"code.py"
        input_path.write_text("frozen input",encoding="utf-8")
        code_path.write_text("# frozen code",encoding="utf-8")
        generation={"schema":"effectiveness-shadow-generation-v1","status":"complete","production_activation":False,
                    "production_hashes_unchanged":True,"as_of":"2026-09-06","terms":[{"congress":119}],
                    "inputs_sha256":{str(input_path):digest(input_path)},"code_sha256":{str(code_path):digest(code_path)},
                    "outputs_sha256":{str(path):digest(path) for path in root.rglob("*") if path.is_file()}}
        write_json(root/"shadow_generation.json",generation)
        return root

    def rehash_outputs(self,root):
        path=root/"shadow_generation.json"
        generation=json.loads(path.read_text())
        generation["outputs_sha256"]={name:digest(Path(name)) for name in generation["outputs_sha256"]}
        write_json(path,generation)

    def mutate_score(self,root,change):
        path=root/"119/data/engine/legislative_effectiveness_v1.json"
        score=json.loads(path.read_text())
        change(score)
        write_json(path,score)
        self.rehash_outputs(root)

    def test_valid_complete_and_missing_history_generations_pass(self):
        for sparse in (False,True):
            with self.subTest(sparse=sparse), tempfile.TemporaryDirectory() as temporary:
                result=verify_generation(self.make_generation(temporary,sparse=sparse))
                self.assertEqual(result["status"],"passed")
                self.assertEqual(result["terms"][0]["denominators_available"],not sparse)
                self.assertGreater(result["checks"],60)

    def test_output_and_source_hash_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            path=root/"119/data/engine/legislative_effectiveness_v1.json"
            path.write_text(path.read_text()+" ")
            with self.assertRaisesRegex(ShadowVerificationError,"outputs hash mismatch"):
                verify_generation(root)
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            (Path(temporary)/"input.txt").write_text("changed")
            with self.assertRaisesRegex(ShadowVerificationError,"inputs hash mismatch"):
                verify_generation(root)

    def test_rehashed_wrong_denominator_and_population_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            self.mutate_score(root,lambda score:score["terms"]["119:House"]["denominators"].update({"LAW":123}))
            with self.assertRaisesRegex(ShadowVerificationError,"unsigned denominator mismatch"):
                verify_generation(root)
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            self.mutate_score(root,lambda score:score["terms"]["119:House"].update({"population":2}))
            with self.assertRaisesRegex(ShadowVerificationError,"population mismatch"):
                verify_generation(root)

    def test_prejoin_and_duplicate_credit_rejected_after_rehash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            def change(score):
                row=next(r for r in score["contributions"] if r["kind"] == "results")
                row["joined_date"]="2026-01-01"
            self.mutate_score(root,change)
            with self.assertRaisesRegex(ShadowVerificationError,"role episode absent/quarantined|prejoin credit"):
                verify_generation(root)
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            self.mutate_score(root,lambda score:score["contributions"].append(score["contributions"][0].copy()))
            with self.assertRaisesRegex(ShadowVerificationError,"duplicate contribution"):
                verify_generation(root)

    def test_unknown_results_cannot_be_replaced_with_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary,sparse=True)
            self.mutate_score(root,lambda score:score["members"][0]["standard"]["results"].update({"available":True,"net":0}))
            with self.assertRaisesRegex(ShadowVerificationError,"missing-data availability|unavailable result fabricated"):
                verify_generation(root)

    def test_code_drift_requires_explicit_development_mode_and_incomplete_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=self.make_generation(temporary)
            (Path(temporary)/"code.py").write_text("# newer code")
            with self.assertRaisesRegex(ShadowVerificationError,"code hash mismatch"):
                verify_generation(root)
            audit=verify_generation(root,allow_code_drift=True)
            self.assertEqual(audit["status"],"passed_with_development_warnings")
            self.assertIn("code drift",audit["warnings"][0])
            generation=json.loads((root/"shadow_generation.json").read_text())
            generation["status"]="building"
            write_json(root/"shadow_generation.json",generation)
            with self.assertRaisesRegex(ShadowVerificationError,"not complete"):
                verify_generation(root,allow_code_drift=True)


if __name__=="__main__":
    unittest.main()
