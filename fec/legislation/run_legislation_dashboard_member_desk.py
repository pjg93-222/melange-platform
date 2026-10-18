# -*- coding: utf-8 -*-
"""Build the dashboard/member-desk branch inside a legislation shadow run.

The legacy producers remain the computational authority.  This wrapper:

* requires the hash-bound network-dependent generation;
* materializes a read-only, hash-recorded snapshot of display-only inputs;
* runs every producer with ``MONEYBALL_ROOT`` and the process cwd set to the
  isolated run directory;
* keeps ``build_dashboard_bundle.py``'s redundant bipartisan output in an
  intermediate directory and proves it equals the upstream staged artifact;
* makes the member-desk's explicitly volatile freshness block deterministic;
* validates all working outputs and publishes one stable intermediate receipt.

It never writes to the repository/production root.  Root dashboard artifacts
are working state for the next shadow step; a later second-pass coordinator
owns the final production-role declarations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT as MB_ROOT  # noqa: E402 - root-aware wrapper contract


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "legislation-dashboard-member-desk-receipt-v1"
NETWORK_SCHEMA = "legislation-network-dependents-receipt-v1"
SAY_DO_SCHEMA = "legislation-say-do-pass-receipt-v1"

NETWORK_FIRST_RECEIPT = (
    Path("intermediate") / "network_dependents_first_pass.receipt.json"
)
NETWORK_SECOND_RECEIPT = (
    Path("intermediate") / "network_dependents_second_pass.receipt.json"
)
# Compatibility alias for first-pass callers.
NETWORK_RECEIPT = NETWORK_FIRST_RECEIPT
SAY_DO_SECOND_RECEIPT = (
    Path("intermediate") / "say_do_second_pass.receipt.json"
)
FIRST_RECEIPT = (
    Path("intermediate") / "dashboard_member_desk_first_pass.json"
)
FIRST_RECEIPT_MIRROR = (
    Path("intermediate") / "dashboard_member_desk_first_pass.receipt.json"
)
SECOND_RECEIPT = (
    Path("intermediate") / "dashboard_member_desk_second_pass.json"
)
SECOND_RECEIPT_MIRROR = (
    Path("intermediate") / "dashboard_member_desk_second_pass.receipt.json"
)
SECOND_PASS_STARTED = (
    Path("intermediate") / "dashboard_member_desk_second_pass.started"
)
FRESHNESS_PIN = (
    Path("intermediate") / "dashboard_producer" / "member_desk_freshness_pin.json"
)

NETWORK_OUTPUTS = (
    "data/money/electoral_plays.json",
    "per_topic_alloc.json",
    "data/lobbying/per_topic_coalition.json",
    "data/money/budget_frontier.json",
    "data/money/budget_frontier_electoral.json",
    "optimize_result.json",
    "im_allocation.json",
    "salience_advisory.json",
)

# Inputs produced by earlier shadow steps.  These are never copied from the
# production tree: a missing item is an upstream-computation blocker.
DYNAMIC_INPUTS = (
    "Moneyball Main Version - Blended.xlsx",
    "archive_leg.json",
    "vote_category_lean.json",
    "behavior_panel.json",
    "behavioral_dispositions.json",
    "coop_v2_candidate.json",
    "member_receptivity.json",
    "category_stance.json",
    "say_do_gap.json",
    "data/engine/pillar_inputs_snapshot.json",
    "ranks_python.json",
    "bipartisan.json",
    *NETWORK_OUTPUTS,
)

# Display-only/background inputs.  copy2 preserves source mtimes because the
# desk exposes the relative dates of a small number of psychometric artifacts.
# Missing optional files remain absent and are recorded as such.
STATIC_SOURCES = (
    "data/engine/congestion.json",
    "data/members/member_desk_src.json",
    "fec/model_bio.json",
    "member_desk_manual.json",
    "data/stance/press_date_overrides.json",
    "data/stance/press_stance_overrides.json",
    "data/money/electoral_history.json",
    "data/lobbying/district_industry.json",
    "data/lobbying/district_industry_4digit.json",
    "data/district/district_employers.json",
    "data/district/district_financial.json",
    "data/district/district_hmda.json",
    "data/district/district_community.json",
    "data/district/district_briefs_v1.json",
    "bill_repository_live.json",
    "member_references.json",
    "data/members/member_personal_texture.json",
    "data/members/big5_blue_validated.json",
    "data/members/big5_blue_emb.json",
    "data/members/big5_v2.json",
    "data/members/big5_traits.json",
    "data/members/big5_llm_opus.json",
    "data/stance/big5_senate_expert_gold.json",
    "data/stance/big5_external_gold_validation.json",
    "data/stance/big5_e_gold_anchored.json",
    "data/stance/big5_gold_anchored.json",
    "data/members/agency_communion.json",
    "data/members/lep_criterion.json",
    "data/network/kb_benchmark.json",
    "data/lobbying/signed_coalitions.json",
    "data/members/behavioral_roles_v2_candidate.json",
    "communication_style.json",
    "data/members/profile_basis_v2.json",
    "data/stance/interview_antagonism.json",
    "data/stance/hearing_antagonism.json",
    "data/money/affective_n.json",
    "member_narratives_v2.json",
    "data/members/staff_directory.json",
    "staff_state.json",
    "staff_interactions.json",
    "data/members/formal_visibility_roles.json",
    "data/members/confidence.json",
    "data/network/co_appearance.json",
    "social_media.db",
    "social_media.db-wal",
    "Social Master.xlsx",
    "data/stance/tweet_stance_final.csv",
    "data/stance/interview_stance_final.csv",
    "data/stance/interviews_cu.csv",
    "data/stance/hearing_stance_final.csv",
    "data/stance/hearings_cu.csv",
    "data/legislation/bill_overrides.csv",
    "manual_bill_evidence.csv",
    "DEMOGRAPHIC_POTENTIAL_METHOD.md",
    "fec/legislators.csv",
    "votes/HS119_members.csv",
    "votes/HS118_members.csv",
    "votes/HS117_members.csv",
)

PRODUCERS = (
    "fec/surfaces/build_dashboard_bundle.py",
    "fec/surfaces/make_dashboard_html.py",
    "fec/surfaces/make_member_desk.py",
    "fec/surfaces/make_combined.py",
)
CODE_INPUTS = (
    *PRODUCERS,
    "fec/surfaces/templates/make_dashboard_html_00.tpl",
    "fec/surfaces/templates/make_member_desk_00.tpl",
    "fec/paths.py",
    "fec/moneyball_core.py",
    "fec/engine/flip_model.py",
    "fec/tier_util.py",
    "fec/config_overlay.py",
    "fec/bill_rules.py",
    "fec/stance/media_rules.py",
    "fec/audit/dashboard_integrity.py",
    "fec/district/demographic_potential.py",
    # 2026-08-05: close the fingerprint over make_member_desk's direct district helpers.
    "fec/district/demographic_normalization.py",
    "fec/district/district_cu_metrics.py",
    "fec/legislation/congress_bill_repository.py",
    "fec/legislation/dashboard_legislative_workbench.py",
    "fec/legislation/legislation_culling_status.py",
    "fec/legislation/update_bill_repository.py",
    "fec/members/member_identity.py",
    "fec/stance/stance_rules.py",
    "tier_overrides.json",
)

WORKING_OUTPUTS = (
    "dashboard_data.json",
    "Moneyball Dashboard.html",
    "member_desk_bundle.json",
    "cu-member-desk.html",
    "cu-member-desk-profiles.js",
    "dashboard_exclusion_reconciliation.json",
    "data/engine/dashboard_integrity_report.json",
    "data/engine/dashboard_integrity_state.json",
    "Moneyball.html",
    "intermediate/dashboard_producer/dashboard_data.json",
    "intermediate/dashboard_producer/bipartisan.json",
)

PINNED_ENVIRONMENT = {
    "BP_SRC": "cosponsor",
    "RECEPT_LAYER": "1",
    "PERS_NONE": "0.45",
    "OVERALL_OPPONENT_CUTOFF": "-1.50",
    "HALFF": "0.10",
    "IMPUTE_IND0": "1",
    "COMPET_MULT": "0",
    "COMPET_SRC": "",
    "OPTIMIZE_RESULT_PATH": "",
    "DESK_B5_FALLBACK": "1",
    "DESK_FOOTPRINT": "1",
    "DESK_SPLIT": "1",
    "DESK_MEDIA_CAP": "0",
    "DESK_MEDIA_STANCE": "1",
    "COMBINED_STALE_OK": "0",
    "PYTHONHASHSEED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class DashboardBranchError(RuntimeError):
    """A shadow input, isolation, producer, or output gate failed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def allowed_roots() -> tuple[Path, ...]:
    roots = {
        (REPO_ROOT / "audits" / "legfix5" / "shadow_recompute").resolve(),
        (REPO_ROOT / "tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    }
    for name in ("TEMP", "TMP", "TMPDIR"):
        value = os.environ.get(name)
        if value:
            roots.add(Path(value).resolve())
    return tuple(sorted(roots, key=str))


def validate_run_dir(raw: Path) -> Path:
    run_dir = raw.resolve()
    if not run_dir.is_dir():
        raise DashboardBranchError(
            f"isolated run directory does not exist: {run_dir}"
        )
    if run_dir == REPO_ROOT.resolve():
        raise DashboardBranchError("production/workspace root is forbidden")
    if not any(is_relative_to(run_dir, root) for root in allowed_roots()):
        raise DashboardBranchError(
            "MONEYBALL_ROOT must be under the legislation shadow audit or temp"
        )
    return run_dir


def validate_shadow_environment(run_dir: Path) -> None:
    if os.environ.get("LEGISLATION_SHADOW_MODE") != "1":
        raise DashboardBranchError("LEGISLATION_SHADOW_MODE=1 is required")
    declared = os.environ.get("LEGISLATION_SHADOW_RUN_DIR")
    if not declared or Path(declared).resolve() != run_dir:
        raise DashboardBranchError(
            "LEGISLATION_SHADOW_RUN_DIR must equal MONEYBALL_ROOT"
        )


def safe_child(run_dir: Path, relative: str | Path) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts or not raw.parts:
        raise DashboardBranchError(f"unsafe run-relative path: {relative}")
    result = (run_dir / raw).resolve()
    if not is_relative_to(result, run_dir):
        raise DashboardBranchError(f"path escapes isolated run: {relative}")
    return result


def logical_receipt(path: Path, root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_file():
        raise DashboardBranchError(f"required file is missing: {resolved}")
    if not is_relative_to(resolved, root.resolve()):
        raise DashboardBranchError(f"receipt path is outside its root: {resolved}")
    stat = resolved.stat()
    return {
        "path": resolved.relative_to(root.resolve()).as_posix(),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DashboardBranchError(f"invalid JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DashboardBranchError(f"JSON root must be an object: {path}")
    return value


def atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(body)
    os.replace(temporary, path)


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(
        path,
        json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n",
    )


def atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    shutil.copyfile(source, temporary)
    if sha256_file(source) != sha256_file(temporary):
        temporary.unlink(missing_ok=True)
        raise DashboardBranchError(f"hash-copy failed: {source}")
    os.replace(temporary, destination)


def _receipt_item_path(
    run_dir: Path, receipt_path: Path, item: Mapping[str, Any]
) -> Path:
    raw = item.get("path")
    if not raw:
        raise DashboardBranchError(
            f"declared output lacks path in {receipt_path}"
        )
    candidate = Path(str(raw))
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        # Producer receipts use run-relative paths.  Accept receipt-relative
        # paths only when they actually resolve to an existing in-run file.
        run_relative = (run_dir / candidate).resolve()
        receipt_relative = (receipt_path.parent / candidate).resolve()
        resolved = (
            run_relative
            if run_relative.is_file()
            else receipt_relative
        )
    if not is_relative_to(resolved, run_dir):
        raise DashboardBranchError(
            f"declared output escapes isolated run: {resolved}"
        )
    return resolved


def _find_declared_output(
    declared: Mapping[str, Any], relative: str
) -> Mapping[str, Any]:
    direct = declared.get(relative)
    if isinstance(direct, Mapping):
        return direct
    matches = []
    for key, item in declared.items():
        if not isinstance(item, Mapping):
            continue
        raw = str(item.get("path") or key).replace("\\", "/")
        if raw == relative or raw.endswith("/" + relative):
            matches.append(item)
    if len(matches) != 1:
        raise DashboardBranchError(
            f"prerequisite must declare exactly one {relative}; found "
            f"{len(matches)}"
        )
    return matches[0]


def validate_network_receipt(
    run_dir: Path, *, phase: str = "first"
) -> dict[str, Any]:
    if phase not in {"first", "second"}:
        raise DashboardBranchError("phase must be first or second")
    receipt_path = safe_child(
        run_dir,
        (
            NETWORK_FIRST_RECEIPT
            if phase == "first"
            else NETWORK_SECOND_RECEIPT
        ),
    )
    value = read_object(receipt_path)
    if (
        value.get("schema") != NETWORK_SCHEMA
        or value.get("status") != "PASS"
    ):
        raise DashboardBranchError(
            "network-dependent prerequisite receipt is not PASS/current"
        )
    generation_id = str(value.get("generation_id") or "")
    generation_basis = str(value.get("generation_basis_sha256") or "")
    if len(generation_id) != 64 or len(generation_basis) != 64:
        raise DashboardBranchError(
            "network-dependent receipt lacks stable generation binding"
        )
    declared = value.get("declared_outputs")
    if not isinstance(declared, Mapping):
        raise DashboardBranchError(
            "network-dependent receipt lacks declared_outputs"
        )
    outputs: dict[str, dict[str, Any]] = {}
    for relative in NETWORK_OUTPUTS:
        item = _find_declared_output(declared, relative)
        path = _receipt_item_path(run_dir, receipt_path, item)
        expected = str(item.get("sha256") or "").casefold()
        if path != safe_child(run_dir, relative):
            raise DashboardBranchError(
                f"network output is not at its canonical run path: {relative}"
            )
        if len(expected) != 64 or sha256_file(path) != expected:
            raise DashboardBranchError(
                f"network-dependent output hash mismatch: {relative}"
            )
        if item.get("size") is not None and path.stat().st_size != int(
            item["size"]
        ):
            raise DashboardBranchError(
                f"network-dependent output size mismatch: {relative}"
            )
        outputs[relative] = logical_receipt(path, run_dir)
    return {
        "schema": NETWORK_SCHEMA,
        "generation_id": generation_id,
        "generation_basis_sha256": generation_basis,
        "declared_outputs": outputs,
    }


def validate_say_do_second_receipt(run_dir: Path) -> dict[str, Any]:
    path = safe_child(run_dir, SAY_DO_SECOND_RECEIPT)
    value = read_object(path)
    if (
        value.get("schema") != SAY_DO_SCHEMA
        or value.get("status") != "PASS"
        or value.get("pass") != "second"
    ):
        raise DashboardBranchError("second-pass say/do receipt is not PASS")
    output = value.get("declared_output")
    if not isinstance(output, Mapping):
        raise DashboardBranchError(
            "second-pass say/do receipt lacks declared output"
        )
    output_path = safe_child(run_dir, "say_do_gap.json")
    expected = str(output.get("sha256") or "").casefold()
    if len(expected) != 64 or sha256_file(output_path) != expected:
        raise DashboardBranchError(
            "second-pass say/do output/receipt hash mismatch"
        )
    return {
        "schema": SAY_DO_SCHEMA,
        "pass": "second",
        "declared_output": logical_receipt(output_path, run_dir),
        "expected_input_sha256": dict(
            sorted((value.get("expected_input_sha256") or {}).items())
        ),
    }


def audit_code_contract(
    workspace_root: Path = REPO_ROOT,
) -> dict[str, dict[str, Any]]:
    checks = {
        "fec/surfaces/build_dashboard_bundle.py": (
            "from paths import ROOT as MB_ROOT",
            "DASH_OUT",
            "dashboard_data.json",
            "bipartisan.json",
        ),
        "fec/surfaces/make_dashboard_html.py": (
            "from paths import ROOT as MB_ROOT",
            "dashboard_data.json",
            "Moneyball Dashboard.html",
        ),
        "fec/surfaces/make_member_desk.py": (
            "from paths import ROOT as MB_ROOT",
            "DI.preflight",
            "DI.postflight",
            "member_desk_bundle.json",
            "cu-member-desk.html",
        ),
        "fec/surfaces/make_combined.py": (
            "from paths import ROOT as MB_ROOT",
            "dashboard_exclusion_reconciliation.json",
            "Moneyball.html",
        ),
    }
    for relative, needles in checks.items():
        text = (workspace_root / relative).read_text(
            encoding="utf-8-sig", errors="strict"
        )
        missing = [needle for needle in needles if needle not in text]
        if missing:
            raise DashboardBranchError(
                f"producer isolation/output contract changed in {relative}: "
                f"{missing}"
            )
    result: dict[str, dict[str, Any]] = {}
    for relative in CODE_INPUTS:
        path = (workspace_root / relative).resolve()
        if not path.is_file():
            raise DashboardBranchError(f"code input is missing: {relative}")
        result[relative] = logical_receipt(path, workspace_root)
    return result


def validate_prestaged_manual_evidence(
    run_dir: Path,
) -> dict[str, Any]:
    """Bind the desk's manual evidence to the accepted-generation snapshot."""
    destination = safe_child(run_dir, "manual_bill_evidence.csv")
    materialization_path = safe_child(
        run_dir, "input_materialization.json"
    )
    materialization = read_object(materialization_path)
    if not (
        materialization.get("schema")
        == "legislation-shadow-input-materialization-v1"
        and materialization.get("status") == "PASS"
        and materialization.get("production_written") is False
    ):
        raise DashboardBranchError(
            "canonical manual evidence input materialization is not PASS"
        )
    matches = []
    for row in materialization.get("records") or []:
        if not isinstance(row, Mapping):
            continue
        if (
            row.get("source_group") == "accepted_generation"
            and row.get("logical_name") == "manual_bill_evidence"
            and str(row.get("materialized_path") or "").replace("\\", "/")
            == "manual_bill_evidence.csv"
            and row.get("generation_bound") is True
        ):
            matches.append(row)
    if len(matches) != 1 or not destination.is_file():
        raise DashboardBranchError(
            "expected exactly one accepted-generation manual evidence copy"
        )
    expected = str(
        matches[0].get("materialized_sha256")
        or matches[0].get("source_sha256")
        or ""
    ).casefold()
    if len(expected) != 64 or sha256_file(destination) != expected:
        raise DashboardBranchError(
            "accepted-generation manual evidence hash mismatch"
        )
    return logical_receipt(destination, run_dir)


def materialize_static_inputs(
    run_dir: Path,
    *,
    workspace_root: Path = REPO_ROOT,
    prior_static: Mapping[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    copied: dict[str, dict[str, Any]] = {}
    absent: list[str] = []
    for relative in STATIC_SOURCES:
        source = (workspace_root / relative).resolve()
        destination = safe_child(run_dir, relative)
        if prior_static is not None:
            item = prior_static.get(relative)
            if not isinstance(item, Mapping):
                if destination.exists():
                    raise DashboardBranchError(
                        f"second pass found unmanifested static input: {relative}"
                    )
                absent.append(relative)
                continue
            expected = str(item.get("sha256") or "").casefold()
            if (
                not destination.is_file()
                or len(expected) != 64
                or sha256_file(destination) != expected
            ):
                raise DashboardBranchError(
                    f"second-pass static snapshot changed: {relative}"
                )
            copied[relative] = logical_receipt(destination, run_dir)
            continue
        if relative == "manual_bill_evidence.csv":
            copied[relative] = validate_prestaged_manual_evidence(run_dir)
            continue
        if not source.is_file():
            if destination.exists():
                raise DashboardBranchError(
                    f"static source absent but staged path exists: {relative}"
                )
            absent.append(relative)
            continue
        source_hash = sha256_file(source)
        if destination.exists():
            if not destination.is_file() or sha256_file(destination) != source_hash:
                raise DashboardBranchError(
                    f"mixed static generation at {relative}"
                )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if sha256_file(destination) != source_hash:
                raise DashboardBranchError(
                    f"static snapshot copy hash mismatch: {relative}"
                )
        copied[relative] = logical_receipt(destination, run_dir)
    return dict(sorted(copied.items())), sorted(absent)


def discover_network_inputs(run_dir: Path) -> dict[str, dict[str, Any]]:
    files = sorted(run_dir.glob("congress_cosponsor_network_*.xlsx"))
    base = [path for path in files if "AUG" not in path.name.upper()]
    augmented = [path for path in files if "AUG" in path.name.upper()]
    if len(base) != 1 or len(augmented) != 1:
        raise DashboardBranchError(
            "exactly one staged base and one staged augmented network are "
            f"required; base={len(base)} augmented={len(augmented)}"
        )
    return {
        path.name: logical_receipt(path, run_dir)
        for path in (base[0], augmented[0])
    }


def collect_dynamic_inputs(run_dir: Path) -> dict[str, dict[str, Any]]:
    result = {
        relative: logical_receipt(safe_child(run_dir, relative), run_dir)
        for relative in DYNAMIC_INPUTS
    }
    result.update(discover_network_inputs(run_dir))
    return dict(sorted(result.items()))


def production_output_guard(
    workspace_root: Path = REPO_ROOT,
) -> dict[str, str | None]:
    guarded = {
        rel for rel in WORKING_OUTPUTS if not rel.startswith("intermediate/")
    } | {"bipartisan.json"}
    return {
        name: (
            sha256_file(workspace_root / name)
            if (workspace_root / name).is_file()
            else None
        )
        for name in sorted(guarded)
    }


def _normalize_stream(text: str, run_dir: Path, workspace_root: Path) -> str:
    return (
        text.replace(str(run_dir), "{run_dir}")
        .replace(str(workspace_root), "{workspace_root}")
        .replace(str(run_dir).replace("\\", "/"), "{run_dir}")
        .replace(str(workspace_root).replace("\\", "/"), "{workspace_root}")
    )


def run_producer(
    relative: str,
    run_dir: Path,
    *,
    workspace_root: Path = REPO_ROOT,
    extra_environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    producer = (workspace_root / relative).resolve()
    environment = dict(os.environ)
    # Caller-shell redirect/override vars must not reach the producers.
    for name in (
        "DESK_OUT",
        "DASH_OUT",
        "EFFICIENCY_LAYERS_PATH",
        "FRONTIER_OUT",
        "OPT_OUT",
        "DESK_FRESHNESS_PIN",
    ):
        environment.pop(name, None)
    environment.update(PINNED_ENVIRONMENT)
    environment.update(
        {
            "MONEYBALL_ROOT": str(run_dir),
            "LEGISLATION_SHADOW_MODE": "1",
            "LEGISLATION_SHADOW_RUN_DIR": str(run_dir),
        }
    )
    if extra_environment:
        environment.update(extra_environment)
    completed = subprocess.run(
        [sys.executable, str(producer)],
        cwd=run_dir,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    stdout = _normalize_stream(completed.stdout, run_dir, workspace_root)
    stderr = _normalize_stream(completed.stderr, run_dir, workspace_root)
    if completed.returncode != 0:
        detail = (stderr or stdout)[-1200:]
        raise DashboardBranchError(
            f"{relative} failed with exit {completed.returncode}: {detail}"
        )
    return {
        "producer": relative,
        "argv": ["{python}", relative],
        "returncode": completed.returncode,
        "stdout_sha256": sha256_bytes(stdout.encode("utf-8")),
        "stderr_sha256": sha256_bytes(stderr.encode("utf-8")),
    }


def json_for_script(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, separators=(",", ":")
    )
    return (
        raw.replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def member_desk_freshness(
    generation_id: str, inputs: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "generated_at": f"hash-bound shadow generation {generation_id}",
        "source_sha256": {
            key: str(value["sha256"])
            for key, value in sorted(inputs.items())
        },
    }


def write_freshness_pin(
    run_dir: Path,
    generation_id: str,
    inputs: Mapping[str, Mapping[str, Any]],
) -> Path:
    # make_member_desk.py reads this file (DESK_FRESHNESS_PIN) and uses it as
    # bundle["freshness"], so every desk output is deterministic at the source.
    path = safe_child(run_dir, FRESHNESS_PIN)
    atomic_json(path, member_desk_freshness(generation_id, inputs))
    return path


def verify_member_desk_freshness(
    run_dir: Path,
    generation_id: str,
    inputs: Mapping[str, Mapping[str, Any]],
) -> None:
    bundle = read_object(safe_child(run_dir, "member_desk_bundle.json"))
    if bundle.get("freshness") != member_desk_freshness(generation_id, inputs):
        raise DashboardBranchError(
            "member desk freshness is not the pinned hash-bound value"
        )


def validate_outputs(run_dir: Path) -> dict[str, Any]:
    dashboard = read_object(safe_child(run_dir, "dashboard_data.json"))
    desk = read_object(safe_child(run_dir, "member_desk_bundle.json"))
    bipartisan = read_object(safe_child(run_dir, "bipartisan.json"))
    producer_bipartisan = read_object(
        safe_child(
            run_dir, "intermediate/dashboard_producer/bipartisan.json"
        )
    )
    names = dashboard.get("name")
    issues = dashboard.get("issues")
    stances = dashboard.get("stance")
    members = desk.get("members")
    if (
        not isinstance(names, list)
        or not names
        or len(names) != len(set(names))
    ):
        raise DashboardBranchError(
            "dashboard_data has empty/duplicate member coverage"
        )
    if (
        not isinstance(issues, list)
        or not issues
        or not isinstance(stances, list)
        or len(stances) != len(names)
        or any(not isinstance(row, str) or len(row) != len(issues) for row in stances)
    ):
        raise DashboardBranchError(
            "dashboard issue/stance alignment is invalid"
        )
    if (
        not isinstance(members, list)
        or {row.get("name") for row in members if isinstance(row, dict)}
        != set(names)
    ):
        raise DashboardBranchError(
            "member-desk roster differs from dashboard roster"
        )
    if set(bipartisan) != set(names) or producer_bipartisan != bipartisan:
        raise DashboardBranchError(
            "dashboard bipartisanship differs from staged upstream authority"
        )
    reconciliation = read_object(
        safe_child(run_dir, "dashboard_exclusion_reconciliation.json")
    )
    sources = reconciliation.get("sources")
    if (
        reconciliation.get("status") != "pass"
        or not isinstance(sources, dict)
        or not sources
        or any(
            not isinstance(row, dict)
            or row.get("status") != "pass"
            or row.get("difference") != 0
            for row in sources.values()
        )
    ):
        raise DashboardBranchError(
            "dashboard exclusion reconciliation is not complete/PASS"
        )
    report = read_object(
        safe_child(run_dir, "data/engine/dashboard_integrity_report.json")
    )
    state = read_object(
        safe_child(run_dir, "data/engine/dashboard_integrity_state.json")
    )
    if (
        report.get("status") != "pass"
        or report.get("errors") not in ([], None)
        or (report.get("counts") or {}).get("members") != len(names)
        or report.get("output_semantic_sha256")
        != state.get("output_semantic_sha256")
    ):
        raise DashboardBranchError(
            "dashboard integrity report/state is not PASS or aligned"
        )
    html_checks = {
        "Moneyball Dashboard.html": ("<body", "<script", "__DATA__"),
        "cu-member-desk.html": ("<body", "<script", "__BUNDLE__"),
        "Moneyball.html": (
            "id=app-network",
            "id=app-member",
            "__BUNDLE__",
        ),
    }
    for relative, (first, second, forbidden) in html_checks.items():
        text = safe_child(run_dir, relative).read_text(encoding="utf-8")
        if first not in text or second not in text or forbidden in text:
            raise DashboardBranchError(f"invalid/incomplete HTML: {relative}")
        if re.search(r"__[A-Z][A-Z_]*__", text):
            raise DashboardBranchError(
                f"unreplaced template placeholder in HTML: {relative}"
            )
    return {
        "members": len(names),
        "issues": len(issues),
        "desk_articles": int((report.get("counts") or {}).get("articles") or 0),
        "desk_bills": int((report.get("counts") or {}).get("bills") or 0),
        "exclusion_sources": {
            name: {
                "source_rows": int(row.get("source_rows") or 0),
                "accounted_rows": int(row.get("accounted_rows") or 0),
                "difference": int(row.get("difference") or 0),
            }
            for name, row in sorted(sources.items())
        },
    }


def output_receipts(
    run_dir: Path,
) -> dict[str, dict[str, Any]]:
    return {
        relative: logical_receipt(safe_child(run_dir, relative), run_dir)
        for relative in WORKING_OUTPUTS
    }


def validate_working_receipts(
    run_dir: Path, declared: Mapping[str, Any]
) -> None:
    if set(declared) != set(WORKING_OUTPUTS):
        raise DashboardBranchError(
            "existing dashboard receipt has an incomplete output set"
        )
    for relative in WORKING_OUTPUTS:
        item = declared.get(relative)
        if not isinstance(item, Mapping):
            raise DashboardBranchError(
                f"existing dashboard receipt lacks {relative}"
            )
        path = safe_child(run_dir, relative)
        expected = str(item.get("sha256") or "").casefold()
        if (
            not path.is_file()
            or len(expected) != 64
            or sha256_file(path) != expected
        ):
            raise DashboardBranchError(
                f"existing dashboard working output changed: {relative}"
            )


def receipt_paths(phase: str) -> tuple[Path, Path]:
    return (
        (FIRST_RECEIPT, FIRST_RECEIPT_MIRROR)
        if phase == "first"
        else (SECOND_RECEIPT, SECOND_RECEIPT_MIRROR)
    )


def read_prior_phase(run_dir: Path, phase: str) -> dict[str, Any] | None:
    receipt_relative, mirror_relative = receipt_paths(phase)
    path = safe_child(run_dir, receipt_relative)
    mirror = safe_child(run_dir, mirror_relative)
    if not path.exists() and not mirror.exists():
        return None
    if not path.is_file() or not mirror.is_file():
        raise DashboardBranchError(
            f"partial {phase}-pass dashboard receipt exists"
        )
    if path.read_bytes() != mirror.read_bytes():
        raise DashboardBranchError(
            f"{phase}-pass dashboard receipt mirror differs"
        )
    value = read_object(path)
    if (
        value.get("schema") != SCHEMA
        or value.get("status") != "PASS"
        or value.get("phase") != phase
    ):
        raise DashboardBranchError(
            f"existing {phase}-pass dashboard receipt is invalid"
        )
    return value


def build_generation_basis(
    *,
    phase: str,
    network: Mapping[str, Any],
    say_do_second: Mapping[str, Any] | None,
    code: Mapping[str, Any],
    static: Mapping[str, Any],
    absent_static: Sequence[str],
    dynamic: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    basis = {
        "schema": "legislation-dashboard-member-desk-generation-basis-v1",
        "phase": phase,
        "network_generation": network,
        "say_do_second_generation": say_do_second,
        "code_sha256": {
            key: value["sha256"] for key, value in sorted(code.items())
        },
        "static_sha256": {
            key: value["sha256"] for key, value in sorted(static.items())
        },
        "absent_static": list(sorted(absent_static)),
        "dynamic_sha256": {
            key: value["sha256"] for key, value in sorted(dynamic.items())
        },
        "environment": dict(sorted(PINNED_ENVIRONMENT.items())),
    }
    return basis, sha256_bytes(canonical_bytes(basis))


def run_dashboard_branch(
    run_dir: Path,
    *,
    phase: str,
    workspace_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    run_dir = validate_run_dir(run_dir)
    if phase not in {"first", "second"}:
        raise DashboardBranchError("phase must be first or second")
    code = audit_code_contract(workspace_root)
    network = validate_network_receipt(run_dir, phase=phase)
    say_do_second = (
        validate_say_do_second_receipt(run_dir)
        if phase == "second"
        else None
    )

    first_prior = read_prior_phase(run_dir, "first")
    if phase == "second" and first_prior is None:
        raise DashboardBranchError(
            "second-pass dashboard run requires a valid first-pass receipt"
        )
    prior_static = (
        (first_prior.get("static_inputs") or {})
        if phase == "second" and first_prior
        else None
    )
    static, absent_static = materialize_static_inputs(
        run_dir,
        workspace_root=workspace_root,
        prior_static=prior_static,
    )
    dynamic = collect_dynamic_inputs(run_dir)
    basis, generation_id = build_generation_basis(
        phase=phase,
        network=network,
        say_do_second=say_do_second,
        code=code,
        static=static,
        absent_static=absent_static,
        dynamic=dynamic,
    )
    receipt_relative, mirror_relative = receipt_paths(phase)
    receipt_path = safe_child(run_dir, receipt_relative)
    mirror_path = safe_child(run_dir, mirror_relative)
    prior = read_prior_phase(run_dir, phase)
    if prior is not None:
        if (
            prior.get("generation_id") != generation_id
            or prior.get("generation_basis_sha256") != generation_id
        ):
            raise DashboardBranchError(
                f"existing {phase}-pass dashboard receipt is stale for inputs"
            )
        validate_working_receipts(
            run_dir, prior.get("declared_outputs") or {}
        )
        validate_outputs(run_dir)
        return {**prior, "_idempotent_reuse": True}

    if phase == "first":
        partial = [
            relative
            for relative in WORKING_OUTPUTS
            if safe_child(run_dir, relative).exists()
        ]
        if partial:
            raise DashboardBranchError(
                "partial dashboard generation exists without a receipt: "
                + ", ".join(partial)
            )
    else:
        assert first_prior is not None
        # A started marker bound to the first-pass generation means an earlier
        # second pass already cleared the outputs; skip the (now impossible)
        # first-pass hash check so a failed attempt can be retried.
        started = safe_child(run_dir, SECOND_PASS_STARTED)
        first_id = str(first_prior.get("generation_id"))
        if not (
            started.is_file()
            and started.read_text(encoding="utf-8").strip() == first_id
        ):
            validate_working_receipts(
                run_dir, first_prior.get("declared_outputs") or {}
            )
            atomic_bytes(started, first_id.encode("utf-8"))
        # Exact, run-confined overwrite targets only.  The first-pass receipt
        # remains immutable as the before-state audit.
        for relative in WORKING_OUTPUTS:
            path = safe_child(run_dir, relative)
            if path.is_file():
                path.unlink()

    production_before = production_output_guard(workspace_root)
    input_hashes_before = {
        **{key: value["sha256"] for key, value in static.items()},
        **{key: value["sha256"] for key, value in dynamic.items()},
    }
    producer_dir = safe_child(run_dir, "intermediate/dashboard_producer")
    producer_dir.mkdir(parents=True, exist_ok=True)
    executions = []
    executions.append(
        run_producer(
            "fec/surfaces/build_dashboard_bundle.py",
            run_dir,
            workspace_root=workspace_root,
            extra_environment={
                "DASH_OUT": str(producer_dir / "dashboard_data.json")
            },
        )
    )
    produced_bipartisan = read_object(producer_dir / "bipartisan.json")
    staged_bipartisan = read_object(
        safe_child(run_dir, "bipartisan.json")
    )
    if produced_bipartisan != staged_bipartisan:
        raise DashboardBranchError(
            "dashboard producer's bipartisanship differs from upstream "
            "staged authority"
        )
    atomic_copy(
        producer_dir / "dashboard_data.json",
        safe_child(run_dir, "dashboard_data.json"),
    )
    executions.append(
        run_producer(
            "fec/surfaces/make_dashboard_html.py",
            run_dir,
            workspace_root=workspace_root,
        )
    )
    freshness_pin = write_freshness_pin(
        run_dir, generation_id, {**static, **dynamic}
    )
    executions.append(
        run_producer(
            "fec/surfaces/make_member_desk.py",
            run_dir,
            workspace_root=workspace_root,
            extra_environment={"DESK_FRESHNESS_PIN": str(freshness_pin)},
        )
    )
    verify_member_desk_freshness(
        run_dir, generation_id, {**static, **dynamic}
    )
    executions.append(
        run_producer(
            "fec/surfaces/make_combined.py",
            run_dir,
            workspace_root=workspace_root,
        )
    )

    current_static = {
        key: logical_receipt(safe_child(run_dir, key), run_dir)
        for key in static
    }
    current_dynamic = collect_dynamic_inputs(run_dir)
    input_hashes_after = {
        **{
            key: value["sha256"]
            for key, value in current_static.items()
        },
        **{
            key: value["sha256"]
            for key, value in current_dynamic.items()
        },
    }
    if input_hashes_before != input_hashes_after:
        changed = sorted(
            key
            for key in set(input_hashes_before) | set(input_hashes_after)
            if input_hashes_before.get(key) != input_hashes_after.get(key)
        )
        raise DashboardBranchError(
            "dashboard producer changed staged inputs: " + ", ".join(changed)
        )
    if production_output_guard(workspace_root) != production_before:
        raise DashboardBranchError(
            "production output guard changed during isolated execution"
        )

    counts = validate_outputs(run_dir)
    declared = output_receipts(run_dir)
    result = {
        "schema": SCHEMA,
        "status": "PASS",
        "phase": phase,
        "staged_only": True,
        "production_or_mainframe_written": False,
        "final_production_roles_declared_here": False,
        "algorithms_changed": False,
        "volatile_freshness_canonicalized_only": True,
        "generation_id": generation_id,
        "generation_basis_sha256": generation_id,
        "generation_basis": basis,
        "network_prerequisite": network,
        "say_do_second_prerequisite": say_do_second,
        "code_inputs": code,
        "static_inputs": static,
        "absent_optional_static_inputs": absent_static,
        "dynamic_inputs": dynamic,
        "input_hashes_unchanged_during_execution": True,
        "production_output_guard_unchanged": True,
        "pinned_environment": dict(sorted(PINNED_ENVIRONMENT.items())),
        "executions": executions,
        "output_contract": counts,
        "declared_outputs": declared,
        "declared_plan_output": receipt_relative.as_posix(),
    }
    body = (
        json.dumps(
            result, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )
    atomic_bytes(receipt_path, body)
    atomic_bytes(mirror_path, body)
    return {**result, "_idempotent_reuse": False}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Run the legacy dashboard/member-desk branch in an isolated, "
            "hash-bound legislation shadow generation."
        )
    )
    result.add_argument(
        "--phase", choices=("first", "second"), default="first"
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        run_dir = validate_run_dir(Path(MB_ROOT))
        validate_shadow_environment(run_dir)
        result = run_dashboard_branch(run_dir, phase=args.phase)
    except (
        DashboardBranchError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"legislation dashboard/member desk: FAIL CLOSED: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
