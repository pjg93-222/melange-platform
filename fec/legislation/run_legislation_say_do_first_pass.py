"""Run the unchanged say/do producer as an isolated, deterministic first pass.

The existing ``say_do_gap.py`` formula remains authoritative.  This wrapper
only verifies its source contract, staged inputs, output shape, confinement,
and repeatability.  It leaves ``say_do_gap.json`` at the isolated run root for
subsequent consumers and makes the declared byte-identical first-pass copy at
``intermediate/say_do_first_pass.json``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import re
from typing import Any, Iterable, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from paths import ROOT as MB_ROOT  # noqa: E402 - required root-aware contract

try:
    from lane_file_io import atomic_write_bytes
except ImportError:  # pragma: no cover - package import path
    from fec.lane_file_io import atomic_write_bytes


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRODUCER = Path(__file__).resolve().parent.parent / "stance" / "say_do_gap.py"
SCHEMA = "legislation-say-do-pass-receipt-v1"
OUTPUT_NAME = "say_do_gap.json"
DIRECT_INPUTS = ("member_desk_bundle.json", "data/members/lep_criterion.json")
FIRST_SEQUENCE_INPUTS = ("category_stance.json",)
FIRST_COPY = Path("intermediate") / "say_do_first_pass.json"
FIRST_RECEIPT = Path("intermediate") / "say_do_first_pass.receipt.json"
STEP_LEDGER = Path("intermediate") / "shadow_step_receipts.json"
STEP_LEDGER_SCHEMA = "legislation-shadow-step-receipts-v1"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")

EXPECTED_ROLE_WEIGHTS = {
    "sponsor": 1.0,
    "original": 0.5,
    "cosponsor": 0.25,
}
EXPECTED_ADVANCE_WEIGHTS = {
    "Introduced": 0.0,
    "Passed House": 1.0,
    "Passed Senate": 1.0,
    "Agreed to in Senate": 1.0,
    "Resolving Differences": 1.5,
    "Failed to pass over veto": 1.5,
    "Became Law": 2.0,
}
EXPECTED_METHOD = (
    "SAY=z(log1p(supportive CU press n)); "
    "DO=z(log1p(sum role_w x advance_w over CU bills)); "
    "gap=SAY-DO, z within chamber; adv_rate floor 3 sponsored; "
    "LEP general follow-through carried as covariate. "
    "Diagnostic only (2026-07-07, boss follow-through note)."
)


class SayDoError(RuntimeError):
    """A staged-input, source, output, or repeatability gate failed."""


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


def receipt(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    stat = resolved.stat()
    return {
        "path": str(resolved),
        "size": stat.st_size,
        "sha256": sha256_file(resolved),
    }


def atomic_write(path: Path, body: bytes) -> None:
    # The scratch name used to be fixed, so two first passes writing the same
    # output clobbered each other's staging file, and OneDrive syncs a stable
    # scratch name and mints conflict copies from it.  ``atomic_write_bytes``
    # is unique per process and per call, fsync'd before publication, and
    # retries the publish through the OneDrive/AV lock window.
    atomic_write_bytes(path, body)


def atomic_json(path: Path, value: Any) -> None:
    # Newlines stay LF: the body is encoded here rather than written through
    # text mode.
    atomic_write(
        path,
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
        + b"\n",
    )


def atomic_copy(source: Path, destination: Path) -> None:
    # Verification now compares the source against the exact bytes being
    # published, rather than against a separate scratch file that a sibling
    # producer could have replaced between the copy and the check.
    body = source.read_bytes()
    if sha256_bytes(body) != sha256_file(source):
        raise SayDoError("first-pass hash-copy verification failed")
    atomic_write(destination, body)


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
        raise SayDoError(f"isolated run directory does not exist: {run_dir}")
    if run_dir == REPO_ROOT.resolve():
        raise SayDoError("production/workspace root is forbidden")
    if not any(is_relative_to(run_dir, root) for root in allowed_roots()):
        raise SayDoError(
            "MONEYBALL_ROOT must be under the legislation shadow audit or temp"
        )
    return run_dir


def read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SayDoError(f"invalid JSON input/output {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SayDoError(f"JSON root must be an object: {path}")
    return value


def audit_producer_contract(source: Path = PRODUCER) -> dict[str, Any]:
    """Verify the existing producer still contains the audited formula."""
    text = source.read_text(encoding="utf-8-sig")
    tree = ast.parse(text, filename=str(source))
    assignments: dict[str, Any] = {}
    strings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in {"ROLE_W", "ADV_W"}:
                    assignments[target.id] = ast.literal_eval(node.value)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append(node.value)
    if assignments.get("ROLE_W") != EXPECTED_ROLE_WEIGHTS:
        raise SayDoError("say_do_gap.py role-weight formula changed")
    if assignments.get("ADV_W") != EXPECTED_ADVANCE_WEIGHTS:
        raise SayDoError("say_do_gap.py advancement-weight formula changed")
    if EXPECTED_METHOD not in strings:
        raise SayDoError("say_do_gap.py method declaration changed")
    for required in (*DIRECT_INPUTS, OUTPUT_NAME):
        if required not in text:
            raise SayDoError(f"say_do_gap.py no longer references {required}")
    return receipt(source)


def safe_child(run_dir: Path, relative: Path | str) -> Path:
    path = (run_dir / relative).resolve()
    if not is_relative_to(path, run_dir):
        raise SayDoError(f"path escapes isolated run directory: {relative}")
    return path


def input_receipts(
    run_dir: Path, names: Iterable[str | Path]
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for name in names:
        relative = Path(name)
        path = safe_child(run_dir, relative)
        if not path.is_file():
            raise SayDoError(f"required staged input is missing: {relative.as_posix()}")
        result[relative.as_posix()] = receipt(path)
    return result


def validate_existing_receipt(
    path: Path,
    *,
    pass_name: str,
    producer_receipt: dict[str, Any],
    inputs: dict[str, dict[str, Any]],
    output_path: Path,
    copy_path: Path | None,
    generation_id: str,
) -> None:
    if not path.exists():
        return
    prior = read_object(path)
    if (
        prior.get("schema") != SCHEMA
        or prior.get("status") != "PASS"
        or prior.get("pass") != pass_name
        or prior.get("generation_id") != generation_id
    ):
        raise SayDoError("existing say/do receipt has the wrong contract")
    if prior.get("producer", {}).get("sha256") != producer_receipt["sha256"]:
        raise SayDoError("existing say/do receipt is stale for producer source")
    expected = {
        key: value["sha256"] for key, value in sorted(inputs.items())
    }
    if prior.get("expected_input_sha256") != expected:
        raise SayDoError("existing say/do receipt is stale for staged inputs")
    declared_output = copy_path if copy_path else output_path
    if not declared_output.is_file():
        raise SayDoError("existing say/do declared output is missing")
    if prior.get("declared_output", {}).get("sha256") != sha256_file(
        declared_output
    ):
        raise SayDoError("existing say/do declared output hash changed")


def validate_output(
    output_path: Path,
    desk_path: Path,
    lep_path: Path,
) -> dict[str, int]:
    output = read_object(output_path)
    desk = read_object(desk_path)
    lep = read_object(lep_path)
    if output.get("method") != EXPECTED_METHOD:
        raise SayDoError("say/do method text differs from audited formula")
    members = output.get("members")
    desk_members = desk.get("members")
    if not isinstance(members, dict) or not members:
        raise SayDoError("say/do output members must be a non-empty object")
    if not isinstance(desk_members, list):
        raise SayDoError("member_desk_bundle.json lacks members list")
    if not isinstance(lep.get("per_member"), dict):
        raise SayDoError("data/members/lep_criterion.json lacks per_member object")
    expected_names = {
        row.get("name")
        for row in desk_members
        if isinstance(row, dict)
        and str(row.get("chamber") or "").casefold() in {"house", "senate"}
        and isinstance(row.get("name"), str)
        and row.get("name")
    }
    if set(members) != expected_names:
        raise SayDoError("say/do output member coverage differs from staged desk")
    counts = {"talker": 0, "doer": 0, "aligned": 0}
    for name, row in members.items():
        if not isinstance(row, dict):
            raise SayDoError(f"say/do member row is not an object: {name}")
        quadrant = row.get("quadrant")
        if quadrant not in counts:
            raise SayDoError(f"invalid say/do quadrant for {name}")
        counts[quadrant] += 1
        for key in ("say_n", "sponsored_cu", "advanced_cu"):
            if not isinstance(row.get(key), int) or row[key] < 0:
                raise SayDoError(f"invalid {key} for {name}")
        for key in ("do_score", "say_z", "do_z", "gap"):
            if not isinstance(row.get(key), (int, float)):
                raise SayDoError(f"invalid {key} for {name}")
        if row["advanced_cu"] > row["sponsored_cu"]:
            raise SayDoError(f"advanced sponsored count exceeds total for {name}")
        rate = row.get("adv_rate")
        if row["sponsored_cu"] < 3:
            if rate is not None:
                raise SayDoError(f"advancement-rate floor violated for {name}")
        elif not isinstance(rate, (int, float)) or not 0 <= rate <= 1:
            raise SayDoError(f"invalid advancement rate for {name}")
        if round(row["say_z"] - row["do_z"], 3) != row["gap"]:
            raise SayDoError(f"gap identity failed for {name}")
        expected_quadrant = (
            "talker"
            if row["gap"] >= 1.0
            else "doer"
            if row["gap"] <= -1.0
            else "aligned"
        )
        if quadrant != expected_quadrant:
            raise SayDoError(f"quadrant threshold failed for {name}")
    return {"members": len(members), **counts}


def run_producer_twice(
    run_dir: Path,
    producer: Path,
    expected_inputs: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, str]]:
    output_path = safe_child(run_dir, OUTPUT_NAME)
    command = [sys.executable, str(producer)]
    command_hash = sha256_bytes(canonical_bytes(command))
    environment = dict(os.environ)
    environment.update(
        {
            "MONEYBALL_ROOT": str(run_dir),
            "LEGISLATION_SHADOW_MODE": "1",
            "LEGISLATION_SHADOW_RUN_DIR": str(run_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    execution_hashes: dict[str, str] = {}
    output_hashes: list[str] = []
    for index in (1, 2):
        completed = subprocess.run(
            command,
            cwd=run_dir,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        execution_hashes[f"run_{index}_stdout_sha256"] = sha256_bytes(
            completed.stdout.encode("utf-8")
        )
        execution_hashes[f"run_{index}_stderr_sha256"] = sha256_bytes(
            completed.stderr.encode("utf-8")
        )
        if completed.returncode != 0:
            raise SayDoError(
                f"say_do_gap.py run {index} failed with exit "
                f"{completed.returncode}: {completed.stderr[-500:]}"
            )
        if not output_path.is_file():
            raise SayDoError(f"say_do_gap.py run {index} emitted no output")
        validate_output(
            output_path,
            safe_child(run_dir, DIRECT_INPUTS[0]),
            safe_child(run_dir, DIRECT_INPUTS[1]),
        )
        output_hashes.append(sha256_file(output_path))
        current_inputs = input_receipts(run_dir, expected_inputs)
        if {
            key: value["sha256"] for key, value in current_inputs.items()
        } != {
            key: value["sha256"] for key, value in expected_inputs.items()
        }:
            raise SayDoError("staged say/do inputs changed during execution")
    if output_hashes[0] != output_hashes[1]:
        raise SayDoError("say/do producer is not byte-deterministic across repeats")
    return (
        {
            "command": command,
            "command_sha256": command_hash,
            "repeat_output_sha256": output_hashes,
        },
        execution_hashes,
    )


def validate_shadow_environment(run_dir: Path) -> None:
    if os.environ.get("LEGISLATION_SHADOW_MODE") != "1":
        raise SayDoError("LEGISLATION_SHADOW_MODE=1 is required")
    declared = os.environ.get("LEGISLATION_SHADOW_RUN_DIR")
    if not declared or Path(declared).resolve() != run_dir:
        raise SayDoError(
            "LEGISLATION_SHADOW_RUN_DIR must equal MONEYBALL_ROOT"
        )


def validate_step_ledger(
    run_dir: Path, *, pass_name: str
) -> tuple[str, dict[str, Any]]:
    path = safe_child(run_dir, STEP_LEDGER)
    ledger = read_object(path)
    generation_id = str(ledger.get("generation_id") or "").casefold()
    completed = [
        row.get("name")
        for row in ledger.get("completed_steps") or []
        if isinstance(row, dict)
    ]
    required = (
        "recompute_category_stance"
        if pass_name == "first"
        else "recompute_dashboard_and_member_desk"
    )
    if not (
        ledger.get("schema") == STEP_LEDGER_SCHEMA
        and ledger.get("status") in {"RUNNING", "COMPLETE"}
        and ledger.get("staged_only") is True
        and ledger.get("production_written") is False
        and HEX_RE.fullmatch(generation_id)
        and required in completed
    ):
        raise SayDoError(
            f"shadow step ledger is not generation-bound through {required}"
        )
    return generation_id, receipt(path)


def run_pass(
    *,
    pass_name: str,
    run_dir: Path,
    sequence_inputs: Sequence[str | Path],
    receipt_relative: Path,
    copy_relative: Path | None,
    require_first_receipt: bool,
    wrapper_path: Path,
) -> dict[str, Any]:
    run_dir = validate_run_dir(run_dir)
    generation_id, step_ledger_receipt = validate_step_ledger(
        run_dir, pass_name=pass_name
    )
    producer_receipt = audit_producer_contract()
    names: list[str | Path] = [
        *DIRECT_INPUTS,
        *sequence_inputs,
        STEP_LEDGER,
    ]
    if require_first_receipt:
        names.append(FIRST_RECEIPT)
    inputs = input_receipts(run_dir, names)
    output_path = safe_child(run_dir, OUTPUT_NAME)
    copy_path = safe_child(run_dir, copy_relative) if copy_relative else None
    receipt_path = safe_child(run_dir, receipt_relative)
    validate_existing_receipt(
        receipt_path,
        pass_name=pass_name,
        producer_receipt=producer_receipt,
        inputs=inputs,
        output_path=output_path,
        copy_path=copy_path,
        generation_id=generation_id,
    )
    if require_first_receipt:
        first = read_object(safe_child(run_dir, FIRST_RECEIPT))
        first_copy = safe_child(run_dir, FIRST_COPY)
        if (
            first.get("pass") != "first"
            or not first_copy.is_file()
            or first.get("declared_output", {}).get("sha256")
            != sha256_file(first_copy)
        ):
            raise SayDoError("first-pass receipt/copy is stale or incomplete")
    execution, execution_hashes = run_producer_twice(
        run_dir, PRODUCER, inputs
    )
    counts = validate_output(
        output_path,
        safe_child(run_dir, DIRECT_INPUTS[0]),
        safe_child(run_dir, DIRECT_INPUTS[1]),
    )
    if copy_path:
        atomic_copy(output_path, copy_path)
    declared_output = copy_path if copy_path else output_path
    result = {
        "schema": SCHEMA,
        "status": "PASS",
        "pass": pass_name,
        "generation_id": generation_id,
        "staged_only": True,
        "production_or_mainframe_written": False,
        "formula_changed": False,
        "producer": producer_receipt,
        "shadow_step_ledger": step_ledger_receipt,
        "wrapper": receipt(wrapper_path.resolve()),
        "command": execution,
        "expected_inputs": inputs,
        "expected_input_sha256": {
            key: value["sha256"] for key, value in sorted(inputs.items())
        },
        "input_hashes_unchanged_across_repeat": True,
        "byte_deterministic_repeat": True,
        "output_contract": {
            "schema": "legacy-say-do-gap-object",
            "method": EXPECTED_METHOD,
            "counts": counts,
        },
        "root_output": receipt(output_path),
        "declared_output": receipt(declared_output),
        "execution_stream_hashes": execution_hashes,
    }
    atomic_json(receipt_path, result)
    return result


def main() -> int:
    try:
        run_dir = validate_run_dir(Path(MB_ROOT))
        validate_shadow_environment(run_dir)
        result = run_pass(
            pass_name="first",
            run_dir=run_dir,
            sequence_inputs=FIRST_SEQUENCE_INPUTS,
            receipt_relative=FIRST_RECEIPT,
            copy_relative=FIRST_COPY,
            require_first_receipt=False,
            wrapper_path=Path(__file__),
        )
    except (SayDoError, OSError, subprocess.SubprocessError) as exc:
        print(f"legislation say/do first pass: FAIL CLOSED: {exc}")
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
