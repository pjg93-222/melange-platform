from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

import audit.audit_postrefresh_planning_dashboard_semantics as audit


def _canonical(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item(path: Path) -> dict:
    return {
        "path": str(path.resolve()),
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
    }


def _json_values(name: str, changed: bool) -> tuple[object, object]:
    delta = 2 if changed else 1
    values = {
        "electoral_plays.json": (
            {"A": {"value": 1, "play": "none"}},
            {"A": {"value": delta, "play": "none"}},
        ),
        "per_topic_alloc.json": (
            {"Topic": {"A": 1}},
            {"Topic": {"A": delta}},
        ),
        "per_topic_coalition.json": (
            {"Topic": {"A": 1}},
            {"Topic": {"A": delta}},
        ),
        "budget_frontier.json": (
            {
                "topics": {"Topic": {"path": [[0, 1]]}},
                "defend_overview": [{"m": "A", "v": 1}],
            },
            {
                "topics": {"Topic": {"path": [[0, delta]]}},
                "defend_overview": [{"m": "A", "v": delta}],
            },
        ),
        "budget_frontier_electoral.json": (
            {
                "topics": {"Topic": {"path": [[0, 1]]}},
                "defend_overview": [{"m": "A", "v": 1}],
            },
            {
                "topics": {"Topic": {"path": [[0, delta]]}},
                "defend_overview": [{"m": "A", "v": delta}],
            },
        ),
        "optimize_result.json": (
            {
                "issues": ["Topic"],
                "alloc": {"A": 1},
                "path": [[0, 1]],
                "selected_marginals": [1],
            },
            {
                "issues": ["Topic"],
                "alloc": {"A": delta},
                "path": [[0, delta]],
                "selected_marginals": [delta],
            },
        ),
        "im_allocation.json": (
            {"flip": {"A": 1}, "coalition": {"A": 1}},
            {"flip": {"A": delta}, "coalition": {"A": delta}},
        ),
        "salience_advisory.json": (
            {"advisory": [{"issue": "Topic", "value": 1}]},
            {"advisory": [{"issue": "Topic", "value": delta}]},
        ),
        "dashboard_data.json": (
            {
                "issues": ["Topic"],
                "name": ["A"],
                "x": [1], "y": [1], "rank": [1], "nl": [1], "bp": [1],
                "cf": [1], "stance": [1], "epval": [1], "cx": [1],
                "cy": [1], "cmi": [1], "recept": [1], "antag": [1],
                "grand": [1], "party": "R", "gen": "A", "play": "n",
                "elay": "m", "flip": [1], "coal": [1], "stats": [1],
                "frontier": [1], "edges": [], "refs": [], "coap": [],
            },
            {
                "issues": ["Topic"],
                "name": ["A"],
                "x": [delta], "y": [1], "rank": [1], "nl": [1], "bp": [1],
                "cf": [1], "stance": [1], "epval": [1], "cx": [1],
                "cy": [1], "cmi": [1], "recept": [1], "antag": [1],
                "grand": [1], "party": "R", "gen": "A", "play": "n",
                "elay": "m", "flip": [1], "coal": [1], "stats": [1],
                "frontier": [1], "edges": [], "refs": [], "coap": [],
            },
        ),
        "member_desk_bundle.json": (
            {"cats": ["Topic"], "members": [{"name": "A", "score": 1}], "adj": []},
            {
                "cats": ["Topic"],
                "members": [{"name": "A", "score": delta}],
                "adj": [],
            },
        ),
        "dashboard_exclusion_reconciliation.json": (
            {"sources": {"press": {"source_rows": 1}}, "exclusion_samples": {}},
            {
                "sources": {"press": {"source_rows": delta}},
                "exclusion_samples": {},
            },
        ),
    }
    return values[name]


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, dict]]:
    frozen_dir = tmp_path / "frozen"
    live_dir = tmp_path / "live"
    drift_dir = tmp_path / "drift"
    frozen_dir.mkdir()
    live_dir.mkdir()
    drift_dir.mkdir()
    loaded: dict[str, tuple[object, object]] = {}
    rows = []
    for name in audit.JSON_TARGETS:
        old, new = _json_values(name, True)
        loaded[name] = (old, new)
        frozen = frozen_dir / name
        live = live_dir / name
        frozen.write_bytes(_canonical(old))
        live.write_bytes(_canonical(new))
        rows.append(
            {
                "schema": "post-weekly-refresh-downstream-file-drift-v1",
                "name": name,
                "changed": True,
                "byte_identical": False,
                "frozen_path": str(frozen.resolve()),
                "frozen_bytes": frozen.stat().st_size,
                "frozen_sha256": _sha(frozen),
                "live_path": str(live.resolve()),
                "live_bytes": live.stat().st_size,
                "live_sha256": _sha(live),
                "accepted_legislation_recomputation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
            }
        )
    html_payloads = {
        "Moneyball Dashboard.html": lambda side: (
            "<html><script>const DATA="
            + json.dumps(loaded["dashboard_data.json"][side])
            + ";</script></html>"
        ),
        "Moneyball.html": lambda side: (
            "<html><script>const DATA="
            + json.dumps(loaded["dashboard_data.json"][side])
            + "; const D="
            + json.dumps(loaded["member_desk_bundle.json"][side])
            + ";</script></html>"
        ),
        "cu-member-desk.html": lambda side: (
            "<html><script>const D="
            + json.dumps(loaded["member_desk_bundle.json"][side])
            + ";</script></html>"
        ),
    }
    for name, render in html_payloads.items():
        frozen = frozen_dir / name
        live = live_dir / name
        frozen.write_text(render(0), encoding="utf-8")
        live.write_text(render(1), encoding="utf-8")
        rows.append(
            {
                "schema": "post-weekly-refresh-downstream-file-drift-v1",
                "name": name,
                "changed": True,
                "byte_identical": False,
                "frozen_path": str(frozen.resolve()),
                "frozen_bytes": frozen.stat().st_size,
                "frozen_sha256": _sha(frozen),
                "live_path": str(live.resolve()),
                "live_bytes": live.stat().st_size,
                "live_sha256": _sha(live),
                "accepted_legislation_recomputation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
            }
        )
    for index in range(55 - len(rows)):
        name = f"unchanged-{index}.json"
        frozen = frozen_dir / name
        live = live_dir / name
        frozen.write_text("{}", encoding="utf-8")
        live.write_text("{}", encoding="utf-8")
        rows.append(
            {
                "name": name,
                "changed": False,
                "byte_identical": True,
                "frozen_path": str(frozen.resolve()),
                "frozen_bytes": frozen.stat().st_size,
                "frozen_sha256": _sha(frozen),
                "live_path": str(live.resolve()),
                "live_bytes": live.stat().st_size,
                "live_sha256": _sha(live),
                "accepted_legislation_recomputation": False,
                "authoritative_for_scoring": False,
                "authoritative_for_sync_or_promotion": False,
            }
        )
    ledger = drift_dir / "file_drift.ndjson"
    ledger.write_bytes(b"".join(_canonical(row) for row in rows))
    manifest = {
        "schema": "post-weekly-refresh-downstream-drift-manifest-v1",
        "audit_id": "d" * 64,
        "outputs": {
            "file_drift": {**_item(ledger), "record_count": 55},
        },
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "network_executed": False,
        "production_or_mainframe_written": False,
        "sync_or_promotion_performed": False,
    }
    manifest_path = drift_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    pointer = {
        "schema": "post-weekly-refresh-downstream-drift-pointer-v1",
        "audit_id": "d" * 64,
        "manifest": _item(manifest_path),
        "authoritative_for_legislation": False,
        "authoritative_for_scoring": False,
        "authoritative_for_sync_or_promotion": False,
        "sync_or_promotion_performed": False,
    }
    pointer_path = drift_dir / "current.json"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")
    return pointer_path, tmp_path / "out", {row["name"]: row for row in rows}


