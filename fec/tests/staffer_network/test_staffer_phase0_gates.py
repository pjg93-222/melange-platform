import json
from pathlib import Path
import sys
import tempfile
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.phase0_gates import atomicity_receipt, isolation_receipt
from staffer_network.privacy_audit import remediate_synced_staff_contacts
from staffer_network.publication import GenerationError, _validate_record_semantics, schema_path
from staffer_network.schema_validation import validate_instance


class StafferPhase0GateTests(unittest.TestCase):
    def test_committed_gate_receipts_and_registry_are_valid(self):
        root = FEC.parent
        paths = [
            root / "data/staffer_network/receipts/isolation_20260829.json",
            root / "data/staffer_network/receipts/atomicity_20260829.json",
            root / "data/staffer_network/receipts/privacy_license_20260829.json",
            root / "data/staffer_network/receipts/phase0_coverage_20260829.json",
            root / "data/staffer_network/policy/legacy_artifact_registry_20260829.json",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                value = json.loads(path.read_text(encoding="utf-8"))
                schema = json.loads(
                    schema_path(FEC / "staffer_network/schemas", value["schema_id"])
                    .read_text(encoding="utf-8")
                )
                validate_instance(value, schema)
                _validate_record_semantics(value, value["schema_id"])

    def test_atomicity_packet_is_reproducible_and_nonvacuous(self):
        receipt = atomicity_receipt(
            FEC / "staffer_network/schema.sql", generated_at="2026-08-30T03:05:00Z"
        )
        self.assertEqual(receipt["status"], "PASS")
        self.assertEqual(receipt["logical_table_count"], 17)
        self.assertEqual(receipt["nonempty_table_count"], 17)
        self.assertEqual(receipt["reader_generation_observations"], ["g1", "g3"])
        broken = dict(receipt)
        broken["incremental_build_digest"] = "f" * 64
        with self.assertRaisesRegex(GenerationError, "digests differ"):
            _validate_record_semantics(broken, broken["schema_id"])

    def test_isolation_packet_finds_an_unexpected_reader(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "fec/network").mkdir(parents=True)
            (root / "fec/network/staffer_network_loader.py").write_text(
                "STAFF_NET = 'off'\n", encoding="utf-8"
            )
            (root / "protected.txt").write_text("unchanged", encoding="utf-8")
            passed = isolation_receipt(
                root,
                generated_at="2026-08-30T03:05:00Z",
                protected_outputs=["protected.txt"],
            )
            self.assertEqual(passed["status"], "PASS")
            self.assertEqual(passed["off_open_attempts"], 0)
            (root / "fec/unexpected.py").write_text(
                "from staffer_network import publication\n", encoding="utf-8"
            )
            failed = isolation_receipt(
                root,
                generated_at="2026-08-30T03:05:00Z",
                protected_outputs=["protected.txt"],
            )
            self.assertEqual(failed["status"], "FAIL")
            self.assertEqual(failed["unexpected_reader_paths"], ["fec/unexpected.py"])

    def test_privacy_remediator_strips_only_staff_contact_keys(self):
        staff = {"Member": [{"id": "1", "n": "A", "t": "Chief", "e": "private@example.test", "p": "202-555-0188", "star": 1}]}
        member = {"name": "Member", "staffdir": staff["Member"], "score": 1}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data/members").mkdir(parents=True)
            (root / "data/members/staff_directory.json").write_text(json.dumps(staff), encoding="utf-8")
            (root / "member_desk_bundle.json").write_text(json.dumps({"members": [member]}), encoding="utf-8")
            (root / "cu-member-desk-profiles.js").write_text(
                "window.__melangeProfileMerge(" + json.dumps({"members_heavy": [member]}) + ");",
                encoding="utf-8",
            )
            receipt = remediate_synced_staff_contacts(root)
            self.assertEqual(receipt["directory_contact_keys_removed"], 2)
            clean = json.loads((root / "data/members/staff_directory.json").read_text(encoding="utf-8"))
            self.assertEqual(clean, {"Member": [{"id": "1", "n": "A", "t": "Chief", "star": 1}]})

    def test_phase0_pass_receipts_reject_planted_failures(self):
        root = FEC.parent / "data/staffer_network/receipts"
        mutations = [
            ("isolation_20260829.json", lambda value: value.update({"off_open_attempts": 1})),
            ("atomicity_20260829.json", lambda value: value.update({"mixed_generation_observations": 1})),
            ("privacy_license_20260829.json", lambda value: value.update({"contact_pattern_rows": 1})),
        ]
        for name, mutate in mutations:
            with self.subTest(name=name):
                value = json.loads((root / name).read_text(encoding="utf-8"))
                mutate(value)
                with self.assertRaises(GenerationError):
                    _validate_record_semantics(value, value["schema_id"])


if __name__ == "__main__":
    unittest.main()
