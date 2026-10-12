from __future__ import annotations

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

import audit.audit_postrefresh_rank_drift as audit


def rank_value() -> dict:
    names = [f"Member {index:03d}" for index in range(1, 534)]
    return {
        "rank": {name: index for index, name in enumerate(names, 1)},
        "score": {
            name: 1.0 - (index / 1000.0)
            for index, name in enumerate(names, 1)
        },
        "provenance": "test fixture",
    }


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


class PostRefreshRankDriftTest(unittest.TestCase):
    def test_build_is_complete_nonauthoritative_and_idempotent(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            frozen = rank_value()
            live = json.loads(json.dumps(frozen))
            first = "Member 001"
            second = "Member 002"
            live["rank"][first], live["rank"][second] = (
                live["rank"][second],
                live["rank"][first],
            )
            live["score"][first] += 0.01
            frozen_ranks = root / "frozen.json"
            live_ranks = root / "live.json"
            frozen_book = root / "frozen.xlsx"
            live_book = root / "live.xlsx"
            write_json(frozen_ranks, frozen)
            write_json(live_ranks, live)
            frozen_book.write_bytes(b"frozen")
            live_book.write_bytes(b"live")
            out_root = root / "out"

            snapshot, manifest, created = audit.build_audit(
                baseline_ranks_path=frozen_ranks,
                live_ranks_path=live_ranks,
                baseline_workbook_path=frozen_book,
                live_workbook_path=live_book,
                out_root=out_root,
            )
            self.assertTrue(created)
            self.assertEqual(manifest["summary"]["member_count"], 533)
            self.assertEqual(
                manifest["summary"]["rank_changed_count"],
                2,
            )
            self.assertEqual(
                manifest["summary"]["score_changed_count"],
                1,
            )
            self.assertFalse(
                manifest["authoritative_for_legislation_ranks"]
            )
            self.assertEqual(
                manifest["producer"]["sha256"],
                audit.sha256_file(Path(audit.__file__).resolve()),
            )
            self.assertFalse(manifest["authoritative_for_scoring"])
            self.assertFalse(manifest["sync_or_promotion_performed"])
            rows = (
                snapshot / "member_rank_drift.ndjson"
            ).read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 533)
            self.assertTrue(
                all(
                    not json.loads(row)["legislation_recomputation_accepted"]
                    for row in rows
                )
            )

            snapshot_again, manifest_again, created_again = audit.build_audit(
                baseline_ranks_path=frozen_ranks,
                live_ranks_path=live_ranks,
                baseline_workbook_path=frozen_book,
                live_workbook_path=live_book,
                out_root=out_root,
            )
            self.assertFalse(created_again)
            self.assertEqual(snapshot_again, snapshot)
            self.assertEqual(
                manifest_again["audit_id"],
                manifest["audit_id"],
            )

    def test_member_set_drift_fails_closed(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            frozen = rank_value()
            live = rank_value()
            live["rank"]["Different Member"] = live["rank"].pop(
                "Member 533"
            )
            live["score"]["Different Member"] = live["score"].pop(
                "Member 533"
            )
            frozen_ranks = root / "frozen.json"
            live_ranks = root / "live.json"
            frozen_book = root / "frozen.xlsx"
            live_book = root / "live.xlsx"
            write_json(frozen_ranks, frozen)
            write_json(live_ranks, live)
            frozen_book.write_bytes(b"frozen")
            live_book.write_bytes(b"live")
            with self.assertRaisesRegex(
                audit.AuditError,
                "member set mismatch",
            ):
                audit.build_audit(
                    baseline_ranks_path=frozen_ranks,
                    live_ranks_path=live_ranks,
                    baseline_workbook_path=frozen_book,
                    live_workbook_path=live_book,
                    out_root=root / "out",
                )


if __name__ == "__main__":
    unittest.main()
