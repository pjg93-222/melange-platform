from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

import audit.audit_postrefresh_behavior_stance_semantics as audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def member_values(delta: float = 0.0) -> dict:
    return {
        "Member A": {"metric": 1.0 + delta, "label": "same", "nullable": None},
        "Member B": {"metric": 2.0, "label": "same", "nullable": None},
    }


def stance_values(delta: float = 0.0) -> dict:
    members = ("Member A", "Member B")
    cats = ["Category A", "Category B"]
    return {
        "cats": cats,
        "defensive": ["Category A"],
        "matrix": {
            member: {"Category A": "Ally", "Category B": "—"}
            for member in members
        },
        "scores": {
            member: {
                "Category A": 0.5 + (delta if member == "Member A" else 0),
                "Category B": 0.0,
            }
            for member in members
        },
        "raw_scores": {
            member: {
                "Category A": 0.4 + (delta if member == "Member A" else 0),
                "Category B": 0.0,
            }
            for member in members
        },
        "rounding": {
            "rule": {"cap": 0.05},
            "factors": {
                member: {
                    "Category A": 0.01,
                    "Category B": 0.0,
                }
                for member in members
            },
        },
        "thresholds": {"ally": 0.4},
        "media": {},
        "gate": {},
    }


def build_drift_binding(root: Path, audit_id: str = "test-drift") -> dict:
    frozen_root = root / "frozen"
    live_root = root / "live"
    frozen_root.mkdir()
    live_root.mkdir()
    values = {}
    for name in audit.MEMBER_ARTIFACTS:
        values[name] = (
            {"members": member_values()},
            {"members": member_values(0.1)},
        )
    values["category_stance.json"] = (
        stance_values(),
        stance_values(0.1),
    )
    for name, (frozen, live) in values.items():
        write_json(frozen_root / name, frozen)
        write_json(live_root / name, live)
    paths = {
        "root": root,
        "frozen": frozen_root,
        "live": live_root,
        "audit_id": audit_id,
    }
    refresh_drift_binding(paths)
    return paths


def refresh_drift_binding(paths: dict) -> None:
    root = paths["root"]
    rows = []
    for name in audit.TARGET_NAMES:
        frozen = paths["frozen"] / name
        live = paths["live"] / name
        rows.append(
            {
                "schema": "post-weekly-refresh-downstream-file-drift-v1",
                "name": name,
                "changed": True,
                "frozen_path": str(frozen),
                "live_path": str(live),
                "frozen_bytes": frozen.stat().st_size,
                "live_bytes": live.stat().st_size,
                "frozen_sha256": audit.sha256_file(frozen),
                "live_sha256": audit.sha256_file(live),
                "declared_stage": f"test_{name}",
                "accepted_legislation_recomputation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
            }
        )
    for index in range(51):
        rows.append(
            {
                "name": f"stable_{index:02d}.json",
                "changed": False,
            }
        )
    ledger = root / "file_drift.ndjson"
    ledger.write_bytes(
        b"".join(audit.canonical_bytes(row) for row in rows)
    )
    manifest = root / "drift_manifest.json"
    write_json(
        manifest,
        {
            "audit_id": paths["audit_id"],
            "generated_at": "2026-07-26T00:00:00+00:00",
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "outputs": {
                "file_drift": audit.file_item(ledger, record_count=55)
            },
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "legislation_recomputation_accepted": False,
            "production_or_mainframe_written": False,
            "sync_or_promotion_performed": False,
        },
    )
    pointer = root / "drift_current.json"
    write_json(
        pointer,
        {
            "schema": "post-weekly-refresh-downstream-drift-pointer-v1",
            "audit_id": paths["audit_id"],
            "manifest": audit.file_item(manifest),
            "authoritative_for_legislation": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "sync_or_promotion_performed": False,
        },
    )
    paths["pointer"] = pointer


def run_build(paths: dict, out_root: Path):
    return audit.build_audit(
        drift_pointer_path=paths["pointer"],
        out_root=out_root,
        expected_drift_audit_id=paths["audit_id"],
        expected_member_count=2,
        expected_category_count=2,
    )


