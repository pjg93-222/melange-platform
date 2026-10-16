# -*- coding: utf-8 -*-
"""Seal the final network and dashboard outputs after the second say/do pass.

This coordinator is intentionally a thin, staged-only integration layer.  It
does not alter any legacy model formula.  It:

* proves that ``say_do_second_pass.receipt.json`` belongs to the accepted
  generation recorded in the shadow step ledger;
* builds the network wrapper's prerequisite manifest from that ledger and the
  already-produced upstream receipts;
* runs the network wrapper before the dashboard/member-desk wrapper; and
* reruns say/do in an isolated probe against the final member desk and requires
  byte parity with the accepted second pass, proving there is no one-generation
  say/do lag;
* declares the final baseline-role artifacts in one stable receipt.

No artifact is copied to the repository root or mainframe.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT as MB_ROOT  # noqa: E402 - audited root-aware contract
from legislation import run_legislation_network_dependents as network  # noqa: E402
from legislation import run_legislation_dashboard_member_desk as dashboard  # noqa: E402
from legislation import run_legislation_say_do_first_pass as say_do_runner  # noqa: E402

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes


REPO_ROOT = Path(__file__).resolve().parents[2]
FEC_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "legislation-second-pass-dependents-receipt-v1"
STEP_LEDGER_SCHEMA = "legislation-shadow-step-receipts-v1"
SAY_DO_SCHEMA = "legislation-say-do-pass-receipt-v1"
ACCEPTED_BINDING_SCHEMA = "legislation-second-pass-artifact-binding-v1"
HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)

STEP_LEDGER = Path("intermediate") / "shadow_step_receipts.json"
INPUT_MATERIALIZATION = Path("input_materialization.json")
SAY_DO_SECOND_RECEIPT = (
    Path("intermediate") / "say_do_second_pass.receipt.json"
)
NETWORK_SECOND_PREREQUISITES = (
    Path("intermediate") / "network_dependents_second_pass.prerequisites.json"
)
NETWORK_BINDING_DIR = (
    Path("intermediate") / "network_second_pass_bindings"
)
FINAL_RECEIPT = (
    Path("intermediate") / "second_pass_dependents.receipt.json"
)
SAY_DO_FIXED_POINT_RECEIPT = (
    Path("intermediate") / "say_do_fixed_point.receipt.json"
)
SAY_DO_FIXED_POINT_DIR = (
    Path("intermediate") / "say_do_fixed_point_probe"
)
SAY_DO_FIXED_POINT_SCHEMA = "legislation-say-do-fixed-point-proof-v1"

FINAL_OUTPUTS = (
    "data/money/electoral_plays.json",
    "per_topic_alloc.json",
    "data/lobbying/per_topic_coalition.json",
    "data/money/budget_frontier.json",
    "data/money/budget_frontier_electoral.json",
    "optimize_result.json",
    "im_allocation.json",
    "salience_advisory.json",
    "dashboard_data.json",
    "Moneyball Dashboard.html",
    "member_desk_bundle.json",
    "cu-member-desk.html",
    "dashboard_exclusion_reconciliation.json",
    "Moneyball.html",
)

NETWORK_FINAL_OUTPUTS = tuple(network.OUTPUT_NAMES)
DASHBOARD_FINAL_OUTPUTS = (
    "dashboard_data.json",
    "Moneyball Dashboard.html",
    "member_desk_bundle.json",
    "cu-member-desk.html",
    "dashboard_exclusion_reconciliation.json",
    "Moneyball.html",
)

# The step ledger is the accepted-generation authority for artifacts whose
# legacy producer has no standalone receipt.
ARTIFACT_SOURCE_STEPS: Mapping[str, str] = {
    "Moneyball Main Version - Blended.xlsx": (
        "recompute_legislative_raw_normalized_sublayer"
    ),
    "ranks_python.json": "recompute_pillars_and_ranks",
    "category_stance.json": "recompute_category_stance",
    "congress_cosponsor_network_AUG_shadow.xlsx": (
        "recompute_augmented_network"
    ),
    "member_receptivity.json": "recompute_receptivity",
    "bipartisan.json": (
        "materialize_base_network_and_prebehavior_bipartisan"
    ),
    "say_do_gap.json": "recompute_say_do_second_pass",
}

CANONICAL_RECEIPTS: Mapping[str, Path] = {
    "ranks_python.json": Path("pillars_ranks.receipt.json"),
    "bipartisan.json": Path("base_bipartisan_manifest.json"),
    "say_do_gap.json": SAY_DO_SECOND_RECEIPT,
}


class SecondPassError(RuntimeError):
    """A generation, freshness, isolation, or child-wrapper gate failed."""


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
        raise SecondPassError(
            f"isolated run directory does not exist: {run_dir}"
        )
    if run_dir == REPO_ROOT.resolve():
        raise SecondPassError("production/workspace root is forbidden")
    if not any(is_relative_to(run_dir, root) for root in allowed_roots()):
        raise SecondPassError(
            "MONEYBALL_ROOT must be under legislation shadow audit or temp"
        )
    return run_dir


def safe_child(run_dir: Path, relative: str | Path) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise SecondPassError(f"unsafe run-relative path: {relative}")
    path = (run_dir / raw).resolve()
    if not is_relative_to(path, run_dir):
        raise SecondPassError(f"path escapes isolated root: {relative}")
    return path


def validate_shadow_environment(run_dir: Path) -> None:
    if os.environ.get("LEGISLATION_SHADOW_MODE") != "1":
        raise SecondPassError("LEGISLATION_SHADOW_MODE=1 is required")
    declared = os.environ.get("LEGISLATION_SHADOW_RUN_DIR")
    if not declared or Path(declared).resolve() != run_dir:
        raise SecondPassError(
            "LEGISLATION_SHADOW_RUN_DIR must equal MONEYBALL_ROOT"
        )


def read_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SecondPassError(f"required JSON is missing: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SecondPassError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SecondPassError(f"JSON root must be an object: {path}")
    return value


def logical_receipt(path: Path, run_dir: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not is_relative_to(resolved, run_dir.resolve()):
        raise SecondPassError(f"receipt path is outside run root: {resolved}")
    stat = resolved.stat()
    return {
        "path": resolved.relative_to(run_dir.resolve()).as_posix(),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def source_receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def atomic_json(path: Path, value: Any) -> None:
    body = (
        json.dumps(
            value, indent=2, sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
        + b"\n"
    )
    # fsync + retried replace + temp cleanup live in lane_file_io (OneDrive/AV locks).
    atomic_write_bytes(path, body)


def atomic_copy(source: Path, destination: Path) -> None:
    body = source.read_bytes()
    if sha256_bytes(body) != sha256_file(source):
        raise SecondPassError(f"staged proof copy failed: {source.name}")
    atomic_write_bytes(destination, body)


def ledger_output(
    ledger: Mapping[str, Any], *, step_name: str, relative: str
) -> dict[str, Any]:
    matching_steps = [
        row
        for row in ledger.get("completed_steps") or []
        if isinstance(row, dict) and row.get("name") == step_name
    ]
    if len(matching_steps) != 1:
        raise SecondPassError(
            f"exact missing upstream receipt: ledger step {step_name}"
        )
    outputs = matching_steps[0].get("outputs")
    if not isinstance(outputs, dict):
        raise SecondPassError(
            f"exact missing upstream receipt: outputs for {step_name}"
        )
    normalized = relative.replace("\\", "/")
    matching = [
        item
        for key, item in outputs.items()
        if str(key).replace("\\", "/") == normalized
        and isinstance(item, dict)
    ]
    if len(matching) != 1:
        raise SecondPassError(
            "exact missing upstream receipt: "
            f"{step_name}/{normalized}"
        )
    sha = str(matching[0].get("sha256") or "").casefold()
    if not HEX_RE.fullmatch(sha):
        raise SecondPassError(
            f"ledger output lacks SHA-256: {step_name}/{normalized}"
        )
    return {"path": normalized, "sha256": sha}


def validate_step_ledger(run_dir: Path) -> tuple[dict[str, Any], str]:
    path = safe_child(run_dir, STEP_LEDGER)
    ledger = read_object(path)
    generation_id = str(ledger.get("generation_id") or "").casefold()
    if not (
        ledger.get("schema") == STEP_LEDGER_SCHEMA
        and ledger.get("status") in {"RUNNING", "COMPLETE"}
        and ledger.get("staged_only") is True
        and ledger.get("production_written") is False
        and HEX_RE.fullmatch(generation_id)
    ):
        raise SecondPassError(
            "shadow step ledger is not a staged accepted generation"
        )
    # This also proves that the second pass is complete in the accepted ledger.
    ledger_output(
        ledger,
        step_name="recompute_say_do_second_pass",
        relative=SAY_DO_SECOND_RECEIPT.as_posix(),
    )
    ledger_output(
        ledger,
        step_name="recompute_say_do_second_pass",
        relative="say_do_gap.json",
    )
    return ledger, generation_id


def validate_accepted_input_manifest(
    run_dir: Path, ledger: Mapping[str, Any], generation_id: str
) -> dict[str, Any]:
    accepted = ledger.get("accepted_generation")
    if not isinstance(accepted, dict):
        raise SecondPassError("accepted-generation ledger binding is absent")
    manifest_hash = str(
        accepted.get("manifest_sha256") or ""
    ).casefold()
    if (
        not HEX_RE.fullmatch(manifest_hash)
        or str(accepted.get("canonical_ledger_sha256") or "").casefold()
        != generation_id
    ):
        raise SecondPassError("accepted-generation ledger binding is invalid")

    materialization = read_object(
        safe_child(run_dir, INPUT_MATERIALIZATION)
    )
    if materialization.get("schema") != (
        "legislation-shadow-input-materialization-v1"
    ):
        raise SecondPassError("accepted input materialization is invalid")
    candidates: list[Path] = []
    for row in materialization.get("records") or []:
        if not isinstance(row, dict):
            continue
        if row.get("source_group") != "accepted_generation":
            continue
        if str(
            row.get("materialized_sha256")
            or row.get("source_sha256")
            or ""
        ).casefold() != manifest_hash:
            continue
        relative = str(row.get("materialized_path") or "")
        try:
            path = safe_child(run_dir, relative)
        except SecondPassError:
            continue
        if path.is_file() and sha256_file(path) == manifest_hash:
            candidates.append(path)
    if len(candidates) != 1:
        raise SecondPassError(
            "accepted input manifest is missing or ambiguous"
        )
    manifest = read_object(candidates[0])
    if str(
        manifest.get("canonical_ledger_sha256") or ""
    ).casefold() != generation_id:
        raise SecondPassError(
            "accepted input manifest has a mixed generation"
        )
    return logical_receipt(candidates[0], run_dir)


def _receipt_declared_path(
    run_dir: Path, receipt_path: Path, item: Mapping[str, Any]
) -> Path:
    raw = str(item.get("path") or "")
    if not raw:
        raise SecondPassError(
            f"receipt lacks declared output path: {receipt_path}"
        )
    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        root_relative = (run_dir / candidate).resolve()
        receipt_relative = (receipt_path.parent / candidate).resolve()
        resolved = (
            root_relative
            if root_relative.is_file() or not receipt_relative.is_file()
            else receipt_relative
        )
    if not is_relative_to(resolved, run_dir.resolve()):
        raise SecondPassError(
            f"receipt output escapes isolated root: {resolved}"
        )
    return resolved


def validate_say_do_second(
    run_dir: Path, ledger: Mapping[str, Any], generation_id: str
) -> dict[str, Any]:
    receipt_path = safe_child(run_dir, SAY_DO_SECOND_RECEIPT)
    receipt_value = read_object(receipt_path)
    if not (
        receipt_value.get("schema") == SAY_DO_SCHEMA
        and receipt_value.get("status") == "PASS"
        and receipt_value.get("pass") == "second"
        and str(receipt_value.get("generation_id") or "").casefold()
        == generation_id
        and receipt_value.get("staged_only") is True
        and receipt_value.get("production_or_mainframe_written") is False
    ):
        raise SecondPassError(
            "second-pass say/do receipt is not a fresh accepted generation"
        )
    declared = receipt_value.get("declared_output")
    if not isinstance(declared, dict):
        raise SecondPassError("second-pass say/do declared output is absent")
    output_path = _receipt_declared_path(
        run_dir, receipt_path, declared
    )
    expected_output = safe_child(run_dir, "say_do_gap.json")
    if output_path != expected_output or not output_path.is_file():
        raise SecondPassError(
            "second-pass say/do declared output is not say_do_gap.json"
        )
    output_sha = sha256_file(output_path)
    if str(declared.get("sha256") or "").casefold() != output_sha:
        raise SecondPassError(
            "second-pass say/do output hash differs from its receipt"
        )
    ledger_receipt = ledger_output(
        ledger,
        step_name="recompute_say_do_second_pass",
        relative=SAY_DO_SECOND_RECEIPT.as_posix(),
    )
    ledger_output_item = ledger_output(
        ledger,
        step_name="recompute_say_do_second_pass",
        relative="say_do_gap.json",
    )
    if (
        ledger_receipt["sha256"] != sha256_file(receipt_path)
        or ledger_output_item["sha256"] != output_sha
    ):
        raise SecondPassError(
            "second-pass say/do receipt/output is stale for step ledger"
        )
    return {
        "receipt": logical_receipt(receipt_path, run_dir),
        "output": logical_receipt(output_path, run_dir),
        "expected_input_sha256": dict(
            sorted(
                (
                    receipt_value.get("expected_input_sha256")
                    or {}
                ).items()
            )
        ),
    }


def _canonical_upstream_path(
    run_dir: Path, artifact_path: str
) -> Path | None:
    relative = CANONICAL_RECEIPTS.get(artifact_path)
    if relative is None:
        return None
    path = safe_child(run_dir, relative)
    return path if path.is_file() else None


def build_artifact_binding(
    run_dir: Path,
    *,
    logical_name: str,
    artifact_path: str,
    artifact_sha256: str,
    generation_id: str,
    ledger: Mapping[str, Any],
    expected_schema: str | None,
) -> dict[str, Any]:
    step_name = ARTIFACT_SOURCE_STEPS.get(artifact_path)
    if not step_name:
        raise SecondPassError(
            f"no accepted-generation source step for {artifact_path}"
        )
    bound = ledger_output(
        ledger, step_name=step_name, relative=artifact_path
    )
    if bound["sha256"] != artifact_sha256:
        raise SecondPassError(
            f"artifact is stale for step ledger: {logical_name}"
        )

    canonical_path = _canonical_upstream_path(run_dir, artifact_path)
    canonical_value: dict[str, Any] | None = None
    if canonical_path is not None:
        canonical_value = read_object(canonical_path)
        canonical_has_artifact = artifact_sha256 in json.dumps(
            canonical_value, sort_keys=True
        ).casefold()
        canonical_generation = str(
            canonical_value.get("generation_id") or ""
        ).casefold()
        canonical_schema = canonical_value.get("schema")
        if (
            canonical_value.get("status") == "PASS"
            and canonical_has_artifact
            and canonical_generation == generation_id
            and (
                expected_schema is None
                or canonical_schema == expected_schema
            )
        ):
            return logical_receipt(canonical_path, run_dir)

    # Some legacy producers do not own accepted-generation receipts, and the
    # base-bipartisan producer has a content-generation id distinct from the
    # accepted ledger id.  Seal an explicit adapter receipt whose source and
    # artifact are both independently checked above.
    binding_relative = (
        NETWORK_BINDING_DIR / f"{logical_name}.receipt.json"
    )
    binding_path = safe_child(run_dir, binding_relative)
    value = {
        "schema": expected_schema or ACCEPTED_BINDING_SCHEMA,
        "status": "PASS",
        "receipt_role": "accepted-generation-binding-adapter",
        "generation_id": generation_id,
        "staged_only": True,
        "production_written": False,
        "mainframe_written": False,
        "source_step": step_name,
        "artifact": {
            "logical_name": logical_name,
            "path": artifact_path,
            "sha256": artifact_sha256,
        },
        "source_receipt": (
            logical_receipt(canonical_path, run_dir)
            if canonical_path is not None
            else None
        ),
        "source_schema": (
            canonical_value.get("schema")
            if canonical_value is not None
            else None
        ),
        "source_generation_id": (
            canonical_value.get("generation_id")
            if canonical_value is not None
            else None
        ),
    }
    atomic_json(binding_path, value)
    return logical_receipt(binding_path, run_dir)


def build_network_prerequisites(
    run_dir: Path,
    *,
    ledger: Mapping[str, Any],
    generation_id: str,
) -> tuple[Path, dict[str, Any]]:
    artifacts: dict[str, dict[str, Any]] = {}
    upstream_receipts: dict[str, dict[str, Any]] = {}
    for logical_name, contract in sorted(
        network.REQUIRED_ARTIFACTS.items()
    ):
        artifact_path = safe_child(run_dir, contract.path)
        if not artifact_path.is_file():
            raise SecondPassError(
                f"network prerequisite artifact is missing: {contract.path}"
            )
        artifact_sha = sha256_file(artifact_path)
        item: dict[str, Any] = {
            "path": contract.path,
            "sha256": artifact_sha,
            "generation_bound": contract.generation_bound,
        }
        if contract.generation_bound:
            receipt_name = contract.upstream_receipt
            if not receipt_name:
                raise SecondPassError(
                    f"generation-bound artifact lacks receipt: {logical_name}"
                )
            expected_schema = network.EXPECTED_RECEIPT_SCHEMAS.get(
                receipt_name
            )
            bound_receipt = build_artifact_binding(
                run_dir,
                logical_name=logical_name,
                artifact_path=contract.path,
                artifact_sha256=artifact_sha,
                generation_id=generation_id,
                ledger=ledger,
                expected_schema=expected_schema,
            )
            existing = upstream_receipts.get(receipt_name)
            entry = {
                **bound_receipt,
                "generation_id": generation_id,
            }
            if existing is not None and existing != entry:
                raise SecondPassError(
                    f"upstream receipt name collision: {receipt_name}"
                )
            upstream_receipts[receipt_name] = entry
            item.update(
                {
                    "generation_id": generation_id,
                    "upstream_receipt": receipt_name,
                }
            )
        artifacts[logical_name] = item

    value = {
        "schema": network.PREREQUISITES_SCHEMA,
        "status": "PASS",
        "phase": "second",
        "staged_only": True,
        "production_written": False,
        "mainframe_written": False,
        "generation_id": generation_id,
        "artifacts": artifacts,
        "upstream_receipts": upstream_receipts,
        "source_step_ledger": logical_receipt(
            safe_child(run_dir, STEP_LEDGER), run_dir
        ),
    }
    path = safe_child(run_dir, NETWORK_SECOND_PREREQUISITES)
    atomic_json(path, value)
    # Prove compatibility before invoking a child process.
    try:
        network.validate_prerequisites(run_dir, path)
    except Exception as exc:
        raise SecondPassError(
            f"refreshed network prerequisite is invalid: {exc}"
        ) from exc
    return path, value


def _wrapper_supports_phase(module: Any) -> bool:
    try:
        return any(
            action.dest == "phase"
            for action in module.parser()._actions
        )
    except (AttributeError, TypeError):
        return False


def network_receipt_relative() -> Path:
    for name in (
        "SECOND_RECEIPT_NAME",
        "SECOND_RECEIPT",
        "RECEIPT_NAME",
    ):
        value = getattr(network, name, None)
        if value is not None:
            return Path(value)
    raise SecondPassError("network wrapper exposes no receipt path")


def run_child(
    run_dir: Path,
    command: Sequence[str],
    *,
    label: str,
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.update(
        {
            "MONEYBALL_ROOT": str(run_dir),
            "LEGISLATION_SHADOW_MODE": "1",
            "LEGISLATION_SHADOW_RUN_DIR": str(run_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    completed = subprocess.run(
        list(command),
        cwd=run_dir,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        diagnostic = (completed.stderr or completed.stdout)[-800:]
        raise SecondPassError(
            f"{label} failed with exit {completed.returncode}: {diagnostic}"
        )
    return {
        "label": label,
        "argv_sha256": sha256_bytes(canonical_bytes(list(command))),
        "exit_code": completed.returncode,
        "stdout_sha256": sha256_bytes(completed.stdout.encode("utf-8")),
        "stderr_sha256": sha256_bytes(completed.stderr.encode("utf-8")),
    }


def validate_child_receipt(
    run_dir: Path,
    *,
    relative: Path,
    schema: str,
    generation_id: str,
    required_outputs: Sequence[str],
    exact_generation_id: bool = True,
) -> dict[str, Any]:
    path = safe_child(run_dir, relative)
    value = read_object(path)
    child_generation = str(value.get("generation_id") or "").casefold()
    generation_ok = (
        child_generation == generation_id
        if exact_generation_id
        else (
            bool(HEX_RE.fullmatch(child_generation))
            and value.get("generation_basis_sha256") == child_generation
            and generation_id in json.dumps(value, sort_keys=True).casefold()
        )
    )
    if not (
        value.get("schema") == schema
        and value.get("status") == "PASS"
        and generation_ok
    ):
        raise SecondPassError(
            f"child receipt is not current/PASS: {relative.as_posix()}"
        )
    declared = value.get("declared_outputs")
    if not isinstance(declared, dict):
        raise SecondPassError(
            f"child receipt lacks declared outputs: {relative.as_posix()}"
        )
    for output in required_outputs:
        item = declared.get(output)
        if not isinstance(item, dict):
            raise SecondPassError(
                f"child receipt omits final output: {output}"
            )
        output_path = safe_child(run_dir, output)
        if (
            not output_path.is_file()
            or str(item.get("sha256") or "").casefold()
            != sha256_file(output_path)
        ):
            raise SecondPassError(
                f"child output hash mismatch: {output}"
            )
    return logical_receipt(path, run_dir)


def validate_dashboard_second_binding(
    run_dir: Path,
    *,
    generation_id: str,
    say_do: Mapping[str, Any],
) -> dict[str, Any]:
    """Prove the final display branch consumed the exact second say/do bytes."""
    path = safe_child(run_dir, Path(dashboard.SECOND_RECEIPT_MIRROR))
    value = read_object(path)
    expected = str((say_do.get("output") or {}).get("sha256") or "").casefold()
    if not HEX_RE.fullmatch(expected):
        raise SecondPassError("second-pass say/do proof lacks an output SHA-256")

    prerequisite = value.get("say_do_second_prerequisite") or {}
    generation_basis = value.get("generation_basis") or {}
    basis_say_do = generation_basis.get("say_do_second_generation") or {}
    dynamic = value.get("dynamic_inputs") or {}
    dynamic_say_do = dynamic.get("say_do_gap.json") or {}
    network_prerequisite = value.get("network_prerequisite") or {}
    observed = {
        "receipt_prerequisite": str(
            (prerequisite.get("declared_output") or {}).get("sha256") or ""
        ).casefold(),
        "generation_basis": str(
            (basis_say_do.get("declared_output") or {}).get("sha256") or ""
        ).casefold(),
        "dynamic_input": str(
            dynamic_say_do.get("sha256") or ""
        ).casefold(),
    }
    if not (
        value.get("schema") == dashboard.SCHEMA
        and value.get("status") == "PASS"
        and value.get("phase") == "second"
        and str(network_prerequisite.get("generation_id") or "").casefold()
        == generation_id
        and all(item == expected for item in observed.values())
    ):
        raise SecondPassError(
            "final dashboard/member-desk receipt is not bound to the exact "
            "second say/do output"
        )
    return {
        "receipt": logical_receipt(path, run_dir),
        "second_say_do_sha256": expected,
        "observed_bindings": observed,
        "accepted_generation_id": generation_id,
        "binding_complete": True,
    }


def validate_say_do_fixed_point_receipt(
    run_dir: Path,
    *,
    generation_id: str,
    say_do: Mapping[str, Any],
) -> dict[str, Any]:
    path = safe_child(run_dir, SAY_DO_FIXED_POINT_RECEIPT)
    value = read_object(path)
    second_sha = str(
        (say_do.get("output") or {}).get("sha256") or ""
    ).casefold()
    final_desk = safe_child(run_dir, "member_desk_bundle.json")
    final_lep = safe_child(run_dir, "data/members/lep_criterion.json")
    probe_output = safe_child(
        run_dir, SAY_DO_FIXED_POINT_DIR / "say_do_gap.json"
    )
    probe_desk = safe_child(
        run_dir, SAY_DO_FIXED_POINT_DIR / "member_desk_bundle.json"
    )
    probe_lep = safe_child(
        run_dir, SAY_DO_FIXED_POINT_DIR / "data/members/lep_criterion.json"
    )
    if not all(
        item.is_file()
        for item in (
            final_desk,
            final_lep,
            probe_output,
            probe_desk,
            probe_lep,
        )
    ):
        raise SecondPassError("say/do fixed-point evidence is incomplete")
    if not (
        value.get("schema") == SAY_DO_FIXED_POINT_SCHEMA
        and value.get("status") == "PASS"
        and value.get("generation_id") == generation_id
        and value.get("parity") is True
        and value.get("second_say_do_sha256") == second_sha
        and value.get("probe_output_sha256") == second_sha
        and value.get("final_member_desk_sha256")
        == sha256_file(final_desk)
        and value.get("probe_member_desk_sha256")
        == sha256_file(probe_desk)
        and value.get("lep_criterion_sha256") == sha256_file(final_lep)
        and sha256_file(probe_lep) == sha256_file(final_lep)
        and sha256_file(probe_output) == second_sha
    ):
        raise SecondPassError(
            "say/do fixed-point receipt is stale or parity is unproven"
        )
    return {
        "receipt": logical_receipt(path, run_dir),
        "proof": value,
    }


def prove_say_do_fixed_point(
    run_dir: Path,
    *,
    generation_id: str,
    say_do: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute say/do from final member-desk input and require byte parity.

    The probe runs in its own child directory, so the accepted second-pass
    ``say_do_gap.json`` is never overwritten.  Any mismatch proves a
    one-generation lag: another say/do pass would change a dynamic input to
    the final dashboard/member-desk branch.  Such a run cannot be accepted.
    """
    receipt_path = safe_child(run_dir, SAY_DO_FIXED_POINT_RECEIPT)
    if receipt_path.is_file():
        return validate_say_do_fixed_point_receipt(
            run_dir, generation_id=generation_id, say_do=say_do
        )

    probe_dir = safe_child(run_dir, SAY_DO_FIXED_POINT_DIR)
    probe_dir.mkdir(parents=True, exist_ok=True)
    final_desk = safe_child(run_dir, "member_desk_bundle.json")
    final_lep = safe_child(run_dir, "data/members/lep_criterion.json")
    if not final_desk.is_file() or not final_lep.is_file():
        raise SecondPassError(
            "final say/do fixed-point inputs are absent"
        )
    probe_desk = probe_dir / "member_desk_bundle.json"
    probe_lep = probe_dir / "data/members/lep_criterion.json"
    atomic_copy(final_desk, probe_desk)
    atomic_copy(final_lep, probe_lep)
    try:
        expected_inputs = say_do_runner.input_receipts(
            probe_dir, say_do_runner.DIRECT_INPUTS
        )
        execution, execution_stream_hashes = (
            say_do_runner.run_producer_twice(
                probe_dir,
                say_do_runner.PRODUCER,
                expected_inputs,
            )
        )
    except say_do_runner.SayDoError as exc:
        raise SecondPassError(
            f"say/do fixed-point probe failed: {exc}"
        ) from exc
    probe_output = probe_dir / say_do_runner.OUTPUT_NAME
    second_sha = str(
        (say_do.get("output") or {}).get("sha256") or ""
    ).casefold()
    probe_sha = sha256_file(probe_output)
    parity = bool(HEX_RE.fullmatch(second_sha) and probe_sha == second_sha)
    proof = {
        "schema": SAY_DO_FIXED_POINT_SCHEMA,
        "status": "PASS" if parity else "BLOCKED",
        "generation_id": generation_id,
        "staged_only": True,
        "production_or_mainframe_written": False,
        "second_say_do_sha256": second_sha,
        "probe_output_sha256": probe_sha,
        "final_member_desk_sha256": sha256_file(final_desk),
        "probe_member_desk_sha256": sha256_file(probe_desk),
        "lep_criterion_sha256": sha256_file(final_lep),
        "parity": parity,
        "proof_method": (
            "rerun unchanged say_do_gap.py twice against the final "
            "member_desk_bundle.json and require byte identity with the "
            "accepted second-pass say_do_gap.json"
        ),
        "execution": execution,
        "execution_stream_hashes": execution_stream_hashes,
        "probe_output": logical_receipt(probe_output, run_dir),
    }
    atomic_json(receipt_path, proof)
    if not parity:
        raise SecondPassError(
            "say/do fixed-point failed: final member-desk inputs would "
            "change the second-pass say/do result and leave a one-generation lag"
        )
    return {
        "receipt": logical_receipt(receipt_path, run_dir),
        "proof": proof,
    }


