# -*- coding: utf-8 -*-
"""Recompute the network/rank-dependent JSON chain inside one sealed shadow root.

The legacy producers and their formulas remain authoritative and unchanged.  This
wrapper adds only isolation, prerequisite/generation receipts, source/input hash
checks, complete output declaration, and a two-pass byte-idempotence gate.

It intentionally does not publish to the workspace root or mainframe.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT as MB_ROOT  # noqa: E402 - root-awareness is audited

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FEC_ROOT = Path(__file__).resolve().parent.parent
SCHEMA = "legislation-network-dependents-receipt-v1"
PREREQUISITES_SCHEMA = "legislation-network-dependents-prerequisites-v1"
PREREQUISITES_NAME = "network_dependents_prerequisites.json"
FIRST_RECEIPT_NAME = (
    "intermediate/network_dependents_first_pass.receipt.json"
)
SECOND_RECEIPT_NAME = (
    "intermediate/network_dependents_second_pass.receipt.json"
)
# Backward-compatible name for callers/tests that mean the first pass.
RECEIPT_NAME = FIRST_RECEIPT_NAME


@dataclass(frozen=True)
class ArtifactContract:
    path: str
    generation_bound: bool
    upstream_receipt: str | None = None


REQUIRED_ARTIFACTS: Mapping[str, ArtifactContract] = {
    "legislative_workbook": ArtifactContract(
        "Moneyball Main Version - Blended.xlsx", True, "legislative_workbook"
    ),
    "ranks": ArtifactContract(
        "ranks_python.json", True, "pillars_ranks"
    ),
    "category_stance": ArtifactContract(
        "category_stance.json", True, "category_stance"
    ),
    "augmented_network": ArtifactContract(
        "congress_cosponsor_network_AUG_shadow.xlsx",
        True,
        "augmented_network",
    ),
    "receptivity": ArtifactContract(
        "member_receptivity.json", True, "receptivity"
    ),
    "bipartisan": ArtifactContract(
        "bipartisan.json", True, "base_bipartisan"
    ),
    "say_do_first_pass": ArtifactContract(
        "say_do_gap.json", True, "say_do_first_pass"
    ),
    "congestion": ArtifactContract("data/engine/congestion.json", False),
    "electoral": ArtifactContract("data/money/electoral.json", False),
    "frame_alignment": ArtifactContract("data/stance/frame_alignment.json", False),
    "votes_119": ArtifactContract("votes/HS119_members.csv", False),
    "votes_118": ArtifactContract("votes/HS118_members.csv", False),
    "votes_117": ArtifactContract("votes/HS117_members.csv", False),
}

EXPECTED_RECEIPT_SCHEMAS: Mapping[str, str] = {
    "pillars_ranks": "legislation-pillars-ranks-receipt-v1",
    "base_bipartisan": "legislation-base-bipartisan-materialization-v1",
    "say_do_first_pass": "legislation-say-do-pass-receipt-v1",
}


@dataclass(frozen=True)
class Step:
    name: str
    script: Path
    args: tuple[str, ...]
    outputs: tuple[str, ...]
    required_source_tokens: tuple[str, ...] = field(default_factory=tuple)


STEPS: tuple[Step, ...] = (
    Step(
        "electoral_plays",
        FEC_ROOT / "engine" / "build_electoral_plays.py",
        (),
        ("data/money/electoral_plays.json",),
        (
            "from paths import ROOT as MB_ROOT",
            r"\data\money\electoral_plays.json",
        ),
    ),
    Step(
        "per_topic_flip",
        FEC_ROOT / "engine" / "per_topic_flip.py",
        (),
        ("per_topic_alloc.json",),
        ("from paths import ROOT as MB_ROOT", "per_topic_alloc.json"),
    ),
    Step(
        "per_topic_coalition",
        FEC_ROOT / "engine" / "per_topic_coalition.py",
        (),
        ("data/lobbying/per_topic_coalition.json",),
        ("from paths import ROOT as MB_ROOT", "per_topic_coalition"),
    ),
    Step(
        "budget_frontier",
        FEC_ROOT / "engine" / "budget_frontier.py",
        (),
        ("data/money/budget_frontier.json",),
        ("from paths import ROOT as MB_ROOT", "budget_frontier"),
    ),
    Step(
        "budget_frontier_electoral",
        FEC_ROOT / "engine" / "budget_frontier.py",
        ("--electoral",),
        ("data/money/budget_frontier_electoral.json",),
        ("from paths import ROOT as MB_ROOT", "--electoral"),
    ),
    Step(
        "optimizer",
        FEC_ROOT / "engine" / "optimize.py",
        (),
        ("optimize_result.json",),
        ("from paths import ROOT as MB_ROOT", "optimize_result.json"),
    ),
    Step(
        "im_allocation",
        FEC_ROOT / "engine" / "build_im_allocation.py",
        (),
        ("im_allocation.json",),
        ("from paths import ROOT as MB_ROOT", "im_allocation.json"),
    ),
    Step(
        "salience_advisory",
        FEC_ROOT / "engine" / "salience_shock.py",
        ("--advise",),
        ("salience_advisory.json",),
        ("from paths import ROOT as MB_ROOT", "salience_advisory.json"),
    ),
)

OUTPUT_NAMES = tuple(
    output for step in STEPS for output in step.outputs
)

# salience_shock.py and optimize.py dynamically import from <MONEYBALL_ROOT>/fec/engine.
# A byte-verified code capsule inside the shadow root satisfies that legacy
# contract without copying any data out of production.
CODE_CAPSULE = (
    "paths.py",
    "moneyball_core.py",
    "bill_rules.py",
    "stance/media_rules.py",
    "engine/money_response.py",
    "engine/flip_model.py",
)

# Environment overrides are useful for sensitivity analysis, but silently
# inheriting one here would make the recompute incomparable to the canonical
# pipeline.  Remove them and set only canonical defaults explicitly.
MODEL_ENVIRONMENT_KEYS = frozenset(
    {
        "BRIDGE",
        "TOPIC",
        "OPT_OUT",
        "PTF_OUT",
        "SAL_OUT",
        "FRONTIER_OUT",
        "CONGEST",
        "CATSTANCE",
        "ELECTORAL",
        "BUDGET",
        "INC",
        "PMAX",
        "HALFF",
        "IMPUTE_IND0",
        "RECEPT_LAYER",
        "COMPET_MULT",
        "COMPET_SRC",
        "OPTIMIZE_RESULT_PATH",
        "LEV",
        "REPFAC",
        "MAXSEEDS",
        "MC",
        "SEED",
        "DTHRESH",
        "LOSSFRAME",
        "PERS_NONE",
        "OVERALL_OPPONENT_CUTOFF",
        "SAL_B0",
        "SAL_BPSI",
        "SAL_DELTA",
        "SAL_PROBE",
        "SAL_PSI_DAMP",
    }
)


class NetworkDependentsError(RuntimeError):
    """A shadow isolation, freshness, or computation gate failed."""


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


def file_receipt(path: Path, *, relative_to: Path | None = None) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": (
            resolved.relative_to(relative_to.resolve()).as_posix()
            if relative_to is not None
            else str(resolved)
        ),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def atomic_write(path: Path, body: bytes) -> None:
    # The code-capsule copy used to stage through a fixed scratch name, so two
    # isolated runs materializing the same capsule clobbered each other's
    # staging file, and OneDrive syncs a stable scratch name and mints conflict
    # copies from it.  ``atomic_write_bytes`` is unique per process and per
    # call, fsync'd before publication, and retries the publish through the
    # OneDrive/AV lock window.
    #
    # The two writers below already carried a PID, so they were never exposed
    # to the clobbering defect; they route through here for the durability half
    # -- the fsync before publication and the PermissionError retry -- which a
    # bare write_bytes/os.replace pair does not provide.
    atomic_write_bytes(path, body)


def atomic_json(path: Path, value: Any) -> None:
    atomic_write(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        + b"\n",
    )


def atomic_text(path: Path, value: str) -> None:
    # ``newline="\n"`` requested no translation, so encoding the body here is
    # byte-identical and keeps LF regardless of platform text-mode defaults.
    atomic_write(path, value.encode("utf-8"))


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NetworkDependentsError(f"invalid JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise NetworkDependentsError(f"JSON root must be an object: {path}")
    return value


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
        raise NetworkDependentsError(
            f"isolated run directory does not exist: {run_dir}"
        )
    if run_dir == REPO_ROOT.resolve():
        raise NetworkDependentsError("production/workspace root is forbidden")
    if not any(is_relative_to(run_dir, root) for root in allowed_roots()):
        raise NetworkDependentsError(
            "MONEYBALL_ROOT must be under legislation shadow audit or temp"
        )
    return run_dir


def safe_child(run_dir: Path, relative: str | Path) -> Path:
    raw = Path(relative)
    if raw.is_absolute() or ".." in raw.parts:
        raise NetworkDependentsError(f"unsafe run-relative path: {relative}")
    path = (run_dir / raw).resolve()
    if not is_relative_to(path, run_dir):
        raise NetworkDependentsError(f"path escapes isolated root: {relative}")
    return path


def validate_shadow_environment(run_dir: Path) -> None:
    if os.environ.get("LEGISLATION_SHADOW_MODE") != "1":
        raise NetworkDependentsError("LEGISLATION_SHADOW_MODE=1 is required")
    declared = os.environ.get("LEGISLATION_SHADOW_RUN_DIR")
    if not declared or Path(declared).resolve() != run_dir:
        raise NetworkDependentsError(
            "LEGISLATION_SHADOW_RUN_DIR must equal MONEYBALL_ROOT"
        )


def contains_string(value: Any, target: str) -> bool:
    """Return whether a receipt recursively contains an exact hash string."""
    if isinstance(value, str):
        return value.casefold() == target.casefold()
    if isinstance(value, dict):
        return any(contains_string(item, target) for item in value.values())
    if isinstance(value, list):
        return any(contains_string(item, target) for item in value)
    return False


def validate_upstream_receipt(
    run_dir: Path,
    *,
    name: str,
    raw: Any,
    generation_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw, dict):
        raise NetworkDependentsError(f"upstream receipt entry absent: {name}")
    relative = str(raw.get("path") or "")
    path = safe_child(run_dir, relative)
    if not path.is_file():
        raise NetworkDependentsError(f"upstream receipt is missing: {name}")
    expected_hash = str(raw.get("sha256") or "").casefold()
    actual_hash = sha256_file(path)
    if expected_hash != actual_hash:
        raise NetworkDependentsError(
            f"upstream receipt hash mismatch: {name}"
        )
    if str(raw.get("generation_id") or "") != generation_id:
        raise NetworkDependentsError(
            f"upstream receipt entry has mixed generation: {name}"
        )
    payload = read_object(path)
    if payload.get("status") != "PASS":
        raise NetworkDependentsError(f"upstream receipt is not PASS: {name}")
    expected_schema = EXPECTED_RECEIPT_SCHEMAS.get(name)
    if expected_schema and payload.get("schema") != expected_schema:
        raise NetworkDependentsError(
            f"upstream receipt schema mismatch: {name}"
        )
    payload_generation = payload.get("generation_id")
    if payload_generation is not None and payload_generation != generation_id:
        raise NetworkDependentsError(
            f"upstream receipt payload has mixed generation: {name}"
        )
    return file_receipt(path, relative_to=run_dir), payload


def validate_prerequisites(
    run_dir: Path, prerequisites_path: Path
) -> dict[str, Any]:
    document = read_object(prerequisites_path)
    if (
        document.get("schema") != PREREQUISITES_SCHEMA
        or document.get("status") != "PASS"
    ):
        raise NetworkDependentsError(
            "network prerequisite receipt is absent or not PASS"
        )
    generation_id = str(document.get("generation_id") or "")
    if len(generation_id) < 16:
        raise NetworkDependentsError("prerequisite generation_id is invalid")
    artifacts = document.get("artifacts")
    upstream_raw = document.get("upstream_receipts")
    if not isinstance(artifacts, dict) or not isinstance(upstream_raw, dict):
        raise NetworkDependentsError(
            "prerequisite artifacts/upstream_receipts must be objects"
        )

    receipt_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    artifact_receipts: dict[str, dict[str, Any]] = {}
    for logical_name, contract in REQUIRED_ARTIFACTS.items():
        raw = artifacts.get(logical_name)
        if not isinstance(raw, dict):
            raise NetworkDependentsError(
                f"prerequisite artifact absent: {logical_name}"
            )
        if raw.get("path") != contract.path:
            raise NetworkDependentsError(
                f"prerequisite artifact path mismatch: {logical_name}"
            )
        if bool(raw.get("generation_bound")) != contract.generation_bound:
            raise NetworkDependentsError(
                f"generation-bound flag mismatch: {logical_name}"
            )
        if contract.generation_bound and raw.get("generation_id") != generation_id:
            raise NetworkDependentsError(
                f"artifact has mixed generation: {logical_name}"
            )
        path = safe_child(run_dir, contract.path)
        if not path.is_file():
            raise NetworkDependentsError(
                f"required staged artifact is missing: {contract.path}"
            )
        actual_hash = sha256_file(path)
        if str(raw.get("sha256") or "").casefold() != actual_hash:
            raise NetworkDependentsError(
                f"artifact hash mismatch: {logical_name}"
            )
        artifact_receipts[logical_name] = file_receipt(
            path, relative_to=run_dir
        )
        if contract.upstream_receipt:
            receipt_name = contract.upstream_receipt
            if raw.get("upstream_receipt") != receipt_name:
                raise NetworkDependentsError(
                    f"artifact receipt binding mismatch: {logical_name}"
                )
            if receipt_name not in receipt_cache:
                receipt_cache[receipt_name] = validate_upstream_receipt(
                    run_dir,
                    name=receipt_name,
                    raw=upstream_raw.get(receipt_name),
                    generation_id=generation_id,
                )
            _, receipt_payload = receipt_cache[receipt_name]
            if not contains_string(receipt_payload, actual_hash):
                raise NetworkDependentsError(
                    f"upstream receipt does not hash-bind artifact: {logical_name}"
                )

    # Selection must be unambiguous: moneyball_core deliberately chooses the
    # newest augmented workbook, so a second AUG file could change the graph.
    augmented = sorted(run_dir.glob("congress_cosponsor_network_*AUG*.xlsx"))
    expected_augmented = safe_child(
        run_dir, REQUIRED_ARTIFACTS["augmented_network"].path
    )
    if augmented != [expected_augmented]:
        raise NetworkDependentsError(
            "augmented-network selection is ambiguous in the shadow root"
        )

    unused_artifacts = sorted(set(artifacts) - set(REQUIRED_ARTIFACTS))
    if unused_artifacts:
        raise NetworkDependentsError(
            f"undeclared prerequisite artifacts: {unused_artifacts}"
        )
    receipt_receipts = {
        name: item[0] for name, item in sorted(receipt_cache.items())
    }
    return {
        "generation_id": generation_id,
        "prerequisites": file_receipt(
            prerequisites_path, relative_to=run_dir
        ),
        "artifacts": artifact_receipts,
        "upstream_receipts": receipt_receipts,
    }


def audit_producers(
    steps: Sequence[Step] = STEPS,
    code_capsule: Sequence[str] = CODE_CAPSULE,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for step in steps:
        if not step.script.is_file():
            raise NetworkDependentsError(
                f"legacy producer is missing: {step.script}"
            )
        text = step.script.read_text(encoding="utf-8-sig", errors="replace")
        for token in step.required_source_tokens:
            if token not in text:
                raise NetworkDependentsError(
                    f"producer contract changed: {step.name}/{token}"
                )
        result[step.name] = file_receipt(step.script)
    for name in code_capsule:
        path = FEC_ROOT / name
        if not path.is_file():
            raise NetworkDependentsError(f"code dependency missing: {name}")
        result[f"code:{name}"] = file_receipt(path)
    return result


def materialize_code_capsule(
    run_dir: Path, names: Sequence[str] = CODE_CAPSULE
) -> dict[str, dict[str, Any]]:
    target_root = safe_child(run_dir, "fec")
    target_root.mkdir(parents=True, exist_ok=True)
    result: dict[str, dict[str, Any]] = {}
    for name in names:
        source = FEC_ROOT / name
        destination = safe_child(run_dir, Path("fec") / name)
        # Verification now compares the source against the exact bytes being
        # published, rather than against a separate scratch file that a sibling
        # producer could have replaced between the copy and the check.
        body = source.read_bytes()
        if sha256_bytes(body) != sha256_file(source):
            raise NetworkDependentsError(
                f"code capsule copy verification failed: {name}"
            )
        atomic_write(destination, body)
        result[name] = file_receipt(destination, relative_to=run_dir)
    return result


def canonical_environment(run_dir: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in MODEL_ENVIRONMENT_KEYS and not key.startswith("SAL_")
    }
    environment.update(
        {
            "MONEYBALL_ROOT": str(run_dir),
            "LEGISLATION_SHADOW_MODE": "1",
            "LEGISLATION_SHADOW_RUN_DIR": str(run_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
            "SEED": "7",
            "MC": "20",
            "IMPUTE_IND0": "1",
            "RECEPT_LAYER": "1",
            "COMPET_MULT": "0",   # the shadow input contracts carry no competitiveness artifact (default is ON since 2026-10-06)
            "LEV": "1",
        }
    )
    return environment


def snapshot_files(paths: Iterable[Path]) -> dict[str, str | None]:
    return {
        str(path.resolve()): sha256_file(path) if path.is_file() else None
        for path in paths
    }


def verify_snapshot_unchanged(
    before: Mapping[str, str | None], *, label: str
) -> None:
    after = {
        name: sha256_file(Path(name)) if Path(name).is_file() else None
        for name in before
    }
    if after != dict(before):
        raise NetworkDependentsError(f"{label} changed during shadow execution")


def validate_output(path: Path, logical_name: str) -> None:
    value = read_object(path)
    if not value:
        raise NetworkDependentsError(f"empty output: {logical_name}")
    if logical_name == "im_allocation.json":
        # QME 2026-10-01: build_im_allocation.py now also writes the additive keys 'basis' and 'members_over_pmax'; require flip+coalition, tolerate extras.
        if not {"flip", "coalition"} <= set(value):
            raise NetworkDependentsError("im_allocation schema mismatch")
    elif logical_name == "salience_advisory.json":
        if not isinstance(value.get("advisory"), list) or not value["advisory"]:
            raise NetworkDependentsError("salience advisory schema mismatch")
    elif logical_name == "data/money/budget_frontier.json":
        if value.get("mode") != "coalition" or not isinstance(
            value.get("topics"), dict
        ):
            raise NetworkDependentsError("coalition frontier schema mismatch")
    elif logical_name == "data/money/budget_frontier_electoral.json":
        if value.get("mode") != "electoral" or not isinstance(
            value.get("topics"), dict
        ):
            raise NetworkDependentsError("electoral frontier schema mismatch")
    elif logical_name == "optimize_result.json":
        if value.get("objective") != "expected weighted flips":
            raise NetworkDependentsError("optimizer output schema mismatch")


def execute_chain_once(
    run_dir: Path,
    *,
    pass_number: int,
    steps: Sequence[Step] = STEPS,
    environment: Mapping[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    env = dict(environment or canonical_environment(run_dir))
    log_root = safe_child(run_dir, "intermediate/network_dependents_logs")
    log_root.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, dict[str, Any]] = {}
    executions: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        for output_name in step.outputs:
            safe_child(run_dir, output_name).parent.mkdir(
                parents=True, exist_ok=True
            )
        command = [sys.executable, str(step.script), *step.args]
        completed = subprocess.run(
            command,
            cwd=run_dir,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        stdout_path = log_root / f"pass{pass_number}_{index:02d}_{step.name}.stdout.txt"
        stderr_path = log_root / f"pass{pass_number}_{index:02d}_{step.name}.stderr.txt"
        atomic_text(stdout_path, completed.stdout)
        atomic_text(stderr_path, completed.stderr)
        if completed.returncode != 0:
            raise NetworkDependentsError(
                f"legacy producer failed: {step.name}/exit={completed.returncode}: "
                f"{completed.stderr[-500:]}"
            )
        declared: dict[str, dict[str, Any]] = {}
        for output_name in step.outputs:
            path = safe_child(run_dir, output_name)
            if not path.is_file():
                raise NetworkDependentsError(
                    f"producer emitted no declared output: {step.name}/{output_name}"
                )
            validate_output(path, output_name)
            item = file_receipt(path, relative_to=run_dir)
            declared[output_name] = item
            outputs[output_name] = item
        executions.append(
            {
                "step": step.name,
                "argv": [str(item) for item in command],
                "argv_sha256": sha256_bytes(canonical_bytes(command)),
                "exit_code": completed.returncode,
                "outputs": declared,
                "stdout_sha256": sha256_file(stdout_path),
                "stderr_sha256": sha256_file(stderr_path),
            }
        )
    expected = {output for step in steps for output in step.outputs}
    if set(outputs) != expected:
        raise NetworkDependentsError("complete output declaration failed")
    return outputs, executions


def run_recompute(
    run_dir: Path,
    *,
    prerequisites_path: Path,
    phase: str = "first",
    steps: Sequence[Step] = STEPS,
    code_capsule: Sequence[str] = CODE_CAPSULE,
) -> dict[str, Any]:
    run_dir = validate_run_dir(run_dir)
    if phase not in {"first", "second"}:
        raise NetworkDependentsError("phase must be first or second")
    receipt_name = (
        FIRST_RECEIPT_NAME
        if phase == "first"
        else SECOND_RECEIPT_NAME
    )
    prerequisites_path = prerequisites_path.resolve()
    if not is_relative_to(prerequisites_path, run_dir):
        raise NetworkDependentsError(
            "prerequisites receipt must be inside the isolated run root"
        )
    prerequisite = validate_prerequisites(run_dir, prerequisites_path)
    producers = audit_producers(steps, code_capsule)
    capsule = materialize_code_capsule(run_dir, code_capsule)

    protected_inputs = [
        safe_child(run_dir, item.path) for item in REQUIRED_ARTIFACTS.values()
    ]
    protected_inputs.extend(
        safe_child(run_dir, item["path"])
        for item in read_object(prerequisites_path)["upstream_receipts"].values()
    )
    input_snapshot = snapshot_files(protected_inputs)
    production_paths = [REPO_ROOT / name for name in OUTPUT_NAMES]
    production_snapshot = snapshot_files(production_paths)
    environment = canonical_environment(run_dir)

    first_outputs, first_execution = execute_chain_once(
        run_dir,
        pass_number=1,
        steps=steps,
        environment=environment,
    )
    verify_snapshot_unchanged(input_snapshot, label="prerequisite input")
    verify_snapshot_unchanged(production_snapshot, label="production output")
    second_outputs, second_execution = execute_chain_once(
        run_dir,
        pass_number=2,
        steps=steps,
        environment=environment,
    )
    verify_snapshot_unchanged(input_snapshot, label="prerequisite input")
    verify_snapshot_unchanged(production_snapshot, label="production output")

    mismatches = [
        name
        for name in sorted(first_outputs)
        if first_outputs[name]["sha256"] != second_outputs[name]["sha256"]
    ]
    if mismatches:
        raise NetworkDependentsError(
            f"network-dependent outputs are not byte-idempotent: {mismatches}"
        )
    generation_basis = {
        "generation_id": prerequisite["generation_id"],
        "prerequisites_sha256": prerequisite["prerequisites"]["sha256"],
        "artifacts_sha256": {
            key: item["sha256"]
            for key, item in sorted(prerequisite["artifacts"].items())
        },
        "upstream_receipts_sha256": {
            key: item["sha256"]
            for key, item in sorted(
                prerequisite["upstream_receipts"].items()
            )
        },
        "producers_sha256": {
            key: item["sha256"] for key, item in sorted(producers.items())
        },
        "canonical_environment": {
            key: environment[key]
            for key in (
                "SEED",
                "MC",
                "IMPUTE_IND0",
                "RECEPT_LAYER",
                "LEV",
            )
        },
        "outputs_sha256": {
            key: item["sha256"] for key, item in sorted(second_outputs.items())
        },
    }
    result = {
        "schema": SCHEMA,
        "status": "PASS",
        "acceptance_passed": True,
        "staging_only": True,
        "production_written": False,
        "mainframe_written": False,
        "formulas_changed": False,
        "phase": phase,
        "generation_id": prerequisite["generation_id"],
        "generation_basis_sha256": sha256_bytes(
            canonical_bytes(generation_basis)
        ),
        "prerequisites": prerequisite["prerequisites"],
        "prerequisite_artifacts": prerequisite["artifacts"],
        "upstream_receipts": prerequisite["upstream_receipts"],
        "producer_sources": producers,
        "code_capsule": capsule,
        "canonical_order": [step.name for step in steps],
        "canonical_environment": generation_basis["canonical_environment"],
        "complete_output_set": list(OUTPUT_NAMES),
        "externally_declared_output": receipt_name,
        "root_outputs_are_working_state": True,
        "declared_outputs": second_outputs,
        "idempotence": {
            "passes": 2,
            "byte_equal": True,
            "mismatches": [],
            "first_sha256": {
                key: item["sha256"]
                for key, item in sorted(first_outputs.items())
            },
            "second_sha256": {
                key: item["sha256"]
                for key, item in sorted(second_outputs.items())
            },
        },
        "execution": {
            "first_pass": first_execution,
            "second_pass": second_execution,
        },
        "artifact_notes": {
            "per_topic_flip.json": (
                "not an artifact; legacy per_topic_flip.py emits "
                "per_topic_alloc.json"
            ),
            "formula_or_order_blockers": [],
            "cycle_blockers": [],
        },
        "production_guard": {
            "paths_checked": sorted(production_snapshot),
            "unchanged": True,
        },
    }
    atomic_json(safe_child(run_dir, receipt_name), result)
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Recompute network/rank-dependent artifacts in an isolated "
            "legislation shadow root."
        )
    )
    result.add_argument(
        "--prerequisites",
        type=Path,
        help=(
            "PASS prerequisite receipt; defaults to "
            "<MONEYBALL_ROOT>/network_dependents_prerequisites.json"
        ),
    )
    result.add_argument(
        "--phase",
        choices=("first", "second"),
        default="first",
        help=(
            "Select an immutable phase-specific receipt; root JSON outputs "
            "remain working state until the second-pass coordinator seals "
            "their final roles."
        ),
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        run_dir = validate_run_dir(Path(MB_ROOT))
        validate_shadow_environment(run_dir)
        prerequisites = (
            args.prerequisites
            if args.prerequisites is not None
            else run_dir / PREREQUISITES_NAME
        )
        result = run_recompute(
            run_dir,
            prerequisites_path=prerequisites,
            phase=args.phase,
        )
    except (NetworkDependentsError, OSError, subprocess.SubprocessError) as exc:
        print(f"legislation network dependents: FAIL CLOSED: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
