from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from audit.drift_closure import (
    CHANGED_COUNT,
    EXPECTED_CHANGED_NAMES,
    TOTAL,
    UNCHANGED_COUNT,
)
import audit.audit_postrefresh_downstream_drift as audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def fixture(root: Path) -> dict[str, Path]:
    live_root = root / "live"
    frozen_root = root / "frozen"
    live_root.mkdir()
    frozen_root.mkdir()
    names = list(sorted(EXPECTED_CHANGED_NAMES))
    names.extend(
        f"stable_{index:02d}.json" for index in range(UNCHANGED_COUNT)
    )
    files = []
    for index, name in enumerate(names):
        frozen = frozen_root / name
        # The manifest records the root-level identity; the live bytes sit where the
        # 2026-08-05 relocation map (audit/live_relocations.py) reads them, as in production.
        recorded = live_root / name
        live = audit.resolve_live_source(recorded, live_root)
        frozen.parent.mkdir(parents=True, exist_ok=True)
        live.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".json"):
            write_json(frozen, {"index": index, "value": "frozen"})
            write_json(
                live,
                {
                    "index": index,
                    "value": (
                        "live"
                        if name in EXPECTED_CHANGED_NAMES
                        else "frozen"
                    ),
                },
            )
        elif name.endswith(".html"):
            frozen.write_text("<p>frozen</p>\n", encoding="utf-8")
            live.write_text("<p>live</p>\n", encoding="utf-8")
        else:
            frozen.write_bytes(b"frozen-workbook")
            live.write_bytes(b"live-workbook")
        files.append(
            {
                "source": str(recorded),
                "copy": str(frozen),
                "size": frozen.stat().st_size,
                "sha256": audit.sha256_file(frozen),
            }
        )
    baseline = root / "baseline.json"
    write_json(
        baseline,
        {"schema": "test", "file_count": TOTAL, "files": files},
    )
    checked_files = []
    for index, item in enumerate(files):
        source = Path(item["source"])
        copy = Path(item["copy"])
        source_hash = audit.sha256_file(audit.resolve_live_source(source, live_root))
        copy_hash = audit.sha256_file(copy)
        checked_files.append(
            {
                "index": index,
                "source": source.relative_to(live_root).as_posix(),
                "copy": str(copy.resolve()),
                "expected_sha256": item["sha256"],
                "source_sha256": source_hash,
                "copy_sha256": copy_hash,
                "passed": source_hash == copy_hash,
            }
        )
    decay = root / "decay.json"
    write_json(
        decay,
        {
            "production_baseline": {
                "passed": True,
                "all_live_sources_and_frozen_copies_match_manifest": False,
                "declared_file_count": TOTAL,
                "source_and_copy_hash_match_count": UNCHANGED_COUNT,
                "manifest": {
                    "path": str(baseline.resolve()),
                    "size": baseline.stat().st_size,
                    "sha256": audit.sha256_file(baseline),
                },
                "checked_files": checked_files,
                "source_and_copy_mismatches": [
                    row for row in checked_files if not row["passed"]
                ],
            }
        },
    )
    rank_manifest_path = root / "rank_snapshot" / "manifest.json"
    write_json(
        rank_manifest_path,
        {
            "schema": "post-weekly-refresh-rank-drift-manifest-v1",
            "audit_id": "rank-audit",
            "status": "PASS_OBSERVATIONAL_NONAUTHORITATIVE",
            "authoritative_for_legislation_ranks": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "legislation_recomputation_accepted": False,
            "legislation_rank_cutover_performed": False,
            "production_write_performed": False,
            "sync_or_promotion_performed": False,
            "network_executed": False,
        },
    )
    rank_path = root / "rank.json"
    write_json(
        rank_path,
        {
            "schema": "post-weekly-refresh-rank-drift-pointer-v1",
            "audit_id": "rank-audit",
            "manifest": audit.file_item(rank_manifest_path),
            "authoritative_for_legislation_ranks": False,
            "authoritative_for_scoring": False,
            "authoritative_for_sync_or_promotion": False,
            "sync_or_promotion_performed": False,
        },
    )
    receipts = {"rank": rank_path}
    for name in ("layout", "model"):
        path = root / f"{name}.json"
        write_json(path, {"receipt": name})
        receipts[name] = path
    return {
        "live_root": live_root,
        "frozen_root": frozen_root,
        "baseline": baseline,
        "decay": decay,
        **receipts,
    }


def run_build(paths: dict[str, Path], out_root: Path):
    return audit.build_audit(
        root=paths["live_root"],
        baseline_manifest_path=paths["baseline"],
        decay_audit_path=paths["decay"],
        rank_pointer_path=paths["rank"],
        layout_receipt_path=paths["layout"],
        model_receipt_path=paths["model"],
        out_root=out_root,
    )


