from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch


FEC = Path(__file__).resolve().parents[2]
if str(FEC) not in sys.path:
    sys.path.insert(0, str(FEC))

from legislation import build_billstatus_relevance_sidecar as sidecar


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def read_rows(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def raw_line(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        + b"\n"
    )


def full_xml(*, identity_type: str = "HR", doctype: bool = False) -> bytes:
    declaration = '<?xml version="1.0" encoding="utf-8"?>\n'
    if doctype:
        declaration += "<!DOCTYPE billStatus>\n"
    return (
        declaration
        + """<billStatus>
  <version>3.0.0</version>
  <bill>
    <congress>119</congress>
    <type>"""
        + identity_type
        + """</type>
    <number>1</number>
    <updateDate>2025-01-01T00:00:00Z</updateDate>
    <policyArea><name>Finance</name></policyArea>
    <subjects><legislativeSubjects>
      <item code="a"><name>Credit unions</name><updateDate>2025-01-02T00:00:00Z</updateDate></item>
      <item code="b"><name>Consumer credit</name></item>
    </legislativeSubjects></subjects>
    <committees><item><systemCode>hsba00</systemCode><name>Financial Services Committee</name>
      <activities><item><name>Referred</name><date>2025-01-03</date></item></activities>
    </item></committees>
    <relatedBills><item><title>Companion</title><congress>119</congress><number>2</number><type>S</type>
      <relationshipDetails>
        <item><type>Identical bill</type><identifiedBy>CRS</identifiedBy></item>
        <item><type>Related bill</type><identifiedBy>House</identifiedBy></item>
      </relationshipDetails>
    </item></relatedBills>
    <amendments><amendment><number>9</number><congress>119</congress><type>HAMDT</type>
      <purpose>Preserve this purpose.</purpose>
      <actions><count>1</count><actions><item><actionDate>2025-01-04</actionDate><text>Agreed to.</text></item></actions></actions>
    </amendment></amendments>
    <laws><item><type>Public Law</type><number>119-1</number></item></laws>
    <committeeReports><committeeReport><citation>H. Rept. 119-1</citation></committeeReport></committeeReports>
    <cboCostEstimates><item><pubDate>2025-01-05T00:00:00Z</pubDate>
      <title>Official estimate</title><url>https://www.cbo.gov/publication/1</url>
      <description>Direct description.</description>
    </item></cboCostEstimates>
  </bill>
</billStatus>
"""
    ).encode("utf-8")


def sparse_xml() -> bytes:
    return b"""<?xml version="1.0" encoding="utf-8"?>
<billStatus>
  <version>3.0.0</version>
  <bill>
    <congress>119</congress>
    <type>S</type>
    <number>2</number>
    <updateDate>2025-01-01T00:00:00Z</updateDate>
    <subjects><legislativeSubjects/></subjects>
  </bill>
</billStatus>
"""


def make_fixture(
    root: Path,
    *,
    first_xml: bytes | None = None,
) -> dict[str, Path]:
    candidates_path = root / "discovery" / "candidates.ndjson"
    candidates = [
        {
            "canonical_key": "119:hr:1",
            "congress": 119,
            "type": "hr",
            "number": 1,
            "title": "First candidate",
            "matched_scoring_categories": ["payments"],
        },
        {
            "canonical_key": "119:s:2",
            "congress": 119,
            "type": "s",
            "number": 2,
            "title": "Second candidate",
            "matched_monitor_categories": ["coverage"],
        },
        {
            "canonical_key": "119:hamdt:9",
            "congress": 119,
            "type": "hamdt",
            "number": 9,
            "title": "Unresolved amendment",
            "matched_scoring_categories": ["payments"],
        },
    ]
    candidates_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [raw_line(row) for row in candidates]
    candidates_path.write_bytes(b"".join(lines))
    line_receipts = {
        row["canonical_key"]: {
            "advanced_search_line": index,
            "advanced_search_line_sha256": hashlib.sha256(
                lines[index - 1].rstrip(b"\r\n")
            ).hexdigest(),
        }
        for index, row in enumerate(candidates, start=1)
    }

    exclusion_path = root / "discovery" / "exclusions.ndjson"
    exclusion_path.write_bytes(b"")
    provenance_path = root / "discovery" / "review_exclusions.ndjson"
    provenance_path.write_bytes(
        raw_line(
            {
                "canonical_key": "119:hr:999",
                "reason": "manual false positive",
            }
        )
    )
    plan_path = root / "plan.json"
    plan = {
        "schema": "govinfo-billstatus-enrichment-fetch-plan-v1",
        "primary_discovery": "Congress.gov Advanced Search - Legislation",
        "quorum_used": False,
        "candidate_creation_authorized": False,
        "semantic_exclusion_authorized": False,
        "production_write_authorized": False,
        "inputs": {
            "advanced_search_records": sidecar.file_item(
                candidates_path, record_count=3
            ),
            "exclusion_ledger": sidecar.file_item(exclusion_path),
            "exclusion_provenance": [
                sidecar.file_item(provenance_path)
            ],
        },
        "record_tasks": [
            {
                "canonical_key": "119:hr:1",
                "url": (
                    "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/hr/"
                    "BILLSTATUS-119hr1.xml"
                ),
                **line_receipts["119:hr:1"],
            },
            {
                "canonical_key": "119:s:2",
                "url": (
                    "https://www.govinfo.gov/bulkdata/BILLSTATUS/119/s/"
                    "BILLSTATUS-119s2.xml"
                ),
                **line_receipts["119:s:2"],
            },
        ],
        "unsupported_candidates": [
            {
                "canonical_key": "119:hamdt:9",
                "status": "unsupported_nested_amendment",
                "candidate_retained": True,
                **line_receipts["119:hamdt:9"],
            }
        ],
        "candidate_partition": {
            "all": 3,
            "top_level_enrichable": 2,
            "nested_only_amendments": 1,
        },
    }
    write_json(plan_path, plan)

    run_dir = root / "run"
    receipts: list[dict] = []
    xml_by_key = {
        "119:hr:1": first_xml if first_xml is not None else full_xml(),
        "119:s:2": sparse_xml(),
    }
    urls = {
        task["canonical_key"]: task["url"]
        for task in plan["record_tasks"]
    }
    for key, body in xml_by_key.items():
        digest = hashlib.sha256(body).hexdigest()
        raw_path = run_dir / "raw" / "sha256" / f"{digest}.xml"
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(body)
        receipt = {
            "schema": "govinfo-billstatus-record-receipt-v1",
            "canonical_key": key,
            "source_url": urls[key],
            "sitemap_lastmod": "2025-01-06T00:00:00Z",
            "raw_path": str(raw_path.resolve()),
            "raw_sha256": digest,
            "raw_bytes": len(body),
            **line_receipts[key],
            "candidate_creation_performed": False,
            "semantic_exclusion_performed": False,
        }
        receipt_path = (
            run_dir / "receipts" / f"{key.replace(':', '_')}.json"
        )
        write_json(receipt_path, receipt)
        receipts.append(receipt)

    worker_path = run_dir / "run_manifest.json"
    worker = {
        "schema": "govinfo-billstatus-enrichment-run-v1",
        "status": "COMPLETE",
        "official_source_only": True,
        "failures": [],
        "production_write_performed": False,
        "sync_or_promotion_performed": False,
        "candidate_creation_performed": False,
        "semantic_exclusion_performed": False,
        "plan": sidecar.file_item(plan_path),
        "partition": {
            "top_level_planned": 2,
            "top_level_completed": 2,
            "top_level_failed": 0,
            "remaining_top_level": 0,
            "nested_only_amendments_retained_unresolved": 1,
        },
        "record_receipts": receipts,
    }
    write_json(worker_path, worker)
    return {
        "worker": worker_path,
        "plan": plan_path,
        "candidates": candidates_path,
        "run": run_dir,
    }


def replace_receipt_raw(
    fixture: dict[str, Path],
    *,
    key: str,
    body: bytes,
) -> None:
    worker = sidecar.read_object(fixture["worker"])
    receipt = next(
        row for row in worker["record_receipts"]
        if row["canonical_key"] == key
    )
    digest = hashlib.sha256(body).hexdigest()
    raw_path = fixture["run"] / "raw" / "sha256" / f"{digest}.xml"
    raw_path.write_bytes(body)
    receipt["raw_path"] = str(raw_path.resolve())
    receipt["raw_sha256"] = digest
    receipt["raw_bytes"] = len(body)
    receipt_path = (
        fixture["run"]
        / "receipts"
        / f"{key.replace(':', '_')}.json"
    )
    write_json(receipt_path, receipt)
    write_json(fixture["worker"], worker)


def make_default_worker_resolution_fixture(root: Path) -> dict[str, object]:
    billstatus_root = (
        root
        / "audits"
        / "legfix5"
        / "billstatus_bulk_enrichment"
    )
    bundle_id = "a" * 64
    bundle_dir = billstatus_root / "snapshots" / bundle_id
    strict_path = (
        root
        / "audits"
        / "legfix5"
        / "congress_advanced_pages_20260725"
        / "manifest.partial.current.json"
    )
    write_json(
        strict_path,
        {
            "schema": "acu-congress-advanced-pages-manifest-v1",
            "primary_instrument": (
                "Congress.gov Advanced Search - Legislation"
            ),
            "quorum_used": False,
        },
    )
    strict_item = sidecar.file_item(strict_path)
    current_plan_path = bundle_dir / "snapshot_plan.json"
    current_plan = {
        "schema": "govinfo-billstatus-enrichment-fetch-plan-v1",
        "primary_discovery": "Congress.gov Advanced Search - Legislation",
        "quorum_used": False,
        "network_executed": False,
        "candidate_creation_authorized": False,
        "semantic_exclusion_authorized": False,
        "production_write_authorized": False,
        "inputs": {
            "advanced_search_records": {
                "sha256": "current-candidate-hash",
            }
        },
        "record_tasks": [{"canonical_key": "119:hr:1"}],
        "unsupported_candidates": [
            {"canonical_key": "119:hamdt:1"}
        ],
        "snapshot_contract": {
            "strict_manifest": strict_item,
        },
    }
    write_json(current_plan_path, current_plan)
    current_plan_item = sidecar.file_item(current_plan_path)

    legacy_plan_path = billstatus_root / "current_snapshot_plan.json"
    legacy_plan = dict(current_plan)
    legacy_plan["inputs"] = {
        "advanced_search_records": {"sha256": "stale-candidate-hash"}
    }
    legacy_plan["record_tasks"] = [{"canonical_key": "118:hr:99"}]
    write_json(legacy_plan_path, legacy_plan)

    worker_plan_path = billstatus_root / "worker_plan.json"
    write_json(worker_plan_path, current_plan)
    worker_path = billstatus_root / "fetch_runs" / "run1" / "run_manifest.json"
    write_json(
        worker_path,
        {
            "schema": "govinfo-billstatus-enrichment-run-v1",
            "status": "COMPLETE",
            "plan": {"path": str(worker_plan_path.resolve())},
        },
    )

    input_path = billstatus_root / "bundle_input.json"
    write_json(input_path, {"fixture": True})
    input_item = sidecar.file_item(input_path)
    bundle_inputs = {
        "selected_strict_manifest": strict_item,
        "advanced_search_records": input_item,
        "advanced_search_memberships": input_item,
        "exclusion_ledger": input_item,
        "exclusion_provenance": [input_item],
        "cumulative_manifest": input_item,
        "query_registry": input_item,
        "membership_review": input_item,
        "coverage_gap_ledger": input_item,
        "producer_sources": {},
    }
    bundle_receipt_path = bundle_dir / "bundle_receipt.json"
    bundle = {
        "schema": sidecar.SNAPSHOT_REFRESH.SCHEMA,
        "bundle_id": bundle_id,
        "immutable": True,
        "snapshot_only": True,
        "network_executed": False,
        "candidate_exclusions_performed": False,
        "candidate_deletions_performed": False,
        "sync_or_promotion_performed": False,
        "inputs": bundle_inputs,
        "outputs": {"snapshot_plan": current_plan_item},
    }
    write_json(bundle_receipt_path, bundle)

    checkpoint_path = (
        root
        / "audits"
        / "legfix5"
        / "shadow_recompute"
        / "presync_current"
        / "checkpoint.json"
    )
    checkpoint_plan_item = {
        "path": current_plan_item["path"],
        "size": current_plan_item["bytes"],
        "sha256": current_plan_item["sha256"],
    }
    checkpoint_strict_item = {
        "path": strict_item["path"],
        "size": strict_item["bytes"],
        "sha256": strict_item["sha256"],
    }
    write_json(
        checkpoint_path,
        {
            "schema": "legislation-presync-audit-checkpoint-v1",
            "staged_only": True,
            "production_or_mainframe_written": False,
            "coverage": {
                "billstatus_snapshot_evidence": {
                    "status": "CURRENT",
                    "snapshot_only": True,
                    "network_executed": False,
                    "quorum_used": False,
                    "authoritative_for_discovery": False,
                    "authoritative_for_semantic_exclusion": False,
                    "authoritative_for_sync_or_promotion": False,
                    "issues": [],
                    "plan": checkpoint_plan_item,
                    "plan_sha256": current_plan_item["sha256"],
                    "strict_manifest": checkpoint_strict_item,
                },
                "billstatus_snapshot_bundle": {
                    "status": "CURRENT",
                    "bundle_id": bundle_id,
                    "bundle_dir": str(bundle_dir.resolve()),
                    "bundle_receipt": str(bundle_receipt_path.resolve()),
                    "snapshot_plan": str(current_plan_path.resolve()),
                    "network_executed": False,
                    "sync_or_promotion_performed": False,
                    "issues": [],
                },
            },
        },
    )
    return {
        "bundle": bundle,
        "bundle_id": bundle_id,
        "bundle_inputs": bundle_inputs,
        "bundle_receipt": bundle_receipt_path,
        "current_plan": current_plan_path,
        "legacy_plan": legacy_plan_path,
        "worker": worker_path,
    }


class BillstatusRelevanceSidecarTest(unittest.TestCase):
    def test_default_worker_uses_current_checkpoint_not_stale_legacy(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_default_worker_resolution_fixture(root)
            with (
                patch.object(sidecar, "ROOT", root),
                patch.object(
                    sidecar.SNAPSHOT_REFRESH,
                    "input_address",
                    return_value=(
                        fixture["bundle_id"],
                        fixture["bundle_inputs"],
                    ),
                ),
                patch.object(
                    sidecar.SNAPSHOT_REFRESH,
                    "validate_bundle",
                ) as validate_bundle,
            ):
                self.assertEqual(
                    sidecar.default_worker_path(),
                    fixture["worker"].resolve(),
                )
            validate_bundle.assert_called_once()

    def test_default_worker_fails_closed_on_bundle_plan_receipt_tampering(
        self,
    ) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_default_worker_resolution_fixture(root)
            bundle = sidecar.read_object(fixture["bundle_receipt"])
            bundle["outputs"]["snapshot_plan"]["sha256"] = "0" * 64
            write_json(fixture["bundle_receipt"], bundle)
            with (
                patch.object(sidecar, "ROOT", root),
                patch.object(
                    sidecar.SNAPSHOT_REFRESH,
                    "input_address",
                    return_value=(
                        fixture["bundle_id"],
                        fixture["bundle_inputs"],
                    ),
                ),
                patch.object(
                    sidecar.SNAPSHOT_REFRESH,
                    "validate_bundle",
                ),
                self.assertRaisesRegex(
                    sidecar.ContractError,
                    "immutable bundle snapshot plan SHA-256 mismatch",
                ),
            ):
                sidecar.default_worker_path()

    def test_builds_content_addressed_lossless_staged_sidecar(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_fixture(root)
            pointer = root / "current.json"
            out_dir, manifest, created = sidecar.build_sidecar(
                worker_path=fixture["worker"],
                out_root=root / "sidecars",
                pointer_path=pointer,
                enforce_workspace_audits=False,
            )
            self.assertTrue(created)
            self.assertEqual(manifest["status"], "COMPLETE_STAGED_ONLY")
            self.assertEqual(
                manifest["coverage"]["selected_candidate_count"], 3
            )
            self.assertEqual(
                manifest["coverage"]["top_level_structured"], 2
            )
            self.assertEqual(
                manifest["coverage"][
                    "nested_amendments_retained_unresolved"
                ],
                1,
            )
            self.assertTrue(
                manifest["coverage"]["candidate_partition_closed"]
            )
            self.assertFalse(manifest["candidate_creation_performed"])
            self.assertFalse(manifest["semantic_inclusion_performed"])
            self.assertFalse(manifest["semantic_exclusion_performed"])
            self.assertFalse(manifest["automatic_scoring_performed"])
            self.assertFalse(manifest["acceptance_passed"])

            measures = read_rows(
                out_dir / manifest["outputs"]["measures"]["file"]
            )
            self.assertEqual(
                [row["canonical_key"] for row in measures],
                ["119:hr:1", "119:s:2"],
            )
            first = measures[0]
            self.assertEqual(
                first["advanced_search_candidate"]["title"],
                "First candidate",
            )
            self.assertEqual(first["missing_sections"], [])
            subject_tree = first["sections"]["legislative_subjects"][
                "data"
            ]
            subject_items = subject_tree["children"]
            self.assertEqual(
                [item["attributes"]["code"] for item in subject_items],
                ["a", "b"],
            )
            related_tree = first["sections"]["related_bills"]["data"]
            detail_items = (
                related_tree["children"][0]["children"][4]["children"]
            )
            self.assertEqual(len(detail_items), 2)
            amendment_text = json.dumps(
                first["sections"]["amendments"]["data"]
            )
            self.assertIn("Preserve this purpose.", amendment_text)
            self.assertEqual(
                first["provenance"]["raw_source"]["sha256"],
                sidecar.sha256_file(
                    Path(first["provenance"]["raw_source"]["path"])
                ),
            )

            sparse = measures[1]
            self.assertEqual(
                sparse["empty_sections"], ["legislative_subjects"]
            )
            self.assertEqual(len(sparse["missing_sections"]), 7)
            missing = read_rows(
                out_dir / manifest["outputs"]["missing_sections"]["file"]
            )
            empty = read_rows(
                out_dir / manifest["outputs"]["empty_sections"]["file"]
            )
            self.assertEqual(len(missing), 7)
            self.assertEqual(len(empty), 1)
            self.assertTrue(
                all(row["candidate_retained"] for row in missing + empty)
            )
            self.assertTrue(
                all(row["absence_is_neutral"] for row in missing + empty)
            )
            unresolved = read_rows(
                out_dir
                / manifest["outputs"]["unresolved_amendments"]["file"]
            )
            self.assertEqual(len(unresolved), 1)
            self.assertEqual(
                unresolved[0]["advanced_search_candidate"]["title"],
                "Unresolved amendment",
            )
            self.assertFalse(
                unresolved[0]["relationship_resolution_performed"]
            )
            self.assertEqual(
                sidecar.read_object(pointer)["sidecar_id"],
                manifest["sidecar_id"],
            )

            out_again, manifest_again, created_again = (
                sidecar.build_sidecar(
                    worker_path=fixture["worker"],
                    out_root=root / "sidecars",
                    pointer_path=pointer,
                    enforce_workspace_audits=False,
                )
            )
            self.assertFalse(created_again)
            self.assertEqual(out_again, out_dir)
            self.assertEqual(
                manifest_again["sidecar_id"], manifest["sidecar_id"]
            )

    def test_rejects_same_size_raw_xml_tampering(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_fixture(root)
            worker = sidecar.read_object(fixture["worker"])
            receipt = worker["record_receipts"][0]
            raw_path = Path(receipt["raw_path"])
            body = raw_path.read_bytes()
            self.assertIn(b"Finance", body)
            raw_path.write_bytes(body.replace(b"Finance", b"Finanxe", 1))
            with self.assertRaisesRegex(
                sidecar.ContractError, "raw XML SHA-256 mismatch"
            ):
                sidecar.build_sidecar(
                    worker_path=fixture["worker"],
                    out_root=root / "sidecars",
                    enforce_workspace_audits=False,
                )

    def test_rejects_rehashed_xml_identity_drift(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_fixture(root)
            replace_receipt_raw(
                fixture,
                key="119:hr:1",
                body=full_xml(identity_type="S"),
            )
            with self.assertRaisesRegex(
                sidecar.ContractError, "XML bill identity drift"
            ):
                sidecar.build_sidecar(
                    worker_path=fixture["worker"],
                    out_root=root / "sidecars",
                    enforce_workspace_audits=False,
                )

    def test_rejects_doctype_even_when_hash_bound(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_fixture(root, first_xml=full_xml(doctype=True))
            with self.assertRaisesRegex(
                sidecar.ContractError, "DOCTYPE is forbidden"
            ):
                sidecar.build_sidecar(
                    worker_path=fixture["worker"],
                    out_root=root / "sidecars",
                    enforce_workspace_audits=False,
                )

    def test_rejects_worker_with_semantic_authority(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_fixture(root)
            worker = sidecar.read_object(fixture["worker"])
            worker["semantic_exclusion_performed"] = True
            write_json(fixture["worker"], worker)
            with self.assertRaisesRegex(
                sidecar.ContractError, "unsafe worker flag"
            ):
                sidecar.build_sidecar(
                    worker_path=fixture["worker"],
                    out_root=root / "sidecars",
                    enforce_workspace_audits=False,
                )

    def test_rejects_tampered_candidate_or_exclusion_provenance(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            fixture = make_fixture(root)
            fixture["candidates"].write_bytes(
                fixture["candidates"].read_bytes().replace(
                    b"First candidate", b"First candidatE", 1
                )
            )
            with self.assertRaisesRegex(
                sidecar.ContractError,
                "advanced-search candidates SHA-256 mismatch",
            ):
                sidecar.build_sidecar(
                    worker_path=fixture["worker"],
                    out_root=root / "sidecars",
                    enforce_workspace_audits=False,
                )


if __name__ == "__main__":
    unittest.main()