class BehaviorStanceSemanticsTest(unittest.TestCase):
    def test_complete_nonauthoritative_and_idempotent(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            out = Path(temp) / "out"
            snapshot, manifest, created = run_build(paths, out)
            self.assertTrue(created)
            self.assertEqual(manifest["summary"]["artifact_count"], 4)
            self.assertEqual(manifest["summary"]["member_count"], 2)
            self.assertEqual(manifest["summary"]["category_count"], 2)
            self.assertTrue(manifest["summary"]["identity_closure_passed"])
            self.assertFalse(manifest["legislation_recomputation_accepted"])
            self.assertFalse(manifest["authoritative_for_scoring"])
            self.assertFalse(manifest["sync_or_promotion_performed"])
            evidence = audit.validate_evidence(
                out / "current.json",
                expected_drift_audit_id=paths["audit_id"],
                expected_member_count=2,
                expected_category_count=2,
            )
            self.assertEqual(evidence["status"], "CURRENT_INFORMATIONAL")
            self.assertTrue(evidence["authority_fields_all_false"])
            self.assertTrue(
                evidence["numeric_grouping_path_method_verified"]
            )
            self.assertEqual(
                evidence["member_identity_closure"]["count"], 2
            )
            pointer = out / "current.json"
            first_body = pointer.read_bytes()
            first_mtime = pointer.stat().st_mtime_ns
            time.sleep(0.02)
            snapshot_2, manifest_2, created_2 = run_build(paths, out)
            self.assertFalse(created_2)
            self.assertEqual(snapshot_2, snapshot)
            self.assertEqual(manifest_2["audit_id"], manifest["audit_id"])
            self.assertEqual(pointer.read_bytes(), first_body)
            self.assertEqual(pointer.stat().st_mtime_ns, first_mtime)
            self.assertEqual(manifest_2, manifest)

    def test_live_hash_movement_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            target = paths["live"] / "behavior_panel.json"
            value = json.loads(target.read_text(encoding="utf-8"))
            value["members"]["Member A"]["metric"] = 99
            write_json(target, value)
            with self.assertRaisesRegex(
                audit.AuditError, "Live target hash/size moved"
            ):
                run_build(paths, Path(temp) / "out")

    def test_member_identity_key_drift_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            target = paths["live"] / "behavioral_dispositions.json"
            value = json.loads(target.read_text(encoding="utf-8"))
            value["members"]["Different Member"] = value["members"].pop(
                "Member B"
            )
            write_json(target, value)
            refresh_drift_binding(paths)
            with self.assertRaisesRegex(
                audit.AuditError, "member identity key closure failed"
            ):
                run_build(paths, Path(temp) / "out")

    def test_category_cell_key_drift_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            target = paths["live"] / "category_stance.json"
            value = json.loads(target.read_text(encoding="utf-8"))
            del value["scores"]["Member A"]["Category B"]
            write_json(target, value)
            refresh_drift_binding(paths)
            with self.assertRaisesRegex(
                audit.AuditError, "category key closure failed"
            ):
                run_build(paths, Path(temp) / "out")

    def test_existing_manifest_authority_tamper_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            out = Path(temp) / "out"
            snapshot, _, _ = run_build(paths, out)
            manifest_path = snapshot / "manifest.json"
            value = json.loads(manifest_path.read_text(encoding="utf-8"))
            value["authoritative_for_scoring"] = True
            write_json(manifest_path, value)
            with self.assertRaisesRegex(
                audit.AuditError,
                "manifest semantic drift|manifest receipt drifted",
            ):
                run_build(paths, out)

    def test_stance_numeric_path_must_remain_numeric(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            target = paths["live"] / "category_stance.json"
            value = json.loads(target.read_text(encoding="utf-8"))
            value["scores"]["Member A"]["Category A"] = "0.6"
            write_json(target, value)
            refresh_drift_binding(paths)
            with self.assertRaisesRegex(
                audit.AuditError, "Stance numeric path is nonnumeric"
            ):
                run_build(paths, Path(temp) / "out")

    def test_added_and_nonnumeric_paths_are_complete_and_visible(self) -> None:
        with TemporaryDirectory() as temp:
            paths = build_drift_binding(Path(temp))
            target = paths["live"] / "behavior_panel.json"
            value = json.loads(target.read_text(encoding="utf-8"))
            value["members"]["Member A"]["new_metric"] = 3.0
            value["members"]["Member B"]["label"] = "changed"
            write_json(target, value)
            refresh_drift_binding(paths)
            snapshot, _, _ = run_build(paths, Path(temp) / "out")
            rows = audit.read_ndjson(
                snapshot / "artifact_semantics.ndjson"
            )
            row = next(
                item
                for item in rows
                if item["artifact"] == "behavior_panel.json"
            )
            summary = row["recursive_change_summary"]
            self.assertEqual(summary["added_path_count"], 1)
            self.assertEqual(summary["nonnumeric_changed_count"], 1)
            self.assertTrue(summary["change_sample_complete"])
            self.assertEqual(
                {item["path"] for item in summary["change_sample"]},
                {
                    "members.Member A.new_metric",
                    "members.Member B.label",
                },
            )


if __name__ == "__main__":
    unittest.main()
