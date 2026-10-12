from pathlib import Path
import sys
import unittest

FEC = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FEC))

from staffer_network.source_manifest import ManifestError, seal_source_manifest, validate_manifest_files, validate_source_manifest


def manifest(**changes):
    value = {
        "schema_id": "staffer-network-source-manifest-v1",
        "source_system_id": "synthetic",
        "enabled": True,
        "authority_scope": "synthetic unit fixture",
        "license": "CC0",
        "terms_uri": "https://example.test/terms",
        "terms_checked_at": "2026-08-29T20:00:00Z",
        "authorization_status": "AUTHORIZED",
        "approved_access_mode": "local synthetic fixture",
        "query_or_inventory": "two synthetic records",
        "crawl_watermark": "fixture-watermark",
        "retention_policy": "temporary fixture",
        "source_schema_version": "fixture-schema-v1",
        "freshness_rule": "immutable fixture",
        "redistribution_class": "public",
        "source_as_of": "2026-08-29T00:00:00Z",
        "collected_at": "2026-08-29T20:00:00Z",
        "collector_version": "fixture-v1",
        "parser_version": "fixture-v1",
        "expected_count": 2,
        "observed_count": 2,
        "accepted_count": 2,
        "rejected_count": 0,
        "duplicate_count": 0,
        "amendment_policy": "replace fixture by version",
        "rejection_reasons": {},
        "acceptance_bar": {"metric": "accepted / expected", "threshold": 1.0, "comparison": ">=", "observed": 1.0, "pass": True},
        "files": [{"path": "fixture.json", "sha256": "a" * 64, "bytes": 2}],
    }
    value.update(changes)
    return value


class StafferSourceManifestTests(unittest.TestCase):
    def test_seal_is_deterministic_and_valid(self):
        first = seal_source_manifest(manifest())
        second = seal_source_manifest(manifest())
        self.assertEqual(first, second)
        validate_source_manifest(first)

    def test_enabled_source_cannot_be_vacuous(self):
        with self.assertRaisesRegex(ManifestError, "zero accepted"):
            validate_source_manifest(manifest(observed_count=2, accepted_count=0, rejected_count=2))

    def test_unsealed_manifest_cannot_authorize_a_source(self):
        with self.assertRaisesRegex(ManifestError, "manifest_sha256"):
            validate_source_manifest(manifest())

    def test_quarantined_source_cannot_be_enabled(self):
        with self.assertRaisesRegex(ManifestError, "must be AUTHORIZED"):
            validate_source_manifest(manifest(authorization_status="QUARANTINED"))

    def test_acceptance_bar_cannot_self_assert_pass(self):
        contradictory = manifest(acceptance_bar={
            "metric": "accepted / expected", "threshold": 1.0,
            "comparison": ">=", "observed": 0.01, "pass": True,
        })
        with self.assertRaisesRegex(ManifestError, "contradicts"):
            validate_source_manifest(contradictory)

    def test_conservation_and_lowercase_sha_are_binding(self):
        with self.assertRaises(ManifestError):
            validate_source_manifest(manifest(rejected_count=1))
        broken = manifest(files=[{"path": "fixture.json", "sha256": "A" * 64, "bytes": 2}])
        with self.assertRaises(ManifestError):
            validate_source_manifest(broken)

    def test_advertised_files_are_hash_and_size_verified(self):
        import hashlib
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "fixture.json"
            target.write_bytes(b"{}")
            document = seal_source_manifest(manifest(files=[{
                "path": "fixture.json", "sha256": hashlib.sha256(b"{}").hexdigest(), "bytes": 2,
            }]))
            validate_manifest_files(document, temp)
            target.write_bytes(b"[]")
            with self.assertRaisesRegex(ManifestError, "SHA-256 mismatch"):
                validate_manifest_files(document, temp)


if __name__ == "__main__":
    unittest.main()
