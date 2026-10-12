# -*- coding: utf-8 -*-
"""Audit downstream consumer isolation and emit a runnable plan only if complete.

This module never executes a consumer.  Its sole mutable targets are
``consumer_plan.json`` and ``consumer_isolation_audit.json`` below the
legislation shadow-recompute directory (or a temporary directory in tests).
Every producer, exact run-relative output, baseline-role owner, and spreadsheet
capability receipt must be present before a runnable plan is emitted.
"""
from __future__ import annotations

import os as _bootstrap_os, sys as _bootstrap_sys; _bootstrap_sys.path.insert(0, _bootstrap_os.path.dirname(_bootstrap_os.path.dirname(_bootstrap_os.path.abspath(__file__))))

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import string
import sys
import tempfile
from typing import Any, Iterable, Sequence

from legislation import run_legislation_shadow_recompute as SHADOW


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "audits" / "legfix5" / "shadow_recompute"
PLAN_PATH_NAME = "consumer_plan.json"
AUDIT_PATH_NAME = "consumer_isolation_audit.json"
AUDIT_SCHEMA = "legislation-shadow-consumer-isolation-audit-v1"
CAPABILITIES_SCHEMA = "legislation-shadow-consumer-capabilities-v1"
APPROVED_PLACEHOLDERS = frozenset(
    {
        "run_dir",
        "workspace_root",
        "input_root",
        "accepted_input_dir",
        "direction_input_dir",
        "base_network_input_dir",
        "rollcall_input_dir",
    }
)
SHELL_OPERATORS = frozenset({"|", "||", "&&", ";", ">", ">>", "<"})


DEFAULT_PRODUCERS = {
    "materialize_archive_and_legislative_workbook": (
        "fec/legislation/build_accepted_legislation_workbook.py"
    ),
    "materialize_rollcall_aggregate": (
        "fec/legislation/materialize_legislation_rollcall_aggregate.py"
    ),
    "materialize_base_network_and_prebehavior_bipartisan": (
        "fec/legislation/materialize_legislation_base_and_bipartisan.py"
    ),
    "recompute_behavior_panel": "fec/members/behavior_panel.py",
    "recompute_behavioral_dispositions": "fec/members/behavioral_dispositions.py",
    "recompute_cooperation": "fec/members/coop_v2_candidate.py",
    "recompute_receptivity": "fec/members/receptivity_layer.py",
    "recompute_augmented_network": "fec/network/build_multilayer_network.py",
    "recompute_category_stance": "fec/stance/per_category_stance.py",
    "recompute_say_do": "fec/legislation/run_legislation_say_do_first_pass.py",
    "recompute_legislative_raw_normalized_sublayer": (
        "fec/legislation/recompute_legislative_workbook.py"
    ),
    "recompute_pillars_and_ranks": (
        "fec/legislation/run_legislation_pillars_and_ranks.py"
    ),
    "recompute_coalitions_flips_budget_optimizer_allocation": (
        "fec/legislation/run_legislation_network_dependents.py"
    ),
    "recompute_dashboard_and_member_desk": (
        "fec/legislation/run_legislation_dashboard_member_desk.py"
    ),
    "recompute_say_do_second_pass": (
        "fec/legislation/run_legislation_say_do_second_pass.py"
    ),
    "recompute_second_pass_dependents": (
        "fec/legislation/run_legislation_second_pass_dependents.py"
    ),
}

DEFAULT_SPREADSHEET_IMPORT_RECEIPT = Path(
    "audits/legfix5/spreadsheet_engine_verification/import_receipt.json"
)
DEFAULT_ACCEPTED_WORKBOOK_PRODUCER_VERIFICATION = Path(
    "audits/legfix5/spreadsheet_engine_verification/"
    "accepted_workbook_producer_capability.json"
)
DEFAULT_ACCEPTED_WORKBOOK_HELPER = Path(
    "fec/build_accepted_legislation_workbook.cjs"
)
DEFAULT_BASELINE_MANIFEST = Path(
    "audits/legfix5/production_baseline_full_downstream/"
    "baseline_manifest.json"
)
BASELINE_WORKBOOK_NAME = "Moneyball Main Version - Blended.xlsx"
ACCEPTED_WORKBOOK_CAPABILITY_SCHEMA = (
    "accepted-legislation-workbook-producer-capability-v1"
)

