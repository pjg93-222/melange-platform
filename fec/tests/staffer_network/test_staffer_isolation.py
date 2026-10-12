from pathlib import Path
import builtins
import hashlib
import json
import os
import sys
import unittest
from unittest import mock

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from network.staffer_network_loader import StaffNetworkModeError, load_staffer_network


class StafferIsolationTests(unittest.TestCase):
    def test_off_performs_no_path_reads(self):
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("artifact read")), \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("artifact read")), \
             mock.patch.object(Path, "open", side_effect=AssertionError("artifact open")), \
             mock.patch.object(builtins, "open", side_effect=AssertionError("artifact open")), \
             mock.patch.object(os, "open", side_effect=AssertionError("artifact os.open")):
            self.assertIsNone(load_staffer_network({"STAFF_NET": "off"}))
            self.assertIsNone(load_staffer_network({}))

    def test_engine_mode_is_not_armed(self):
        with self.assertRaisesRegex(StaffNetworkModeError, "not armed"):
            load_staffer_network({"STAFF_NET": "engine"})

    def test_analysis_requires_a_receipted_root(self):
        with self.assertRaisesRegex(StaffNetworkModeError, "requires root"):
            load_staffer_network({"STAFF_NET": "analysis"})
        with self.assertRaisesRegex(StaffNetworkModeError, "unreadable"):
            load_staffer_network(
                {"STAFF_NET": "analysis"}, root=FEC / "tests" / "does-not-exist"
            )

    def test_analysis_verifies_and_returns_artifact_paths(self):
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            files = []
            for role, name in (
                ("network_edges", "network_edges.ndjson.gz"),
                ("network_scores", "network_scores.ndjson.gz"),
            ):
                path = root / name
                path.write_bytes(role.encode("utf-8"))
                files.append(
                    {
                        "role": role,
                        "path": name,
                        "bytes": path.stat().st_size,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                )
            (root / "network_build_receipt.json").write_text(
                json.dumps(
                    {
                        "schema_id": "staffer-network-network-build-receipt-v1",
                        "status": "PASS",
                        "canonical_identity_merges": 0,
                        "files": files,
                    }
                ),
                encoding="utf-8",
            )
            receipt, artifacts = load_staffer_network(
                {"STAFF_NET": "analysis"}, root=root
            )
            self.assertEqual(receipt["status"], "PASS")
            self.assertEqual(artifacts["edges_path"], root / "network_edges.ndjson.gz")


if __name__ == "__main__":
    unittest.main()
