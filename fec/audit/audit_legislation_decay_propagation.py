#!/usr/bin/env python3
"""Hash-bound, read-only audit of Congress-decay propagation.

This audit never runs a collector or a legislation consumer and never writes
outside its explicit audit output directory.  It distinguishes the staged
1/.66/.33/.16 policy from the frozen production 1/.8/.64/.512 policy and
records the places where Congress decay is intentionally absent.
"""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

try:
    from .live_relocations import LIVE_RELOCATIONS, resolve_live_source
except ImportError:
    try:
        from fec.audit.live_relocations import (
            LIVE_RELOCATIONS,
            resolve_live_source,
        )
    except ModuleNotFoundError:
        # Direct script execution from fec/audit, or spec-loaded by a test
        # without this directory on sys.path -- anchor by __file__.
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from live_relocations import LIVE_RELOCATIONS, resolve_live_source  # stale-ok: guarded script-execution fallback after explicit dir insert


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "audits" / "legfix5" / "decay_propagation"
BASELINE_MANIFEST = (
    ROOT
    / "audits"
    / "legfix5"
    / "production_baseline_full_downstream"
    / "baseline_manifest.json"
)
STAGED = {"116": 0.16, "117": 0.33, "118": 0.66, "119": 1.0}
FROZEN = {"116": 0.512, "117": 0.64, "118": 0.8, "119": 1.0}
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class DecayAuditError(RuntimeError):
    """The audit could not prove a required decay or freeze invariant."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": relative(resolved),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def literal_assignment(path: Path, name: str) -> Any:
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    found: list[Any] = []
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                found.append(ast.literal_eval(node.value))
    if len(found) != 1:
        raise DecayAuditError(
            f"{relative(path)} must assign {name} exactly once; found {len(found)}"
        )
    return found[0]


def normalize_schedule(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        raise DecayAuditError("decay schedule is not a mapping")
    try:
        return {
            str(int(key)): float(number)
            for key, number in sorted(value.items(), key=lambda item: int(item[0]))
        }
    except (TypeError, ValueError) as exc:
        raise DecayAuditError("decay schedule is malformed") from exc


def source_contract(
    *,
    name: str,
    path: str,
    variable: str | None,
    expected: dict[str, float] | None,
    required_tokens: Iterable[str],
    scope: str,
) -> dict[str, Any]:
    source = ROOT / path
    text = source.read_text(encoding="utf-8-sig")
    missing = [token for token in required_tokens if token not in text]
    observed = (
        normalize_schedule(literal_assignment(source, variable))
        if variable is not None
        else None
    )
    passed = not missing and (expected is None or observed == expected)
    return {
        "name": name,
        "scope": scope,
        "source": receipt(source),
        "schedule_variable": variable,
        "observed_schedule": observed,
        "expected_schedule": expected,
        "required_tokens_present": not missing,
        "missing_tokens": missing,
        "passed": passed,
    }


def staged_source_contracts() -> list[dict[str, Any]]:
    specs = (
        (
            "review_export_annotation",
            "fec/legislation/reconcile_congress_advanced_pages.py",
            "DECAY_WEIGHT",
            STAGED,
            ("LEGACY_DECAY_WEIGHT", '"affects_collection_or_exclusion": False'),
            "metadata only; discovery/review decisions do not consume decay",
        ),
        (
            "accepted_bill_relationships",
            "fec/legislation/build_accepted_legislation_generation.py",
            "DECAY_WEIGHT",
            STAGED,
            (
                "decayed_scenarios",
                "round(weight * effective_weight, 12)",
                '"measure_weight_policy": measure_weight_policy()',
                '"decay_policy"',
            ),
            "direct staged bill scoring",
        ),
        (
            "approved_generation_assembly_gate",
            "fec/legislation/assemble_approved_legislation_generation.py",
            "EXPECTED_DECAY",
            STAGED,
            (
                "accepted generation does not use 1/.66/.33/.16",
                "numeric plan decay policy mismatch",
            ),
            "hash/approval gate; applies no second decay",
        ),
        (
            "base_cosponsor_network",
            "fec/legislation/build_legislation_base_network.py",
            "DECAY",
            STAGED,
            (
                "role_factor * effective / denominator",
                '"measure_weight": measure_weight_policy()',
                '"later_cosponsor": (',
                '"original_cosponsor": (',
            ),
            "direct staged network edge scoring",
        ),
        (
            "base_network_adapter_and_bipartisanship",
            "fec/legislation/materialize_legislation_base_and_bipartisan.py",
            "EXPECTED_DECAY",
            STAGED,
            (
                '"congress_decay_already_embedded": EXPECTED_DECAY',
                '"adapter_applies_no_additional_decay": True',
            ),
            "inherits decayed base weights; applies no second decay",
        ),
        (
            "official_rollcall_diagnostic",
            "fec/legislation/materialize_legislation_rollcall_aggregate.py",
            "DECAY",
            STAGED,
            (
                '"decayed_lean_is_diagnostic_only": True',
                '"lean_enforces_bill_1_amendment_0_5_measure_weight": True',
                '"measure_weight_policy": measure_weight_policy()',
                '"sum(aligned*measure_weight)/sum(measure_weight)"',
            ),
            (
                "direct bill/amendment weighting in canonical lean; Congress "
                "decay remains a named diagnostic"
            ),
        ),
        (
            "workbook_numeric_plan",
            "fec/legislation/build_legislation_workbook_numeric_plan.py",
            "EXPECTED_DECAY",
            STAGED,
            (
                '"applied_to": ["accepted_bill_rows", "manual_LTR_rows"]',
                'denominator = sum(float(item["decay"]) for item in eligible)',
                'float(item["sign"]) * float(item["decay"])',
                '"stored_decay_mismatch_count"',
            ),
            "direct bills, manual LTR units, and legislative efficiency",
        ),
        (
            "accepted_archive_and_workbook_materialization",
            "fec/legislation/build_accepted_legislation_workbook.py",
            "EXPECTED_DECAY",
            STAGED,
            (
                "round(sign_value * role_weight * effective_weight, 12)",
                '"measure_weight_policy": measure_weight_policy()',
                '"unit": "decay_weighted_sign_on_letter"',
                '"activity_decay_weight": activity_weight',
            ),
            "direct archive scoring and staged workbook input",
        ),
        (
            "evaluated_workbook_adapter",
            "fec/legislation/recompute_legislative_workbook.py",
            "EXPECTED_DECAY",
            STAGED,
            (
                "under 1/.66/.33/.16 decay",
                "numeric write plan must recompute CU/CW",
            ),
            "validates precomputed writes; applies no second decay",
        ),
    )
    return [
        source_contract(
            name=name,
            path=path,
            variable=variable,
            expected=expected,
            required_tokens=tokens,
            scope=scope,
        )
        for name, path, variable, expected, tokens, scope in specs
    ]


def downstream_consumers() -> list[dict[str, Any]]:
    """The complete 16-stage shadow chain with explicit decay semantics."""
    rows = [
        (
            "materialize_archive_and_legislative_workbook",
            "direct",
            "Bills and archive contributions use staged decay; CU/CW manual LTR units and efficiency are rebuilt under it.",
            ["fec/legislation/build_accepted_legislation_workbook.py"],
        ),
        (
            "materialize_rollcall_aggregate",
            "mixed",
            "Canonical lean enforces bill=1/amendment=.5; the separately named diagnostic also applies staged Congress decay, while n remains a raw recorded-vote count.",
            ["fec/legislation/materialize_legislation_rollcall_aggregate.py"],
        ),
        (
            "materialize_base_network_and_prebehavior_bipartisan",
            "inherited",
            "Base edges already contain staged decay; bipartisanship consumes those weights and the adapter applies no second decay.",
            ["fec/legislation/materialize_legislation_base_and_bipartisan.py"],
        ),
        (
            "recompute_behavior_panel",
            "mixed",
            "Bipartisanship inherits decayed edge weights; sponsorship counts and unweighted graph degree intentionally have no Congress decay.",
            ["fec/members/behavior_panel.py"],
        ),
        (
            "recompute_behavioral_dispositions",
            "mixed",
            "Cooperation inherits decayed bipartisanship; activity counts and legislative breadth intentionally remain counts.",
            ["fec/members/behavioral_dispositions.py"],
        ),
        (
            "recompute_cooperation",
            "mixed",
            "c1 inherits decayed bipartisanship; c2 cross-party share and c3 reciprocity intentionally remain relationship shares.",
            ["fec/members/coop_v2_candidate.py"],
        ),
        (
            "recompute_receptivity",
            "inherited",
            "Consumes recomputed behavioral dispositions/cooperation; no additional Congress decay is appropriate.",
            ["fec/members/receptivity_layer.py"],
        ),
        (
            "recompute_augmented_network",
            "mixed",
            "Cosponsor edges are copied verbatim with staged decay already embedded; nonlegislative added layers intentionally have no Congress decay.",
            ["fec/network/build_multilayer_network.py"],
        ),
        (
            "recompute_category_stance",
            "mixed",
            "Legislation input inherits staged-decayed archive scores; roll-call lean enforces bill=1/amendment=.5 while its Congress-decayed variant remains diagnostic.",
            ["fec/stance/per_category_stance.py"],
        ),
        (
            "recompute_say_do",
            "intentionally_absent",
            "Preserved say/do DO is role_weight x advancement_weight with no Congress term; adding one would change the approved consumer-specific method.",
            ["fec/stance/say_do_gap.py", "fec/legislation/run_legislation_say_do_first_pass.py"],
        ),
        (
            "recompute_legislative_raw_normalized_sublayer",
            "direct",
            "Consumes the staged numeric plan for bills, LTR rows, efficiency, normalization, and sublayers.",
            ["fec/legislation/recompute_legislative_workbook.py"],
        ),
        (
            "recompute_pillars_and_ranks",
            "inherited",
            "Consumes the evaluated workbook; the legislation pillar inherits staged decay without a second decay.",
            ["fec/legislation/run_legislation_pillars_and_ranks.py", "fec/engine/compute_pillars.py"],
        ),
        (
            "recompute_coalitions_flips_budget_optimizer_allocation",
            "inherited",
            "Consumes recomputed stance, network, workbook, and ranks; applies no separate Congress-age term.",
            ["fec/legislation/run_legislation_network_dependents.py"],
        ),
        (
            "recompute_dashboard_and_member_desk",
            "inherited",
            "Displays/joins recomputed archive, stance, network, workbook, and rank outputs; applies no separate decay.",
            ["fec/legislation/run_legislation_dashboard_member_desk.py", "fec/surfaces/make_member_desk.py"],
        ),
        (
            "recompute_say_do_second_pass",
            "intentionally_absent",
            "Re-runs the same preserved say/do formula after the final desk; Congress decay remains intentionally absent.",
            ["fec/stance/say_do_gap.py", "fec/legislation/run_legislation_say_do_second_pass.py"],
        ),
        (
            "recompute_second_pass_dependents",
            "inherited",
            "Consumes hash-bound final say/do, stance, network, workbook, and ranks; applies no new Congress decay.",
            ["fec/legislation/run_legislation_second_pass_dependents.py"],
        ),
    ]
    result = []
    for name, mode, explanation, files in rows:
        result.append(
            {
                "stage": name,
                "mode": mode,
                "explanation": explanation,
                "sources": [receipt(ROOT / item) for item in files],
            }
        )
    return result


def _xlsx_shared_strings(book: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    return ["".join(node.itertext()) for node in root]


def _xlsx_sheet_path(book: zipfile.ZipFile, sheet_name: str) -> str:
    ns = {
        "x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "p": "http://schemas.openxmlformats.org/package/2006/relationships",
    }
    workbook = ET.fromstring(book.read("xl/workbook.xml"))
    rels = ET.fromstring(book.read("xl/_rels/workbook.xml.rels"))
    relation = {
        node.attrib["Id"]: node.attrib["Target"]
        for node in rels.findall("p:Relationship", ns)
    }
    for node in workbook.findall("x:sheets/x:sheet", ns):
        if node.attrib["name"] == sheet_name:
            rid = node.attrib[
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            ]
            target = relation[rid].replace("\\", "/")
            if target.startswith("/"):
                return target.lstrip("/")
            return os.path.normpath("xl/" + target).replace("\\", "/")
    raise DecayAuditError(f"XLSX sheet is absent: {sheet_name}")


def xlsx_rows(path: Path, sheet_name: str) -> list[dict[str, Any]]:
    main_ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(path) as book:
        shared = _xlsx_shared_strings(book)
        sheet = ET.fromstring(book.read(_xlsx_sheet_path(book, sheet_name)))
    rows: list[dict[str, Any]] = []
    for row in sheet.iter(main_ns + "row"):
        values: dict[str, Any] = {}
        for cell in row.findall(main_ns + "c"):
            column = re.match(r"[A-Z]+", cell.attrib["r"])
            if column is None:
                continue
            value_node = cell.find(main_ns + "v")
            inline_node = cell.find(main_ns + "is")
            value: Any = None
            if cell.attrib.get("t") == "s" and value_node is not None:
                value = shared[int(value_node.text or "0")]
            elif cell.attrib.get("t") == "inlineStr" and inline_node is not None:
                value = "".join(inline_node.itertext())
            elif value_node is not None:
                value = value_node.text
            values[column.group()] = value
        rows.append(values)
    return rows


def archive_decay_evidence(path: Path) -> dict[str, Any]:
    rows = xlsx_rows(path, "All Bills")
    if not rows:
        raise DecayAuditError("frozen production archive has no All Bills rows")
    header = {str(value): column for column, value in rows[0].items()}
    congress_column = header.get("Congress")
    decay_column = header.get("DecayWeight")
    if not congress_column or not decay_column:
        raise DecayAuditError("frozen archive lacks Congress/DecayWeight columns")
    observed: dict[str, set[float]] = {}
    for row in rows[1:]:
        if row.get(congress_column) in (None, ""):
            continue
        congress = str(int(float(row[congress_column])))
        observed.setdefault(congress, set()).add(float(row[decay_column]))
    normalized = {
        key: sorted(round(value, 12) for value in values)
        for key, values in sorted(observed.items())
    }
    mismatch = {
        key: values
        for key, values in normalized.items()
        if values != [FROZEN[key]]
    }
    return {
        "file": receipt(path),
        "row_count_including_header": len(rows),
        "observed_decay_by_congress": normalized,
        "expected_frozen_schedule": FROZEN,
        "mismatches": mismatch,
        "passed": not mismatch,
    }


def manual_ltr_evidence(path: Path) -> dict[str, Any]:
    by_congress: dict[str, set[float]] = {}
    total = 0
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if not str(row.get("Bill") or "").strip().upper().startswith("LTR-"):
                continue
            total += 1
            congress = str(int(row["Congress"]))
            by_congress.setdefault(congress, set()).add(float(row["DecayWeight"]))
    observed = {
        key: sorted(values) for key, values in sorted(by_congress.items())
    }
    prior_schedule_stored = all(
        values == [FROZEN[key]] for key, values in observed.items()
    )
    return {
        "file": receipt(path),
        "LTR_row_count": total,
        "stored_decay_by_congress": observed,
        "stored_values_match_prior_live_schedule": prior_schedule_stored,
        "stored_values_are_not_staged_authority": True,
        "staged_canonicalization_source": receipt(
            ROOT / "fec" / "legislation" / "build_legislation_workbook_numeric_plan.py"
        ),
        "staged_schedule_applied": STAGED,
        "passed": total > 0 and prior_schedule_stored,
    }


def production_freeze_audit() -> dict[str, Any]:
    manifest = read_json(BASELINE_MANIFEST)
    rows = manifest.get("files")
    if (
        manifest.get("schema") != "legislation-production-baseline-v1"
        or not isinstance(rows, list)
        or manifest.get("file_count") != len(rows)
    ):
        raise DecayAuditError("frozen production manifest contract is invalid")
    frozen_mismatches = []
    live_drift = []
    checked = []
    by_name: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows):
        recorded_source = Path(row["source"])
        if not recorded_source.is_absolute():
            recorded_source = ROOT / recorded_source
        recorded_source = recorded_source.resolve()
        # 2026-08-05: resolve every frozen-manifest live read through the shared
        # Phase 2+3 relocation policy while retaining the manifest identity.
        source = resolve_live_source(recorded_source, ROOT)
        copy = Path(row["copy"]).resolve()
        expected = str(row["sha256"]).casefold()
        source_hash = sha256_file(source) if source.is_file() else None
        copy_hash = sha256_file(copy) if copy.is_file() else None
        copy_size = copy.stat().st_size if copy.is_file() else None
        source_size = source.stat().st_size if source.is_file() else None
        frozen_copy_integrity_passed = (
            HEX_RE.fullmatch(expected) is not None
            and copy_hash == expected
            and copy_size == row["size"]
        )
        live_source_matches_frozen = (
            frozen_copy_integrity_passed
            and source_hash == expected
            and source_size == row["size"]
        )
        item = {
            "index": index,
            "source": relative(recorded_source),
            "live_source": relative(source),
            "live_source_relocated": source != recorded_source,
            "live_relocation": LIVE_RELOCATIONS.get(recorded_source.name),
            "copy": relative(copy),
            "expected_sha256": expected,
            "expected_size": row["size"],
            "source_sha256": source_hash,
            "copy_sha256": copy_hash,
            "source_size": source_size,
            "copy_size": copy_size,
            "frozen_copy_integrity_passed": frozen_copy_integrity_passed,
            "live_source_matches_frozen": live_source_matches_frozen,
            "live_source_drift_observed": not live_source_matches_frozen,
            # Retained for the independently sealed downstream-drift audit.
            "passed": live_source_matches_frozen,
        }
        checked.append(item)
        by_name[recorded_source.name] = item
        if not frozen_copy_integrity_passed:
            frozen_mismatches.append(item)
        if not live_source_matches_frozen:
            live_drift.append(item)
    live_rules = ROOT / "fec" / "bill_rules.py"
    frozen_rules = (
        ROOT
        / "audits"
        / "legfix5"
        / "production_baseline_full_downstream"
        / "files"
        / "fec"
        / "bill_rules.py"
    )
    factors = {
        "live": float(literal_assignment(live_rules, "CONGRESS_DECAY_FACTOR")),
        "frozen_copy": float(
            literal_assignment(frozen_rules, "CONGRESS_DECAY_FACTOR")
        ),
    }
    computed = {
        str(congress): round(factors["live"] ** (119 - congress), 12)
        for congress in (116, 117, 118, 119)
    }
    archive_item = next(
        item
        for item in checked
        if Path(item["copy"]).name.startswith("congress_bills_archive_")
    )
    frozen_archive = archive_decay_evidence(ROOT / archive_item["copy"])
    live_archive = archive_decay_evidence(
        ROOT / archive_item["live_source"]
    )
    frozen_rules_passed = factors["frozen_copy"] == 0.8
    live_rules_preserve_frozen_schedule = (
        factors["live"] == 0.8 and computed == FROZEN
    )
    no_cutover_proved = (
        live_rules_preserve_frozen_schedule
        and live_archive["passed"]
        and frozen_rules_passed
        and frozen_archive["passed"]
    )
    frozen_integrity_passed = not frozen_mismatches
    return {
        "manifest": receipt(BASELINE_MANIFEST),
        "declared_file_count": len(rows),
        "frozen_copy_hash_match_count": sum(
            item["frozen_copy_integrity_passed"] for item in checked
        ),
        "frozen_copy_mismatches": frozen_mismatches,
        "frozen_baseline_integrity_passed": frozen_integrity_passed,
        "live_source_match_count": sum(
            item["live_source_matches_frozen"] for item in checked
        ),
        "live_source_drift_count": len(live_drift),
        "live_source_drift": live_drift,
        "live_source_drift_is_not_baseline_tampering": True,
        # Compatibility fields retain their literal byte-identity meaning.
        "source_and_copy_hash_match_count": sum(item["passed"] for item in checked),
        "source_and_copy_mismatches": live_drift,
        "all_live_sources_and_frozen_copies_match_manifest": not live_drift,
        "bill_rules": {
            "live": receipt(live_rules),
            "frozen_copy": receipt(frozen_rules),
            "factors": factors,
            "computed_schedule": computed,
            "expected_frozen_schedule": FROZEN,
            "frozen_copy_passed": frozen_rules_passed,
            "live_source_preserves_frozen_schedule": (
                live_rules_preserve_frozen_schedule
            ),
            "passed": (
                frozen_rules_passed and live_rules_preserve_frozen_schedule
            ),
        },
        "archive_decay_column": frozen_archive,
        "live_archive_decay_column": live_archive,
        "production_no_cutover_gate_passed": no_cutover_proved,
        "production_cutover_to_staged_schedule_detected": False
        if no_cutover_proved
        else None,
        "checked_files": checked,
        "passed": frozen_integrity_passed and no_cutover_proved,
    }


def build_audit() -> dict[str, Any]:
    producer = receipt(Path(__file__))
    staged = staged_source_contracts()
    consumers = downstream_consumers()
    production = production_freeze_audit()
    manual = manual_ltr_evidence(ROOT / "manual_bill_evidence.csv")
    staged_old_schedule_users = [
        item["name"] for item in staged if not item["passed"]
    ]
    consumer_names = [item["stage"] for item in consumers]
    blockers = []
    if staged_old_schedule_users:
        blockers.append(
            "staged source contract mismatch: "
            + ", ".join(staged_old_schedule_users)
        )
    if len(consumers) != 16 or len(set(consumer_names)) != 16:
        blockers.append("16-stage downstream ledger is incomplete or duplicated")
    if not production["frozen_baseline_integrity_passed"]:
        blockers.append("immutable frozen production baseline integrity failed")
    if not production["production_no_cutover_gate_passed"]:
        blockers.append("production decay cutover state is not proven safe")
    if not manual["passed"]:
        blockers.append("manual LTR stored-weight provenance is inconsistent")
    source_hashes = sorted(
        {
            item["source"]["sha256"] for item in staged
        }
        | {
            source["sha256"]
            for item in consumers
            for source in item["sources"]
        }
        | {
            production["manifest"]["sha256"],
            manual["file"]["sha256"],
            producer["sha256"],
        }
    )
    result = {
        "schema": "legislation-congress-decay-propagation-audit-v1",
        "status": "PASS" if not blockers else "FAIL",
        "staging_only": True,
        "read_only_source_audit": True,
        "collector_executed": False,
        "production_or_mainframe_written": False,
        "promotion_authorized": False,
        "producer": producer,
        "schedules": {
            "staged_unapproved": STAGED,
            "frozen_live": FROZEN,
            "schedules_equal": False,
        },
        "staged_producer_contracts": staged,
        "manual_LTR_evidence": manual,
        "downstream_consumer_ledger": consumers,
        "staged_consumer_using_old_schedule": staged_old_schedule_users,
        "production_baseline": production,
        "summary": {
            "staged_contract_count": len(staged),
            "staged_contract_pass_count": sum(item["passed"] for item in staged),
            "downstream_stage_count": len(consumers),
            "direct_stage_count": sum(item["mode"] == "direct" for item in consumers),
            "mixed_stage_count": sum(item["mode"] == "mixed" for item in consumers),
            "inherited_stage_count": sum(item["mode"] == "inherited" for item in consumers),
            "intentionally_absent_stage_count": sum(
                item["mode"] == "intentionally_absent" for item in consumers
            ),
            "production_file_count": production["declared_file_count"],
            "production_file_match_count": production[
                "frozen_copy_hash_match_count"
            ],
            "production_live_source_match_count": production[
                "live_source_match_count"
            ],
            "production_live_source_drift_count": production[
                "live_source_drift_count"
            ],
            "staged_old_schedule_consumer_count": len(
                staged_old_schedule_users
            ),
            "production_cutover_detected": production[
                "production_cutover_to_staged_schedule_detected"
            ],
        },
        "acceptance_context": {
            "snapshot_only": True,
            "authoritative_for_policy_approval": False,
            "authoritative_for_shadow_or_sync": False,
            "decay_propagation_static_gate_passed": not blockers,
            "decay_policy_approved": False,
            "real_shadow_run_complete": False,
            "sync_allowed": False,
        },
        "blocking_issues": blockers,
        "audit_basis": {
            "source_sha256": source_hashes,
            "source_count": len(source_hashes),
        },
    }
    result["audit_basis"]["sha256"] = hashlib.sha256(
        canonical_bytes(
            {
                "schema": result["schema"],
                "schedules": result["schedules"],
                "source_sha256": source_hashes,
                "consumer_modes": [
                    [item["stage"], item["mode"]] for item in consumers
                ],
            }
        )
    ).hexdigest()
    return result


def markdown_report(audit: dict[str, Any]) -> str:
    summary = audit["summary"]
    lines = [
        "# Congress decay propagation audit",
        "",
        f"- Static audit status: **{audit['status']}**",
        "- Staged schedule: `119/118/117/116 = 1/.66/.33/.16`",
        "- Frozen production schedule: `119/118/117/116 = 1/.8/.64/.512`",
        (
            f"- Staged producer contracts: "
            f"**{summary['staged_contract_pass_count']}/"
            f"{summary['staged_contract_count']}**"
        ),
        (
            f"- Frozen copies matching the immutable manifest: "
            f"**{summary['production_file_match_count']}/"
            f"{summary['production_file_count']}**"
        ),
        (
            f"- Live files byte-identical to the frozen snapshot: "
            f"**{summary['production_live_source_match_count']}/"
            f"{summary['production_file_count']}**"
        ),
        (
            f"- Explicitly retained live-drift rows: "
            f"**{summary['production_live_source_drift_count']}**"
        ),
        (
            f"- Staged old-schedule consumers: "
            f"**{summary['staged_old_schedule_consumer_count']}**"
        ),
        (
            f"- Production cutover detected: "
            f"**{'YES' if summary['production_cutover_detected'] else 'NO'}**"
        ),
        "- Promotion/sync authorized: **NO**",
        "",
        "## Downstream ledger",
        "",
        "| Stage | Decay mode | Contract |",
        "|---|---|---|",
    ]
    for item in audit["downstream_consumer_ledger"]:
        lines.append(
            f"| `{item['stage']}` | `{item['mode']}` | "
            f"{item['explanation']} |"
        )
    lines.extend(
        [
            "",
            "## Important intentional absences",
            "",
            "- Official roll-call event collection is factual and undecayed. Canonical `lean` enforces bill=1/amendment=.5, `n` stays a raw vote count, and only the explicitly named diagnostic additionally uses Congress decay.",
            "- Behavior sponsorship counts, graph degree, breadth, cross-party share, and reciprocity remain counts/shares. Decayed network weights still propagate through bipartisanship.",
            "- Say/do preserves `role_weight × advancement_weight` with no Congress term in both passes. Adding decay there would be a separate method change.",
            "- Direction, culling, relationship visibility, and exclusions never consume decay.",
            "",
            "The static propagation gate passing does not approve the staged schedule, complete coverage, execute a real shadow run, or permit promotion.",
            "",
        ]
    )
    return "\n".join(lines)


def write_artifacts(out: Path, audit: dict[str, Any]) -> None:
    out = out.resolve()
    allowed = (ROOT / "audits" / "legfix5").resolve()
    try:
        out.relative_to(allowed)
    except ValueError as exc:
        raise DecayAuditError(
            "output must remain below audits/legfix5"
        ) from exc
    out.mkdir(parents=True, exist_ok=True)
    (out / "decay_propagation_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out / "DECAY_PROPAGATION_AUDIT.md").write_text(
        markdown_report(audit), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser.parse_args()


def main() -> int:
    try:
        audit = build_audit()
        write_artifacts(parse_args().out, audit)
    except (DecayAuditError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        print(f"Congress decay propagation audit: FAIL CLOSED: {exc}")
        return 2
    print(
        "Congress decay propagation audit: "
        f"{audit['status']} "
        f"({audit['summary']['staged_contract_pass_count']}/"
        f"{audit['summary']['staged_contract_count']} staged contracts; "
        f"{audit['summary']['production_file_match_count']}/"
        f"{audit['summary']['production_file_count']} frozen/live files)"
    )
    return 0 if audit["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
