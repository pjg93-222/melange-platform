import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "fec"))

import audit.audit_quorum_recall_floor as audit


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def write_ndjson(path: Path, values: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(value) + "\n" for value in values),
        encoding="utf-8",
    )


def fixture_chain(tmp_path: Path) -> tuple[Path, Path]:
    workbook = tmp_path / "floor.xlsx"
    workbook.write_bytes(b"frozen-floor")
    identifiers = ["119:hr:1", "119:s:2"]
    identifier_hash = hashlib.sha256(
        ("\n".join(identifiers) + "\n").encode()
    ).hexdigest()
    floor = tmp_path / "floor.json"
    write_json(
        floor,
        {
            "schema": "acu-legislation-quorum-recall-floor-v1",
            "status": "FROZEN_FLOOR_NOT_PRIMARY",
            "collection_policy": {
                "primary_instrument": audit.PRIMARY_INSTRUMENT,
                "quorum_used_as_primary": False,
                "quorum_used_as_recall_floor": True,
                "floor_is_proof_of_completeness": False,
                "scoring_or_promotion_allowed": False,
            },
            "source_workbook": {
                "path": str(workbook),
                "bytes": workbook.stat().st_size,
                "sha256": audit.sha256_file(workbook),
            },
            "counts": {
                "source_rows": 2,
                "unique_identifiers": 2,
                "duplicate_identifiers": 0,
            },
            "identifier_set_sha256": identifier_hash,
            "unique_identifiers": identifiers,
            "rows": [
                {"identifier": "119:hr:1"},
                {"identifier": "119:s:2"},
            ],
        },
    )
    primary = tmp_path / "primary"
    write_ndjson(
        primary / "congress_advanced_records.ndjson",
        [
            {
                "canonical_key": "119:hr:1",
                "congress": 119,
                "type": "hr",
                "number": 1,
            }
        ],
    )
    write_ndjson(
        primary / "exclusion_ledger.ndjson",
        [{"canonical_key": "119:s:2", "reason": "explicit fixture exclusion"}],
    )
    write_json(
        primary / "manifest.json",
        {
            "schema": "acu-congress-advanced-pages-manifest-v1",
            "status": "complete",
            "primary_instrument": audit.PRIMARY_INSTRUMENT,
            "quorum_used": False,
            "union": {"unique_record_count": 1},
        },
    )
    return floor, primary


class QuorumRecallFloorTests(unittest.TestCase):
    def test_floor_passes_with_primary_or_explicit_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            floor, primary = fixture_chain(Path(directory))
            receipt = audit.audit_floor(floor, primary)
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(
                receipt["counts"],
                {
                    "floor_source_rows": 2,
                    "floor_unique_identifiers": 2,
                    "primary_unique_records": 1,
                    "present_in_primary_union": 1,
                    "explicitly_excluded": 1,
                    "unresolved": 0,
                },
            )
            self.assertIs(receipt["policy"]["quorum_used_as_primary"], False)
            self.assertIs(
                receipt["policy"]["scoring_sync_promotion_authorized"], False
            )

    def test_floor_fails_closed_on_unresolved_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            floor, primary = fixture_chain(Path(directory))
            (primary / "exclusion_ledger.ndjson").write_text(
                "", encoding="utf-8"
            )
            with self.assertRaisesRegex(audit.FloorAuditError, "119:s:2"):
                audit.audit_floor(floor, primary)

    def test_floor_fails_on_workbook_receipt_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            floor, primary = fixture_chain(Path(directory))
            floor_document = json.loads(floor.read_text(encoding="utf-8"))
            Path(floor_document["source_workbook"]["path"]).write_bytes(
                b"changed"
            )
            with self.assertRaisesRegex(
                audit.FloorAuditError, "workbook receipt drift"
            ):
                audit.audit_floor(floor, primary)