DEFAULT_ISOLATED_OUTPUTS = {
    "materialize_archive_and_legislative_workbook": [
        (
            "congress_bills_archive_shadow.xlsx",
            "legislation_archive_workbook",
        ),
        ("archive_leg.json", "archive_leg"),
        ("legislation_routing_audit.json", "legislation_routing_audit"),
        (
            "intermediate/accepted_legislation_workbook.receipt.json",
            None,
        ),
    ],
    "materialize_rollcall_aggregate": [
        ("vote_category_lean.json", "legacy_rollcall_aggregate"),
        ("official_rollcall_category_cells.ndjson", None),
        ("official_rollcall_category_contributions.ndjson", None),
        ("official_rollcall_event_decisions.ndjson", None),
        ("official_rollcall_member_vote_decisions.ndjson", None),
        ("official_rollcall_measure_decisions.ndjson", None),
        ("official_rollcall_member_identity_audit.ndjson", None),
        ("rollcall_consumer_compatibility.json", None),
        ("rollcall_aggregate_manifest.json", None),
    ],
    "materialize_base_network_and_prebehavior_bipartisan": [
        ("congress_cosponsor_network_shadow.xlsx", "base_network"),
        ("bipartisan.json", "bipartisan"),
        ("bipartisan_by_bioguide.json", None),
        ("bipartisan_member_audit.ndjson", None),
        ("bipartisan_edge_exclusions.ndjson", None),
        ("base_bipartisan_consumer_compatibility.json", None),
        ("base_bipartisan_manifest.json", None),
    ],
    "recompute_behavior_panel": [
        ("behavior_panel.json", "behavior_panel"),
    ],
    "recompute_behavioral_dispositions": [
        ("behavioral_dispositions.json", "behavioral_dispositions"),
    ],
    "recompute_cooperation": [
        ("coop_v2_candidate.json", "cooperation"),
    ],
    "recompute_receptivity": [
        ("member_receptivity.json", "receptivity"),
    ],
    "recompute_augmented_network": [
        ("congress_cosponsor_network_AUG_shadow.xlsx", "augmented_network"),
    ],
    "recompute_category_stance": [
        ("category_stance.json", "category_stance"),
    ],
    "recompute_say_do": [
        ("intermediate/say_do_first_pass.json", None),
        ("intermediate/say_do_first_pass.receipt.json", None),
    ],
    "recompute_legislative_raw_normalized_sublayer": [
        ("Moneyball Main Version - Blended.xlsx", "blended_workbook"),
        ("legislation_workbook.receipt.json", None),
    ],
    "recompute_say_do_second_pass": [
        ("say_do_gap.json", "say_do"),
        ("intermediate/say_do_second_pass.receipt.json", None),
    ],
    "recompute_pillars_and_ranks": [
        ("data/engine/pillar_inputs_snapshot.json", "pillar_snapshot"),
        ("ranks_python.json", "ranks"),
        ("intermediate/pillar_inputs_first_pass.json", None),
        ("intermediate/ranks_first_pass.json", None),
        ("pillars_ranks.receipt.json", None),
    ],
    "recompute_coalitions_flips_budget_optimizer_allocation": [
        (
            "intermediate/"
            "network_dependents_first_pass.receipt.json",
            None,
        ),
    ],
    "recompute_dashboard_and_member_desk": [
        (
            "intermediate/"
            "dashboard_member_desk_first_pass.receipt.json",
            None,
        ),
    ],
    "recompute_second_pass_dependents": [
        ("data/money/electoral_plays.json", "electoral_plays"),
        ("per_topic_alloc.json", "per_topic_alloc"),
        ("data/lobbying/per_topic_coalition.json", "per_topic_coalition"),
        ("data/money/budget_frontier.json", "budget_frontier"),
        (
            "data/money/budget_frontier_electoral.json",
            "budget_frontier_electoral",
        ),
        ("optimize_result.json", "optimize_result"),
        ("im_allocation.json", "im_allocation"),
        ("salience_advisory.json", "salience_advisory"),
        ("dashboard_data.json", "dashboard_bundle"),
        ("Moneyball Dashboard.html", "dashboard_html"),
        ("member_desk_bundle.json", "member_desk"),
        ("cu-member-desk.html", "member_desk_html"),
        (
            "dashboard_exclusion_reconciliation.json",
            "dashboard_exclusion_reconciliation",
        ),
        ("Moneyball.html", "combined_html"),
        (
            "intermediate/second_pass_dependents.receipt.json",
            None,
        ),
    ],
}


