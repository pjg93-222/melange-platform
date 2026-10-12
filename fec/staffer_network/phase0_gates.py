# -*- coding: utf-8 -*-
"""Executable Phase 0 isolation and determinism/atomicity evidence packets."""

from __future__ import annotations

from contextlib import closing
import importlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile
from typing import Any, Iterable
from unittest import mock

from .publication import (
    POINTER_NAME,
    canonical_ndjson_gzip,
    load_current_generation,
    publish_generation,
)
from .source_manifest import (
    canonical_json_bytes,
    seal_source_manifest,
    sha256_bytes,
    sha256_file,
)


ISOLATION_SCHEMA_ID = "staffer-network-isolation-receipt-v1"
ATOMICITY_SCHEMA_ID = "staffer-network-atomicity-receipt-v1"
_READER_MARKERS = (
    "STAFF_NET",
    "load_staffer_network",
    "staffer_network_current.json",
    "import staffer_network",
    "from staffer_network",
)
_APPROVED_PRODUCTION_READERS = ("fec/network/staffer_network_loader.py",)


class Phase0GateError(RuntimeError):
    pass


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _production_reader_candidates(repo_root: Path) -> tuple[int, list[str]]:
    fec_root = repo_root / "fec"
    candidates: list[str] = []
    files_scanned = 0
    for path in sorted(fec_root.rglob("*.py")):
        relative = _relative(path, repo_root)
        parts = set(path.relative_to(fec_root).parts)
        if "tests" in parts or any(part.startswith("_quarantine") for part in parts):
            continue
        if path.is_relative_to(fec_root / "staffer_network"):
            continue
        files_scanned += 1
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        if any(marker in text for marker in _READER_MARKERS):
            candidates.append(relative)
    return files_scanned, candidates


def _hash_inventory(repo_root: Path, relative_paths: Iterable[str]) -> list[dict[str, Any]]:
    inventory: list[dict[str, Any]] = []
    for relative in relative_paths:
        target = repo_root / relative
        if not target.is_file():
            raise Phase0GateError(f"protected output is missing: {relative}")
        inventory.append(
            {
                "path": relative,
                "bytes": target.stat().st_size,
                "sha256": sha256_file(target),
            }
        )
    return inventory


def isolation_receipt(
    repo_root: str | Path,
    *,
    generated_at: str,
    protected_outputs: Iterable[str],
) -> dict[str, Any]:
    """Prove the default/off branch has no staff-artifact I/O or output impact."""

    root = Path(repo_root).resolve()
    files_scanned, candidates = _production_reader_candidates(root)
    approved = list(_APPROVED_PRODUCTION_READERS)
    unexpected = sorted(set(candidates) - set(approved))
    missing_approved = sorted(set(approved) - set(candidates))
    before = _hash_inventory(root, protected_outputs)

    from network.staffer_network_loader import load_staffer_network

    open_attempts: list[str] = []

    def deny(*args, **kwargs):
        open_attempts.append("artifact-open")
        raise AssertionError("off mode attempted artifact I/O")

    results: list[Any] = []
    failures: list[str] = []
    try:
        with mock.patch.object(Path, "read_text", side_effect=deny), \
             mock.patch.object(Path, "read_bytes", side_effect=deny), \
             mock.patch.object(Path, "open", side_effect=deny), \
             mock.patch("builtins.open", side_effect=deny), \
             mock.patch("os.open", side_effect=deny):
            results.append(load_staffer_network({"STAFF_NET": "off"}, root=root))
            results.append(
                load_staffer_network(
                    {}, root=root / ".staffer-network-physically-absent-negative-control"
                )
            )
    except AssertionError:
        failures.append("off mode attempted artifact I/O")

    after = _hash_inventory(root, protected_outputs)
    changed = [
        left["path"]
        for left, right in zip(before, after)
        if left != right
    ]
    if unexpected:
        failures.append("unexpected production reader candidates")
    if missing_approved:
        failures.append("approved reader was not found by the scanner")
    if open_attempts:
        failures.append("off mode recorded open attempts")
    if len(results) != 2 or any(value is not None for value in results):
        failures.append("off/absent controls did not return null")
    if changed:
        failures.append("protected production outputs changed")

    return {
        "schema_id": ISOLATION_SCHEMA_ID,
        "generated_at": generated_at,
        "status": "PASS" if not failures else "FAIL",
        "production_python_files_scanned": files_scanned,
        "reader_candidates": candidates,
        "approved_production_readers": approved,
        "unexpected_reader_paths": unexpected,
        "missing_approved_reader_paths": missing_approved,
        "off_open_attempts": len(open_attempts),
        "off_results_null": len(results) == 2 and all(value is None for value in results),
        "physical_absence_result_null": len(results) == 2 and results[-1] is None,
        "protected_outputs_before": before,
        "protected_outputs_after": after,
        "protected_output_changes": changed,
        "failures": failures,
    }