def snapshot_production_outputs() -> dict[str, str | None]:
    return {
        output: (
            sha256_file(REPO_ROOT / output)
            if (REPO_ROOT / output).is_file()
            else None
        )
        for output in FINAL_OUTPUTS
    }


def verify_production_unchanged(
    before: Mapping[str, str | None]
) -> None:
    after = snapshot_production_outputs()
    if dict(before) != after:
        changed = [
            name
            for name in FINAL_OUTPUTS
            if before.get(name) != after.get(name)
        ]
        raise SecondPassError(
            f"production output guard changed: {changed}"
        )


def validate_final_outputs(run_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for relative in FINAL_OUTPUTS:
        path = safe_child(run_dir, relative)
        if not path.is_file() or path.stat().st_size == 0:
            raise SecondPassError(f"final output is absent/empty: {relative}")
        if path.suffix.casefold() == ".json":
            read_object(path)
        result[relative] = logical_receipt(path, run_dir)
    return result


def audit_wrappers() -> dict[str, dict[str, Any]]:
    contracts = {
        "network": (
            FEC_ROOT / "legislation" / "run_legislation_network_dependents.py",
            (
                "from paths import ROOT as MB_ROOT",
                "def validate_prerequisites",
            ),
        ),
        "dashboard_member_desk": (
            FEC_ROOT / "legislation" / "run_legislation_dashboard_member_desk.py",
            (
                "from paths import ROOT as MB_ROOT",
                "--phase",
                "second",
            ),
        ),
        "say_do_fixed_point_runner": (
            FEC_ROOT / "legislation" / "run_legislation_say_do_first_pass.py",
            (
                "def run_producer_twice",
                "byte-deterministic across repeats",
            ),
        ),
        "say_do_producer": (
            FEC_ROOT / "stance" / "say_do_gap.py",
            (
                "ROLE_W",
                "ADV_W",
                "member_desk_bundle.json",
            ),
        ),
    }
    result: dict[str, dict[str, Any]] = {}
    for name, (path, tokens) in contracts.items():
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        missing = [token for token in tokens if token not in text]
        if missing:
            raise SecondPassError(
                f"child wrapper contract changed: {name}/{missing}"
            )
        result[name] = source_receipt(path)
    result["coordinator"] = source_receipt(Path(__file__))
    return result


def run_second_pass_dependents(run_dir: Path) -> dict[str, Any]:
    run_dir = validate_run_dir(run_dir)
    validate_shadow_environment(run_dir)
    wrappers = audit_wrappers()
    ledger, generation_id = validate_step_ledger(run_dir)
    accepted_manifest = validate_accepted_input_manifest(
        run_dir, ledger, generation_id
    )
    say_do = validate_say_do_second(run_dir, ledger, generation_id)
    prerequisites_path, prerequisites_value = build_network_prerequisites(
        run_dir, ledger=ledger, generation_id=generation_id
    )

    generation_basis = {
        "schema": "legislation-second-pass-dependents-generation-basis-v1",
        "generation_id": generation_id,
        "accepted_manifest_sha256": accepted_manifest["sha256"],
        "say_do_second_output_sha256": say_do["output"]["sha256"],
        "say_do_second_expected_input_sha256": (
            say_do["expected_input_sha256"]
        ),
        "network_prerequisite_value_sha256": sha256_bytes(
            canonical_bytes(prerequisites_value)
        ),
        "wrapper_sha256": {
            key: value["sha256"]
            for key, value in sorted(wrappers.items())
        },
        "order": [
            "network_dependents",
            "dashboard_member_desk",
            "say_do_fixed_point_proof",
        ],
    }
    basis_sha = sha256_bytes(canonical_bytes(generation_basis))
    final_receipt_path = safe_child(run_dir, FINAL_RECEIPT)
    if final_receipt_path.is_file():
        prior = read_object(final_receipt_path)
        if (
            prior.get("schema") == SCHEMA
            and prior.get("status") == "PASS"
            and prior.get("generation_id") == generation_id
            and prior.get("generation_basis_sha256") == basis_sha
        ):
            outputs = validate_final_outputs(run_dir)
            prior_outputs = prior.get("declared_outputs")
            if isinstance(prior_outputs, dict) and all(
                (prior_outputs.get(name) or {}).get("sha256")
                == item["sha256"]
                for name, item in outputs.items()
            ):
                network_receipt = validate_child_receipt(
                    run_dir,
                    relative=network_receipt_relative(),
                    schema=network.SCHEMA,
                    generation_id=generation_id,
                    required_outputs=NETWORK_FINAL_OUTPUTS,
                )
                dashboard_receipt = validate_child_receipt(
                    run_dir,
                    relative=Path(dashboard.SECOND_RECEIPT_MIRROR),
                    schema=dashboard.SCHEMA,
                    generation_id=generation_id,
                    required_outputs=DASHBOARD_FINAL_OUTPUTS,
                    exact_generation_id=False,
                )
                dashboard_binding = validate_dashboard_second_binding(
                    run_dir,
                    generation_id=generation_id,
                    say_do=say_do,
                )
                fixed_point = validate_say_do_fixed_point_receipt(
                    run_dir,
                    generation_id=generation_id,
                    say_do=say_do,
                )
                prior_children = prior.get("child_receipts") or {}
                prior_binding = prior.get("dashboard_second_pass_binding") or {}
                prior_fixed = prior.get("say_do_fixed_point_proof") or {}
                if not (
                    (prior_children.get("network_dependents") or {}).get(
                        "sha256"
                    )
                    == network_receipt["sha256"]
                    and (
                        prior_children.get("dashboard_member_desk") or {}
                    ).get("sha256")
                    == dashboard_receipt["sha256"]
                    and (prior_binding.get("receipt") or {}).get("sha256")
                    == dashboard_binding["receipt"]["sha256"]
                    and (prior_fixed.get("receipt") or {}).get("sha256")
                    == fixed_point["receipt"]["sha256"]
                ):
                    raise SecondPassError(
                        "existing second-pass proof receipts changed"
                    )
                return {**prior, "_idempotent_reuse": True}
        raise SecondPassError(
            "existing second-pass final receipt is stale or incomplete"
        )

    production_before = snapshot_production_outputs()
    network_command = [
        sys.executable,
        str(FEC_ROOT / "legislation" / "run_legislation_network_dependents.py"),
        "--prerequisites",
        str(prerequisites_path),
    ]
    if _wrapper_supports_phase(network):
        network_command.extend(["--phase", "second"])
    network_execution = run_child(
        run_dir, network_command, label="network_dependents_second_pass"
    )
    verify_production_unchanged(production_before)
    network_receipt = validate_child_receipt(
        run_dir,
        relative=network_receipt_relative(),
        schema=network.SCHEMA,
        generation_id=generation_id,
        required_outputs=NETWORK_FINAL_OUTPUTS,
    )

    dashboard_command = [
        sys.executable,
        str(FEC_ROOT / "legislation" / "run_legislation_dashboard_member_desk.py"),
        "--phase",
        "second",
    ]
    dashboard_execution = run_child(
        run_dir,
        dashboard_command,
        label="dashboard_member_desk_second_pass",
    )
    verify_production_unchanged(production_before)
    dashboard_receipt = validate_child_receipt(
        run_dir,
        relative=Path(dashboard.SECOND_RECEIPT_MIRROR),
        schema=dashboard.SCHEMA,
        generation_id=generation_id,
        required_outputs=DASHBOARD_FINAL_OUTPUTS,
        exact_generation_id=False,
    )
    dashboard_binding = validate_dashboard_second_binding(
        run_dir,
        generation_id=generation_id,
        say_do=say_do,
    )
    outputs = validate_final_outputs(run_dir)
    if tuple(outputs) != FINAL_OUTPUTS:
        raise SecondPassError("final output declaration order/set changed")
    fixed_point = prove_say_do_fixed_point(
        run_dir,
        generation_id=generation_id,
        say_do=say_do,
    )
    verify_production_unchanged(production_before)

    result = {
        "schema": SCHEMA,
        "status": "PASS",
        "acceptance_passed": True,
        "staged_only": True,
        "production_written": False,
        "mainframe_written": False,
        "algorithms_changed": False,
        "generation_id": generation_id,
        "generation_basis_sha256": basis_sha,
        "generation_basis": generation_basis,
        "accepted_input_manifest": accepted_manifest,
        "say_do_second_pass": say_do,
        "refreshed_network_prerequisite": logical_receipt(
            prerequisites_path, run_dir
        ),
        "wrapper_sources": wrappers,
        "documented_order": [
            "network_dependents",
            "dashboard_member_desk",
            "say_do_fixed_point_proof",
        ],
        "executions": [network_execution, dashboard_execution],
        "child_receipts": {
            "network_dependents": network_receipt,
            "dashboard_member_desk": dashboard_receipt,
        },
        "dashboard_second_pass_binding": dashboard_binding,
        "say_do_fixed_point_proof": fixed_point,
        "no_one_generation_say_do_lag": True,
        "final_baseline_outputs": list(FINAL_OUTPUTS),
        "declared_outputs": outputs,
        "complete_final_output_set": True,
        "production_guard_unchanged": True,
        "externally_declared_output": FINAL_RECEIPT.as_posix(),
    }
    atomic_json(final_receipt_path, result)
    return {**result, "_idempotent_reuse": False}


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    try:
        result = run_second_pass_dependents(Path(MB_ROOT))
    except (
        SecondPassError,
        OSError,
        subprocess.SubprocessError,
        ValueError,
    ) as exc:
        print(f"legislation second-pass dependents: FAIL CLOSED: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