class IsolationError(ValueError):
    """A malformed or unsafe consumer capability."""


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Audit legislation downstream consumer isolation without running "
            "any consumer."
        )
    )
    result.add_argument("--out", type=Path, default=DEFAULT_OUT)
    result.add_argument(
        "--workspace-root",
        type=Path,
        default=ROOT,
        help="Repository root whose producer paths are audited.",
    )
    result.add_argument(
        "--capabilities-manifest",
        type=Path,
        help=(
            "Optional explicit isolation capabilities. Without it, current "
            "repository producers are inspected and missing adapters block."
        ),
    )
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_receipt(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "size": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def atomic_json(path: Path, value: Any) -> None:
    body = json.dumps(
        value, indent=2, sort_keys=True, ensure_ascii=False
    ) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(body, encoding="utf-8", newline="\n")
    os.replace(temporary, path)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def allowed_output_roots() -> tuple[Path, ...]:
    roots = {
        DEFAULT_OUT.resolve(),
        (ROOT / "tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    for name in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            roots.add(Path(value).resolve())
    return tuple(sorted(roots, key=str))


def validate_out(path: Path) -> Path:
    resolved = path.resolve()
    if resolved.suffix:
        raise IsolationError("--out must be a directory")
    if not any(_is_relative_to(resolved, root) for root in allowed_output_roots()):
        raise IsolationError(
            "--out must be audits/legfix5/shadow_recompute or a temp directory"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise IsolationError(f"JSON root must be an object: {path}")
    return value


def placeholders(values: Iterable[str]) -> set[str]:
    found: set[str] = set()
    formatter = string.Formatter()
    for value in values:
        try:
            for _, field, _, _ in formatter.parse(value):
                if field:
                    found.add(field)
        except ValueError as exc:
            raise IsolationError(f"invalid placeholder syntax: {value!r}") from exc
    return found


def safe_relative(value: Any) -> Path:
    path = Path(str(value))
    if not str(value) or path.is_absolute() or ".." in path.parts:
        raise IsolationError(f"unsafe output path: {value!r}")
    return path


def resolve_workspace_path(value: Any, workspace_root: Path) -> Path:
    text = str(value)
    text = text.replace("{workspace_root}", str(workspace_root))
    path = Path(text)
    if not path.is_absolute():
        path = workspace_root / path
    return path.resolve()


def validate_executable(command: list[str], workspace_root: Path) -> list[str]:
    normalized = [sys.executable if token == "{python}" else token for token in command]
    probe_values = {
        "run_dir": str(Path(tempfile.gettempdir()) / "legislation-shadow-probe"),
        "workspace_root": str(workspace_root),
        "input_root": str(
            Path(tempfile.gettempdir()) / "legislation-shadow-probe" / "inputs"
        ),
        "accepted_input_dir": str(
            Path(tempfile.gettempdir())
            / "legislation-shadow-probe"
            / "inputs"
            / "accepted_generation"
        ),
        "direction_input_dir": str(
            Path(tempfile.gettempdir())
            / "legislation-shadow-probe"
            / "inputs"
            / "direction"
        ),
        "base_network_input_dir": str(
            Path(tempfile.gettempdir())
            / "legislation-shadow-probe"
            / "inputs"
            / "base_network"
        ),
        "rollcall_input_dir": str(
            Path(tempfile.gettempdir())
            / "legislation-shadow-probe"
            / "inputs"
            / "rollcall"
        ),
    }
    rendered = [token.format(**probe_values) for token in normalized]
    executable = Path(rendered[0])
    if executable.is_absolute():
        if not executable.is_file():
            raise IsolationError(f"command executable is missing: {executable}")
    elif shutil.which(rendered[0]) is None:
        raise IsolationError(f"command executable is not resolvable: {rendered[0]}")
    return normalized


def validate_global_gate(
    gate: Any,
    *,
    name: str,
    workspace_root: Path,
) -> tuple[bool, dict[str, Any]]:
    if not isinstance(gate, dict) or gate.get("status") != "PASS":
        return False, {"gate": name, "status": "FAIL", "reason": "PASS receipt absent"}
    producer = gate.get("producer")
    evidence = gate.get("verification_receipt")
    expected_hash = str(gate.get("verification_sha256") or "").casefold()
    details: dict[str, Any] = {"gate": name, "status": "FAIL"}
    if producer:
        producer_path = resolve_workspace_path(producer, workspace_root)
        if not producer_path.is_file():
            details["reason"] = f"producer missing: {producer_path}"
            return False, details
        details["producer"] = file_receipt(producer_path)
    if not evidence:
        details["reason"] = "verification_receipt absent"
        return False, details
    evidence_path = resolve_workspace_path(evidence, workspace_root)
    if not evidence_path.is_file():
        details["reason"] = f"verification receipt missing: {evidence_path}"
        return False, details
    actual_hash = sha256_file(evidence_path)
    if expected_hash != actual_hash:
        details["reason"] = "verification receipt hash mismatch"
        details["expected_sha256"] = expected_hash
        details["actual_sha256"] = actual_hash
        return False, details
    try:
        evidence_value = read_json(evidence_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        details["reason"] = f"verification receipt is invalid JSON: {exc}"
        return False, details

    baseline_manifest = workspace_root / DEFAULT_BASELINE_MANIFEST
    if not baseline_manifest.is_file():
        details["reason"] = (
            f"frozen production baseline manifest missing: {baseline_manifest}"
        )
        return False, details
    try:
        baseline_value = read_json(baseline_manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        details["reason"] = f"frozen baseline manifest is invalid: {exc}"
        return False, details
    baseline_rows = baseline_value.get("files")
    if not isinstance(baseline_rows, list):
        details["reason"] = "frozen baseline manifest has no files list"
        return False, details
    workbook_rows = [
        row
        for row in baseline_rows
        if isinstance(row, dict)
        and Path(str(row.get("copy") or row.get("source") or "")).name
        == BASELINE_WORKBOOK_NAME
    ]
    if len(workbook_rows) != 1:
        details["reason"] = (
            "frozen baseline must contain exactly one blended workbook"
        )
        return False, details
    baseline_workbook = Path(
        str(workbook_rows[0].get("copy") or workbook_rows[0].get("source"))
    )
    if not baseline_workbook.is_absolute():
        baseline_workbook = workspace_root / baseline_workbook
    baseline_workbook = baseline_workbook.resolve()
    baseline_sha = str(workbook_rows[0].get("sha256") or "").casefold()
    if (
        not baseline_workbook.is_file()
        or sha256_file(baseline_workbook) != baseline_sha
    ):
        details["reason"] = "frozen blended-workbook copy/hash mismatch"
        return False, details

    contract_issues: list[str] = []
    if name == "approved_spreadsheet_engine_import":
        source = evidence_value.get("input") or {}
        required = {
            "schema": "legislation-spreadsheet-engine-import-verification-v1",
            "status": "PASS",
            "engine": "@oai/artifact-tool",
            "import_completed": True,
            "production_written": False,
            "workbook_edited": False,
        }
        contract_issues.extend(
            f"{key}={evidence_value.get(key)!r}"
            for key, expected in required.items()
            if evidence_value.get(key) != expected
        )
        if source.get("unchanged_after_import") is not True:
            contract_issues.append("input.unchanged_after_import is not true")
        if str(source.get("sha256") or "").casefold() != baseline_sha:
            contract_issues.append(
                "import input is not hash-bound to frozen blended workbook"
            )
        if evidence_value.get("missing_required_sheets") != []:
            contract_issues.append("required workbook sheets are missing")
    elif name == "accepted_ledger_workbook_producer":
        producer_receipt = details.get("producer")
        helper_path = (
            workspace_root / DEFAULT_ACCEPTED_WORKBOOK_HELPER
        ).resolve()
        smoke = evidence_value.get("smoke_test") or {}
        runtime = evidence_value.get("runtime") or {}
        required = {
            "schema": ACCEPTED_WORKBOOK_CAPABILITY_SCHEMA,
            "status": "PASS",
            "approved_engine": "@oai/artifact-tool",
            "staged_only": True,
            "production_written": False,
        }
        contract_issues.extend(
            f"{key}={evidence_value.get(key)!r}"
            for key, expected in required.items()
            if evidence_value.get(key) != expected
        )
        if (
            str(evidence_value.get("producer_sha256") or "").casefold()
            != str((producer_receipt or {}).get("sha256") or "").casefold()
        ):
            contract_issues.append(
                "capability receipt is not bound to current producer source"
            )
        if not helper_path.is_file():
            contract_issues.append(
                "current workbook helper source is missing"
            )
        elif (
            str(evidence_value.get("helper_sha256") or "").casefold()
            != sha256_file(helper_path)
        ):
            contract_issues.append(
                "capability receipt is not bound to current workbook "
                "helper source"
            )
        if (
            str(evidence_value.get("baseline_input_sha256") or "").casefold()
            != baseline_sha
        ):
            contract_issues.append(
                "capability smoke input is not the frozen blended workbook"
            )
        if smoke.get("passed") is not True:
            contract_issues.append("artifact-tool smoke test did not pass")
        if smoke.get("required_sheets_present") is not True:
            contract_issues.append("smoke test did not verify required sheets")
        if smoke.get("composite_rebind") is True:
            source_tests = smoke.get("source_contract_tests") or {}
            if not (
                smoke.get("helper_source_unchanged") is True
                and str(smoke.get("producer_only_change") or "").strip()
                and source_tests.get("passed") is True
                and isinstance(source_tests.get("tests_run"), int)
                and source_tests["tests_run"] > 0
            ):
                contract_issues.append(
                    "composite capability rebind lacks unchanged-helper and "
                    "passing source-contract-test evidence"
                )
        runtime_node = Path(str(runtime.get("artifact_tool_node") or ""))
        runtime_modules = Path(
            str(runtime.get("artifact_tool_node_modules") or "")
        )
        if not runtime_node.is_absolute() or not runtime_node.is_file():
            contract_issues.append(
                "loader-grounded artifact-tool Node runtime is absent"
            )
        if (
            not runtime_modules.is_absolute()
            or not runtime_modules.is_dir()
        ):
            contract_issues.append(
                "loader-grounded artifact-tool node_modules is absent"
            )
        for field in ("output_workbook", "inspection"):
            item = evidence_value.get(field)
            if not isinstance(item, dict):
                contract_issues.append(f"{field} receipt absent")
                continue
            item_path = resolve_workspace_path(item.get("path"), workspace_root)
            item_sha = str(item.get("sha256") or "").casefold()
            if (
                not item_path.is_file()
                or sha256_file(item_path) != item_sha
                or (
                    item.get("size") is not None
                    and item_path.stat().st_size != int(item["size"])
                )
            ):
                contract_issues.append(f"{field} path/hash/size mismatch")
            allowed = (workspace_root / "audits" / "legfix5").resolve()
            if not _is_relative_to(item_path, allowed):
                contract_issues.append(
                    f"{field} is not retained under audits/legfix5"
                )
    else:
        contract_issues.append(f"unknown global gate contract: {name}")

    if contract_issues:
        details.update(
            {
                "reason": "verification receipt contract failed",
                "contract_issues": contract_issues,
                "verification": file_receipt(evidence_path),
            }
        )
        return False, details
    details.update(
        {
            "status": "PASS",
            "verification": file_receipt(evidence_path),
            "frozen_workbook": file_receipt(baseline_workbook),
        }
    )
    if name == "accepted_ledger_workbook_producer":
        details["helper"] = file_receipt(
            (workspace_root / DEFAULT_ACCEPTED_WORKBOOK_HELPER).resolve()
        )
    return True, details


def default_capabilities(workspace_root: Path) -> dict[str, Any]:
    workbook_runtime: dict[str, str] = {}
    producer_receipt = (
        workspace_root
        / DEFAULT_ACCEPTED_WORKBOOK_PRODUCER_VERIFICATION
    )
    if producer_receipt.is_file():
        try:
            capability = read_json(producer_receipt)
        except (OSError, ValueError, json.JSONDecodeError):
            capability = {}
        runtime = capability.get("runtime") or {}
        node = Path(str(runtime.get("artifact_tool_node") or ""))
        modules = Path(
            str(runtime.get("artifact_tool_node_modules") or "")
        )
        if node.is_absolute() and node.is_file():
            workbook_runtime["ARTIFACT_TOOL_NODE"] = str(node)
        if modules.is_absolute() and modules.is_dir():
            workbook_runtime["ARTIFACT_TOOL_NODE_MODULES"] = str(modules)
    steps: list[dict[str, Any]] = []
    for name in SHADOW.REQUIRED_STEP_ORDER:
        producer = DEFAULT_PRODUCERS[name]
        producer_path = workspace_root / producer
        root_aware = False
        if producer_path.is_file():
            text = producer_path.read_text(encoding="utf-8-sig", errors="replace")
            root_aware = "from paths import ROOT as MB_ROOT" in text
        command = (
            ["{python}", f"{{workspace_root}}/{producer.replace(os.sep, '/')}"]
            if producer_path.is_file()
            else None
        )
        environment = {"MONEYBALL_ROOT": "{run_dir}"} if root_aware else {}
        if name in {
            "materialize_archive_and_legislative_workbook",
            "recompute_legislative_raw_normalized_sublayer",
        }:
            environment.update(workbook_runtime)
        if name == "recompute_augmented_network" and root_aware:
            environment["AUG_OUT"] = (
                "{run_dir}/congress_cosponsor_network_AUG_shadow.xlsx"
            )
        outputs = [
            {
                "path": output_path,
                "baseline_role": role,
                "intermediate": role is None,
                "attribution": {
                    "drivers": ["accepted_legislation"],
                    "rationale": (
                        "This isolated consumer is downstream of the accepted "
                        "legislation generation."
                    ),
                },
            }
            for output_path, role in DEFAULT_ISOLATED_OUTPUTS.get(name, [])
        ]
        steps.append(
            {
                "name": name,
                "producer": producer,
                "command": command,
                "environment": environment,
                "isolated": root_aware,
                "writes_only_under_run_dir": root_aware,
                "outputs": outputs,
                "inspection": {
                    "producer_exists": producer_path.is_file(),
                    "moneyball_root_override_present": root_aware,
                },
            }
        )
    global_gates: dict[str, Any] = {}
    spreadsheet_receipt = (
        workspace_root / DEFAULT_SPREADSHEET_IMPORT_RECEIPT
    )
    if spreadsheet_receipt.is_file():
        try:
            spreadsheet_value = read_json(spreadsheet_receipt)
        except (OSError, ValueError, json.JSONDecodeError):
            spreadsheet_value = {}
        if (
            spreadsheet_value.get("schema")
            == "legislation-spreadsheet-engine-import-verification-v1"
            and spreadsheet_value.get("status") == "PASS"
            and spreadsheet_value.get("engine") == "@oai/artifact-tool"
            and spreadsheet_value.get("production_written") is False
            and spreadsheet_value.get("workbook_edited") is False
        ):
            global_gates["approved_spreadsheet_engine_import"] = {
                "status": "PASS",
                "verification_receipt": (
                    DEFAULT_SPREADSHEET_IMPORT_RECEIPT.as_posix()
                ),
                "verification_sha256": sha256_file(
                    spreadsheet_receipt
                ),
            }
    producer = (
        workspace_root
        / DEFAULT_PRODUCERS[
            "materialize_archive_and_legislative_workbook"
        ]
    )
    producer_receipt = (
        workspace_root
        / DEFAULT_ACCEPTED_WORKBOOK_PRODUCER_VERIFICATION
    )
    if producer.is_file() and producer_receipt.is_file():
        try:
            producer_value = read_json(producer_receipt)
        except (OSError, ValueError, json.JSONDecodeError):
            producer_value = {}
        if (
            producer_value.get("status") == "PASS"
            and producer_value.get("production_written") is False
            and producer_value.get("approved_engine")
            == "@oai/artifact-tool"
        ):
            global_gates[
                "accepted_ledger_workbook_producer"
            ] = {
                "status": "PASS",
                "producer": DEFAULT_PRODUCERS[
                    "materialize_archive_and_legislative_workbook"
                ],
                "verification_receipt": (
                    DEFAULT_ACCEPTED_WORKBOOK_PRODUCER_VERIFICATION.as_posix()
                ),
                "verification_sha256": sha256_file(
                    producer_receipt
                ),
            }
    return {
        "schema": CAPABILITIES_SCHEMA,
        "source": "repository_static_inspection",
        "global_gates": global_gates,
        "steps": steps,
    }


def audit_step(
    raw: Any,
    *,
    expected_name: str,
    workspace_root: Path,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if not isinstance(raw, dict):
        return None, {
            "name": expected_name,
            "status": "BLOCKED",
            "issues": [{"code": "capability_absent"}],
        }
    if raw.get("name") != expected_name:
        issues.append(
            {
                "code": "step_name_mismatch",
                "expected": expected_name,
                "actual": raw.get("name"),
            }
        )
    producer_raw = raw.get("producer")
    producer_path: Path | None = None
    if not producer_raw:
        issues.append({"code": "producer_not_declared"})
    else:
        producer_path = resolve_workspace_path(producer_raw, workspace_root)
        if not producer_path.is_file():
            issues.append(
                {
                    "code": "missing_producer",
                    "path": str(producer_path),
                }
            )
    command = raw.get("command")
    normalized_command: list[str] | None = None
    if not isinstance(command, list) or not command or not all(
        isinstance(token, str) and token for token in command
    ):
        issues.append({"code": "explicit_argv_command_absent"})
    else:
        if any(token in SHELL_OPERATORS for token in command):
            issues.append({"code": "shell_operator_forbidden"})
        used_placeholders = placeholders(command)
        unknown = sorted(used_placeholders - APPROVED_PLACEHOLDERS - {"python"})
        if unknown:
            issues.append(
                {"code": "unapproved_command_placeholders", "values": unknown}
            )
        try:
            normalized_command = validate_executable(command, workspace_root)
        except (IsolationError, KeyError) as exc:
            issues.append({"code": "command_not_executable", "detail": str(exc)})
        if producer_path is not None and normalized_command:
            rendered_tokens = [
                token.replace("{workspace_root}", str(workspace_root))
                for token in normalized_command
            ]
            command_paths = {
                Path(token).resolve()
                for token in rendered_tokens
                if token.casefold().endswith((".py", ".ps1", ".exe"))
            }
            if producer_path not in command_paths:
                issues.append(
                    {
                        "code": "declared_producer_not_in_command",
                        "producer": str(producer_path),
                    }
                )
    environment = raw.get("environment")
    if not isinstance(environment, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in (environment or {}).items()
    ):
        issues.append({"code": "environment_must_be_nonempty_strings"})
        environment = {}
    else:
        unknown = sorted(
            placeholders(environment.values()) - APPROVED_PLACEHOLDERS
        )
        if unknown:
            issues.append(
                {"code": "unapproved_environment_placeholders", "values": unknown}
            )
    command_bound = bool(
        isinstance(command, list)
        and any("{run_dir}" in token for token in command)
    )
    root_bound = environment.get("MONEYBALL_ROOT") == "{run_dir}"
    if not (command_bound or root_bound):
        issues.append(
            {
                "code": "run_dir_confinement_absent",
                "required": (
                    "{run_dir} in argv or MONEYBALL_ROOT={run_dir}"
                ),
            }
        )
    for key, value in environment.items():
        if key.endswith(("_OUT", "_ROOT")) and not value.startswith("{run_dir}"):
            issues.append(
                {
                    "code": "unsafe_environment_path_override",
                    "key": key,
                    "value": value,
                }
            )
    if raw.get("isolated") is not True:
        issues.append({"code": "isolated_contract_false"})
    if raw.get("writes_only_under_run_dir") is not True:
        issues.append({"code": "writes_only_under_run_dir_false"})
    outputs = raw.get("outputs")
    normalized_outputs: list[dict[str, Any]] = []
    if not isinstance(outputs, list) or not outputs:
        issues.append(
            {
                "code": "output_overrides_absent",
                "detail": "No exact run-relative outputs were declared.",
            }
        )
    else:
        for output in outputs:
            if not isinstance(output, dict):
                issues.append({"code": "invalid_output_declaration"})
                continue
            try:
                relative = safe_relative(output.get("path"))
            except IsolationError as exc:
                issues.append({"code": "unsafe_output_path", "detail": str(exc)})
                continue
            role = output.get("baseline_role")
            intermediate = output.get("intermediate") is True
            if bool(role) == intermediate:
                issues.append(
                    {
                        "code": "output_role_contract_invalid",
                        "path": str(relative),
                    }
                )
                continue
            if role and role not in SHADOW.REQUIRED_BASELINE_ROLES:
                issues.append(
                    {
                        "code": "unknown_baseline_role",
                        "role": role,
                    }
                )
                continue
            if role and not isinstance(output.get("attribution"), dict):
                issues.append(
                    {
                        "code": "delta_attribution_absent",
                        "role": role,
                    }
                )
                continue
            normalized_outputs.append(
                {**output, "path": str(relative).replace("\\", "/")}
            )
    passed = not issues
    normalized = None
    if passed and normalized_command is not None:
        normalized = {
            "name": expected_name,
            "isolated": True,
            "writes_only_under_run_dir": True,
            "producer": str(producer_path),
            "command": normalized_command,
            "environment": dict(sorted(environment.items())),
            "outputs": normalized_outputs,
        }
    return normalized, {
        "name": expected_name,
        "status": "PASS" if passed else "BLOCKED",
        "producer": str(producer_path) if producer_path else None,
        "command": normalized_command,
        "environment": environment,
        "declared_output_count": len(normalized_outputs),
        "issues": issues,
    }


def validate_role_scope(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    roles = [
        str(output["baseline_role"])
        for step in steps
        for output in step["outputs"]
        if output.get("baseline_role")
    ]
    missing = sorted(set(SHADOW.REQUIRED_BASELINE_ROLES) - set(roles))
    duplicate = sorted({role for role in roles if roles.count(role) > 1})
    issues = []
    if missing:
        issues.append({"code": "baseline_roles_missing", "roles": missing})
    if duplicate:
        issues.append({"code": "baseline_roles_duplicated", "roles": duplicate})
    return issues


def validate_exact_output_ownership(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Reject a plan whose producer/output ownership differs from the contract.

    Merely covering every baseline role is insufficient: a stale fixture could
    assign ranks to the second-pass dashboard step, for example, and still pass
    a global uniqueness check.  Downstream receipts are step-bound, so paths
    and owners must match the declared producer exactly.
    """

    issues: list[dict[str, Any]] = []
    by_name = {str(step["name"]): step for step in steps}
    for name in SHADOW.REQUIRED_STEP_ORDER:
        expected = {
            (str(path).replace("\\", "/"), role)
            for path, role in DEFAULT_ISOLATED_OUTPUTS.get(name, [])
        }
        actual = {
            (
                str(output.get("path") or "").replace("\\", "/"),
                output.get("baseline_role"),
            )
            for output in by_name.get(name, {}).get("outputs", [])
        }
        missing = sorted(
            expected - actual,
            key=lambda item: (item[0], str(item[1])),
        )
        unexpected = sorted(
            actual - expected,
            key=lambda item: (item[0], str(item[1])),
        )
        if missing:
            issues.append(
                {
                    "code": "step_outputs_missing",
                    "step": name,
                    "outputs": [
                        {"path": path, "baseline_role": role}
                        for path, role in missing
                    ],
                }
            )
        if unexpected:
            issues.append(
                {
                    "code": "step_outputs_unexpected_or_misowned",
                    "step": name,
                    "outputs": [
                        {"path": path, "baseline_role": role}
                        for path, role in unexpected
                    ],
                }
            )
    return issues


def build(
    *,
    out_dir: Path,
    workspace_root: Path,
    capabilities_path: Path | None,
) -> dict[str, Any]:
    out = validate_out(out_dir)
    workspace = workspace_root.resolve()
    plan_path = out / PLAN_PATH_NAME
    audit_path = out / AUDIT_PATH_NAME
    if capabilities_path is None:
        capabilities = default_capabilities(workspace)
        capability_receipt = None
    else:
        path = capabilities_path.resolve()
        capabilities = read_json(path)
        capability_receipt = file_receipt(path)
    if capabilities.get("schema") != CAPABILITIES_SCHEMA:
        raise IsolationError(
            f"capabilities schema must be {CAPABILITIES_SCHEMA!r}"
        )
    raw_steps = capabilities.get("steps")
    if not isinstance(raw_steps, list):
        raise IsolationError("capabilities steps must be a list")
    by_name: dict[str, Any] = {}
    duplicate_names: list[str] = []
    for step in raw_steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or "")
        if name in by_name:
            duplicate_names.append(name)
        by_name[name] = step

    normalized_steps: list[dict[str, Any]] = []
    step_audits: list[dict[str, Any]] = []
    for name in SHADOW.REQUIRED_STEP_ORDER:
        normalized, audit = audit_step(
            by_name.get(name),
            expected_name=name,
            workspace_root=workspace,
        )
        step_audits.append(audit)
        if normalized:
            normalized_steps.append(normalized)

    global_gates = capabilities.get("global_gates") or {}
    accepted_pass, accepted_audit = validate_global_gate(
        global_gates.get("accepted_ledger_workbook_producer"),
        name="accepted_ledger_workbook_producer",
        workspace_root=workspace,
    )
    spreadsheet_pass, spreadsheet_audit = validate_global_gate(
        global_gates.get("approved_spreadsheet_engine_import"),
        name="approved_spreadsheet_engine_import",
        workspace_root=workspace,
    )
    if capabilities_path is None:
        if not accepted_pass:
            accepted_audit.update(
                {
                    "code": (
                        "accepted_ledger_to_workbook_producer_absent"
                    ),
                    "required_producer": str(
                        workspace
                        / "fec"
                        / "legislation" / "build_accepted_legislation_workbook.py"
                    ),
                    "required_verification_receipt": str(
                        workspace
                        / DEFAULT_ACCEPTED_WORKBOOK_PRODUCER_VERIFICATION
                    ),
                }
            )
        if not spreadsheet_pass:
            spreadsheet_audit.update(
                {
                    "code": (
                        "approved_spreadsheet_engine_import_blocker"
                    ),
                    "required_receipt": str(
                        workspace
                        / DEFAULT_SPREADSHEET_IMPORT_RECEIPT
                    ),
                }
            )
    role_issues = (
        (
            validate_role_scope(normalized_steps)
            + validate_exact_output_ownership(normalized_steps)
        )
        if len(normalized_steps) == len(SHADOW.REQUIRED_STEP_ORDER)
        else []
    )
    blocking = [
        {
            "code": issue.get("code", "consumer_step_not_isolatable"),
            "step": audit["name"],
            "detail": issue,
        }
        for audit in step_audits
        for issue in audit["issues"]
    ]
    blocking.extend(
        {"code": "duplicate_step_capability", "step": name}
        for name in sorted(set(duplicate_names))
    )
    blocking.extend(role_issues)
    if not accepted_pass:
        blocking.append(accepted_audit)
    if not spreadsheet_pass:
        blocking.append(spreadsheet_audit)

    complete = bool(
        not blocking
        and len(normalized_steps) == len(SHADOW.REQUIRED_STEP_ORDER)
    )
    if complete:
        plan = {
            "schema": SHADOW.PLAN_SCHEMA,
            "status": "PASS",
            "production_consumers_executed": False,
            "production_written": False,
            "workspace_root": str(workspace),
            "capabilities_manifest": capability_receipt,
            "global_gates": {
                "accepted_ledger_workbook_producer": accepted_audit,
                "approved_spreadsheet_engine_import": spreadsheet_audit,
            },
            "steps": normalized_steps,
        }
        atomic_json(plan_path, plan)
        pass_audit = {
            "schema": AUDIT_SCHEMA,
            "status": "PASS",
            "plan_emitted": True,
            "runnable_plan_present": True,
            "production_consumers_executed": False,
            "production_written": False,
            "workspace_root": str(workspace),
            "capabilities_manifest": capability_receipt,
            "required_step_order": list(SHADOW.REQUIRED_STEP_ORDER),
            "passing_step_count": len(normalized_steps),
            "required_step_count": len(SHADOW.REQUIRED_STEP_ORDER),
            "steps": step_audits,
            "global_gates": {
                "accepted_ledger_workbook_producer": accepted_audit,
                "approved_spreadsheet_engine_import": spreadsheet_audit,
            },
            "blocking_issues": [],
            "plan": file_receipt(plan_path),
        }
        atomic_json(audit_path, pass_audit)
        return {
            "status": "PASS",
            "plan_emitted": True,
            "plan": file_receipt(plan_path),
            "audit": file_receipt(audit_path),
            "consumer_count": len(normalized_steps),
            "production_consumers_executed": False,
            "production_written": False,
        }

    stale_plan_removed = False
    if plan_path.exists():
        plan_path.unlink()
        stale_plan_removed = True
    audit = {
        "schema": AUDIT_SCHEMA,
        "status": "FAIL",
        "plan_emitted": False,
        "runnable_plan_present": False,
        "production_consumers_executed": False,
        "production_written": False,
        "workspace_root": str(workspace),
        "capabilities_manifest": capability_receipt,
        "required_step_order": list(SHADOW.REQUIRED_STEP_ORDER),
        "passing_step_count": sum(
            item["status"] == "PASS" for item in step_audits
        ),
        "required_step_count": len(SHADOW.REQUIRED_STEP_ORDER),
        "steps": step_audits,
        "global_gates": {
            "accepted_ledger_workbook_producer": accepted_audit,
            "approved_spreadsheet_engine_import": spreadsheet_audit,
        },
        "blocking_issues": blocking,
        "stale_runnable_plan_removed": stale_plan_removed,
    }
    atomic_json(audit_path, audit)
    return audit


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = build(
            out_dir=args.out,
            workspace_root=args.workspace_root,
            capabilities_path=args.capabilities_manifest,
        )
    except (IsolationError, OSError, json.JSONDecodeError) as exc:
        print(f"consumer isolation audit failed closed: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