def _json_sql_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"bytes_hex": value.hex()}
    if isinstance(value, float):
        return {"float": format(value, ".17g")}
    return value


def logical_sqlite_sha256(connection: sqlite3.Connection) -> str:
    """Hash schema plus sorted logical rows, independent of SQLite page layout."""

    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    logical: list[dict[str, Any]] = []
    for table in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({quoted})")]
        schema_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        rows = [
            [_json_sql_value(value) for value in row]
            for row in connection.execute(f"SELECT * FROM {quoted}")
        ]
        rows.sort(key=lambda row: canonical_json_bytes(row))
        logical.append(
            {
                "table": table,
                "schema": re.sub(r"\s+", " ", schema_sql).strip(),
                "columns": columns,
                "rows": rows,
            }
        )
    return sha256_bytes(canonical_json_bytes(logical))


def _fixture_statements() -> tuple[list[tuple[str, tuple[Any, ...]]], list[tuple[str, tuple[Any, ...]]]]:
    phase_one = [
        ("INSERT INTO source_system VALUES (?,?,?,?,?,?,?)", ("src", "Fixture", "fixture", "urn:test", "CC0", "public", "AUTHORIZED")),
        ("INSERT INTO ingest_run VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("run", "src", "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z", "{}", "v1", "v1", 1, 1, 1, 0, "COMPLETE", "a" * 64)),
        ("INSERT INTO raw_record VALUES (?,?,?,?,?,?,?,?,?,?)", ("raw", "run", "fixture", "one", "urn:test:one", "b" * 64, "application/json", None, None, "{}")),
        ("INSERT INTO entity VALUES (?,?,?,?)", ("person:1", "person", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity VALUES (?,?,?,?)", ("person:2", "person", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity VALUES (?,?,?,?)", ("org:1", "organization", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity VALUES (?,?,?,?)", ("activity:1", "lda_activity", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO source_entity VALUES (?,?,?,?,?)", ("se:person:1", "src", "person", "p1", "raw")),
        ("INSERT INTO source_entity VALUES (?,?,?,?,?)", ("se:person:2", "src", "person", "p2", "raw")),
        ("INSERT INTO source_entity VALUES (?,?,?,?,?)", ("se:org:1", "src", "organization", "o1", "raw")),
        ("INSERT INTO source_entity VALUES (?,?,?,?,?)", ("se:activity:1", "src", "lda_activity", "a1", "raw")),
    ]
    phase_two = [
        ("INSERT INTO source_entity_observation VALUES (?,?,?,?)", ("se:person:1", "raw", "2026-08-30T00:00:00Z", "PRIMARY")),
        ("INSERT INTO source_entity_observation VALUES (?,?,?,?)", ("se:person:2", "raw", "2026-08-30T00:00:00Z", "PRIMARY")),
        ("INSERT INTO source_entity_observation VALUES (?,?,?,?)", ("se:org:1", "raw", "2026-08-30T00:00:00Z", "PRIMARY")),
        ("INSERT INTO source_entity_observation VALUES (?,?,?,?)", ("se:activity:1", "raw", "2026-08-30T00:00:00Z", "PRIMARY")),
        ("INSERT INTO resolution_decision VALUES (?,?,?,?,?,?,?,?,?,?,?)", ("decision:1", "se:person:1", "se:person:2", "MATCH", "fixture", None, 1.0, "fixture", "fixture", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity_membership VALUES (?,?,?,?,?,?)", ("se:person:1", "person:1", None, "SOURCE_NATIVE", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity_membership VALUES (?,?,?,?,?,?)", ("se:person:2", "person:1", "decision:1", "RESOLUTION", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity_membership VALUES (?,?,?,?,?,?)", ("se:org:1", "org:1", None, "SOURCE_NATIVE", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO entity_membership VALUES (?,?,?,?,?,?)", ("se:activity:1", "activity:1", None, "SOURCE_NATIVE", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO identifier_assertion VALUES (?,?,?,?,?,?,?)", ("identifier:1", "person:1", "fixture", "p1", "se:person:1", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO lda_activity_lobbyist_observation VALUES (?,?,?,?,?,?,?,?,?,?)", ("ldaobs:1", "raw", "se:activity:1", "se:person:1", 0, "Former staff", 1, "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO relation_assertion VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("assertion:1", "se:person:1", "WORKED_FOR", "se:org:1", None, "2026-01-01", "2026-01-01", None, None, "day", "unknown", 1, "2026-08-30T00:00:00Z", "2026-08-30T00:00:00Z", None, "raw", "fixture", 1.0, "v1")),
        ("INSERT INTO consolidation_build VALUES (?,?,?,?,?)", ("consolidation:1", "c" * 64, "v1", "2026-08-30T00:00:00Z", "{}")),
        ("INSERT INTO canonical_fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("fact:1", "person:1", "WORKED_FOR", "org:1", None, "2026-01-01", "2026-01-01", None, None, "ACTIVE", "consolidation:1", "2026-08-30T00:00:00Z", None, None)),
        ("INSERT INTO fact_support VALUES (?,?,?,?,?)", ("fact:1", "assertion:1", "PRIMARY", "2026-08-30T00:00:00Z", None)),
        ("INSERT INTO network_build VALUES (?,?,?,?,?)", ("build:1", "2026-08-30T00:00:00Z", "c" * 64, "{}", "v1")),
        ("INSERT INTO network_edge VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("edge:1", "build:1", "staffer-industry", "WORKED_FOR", "person:1", "org:1", 1, 1.0, None, "fixture", "day", 1, "d" * 64)),
        ("INSERT INTO network_edge_support VALUES (?,?,?)", ("edge:1", "fact:1", 1.0)),
    ]
    return phase_one, phase_two


def _build_database(schema_sql: str, *, incremental: bool, reverse_independent: bool = False) -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.executescript(schema_sql)
    phase_one, phase_two = _fixture_statements()
    if reverse_independent:
        fixed = phase_one[:3]
        entities = list(reversed(phase_one[3:7]))
        source_entities = list(reversed(phase_one[7:]))
        phase_one = fixed + entities + source_entities
    for statement, params in phase_one:
        connection.execute(statement, params)
    if incremental:
        connection.commit()
    for statement, params in phase_two:
        connection.execute(statement, params)
    connection.commit()
    return connection


def _publication_fixture(temp_root: Path, generation_id: str) -> tuple[dict[str, tuple[bytes, str, int]], dict[str, tuple[dict[str, Any], Path]]]:
    source = temp_root / "input.json"
    source.write_bytes(b"{}")
    manifest = seal_source_manifest(
        {
            "schema_id": "staffer-network-source-manifest-v1",
            "source_system_id": "atomicity-fixture",
            "enabled": True,
            "authority_scope": "atomicity fixture",
            "license": "CC0",
            "terms_uri": "urn:test:atomicity",
            "terms_checked_at": "2026-08-30T00:00:00Z",
            "authorization_status": "AUTHORIZED",
            "approved_access_mode": "local fixture",
            "query_or_inventory": "one fixture file",
            "crawl_watermark": "fixture",
            "retention_policy": "temporary",
            "source_schema_version": "fixture-v1",
            "freshness_rule": "immutable fixture",
            "redistribution_class": "public",
            "source_as_of": "2026-08-30T00:00:00Z",
            "collected_at": "2026-08-30T00:00:00Z",
            "collector_version": "fixture-v1",
            "parser_version": "fixture-v1",
            "expected_count": 1,
            "observed_count": 1,
            "accepted_count": 1,
            "rejected_count": 0,
            "duplicate_count": 0,
            "amendment_policy": "replace",
            "rejection_reasons": {},
            "acceptance_bar": {"metric": "accepted", "threshold": 1.0, "comparison": ">=", "observed": 1.0, "pass": True},
            "files": [{"path": "input.json", "sha256": sha256_file(source), "bytes": 2}],
        }
    )
    metric = {
        "metric_id": "I-11-fixture",
        "metric_definition_version": "v1",
        "lifecycle_state": "INVARIANT",
        "manifest_sha256": [manifest["manifest_sha256"]],
        "provenance_manifest_state": "SEALED",
        "as_of": "2026-08-30T00:00:00Z",
        "measured_at": "2026-08-30T00:00:00Z",
        "eligible_denominator_count": 1,
        "cohort_query": "atomicity fixture",
        "numerator_count": 1,
        "statistic": 1.0,
        "confidence_interval": None,
        "threshold": 1.0,
        "comparison": ">=",
        "strata": [],
        "exclusions": [],
        "evidence_artifact_uri": "urn:test:atomicity",
        "evidence_sha256": "e" * 64,
        "result": "PASS",
        "not_evaluable_reason": None,
        "consequence_class": "ABORT_BUILD",
        "owner": "staffer-network",
    }
    coverage = {
        "schema_id": "staffer-network-coverage-v1",
        "generation_id": generation_id,
        "metrics": [metric],
    }
    return (
        {"coverage.json": (canonical_json_bytes(coverage), "staffer-network-coverage-v1", 1)},
        {"fixture://atomicity": (manifest, temp_root)},
    )


def atomicity_receipt(schema_path: str | Path, *, generated_at: str) -> dict[str, Any]:
    """Exercise logical database parity, integrity, and pointer fault isolation."""

    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    with closing(_build_database(schema_sql, incremental=False)) as clean_a, \
         closing(_build_database(schema_sql, incremental=False, reverse_independent=True)) as clean_b, \
         closing(_build_database(schema_sql, incremental=True)) as incremental:
        clean_a_digest = logical_sqlite_sha256(clean_a)
        clean_b_digest = logical_sqlite_sha256(clean_b)
        incremental_digest = logical_sqlite_sha256(incremental)
        clean_integrity = clean_a.execute("PRAGMA integrity_check").fetchone()[0]
        incremental_integrity = incremental.execute("PRAGMA integrity_check").fetchone()[0]
        table_count = clean_a.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        nonempty_tables = sum(
            clean_a.execute(f'SELECT EXISTS(SELECT 1 FROM "{name}")').fetchone()[0]
            for (name,) in clean_a.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        )

    fault_raised = False
    pointer_unchanged = False
    reader_old = False
    reader_new = False
    observations: list[str] = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        artifacts, registry = _publication_fixture(root, "g1")
        publish_generation(root, generation_id="g1", build_as_of="2026-08-30T00:00:00Z", derivation_version="fixture-v1", source_manifest_registry=registry, artifacts=artifacts)
        pointer_before = (root / POINTER_NAME).read_bytes()
        atomic_module = importlib.import_module(publish_generation.__globals__["atomic_write_bytes"].__module__)
        original_replace = atomic_module.os.replace

        def fail_pointer(source, destination):
            if Path(destination).name == POINTER_NAME:
                raise OSError("planted pointer replacement failure")
            return original_replace(source, destination)

        artifacts_g2, registry_g2 = _publication_fixture(root, "g2")
        try:
            with mock.patch.object(atomic_module.os, "replace", side_effect=fail_pointer):
                publish_generation(root, generation_id="g2", build_as_of="2026-08-30T00:00:00Z", derivation_version="fixture-v1", source_manifest_registry=registry_g2, artifacts=artifacts_g2)
        except OSError:
            fault_raised = True
        pointer_unchanged = (root / POINTER_NAME).read_bytes() == pointer_before
        old_manifest, _ = load_current_generation(root)
        observations.append(old_manifest["generation_id"])
        reader_old = old_manifest["generation_id"] == "g1"
        artifacts_g3, registry_g3 = _publication_fixture(root, "g3")
        publish_generation(root, generation_id="g3", build_as_of="2026-08-30T00:00:00Z", derivation_version="fixture-v1", source_manifest_registry=registry_g3, artifacts=artifacts_g3)
        new_manifest, _ = load_current_generation(root)
        observations.append(new_manifest["generation_id"])
        reader_new = new_manifest["generation_id"] == "g3"

    failures: list[str] = []
    if clean_a_digest != clean_b_digest:
        failures.append("two clean builds differ logically")
    if clean_a_digest != incremental_digest:
        failures.append("clean and incremental builds differ logically")
    if clean_integrity != "ok" or incremental_integrity != "ok":
        failures.append("SQLite integrity_check failed")
    if nonempty_tables != table_count:
        failures.append("logical database fixture is vacuous")
    if not all((fault_raised, pointer_unchanged, reader_old, reader_new)):
        failures.append("pointer fault isolation failed")
    if observations != ["g1", "g3"]:
        failures.append("reader observed a mixed or failed generation")

    return {
        "schema_id": ATOMICITY_SCHEMA_ID,
        "generated_at": generated_at,
        "status": "PASS" if not failures else "FAIL",
        "schema_sha256": sha256_file(schema_path),
        "logical_table_count": table_count,
        "nonempty_table_count": nonempty_tables,
        "clean_build_digest_a": clean_a_digest,
        "clean_build_digest_b": clean_b_digest,
        "incremental_build_digest": incremental_digest,
        "clean_builds_equal": clean_a_digest == clean_b_digest,
        "clean_incremental_equal": clean_a_digest == incremental_digest,
        "clean_integrity_check": clean_integrity,
        "incremental_integrity_check": incremental_integrity,
        "fault_injection_raised": fault_raised,
        "pointer_unchanged_after_fault": pointer_unchanged,
        "reader_old_after_fault": reader_old,
        "reader_new_after_success": reader_new,
        "reader_generation_observations": observations,
        "mixed_generation_observations": sum(value not in {"g1", "g3"} for value in observations),
        "failures": failures,
    }