class PlanningDashboardSemanticAuditTests(unittest.TestCase):
    def test_complete_non_authoritative_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer, out, _ = _fixture(Path(temporary))
            current, manifest, created = audit.build_audit(
                drift_pointer_path=pointer, out_root=out
            )
            self.assertTrue(created)
            self.assertEqual(manifest["summary"]["target_count"], 14)
            self.assertEqual(manifest["summary"]["html_payload_count"], 4)
            self.assertEqual(
                manifest["summary"][
                    "html_payload_external_identity_proven_count"
                ],
                4,
            )
            self.assertFalse(
                manifest["authoritative_for_sync_or_promotion"]
            )
            before_hash = _sha(current)
            before_mtime = current.stat().st_mtime_ns
            time.sleep(0.01)
            current2, manifest2, created2 = audit.build_audit(
                drift_pointer_path=pointer, out_root=out
            )
            self.assertFalse(created2)
            self.assertEqual(manifest2["audit_id"], manifest["audit_id"])
            self.assertEqual(_sha(current2), before_hash)
            self.assertEqual(current2.stat().st_mtime_ns, before_mtime)

    def test_rejects_live_drift_after_bound_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer, out, rows = _fixture(Path(temporary))
            Path(rows["electoral_plays.json"]["live_path"]).write_text(
                '{"drift":true}', encoding="utf-8"
            )
            with self.assertRaisesRegex(
                audit.AuditError, "drifted after"
            ):
                audit.build_audit(
                    drift_pointer_path=pointer, out_root=out
                )

    def test_rejects_tampered_drift_output_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer, out, _ = _fixture(Path(temporary))
            pointer_data = json.loads(pointer.read_text())
            manifest_path = Path(pointer_data["manifest"]["path"])
            manifest = json.loads(manifest_path.read_text())
            manifest["outputs"]["file_drift"]["sha256"] = "0" * 64
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            pointer_data["manifest"] = _item(manifest_path)
            pointer.write_text(json.dumps(pointer_data), encoding="utf-8")
            with self.assertRaisesRegex(audit.AuditError, "receipt drift"):
                audit.build_audit(
                    drift_pointer_path=pointer, out_root=out
                )

    def test_rejects_authority_tamper_in_existing_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer, out, _ = _fixture(Path(temporary))
            current, _, _ = audit.build_audit(
                drift_pointer_path=pointer, out_root=out
            )
            data = json.loads(current.read_text())
            data["authoritative_for_scoring"] = True
            current.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(
                audit.AuditError, "Authority/write flag"
            ):
                audit.build_audit(
                    drift_pointer_path=pointer, out_root=out
                )

    def test_rejects_missing_target_closure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pointer, out, rows = _fixture(Path(temporary))
            pointer_data = json.loads(pointer.read_text())
            manifest_path = Path(pointer_data["manifest"]["path"])
            manifest = json.loads(manifest_path.read_text())
            ledger = Path(manifest["outputs"]["file_drift"]["path"])
            materialized = [
                json.loads(line)
                for line in ledger.read_text().splitlines()
                if json.loads(line)["name"] != "electoral_plays.json"
            ]
            materialized.append(
                dict(rows["unchanged-0.json"], name="replacement.json")
            )
            ledger.write_bytes(
                b"".join(_canonical(row) for row in materialized)
            )
            manifest["outputs"]["file_drift"] = {
                **_item(ledger),
                "record_count": 55,
            }
            manifest_path.write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            pointer_data["manifest"] = _item(manifest_path)
            pointer.write_text(
                json.dumps(pointer_data), encoding="utf-8"
            )
            with self.assertRaisesRegex(audit.AuditError, "target closure"):
                audit.build_audit(
                    drift_pointer_path=pointer, out_root=out
                )


if __name__ == "__main__":
    unittest.main()