class PostRefreshDownstreamDriftTest(unittest.TestCase):
    def test_complete_nonauthoritative_and_idempotent(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            out = Path(temp) / "out"
            snapshot, manifest, created = run_build(paths, out)
            self.assertTrue(created)
            self.assertEqual(
                manifest["summary"]["declared_file_count"], TOTAL
            )
            self.assertEqual(
                manifest["summary"]["changed_file_count"], CHANGED_COUNT
            )
            self.assertEqual(
                manifest["summary"]["unchanged_file_count"],
                UNCHANGED_COUNT,
            )
            self.assertEqual(
                manifest["summary"][
                    "changed_without_existing_domain_semantic_receipt_count"
                ],
                CHANGED_COUNT - 1,
            )
            self.assertFalse(manifest["legislation_recomputation_accepted"])
            self.assertFalse(manifest["authoritative_for_scoring"])
            self.assertFalse(manifest["sync_or_promotion_performed"])
            self.assertEqual(
                len(
                    (snapshot / "file_drift.ndjson")
                    .read_text(encoding="utf-8")
                    .splitlines()
                ),
                TOTAL,
            )
            snapshot_2, manifest_2, created_2 = run_build(paths, out)
            self.assertFalse(created_2)
            self.assertEqual(snapshot_2, snapshot)
            self.assertEqual(manifest_2["audit_id"], manifest["audit_id"])
            pointer = out / "current.json"
            pointer_mtime = pointer.stat().st_mtime_ns
            pointer_body = pointer.read_bytes()
            snapshot_3, manifest_3, created_3 = run_build(paths, out)
            self.assertFalse(created_3)
            self.assertEqual(snapshot_3, snapshot)
            self.assertEqual(manifest_3["audit_id"], manifest["audit_id"])
            self.assertEqual(pointer.read_bytes(), pointer_body)
            self.assertEqual(pointer.stat().st_mtime_ns, pointer_mtime)

    def test_live_drift_after_decay_receipt_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            target = paths["live_root"] / "behavior_panel.json"
            write_json(target, {"value": "later-live-drift"})
            with self.assertRaisesRegex(
                audit.AuditError,
                "Decay checked-file receipt drift: behavior_panel.json",
            ):
                run_build(paths, Path(temp) / "out")

    def test_tampered_decay_checked_file_hash_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            decay = json.loads(paths["decay"].read_text(encoding="utf-8"))
            target = next(
                row
                for row in decay["production_baseline"]["checked_files"]
                if row["source"] == "behavior_panel.json"
            )
            target["copy_sha256"] = "0" * 64
            write_json(paths["decay"], decay)
            with self.assertRaisesRegex(
                audit.AuditError,
                "Decay checked-file receipt drift: behavior_panel.json",
            ):
                run_build(paths, Path(temp) / "out")

    def test_existing_manifest_authority_tampering_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            out = Path(temp) / "out"
            snapshot, _, _ = run_build(paths, out)
            manifest = json.loads(
                (snapshot / "manifest.json").read_text(encoding="utf-8")
            )
            manifest["authoritative_for_scoring"] = True
            write_json(snapshot / "manifest.json", manifest)
            with self.assertRaisesRegex(
                audit.AuditError,
                "Existing downstream-drift manifest contract drift",
            ):
                run_build(paths, out)

    def test_existing_current_pointer_tampering_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            out = Path(temp) / "out"
            run_build(paths, out)
            pointer = json.loads(
                (out / "current.json").read_text(encoding="utf-8")
            )
            pointer["authoritative_for_scoring"] = True
            write_json(out / "current.json", pointer)
            with self.assertRaisesRegex(
                audit.AuditError,
                "Existing downstream-drift current pointer drift",
            ):
                run_build(paths, out)

    def test_frozen_hash_tampering_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            target = (
                paths["frozen_root"]
                / "Moneyball Main Version - Blended.xlsx"
            )
            target.write_bytes(b"tampered")
            with self.assertRaisesRegex(
                audit.AuditError, "Frozen hash tampering"
            ):
                run_build(paths, Path(temp) / "out")

    def test_missing_changed_file_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            (
                paths["live_root"] / "behavior_panel.json"
            ).unlink()
            with self.assertRaisesRegex(audit.AuditError, "Missing live file"):
                run_build(paths, Path(temp) / "out")

    def test_incomplete_closure_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            paths = fixture(Path(temp))
            manifest = json.loads(
                paths["baseline"].read_text(encoding="utf-8")
            )
            manifest["files"].pop()
            manifest["file_count"] = TOTAL - 1
            write_json(paths["baseline"], manifest)
            with self.assertRaisesRegex(
                audit.AuditError, "Incomplete baseline file closure"
            ):
                run_build(paths, Path(temp) / "out")


if __name__ == "__main__":
    unittest.main()
